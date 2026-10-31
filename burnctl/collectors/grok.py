"""Grok Build (xAI) usage collector.

Reads ``<base>/sessions/<encoded-cwd>/<session-id>/usage.json`` where *base*
is ``$GROK_HOME`` or ``~/.grok``. Only the per-turn deltas in ``turns[]`` are
aggregated; the cumulative ``session`` block also counts history inherited by
resumed/forked sessions and would double count.
"""

import json
import os
from datetime import datetime, timezone

from burnctl.collectors.base import BaseCollector, _check_file_size, load_with_stat_cache
from burnctl.pricing import get_model_pricing_for_time

_TICKS_PER_USD = 1e10


def _sessions_dir():
    """Resolve the sessions dir at call time so ``GROK_HOME`` is honored."""
    base = os.environ.get("GROK_HOME") or os.path.expanduser("~/.grok")
    return os.path.join(base, "sessions")


def _parse_ts(s):
    """Parse an ISO 8601 string to a UTC-aware datetime."""
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, OSError):
        return None


def _date_str(dt):
    return dt.strftime("%Y-%m-%d") if dt else ""


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


def _read_json(path):
    if not _check_file_size(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            obj = json.load(fh)
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _iter_session_dirs():
    """Yield ``<sessions>/<cwd>/<id>`` directories (non-dirs are skipped)."""
    root = _sessions_dir()
    try:
        cwds = sorted(os.listdir(root))
    except OSError:
        return
    for cwd in cwds:
        cwd_path = os.path.join(root, cwd)
        if not os.path.isdir(cwd_path):
            continue
        try:
            ids = sorted(os.listdir(cwd_path))
        except OSError:
            continue
        for sid in ids:
            sdir = os.path.join(cwd_path, sid)
            if os.path.isdir(sdir):
                yield sdir


def _parse_updates(path):
    """Return (turn_completed_ts, tool_call_ts) from updates.jsonl, or None."""
    if not os.path.isfile(path) or not _check_file_size(path):
        return None
    msgs, tools = [], []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                # Cheap pre-filter: skip the (large) chunk events without JSON parsing.
                if "turn_completed" not in line and '"tool_call"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                    kind = obj["params"]["update"]["sessionUpdate"]
                    ts = datetime.fromtimestamp(float(obj["timestamp"]), tz=timezone.utc)
                except (ValueError, KeyError, TypeError, OSError, OverflowError):
                    continue
                if kind == "turn_completed":
                    msgs.append(ts)
                elif kind == "tool_call":
                    tools.append(ts)
    except OSError:
        return None
    return msgs, tools


def _parse_session(usage_path):
    """Parse one session dir (given its usage.json path); None if unusable."""
    obj = _read_json(usage_path)
    if obj is None:
        return None
    sdir = os.path.dirname(usage_path)
    sid = str(obj.get("sessionId") or os.path.basename(sdir))
    session = obj.get("session") if isinstance(obj.get("session"), dict) else {}
    primary = session.get("primaryModelId") or "unknown"

    turns = []
    raw_turns = obj.get("turns")
    for t in raw_turns if isinstance(raw_turns, list) else []:
        if not isinstance(t, dict):
            continue
        mu = t.get("modelUsage")
        if not isinstance(mu, dict) or not mu:
            mu = {primary: t}
        models = {}
        for model, u in mu.items():
            if isinstance(u, dict):
                cached = _num(u.get("cachedReadTokens"))
                models[str(model)] = (
                    max(_num(u.get("inputTokens")) - cached, 0),
                    cached,
                    _num(u.get("outputTokens")),
                )
        ticks = _num(t.get("costUsdTicks"))
        turns.append({
            "n": t.get("turnNumber"),
            "ts": _parse_ts(t.get("endedAt")),
            "models": models,
            "cost": ticks / _TICKS_PER_USD if ticks > 0 else None,
        })

    summary = _read_json(os.path.join(sdir, "summary.json")) or {}
    stamps = [t["ts"] for t in turns if t["ts"]]
    return {
        "sid": sid,
        "turns": turns,
        "first": _parse_ts(summary.get("created_at")) or (min(stamps) if stamps else None),
        "last": _parse_ts(summary.get("last_active_at")) or (max(stamps) if stamps else None),
        "updates": _parse_updates(os.path.join(sdir, "updates.jsonl")),
    }


# Live/top-mode parse cache: path -> ((st_mtime_ns, st_size), parsed).
_SESSION_PARSE_CACHE = {}  # type: dict


def _turn_cost(turn, ts):
    """USD cost of a turn: server-reported ticks, else the pricing table."""
    if turn["cost"] is not None:
        return turn["cost"]
    cost = 0.0
    for model, (uncached, cached, out) in turn["models"].items():
        p = get_model_pricing_for_time("grok", model, ts)
        if p:
            cost += (
                uncached * p["input"]
                + cached * p.get("cache_read", p["input"])
                + out * p["output"]
            ) / 1_000_000
    return cost


class GrokCollector(BaseCollector):
    """Collector for xAI Grok Build usage data."""

    top_report = True
    model_prefixes = ("grok-",)

    @property
    def name(self):
        return "Grok Build"

    @property
    def id(self):
        return "grok"

    def is_available(self):
        return os.path.isdir(_sessions_dir())

    def get_stats(self, start, end, ref_date, live=False):
        """Collect Grok Build stats for [start, end)."""
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if not os.path.isdir(_sessions_dir()):
            return None

        p_msgs = p_in = p_out = p_tools = 0
        p_cost = a_cost = 0.0
        p_models = {}  # type: dict
        a_models = {}  # type: dict
        p_daily = {}  # type: dict
        p_sids = set()
        a_sids = set()
        a_msgs = 0
        first_dt = last_dt = None
        seen = set()

        for sdir in _iter_session_dirs():
            path = os.path.join(sdir, "usage.json")
            if not os.path.isfile(path):
                continue
            parsed = (
                load_with_stat_cache(_SESSION_PARSE_CACHE, path, _parse_session)
                if live else _parse_session(path)
            )
            if parsed is None:
                continue
            a_sids.add(parsed["sid"])
            for dt in (parsed["first"], parsed["last"]):
                if dt and (first_dt is None or dt < first_dt):
                    first_dt = dt
                if dt and (last_dt is None or dt > last_dt):
                    last_dt = dt

            turns_in_p = 0
            for turn in parsed["turns"]:
                if turn["n"] is not None:
                    key = (parsed["sid"], turn["n"])
                    if key in seen:
                        continue
                    seen.add(key)
                ts = turn["ts"]
                cost = _turn_cost(turn, ts)
                a_cost += cost
                in_p = ts is not None and start <= ts < end
                if in_p:
                    p_cost += cost
                    turns_in_p += 1
                for model, (uncached, _cached, out) in turn["models"].items():
                    a_models.setdefault(model, {"inputTokens": 0, "outputTokens": 0})
                    a_models[model]["inputTokens"] += uncached
                    a_models[model]["outputTokens"] += out
                    if in_p:
                        p_in += uncached
                        p_out += out
                        p_models.setdefault(model, {"inputTokens": 0, "outputTokens": 0})
                        p_models[model]["inputTokens"] += uncached
                        p_models[model]["outputTokens"] += out

            if turns_in_p:
                p_sids.add(parsed["sid"])
            if parsed["updates"] is not None:
                msgs, tools = parsed["updates"]
                a_msgs += len(msgs)
                p_tools += sum(1 for t in tools if start <= t < end)
                period_msgs = [t for t in msgs if start <= t < end]
            else:
                a_msgs += len(parsed["turns"])
                period_msgs = [t["ts"] for t in parsed["turns"] if t["ts"] and start <= t["ts"] < end]
            p_msgs += len(period_msgs)
            for t in period_msgs:
                d = _date_str(t)
                p_daily[d] = p_daily.get(d, 0) + 1

        if not a_sids:
            return None

        return {
            "messages": p_msgs,
            "sessions": len(p_sids),
            "input_tokens": p_in,
            "output_tokens": p_out,
            "period_cost": p_cost,
            "alltime_cost": a_cost,
            "model_usage": p_models,
            "alltime_model_usage": a_models,
            "daily_messages": p_daily,
            "first_session": _date_str(first_dt),
            "last_active": _date_str(last_dt),
            "total_messages": a_msgs,
            "total_sessions": len(a_sids),
            "tool_calls": p_tools,
        }

    def get_upgrade_url(self):
        return "https://console.x.ai/"
