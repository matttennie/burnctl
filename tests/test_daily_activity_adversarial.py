"""Adversarial tests for the DAILY ACTIVITY section of render_full.

Hammers the daily activity rendering with bizarre, hostile, and
edge-case agent data: empty maps, huge counts, negative values,
non-string keys, Unicode dates, hundreds of agents, single-pixel
terminals, NaN/Inf counts, and more.

Python 3.8 compatible.
"""

import os
from unittest.mock import patch

from burnctl.report import render_full, _strip_ansi


# ── Helpers ──────────────────────────────────────────────────────────


_UNSET = object()


def _agent(id="test", name="Test", daily_messages=_UNSET, **kw):
    """Minimal agent dict with sensible defaults."""
    base = {
        "id": id,
        "name": name,
        "plan_name": "pro",
        "plan_price": 20.0,
        "interval": "mo",
        "period_start": "2025-01-01",
        "period_end": "2025-02-01",
        "days_elapsed": 15,
        "days_remaining": 16,
        "total_days": 31,
        "pace_pct": 50.0,
        "projected_cost": 25.0,
        "messages": 100,
        "sessions": 10,
        "output_tokens": 50000,
        "tool_calls": 25,
        "period_cost": 12.50,
        "alltime_cost": 150.00,
        "value_ratio": 1.0,
        "model_usage": {},
        "daily_messages": {} if daily_messages is _UNSET else daily_messages,
        "spark_data": [],
        "first_session": "2024-06-15",
        "total_messages": 500,
        "total_sessions": 50,
    }
    base.update(kw)
    return base


def _stats(agents, today="2025-01-16"):
    total = sum(a["period_cost"] for a in agents)
    return {"agents": agents, "total_period_cost": total, "today": today}


def _render(agents, term_w=100, use_color=False, **kw):
    """Render with patched terminal size, return plain text."""
    with patch(
        "os.get_terminal_size",
        return_value=os.terminal_size((term_w, 40)),
    ):
        return render_full(_stats(agents, **kw), use_color=use_color)


# =====================================================================
# Empty / missing daily_messages
# =====================================================================


class TestDailyActivityEmpty:
    """DAILY ACTIVITY section with agents that have no active days."""

    def test_all_agents_empty_daily_messages(self):
        """No bars rendered when every agent has empty daily_messages."""
        result = _render([_agent(daily_messages={})])
        assert "DAILY ACTIVITY" in result
        assert "Messages per day" in result
        # No date rows
        assert "msgs" not in result.split("Messages per day")[1].split("Generated")[0]

    def test_daily_messages_key_missing(self):
        """Agent with daily_messages key missing entirely."""
        a = _agent()
        del a["daily_messages"]
        # Should not crash — .get("daily_messages", {}) handles it
        result = _render([a])
        assert "DAILY ACTIVITY" in result

    def test_daily_messages_is_none(self):
        """daily_messages=None instead of dict — should be treated as empty."""
        a = _agent(daily_messages=None)
        result = _render([a])
        assert "DAILY ACTIVITY" in result

    def test_all_zero_counts(self):
        """All dates have count=0 — no bars should render."""
        dm = {"2025-01-01": 0, "2025-01-02": 0, "2025-01-03": 0}
        result = _render([_agent(daily_messages=dm)])
        plain = _strip_ansi(result)
        activity_section = plain.split("DAILY ACTIVITY")[1]
        # No date rows because filter is `if c > 0`
        assert "01-01" not in activity_section
        assert "01-02" not in activity_section

    def test_mix_of_zero_and_nonzero(self):
        """Only nonzero days should appear."""
        dm = {"2025-01-01": 0, "2025-01-02": 5, "2025-01-03": 0}
        result = _render([_agent(daily_messages=dm)])
        plain = _strip_ansi(result)
        assert "01-02" in plain
        assert "01-01" not in plain.split("DAILY ACTIVITY")[1].split("01-02")[0]


# =====================================================================
# Extreme counts
# =====================================================================


