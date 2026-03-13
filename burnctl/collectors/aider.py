"""Aider usage collector.

Parses ``.aider.chat.history.md`` files for inline cost data.  Aider
writes cost information in the form::

    Tokens: 1.5k sent, 2.1k received. Cost: $0.03

The k/M suffixes are expanded (1.5k -> 1500, 2.1M -> 2100000) and the
dollar amounts are summed for the billing period.
"""

import os
import re

from burnctl.collectors.base import BaseCollector

_HISTORY_NAME = ".aider.chat.history.md"

# Directories to scan (relative to $HOME), searched recursively up to depth 2.
_SEARCH_ROOTS = ("", "Desktop", "Documents", "Projects")

_COST_RE = re.compile(
    r"Tokens:\s+([\d.]+[kKmM]?)\s+sent,\s+([\d.]+[kKmM]?)\s+received.*?Cost:\s+\$([\d.]+)"
)


def _expand_suffix(value):
    """Convert a human-friendly token count (e.g. '1.5k') to an int."""
    value = value.strip()
    if not value:
        return 0
    suffix = value[-1].lower()
    if suffix == "k":
        return int(float(value[:-1]) * 1_000)
    if suffix == "m":
        return int(float(value[:-1]) * 1_000_000)
    return int(float(value))


def _find_history_files():
    """Return a list of all ``.aider.chat.history.md`` paths found."""
    home = os.path.expanduser("~")
    found = []

    for root_rel in _SEARCH_ROOTS:
        root = os.path.join(home, root_rel) if root_rel else home
        if not os.path.isdir(root):
            continue

        # Check the root itself
        candidate = os.path.join(root, _HISTORY_NAME)
        if os.path.isfile(candidate):
            found.append(candidate)

        # Walk up to depth 2 below root
        if root_rel:
            for dirpath, dirnames, filenames in os.walk(root):
                depth = dirpath[len(root):].count(os.sep)
                if depth >= 2:
                    dirnames.clear()
                    continue
                if _HISTORY_NAME in filenames:
                    full = os.path.join(dirpath, _HISTORY_NAME)
                    if full not in found:
                        found.append(full)

    return found


class AiderCollector(BaseCollector):
    """Collector for Aider chat-history cost data."""

    @property
    def name(self):
        return "Aider"

    @property
    def id(self):
        return "aider"

    # ── Detection ─────────────────────────────────────────────────

    def is_available(self):
        """Return *True* if at least one history file is found."""
        return len(_find_history_files()) > 0

    # ── Stats collection ──────────────────────────────────────────

    def get_stats(self, start, end, ref_date):
        """Parse all discovered history files and sum cost data.

        Because Aider history files don't include per-line timestamps,
        we use the file's modification time as a rough filter: files
        modified before *start* are skipped entirely.
        """
        files = _find_history_files()
        if not files:
            return None

        total_sent = 0
        total_received = 0
        period_cost = 0.0
        match_count = 0

        start_ts = start.timestamp()

        for path in files:
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue

            # Rough filter: skip files not touched during the period.
            if mtime < start_ts:
                continue

            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
            except OSError:
                continue

            for m in _COST_RE.finditer(content):
                sent = _expand_suffix(m.group(1))
                received = _expand_suffix(m.group(2))
                cost = float(m.group(3))

                total_sent += sent
                total_received += received
                period_cost += cost
                match_count += 1

        if match_count == 0:
            return None

        return {
            "messages": match_count,
            "sessions": 0,
            "output_tokens": total_received,
            "period_cost": period_cost,
            "alltime_cost": period_cost,
            "model_usage": {},
            "daily_messages": {},
            "first_session": "",
            "total_messages": match_count,
            "total_sessions": 0,
            "tool_calls": 0,
            "spark_data": [],
        }

    # ── Plan / billing ────────────────────────────────────────────

    def get_upgrade_url(self):
        return "https://aider.chat/"

    def get_plan_info(self, config):
        return {
            "plan_name": "pay-as-you-go",
            "plan_price": 0,
            "billing_day": config.get("billing_day", 1),
            "interval": "mo",
        }
