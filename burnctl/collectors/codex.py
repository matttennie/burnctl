"""Codex CLI usage collector.

Reads session data from ``~/.codex/sessions/`` (JSONL files) and
``~/.codex/history.jsonl`` to compute usage statistics.
"""

import json
import os
from datetime import datetime, timedelta, timezone

from burnctl.collectors.base import BaseCollector
from burnctl.pricing import get_agent_pricing

CODEX_DIR = os.path.join(os.path.expanduser("~"), ".codex")
SESSIONS_DIR = os.path.join(CODEX_DIR, "sessions")
HISTORY_FILE = os.path.join(CODEX_DIR, "history.jsonl")

# Tool-related event types in Codex CLI sessions.
_TOOL_EVENT_TYPES = frozenset({
    "exec_command",
    "apply_patch",
    "apply_diff",
    "file_edit",
    "file_read",
    "file_write",
    "shell",
    "browser",
    "computer",
    "mcp_tool_call",
})


def _parse_ts(ts_str):
    """Parse an ISO-8601 timestamp string to a timezone-aware datetime.

    Handles both ``Z`` suffix and ``+HH:MM`` offset formats.  Returns
    *None* on failure.
    """
    if not ts_str:
        return None
    try:
        cleaned = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except (ValueError, TypeError):
        return None


def _date_str(dt):
    """Return ``YYYY-MM-DD`` for a datetime, or empty string."""
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d")


# Skip session files larger than 50 MB to avoid unbounded memory usage.
_MAX_SESSION_BYTES = 50 * 1024 * 1024


def _iter_session_files():
    """Yield absolute paths to every ``.jsonl`` file under SESSIONS_DIR.

    Files larger than ``_MAX_SESSION_BYTES`` are silently skipped.
    """
    if not os.path.isdir(SESSIONS_DIR):
        return
    for dirpath, _dirnames, filenames in os.walk(SESSIONS_DIR):
        for fname in filenames:
            if fname.endswith(".jsonl"):
                full = os.path.join(dirpath, fname)
                try:
                    if os.path.getsize(full) <= _MAX_SESSION_BYTES:
                        yield full
                except OSError:
                    continue


def _parse_session(path):
    """Parse a single Codex session JSONL file.

    Returns a dict with aggregated session data, or *None* if the file
    cannot be meaningfully parsed.

    Returned keys
    -------------
    session_ts : datetime | None
        Timestamp from ``session_meta``.
    models : set[str]
        Model names observed in ``turn_context`` events.
    user_messages : list[datetime]
        Timestamps of each user message.
    total_token_usage : dict | None
        The ``total_token_usage`` from the *last* ``token_count`` event
        (cumulative within a session).
    tool_calls : int
        Count of tool-related events.
    """
    session_ts = None
    models = set()
    user_messages = []
    last_token_usage = None
    tool_calls = 0

    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                evt_type = obj.get("type")
                timestamp = _parse_ts(obj.get("timestamp"))
                payload = obj.get("payload") or {}

                if evt_type == "session_meta":
                    session_ts = (
                        _parse_ts(payload.get("timestamp")) or timestamp
                    )

                elif evt_type == "turn_context":
                    model = payload.get("model")
                    if model:
                        models.add(model)

                elif evt_type == "event_msg":
                    msg_type = payload.get("type", "")

                    if msg_type == "user_message":
                        user_messages.append(timestamp)

                    elif msg_type == "token_count":
                        info = payload.get("info") or {}
                        usage = info.get("total_token_usage")
                        if usage:
                            last_token_usage = usage

                    elif msg_type in _TOOL_EVENT_TYPES:
                        tool_calls += 1

                elif evt_type == "response_item":
                    item_type = payload.get("type", "")
                    if item_type in ("function_call", "tool_call"):
                        tool_calls += 1
                    for part in (payload.get("content") or []):
                        if isinstance(part, dict) and part.get("type") in (
                            "tool_use",
                            "function_call",
                        ):
                            tool_calls += 1

    except OSError:
        return None

    if session_ts is None and not user_messages:
        return None

    return {
        "session_ts": session_ts,
        "models": models,
        "user_messages": user_messages,
        "total_token_usage": last_token_usage,
        "tool_calls": tool_calls,
    }


def _default_model_pricing():
    """Fallback pricing for unknown Codex/OpenAI models (per 1M tokens)."""
    return {"input": 2.50, "output": 15.0, "cache_read": 0.25}


def _compute_session_cost(token_usage, model_pricing):
    """Compute the USD cost of a session from its cumulative token usage.

    OpenAI charges 50 % for cached input tokens, full price for
    non-cached input tokens, and a separate output rate.  Reasoning
    output tokens are counted as regular output tokens.
    """
    if not token_usage:
        return 0.0

    input_tokens = token_usage.get("input_tokens", 0)
    cached_input = token_usage.get("cached_input_tokens", 0)
    output_tokens = token_usage.get("output_tokens", 0)

    if isinstance(model_pricing, dict) and "input" in model_pricing:
        pricing = model_pricing
    else:
        pricing = _default_model_pricing()

    non_cached = max(input_tokens - cached_input, 0)
    cache_rate = pricing.get("cache_read", pricing["input"] * 0.5)
    cost = (
        non_cached * pricing["input"]
        + cached_input * cache_rate
        + output_tokens * pricing["output"]
    ) / 1_000_000

    return cost