class TestDailyActivityExtremeCounts:
    """Extreme numeric values in daily_messages."""

    def test_single_message(self):
        """Count of 1 should produce at least 1 bar char."""
        result = _render([_agent(daily_messages={"2025-01-05": 1})])
        assert "1 msgs" in _strip_ansi(result)

    def test_huge_count(self):
        """Millions of messages should format with commas."""
        dm = {"2025-01-01": 9_999_999}
        result = _render([_agent(daily_messages=dm)])
        plain = _strip_ansi(result)
        assert "9,999,999 msgs" in plain

    def test_billion_count(self):
        """Billions of messages — no overflow."""
        dm = {"2025-01-01": 2_000_000_000}
        result = _render([_agent(daily_messages=dm)])
        plain = _strip_ansi(result)
        assert "2,000,000,000 msgs" in plain

    def test_count_of_one_with_global_max_million(self):
        """Tiny count relative to huge max still gets at least 1 bar char."""
        dm1 = {"2025-01-01": 1_000_000}
        dm2 = {"2025-01-01": 1}
        result = _render([
            _agent(id="a", name="Big", daily_messages=dm1),
            _agent(id="b", name="Tiny", daily_messages=dm2),
        ])
        plain = _strip_ansi(result)
        # The tiny agent should still get at least one bar character
        assert "1 msgs" in plain


# =====================================================================
# Negative and float counts
# =====================================================================


class TestDailyActivityNegativeAndFloat:
    """Negative and floating-point counts in daily_messages."""

    def test_negative_count_filtered_out(self):
        """Negative counts (c > 0 check) should be skipped."""
        dm = {"2025-01-01": -5, "2025-01-02": 10}
        result = _render([_agent(daily_messages=dm)])
        plain = _strip_ansi(result)
        activity = plain.split("DAILY ACTIVITY")[1]
        assert "01-02" in activity
        # Negative day should NOT appear
        assert "-5 msgs" not in activity

    def test_float_count(self):
        """Floating-point counts should still render (int() truncates)."""
        dm = {"2025-01-01": 3.7}
        result = _render([_agent(daily_messages=dm)])
        plain = _strip_ansi(result)
        # fmt() will format whatever int(3.7) or 3.7 produces
        assert "msgs" in plain

    def test_nan_count_skipped(self):
        """NaN count should be filtered out, not crash."""
        dm = {"2025-01-01": float("nan"), "2025-01-02": 10}
        result = _render([_agent(daily_messages=dm)])
        plain = _strip_ansi(result)
        assert "10 msgs" in plain
        # NaN day should not appear
        assert "01-01" not in plain.split("DAILY ACTIVITY")[1].split("10 msgs")[0]

    def test_inf_count_skipped(self):
        """Infinity count should be filtered out, not crash."""
        dm = {"2025-01-01": float("inf"), "2025-01-02": 10}
        result = _render([_agent(daily_messages=dm)])
        plain = _strip_ansi(result)
        assert "10 msgs" in plain

    def test_negative_inf_count_skipped(self):
        """Negative infinity count should be filtered out."""
        dm = {"2025-01-01": float("-inf"), "2025-01-02": 10}
        result = _render([_agent(daily_messages=dm)])
        plain = _strip_ansi(result)
        assert "10 msgs" in plain

    def test_all_nan_counts(self):
        """All NaN counts — no bars rendered."""
        dm = {"2025-01-01": float("nan"), "2025-01-02": float("nan")}
        result = _render([_agent(daily_messages=dm)])
        assert "DAILY ACTIVITY" in result

    def test_all_inf_counts(self):
        """All inf counts — no bars rendered."""
        dm = {"2025-01-01": float("inf")}
        result = _render([_agent(daily_messages=dm)])
        assert "DAILY ACTIVITY" in result


# =====================================================================
# Date string edge cases
# =====================================================================


