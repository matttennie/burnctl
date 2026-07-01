"""Antigravity CLI usage collector.

Parses session data from:
~/.gemini/antigravity-cli/tmp/*/chats/session-*.json
~/.gemini/antigravity/tmp/*/chats/session-*.json
~/.gemini/tmp/*/chats/session-*.json
Each session file contains messages with per-turn token counts and model info.
"""

import glob
import json
import os
from datetime import datetime

from burnctl.collectors.base import (
    BaseCollector,
    _check_file_size,
    load_with_stat_cache,
)
from burnctl.pricing import get_agent_pricing, get_model_pricing_for_time

_DEFAULT_CHAT_PATTERN = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity-cli", "tmp", "*", "chats", "session-*.json*")
_CHAT_PATTERN = _DEFAULT_CHAT_PATTERN

_ALT_PATTERNS = [
    os.path.join(os.path.expanduser("~"), ".gemini", "antigravity", "tmp", "*", "chats", "session-*.json*"),
    os.path.join(os.path.expanduser("~"), ".gemini", "tmp", "*", "chats", "session-*.json*"),
]


def _get_session_files():
    files = glob.glob(_CHAT_PATTERN)
    if _CHAT_PATTERN == _DEFAULT_CHAT_PATTERN:
        for pattern in _ALT_PATTERNS:
            files.extend(glob.glob(pattern))
    return sorted(list(set(files)))


