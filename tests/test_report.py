"""Comprehensive tests for burnctl.report module."""

import csv
import json
import os
import re
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from burnctl.report import (
    _FallbackTheme,
    _strip_ansi,
    aggregate_stats,
    compute_period,
    export_csv,
    fmt,
    fmt_usd,
    render_accessible,
    render_compact,
    render_diff,
    render_full,
    render_json,
    sparkline,
    _safe_replace_day,
)


# ── Helpers ──────────────────────────────────────────────────────

_ANSI_RE = re.compile(r'\033\[[0-9;]*m')


def _make_collector(
    collector_id="test",
    name="Test Agent",
    plan_name="pro",
    plan_price=20.0,
    billing_day=1,
    interval="mo",
    stats=None,
):
    """Create a mock collector with reasonable defaults."""
    c = MagicMock()
    c.id = collector_id
    c.name = name
    c.get_plan_info.return_value = {
        "plan_name": plan_name,
        "plan_price": plan_price,
        "billing_day": billing_day,
        "interval": interval,
    }
    default_stats = {
        "messages": 100,
        "sessions": 10,
        "output_tokens": 50000,
        "tool_calls": 25,
        "period_cost": 12.50,
        "alltime_cost": 150.00,
        "model_usage": {
            "claude-3-opus-20251101": {"outputTokens": 30000},
            "claude-3-sonnet-20251101": {"outputTokens": 20000},
        },
        "daily_messages": {"2025-01-01": 5, "2025-01-02": 10},
        "spark_data": [5, 10, 15, 20, 8, 3],
        "first_session": "2024-06-15",
        "total_messages": 500,
        "total_sessions": 50,
    }
    if stats is not None:
        default_stats.update(stats)
    c.get_stats.return_value = default_stats
    return c


def _make_agent_data(**overrides):
    """Build a single agent data dict suitable for stats['agents']."""
    base = {
        "id": "test",
        "name": "Test Agent",
        "plan_name": "pro",
        "plan_price": 20.0,
        "interval": "mo",
        "period_start": "2025-01-01",
        "period_end": "2025-02-01",
        "days_elapsed": 15,
        "days_remaining": 16,
        "total_days": 31,
        "pace_pct": 62.5,
        "projected_cost": 25.81,
        "messages": 100,
        "sessions": 10,
        "output_tokens": 50000,
        "tool_calls": 25,
        "period_cost": 12.50,
        "alltime_cost": 150.00,
        "value_ratio": 1.1,
        "model_usage": {
            "claude-3-opus-20251101": {"outputTokens": 30000},
            "claude-3-sonnet-20251101": {"outputTokens": 20000},
        },
        "daily_messages": {},
        "spark_data": [5, 10, 15, 20, 8, 3],
        "first_session": "2024-06-15",
        "total_messages": 500,
        "total_sessions": 50,
    }
    base.update(overrides)
    return base


def _make_stats(agents=None, total_period_cost=None, today="2025-01-16"):
    """Build a complete stats dict suitable for render functions."""
    if agents is None:
        agents = [_make_agent_data()]
    if total_period_cost is None:
        total_period_cost = sum(a["period_cost"] for a in agents)
    return {
        "agents": agents,
        "total_period_cost": round(total_period_cost, 2),
        "today": today,
    }


# ── _safe_replace_day ────────────────────────────────────────────


class TestSafeReplaceDay:
    def test_normal_day(self):
        dt = datetime(2025, 3, 15)
        result = _safe_replace_day(dt, 10)
        assert result.day == 10
        assert result.month == 3
        assert result.year == 2025

    def test_clamp_feb_non_leap(self):
        dt = datetime(2025, 2, 15)
        result = _safe_replace_day(dt, 31)
        assert result.day == 28

    def test_clamp_feb_leap(self):
        dt = datetime(2024, 2, 15)
        result = _safe_replace_day(dt, 31)
        assert result.day == 29

    def test_clamp_april_30(self):
        dt = datetime(2025, 4, 10)
        result = _safe_replace_day(dt, 31)
        assert result.day == 30

    def test_exact_day(self):
        dt = datetime(2025, 1, 20)
        result = _safe_replace_day(dt, 31)
        assert result.day == 31

    def test_day_1(self):
        dt = datetime(2025, 7, 20)
        result = _safe_replace_day(dt, 1)
        assert result.day == 1


# ── compute_period ───────────────────────────────────────────────


class TestComputePeriod:
    """Test billing period calculation with deterministic dates."""

    @patch("burnctl.report.datetime")
    def test_billing_day_before_today(self, mock_dt):
        """When today's day >= billing_day, period starts this month."""
        mock_dt.now.return_value = datetime(2025, 1, 15)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        start, end, today_dt = compute_period(1)
        assert start == datetime(2025, 1, 1)
        assert end == datetime(2025, 2, 1)
        assert today_dt == datetime(2025, 1, 15)

    @patch("burnctl.report.datetime")
    def test_billing_day_after_today(self, mock_dt):
        """When today's day < billing_day, period started last month."""
        mock_dt.now.return_value = datetime(2025, 1, 5)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        start, end, today_dt = compute_period(15)
        assert start == datetime(2024, 12, 15)
        assert end == datetime(2025, 1, 15)

    @patch("burnctl.report.datetime")
    def test_billing_day_equals_today(self, mock_dt):
        """When today's day == billing_day, period starts today."""
        mock_dt.now.return_value = datetime(2025, 1, 15)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        start, end, today_dt = compute_period(15)
        assert start == datetime(2025, 1, 15)
        assert end == datetime(2025, 2, 15)

    @patch("burnctl.report.datetime")
    def test_billing_day_31_feb(self, mock_dt):
        """Billing day 31 should clamp in February."""
        mock_dt.now.return_value = datetime(2025, 2, 15)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        start, end, today_dt = compute_period(31)
        # day 15 < 31 so period started last month
        assert start == datetime(2025, 1, 31)
        # end clamped to Feb 28
        assert end == datetime(2025, 2, 28)

    @patch("burnctl.report.datetime")
    def test_billing_day_29_feb_leap(self, mock_dt):
        """Billing day 29 in Feb of a leap year should work."""
        mock_dt.now.return_value = datetime(2024, 2, 29)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        start, end, today_dt = compute_period(29)
        assert start == datetime(2024, 2, 29)
        assert end == datetime(2024, 3, 29)

    @patch("burnctl.report.datetime")
    def test_offset_minus_1(self, mock_dt):
        """offset=-1 should shift to the previous month."""
        mock_dt.now.return_value = datetime(2025, 3, 15)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        start, end, today_dt = compute_period(1, offset=-1)
        # today_dt shifted to Feb 2025
        assert today_dt.month == 2
        assert today_dt.year == 2025
        # Billing day 1, day 15 >= 1, so start should be Feb 1
        assert start.month == 2
        assert start.day == 1

    @patch("burnctl.report.datetime")
    def test_offset_minus_1_january_wraps_to_dec(self, mock_dt):
        """offset=-1 from January should go to December of previous year."""
        mock_dt.now.return_value = datetime(2025, 1, 15)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        start, end, today_dt = compute_period(1, offset=-1)
        assert today_dt.month == 12
        assert today_dt.year == 2024

    @patch("burnctl.report.datetime")
    def test_offset_minus_1_clamps_day(self, mock_dt):
        """offset=-1 from March 31 should clamp to Feb 28."""
        mock_dt.now.return_value = datetime(2025, 3, 31)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        start, end, today_dt = compute_period(1, offset=-1)
        assert today_dt.month == 2
        assert today_dt.day == 28

    @patch("burnctl.report.datetime")
    def test_offset_plus_1_triggers_month_over_12(self, mock_dt):
        """Lines 120-121: positive offset causing month > 12 wraps year."""
        mock_dt.now.return_value = datetime(2025, 12, 15)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        start, end, today_dt = compute_period(1, offset=1)
        # month 12 + 1 = 13 -> subtract 12, year += 1
        assert today_dt.month == 1
        assert today_dt.year == 2026

    @patch("burnctl.report.datetime")
    def test_offset_plus_2_from_november(self, mock_dt):
        """Large positive offset: Nov + 2 = Jan next year."""
        mock_dt.now.return_value = datetime(2025, 11, 10)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        start, end, today_dt = compute_period(5, offset=2)
        # month 11 + 2 = 13 -> 1, year 2026
        assert today_dt.month == 1
        assert today_dt.year == 2026

    @patch("burnctl.report.datetime")
    def test_year_boundary_billing_after_today(self, mock_dt):
        """Test Jan 5 with billing day 15: period spans Dec-Jan."""
        mock_dt.now.return_value = datetime(2025, 1, 5)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        start, end, today_dt = compute_period(15)
        assert start == datetime(2024, 12, 15)
        assert end == datetime(2025, 1, 15)

    @patch("burnctl.report.datetime")
    def test_december_billing_spans_to_january(self, mock_dt):
        """Test Dec 20 with billing day 15: end date goes to Jan."""
        mock_dt.now.return_value = datetime(2025, 12, 20)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        start, end, today_dt = compute_period(15)
        assert start == datetime(2025, 12, 15)
        assert end == datetime(2026, 1, 15)