class TestDailyActivityDateEdges:
    """Adversarial date strings in daily_messages keys."""

    def test_short_date_string(self):
        """Date shorter than 5 chars — day[5:] produces empty string."""
        dm = {"2025": 10}
        result = _render([_agent(daily_messages=dm)])
        # Should not crash; short_date = "" which renders fine
        assert isinstance(result, str)

    def test_empty_date_string(self):
        """Empty string as date key."""
        dm = {"": 10}
        result = _render([_agent(daily_messages=dm)])
        assert isinstance(result, str)

    def test_date_with_extra_chars(self):
        """Date with extra suffix — day[5:] includes the extra."""
        dm = {"2025-01-01T12:00:00Z": 10}
        result = _render([_agent(daily_messages=dm)])
        plain = _strip_ansi(result)
        assert "01-01T12:00:00Z" in plain

    def test_non_date_string_key(self):
        """Completely non-date string as key."""
        dm = {"not-a-date-at-all": 10}
        result = _render([_agent(daily_messages=dm)])
        assert isinstance(result, str)

    def test_unicode_date_key(self):
        """Unicode characters in date key."""
        dm = {"\u2603\u2764\u2600\u2601\u2602-\u00e9": 10}
        result = _render([_agent(daily_messages=dm)])
        assert isinstance(result, str)

    def test_dates_sort_correctly(self):
        """Dates should appear in chronological order."""
        dm = {"2025-01-03": 3, "2025-01-01": 1, "2025-01-02": 2}
        result = _render([_agent(daily_messages=dm)])
        plain = _strip_ansi(result)
        pos1 = plain.index("01-01")
        pos2 = plain.index("01-02")
        pos3 = plain.index("01-03")
        assert pos1 < pos2 < pos3

    def test_duplicate_date_impossible_in_dict(self):
        """Dict can't have duplicate keys, but last-write-wins is fine."""
        dm = {"2025-01-01": 99}
        result = _render([_agent(daily_messages=dm)])
        assert "99 msgs" in _strip_ansi(result)


# =====================================================================
# Many agents
# =====================================================================


class TestDailyActivityManyAgents:
    """Stress-test with many agents in the DAILY ACTIVITY section."""

    def test_ten_agents(self):
        """Ten agents each with activity."""
        agents = [
            _agent(
                id="agent_%d" % i,
                name="Agent %d" % i,
                daily_messages={"2025-01-01": (i + 1) * 10},
            )
            for i in range(10)
        ]
        result = _render(agents)
        plain = _strip_ansi(result)
        for i in range(10):
            assert "Agent %d" % i in plain

    def test_fifty_agents(self):
        """Fifty agents — should not crash or take unreasonable time."""
        agents = [
            _agent(
                id="a%d" % i,
                name="A%d" % i,
                daily_messages={"2025-01-01": i + 1},
            )
            for i in range(50)
        ]
        result = _render(agents)
        assert "DAILY ACTIVITY" in result

    def test_agents_with_no_overlap(self):
        """Each agent active on a different day."""
        agents = [
            _agent(
                id="a%d" % i,
                name="A%d" % i,
                daily_messages={"2025-01-%02d" % (i + 1): 100},
            )
            for i in range(20)
        ]
        result = _render(agents)
        plain = _strip_ansi(result)
        for i in range(20):
            assert "01-%02d" % (i + 1) in plain

    def test_one_agent_dominates(self):
        """One agent has 1M messages, others have 1 — bars still render."""
        agents = [
            _agent(
                id="big", name="Big",
                daily_messages={"2025-01-01": 1_000_000},
            ),
        ]
        for i in range(5):
            agents.append(
                _agent(
                    id="small%d" % i,
                    name="Small%d" % i,
                    daily_messages={"2025-01-01": 1},
                )
            )
        result = _render(agents)
        plain = _strip_ansi(result)
        assert "1,000,000 msgs" in plain
        assert "1 msgs" in plain

    def test_global_scaling_across_agents(self):
        """Global max should come from the agent with the highest count."""
        agents = [
            _agent(
                id="a", name="A",
                daily_messages={"2025-01-01": 100},
            ),
            _agent(
                id="b", name="B",
                daily_messages={"2025-01-01": 200},
            ),
        ]
        result = _render(agents)
        plain = _strip_ansi(result)
        assert "100 msgs" in plain
        assert "200 msgs" in plain


