"""Claude Code usage collector.

Reads ``~/.claude/stats-cache.json`` and delegates pricing lookups to
the ``claude_usage`` package.
"""

import json
import os
import sys
from datetime import timedelta

from burnctl.collectors.base import BaseCollector
from burnctl.config import PLAN_PRICES, ANNUAL_PRICES

STATS_FILE = os.path.join(os.path.expanduser("~"), ".claude", "stats-cache.json")


def _default_pricing():
    """Fallback pricing for models not found in the pricing table."""
    return {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_create": 6.25}


def _cost_for_model(model, usage, pricing_table):
    """Compute all-time cost for a single model's cumulative token usage."""
    pricing = pricing_table.get(model, _default_pricing())
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

    def _load_data(self):
        """Load and return the raw stats-cache.json contents."""
        if not os.path.isfile(STATS_FILE):
            return None
        try:
            with open(STATS_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"Warning: could not read Claude stats: {exc}", file=sys.stderr)
            return None

    def _get_pricing_table(self, data):
        """Return the Claude pricing table, refreshing if unknown models appear."""
        from claude_usage.pricing import get_pricing

        pricing_table = get_pricing()

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
        period_cost = 0.0
        for entry in daily_tokens:
            for model, out_tokens in entry.get("tokensByModel", {}).items():
                period_output_tokens += out_tokens
                model_pricing = pricing_table.get(model, _default_pricing())
                period_cost += out_tokens * model_pricing["output"] / 1_000_000

        # All-time cost across every model
        alltime_cost = sum(
            _cost_for_model(m, u, pricing_table)
            for m, u in data.get("modelUsage", {}).items()
        )

        # Daily message map (date string -> count)
        daily_messages = {
            d.get("date", ""): d.get("messageCount", 0)
            for d in daily
        }

        # Spark data: one entry per elapsed day
        days_elapsed = min((ref_date - start).days, (end - start).days)
        spark_data = []
        for i in range(days_elapsed + 1):
            day_str = (start + timedelta(days=i)).strftime("%Y-%m-%d")
            spark_data.append(daily_messages.get(day_str, 0))

        first_session = data.get("firstSessionDate", "")[:10]

        return {
            "messages": period_messages,
            "sessions": period_sessions,
            "output_tokens": period_output_tokens,
            "period_cost": period_cost,
            "alltime_cost": alltime_cost,
            "model_usage": data.get("modelUsage", {}),
            "daily_messages": daily_messages,
            "first_session": first_session,
            "total_messages": data.get("totalMessages", 0),
            "total_sessions": data.get("totalSessions", 0),
            "tool_calls": period_tools,
            "spark_data": spark_data,
        }

    # ── Plan / billing helpers ───────────────────────────────────

    def get_upgrade_url(self):
        return "https://claude.ai/settings/billing"

    def get_plan_info(self, config):
        """Resolve Claude plan from config.

        Checks ``claude_plan`` first, then falls back to the generic
        ``plan`` key, defaulting to ``"max5x"``.
        """
        plan = config.get("claude_plan", config.get("plan", "max5x"))
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