def _parse_iso(ts):
    """Parse an ISO 8601 timestamp, handling Z suffix for Python < 3.11."""
    if not ts:
        return None
    ts = ts.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def _load_session(fpath):
    """Load one session JSON or JSONL file, returning None when unusable."""
    if not _check_file_size(fpath):
        return None
    try:
        if fpath.endswith(".jsonl"):
            session = {"messages": []}
            with open(fpath, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    if "sessionId" in obj:
                        session["sessionId"] = obj["sessionId"]
                    if "startTime" in obj:
                        session["startTime"] = obj["startTime"]
                    if "$set" in obj:
                        set_val = obj["$set"]
                        if isinstance(set_val, dict) and "messages" in set_val:
                            session["messages"].extend(set_val["messages"])
                    elif "type" in obj and "id" in obj:
                        session["messages"].append(obj)
            return session
        else:
            with open(fpath, encoding="utf-8-sig", errors="replace") as f:
                session = json.load(f)
            return session if isinstance(session, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


# Live/top-mode parse cache: path -> ((st_mtime_ns, st_size), session).
_SESSION_JSON_CACHE = {}  # type: dict


def _load_session_cached(fpath):
    """Load a session file, reusing the cached result when unchanged."""
    return load_with_stat_cache(_SESSION_JSON_CACHE, fpath, _load_session)


class AntigravityCollector(BaseCollector):
    """Collector for Antigravity CLI session data."""

    @property
    def name(self):
        return "Antigravity CLI"

    @property
    def id(self):
        return "antigravity"

    def is_available(self):
        return bool(_get_session_files())

    def get_stats(self, start, end, ref_date, live=False):
        session_files = _get_session_files()
        if not session_files:
            return None

        pricing = get_agent_pricing("antigravity") or {}
        default_price = {"input": 0.15, "output": 0.60, "cache_read": 0.04}

        # Period and all-time accumulators
        p_messages = 0
        p_sessions = 0
        p_input_tokens = 0
        p_output_tokens = 0
        p_cost = 0.0
        p_tool_calls = 0
        p_model_usage = {}  # type: dict
        a_model_usage = {}  # type: dict

        a_messages = 0
        a_sessions = 0
        a_cost = 0.0
        first_session = None
        last_session = None

        start_str = start.strftime("%Y-%m-%d")
        end_str = end.strftime("%Y-%m-%d")

        for fpath in session_files:
            session = (
                _load_session_cached(fpath) if live else _load_session(fpath)
            )
            if session is None:
                continue

            messages = session.get("messages", [])
            if not messages:
                continue

            # Determine session date from startTime or first message timestamp
            session_start = _parse_iso(session.get("startTime", ""))
            if not session_start and messages:
                session_start = _parse_iso(messages[0].get("timestamp", ""))
            if not session_start:
                continue

            session_date = session_start.strftime("%Y-%m-%d")

            # Track earliest/latest session
            if first_session is None or session_date < first_session:
                first_session = session_date
            if last_session is None or session_date > last_session:
                last_session = session_date

            # Count all-time stats for this session
            sess_user_msgs = 0
            sess_cost = 0.0

            for msg in messages:
                msg_type = msg.get("type", "")

                if msg_type == "user":
                    sess_user_msgs += 1
                elif msg_type in ("gemini", "antigravity"):
                    tokens = msg.get("tokens", {})
                    model = msg.get("model", "unknown")
                    msg_ts = _parse_iso(msg.get("timestamp", ""))
                    model_price = get_model_pricing_for_time(
                        "antigravity", model, msg_ts or session_start,
                    ) or pricing.get(model, default_price)

                    inp = tokens.get("input", 0)
                    out = tokens.get("output", 0)
                    cached = tokens.get("cached", 0)
                    non_cached = max(inp - cached, 0)

                    cost = (
                        non_cached * model_price.get("input", 0.15) / 1_000_000
                        + out * model_price.get("output", 0.60) / 1_000_000
                        + cached * model_price.get("cache_read", 0.04) / 1_000_000
                    )
                    sess_cost += cost

                    # All-time Model usage
                    if model not in a_model_usage:
                        a_model_usage[model] = {"inputTokens": 0, "outputTokens": 0, "cachedTokens": 0}
                    a_model_usage[model]["inputTokens"] += non_cached
                    a_model_usage[model]["outputTokens"] += out
                    a_model_usage[model]["cachedTokens"] += cached

                    # Check if this message is in period
                    msg_date = msg_ts.strftime("%Y-%m-%d") if msg_ts else session_date

                    if start_str <= msg_date < end_str:
                        p_input_tokens += non_cached
                        p_output_tokens += out
                        p_cost += cost

                        # Model usage
                        if model not in p_model_usage:
                            p_model_usage[model] = {"inputTokens": 0, "outputTokens": 0, "cachedTokens": 0}
                        p_model_usage[model]["inputTokens"] += non_cached
                        p_model_usage[model]["outputTokens"] += out
                        p_model_usage[model]["cachedTokens"] += cached

                    # Count tool calls
                    tool_calls = msg.get("toolCalls", [])
                    if tool_calls and start_str <= (msg_ts.strftime("%Y-%m-%d") if msg_ts else session_date) < end_str:
                        p_tool_calls += len(tool_calls)

            a_sessions += 1
            a_messages += sess_user_msgs
            a_cost += sess_cost

            # Period session/message counting
            if start_str <= session_date < end_str:
                p_sessions += 1
                for msg in messages:
                    if msg.get("type") == "user":
                        msg_ts = _parse_iso(msg.get("timestamp", ""))
                        msg_date = msg_ts.strftime("%Y-%m-%d") if msg_ts else session_date
                        if start_str <= msg_date < end_str:
                            p_messages += 1

        if a_sessions == 0:
            return None

        return {
            "messages": p_messages,
            "sessions": p_sessions,
            "input_tokens": p_input_tokens,
            "output_tokens": p_output_tokens,
            "period_cost": p_cost,
            "alltime_cost": a_cost,
            "model_usage": p_model_usage,
            "alltime_model_usage": a_model_usage,
            "first_session": first_session or "",
            "last_active": last_session or "",
            "total_messages": a_messages,
            "total_sessions": a_sessions,
            "tool_calls": p_tool_calls,
        }

    def get_upgrade_url(self):
        return "https://aistudio.google.com/app/plan_management"

    def get_plan_info(self, config):
        from burnctl.config import ANTIGRAVITY_PLAN_PRICES
        plan = config.get("agent_plans", {}).get("antigravity")
        if not plan:
            plan = config.get("antigravity_plan", "free")
        price = ANTIGRAVITY_PLAN_PRICES.get(plan, 0)
        agent_bd = config.get("agent_billing_days", {}).get("antigravity")
        if not agent_bd:
            agent_bd = config.get("antigravity_billing_day", 0)
        bd = agent_bd if agent_bd else config.get("billing_day", 1)
        return {
            "plan_name": plan,
            "plan_price": price,
            "billing_day": bd,
            "interval": "mo",
        }
