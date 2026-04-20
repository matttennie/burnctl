"""Multi-agent pricing dispatcher.

Each agent has its own pricing table (or delegates to an external package).
``get_agent_pricing`` is the single entry-point for the rest of burnctl.
"""

import json
import os
from typing import Dict, Optional
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

# ── Gemini (per-million-token rates, USD) ────────────────────────

GEMINI_PRICING = {
    "gemini-3.1-pro-preview": {"input": 2.00, "output": 12.0, "cache_read": 0.20},
    "gemini-3.1-flash-lite": {"input": 0.25, "output": 1.50, "cache_read": 0.025},
    "gemini-3-flash-preview": {"input": 0.50, "output": 3.0, "cache_read": 0.05},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.0, "cache_read": 0.125},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50, "cache_read": 0.03},
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40, "cache_read": 0.01},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40, "cache_read": 0.025},
    "gemini-2.0-flash-lite": {"input": 0.075, "output": 0.30, "cache_read": 0.02},
}

# ── OpenAI / Codex (per-million-token rates, USD) ───────────────

OPENAI_PRICING = {
    "gpt-5.4-pro": {"input": 30.0, "output": 180.0},
    "gpt-5.4": {"input": 2.50, "output": 15.0, "cache_read": 0.25},
    "gpt-5.4-mini": {"input": 0.75, "output": 4.50, "cache_read": 0.075},
    "gpt-5.4-nano": {"input": 0.20, "output": 1.25, "cache_read": 0.02},
    "gpt-5.3-chat": {"input": 1.75, "output": 14.0, "cache_read": 0.175},
    "gpt-5.3-codex": {"input": 1.75, "output": 14.0, "cache_read": 0.175},
    "gpt-5.2-codex": {"input": 1.75, "output": 14.0, "cache_read": 0.175},
    "gpt-4o": {"input": 2.50, "output": 10.0, "cache_read": 1.25},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60, "cache_read": 0.075},
    "o3-pro": {"input": 20.0, "output": 80.0},
    "o3": {"input": 2.0, "output": 8.0, "cache_read": 0.50},
    "o3-mini": {"input": 1.10, "output": 4.40, "cache_read": 0.55},
    "o4-mini": {"input": 0.55, "output": 2.20, "cache_read": 0.275},
    "codex-mini": {"input": 0.75, "output": 3.0, "cache_read": 0.025},
}

_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_OPENROUTER_KEY_ENV_VARS = (
    "OPENROUTER_MGMT_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENROUTER_ORCHARD_API_KEY",
)
_OPENROUTER_PRICING_TTL_SECONDS = 60
_OPENROUTER_PRICING_CACHE: Optional[Dict[str, Dict[str, float]]] = None
_OPENROUTER_PRICING_CACHE_TS = 0.0

_PRICING_HISTORY_DIR = os.path.join(
    os.path.expanduser("~"), ".local", "share", "burnctl",
)
_PRICING_HISTORY_FILE = os.path.join(_PRICING_HISTORY_DIR, "pricing-history.json")
_HISTORY_TRACKED_AGENTS = {"gemini", "codex"}


