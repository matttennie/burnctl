"""Claude Code usage collector.

Reads ``~/.claude/stats-cache.json`` and delegates pricing lookups to
the ``claude_usage`` package.  When the cache is stale (``lastComputedDate``
is before today), raw session JSONL files are scanned to fill the gap.
"""

import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta

from burnctl.collectors.base import BaseCollector
from burnctl.config import PLAN_PRICES, ANNUAL_PRICES

CLAUDE_DIR = os.path.join(os.path.expanduser("~"), ".claude")
STATS_FILE = os.path.join(CLAUDE_DIR, "stats-cache.json")
PROJECTS_DIR = os.path.join(CLAUDE_DIR, "projects")


def _default_pricing():
    """Fallback pricing for models not found in the pricing table."""
    return {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_create": 6.25}


def _lookup_pricing(model, pricing_table):
    """Look up pricing for *model*, stripping date suffixes if needed."""
    if model in pricing_table:
        return pricing_table[model]
    # Try stripping a trailing date suffix like -20250929 or -20251101
    stripped = re.sub(r'-\d{8}$', '', model)
    return pricing_table.get(stripped, _default_pricing())


def _cost_for_model(model, usage, pricing_table):
    """Compute all-time cost for a single model's cumulative token usage."""
    pricing = _lookup_pricing(model, pricing_table)
    return (
        usage.get("inputTokens", 0) * pricing["input"] / 1_000_000
        + usage.get("outputTokens", 0) * pricing["output"] / 1_000_000
        + usage.get("cacheReadInputTokens", 0) * pricing["cache_read"] / 1_000_000
        + usage.get("cacheCreationInputTokens", 0) * pricing.get("cache_create", 0) / 1_000_000
    )


class ClaudeCollector(BaseCollector):
    """Collector for Claude Code usage data."""

    @property
    def name(self):
        return "Claude Code"

    @property
    def id(self):
        return "claude"

    # ── Data access ──────────────────────────────────────────────

    def is_available(self):
        """Return *True* if the stats-cache file exists."""
        return os.path.isfile(STATS_FILE)

    @staticmethod
    def _fallback_pricing():
        """Hardcoded Claude pricing when claude_usage is not installed."""
        dp = _default_pricing()
        return {
            "claude-opus-4-6": {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_create": 6.25},
            "claude-sonnet-4-6": {"input": 1.0, "output": 5.0, "cache_read": 0.10, "cache_create": 1.25},
            "claude-opus-4-5": dp,
            "claude-sonnet-4-5": {"input": 1.0, "output": 5.0, "cache_read": 0.10, "cache_create": 1.25},
            "claude-haiku-4-5": {"input": 0.25, "output": 1.25, "cache_read": 0.025, "cache_create": 0.3125},
        }

    def _get_pricing_table(self, data):
        """Return the Claude pricing table, refreshing if unknown models appear."""
        try:
            from claude_usage.pricing import get_pricing
            pricing_table = get_pricing()
        except ImportError:
            pricing_table = self._fallback_pricing()

        # Collect every model id referenced in the data
        all_models = set(data.get("modelUsage", {}).keys())
        for entry in data.get("dailyModelTokens", []):
            all_models.update(entry.get("tokensByModel", {}).keys())

        unknown = all_models - set(pricing_table.keys())
        if unknown:
            refreshed = get_pricing(force_refresh=True)
            if refreshed:
                pricing_table = refreshed

        return pricing_table

    # ── Live gap-fill from raw session JSONLs ──────────────────

    @staticmethod
    def _scan_sessions_after(cutoff_date, today_str):
        """Scan raw session JSONL files for data after *cutoff_date*.

        Returns (daily_activity, daily_model_tokens, model_usage_delta)
        where each mirrors the corresponding ``stats-cache.json`` structure.
        """
        if not os.path.isdir(PROJECTS_DIR):
            return [], [], {}

        # Collect per-date aggregates
        def _new_day():  # type: () -> dict
            return {"messages": 0, "sessions": set(), "tool_calls": 0}

        activity = defaultdict(_new_day)  # type: dict
        model_tokens = defaultdict(lambda: defaultdict(int))  # type: dict
        model_delta = defaultdict(lambda: defaultdict(int))  # type: dict

        cutoff_epoch = datetime.strptime(cutoff_date, "%Y-%m-%d").timestamp()

        for dirpath, _dirnames, filenames in os.walk(PROJECTS_DIR):
            for fname in filenames:
                if not fname.endswith(".jsonl"):
                    continue
                fpath = os.path.join(dirpath, fname)
                try:
                    # Skip files not modified since the cache was built
                    if os.path.getmtime(fpath) < cutoff_epoch:
                        continue
                    with open(fpath) as fh:
                        for line in fh:
                            try:
                                entry = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            ts_raw = entry.get("timestamp")
                            if isinstance(ts_raw, (int, float)):
                                day = datetime.fromtimestamp(ts_raw / 1000).strftime("%Y-%m-%d")
                            elif isinstance(ts_raw, str):
                                day = ts_raw[:10]  # "YYYY-MM-DD" prefix
                            else:
                                continue
                            if day <= cutoff_date or day > today_str:
                                continue
                            etype = entry.get("type", "")
                            sid = entry.get("sessionId", "")
                            if etype == "user":
                                activity[day]["messages"] += 1
                                activity[day]["sessions"].add(sid)
                            elif etype == "assistant":
                                msg = entry.get("message", {})
                                usage = msg.get("usage", {})
                                model = msg.get("model", "")
                                out_tok = usage.get("output_tokens", 0)
                                in_tok = usage.get("input_tokens", 0)
                                cache_read = usage.get("cache_read_input_tokens", 0)
                                cache_create_raw = usage.get("cache_creation_input_tokens", 0)
                                cache_detail = usage.get("cache_creation", {})
                                cache_create = cache_create_raw or sum(cache_detail.values())
                                if model and out_tok:
                                    model_tokens[day][model] += out_tok
                                    model_delta[model]["outputTokens"] += out_tok
                                    model_delta[model]["inputTokens"] += in_tok
                                    model_delta[model]["cacheReadInputTokens"] += cache_read
                                    model_delta[model]["cacheCreationInputTokens"] += cache_create
                                # Count tool_use blocks
                                for block in msg.get("content", []):
                                    if isinstance(block, dict) and block.get("type") == "tool_use":
                                        activity[day]["tool_calls"] += 1
                                        activity[day]["sessions"].add(sid)
                except (OSError, UnicodeDecodeError):
                    continue

        # Convert to cache-compatible structures
        daily_act = []
        for day in sorted(activity):
            a = activity[day]
            daily_act.append({
                "date": day,
                "messageCount": a["messages"],
                "sessionCount": len(a["sessions"]),
                "toolCallCount": a["tool_calls"],
            })
        daily_tok = []
        for day in sorted(model_tokens):
            daily_tok.append({
                "date": day,
                "tokensByModel": dict(model_tokens[day]),
            })
        return daily_act, daily_tok, dict(model_delta)

    def _load_data(self):
        """Load stats-cache.json and fill any gap with live session data."""
        if not os.path.isfile(STATS_FILE):
            return None
        try:
            with open(STATS_FILE) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"Warning: could not read Claude stats: {exc}", file=sys.stderr)
            return None

        today_str = datetime.now().strftime("%Y-%m-%d")
        last_computed = data.get("lastComputedDate", today_str)
        if last_computed >= today_str:
            return data

        # Cache is stale — supplement with raw JSONL data
        extra_act, extra_tok, model_delta = self._scan_sessions_after(last_computed, today_str)
        if extra_act:
            data.setdefault("dailyActivity", []).extend(extra_act)
            extra_msgs = sum(a["messageCount"] for a in extra_act)
            extra_sess = sum(a["sessionCount"] for a in extra_act)
            data["totalMessages"] = data.get("totalMessages", 0) + extra_msgs
            data["totalSessions"] = data.get("totalSessions", 0) + extra_sess
        if extra_tok:
            data.setdefault("dailyModelTokens", []).extend(extra_tok)
        # Merge cumulative model usage deltas
        model_usage = data.setdefault("modelUsage", {})
        for model, delta in model_delta.items():
            mu = model_usage.setdefault(model, {
                "inputTokens": 0, "outputTokens": 0,
                "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0,
            })
            for key, val in delta.items():
                mu[key] = mu.get(key, 0) + val

        return data

    # ── Stats computation ────────────────────────────────────────

    def get_stats(self, start, end, ref_date):
        """Collect Claude Code stats for the billing period [start, end)."""
        data = self._load_data()
        if data is None:
            return None

        pricing_table = self._get_pricing_table(data)

        ps = start.strftime("%Y-%m-%d")
        pe = end.strftime("%Y-%m-%d")

        # Filter daily activity and token records to the billing period
        daily = [
            d for d in data.get("dailyActivity", [])
            if ps <= d.get("date", "") < pe
        ]
        daily_tokens = [
            d for d in data.get("dailyModelTokens", [])
            if ps <= d.get("date", "") < pe
        ]

        # Aggregate period metrics
        period_messages = sum(d.get("messageCount", 0) for d in daily)
        period_sessions = sum(d.get("sessionCount", 0) for d in daily)
        period_tools = sum(d.get("toolCallCount", 0) for d in daily)

        period_output_tokens = 0
        period_model_usage = {}  # type: dict
        for entry in daily_tokens:
            for model, out_tokens in entry.get("tokensByModel", {}).items():
                period_output_tokens += out_tokens
                bucket = period_model_usage.setdefault(
                    model, {"outputTokens": 0},
                )
                bucket["outputTokens"] += out_tokens

        # Compute period cost using effective per-output-token rate that
        # accounts for input + cache costs (derived from all-time ratios).
        alltime_model_usage = data.get("modelUsage", {})
        period_cost = 0.0
        for model, bucket in period_model_usage.items():
            out_tok = bucket["outputTokens"]
            at_usage = alltime_model_usage.get(model)
            if at_usage and at_usage.get("outputTokens", 0) > 0:
                # Derive effective rate from all-time data
                at_cost = _cost_for_model(model, at_usage, pricing_table)
                at_out = at_usage["outputTokens"]
                effective_rate = at_cost / at_out * 1_000_000
            else:
                # Fall back to raw output-only pricing
                model_pricing = _lookup_pricing(model, pricing_table)
                effective_rate = model_pricing["output"]
            period_cost += out_tok * effective_rate / 1_000_000

        # All-time cost across every model
        alltime_cost = sum(
            _cost_for_model(m, u, pricing_table)
            for m, u in alltime_model_usage.items()
        )

        first_session = data.get("firstSessionDate", "")[:10]

        # Estimate period input tokens from all-time input/output ratio
        period_input_tokens = 0
        for model, bucket in period_model_usage.items():
            out_tok = bucket["outputTokens"]
            at_usage = alltime_model_usage.get(model)
            if at_usage and at_usage.get("outputTokens", 0) > 0:
                at_in = at_usage.get("inputTokens", 0)
                at_out = at_usage["outputTokens"]
                est_in = int(out_tok * at_in / at_out)
                bucket["inputTokens"] = est_in
                period_input_tokens += est_in

        return {
            "messages": period_messages,
            "sessions": period_sessions,
            "input_tokens": period_input_tokens if period_model_usage else None,
            "output_tokens": period_output_tokens,
            "period_cost": period_cost,
            "alltime_cost": alltime_cost,
            "model_usage": period_model_usage,
            "first_session": first_session,
            "total_messages": data.get("totalMessages", 0),
            "total_sessions": data.get("totalSessions", 0),
            "tool_calls": period_tools,
        }

    # ── Plan / billing helpers ───────────────────────────────────

    def get_upgrade_url(self):
        return "https://claude.ai/settings/billing"

    def get_plan_info(self, config):
        """Resolve Claude plan from env, config, or default.

        Priority: ``CLAUDE_PLAN`` env var > explicit config >
        default (``max5x`` with a stderr warning).
        """
        env_plan = os.environ.get("CLAUDE_PLAN", "").lower()
        from_env = env_plan and env_plan in PLAN_PRICES
        if from_env:
            plan = env_plan
        else:
            plan = config.get("claude_plan", "max5x")

        # Warn if using the default and the user never set it
        if plan == "max5x" and not from_env and not config.get("_claude_plan_set"):
            cfg_file = os.path.join(
                os.path.expanduser("~"), ".config", "burnctl", "config.json",
            )
            explicitly_set = False
            try:
                with open(cfg_file) as f:
                    saved = json.load(f)
                explicitly_set = "claude_plan" in saved
            except (OSError, json.JSONDecodeError, TypeError):
                pass
            if not explicitly_set:
                print(
                    "Warning: Claude plan defaulting to 'max5x' ($100/mo). "
                    "Set your plan: burnctl config claude_plan <plan>",
                    file=sys.stderr,
                )

        interval = config.get("billing_interval", "mo")

        if interval == "yr" and plan in ANNUAL_PRICES:
            price = ANNUAL_PRICES[plan] / 12
        else:
            price = PLAN_PRICES.get(plan, 0)

        return {
            "plan_name": plan,
            "plan_price": price,
            "billing_day": config.get("billing_day", 10),
            "interval": interval,
        }