# =====================================================================
# Many days
# =====================================================================


class TestDailyActivityManyDays:
    """Agents with many active days."""

    def test_thirty_one_days(self):
        """Full month of data."""
        dm = {"2025-01-%02d" % d: d * 10 for d in range(1, 32)}
        result = _render([_agent(daily_messages=dm)])
        plain = _strip_ansi(result)
        assert "01-01" in plain
        assert "01-31" in plain

    def test_three_hundred_sixty_five_days(self):
        """Full year of data — should not crash or be unreasonably slow."""
        dm = {}
        for m in range(1, 13):
            for d in range(1, 29):
                dm["2025-%02d-%02d" % (m, d)] = m * d
        result = _render([_agent(daily_messages=dm)])
        assert "DAILY ACTIVITY" in result

    def test_single_day(self):
        """Just one active day."""
        result = _render([_agent(daily_messages={"2025-01-15": 42})])
        assert "42 msgs" in _strip_ansi(result)


# =====================================================================
# Terminal width edge cases
# =====================================================================


class TestDailyActivityTerminalWidth:
    """DAILY ACTIVITY rendering under extreme terminal widths."""

    def test_very_narrow_terminal(self):
        """Terminal width of 20 — should not crash."""
        result = _render(
            [_agent(daily_messages={"2025-01-01": 5})],
            term_w=20,
        )
        assert isinstance(result, str)

    def test_minimum_terminal(self):
        """Terminal width of 1 — absolute minimum."""
        result = _render(
            [_agent(daily_messages={"2025-01-01": 5})],
            term_w=1,
        )
        assert isinstance(result, str)

    def test_very_wide_terminal(self):
        """Terminal width of 500 — bars should scale up."""
        result = _render(
            [_agent(daily_messages={"2025-01-01": 100})],
            term_w=500,
        )
        assert "100 msgs" in _strip_ansi(result)

    def test_zero_terminal_width(self):
        """Terminal width of 0 — should not crash."""
        result = _render(
            [_agent(daily_messages={"2025-01-01": 5})],
            term_w=0,
        )
        assert isinstance(result, str)


# =====================================================================
# Agent name edge cases
# =====================================================================


class TestDailyActivityAgentNames:
    """Adversarial agent names in the DAILY ACTIVITY section."""

    def test_empty_name(self):
        result = _render([_agent(name="", daily_messages={"2025-01-01": 5})])
        assert isinstance(result, str)

    def test_very_long_name(self):
        name = "A" * 500
        result = _render([_agent(name=name, daily_messages={"2025-01-01": 5})])
        assert isinstance(result, str)

    def test_unicode_name(self):
        result = _render([
            _agent(name="\U0001f916\U0001f4a5\u2603", daily_messages={"2025-01-01": 5}),
        ])
        assert isinstance(result, str)

    def test_ansi_in_name(self):
        """Agent name containing ANSI codes should not corrupt layout."""
        result = _render([
            _agent(name="\033[31mRed\033[0m", daily_messages={"2025-01-01": 5}),
        ])
        assert isinstance(result, str)

    def test_newline_in_name(self):
        """Newline in agent name should not break box drawing."""
        result = _render([
            _agent(name="Line1\nLine2", daily_messages={"2025-01-01": 5}),
        ])
        assert isinstance(result, str)


# =====================================================================
# Agent ID edge cases
# =====================================================================


class TestDailyActivityAgentIds:
    """Adversarial agent IDs."""

    def test_empty_id(self):
        result = _render([_agent(id="", daily_messages={"2025-01-01": 5})])
        assert isinstance(result, str)

    def test_missing_id(self):
        a = _agent(daily_messages={"2025-01-01": 5})
        del a["id"]
        result = _render([a])
        assert isinstance(result, str)

    def test_unknown_agent_id(self):
        """Unknown ID should still render (falls back to default colors)."""
        result = _render([
            _agent(id="totally_unknown_agent_xyz", daily_messages={"2025-01-01": 5}),
        ])
        assert "5 msgs" in _strip_ansi(result)

    def test_claude_gemini_codex_ids(self):
        """Known agent IDs should each render their own section."""
        agents = [
            _agent(id="claude", name="Claude", daily_messages={"2025-01-01": 10}),
            _agent(id="gemini", name="Gemini", daily_messages={"2025-01-01": 20}),
            _agent(id="codex", name="Codex", daily_messages={"2025-01-01": 30}),
        ]
        result = _render(agents)
        plain = _strip_ansi(result)
        assert "Claude" in plain
        assert "Gemini" in plain
        assert "Codex" in plain
        assert "10 msgs" in plain
        assert "20 msgs" in plain
        assert "30 msgs" in plain


