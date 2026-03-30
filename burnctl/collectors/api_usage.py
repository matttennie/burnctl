"""Provider-backed usage collectors.

OpenRouter is sourced directly from the OpenRouter API to avoid stale or
incomplete local harness logs. Other provider rows continue to be sourced
from Orchard's JSONL usage log when present.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime

from burnctl.collectors.base import BaseCollector
from burnctl.openrouter_ledger import load_entries as load_openrouter_ledger

USAGE_FILE = os.path.join(
    os.path.expanduser("~"), ".config", "orchard", "usage.jsonl",
)

_OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"
_OPENROUTER_KEY_ENV_VARS = (
    "OPENROUTER_MGMT_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENROUTER_ORCHARD_API_KEY",
)

# Skip files larger than 100 MB to avoid unbounded memory usage.
_MAX_FILE_BYTES = 100 * 1024 * 1024

# Provider display names and upgrade URLs.
_PROVIDER_META = {
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
    """Parse an ISO-8601 timestamp to a naive datetime (UTC assumed)."""
    if not ts_str or not isinstance(ts_str, str):
        return None
    try:
        cleaned = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        return dt.replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def _parse_entry(line):
    """Parse a single Orchard JSONL line into a validated dict."""
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

    provider = obj.get("provider")
    model_id = obj.get("model_id")
    if not provider or not model_id:
        return None

    try:
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
    except (ValueError, TypeError):
        return None


def _load_entries(filepath=None):
    """Load and parse all entries from the usage JSONL file."""
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
        with open(filepath, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                entry = _parse_entry(line)
                if entry is not None:
                    entries.append(entry)
    except (OSError, UnicodeDecodeError):
        return []

    return entries


def _openrouter_api_key():
    """Return the first configured OpenRouter API key, if any."""
    for name in _OPENROUTER_KEY_ENV_VARS:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _openrouter_get_json(path, api_key):
    """GET an OpenRouter endpoint and decode the JSON response."""
    req = urllib.request.Request(
        _OPENROUTER_API_BASE + path,
        headers={
            "Authorization": "Bearer " + api_key,
            "Accept": "application/json",
            "User-Agent": "burnctl/0.1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _warn_openrouter_api(message):
    print("Warning: OpenRouter collector: " + message, file=sys.stderr)


def _float_or(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_or(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_activity_day(day_str):
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(str(day_str), fmt)
        except ValueError:
            continue
    return None


class OpenRouterCollector(BaseCollector):
    """Collector backed by the OpenRouter account API."""

    @property
    def name(self):
        return "OpenRouter"

    @property
    def id(self):
        return "openrouter"

    def is_available(self):
        return bool(_openrouter_api_key())

    def get_upgrade_url(self):
        return "https://openrouter.ai/credits"

    def get_stats(self, start, end, ref_date):
        api_key = _openrouter_api_key()
        if not api_key:
            return None
        start_day = start.date()
        end_day = end.date()

        try:
            activity_resp = _openrouter_get_json("/activity", api_key)
        except urllib.error.HTTPError as err:
            if err.code in (401, 403):
                _warn_openrouter_api(
                    "analytics activity endpoint denied. Use an OpenRouter "
                    "management/provisioning key for accurate usage totals."
                )
                return None
            _warn_openrouter_api("analytics request failed with HTTP %s." % err.code)
            return None
        except (urllib.error.URLError, ValueError, OSError) as err:
            _warn_openrouter_api("analytics request failed: %s" % err)
            return None

        try:
            credits_resp = _openrouter_get_json("/credits", api_key)
        except urllib.error.HTTPError as err:
            credits_resp = None
            if err.code not in (401, 403):
                _warn_openrouter_api("credits request failed with HTTP %s." % err.code)
        except (urllib.error.URLError, ValueError, OSError):
            credits_resp = None

        rows = activity_resp.get("data", [])
        if not isinstance(rows, list):
            return None

        period_messages = 0
        period_input_tokens = 0
        period_output_tokens = 0
        period_cost = 0.0
        period_model_usage = {}
        period_endpoints = set()
        latest_activity_day = None
        settled_request_ids = set()
        ledger_used = False

        observed_messages = 0
        observed_endpoints = set()

        for row in rows:
            if not isinstance(row, dict):
                continue
            day = _parse_activity_day(row.get("date"))
            if day is None:
                continue
            if latest_activity_day is None or day.date() > latest_activity_day:
                latest_activity_day = day.date()

            requests = _int_or(row.get("requests"))
            prompt_tokens = _int_or(row.get("prompt_tokens"))
            completion_tokens = _int_or(row.get("completion_tokens"))
            usage = _float_or(row.get("usage"))
            model = str(row.get("model", "") or row.get("model_name", "") or "Unknown")
            endpoint_id = str(row.get("endpoint_id", ""))
            request_id = str(row.get("id", "") or row.get("generation_id", ""))

            observed_messages += requests
            if endpoint_id:
                observed_endpoints.add(endpoint_id)
            if request_id:
                settled_request_ids.add(request_id)

            if not (start_day <= day.date() < end_day):
                continue

            period_messages += requests
            period_input_tokens += prompt_tokens
            period_output_tokens += completion_tokens
            period_cost += usage
            if endpoint_id:
                period_endpoints.add(endpoint_id)

            bucket = period_model_usage.setdefault(
                model, {"inputTokens": 0, "outputTokens": 0},
            )
            bucket["inputTokens"] += prompt_tokens
            bucket["outputTokens"] += completion_tokens

        ledger_cutoff = None
        if latest_activity_day is not None:
            ledger_cutoff = datetime.combine(
                latest_activity_day,
                datetime.min.time(),
            )
        for entry in load_openrouter_ledger():
            if entry.get("provider") != "openrouter":
                continue
            ts = entry["ts"]
            if not (start <= ts < end):
                continue
            request_id = entry.get("request_id", "")
            if request_id and request_id in settled_request_ids:
                continue
            if ledger_cutoff is not None and ts <= ledger_cutoff:
                continue

            ledger_used = True
            period_messages += 1
            period_input_tokens += entry.get("input_tokens", 0)
            period_output_tokens += entry.get("output_tokens", 0)
            period_cost += entry.get("cost", 0.0)
            if request_id:
                period_endpoints.add(request_id)
            bucket = period_model_usage.setdefault(
                entry.get("model", "Unknown"),
                {"inputTokens": 0, "outputTokens": 0},
            )
            bucket["inputTokens"] += entry.get("input_tokens", 0)
            bucket["outputTokens"] += entry.get("output_tokens", 0)

        alltime_cost = None
        credits_data = credits_resp.get("data", {}) if isinstance(credits_resp, dict) else {}
        if isinstance(credits_data, dict):
            total_usage = credits_data.get("total_usage")
            if total_usage is not None:
                alltime_cost = _float_or(total_usage, None)

        if alltime_cost is None:
            alltime_cost = sum(
                _float_or(row.get("usage"))
                for row in rows
                if isinstance(row, dict)
            )

        return {
            "messages": period_messages,
            "sessions": len(period_endpoints),
            "input_tokens": period_input_tokens,
            "output_tokens": period_output_tokens,
            "period_cost": period_cost,
            "alltime_cost": alltime_cost,
            "model_usage": period_model_usage,
            "first_session": "",
            "total_messages": None,
            "total_sessions": None,
            "tool_calls": 0,
            "observed_messages": observed_messages,
            "observed_sessions": len(observed_endpoints),
            "activity_through": (
                latest_activity_day.isoformat() if latest_activity_day else ""
            ),
            "live_ledger": ledger_used,
        }


class ApiUsageCollector(BaseCollector):
    """Collector for non-OpenRouter provider rows sourced from Orchard."""

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
        entries = _load_entries(self._file)
        return any(e["provider"] == self._provider_id for e in entries)

    def get_stats(self, start, end, ref_date):
        all_entries = _load_entries(self._file)
        entries = [
            e for e in all_entries if e["provider"] == self._provider_id
        ]
        if not entries:
            return None

        period_messages = 0
        period_input_tokens = 0
        period_output_tokens = 0
        period_cost = 0.0
        period_model_usage = {}
        period_node_ids = set()

        alltime_cost = 0.0
        alltime_messages = 0
        alltime_node_ids = set()
        first_ts = None

        for entry in entries:
            ts = entry["ts"]

            alltime_cost += entry["cost"]
            alltime_messages += 1
            alltime_node_ids.add(entry["node_id"])
            if first_ts is None or ts < first_ts:
                first_ts = ts

            if start <= ts < end:
                period_messages += 1
                period_input_tokens += entry["input_tokens"]
                period_output_tokens += entry["output_tokens"]
                period_cost += entry["cost"]
                period_node_ids.add(entry["node_id"])

                model = entry["model_name"]
                bucket = period_model_usage.setdefault(
                    model, {"inputTokens": 0, "outputTokens": 0},
                )
                bucket["inputTokens"] += entry["input_tokens"]
                bucket["outputTokens"] += entry["output_tokens"]

        if alltime_messages == 0:
            return None

        first_session = first_ts.strftime("%Y-%m-%d") if first_ts else ""

        return {
            "messages": period_messages,
            "sessions": len(period_node_ids),
            "input_tokens": period_input_tokens,
            "output_tokens": period_output_tokens,
            "period_cost": period_cost,
            "alltime_cost": alltime_cost,
            "model_usage": period_model_usage,
            "first_session": first_session,
            "total_messages": alltime_messages,
            "total_sessions": len(alltime_node_ids),
            "tool_calls": 0,
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
    """Return provider collectors.

    OpenRouter is always represented by its dedicated API-backed collector.
    Other providers are discovered from Orchard's JSONL usage file.
    """
    entries = _load_entries(usage_file)
    providers = sorted(
        set(e["provider"] for e in entries if e["provider"] != "openrouter")
    )

    collectors = [OpenRouterCollector()]
    for pid in providers:
        meta = _PROVIDER_META.get(pid, {})
        display_name = meta.get("name", pid.title())
        upgrade_url = meta.get("upgrade_url", "")
        collectors.append(
            ApiUsageCollector(pid, display_name, usage_file, upgrade_url),
        )
    return collectors