def _snapshot_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_effective_from(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(
            timezone.utc,
        )
    except (ValueError, TypeError):
        return None


def _copy_pricing_table(table):
    copied = {}
    for model, rates in (table or {}).items():
        if isinstance(rates, dict):
            copied[str(model)] = dict(rates)
    return copied


def _load_pricing_history():
    if not os.path.isfile(_PRICING_HISTORY_FILE):
        return {}
    try:
        with open(_PRICING_HISTORY_FILE, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_pricing_history(history):
    try:
        os.makedirs(_PRICING_HISTORY_DIR, exist_ok=True)
        with open(_PRICING_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, sort_keys=True)
            f.write("\n")
    except OSError:
        return


def _record_pricing_snapshot(agent_id, pricing_table):
    if agent_id not in _HISTORY_TRACKED_AGENTS:
        return
    table = _copy_pricing_table(pricing_table)
    if not table:
        return
    history = _load_pricing_history()
    rows = history.get(agent_id)
    if not isinstance(rows, list):
        rows = []
    if rows and isinstance(rows[-1], dict) and rows[-1].get("pricing") == table:
        return
    rows.append({
        "effective_from": _snapshot_now_iso(),
        "pricing": table,
    })
    history[agent_id] = rows
    _save_pricing_history(history)


def get_agent_pricing_for_time(agent_id, when=None):
    """Return the pricing table effective at *when* for *agent_id*."""
    current = get_agent_pricing(agent_id)
    if agent_id not in _HISTORY_TRACKED_AGENTS or when is None:
        return current

    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    else:
        when = when.astimezone(timezone.utc)

    history = _load_pricing_history()
    rows = history.get(agent_id)
    if not isinstance(rows, list) or not rows:
        return current

    selected = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        effective_from = _parse_effective_from(row.get("effective_from"))
        pricing = row.get("pricing")
        if effective_from is None or not isinstance(pricing, dict):
            continue
        if effective_from <= when:
            selected = _copy_pricing_table(pricing)
        elif selected is None:
            selected = _copy_pricing_table(pricing)
            break
        else:
            break

    return selected if selected is not None else current


def get_model_pricing_for_time(agent_id, model_id, when=None):
    """Return pricing for one model, resolved against historical snapshots."""
    pricing_table = get_agent_pricing_for_time(agent_id, when) or {}
    if model_id in pricing_table:
        return pricing_table[model_id]
    stripped = str(model_id)
    if agent_id in ("claude", "codex", "gemini"):
        import re
        stripped = re.sub(r"-(\d{8}|latest)$", "", stripped)
    return pricing_table.get(stripped, {})


def _openrouter_api_key():
    for name in _OPENROUTER_KEY_ENV_VARS:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _float_or_none(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _get_openrouter_pricing():
    global _OPENROUTER_PRICING_CACHE, _OPENROUTER_PRICING_CACHE_TS
    now = time.time()
    if (
        _OPENROUTER_PRICING_CACHE is not None
        and (now - _OPENROUTER_PRICING_CACHE_TS) < _OPENROUTER_PRICING_TTL_SECONDS
    ):
        return dict(_OPENROUTER_PRICING_CACHE)

    headers = {
        "Accept": "application/json",
        "User-Agent": "burnctl/0.1.0",
    }
    api_key = _openrouter_api_key()
    if api_key:
        headers["Authorization"] = "Bearer " + api_key

    req = urllib.request.Request(_OPENROUTER_MODELS_URL, headers=headers)
    pricing = {}
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError):
        if _OPENROUTER_PRICING_CACHE is not None:
            return dict(_OPENROUTER_PRICING_CACHE)
        _OPENROUTER_PRICING_CACHE = {}
        _OPENROUTER_PRICING_CACHE_TS = now
        return {}

    if not isinstance(payload, dict):
        if _OPENROUTER_PRICING_CACHE is not None:
            return dict(_OPENROUTER_PRICING_CACHE)
        _OPENROUTER_PRICING_CACHE = {}
        _OPENROUTER_PRICING_CACHE_TS = now
        return {}

    rows = payload.get("data", [])
    if not isinstance(rows, list):
        if _OPENROUTER_PRICING_CACHE is not None:
            return dict(_OPENROUTER_PRICING_CACHE)
        _OPENROUTER_PRICING_CACHE = {}
        _OPENROUTER_PRICING_CACHE_TS = now
        return {}

    for row in rows:
        if not isinstance(row, dict):
            continue
        model_id = row.get("id")
        pricing_obj = row.get("pricing", {})
        if not model_id or not isinstance(pricing_obj, dict):
            continue
        prompt = _float_or_none(pricing_obj.get("prompt"))
        completion = _float_or_none(pricing_obj.get("completion"))
        if prompt is None and completion is None:
            continue
        entry = {}
        if prompt is not None:
            entry["input"] = prompt * 1_000_000
        if completion is not None:
            entry["output"] = completion * 1_000_000
        reasoning = _float_or_none(pricing_obj.get("internal_reasoning"))
        if reasoning is not None:
            entry["reasoning"] = reasoning * 1_000_000
        if entry:
            pricing[str(model_id)] = entry

    _OPENROUTER_PRICING_CACHE = pricing
    _OPENROUTER_PRICING_CACHE_TS = now
    return dict(pricing)


def get_agent_pricing(agent_id):
    """Return the pricing table for *agent_id*.

    Returns
    -------
    dict or None
        A ``{model_id: {input, output, ...}}`` mapping.
        An empty ``{}`` for local/free models.
    """
    if agent_id == "claude":
        try:
            from claude_usage.pricing import get_pricing
            return get_pricing()
        except ImportError:
            return {
                "claude-opus-4-6": {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_create": 6.25},
                "claude-opus-4-5": {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_create": 6.25},
                "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_create": 3.75},
                "claude-sonnet-4-5": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_create": 3.75},
                "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cache_read": 0.10, "cache_create": 1.25},
            }

    if agent_id == "gemini":
        result = dict(GEMINI_PRICING)
        _record_pricing_snapshot(agent_id, result)
        return result

    if agent_id == "codex":
        result = dict(OPENAI_PRICING)
        _record_pricing_snapshot(agent_id, result)
        return result

    if agent_id == "openrouter":
        return _get_openrouter_pricing()

    # Local models, unknown agents -- $0.
    return {}