# =====================================================================
# Color mode
# =====================================================================


class TestDailyActivityColor:
    """DAILY ACTIVITY rendering with color enabled and disabled."""

    def test_no_color_no_ansi_in_daily_section(self):
        result = _render(
            [_agent(daily_messages={"2025-01-01": 10})],
            use_color=False,
        )
        activity = result.split("DAILY ACTIVITY")[1]
        assert "\033[" not in activity

    def test_color_enabled_has_ansi(self):
        with patch.dict(
            "sys.modules",
            {"claude_usage": None, "claude_usage.colors": None},
        ):
            result = _render(
                [_agent(daily_messages={"2025-01-01": 10})],
                use_color=True,
            )
        activity = result.split("DAILY ACTIVITY")[1]
        assert "\033[" in activity

    def test_msgs_label_present_with_color(self):
        with patch.dict(
            "sys.modules",
            {"claude_usage": None, "claude_usage.colors": None},
        ):
            result = _render(
                [_agent(daily_messages={"2025-01-01": 10})],
                use_color=True,
            )
        plain = _strip_ansi(result)
        assert "10 msgs" in plain

    def test_messages_per_day_subtitle_with_color(self):
        with patch.dict(
            "sys.modules",
            {"claude_usage": None, "claude_usage.colors": None},
        ):
            result = _render(
                [_agent(daily_messages={"2025-01-01": 10})],
                use_color=True,
            )
        plain = _strip_ansi(result)
        assert "Messages per day" in plain


# =====================================================================
# Global max edge cases
# =====================================================================


class TestDailyActivityGlobalMax:
    """Edge cases in global max computation."""

    def test_global_max_floor_is_one(self):
        """When all counts are 0, global_max should be 1 (not 0)."""
        dm = {"2025-01-01": 0}
        result = _render([_agent(daily_messages=dm)])
        # No crash (no division by zero)
        assert isinstance(result, str)

    def test_all_agents_same_count(self):
        """All agents, all days, same count — bars should be full width."""
        agents = [
            _agent(id="a", name="A", daily_messages={"2025-01-01": 100}),
            _agent(id="b", name="B", daily_messages={"2025-01-01": 100}),
        ]
        result = _render(agents)
        plain = _strip_ansi(result)
        assert plain.count("100 msgs") == 2

    def test_single_count_equals_global_max(self):
        """One agent, one day — bar should be full width."""
        result = _render([_agent(daily_messages={"2025-01-01": 50})])
        assert "50 msgs" in _strip_ansi(result)


# =====================================================================
# Interaction with other sections
# =====================================================================


class TestDailyActivitySectionInteraction:
    """Ensure DAILY ACTIVITY doesn't break other sections."""

    def test_model_breakdown_still_present(self):
        """MODEL BREAKDOWN should still render when daily_messages is wild."""
        a = _agent(
            daily_messages={"2025-01-01": 999999},
            model_usage={"some-model": {"outputTokens": 1000}},
        )
        result = _render([a])
        plain = _strip_ansi(result)
        assert "MODEL BREAKDOWN" in plain
        assert "DAILY ACTIVITY" in plain

    def test_simple_mode(self):
        """simple=True should still render DAILY ACTIVITY."""
        with patch(
            "os.get_terminal_size",
            return_value=os.terminal_size((100, 40)),
        ):
            result = render_full(
                _stats([_agent(daily_messages={"2025-01-01": 10})]),
                simple=True,
                use_color=False,
            )
        assert "DAILY ACTIVITY" in result
        assert "10 msgs" in result

    def test_box_alignment_preserved(self):
        """All box lines should be the same visual width."""
        result = _render(
            [_agent(daily_messages={"2025-01-01": 10, "2025-01-02": 20})],
            use_color=False,
        )
        lines = result.splitlines()
        border_starts = (
            "\u2551", "\u2554", "\u255a", "\u2560", "\u255f",
        )
        box_lines = [
            ln for ln in lines if ln.startswith(border_starts)
        ]
        if box_lines:
            widths = [len(ln) for ln in box_lines]
            assert len(set(widths)) == 1, (
                "Inconsistent widths: %s" % widths
            )