# ── aggregate_stats ──────────────────────────────────────────────


class TestAggregateStats:
    def test_single_collector(self):
        collector = _make_collector(
            collector_id="claude",
            name="Claude",
            plan_price=20.0,
            billing_day=1,
        )
        config = {"billing_day": 1}
        ref = datetime(2025, 1, 16)

        result = aggregate_stats([collector], config, ref_date=ref)

        assert len(result["agents"]) == 1
        agent = result["agents"][0]
        assert agent["id"] == "claude"
        assert agent["name"] == "Claude"
        assert agent["messages"] == 100
        assert agent["sessions"] == 10
        assert agent["output_tokens"] == 50000
        assert agent["tool_calls"] == 25
        assert agent["period_cost"] == 12.50
        assert agent["alltime_cost"] == 150.00
        assert result["today"] == "2025-01-16"

    def test_multiple_collectors(self):
        c1 = _make_collector(
            collector_id="claude",
            name="Claude",
            stats={"period_cost": 10.0},
        )
        c2 = _make_collector(
            collector_id="gemini",
            name="Gemini",
            stats={"period_cost": 5.0},
        )
        config = {}
        ref = datetime(2025, 1, 16)

        result = aggregate_stats([c1, c2], config, ref_date=ref)

        assert len(result["agents"]) == 2
        assert result["total_period_cost"] == 15.0

    def test_unavailable_collector_returning_none_is_skipped(self):
        c1 = _make_collector(collector_id="claude", name="Claude")
        c2 = _make_collector(collector_id="broken", name="Broken")
        c2.get_stats.return_value = None
        c2.is_available.return_value = False
        config = {}
        ref = datetime(2025, 1, 16)

        result = aggregate_stats([c1, c2], config, ref_date=ref)

        assert len(result["agents"]) == 1
        assert result["agents"][0]["id"] == "claude"

    def test_available_collector_returning_none_shown_inactive(self):
        c1 = _make_collector(collector_id="claude", name="Claude")
        c2 = _make_collector(collector_id="idle", name="Idle")
        c2.get_stats.return_value = None
        c2.is_available.return_value = True
        config = {}
        ref = datetime(2025, 1, 16)

        result = aggregate_stats([c1, c2], config, ref_date=ref)

        assert len(result["agents"]) == 2
        assert result["agents"][0]["id"] == "claude"
        assert result["agents"][1]["id"] == "idle"
        assert result["agents"][1].get("inactive") is True
        assert result["agents"][1]["messages"] == 0

    def test_empty_collectors_list(self):
        result = aggregate_stats([], {}, ref_date=datetime(2025, 1, 16))

        assert result["agents"] == []
        assert result["total_period_cost"] == 0.0
        assert result["today"] == "2025-01-16"

    def test_all_collectors_return_none_unavailable(self):
        c = _make_collector()
        c.get_stats.return_value = None
        c.is_available.return_value = False
        result = aggregate_stats([c], {}, ref_date=datetime(2025, 1, 16))

        assert result["agents"] == []
        assert result["total_period_cost"] == 0.0

    def test_all_collectors_return_none_available(self):
        c = _make_collector()
        c.get_stats.return_value = None
        c.is_available.return_value = True
        result = aggregate_stats([c], {}, ref_date=datetime(2025, 1, 16))

        assert len(result["agents"]) == 1
        assert result["agents"][0].get("inactive") is True

    def test_value_ratio_with_first_session(self):
        c = _make_collector(
            plan_price=20.0,
            stats={
                "alltime_cost": 120.0,
                "first_session": "2024-07-16",
            },
        )
        ref = datetime(2025, 1, 16)
        result = aggregate_stats([c], {}, ref_date=ref)

        agent = result["agents"][0]
        # months_active = (2025-2024)*12 + (1-7) = 6
        # total_paid = 20 * 6 = 120
        # value_ratio = 120 / 120 = 1.0
        assert agent["value_ratio"] == 1.0

    def test_value_ratio_zero_plan_price(self):
        c = _make_collector(
            plan_price=0,
            stats={"alltime_cost": 50.0, "first_session": "2024-06-01"},
        )
        ref = datetime(2025, 1, 16)
        result = aggregate_stats([c], {}, ref_date=ref)

        assert result["agents"][0]["value_ratio"] == 0.0

    def test_value_ratio_invalid_first_session(self):
        c = _make_collector(
            plan_price=20.0,
            stats={"alltime_cost": 100.0, "first_session": "not-a-date"},
        )
        ref = datetime(2025, 1, 16)
        result = aggregate_stats([c], {}, ref_date=ref)

        agent = result["agents"][0]
        # ValueError caught, months_active=1, total_paid=20
        # value_ratio = 100 / 20 = 5.0
        assert agent["value_ratio"] == 5.0

    def test_value_ratio_empty_first_session(self):
        c = _make_collector(
            plan_price=20.0,
            stats={"alltime_cost": 60.0, "first_session": ""},
        )
        ref = datetime(2025, 1, 16)
        result = aggregate_stats([c], {}, ref_date=ref)

        agent = result["agents"][0]
        # first_session empty: months_active=1, total_paid=20
        # value_ratio = 60 / 20 = 3.0
        assert agent["value_ratio"] == 3.0

    def test_pace_pct_calculation(self):
        c = _make_collector(
            plan_price=100.0,
            stats={"period_cost": 50.0},
        )
        ref = datetime(2025, 1, 16)
        result = aggregate_stats([c], {}, ref_date=ref)

        assert result["agents"][0]["pace_pct"] == 50.0

    def test_pace_pct_zero_plan_price(self):
        c = _make_collector(
            plan_price=0,
            stats={"period_cost": 50.0},
        )
        ref = datetime(2025, 1, 16)
        result = aggregate_stats([c], {}, ref_date=ref)

        assert result["agents"][0]["pace_pct"] == 0.0

    def test_projected_cost_zero_elapsed(self):
        """When days_elapsed=0 the projected cost should be 0."""
        c = _make_collector(billing_day=16)
        ref = datetime(2025, 1, 16)
        # ref_date == start => days_elapsed = 0
        result = aggregate_stats([c], {}, ref_date=ref)

        assert result["agents"][0]["projected_cost"] == 0.0

    def test_ref_date_defaults_to_now(self):
        c = _make_collector()
        with patch("burnctl.report.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 6, 15)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_dt.strptime = datetime.strptime
            result = aggregate_stats([c], {})

        assert result["today"] == "2025-06-15"

    def test_offset_forwarded_to_compute_period(self):
        c = _make_collector()
        config = {}
        ref = datetime(2025, 3, 15)

        with patch("burnctl.report.compute_period") as mock_cp:
            mock_cp.return_value = (
                datetime(2025, 2, 1),
                datetime(2025, 3, 1),
                datetime(2025, 2, 15),
            )
            aggregate_stats([c], config, ref_date=ref, offset=-1)
            mock_cp.assert_called_once_with(1, -1)

    def test_model_usage_passthrough(self):
        models = {"gpt-4": {"outputTokens": 10000}}
        c = _make_collector(stats={"model_usage": models})
        ref = datetime(2025, 1, 16)
        result = aggregate_stats([c], {}, ref_date=ref)

        assert result["agents"][0]["model_usage"] == models


# ── fmt / fmt_usd ────────────────────────────────────────────────


class TestFmt:
    def test_zero(self):
        assert fmt(0) == "0"

    def test_small(self):
        assert fmt(42) == "42"

    def test_thousands(self):
        assert fmt(1000) == "1,000"

    def test_millions(self):
        assert fmt(1234567) == "1,234,567"

    def test_negative(self):
        assert fmt(-1000) == "-1,000"


class TestFmtUsd:
    def test_zero(self):
        assert fmt_usd(0) == "$0.00"

    def test_small(self):
        assert fmt_usd(1.5) == "$1.50"

    def test_thousands(self):
        assert fmt_usd(1234.56) == "$1,234.56"

    def test_large(self):
        assert fmt_usd(1000000.99) == "$1,000,000.99"

    def test_rounds(self):
        assert fmt_usd(1.999) == "$2.00"

    def test_negative(self):
        assert fmt_usd(-42.5) == "$-42.50"


# ── sparkline ────────────────────────────────────────────────────


class TestSparkline:
    def test_empty_list(self):
        assert sparkline([]) == ""

    def test_single_value(self):
        result = sparkline([5])
        assert len(result) == 1
        # single value: mn==mx, rng=1
        # (5-5)/1*8 = 0 -> blocks[0] = space
        assert result == " "

    def test_all_same_values(self):
        result = sparkline([5, 5, 5])
        assert len(result) == 3
        # All same: rng=1, all map to index 0 => space
        assert result == "   "

    def test_varied_values(self):
        result = sparkline([0, 4, 8])
        assert len(result) == 3
        # min=0, max=8, rng=8
        # 0 -> blocks[0] = space
        # 4 -> blocks[min(8, int(4/8*8))] = blocks[4]
        # 8 -> blocks[min(8, int(8/8*8))] = blocks[8]
        blocks = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
        assert result[0] == blocks[0]
        assert result[1] == blocks[4]
        assert result[2] == blocks[8]

    def test_two_values(self):
        result = sparkline([0, 10])
        blocks = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
        assert result[0] == blocks[0]
        assert result[1] == blocks[8]

    def test_descending_values(self):
        result = sparkline([10, 5, 0])
        blocks = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
        assert result[0] == blocks[8]
        assert result[2] == blocks[0]


# ── _strip_ansi ──────────────────────────────────────────────────


class TestStripAnsi:
    def test_no_ansi(self):
        assert _strip_ansi("hello world") == "hello world"

    def test_single_code(self):
        assert _strip_ansi("\033[1mhello\033[0m") == "hello"

    def test_multiple_codes(self):
        text = "\033[36m[\033[0m text \033[1;97mtitle\033[0m"
        assert _strip_ansi(text) == "[ text title"

    def test_empty_string(self):
        assert _strip_ansi("") == ""

    def test_complex_codes(self):
        text = "\033[38;5;196mred\033[0m"
        # The regex only handles [0-9;]*m so 38;5;196 matches
        assert _strip_ansi(text) == "red"


# ── _FallbackTheme ───────────────────────────────────────────────


class TestFallbackThemeColorEnabled:
    """Test _FallbackTheme with color enabled (default)."""

    def setup_method(self):
        self.theme = _FallbackTheme(use_color=True)

    def test_border(self):
        result = self.theme.border("X")
        assert "X" in result
        assert "\033[" in result

    def test_border_line(self):
        result = self.theme.border_line("===")
        assert "===" in result
        assert "\033[36m" in result

    def test_title(self):
        result = self.theme.title("TITLE")
        assert "TITLE" in result
        assert "\033[1;97m" in result

    def test_accent(self):
        result = self.theme.accent("text")
        assert "text" in result
        assert "\033[36m" in result

    def test_highlight(self):
        result = self.theme.highlight("text")
        assert "text" in result
        assert "\033[33m" in result

    def test_warm(self):
        result = self.theme.warm("text")
        assert "text" in result
        assert "\033[33m" in result

    def test_success(self):
        result = self.theme.success("text")
        assert "text" in result
        assert "\033[32m" in result

    def test_muted(self):
        result = self.theme.muted("text")
        assert "text" in result
        assert "\033[2m" in result

    def test_bold(self):
        result = self.theme.bold("text")
        assert "text" in result
        assert "\033[1m" in result

    def test_stat_icon_color(self):
        result = self.theme.stat_icon_color(0)
        assert "\033[36m" in result

    def test_stat_icon_color_wraps(self):
        """Index wraps around the 5-color palette."""
        result_0 = self.theme.stat_icon_color(0)
        result_5 = self.theme.stat_icon_color(5)
        assert result_0 == result_5

    def test_progress_bar(self):
        result = self.theme.progress_bar(5, 3, 8)
        assert "\u2588" * 5 in _strip_ansi(result)
        assert "\u2591" * 3 in _strip_ansi(result)
        assert "\033[34m" in result

    def test_value_bar(self):
        result = self.theme.value_bar(3, 2)
        plain = _strip_ansi(result)
        assert len(plain) == 5
        assert "\033[31m" in result
        assert "\033[32m" in result

    def test_model_bar(self):
        result = self.theme.model_bar(4, 2, "opus")
        plain = _strip_ansi(result)
        assert "\u2593" * 4 in plain
        assert "\u2591" * 2 in plain
        assert "\033[36m" in result


class TestFallbackThemeColorDisabled:
    """Test _FallbackTheme with color disabled."""

    def setup_method(self):
        self.theme = _FallbackTheme(use_color=False)

    def test_border_no_ansi(self):
        result = self.theme.border("X")
        assert result == "X"
        assert "\033[" not in result

    def test_title_no_ansi(self):
        result = self.theme.title("TITLE")
        assert result == "TITLE"
        assert "\033[" not in result

    def test_accent_no_ansi(self):
        assert self.theme.accent("text") == "text"

    def test_highlight_no_ansi(self):
        assert self.theme.highlight("text") == "text"

    def test_warm_no_ansi(self):
        assert self.theme.warm("text") == "text"

    def test_success_no_ansi(self):
        assert self.theme.success("text") == "text"

    def test_muted_no_ansi(self):
        assert self.theme.muted("text") == "text"

    def test_bold_no_ansi(self):
        assert self.theme.bold("text") == "text"

    def test_stat_icon_color_empty(self):
        assert self.theme.stat_icon_color(0) == ""

    def test_progress_bar_no_ansi(self):
        result = self.theme.progress_bar(5, 3, 8)
        assert result == "\u2588" * 5 + "\u2591" * 3
        assert "\033[" not in result

    def test_value_bar_no_ansi(self):
        result = self.theme.value_bar(3, 2)
        assert result == "\u2588" * 5
        assert "\033[" not in result

    def test_model_bar_no_ansi(self):
        result = self.theme.model_bar(4, 2, "opus")
        assert result == "\u2593" * 4 + "\u2591" * 2
        assert "\033[" not in result

    def test_border_line_no_ansi(self):
        assert self.theme.border_line("===") == "==="

    def test_wrap_converts_non_string(self):
        """_wrap should convert non-string input to string."""
        result = self.theme.bold(42)
        assert result == "42"


# ── render_full ──────────────────────────────────────────────────


class TestRenderFull:
    """Test the multi-column box renderer."""

    @patch("os.get_terminal_size")
    def test_empty_agents(self, mock_term):
        mock_term.return_value = os.terminal_size((120, 40))
        stats = _make_stats(agents=[])
        result = render_full(stats)
        assert result == "No agent data available."

    @patch("os.get_terminal_size")
    def test_single_agent_title(self, mock_term):
        mock_term.return_value = os.terminal_size((120, 40))
        stats = _make_stats()
        result = render_full(stats, use_color=False)
        assert "TEST AGENT USAGE REPORT" in result

    @patch("os.get_terminal_size")
    def test_multi_agent_title(self, mock_term):
        mock_term.return_value = os.terminal_size((120, 40))
        a1 = _make_agent_data(id="claude", name="Claude")
        a2 = _make_agent_data(id="gemini", name="Gemini")
        stats = _make_stats(agents=[a1, a2])
        result = render_full(stats, use_color=False)
        assert "BURNCTL MULTI-AGENT REPORT" in result

    @patch("os.get_terminal_size")
    def test_contains_period_usage(self, mock_term):
        mock_term.return_value = os.terminal_size((120, 40))
        stats = _make_stats()
        result = render_full(stats, use_color=False)
        assert "PERIOD USAGE" in result
        assert "100" in result  # messages
        assert "10" in result  # sessions

    @patch("os.get_terminal_size")
    def test_simple_mode_skips_value_roi(self, mock_term):
        mock_term.return_value = os.terminal_size((120, 40))
        stats = _make_stats()
        result = render_full(stats, simple=True, use_color=False)
        assert "VALUE & ROI" not in result

    @patch("os.get_terminal_size")
    def test_normal_mode_includes_value_roi(self, mock_term):
        mock_term.return_value = os.terminal_size((120, 40))
        stats = _make_stats()
        result = render_full(stats, simple=False, use_color=False)
        assert "VALUE & ROI" in result

    @patch("os.get_terminal_size")
    def test_no_color_no_ansi(self, mock_term):
        mock_term.return_value = os.terminal_size((120, 40))
        stats = _make_stats()
        result = render_full(stats, use_color=False)
        assert "\033[" not in result

    @patch("os.get_terminal_size")
    def test_color_has_ansi(self, mock_term):
        mock_term.return_value = os.terminal_size((120, 40))
        stats = _make_stats()
        # Patch import to force fallback theme
        with patch.dict("sys.modules", {"claude_usage": None, "claude_usage.colors": None}):
            result = render_full(stats, use_color=True)
        assert "\033[" in result

    @patch("os.get_terminal_size")
    def test_model_breakdown_section(self, mock_term):
        mock_term.return_value = os.terminal_size((120, 40))
        stats = _make_stats()
        result = render_full(stats, use_color=False)
        assert "MODEL BREAKDOWN" in result

    @patch("os.get_terminal_size")
    def test_no_model_breakdown_when_empty(self, mock_term):
        mock_term.return_value = os.terminal_size((120, 40))
        agent = _make_agent_data(model_usage={})
        stats = _make_stats(agents=[agent])
        result = render_full(stats, use_color=False)
        assert "MODEL BREAKDOWN" not in result

    @patch("os.get_terminal_size")
    def test_daily_activity_section(self, mock_term):
        mock_term.return_value = os.terminal_size((120, 40))
        stats = _make_stats()
        result = render_full(stats, use_color=False)
        assert "DAILY ACTIVITY" in result

    @patch("os.get_terminal_size")
    def test_generated_date_footer(self, mock_term):
        mock_term.return_value = os.terminal_size((120, 40))
        stats = _make_stats(today="2025-03-01")
        result = render_full(stats, use_color=False)
        assert "2025-03-01" in result

    @patch("os.get_terminal_size")
    def test_system_total_for_multi_agent(self, mock_term):
        mock_term.return_value = os.terminal_size((120, 40))
        a1 = _make_agent_data(id="claude", name="Claude", period_cost=10.0)
        a2 = _make_agent_data(id="gemini", name="Gemini", period_cost=5.0)
        stats = _make_stats(agents=[a1, a2], total_period_cost=15.0)
        result = render_full(stats, use_color=False)
        assert "System Total" in result

    @patch("os.get_terminal_size")
    def test_no_system_total_for_single_agent(self, mock_term):
        mock_term.return_value = os.terminal_size((120, 40))
        stats = _make_stats()
        result = render_full(stats, use_color=False)
        assert "System Total" not in result

    @patch("os.get_terminal_size")
    def test_terminal_size_fallback(self, mock_term):
        """When get_terminal_size raises, should fall back to 80 cols."""
        mock_term.side_effect = OSError("not a tty")
        stats = _make_stats()
        result = render_full(stats, use_color=False)
        # Should still render without error
        assert "TEST AGENT USAGE REPORT" in result

    @patch("os.get_terminal_size")
    def test_spark_data_from_daily_messages(self, mock_term):
        """When spark_data is empty, it should be built from daily_messages."""
        mock_term.return_value = os.terminal_size((120, 40))
        agent = _make_agent_data(
            spark_data=[],
            daily_messages={"2025-01-01": 5, "2025-01-02": 10},
            period_start="2025-01-01",
            days_elapsed=2,
        )
        stats = _make_stats(agents=[agent])
        result = render_full(stats, use_color=False)
        assert "DAILY ACTIVITY" in result

    @patch("os.get_terminal_size")
    def test_pace_bar_rendered(self, mock_term):
        mock_term.return_value = os.terminal_size((120, 40))
        agent = _make_agent_data(pace_pct=50.0)
        stats = _make_stats(agents=[agent])
        result = render_full(stats, use_color=False)
        assert "50%" in result

    @patch("os.get_terminal_size")
    def test_pace_clamped_at_100(self, mock_term):
        """Pace > 100% should be clamped for the bar display."""
        mock_term.return_value = os.terminal_size((120, 40))
        agent = _make_agent_data(pace_pct=150.0)
        stats = _make_stats(agents=[agent])
        result = render_full(stats, use_color=False)
        # Clamped to 100 for the bar but the label shows "100%"
        assert "100%" in result


# ── Model breakdown percentage labels & token consistency ────────


class TestModelBreakdownPctLabel:
    """Test <1% display for tiny fractions and token consistency between sections."""

    @patch("os.get_terminal_size")
    def test_tiny_fraction_shows_less_than_one_pct(self, mock_term):
        """A model with 1 token out of 1,000,000 total should show '<1%'."""
        mock_term.return_value = os.terminal_size((120, 40))
        agent = _make_agent_data(
            output_tokens=1000000,
            model_usage={
                "claude-3-opus-20251101": {"outputTokens": 999999},
                "claude-3-sonnet-20251101": {"outputTokens": 1},
            },
        )
        stats = _make_stats(agents=[agent])
        result = render_full(stats, use_color=False)
        assert "<1%" in result

    @patch("os.get_terminal_size")
    def test_zero_tokens_shows_zero_pct(self, mock_term):
        """A model with 0 tokens should show '0%', not '<1%'."""
        mock_term.return_value = os.terminal_size((120, 40))
        agent = _make_agent_data(
            output_tokens=1000,
            model_usage={
                "claude-3-opus-20251101": {"outputTokens": 1000},
                "claude-3-sonnet-20251101": {"outputTokens": 0},
            },
        )
        stats = _make_stats(agents=[agent])
        result = render_full(stats, use_color=False)
        assert "0%" in result
        # The zero-token model row itself should NOT show "<1%"
        lines = result.split("\n")
        for line in lines:
            if "sonnet" in line.lower():
                assert "<1%" not in line
                assert "0%" in line

    @patch("os.get_terminal_size")
    def test_fifty_pct_shows_50(self, mock_term):
        """A model with exactly 50% should show '50%'."""
        mock_term.return_value = os.terminal_size((120, 40))
        agent = _make_agent_data(
            output_tokens=2000,
            model_usage={
                "claude-3-opus-20251101": {"outputTokens": 1000},
                "claude-3-sonnet-20251101": {"outputTokens": 1000},
            },
        )
        stats = _make_stats(agents=[agent])
        result = render_full(stats, use_color=False)
        assert "50%" in result

    @patch("os.get_terminal_size")
    def test_multiple_models_one_tiny_fraction(self, mock_term):
        """Multiple models where one has a tiny fraction: only tiny one shows '<1%'."""
        mock_term.return_value = os.terminal_size((120, 40))
        agent = _make_agent_data(
            output_tokens=100001,
            model_usage={
                "claude-3-opus-20251101": {"outputTokens": 90000},
                "claude-3-sonnet-20251101": {"outputTokens": 10000},
                "claude-3-haiku-20251101": {"outputTokens": 1},
            },
        )
        stats = _make_stats(agents=[agent])
        result = render_full(stats, use_color=False)
        lines = result.split("\n")
        for line in lines:
            if "haiku" in line.lower():
                assert "<1%" in line
                break
        else:
            pytest.fail("haiku model row not found in output")

    @patch("os.get_terminal_size")
    def test_model_breakdown_total_matches_period_output_tokens(self, mock_term):
        """MODEL BREAKDOWN total should match the 'Output Tokens' in PERIOD USAGE."""
        mock_term.return_value = os.terminal_size((120, 40))
        # model_usage outputTokens sum to 50,000 which equals output_tokens
        agent = _make_agent_data(
            output_tokens=50000,
            model_usage={
                "claude-3-opus-20251101": {"outputTokens": 30000},
                "claude-3-sonnet-20251101": {"outputTokens": 20000},
            },
        )
        stats = _make_stats(agents=[agent])
        result = render_full(stats, use_color=False)
        # Both sections should contain the same formatted token count
        assert "50,000" in result
        # Count occurrences: period usage row + at least model breakdown rows
        # The period usage "Output Tokens" row shows 50,000
        # The model breakdown individual rows show 30,000 and 20,000
        lines = result.split("\n")
        period_output_tokens = None
        for line in lines:
            if "Output Tokens" in line:
                # Extract the formatted number from the period usage row
                match = re.search(r"[\d,]+", line.replace("Output Tokens", ""))
                if match:
                    period_output_tokens = match.group()
                break
        assert period_output_tokens == "50,000"
        # Verify model rows sum to the same total
        model_tok_sum = 0
        in_model_section = False
        for line in lines:
            if "MODEL BREAKDOWN" in line:
                in_model_section = True
                continue
            if in_model_section and "tok" in line:
                tok_match = re.search(r"([\d,]+)\s+tok", line)
                if tok_match:
                    model_tok_sum += int(tok_match.group(1).replace(",", ""))
            if in_model_section and "DAILY ACTIVITY" in line:
                break
        assert model_tok_sum == 50000

    @patch("os.get_terminal_size")
    def test_model_breakdown_consistent_with_varied_tokens(self, mock_term):
        """Token consistency with an asymmetric split across models."""
        mock_term.return_value = os.terminal_size((120, 40))
        agent = _make_agent_data(
            output_tokens=75000,
            model_usage={
                "claude-3-opus-20251101": {"outputTokens": 50000},
                "claude-3-sonnet-20251101": {"outputTokens": 25000},
            },
        )
        stats = _make_stats(agents=[agent])
        result = render_full(stats, use_color=False)
        # Period usage row should show 75,000
        assert "75,000" in result
        # Model rows should show 50,000 and 25,000
        assert "50,000" in result
        assert "25,000" in result

    @patch("os.get_terminal_size")
    def test_single_model_shows_100_pct(self, mock_term):
        """A single model should show '100%'."""
        mock_term.return_value = os.terminal_size((120, 40))
        agent = _make_agent_data(
            output_tokens=10000,
            model_usage={
                "claude-3-opus-20251101": {"outputTokens": 10000},
            },
        )
        stats = _make_stats(agents=[agent])
        result = render_full(stats, use_color=False)
        lines = result.split("\n")
        for line in lines:
            if "opus" in line.lower() and "tok" in line:
                assert "100%" in line
                break
        else:
            pytest.fail("opus model row not found in output")


# ── render_json ──────────────────────────────────────────────────


class TestRenderJson:
    def test_valid_json(self):
        stats = _make_stats()
        result = render_json(stats)
        parsed = json.loads(result)
        assert parsed["agents"][0]["id"] == "test"
        assert parsed["total_period_cost"] == 12.50
        assert parsed["today"] == "2025-01-16"

    def test_empty_agents(self):
        stats = _make_stats(agents=[])
        result = render_json(stats)
        parsed = json.loads(result)
        assert parsed["agents"] == []

    def test_indented(self):
        stats = _make_stats()
        result = render_json(stats)
        # json.dumps with indent=2 produces multi-line output
        assert "\n" in result


# ── render_compact ───────────────────────────────────────────────


class TestRenderCompact:
    def test_single_agent(self):
        stats = _make_stats()
        result = render_compact(stats)
        assert "Test Agent: $12.50" in result
        # No "Total" for single agent
        assert "Total" not in result

    def test_multi_agent(self):
        a1 = _make_agent_data(name="Claude", period_cost=10.0)
        a2 = _make_agent_data(name="Gemini", period_cost=5.0)
        stats = _make_stats(agents=[a1, a2], total_period_cost=15.0)
        result = render_compact(stats)
        assert "Claude: $10.00" in result
        assert "Gemini: $5.00" in result
        assert "Total: $15.00" in result
        assert " | " in result

    def test_empty_agents(self):
        stats = _make_stats(agents=[])
        result = render_compact(stats)
        assert result == "No agent data available."

    def test_pipe_separator(self):
        a1 = _make_agent_data(name="A", period_cost=1.0)
        a2 = _make_agent_data(name="B", period_cost=2.0)
        stats = _make_stats(agents=[a1, a2], total_period_cost=3.0)
        result = render_compact(stats)
        parts = result.split(" | ")
        assert len(parts) == 3  # A, B, Total


# ── render_accessible ────────────────────────────────────────────


class TestRenderAccessible:
    def test_no_ansi(self):
        stats = _make_stats()
        result = render_accessible(stats)
        assert "\033[" not in result

    def test_no_box_drawing(self):
        stats = _make_stats()
        result = render_accessible(stats)
        for ch in "\u2550\u2551\u2554\u2557\u255a\u255d\u2560\u2563":
            assert ch not in result

    def test_single_agent_title(self):
        stats = _make_stats()
        result = render_accessible(stats)
        assert "Test Agent Usage Report" in result

    def test_multi_agent_title(self):
        a1 = _make_agent_data(name="Claude")
        a2 = _make_agent_data(name="Gemini")
        stats = _make_stats(agents=[a1, a2])
        result = render_accessible(stats)
        assert "Burnctl Multi-Agent Usage Report" in result

    def test_empty_agents(self):
        stats = _make_stats(agents=[])
        result = render_accessible(stats)
        assert result == "No agent data available."

    def test_includes_agent_details(self):
        stats = _make_stats()
        result = render_accessible(stats)
        assert "Agent: Test Agent" in result
        assert "Plan: pro" in result
        assert "Messages: 100" in result
        assert "Sessions: 10" in result
        assert "Output tokens: 50,000" in result
        assert "Tool calls: 25" in result

    def test_includes_value_info(self):
        stats = _make_stats()
        result = render_accessible(stats)
        assert "All-time value: $150.00" in result
        assert "Value ratio: 1.1x" in result

    def test_includes_billing_period(self):
        stats = _make_stats()
        result = render_accessible(stats)
        assert "Billing period: 2025-01-01 to 2025-02-01" in result
        assert "Days remaining: 16 of 31" in result

    def test_includes_pace_with_plan_price(self):
        agent = _make_agent_data(plan_price=20.0, pace_pct=62.5)
        stats = _make_stats(agents=[agent])
        result = render_accessible(stats)
        assert "62 percent of plan value used" in result

    def test_no_pace_with_zero_plan_price(self):
        agent = _make_agent_data(plan_price=0, pace_pct=0.0)
        stats = _make_stats(agents=[agent])
        result = render_accessible(stats)
        assert "percent of plan value used" not in result

    def test_projected_cost_shown(self):
        agent = _make_agent_data(
            plan_price=20.0, projected_cost=25.81,
        )
        stats = _make_stats(agents=[agent])
        result = render_accessible(stats)
        assert "Projected period cost: $25.81" in result

    def test_projected_cost_hidden_when_zero(self):
        agent = _make_agent_data(plan_price=20.0, projected_cost=0)
        stats = _make_stats(agents=[agent])
        result = render_accessible(stats)
        assert "Projected period cost" not in result

    def test_system_total_multi_agent(self):
        a1 = _make_agent_data(name="Claude", period_cost=10.0)
        a2 = _make_agent_data(name="Gemini", period_cost=5.0)
        stats = _make_stats(agents=[a1, a2], total_period_cost=15.0)
        result = render_accessible(stats)
        assert "System total period cost: $15.00" in result

    def test_no_system_total_single_agent(self):
        stats = _make_stats()
        result = render_accessible(stats)
        assert "System total" not in result

    def test_report_date(self):
        stats = _make_stats(today="2025-03-01")
        result = render_accessible(stats)
        assert "Report date: 2025-03-01" in result

    def test_first_session_and_totals(self):
        stats = _make_stats()
        result = render_accessible(stats)
        assert "First session: 2024-06-15" in result
        assert "Total messages: 500" in result
        assert "Total sessions: 50" in result


# ── export_csv ───────────────────────────────────────────────────


class TestExportCsv:
    def test_creates_file(self, tmp_path):
        filepath = str(tmp_path / "test.csv")
        stats = _make_stats()
        export_csv(stats, filepath=filepath)
        assert os.path.isfile(filepath)

    def test_csv_contents(self, tmp_path):
        filepath = str(tmp_path / "test.csv")
        stats = _make_stats()
        export_csv(stats, filepath=filepath)

        with open(filepath, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 1
        assert rows[0]["agent"] == "test"
        assert rows[0]["messages"] == "100"
        assert rows[0]["sessions"] == "10"
        assert rows[0]["output_tokens"] == "50000"
        assert rows[0]["period_cost"] == "12.5"
        assert rows[0]["alltime_cost"] == "150.0"
        assert rows[0]["period_start"] == "2025-01-01"
        assert rows[0]["period_end"] == "2025-02-01"

    def test_appends_to_existing(self, tmp_path):
        filepath = str(tmp_path / "test.csv")
        stats = _make_stats()

        export_csv(stats, filepath=filepath)
        export_csv(stats, filepath=filepath)

        with open(filepath, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        # First call writes header + 1 row, second appends 1 row (no header)
        assert len(rows) == 2

    def test_multi_agent(self, tmp_path):
        filepath = str(tmp_path / "test.csv")
        a1 = _make_agent_data(id="claude", name="Claude")
        a2 = _make_agent_data(id="gemini", name="Gemini")
        stats = _make_stats(agents=[a1, a2])
        export_csv(stats, filepath=filepath)

        with open(filepath, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 2
        assert rows[0]["agent"] == "claude"
        assert rows[1]["agent"] == "gemini"

    def test_empty_agents_no_file(self, tmp_path, capsys):
        filepath = str(tmp_path / "test.csv")
        stats = _make_stats(agents=[])
        export_csv(stats, filepath=filepath)
        assert not os.path.isfile(filepath)
        captured = capsys.readouterr()
        assert "No agent data to export" in captured.err

    def test_csv_header(self, tmp_path):
        filepath = str(tmp_path / "test.csv")
        stats = _make_stats()
        export_csv(stats, filepath=filepath)

        with open(filepath, "r") as f:
            header = f.readline().strip()

        expected = "agent,period_start,period_end,messages,sessions,output_tokens,period_cost,alltime_cost"
        assert header == expected

    def test_write_error_exits(self, tmp_path):
        """Writing to a bad path should print an error and exit."""
        stats = _make_stats()
        bad_path = str(tmp_path / "nonexistent_dir" / "test.csv")

        with pytest.raises(SystemExit) as exc_info:
            export_csv(stats, filepath=bad_path)

        assert exc_info.value.code == 1

    def test_prints_success_message(self, tmp_path, capsys):
        filepath = str(tmp_path / "test.csv")
        stats = _make_stats()
        export_csv(stats, filepath=filepath)
        captured = capsys.readouterr()
        assert "Exported 1 agent(s)" in captured.out


# ── Inactive agents ─────────────────────────────────────────────


def _make_inactive_agent(**overrides):
    """Build an inactive agent data dict with zeroed stats."""
    base = {
        "id": "idle",
        "name": "Idle Agent",
        "plan_name": "pro",
        "plan_price": 20.0,
        "interval": "mo",
        "period_start": "2025-01-01",
        "period_end": "2025-02-01",
        "days_elapsed": 15,
        "days_remaining": 16,
        "total_days": 31,
        "pace_pct": 0.0,
        "projected_cost": 0.0,
        "messages": 0,
        "sessions": 0,
        "output_tokens": 0,
        "tool_calls": 0,
        "period_cost": 0.0,
        "alltime_cost": 0.0,
        "value_ratio": 0.0,
        "model_usage": {},
        "daily_messages": {},
        "spark_data": [],
        "first_session": "",
        "total_messages": 0,
        "total_sessions": 0,
        "inactive": True,
    }
    base.update(overrides)
    return base


class TestInactiveAgents:
    """Tests for the inactive agent feature."""

    def test_inactive_agent_has_all_expected_keys(self):
        """Inactive agent dict should contain all standard keys."""
        agent = _make_inactive_agent()
        expected_keys = [
            "id", "name", "plan_name", "plan_price", "interval",
            "period_start", "period_end", "days_elapsed", "days_remaining",
            "total_days", "pace_pct", "projected_cost", "messages",
            "sessions", "output_tokens", "tool_calls", "period_cost",
            "alltime_cost", "value_ratio", "model_usage", "daily_messages",
            "spark_data", "first_session", "total_messages", "total_sessions",
            "inactive",
        ]
        for key in expected_keys:
            assert key in agent, "missing key: %s" % key

    def test_inactive_agent_has_zeroed_stats(self):
        """Inactive agent should have zero values for all numeric stats."""
        agent = _make_inactive_agent()
        assert agent["messages"] == 0
        assert agent["sessions"] == 0
        assert agent["output_tokens"] == 0
        assert agent["tool_calls"] == 0
        assert agent["period_cost"] == 0.0
        assert agent["alltime_cost"] == 0.0
        assert agent["projected_cost"] == 0.0
        assert agent["pace_pct"] == 0.0
        assert agent["value_ratio"] == 0.0
        assert agent["total_messages"] == 0
        assert agent["total_sessions"] == 0

    def test_inactive_agent_has_inactive_flag(self):
        """Inactive agent should have inactive=True."""
        agent = _make_inactive_agent()
        assert agent["inactive"] is True

    def test_active_agent_has_no_inactive_flag(self):
        """Active agents should not have the inactive key."""
        agent = _make_agent_data()
        assert "inactive" not in agent

    def test_aggregate_stats_inactive_via_available_none(self):
        """When get_stats returns None but is_available returns True,
        the agent should appear with inactive=True and zeroed stats."""
        c = _make_collector(collector_id="idle", name="Idle Agent")
        c.get_stats.return_value = None
        c.is_available.return_value = True

        result = aggregate_stats([c], {}, ref_date=datetime(2025, 1, 16))

        assert len(result["agents"]) == 1
        agent = result["agents"][0]
        assert agent["inactive"] is True
        assert agent["messages"] == 0
        assert agent["sessions"] == 0
        assert agent["period_cost"] == 0.0

    def test_inactive_agent_does_not_contribute_to_total_period_cost(self):
        """Inactive agents have period_cost=0 and should not
        inflate total_period_cost."""
        active = _make_collector(
            collector_id="claude", name="Claude",
            stats={"period_cost": 10.0},
        )
        inactive = _make_collector(
            collector_id="idle", name="Idle",
        )
        inactive.get_stats.return_value = None
        inactive.is_available.return_value = True

        result = aggregate_stats(
            [active, inactive], {}, ref_date=datetime(2025, 1, 16),
        )

        assert result["total_period_cost"] == 10.0

    def test_mix_active_and_inactive_agents_in_stats(self):
        """Both active and inactive agents should appear in the agents list."""
        active = _make_collector(
            collector_id="claude", name="Claude",
            stats={"period_cost": 15.0},
        )
        inactive = _make_collector(
            collector_id="idle", name="Idle",
        )
        inactive.get_stats.return_value = None
        inactive.is_available.return_value = True

        result = aggregate_stats(
            [active, inactive], {}, ref_date=datetime(2025, 1, 16),
        )

        assert len(result["agents"]) == 2
        ids = [a["id"] for a in result["agents"]]
        assert "claude" in ids
        assert "idle" in ids
        inactive_agent = [a for a in result["agents"] if a["id"] == "idle"][0]
        assert inactive_agent["inactive"] is True
        active_agent = [a for a in result["agents"] if a["id"] == "claude"][0]
        assert "inactive" not in active_agent

    @patch("os.get_terminal_size")
    def test_render_full_inactive_shows_inactive_in_header(self, mock_term):
        """render_full should show '(inactive)' in the column header
        for inactive agents."""
        mock_term.return_value = os.terminal_size((120, 40))
        active = _make_agent_data(id="claude", name="Claude")
        inactive = _make_inactive_agent(id="idle", name="Idle")
        stats = _make_stats(agents=[active, inactive])

        result = render_full(stats, use_color=False)

        assert "(inactive)" in result

    @patch("os.get_terminal_size")
    def test_render_full_active_no_inactive_label(self, mock_term):
        """render_full should not show '(inactive)' for active agents."""
        mock_term.return_value = os.terminal_size((120, 40))
        stats = _make_stats()

        result = render_full(stats, use_color=False)

        assert "(inactive)" not in result

    @patch("os.get_terminal_size")
    def test_render_full_only_inactive_agents(self, mock_term):
        """render_full should handle a report with only inactive agents."""
        mock_term.return_value = os.terminal_size((120, 40))
        inactive = _make_inactive_agent()
        stats = _make_stats(agents=[inactive])

        result = render_full(stats, use_color=False)

        assert "(inactive)" in result
        assert "USAGE REPORT" in result

    @patch("os.get_terminal_size")
    def test_render_full_mixed_does_not_crash(self, mock_term):
        """render_full should complete without error for mixed agents."""
        mock_term.return_value = os.terminal_size((120, 40))
        active = _make_agent_data(id="claude", name="Claude")
        inactive = _make_inactive_agent(id="idle", name="Idle")
        stats = _make_stats(agents=[active, inactive])

        # Should not raise
        result = render_full(stats, use_color=False)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_render_compact_inactive(self):
        """render_compact should include inactive agents with $0.00."""
        active = _make_agent_data(name="Claude", period_cost=10.0)
        inactive = _make_inactive_agent(name="Idle")
        stats = _make_stats(
            agents=[active, inactive], total_period_cost=10.0,
        )

        result = render_compact(stats)

        assert "Claude: $10.00" in result
        assert "Idle: $0.00" in result
        assert "Total: $10.00" in result

    def test_render_accessible_inactive(self):
        """render_accessible should include inactive agents without crashing."""
        active = _make_agent_data(name="Claude")
        inactive = _make_inactive_agent(name="Idle")
        stats = _make_stats(agents=[active, inactive])

        result = render_accessible(stats)

        assert "Agent: Claude" in result
        assert "Agent: Idle" in result
        assert "\033[" not in result

    def test_render_json_inactive(self):
        """render_json should include the inactive flag in output."""
        inactive = _make_inactive_agent()
        stats = _make_stats(agents=[inactive])

        result = render_json(stats)
        parsed = json.loads(result)

        assert len(parsed["agents"]) == 1
        assert parsed["agents"][0]["inactive"] is True
        assert parsed["agents"][0]["messages"] == 0

    def test_render_json_mixed_active_inactive(self):
        """render_json should correctly represent both active and inactive."""
        active = _make_agent_data(id="claude", name="Claude")
        inactive = _make_inactive_agent(id="idle", name="Idle")
        stats = _make_stats(agents=[active, inactive])

        result = render_json(stats)
        parsed = json.loads(result)

        assert len(parsed["agents"]) == 2
        claude = [a for a in parsed["agents"] if a["id"] == "claude"][0]
        idle = [a for a in parsed["agents"] if a["id"] == "idle"][0]
        assert "inactive" not in claude
        assert idle["inactive"] is True


# ── render_diff ─────────────────────────────────────────────────


class TestRenderDiff:
    """Tests for the render_diff period-over-period comparison."""

    def test_basic_diff_one_agent_both_periods(self):
        """Single agent present in both current and previous periods."""
        prev = _make_stats(
            agents=[_make_agent_data(
                id="claude", name="Claude",
                messages=80, sessions=8, period_cost=10.0,
                period_start="2024-12-01", period_end="2025-01-01",
            )],
            total_period_cost=10.0,
            today="2025-01-16",
        )
        cur = _make_stats(
            agents=[_make_agent_data(
                id="claude", name="Claude",
                messages=100, sessions=10, period_cost=12.50,
                period_start="2025-01-01", period_end="2025-02-01",
            )],
            total_period_cost=12.50,
            today="2025-01-16",
        )

        result = render_diff(cur, prev)

        assert "Claude" in result
        assert "100" in result
        assert "80" in result

    def test_agent_only_in_current(self):
        """Agent present only in current period should show zeroed previous."""
        prev = _make_stats(agents=[], total_period_cost=0.0)
        cur = _make_stats(
            agents=[_make_agent_data(
                id="claude", name="Claude",
                messages=50, period_cost=5.0,
            )],
            total_period_cost=5.0,
        )

        result = render_diff(cur, prev)

        assert "Claude" in result
        assert "50" in result
        # Previous values shown as "?" for missing period dates
        assert "?" in result

    def test_agent_only_in_previous(self):
        """Agent present only in previous period should show zeroed current."""
        prev = _make_stats(
            agents=[_make_agent_data(
                id="claude", name="Claude",
                messages=80, period_cost=10.0,
                period_start="2024-12-01", period_end="2025-01-01",
            )],
            total_period_cost=10.0,
        )
        cur = _make_stats(agents=[], total_period_cost=0.0)

        result = render_diff(cur, prev)

        assert "Claude" in result
        assert "80" in result

    def test_multiple_agents(self):
        """Diff with multiple agents in both periods."""
        prev_agents = [
            _make_agent_data(
                id="claude", name="Claude",
                messages=80, period_cost=10.0,
                period_start="2024-12-01", period_end="2025-01-01",
            ),
            _make_agent_data(
                id="gemini", name="Gemini",
                messages=60, period_cost=8.0,
                period_start="2024-12-01", period_end="2025-01-01",
            ),
        ]
        cur_agents = [
            _make_agent_data(
                id="claude", name="Claude",
                messages=100, period_cost=12.50,
            ),
            _make_agent_data(
                id="gemini", name="Gemini",
                messages=90, period_cost=11.0,
            ),
        ]
        prev = _make_stats(agents=prev_agents, total_period_cost=18.0)
        cur = _make_stats(agents=cur_agents, total_period_cost=23.50)

        result = render_diff(cur, prev)

        assert "Claude" in result
        assert "Gemini" in result
        assert "System Total" in result

    def test_empty_agents_both_periods(self):
        """Both periods with no agents should return the no-data message."""
        prev = _make_stats(agents=[], total_period_cost=0.0)
        cur = _make_stats(agents=[], total_period_cost=0.0)

        result = render_diff(cur, prev)

        assert result == "No agent data available."

    def test_column_headers_present(self):
        """Output should contain the expected column headers."""
        prev = _make_stats(
            agents=[_make_agent_data(id="claude", name="Claude")],
        )
        cur = _make_stats(
            agents=[_make_agent_data(id="claude", name="Claude")],
        )

        result = render_diff(cur, prev)

        assert "Last" in result
        assert "Current" in result
        assert "Delta" in result

    def test_delta_positive_sign(self):
        """Delta should show '+' for increases."""
        prev = _make_stats(
            agents=[_make_agent_data(
                id="claude", name="Claude",
                messages=50, period_cost=5.0,
                period_start="2024-12-01", period_end="2025-01-01",
            )],
            total_period_cost=5.0,
        )
        cur = _make_stats(
            agents=[_make_agent_data(
                id="claude", name="Claude",
                messages=100, period_cost=12.50,
            )],
            total_period_cost=12.50,
        )

        result = render_diff(cur, prev)

        assert "+50" in result
        assert "+$7.50" in result

    def test_delta_negative_sign(self):
        """Delta should show '-' for decreases."""
        prev = _make_stats(
            agents=[_make_agent_data(
                id="claude", name="Claude",
                messages=100, period_cost=12.50,
                period_start="2024-12-01", period_end="2025-01-01",
            )],
            total_period_cost=12.50,
        )
        cur = _make_stats(
            agents=[_make_agent_data(
                id="claude", name="Claude",
                messages=50, period_cost=5.0,
            )],
            total_period_cost=5.0,
        )

        result = render_diff(cur, prev)

        assert "-50" in result
        # _diff_str formats negative USD as "$-7.50" (sign before digits)
        assert "$-7.50" in result

    def test_delta_zero_shows_plus(self):
        """Delta of zero should show '+0'."""
        agent = _make_agent_data(id="claude", name="Claude")
        prev = _make_stats(agents=[agent])
        cur = _make_stats(agents=[agent])

        result = render_diff(cur, prev)

        assert "+0" in result

    def test_system_total_line_present(self):
        """Output should contain a System Total line."""
        prev = _make_stats(
            agents=[_make_agent_data(id="claude", name="Claude")],
            total_period_cost=12.50,
        )
        cur = _make_stats(
            agents=[_make_agent_data(id="claude", name="Claude")],
            total_period_cost=12.50,
        )

        result = render_diff(cur, prev)

        assert "System Total" in result

    def test_system_total_shows_arrow(self):
        """System total should show prev -> cur format."""
        prev = _make_stats(
            agents=[_make_agent_data(
                id="claude", name="Claude", period_cost=10.0,
            )],
            total_period_cost=10.0,
        )
        cur = _make_stats(
            agents=[_make_agent_data(
                id="claude", name="Claude", period_cost=15.0,
            )],
            total_period_cost=15.0,
        )

        result = render_diff(cur, prev)

        assert "$10.00 ->" in result
        assert "$15.00" in result

    def test_generated_date_in_footer(self):
        """Output should include the generated date from current stats."""
        prev = _make_stats(
            agents=[_make_agent_data(id="claude", name="Claude")],
            today="2025-01-01",
        )
        cur = _make_stats(
            agents=[_make_agent_data(id="claude", name="Claude")],
            today="2025-02-01",
        )

        result = render_diff(cur, prev)

        assert "Generated: 2025-02-01" in result

    def test_period_header_title(self):
        """Output should include the diff report title."""
        prev = _make_stats(
            agents=[_make_agent_data(id="claude", name="Claude")],
        )
        cur = _make_stats(
            agents=[_make_agent_data(id="claude", name="Claude")],
        )

        result = render_diff(cur, prev)

        assert "BURNCTL PERIOD-OVER-PERIOD DIFF" in result

    def test_metrics_rows_present(self):
        """All expected metric labels should appear in the output."""
        prev = _make_stats(
            agents=[_make_agent_data(id="claude", name="Claude")],
        )
        cur = _make_stats(
            agents=[_make_agent_data(id="claude", name="Claude")],
        )

        result = render_diff(cur, prev)

        assert "Messages" in result
        assert "Sessions" in result
        assert "Output Tokens" in result
        assert "Tool Calls" in result
        assert "Est. API Cost" in result

    def test_agent_order_preserves_current_first(self):
        """Agents from current should appear before agents only in previous."""
        prev = _make_stats(
            agents=[
                _make_agent_data(id="old_agent", name="Old"),
            ],
        )
        cur = _make_stats(
            agents=[
                _make_agent_data(id="new_agent", name="New"),
            ],
        )

        result = render_diff(cur, prev)

        new_pos = result.index("New")
        old_pos = result.index("Old")
        assert new_pos < old_pos

    def test_diff_with_inactive_agent(self):
        """render_diff should handle inactive agents in current period."""
        inactive = _make_inactive_agent(id="idle", name="Idle")
        active_prev = _make_agent_data(
            id="idle", name="Idle",
            messages=50, period_cost=5.0,
            period_start="2024-12-01", period_end="2025-01-01",
        )
        prev = _make_stats(agents=[active_prev], total_period_cost=5.0)
        cur = _make_stats(agents=[inactive], total_period_cost=0.0)

        result = render_diff(cur, prev)

        assert "Idle" in result
        assert "-50" in result