class CodexCollector(BaseCollector):
    """Collector for OpenAI Codex CLI usage data."""

    @property
    def name(self):
        return "Codex CLI"

    @property
    def id(self):
        return "codex"

    # -- Data access ---------------------------------------------------

    def is_available(self):
        """Return *True* if the Codex sessions directory exists."""
        return os.path.isdir(SESSIONS_DIR)

    def _get_pricing_table(self):
        """Return the model -> pricing dict for Codex."""
        return get_agent_pricing("codex")

    # -- Stats computation ---------------------------------------------

    def get_stats(self, start, end, ref_date):
        """Collect Codex CLI stats for [start, end)."""
        if not os.path.isdir(SESSIONS_DIR):
            return None

        pricing_table = self._get_pricing_table()

        # Ensure start/end/ref_date are timezone-aware for comparison
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if ref_date.tzinfo is None:
            ref_date = ref_date.replace(tzinfo=timezone.utc)

        # Period accumulators
        period_messages = 0
        period_sessions = 0
        period_input_tokens = 0
        period_output_tokens = 0
        period_cost = 0.0
        period_tool_calls = 0
        period_model_usage = {}  # type: dict
        daily_messages = {}  # type: dict

        # All-time accumulators
        alltime_cost = 0.0
        alltime_messages = 0
        alltime_sessions = 0
        first_session_dt = None

        session_files = list(_iter_session_files())
        if not session_files:
            return None

        for path in session_files:
            parsed = _parse_session(path)
            if parsed is None:
                continue

            session_ts = parsed["session_ts"]
            token_usage = parsed["total_token_usage"]
            models = parsed["models"]

            # Pick the primary model for pricing (alphabetically for
            # a stable tie-breaker when multiple models appear).
            primary_model = sorted(models)[0] if models else None

            if primary_model and primary_model in pricing_table:
                model_pricing = pricing_table[primary_model]
            else:
                model_pricing = _default_model_pricing()

            # -- All-time ------------------------------------------
            alltime_sessions += 1
            alltime_messages += len(parsed["user_messages"])
            alltime_cost += _compute_session_cost(
                token_usage, model_pricing,
            )

            if session_ts is not None:
                if (
                    first_session_dt is None
                    or session_ts < first_session_dt
                ):
                    first_session_dt = session_ts

            # -- Period filtering ----------------------------------
            in_period = False
            if session_ts is not None:
                in_period = start <= session_ts < end

            if in_period:
                period_sessions += 1
                period_tool_calls += parsed["tool_calls"]

                if token_usage:
                    out_tok = token_usage.get("output_tokens", 0)
                    in_tok = token_usage.get("input_tokens", 0)
                    cached_in = token_usage.get("cached_input_tokens", 0)
                    non_cached_in = max(in_tok - cached_in, 0)
                    period_input_tokens += non_cached_in
                    period_output_tokens += out_tok
                    period_cost += _compute_session_cost(
                        token_usage, model_pricing,
                    )

                    if primary_model:
                        bucket = period_model_usage.setdefault(
                            primary_model,
                            {"inputTokens": 0, "outputTokens": 0},
                        )
                        bucket["inputTokens"] += non_cached_in
                        bucket["outputTokens"] += out_tok

            # Count user messages per day
            for msg_ts in parsed["user_messages"]:
                if msg_ts is None:
                    continue
                day_str = _date_str(msg_ts)
                if start <= msg_ts < end:
                    period_messages += 1
                    daily_messages[day_str] = (
                        daily_messages.get(day_str, 0) + 1
                    )

        if alltime_sessions == 0:
            return None

        # Fold in history.jsonl for total counts when available
        hist_messages, hist_sessions = self._count_history()
        if hist_messages > alltime_messages:
            alltime_messages = hist_messages
        if hist_sessions > alltime_sessions:
            alltime_sessions = hist_sessions

        first_session = (
            _date_str(first_session_dt) if first_session_dt else ""
        )

        return {
            "messages": period_messages,
            "sessions": period_sessions,
            "input_tokens": period_input_tokens,
            "output_tokens": period_output_tokens,
            "period_cost": period_cost,
            "alltime_cost": alltime_cost,
            "model_usage": period_model_usage,
            "first_session": first_session,
            "total_messages": alltime_messages,
            "total_sessions": alltime_sessions,
            "tool_calls": period_tool_calls,
        }

    # -- History helper ------------------------------------------------

    @staticmethod
    def _count_history():
        """Count messages and sessions from ``~/.codex/history.jsonl``.

        Returns ``(total_messages, total_sessions)``.
        """
        if not os.path.isfile(HISTORY_FILE):
            return 0, 0

        total_messages = 0
        session_ids = set()
        try:
            with open(HISTORY_FILE) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    total_messages += 1
                    sid = obj.get("session_id")
                    if sid:
                        session_ids.add(sid)
        except OSError:
            return 0, 0

        return total_messages, len(session_ids)

    # -- Plan / billing helpers ----------------------------------------

    def get_upgrade_url(self):
        return "https://platform.openai.com/usage"

    def get_plan_info(self, config):
        from burnctl.config import CODEX_PLAN_PRICES
        plan = config.get("codex_plan", "none")
        price = CODEX_PLAN_PRICES.get(plan, 0)
        return {
            "plan_name": plan,
            "plan_price": price,
            "billing_day": config.get("billing_day", 1),
            "interval": "mo",
        }