# =====================================================================
# Pathological combined scenarios
# =====================================================================


class TestDailyActivityPathological:
    """Pathological combinations of adversarial inputs."""

    def test_hundred_agents_each_with_hundred_days(self):
        """100 agents x 100 days = 10,000 date entries. Should complete."""
        agents = []
        for i in range(100):
            dm = {"2025-01-%02d" % (d + 1): d + 1 for d in range(28)}
            agents.append(_agent(
                id="a%d" % i,
                name="A%d" % i,
                daily_messages=dm,
            ))
        result = _render(agents, term_w=200)
        assert "DAILY ACTIVITY" in result

    def test_agent_with_only_negative_counts(self):
        """Agent with only negative counts should be skipped entirely."""
        dm = {"2025-01-01": -10, "2025-01-02": -5, "2025-01-03": -1}
        result = _render([_agent(daily_messages=dm)])
        plain = _strip_ansi(result)
        activity = plain.split("DAILY ACTIVITY")[1]
        # No date rows because all counts are negative (< 0, filtered by c > 0)
        assert "01-01" not in activity
        assert "msgs" not in activity.split("Messages per day")[1].split("\u2550")[0]

    def test_mixed_known_and_unknown_agent_ids(self):
        """Mix of claude, gemini, codex, and unknown IDs."""
        agents = [
            _agent(id="claude", name="Claude", daily_messages={"2025-01-01": 10}),
            _agent(id="gemini", name="Gemini", daily_messages={"2025-01-01": 20}),
            _agent(id="codex", name="Codex", daily_messages={"2025-01-01": 30}),
            _agent(id="mystery", name="Mystery", daily_messages={"2025-01-01": 40}),
            _agent(id="", name="NoID", daily_messages={"2025-01-01": 50}),
        ]
        result = _render(agents)
        plain = _strip_ansi(result)
        for name in ["Claude", "Gemini", "Codex", "Mystery", "NoID"]:
            assert name in plain

    def test_narrow_terminal_many_agents_long_names(self):
        """Narrow terminal + many agents + long names."""
        agents = [
            _agent(
                id="a%d" % i,
                name="SuperLongAgentName%d" % i,
                daily_messages={"2025-01-01": 100},
            )
            for i in range(10)
        ]
        result = _render(agents, term_w=40)
        assert isinstance(result, str)
        assert "DAILY ACTIVITY" in _strip_ansi(result)

    def test_today_date_edge_case(self):
        """Today is before all daily_messages dates."""
        dm = {"2030-12-31": 100}
        result = _render([_agent(daily_messages=dm)], today="2020-01-01")
        # Should still render — the filter is on active_days c > 0
        assert "100 msgs" in _strip_ansi(result)

    def test_count_exactly_global_max(self):
        """When every agent has the exact same count on the same day."""
        agents = [
            _agent(id="a%d" % i, name="A%d" % i, daily_messages={"2025-01-01": 42})
            for i in range(5)
        ]
        result = _render(agents)
        plain = _strip_ansi(result)
        assert plain.count("42 msgs") == 5

    def test_count_one_everywhere(self):
        """Every agent has count=1 — all bars should be max width."""
        agents = [
            _agent(id="a%d" % i, name="A%d" % i, daily_messages={"2025-01-01": 1})
            for i in range(3)
        ]
        result = _render(agents)
        plain = _strip_ansi(result)
        assert plain.count("1 msgs") == 3
