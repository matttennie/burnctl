"""Golden-file snapshot tests for render output.

These verify that the visual output of each render function does not
regress.  On the first run the golden files are created; subsequent
runs compare against them.  Delete the snapshot file to regenerate.

Python 3.8 compatible.
"""

import os
from unittest.mock import patch

from burnctl.report import (
    render_accessible,
    render_compact,
    render_full,
    render_json,
)

# ── Snapshot infrastructure ───────────────────────────────────────────

SNAPSHOT_DIR = os.path.join(os.path.dirname(__file__), "snapshots")


def _snapshot_path(name):
    return os.path.join(SNAPSHOT_DIR, name)


def assert_snapshot(output, name):
    """Compare output against golden file.  Create if missing."""
    path = _snapshot_path(name)
    if not os.path.exists(path):
        os.makedirs(SNAPSHOT_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(output)
        return  # First run creates the snapshot
    with open(path, encoding="utf-8") as f:
        expected = f.read()
    assert output == expected, (
        "Snapshot mismatch for {name}. "
        "Delete {path} to regenerate.".format(name=name, path=path)
    )


# ── Deterministic test data ──────────────────────────────────────────

SNAPSHOT_STATS = {
    "agents": [
        {
            "id": "claude",
            "name": "Claude Code",
            "plan_name": "max5x",
            "plan_price": 100,
            "interval": "mo",
            "period_start": "2026-03-10",
            "period_end": "2026-04-10",
            "days_elapsed": 3,
            "days_remaining": 28,
            "total_days": 31,
            "pace_pct": 4.6,
            "projected_cost": 47.53,
            "messages": 1931,
            "sessions": 5,
            "output_tokens": 185081,
            "tool_calls": 461,
            "period_cost": 4.63,
            "alltime_cost": 844.12,
            "value_ratio": 4.2,
            "model_usage": {
                "claude-opus-4-5": {
                    "inputTokens": 500000,
                    "outputTokens": 473958,
                },
                "claude-opus-4-6": {
                    "inputTokens": 1200000,
                    "outputTokens": 1034712,
                },
            },
            "input_tokens": None,
            "first_session": "2026-01-10",
            "total_messages": 29328,
            "total_sessions": 95,
        },
        {
            "id": "gemini",
            "name": "Gemini CLI",
            "plan_name": "pay-as-you-go",
            "plan_price": 0,
            "interval": "mo",
            "period_start": "2026-03-01",
            "period_end": "2026-04-01",
            "days_elapsed": 12,
            "days_remaining": 19,
            "total_days": 31,
            "pace_pct": 0,
            "projected_cost": 0,
            "messages": 57,
            "sessions": 18,
            "output_tokens": 45000,
            "tool_calls": 379,
            "period_cost": 4.10,
            "alltime_cost": 4.10,
            "value_ratio": 0,
            "model_usage": {
                "gemini-2.5-flash": {
                    "inputTokens": 100000,
                    "outputTokens": 45000,
                },
            },
            "input_tokens": 100000,
            "first_session": "2026-02-12",
            "total_messages": 57,
            "total_sessions": 18,
        },
    ],
    "total_period_cost": 8.73,
    "today": "2026-03-13",
}


def _single_agent_stats():
    """Stats dict containing only the Claude agent."""
    return {
        "agents": [SNAPSHOT_STATS["agents"][0]],
        "total_period_cost": SNAPSHOT_STATS["agents"][0]["period_cost"],
        "today": SNAPSHOT_STATS["today"],
    }


# ── Mock terminal size for deterministic column widths ───────────────

_TERM_PATCH = patch(
    "os.get_terminal_size",
    return_value=os.terminal_size((100, 40)),
)


# ── Snapshot tests ───────────────────────────────────────────────────


class TestRenderSnapshots:
    """Golden-file regression tests for every render function."""

    def test_full_multi(self):
        with _TERM_PATCH:
            output = render_full(SNAPSHOT_STATS, use_color=False)
        assert_snapshot(output, "render_full_multi.txt")

    def test_full_single(self):
        with _TERM_PATCH:
            output = render_full(_single_agent_stats(), use_color=False)
        assert_snapshot(output, "render_full_single.txt")

    def test_full_simple(self):
        with _TERM_PATCH:
            output = render_full(
                SNAPSHOT_STATS, simple=True, use_color=False,
            )
        assert_snapshot(output, "render_full_simple.txt")

    def test_compact(self):
        output = render_compact(SNAPSHOT_STATS)
        assert_snapshot(output, "render_compact.txt")

    def test_accessible(self):
        output = render_accessible(SNAPSHOT_STATS)
        assert_snapshot(output, "render_accessible.txt")

    def test_json(self):
        output = render_json(SNAPSHOT_STATS)
        assert_snapshot(output, "render_json.txt")
