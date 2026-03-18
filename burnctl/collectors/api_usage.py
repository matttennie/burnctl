"""API usage collectors from a JSONL usage log.

Reads ``~/.config/orchard/usage.jsonl`` and creates one collector per
upstream provider (OpenRouter, HuggingFace, etc.).  Each provider appears
as its own agent in the burnctl report — indistinguishable from Claude Code
or Codex CLI.
"""

import json
import os
from datetime import datetime, timedelta

from burnctl.collectors.base import BaseCollector

USAGE_FILE = os.path.join(
    os.path.expanduser("~"), ".config", "orchard", "usage.jsonl",
)

# Skip files larger than 100 MB to avoid unbounded memory usage.
_MAX_FILE_BYTES = 100 * 1024 * 1024

# Provider display names and upgrade URLs.
_PROVIDER_META = {
    "openrouter": {
        "name": "OpenRouter",
        "upgrade_url": "https://openrouter.ai/credits",
    },
    "huggingface": {
        "name": "HuggingFace",
        "upgrade_url": "https://huggingface.co/pricing",
    },
    "anthropic": {
        "name": "Anthropic",
        "upgrade_url": "https://console.anthropic.com/settings/billing",
    },
    "openai": {
        "name": "OpenAI",
        "upgrade_url": "https://platform.openai.com/usage",
    },
}


def _parse_ts(ts_str):
    """Parse an ISO-8601 timestamp to a naive datetime (UTC assumed).

    Returns *None* on failure.
    """
    if not ts_str or not isinstance(ts_str, str):
        return None
    try:
        cleaned = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        # Strip timezone info for comparison with naive period boundaries
        return dt.replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def _parse_entry(line):
    """Parse a single JSONL line into a validated dict.

    Returns *None* for malformed or incomplete entries.
    """
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None

    if not isinstance(obj, dict):
        return None

    ts = _parse_ts(obj.get("ts"))
    if ts is None:
        return None

    # Require at minimum: provider and model_id
    provider = obj.get("provider")
    model_id = obj.get("model_id")
    if not provider or not model_id:
        return None

    return {
        "ts": ts,
        "provider": str(provider),
        "model_id": str(model_id),
        "model_name": str(obj.get("model_name", model_id)),
        "input_tokens": int(obj.get("input_tokens", 0)),
        "output_tokens": int(obj.get("output_tokens", 0)),
        "cost": float(obj.get("cost", 0.0)),
        "node_id": str(obj.get("node_id", "")),
        "estimated": bool(obj.get("estimated", False)),
    }


def _load_entries(filepath=None):
    """Load and parse all entries from the usage JSONL file.

    Returns a list of validated entry dicts.
    """
    filepath = filepath or USAGE_FILE
    if not os.path.isfile(filepath):
        return []

    try:
        if os.path.getsize(filepath) > _MAX_FILE_BYTES:
            return []
    except OSError:
        return []

    entries = []
    try:
        with open(filepath) as fh:
            for line in fh:
                entry = _parse_entry(line)
                if entry is not None:
                    entries.append(entry)
    except (OSError, UnicodeDecodeError):
        return []

    return entries


class ApiUsageCollector(BaseCollector):
    """Collector for a single API provider's usage data.

    Each instance represents one provider (e.g. OpenRouter, HuggingFace).
    Multiple instances are created by :func:`discover_collectors` — one
    per provider found in the usage log.
    """

    def __init__(self, provider_id, provider_name, usage_file=None,
                 upgrade_url=""):
        self._provider_id = provider_id
        self._provider_name = provider_name
        self._usage_file = usage_file
        self._upgrade_url = upgrade_url

    @property
    def _file(self):
        return self._usage_file or USAGE_FILE

    @property
    def name(self):
        return self._provider_name

    @property
    def id(self):
        return self._provider_id

    def is_available(self):
        """Return *True* if the usage file has entries for this provider."""
        entries = _load_entries(self._file)
        return any(e["provider"] == self._provider_id for e in entries)

    def get_stats(self, start, end, ref_date):
        """Collect stats for this provider for [start, end)."""
        all_entries = _load_entries(self._file)
        entries = [
            e for e in all_entries if e["provider"] == self._provider_id
        ]
        if not entries:
            return None

        # ── Period accumulators ──
        period_messages = 0
        period_output_tokens = 0
        period_cost = 0.0
        period_model_usage: dict = {}
        daily_messages: dict = {}
        period_node_ids = set()

        # ── All-time accumulators ──
        alltime_cost = 0.0
        alltime_messages = 0
        alltime_node_ids = set()
        first_ts = None

        for entry in entries:
            ts = entry["ts"]

            # ── All-time ──
            alltime_cost += entry["cost"]
            alltime_messages += 1
            alltime_node_ids.add(entry["node_id"])
            if first_ts is None or ts < first_ts:
                first_ts = ts

            # ── Period filtering ──
            if start <= ts < end:
                period_messages += 1
                period_output_tokens += entry["output_tokens"]
                period_cost += entry["cost"]
                period_node_ids.add(entry["node_id"])

                # Model usage keyed by human-readable name
                model = entry["model_name"]
                bucket = period_model_usage.setdefault(
                    model, {"inputTokens": 0, "outputTokens": 0},
                )
                bucket["inputTokens"] += entry["input_tokens"]
                bucket["outputTokens"] += entry["output_tokens"]

                # Daily messages
                day_str = ts.strftime("%Y-%m-%d")
                daily_messages[day_str] = daily_messages.get(day_str, 0) + 1

        if alltime_messages == 0:
            return None

        # Sessions = distinct node IDs active in the period
        period_sessions = len(period_node_ids)

        # Spark data: one entry per elapsed day
        days_elapsed = min((ref_date - start).days, (end - start).days)
        spark_data = []
        for i in range(days_elapsed + 1):
            day_str = (start + timedelta(days=i)).strftime("%Y-%m-%d")
            spark_data.append(daily_messages.get(day_str, 0))

        first_session = first_ts.strftime("%Y-%m-%d") if first_ts else ""

        return {
            "messages": period_messages,
            "sessions": period_sessions,
            "output_tokens": period_output_tokens,
            "period_cost": period_cost,
            "alltime_cost": alltime_cost,
            "model_usage": period_model_usage,
            "daily_messages": daily_messages,
            "first_session": first_session,
            "total_messages": alltime_messages,
            "total_sessions": len(alltime_node_ids),
            "tool_calls": 0,
            "spark_data": spark_data,
        }

    def get_upgrade_url(self):
        return self._upgrade_url

    def get_plan_info(self, config):
        return {
            "plan_name": "pay-as-you-go",
            "plan_price": 0,
            "billing_day": config.get("billing_day", 1),
            "interval": "mo",
        }


def discover_collectors(usage_file=None):
    """Scan the usage JSONL and return one collector per provider.

    Returns an empty list if the file doesn't exist or has no valid
    entries.
    """
    entries = _load_entries(usage_file)
    providers = sorted(set(e["provider"] for e in entries))

    collectors = []
    for pid in providers:
        meta = _PROVIDER_META.get(pid, {})
        display_name = meta.get("name", pid.title())
        upgrade_url = meta.get("upgrade_url", "")
        collectors.append(
            ApiUsageCollector(pid, display_name, usage_file, upgrade_url),
        )
    return collectors
