"""Provider-backed usage collectors.

OpenRouter is sourced directly from its account APIs. Other provider rows are
discovered from burnctl's provider usage log when present.
"""

import json
import os
import sys
from typing import Dict, List
import urllib.error
import urllib.request
from datetime import datetime

from burnctl import __version__ as _BURNCTL_VERSION
from burnctl.collectors.base import BaseCollector, load_with_stat_cache
from burnctl.openrouter_ledger import load_entries as load_openrouter_ledger

_DEFAULT_USAGE_FILE = os.path.join(
    os.path.expanduser("~"), ".config", "burnctl", "usage.jsonl",
)

_OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"
_OPENROUTER_KEY_ENV_VARS = (
    "OPENROUTER_MGMT_API_KEY",
    "OPENROUTER_API_KEY",
)

_HF_API_BASE = "https://huggingface.co"
_HF_KEY_ENV_VARS = (
    "HF_TOKEN",
    "HUGGINGFACE_API_KEY",
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
    # Inworld exposes no public usage API (verified 2026-06); usage rows
    # logged to the burnctl usage file are the supported source.
    "inworld": {
        "name": "Inworld",
        "upgrade_url": "https://platform.inworld.ai/billing",
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
    """Parse a single provider-usage JSONL line into a validated dict."""
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


# Parsed-entries cache: path -> ((st_mtime_ns, st_size), entries).
# The usage file is read at discovery, availability, and stats time
# within a single run, and on every refresh in top-mode.
_ENTRIES_CACHE = {}  # type: dict


def _read_entries(filepath):
    """Parse every entry in the usage JSONL file at *filepath*."""
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


def _load_entries(filepath=None):
    """Load all entries from the usage JSONL file, cached by file state."""
    filepath = filepath or os.environ.get("BURNCTL_USAGE_FILE", "").strip()
    if not filepath:
        filepath = _DEFAULT_USAGE_FILE
    if not os.path.isfile(filepath):
        return []
    return load_with_stat_cache(_ENTRIES_CACHE, filepath, _read_entries)


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


def _openrouter_api_key():
    """Return the first configured OpenRouter API key, if any."""
    for name in _OPENROUTER_KEY_ENV_VARS:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _openrouter_get_json(path, api_key, timeout=10):
    """Fetch JSON from OpenRouter API."""
    req = urllib.request.Request(
        _OPENROUTER_API_BASE + path,
        headers={
            "Authorization": "Bearer " + api_key,
            "Accept": "application/json",
            "User-Agent": "burnctl/" + _BURNCTL_VERSION,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, dict) else None


def _warn_openrouter_api(message):
    print("Warning: OpenRouter collector: " + message, file=sys.stderr)


def _hf_api_keys():
    """Return all distinct configured HuggingFace tokens, in priority order."""
    keys = []
    for name in _HF_KEY_ENV_VARS:
        value = os.environ.get(name, "").strip()
        if value and value not in keys:
            keys.append(value)
    return keys


def _hf_get_json(path, api_key, timeout=10):
    """Fetch JSON from the HuggingFace Hub API."""
    req = urllib.request.Request(
        _HF_API_BASE + path,
        headers={
            "Authorization": "Bearer " + api_key,
            "Accept": "application/json",
            "User-Agent": "burnctl/" + _BURNCTL_VERSION,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data


def _warn_hf_api(message):
    print("Warning: HuggingFace collector: " + message, file=sys.stderr)


def _parse_hf_usage(payload):
    """Parse a billing usage-v2 payload into burnctl stats.

    Deliberately strict: only payload shapes that are unambiguously
    understood produce numbers.  Anything else returns None so the caller
    can fall back to the local usage log — never invented figures.
    """
    if isinstance(payload, dict):
        items = None
        for key in ("usage", "data", "items", "records"):
            if isinstance(payload.get(key), list):
                items = payload[key]
                break
    elif isinstance(payload, list):
        items = payload
    else:
        items = None
    if items is None:
        return None

    period_cost = 0.0
    requests_total = 0
    saw_requests = False
    saw_cost = False
    model_usage = {}  # type: Dict[str, Dict[str, int]]

    for item in items:
        if not isinstance(item, dict):
            continue
        cost = None
        for key in ("totalCost", "cost", "amountUsd", "usd"):
            if isinstance(item.get(key), (int, float)):
                cost = float(item[key])
                break
        if cost is None:
            continue
        saw_cost = True
        period_cost += cost

        requests = None
        for key in ("requests", "numRequests", "count"):
            if isinstance(item.get(key), int):
                requests = item[key]
                break
        if requests is not None:
            saw_requests = True
            requests_total += requests

        model = ""
        for key in ("model", "modelId", "model_id", "name"):
            if isinstance(item.get(key), str) and item[key]:
                model = item[key]
                break
        in_tok = item.get("inputTokens", 0)
        out_tok = item.get("outputTokens", 0)
        if model and (
            isinstance(in_tok, int) and isinstance(out_tok, int)
            and (in_tok or out_tok)
        ):
            bucket = model_usage.setdefault(
                model, {"inputTokens": 0, "outputTokens": 0},
            )
            bucket["inputTokens"] += in_tok
            bucket["outputTokens"] += out_tok

    if not saw_cost:
        return None

    input_tokens = sum(u["inputTokens"] for u in model_usage.values())
    output_tokens = sum(u["outputTokens"] for u in model_usage.values())
    return {
        "messages": requests_total if saw_requests else None,
        "sessions": None,
        "input_tokens": input_tokens if model_usage else None,
        "output_tokens": output_tokens,
        "period_cost": period_cost,
        "alltime_cost": 0.0,
        "model_usage": model_usage,
        "first_session": "",
        "last_active": "",
        "total_messages": None,
        "total_sessions": None,
        "tool_calls": 0,
    }


class OpenRouterCollector(BaseCollector):
    """Collector backed by the OpenRouter account API."""

    rolling_window_days = 30

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

    def get_stats(self, start, end, ref_date, live=False):
        api_key = _openrouter_api_key()
        if not api_key:
            return None
        start_day = start.date()
        end_day = end.date()

        timeout = 2 if live else 10
        try:
            activity_resp = _openrouter_get_json("/activity", api_key, timeout=timeout)
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
            credits_resp = _openrouter_get_json("/credits", api_key, timeout=timeout)
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError):
            credits_resp = None

        if not isinstance(activity_resp, dict):
            return None

        rows = activity_resp.get("data", [])
        if not isinstance(rows, list):
            return None

        period_messages = 0
        period_input_tokens = 0
        period_output_tokens = 0
        period_cost = 0.0
        period_model_usage: Dict[str, Dict[str, int]] = {}
        period_requests = 0
        latest_activity_day = None
        settled_request_ids = set()
        ledger_used = False

        observed_messages = 0
        observed_requests = 0

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
            request_id = str(row.get("id", "") or row.get("generation_id", ""))

            observed_messages += requests
            observed_requests += requests
            if request_id:
                settled_request_ids.add(request_id)

            if not (start_day <= day.date() < end_day):
                continue

            period_messages += requests
            period_requests += requests
            period_input_tokens += prompt_tokens
            period_output_tokens += completion_tokens
            period_cost += usage

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
            period_requests += 1
            period_input_tokens += entry.get("input_tokens", 0)
            period_output_tokens += entry.get("output_tokens", 0)
            period_cost += entry.get("cost", 0.0)
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
            "sessions": None,
            "input_tokens": period_input_tokens,
            "output_tokens": period_output_tokens,
            "period_cost": period_cost,
            "alltime_cost": alltime_cost,
            "model_usage": period_model_usage,
            "first_session": "",
            "last_active": latest_activity_day.isoformat() if latest_activity_day else "",
            "total_messages": None,
            "total_sessions": None,
            "tool_calls": 0,
            "observed_messages": observed_messages,
            "observed_sessions": None,
            "observed_requests": observed_requests,
            "period_requests": period_requests,
            "activity_through": (
                latest_activity_day.isoformat() if latest_activity_day else ""
            ),
            "live_ledger": ledger_used,
        }


class ApiUsageCollector(BaseCollector):
    """Collector for non-OpenRouter provider rows sourced from the usage log."""

    rolling_window_days = 30

    def __init__(self, provider_id, provider_name, usage_file=None,
                 upgrade_url=""):
        self._provider_id = provider_id
        self._provider_name = provider_name
        self._usage_file = usage_file
        self._upgrade_url = upgrade_url

    @property
    def _file(self):
        return (
            self._usage_file
            or os.environ.get("BURNCTL_USAGE_FILE", "").strip()
            or _DEFAULT_USAGE_FILE
        )

    @property
    def name(self):
        return self._provider_name

    @property
    def id(self):
        return self._provider_id

    def is_available(self):
        entries = _load_entries(self._file)
        return any(e["provider"] == self._provider_id for e in entries)

    def get_stats(self, start, end, ref_date, live=False):
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
        period_model_usage: Dict[str, Dict[str, int]] = {}
        period_node_ids = set()

        alltime_cost = 0.0
        alltime_messages = 0
        alltime_node_ids = set()
        first_ts = None
        last_ts = None

        for entry in entries:
            ts = entry["ts"]

            alltime_cost += entry["cost"]
            alltime_messages += 1
            alltime_node_ids.add(entry["node_id"])
            if first_ts is None or ts < first_ts:
                first_ts = ts
            if last_ts is None or ts > last_ts:
                last_ts = ts

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
        last_active = last_ts.strftime("%Y-%m-%d") if last_ts else ""

        return {
            "messages": period_messages,
            "sessions": len(period_node_ids),
            "input_tokens": period_input_tokens,
            "output_tokens": period_output_tokens,
            "period_cost": period_cost,
            "alltime_cost": alltime_cost,
            "model_usage": period_model_usage,
            "first_session": first_session,
            "last_active": last_active,
            "total_messages": alltime_messages,
            "total_sessions": len(alltime_node_ids),
            "tool_calls": 0,
        }

    def get_upgrade_url(self):
        return self._upgrade_url

    def get_plan_info(self, config):
        plan = config.get("agent_plans", {}).get(self.id)
        if not plan:
            plan = config.get(f"{self.id}_plan", "")
        agent_bd = config.get("agent_billing_days", {}).get(self.id)
        if not agent_bd:
            agent_bd = config.get(f"{self.id}_billing_day", 0)
        return {
            "plan_name": plan or "pay-as-you-go",
            "plan_price": 0,
            "billing_day": agent_bd if agent_bd else config.get("billing_day", 1),
            "interval": "mo",
        }


class HuggingFaceCollector(ApiUsageCollector):
    """HuggingFace spend from the account billing API.

    Falls back to local usage-log rows when no token is configured, the
    token lacks billing permission, or the API payload is unrecognized.
    """

    def __init__(self, usage_file=None):
        super().__init__(
            "huggingface",
            "HuggingFace",
            usage_file,
            "https://huggingface.co/settings/billing",
        )

    def is_available(self):
        return bool(_hf_api_keys()) or super().is_available()

    def get_stats(self, start, end, ref_date, live=False):
        keys = _hf_api_keys()
        if not keys:
            return super().get_stats(start, end, ref_date, live=live)

        path = "/api/settings/billing/usage-v2?startDate=%d&endDate=%d" % (
            int(start.timestamp() * 1000),
            int(end.timestamp() * 1000),
        )
        timeout = 2 if live else 10
        payload = None
        denied = False
        for key in keys:
            try:
                payload = _hf_get_json(path, key, timeout=timeout)
                break
            except urllib.error.HTTPError as err:
                if err.code in (401, 403):
                    denied = True
                    continue
                _warn_hf_api("billing request failed with HTTP %s." % err.code)
                return super().get_stats(start, end, ref_date, live=live)
            except (urllib.error.URLError, ValueError, OSError) as err:
                _warn_hf_api("billing request failed: %s" % err)
                return super().get_stats(start, end, ref_date, live=live)

        if payload is None:
            if denied:
                _warn_hf_api(
                    "billing access denied. Add the Billing read permission "
                    "to your fine-grained token at "
                    "https://huggingface.co/settings/tokens"
                )
            return super().get_stats(start, end, ref_date, live=live)

        stats = _parse_hf_usage(payload)
        if stats is None:
            _warn_hf_api(
                "unrecognized billing usage payload; "
                "falling back to the local usage log."
            )
            return super().get_stats(start, end, ref_date, live=live)
        return stats


def discover_collectors(usage_file=None):
    """Return provider collectors.

    OpenRouter and HuggingFace are represented by dedicated API-backed
    collectors. Other providers are discovered from burnctl's usage JSONL
    file.
    """
    entries = _load_entries(usage_file)
    supported_providers = set(_PROVIDER_META)
    providers = sorted(
        set(
            e["provider"]
            for e in entries
            if e["provider"] in supported_providers
        )
    )

    collectors: List[BaseCollector] = [
        OpenRouterCollector(),
        HuggingFaceCollector(usage_file),
    ]

    for pid in providers:
        if pid in ("openrouter", "huggingface"):
            continue
        meta = _PROVIDER_META.get(pid, {})
        display_name = meta.get("name", pid.title())
        upgrade_url = meta.get("upgrade_url", "")
        collectors.append(
            ApiUsageCollector(pid, display_name, usage_file, upgrade_url),
        )
    return collectors
