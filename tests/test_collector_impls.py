"""Tests for individual collector implementations.

Covers Claude, Gemini, Codex, and Aider collectors with mocked file-system
access.  Python 3.8 compatible -- no walrus operator, no match/case.
"""

import json
import os
from datetime import datetime, timezone
from unittest.mock import patch, mock_open, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Claude collector
# ---------------------------------------------------------------------------

from burnctl.collectors.claude import (
    ClaudeCollector,
    _cost_for_model,
    _default_pricing,
)


class TestDefaultPricing:
    """Verify _default_pricing() returns the expected fallback dict."""

    def test_returns_dict_with_required_keys(self):
        p = _default_pricing()
        assert isinstance(p, dict)
        assert set(p.keys()) == {"input", "output", "cache_read", "cache_create"}

    def test_values(self):
        p = _default_pricing()
        assert p["input"] == 5.0
        assert p["output"] == 25.0
        assert p["cache_read"] == 0.50
        assert p["cache_create"] == 6.25


class TestCostForModel:
    """Verify _cost_for_model() cost computation."""

    def test_known_model(self):
        pricing_table = {
            "claude-sonnet-4-6": {
                "input": 1.0,
                "output": 5.0,
                "cache_read": 0.10,
                "cache_create": 1.25,
            }
        }
        usage = {
            "inputTokens": 1_000_000,
            "outputTokens": 1_000_000,
            "cacheReadInputTokens": 1_000_000,
            "cacheCreationInputTokens": 1_000_000,
        }
        cost = _cost_for_model("claude-sonnet-4-6", usage, pricing_table)
        expected = 1.0 + 5.0 + 0.10 + 1.25
        assert cost == pytest.approx(expected)

    def test_unknown_model_uses_defaults(self):
        pricing_table = {}  # no models at all
        usage = {
            "inputTokens": 1_000_000,
            "outputTokens": 1_000_000,
            "cacheReadInputTokens": 0,
            "cacheCreationInputTokens": 0,
        }
        cost = _cost_for_model("unseen-model", usage, pricing_table)
        dp = _default_pricing()
        expected = dp["input"] + dp["output"]
        assert cost == pytest.approx(expected)

    def test_zero_tokens(self):
        cost = _cost_for_model("any", {}, {"any": _default_pricing()})
        assert cost == 0.0


class TestClaudeIsAvailable:
    """is_available delegates to os.path.isfile(STATS_FILE)."""

    def test_available_when_file_exists(self):
        with patch("burnctl.collectors.claude.os.path.isfile", return_value=True):
            assert ClaudeCollector().is_available() is True

    def test_unavailable_when_file_missing(self):
        with patch("burnctl.collectors.claude.os.path.isfile", return_value=False):
            assert ClaudeCollector().is_available() is False


class TestClaudeLoadData:
    """_load_data reads and parses STATS_FILE."""

    def test_valid_json(self):
        payload = {"totalMessages": 42}
        m = mock_open(read_data=json.dumps(payload))
        with patch("burnctl.collectors.claude.os.path.isfile", return_value=True), \
             patch("builtins.open", m):
            result = ClaudeCollector()._load_data()
        assert result == payload

    def test_malformed_json(self):
        m = mock_open(read_data="not-json{{{")
        with patch("burnctl.collectors.claude.os.path.isfile", return_value=True), \
             patch("builtins.open", m):
            result = ClaudeCollector()._load_data()
        assert result is None

    def test_missing_file(self):
        with patch("burnctl.collectors.claude.os.path.isfile", return_value=False):
            result = ClaudeCollector()._load_data()
        assert result is None


class TestClaudeGetStats:
    """get_stats with realistic mock data."""

    @staticmethod
    def _build_mock_data():
        """Return a mock stats-cache.json structure."""
        return {
            "firstSessionDate": "2026-03-01T10:00:00Z",
            "totalMessages": 150,
            "totalSessions": 30,
            "dailyActivity": [
                {"date": "2026-03-08", "messageCount": 5, "sessionCount": 2, "toolCallCount": 10},
                {"date": "2026-03-10", "messageCount": 10, "sessionCount": 3, "toolCallCount": 20},
                {"date": "2026-03-12", "messageCount": 8, "sessionCount": 1, "toolCallCount": 5},
                # Outside period (before start)
                {"date": "2026-02-28", "messageCount": 3, "sessionCount": 1, "toolCallCount": 2},
            ],
            "dailyModelTokens": [
                {"date": "2026-03-10", "tokensByModel": {"claude-sonnet-4-6": 5000}},
                {"date": "2026-03-12", "tokensByModel": {"claude-opus-4-6": 2000}},
                # Outside period
                {"date": "2026-02-28", "tokensByModel": {"claude-sonnet-4-6": 1000}},
            ],
            "modelUsage": {
                "claude-sonnet-4-6": {
                    "inputTokens": 100_000,
                    "outputTokens": 50_000,
                    "cacheReadInputTokens": 10_000,
                    "cacheCreationInputTokens": 5_000,
                },
            },
        }

    def test_period_filtering(self):
        """Only days within [start, end) should count."""
        data = self._build_mock_data()
        start = datetime(2026, 3, 10)
        end = datetime(2026, 3, 13)
        ref_date = datetime(2026, 3, 13)

        collector = ClaudeCollector()
        with patch.object(collector, "_load_data", return_value=data), \
             patch.object(collector, "_get_pricing_table", return_value=collector._fallback_pricing()):
            stats = collector.get_stats(start, end, ref_date)

        assert stats is not None
        # Only Mar 10 and Mar 12 are in [Mar 10, Mar 13)
        assert stats["messages"] == 10 + 8  # from daily activity
        assert stats["sessions"] == 3 + 1
        assert stats["tool_calls"] == 20 + 5

    def test_returns_all_expected_keys(self):
        data = self._build_mock_data()
        start = datetime(2026, 3, 10)
        end = datetime(2026, 3, 13)
        ref_date = datetime(2026, 3, 13)

        collector = ClaudeCollector()
        with patch.object(collector, "_load_data", return_value=data), \
             patch.object(collector, "_get_pricing_table", return_value=collector._fallback_pricing()):
            stats = collector.get_stats(start, end, ref_date)

        expected_keys = {
            "messages", "sessions", "output_tokens", "period_cost",
            "alltime_cost", "model_usage", "daily_messages",
            "first_session", "total_messages", "total_sessions",
            "tool_calls", "spark_data",
        }
        assert set(stats.keys()) == expected_keys

    def test_returns_none_when_file_missing(self):
        collector = ClaudeCollector()
        with patch.object(collector, "_load_data", return_value=None):
            stats = collector.get_stats(datetime(2026, 3, 1), datetime(2026, 3, 31), datetime(2026, 3, 13))
        assert stats is None

    def test_first_session_date_truncated(self):
        data = self._build_mock_data()
        collector = ClaudeCollector()
        with patch.object(collector, "_load_data", return_value=data), \
             patch.object(collector, "_get_pricing_table", return_value=collector._fallback_pricing()):
            stats = collector.get_stats(datetime(2026, 3, 10), datetime(2026, 3, 13), datetime(2026, 3, 13))
        assert stats["first_session"] == "2026-03-01"

    def test_daily_messages_map(self):
        data = self._build_mock_data()
        start = datetime(2026, 3, 10)
        end = datetime(2026, 3, 13)

        collector = ClaudeCollector()
        with patch.object(collector, "_load_data", return_value=data), \
             patch.object(collector, "_get_pricing_table", return_value=collector._fallback_pricing()):
            stats = collector.get_stats(start, end, datetime(2026, 3, 13))

        assert stats["daily_messages"]["2026-03-10"] == 10
        assert stats["daily_messages"]["2026-03-12"] == 8
        assert "2026-02-28" not in stats["daily_messages"]

    def test_spark_data_length(self):
        data = self._build_mock_data()
        start = datetime(2026, 3, 10)
        end = datetime(2026, 3, 13)
        ref_date = datetime(2026, 3, 13)

        collector = ClaudeCollector()
        with patch.object(collector, "_load_data", return_value=data), \
             patch.object(collector, "_get_pricing_table", return_value=collector._fallback_pricing()):
            stats = collector.get_stats(start, end, ref_date)

        # days_elapsed = min((ref_date - start).days, (end - start).days) = min(3, 3) = 3
        # range(4) => indices 0..3 => 4 entries
        assert len(stats["spark_data"]) == 4

    def test_period_cost_calculation(self):
        """period_cost should be computed using output-only token pricing."""
        data = self._build_mock_data()
        start = datetime(2026, 3, 10)
        end = datetime(2026, 3, 13)

        pricing = ClaudeCollector._fallback_pricing()
        collector = ClaudeCollector()
        with patch.object(collector, "_load_data", return_value=data), \
             patch.object(collector, "_get_pricing_table", return_value=pricing):
            stats = collector.get_stats(start, end, datetime(2026, 3, 13))

        # Mar 10: claude-sonnet-4-6 => 5000 tokens at $5/M output
        # Mar 12: claude-opus-4-6   => 2000 tokens at $25/M output
        expected = (5000 * 5.0 / 1_000_000) + (2000 * 25.0 / 1_000_000)
        assert stats["period_cost"] == pytest.approx(expected)

    def test_alltime_cost_uses_full_model_usage(self):
        data = self._build_mock_data()
        pricing = ClaudeCollector._fallback_pricing()
        collector = ClaudeCollector()
        with patch.object(collector, "_load_data", return_value=data), \
             patch.object(collector, "_get_pricing_table", return_value=pricing):
            stats = collector.get_stats(datetime(2026, 3, 10), datetime(2026, 3, 13), datetime(2026, 3, 13))

        # all-time cost uses _cost_for_model for every model in modelUsage
        model_usage = data["modelUsage"]["claude-sonnet-4-6"]
        expected = _cost_for_model("claude-sonnet-4-6", model_usage, pricing)
        assert stats["alltime_cost"] == pytest.approx(expected)


class TestClaudePlanInfo:
    """get_plan_info with various config dicts."""

    def test_default_plan(self):
        info = ClaudeCollector().get_plan_info({})
        assert info["plan_name"] == "max5x"
        assert info["plan_price"] == 100
        assert info["billing_day"] == 10

    def test_explicit_claude_plan(self):
        info = ClaudeCollector().get_plan_info({"claude_plan": "pro"})
        assert info["plan_name"] == "pro"
        assert info["plan_price"] == 20

    def test_generic_plan_key_fallback(self):
        info = ClaudeCollector().get_plan_info({"plan": "free"})
        assert info["plan_name"] == "free"
        assert info["plan_price"] == 0

    def test_claude_plan_overrides_generic_plan(self):
        info = ClaudeCollector().get_plan_info({"claude_plan": "max20x", "plan": "free"})
        assert info["plan_name"] == "max20x"
        assert info["plan_price"] == 200

    def test_annual_billing(self):
        info = ClaudeCollector().get_plan_info({
            "claude_plan": "pro",
            "billing_interval": "yr",
        })
        assert info["plan_price"] == pytest.approx(200 / 12)
        assert info["interval"] == "yr"

    def test_annual_billing_no_annual_price(self):
        """max5x has no annual pricing -- should fall back to monthly."""
        info = ClaudeCollector().get_plan_info({
            "claude_plan": "max5x",
            "billing_interval": "yr",
        })
        assert info["plan_price"] == 100

    def test_billing_day_from_config(self):
        info = ClaudeCollector().get_plan_info({"billing_day": 25})
        assert info["billing_day"] == 25


class TestClaudeUpgradeUrl:
    def test_returns_billing_url(self):
        assert ClaudeCollector().get_upgrade_url() == "https://claude.ai/settings/billing"


class TestClaudeGetPricingTable:
    """_get_pricing_table with/without claude_usage installed."""

    def test_without_claude_usage(self):
        data = {"modelUsage": {}, "dailyModelTokens": []}
        collector = ClaudeCollector()
        with patch.dict("sys.modules", {"claude_usage": None, "claude_usage.pricing": None}):
            table = collector._get_pricing_table(data)
        # Should return the fallback pricing table
        assert "claude-opus-4-6" in table
        assert "claude-sonnet-4-6" in table

    def test_with_claude_usage(self):
        mock_pricing = {"model-a": {"input": 1.0, "output": 2.0, "cache_read": 0.1}}
        data = {"modelUsage": {"model-a": {}}, "dailyModelTokens": []}

        with patch("burnctl.collectors.claude.ClaudeCollector._fallback_pricing") as fb:
            fb.return_value = mock_pricing
            # Simulate ImportError on the import inside _get_pricing_table
            collector = ClaudeCollector()
            with patch.dict("sys.modules", {"claude_usage": None, "claude_usage.pricing": None}):
                table = collector._get_pricing_table(data)

        # When ImportError occurs, should use fallback
        assert isinstance(table, dict)

    def test_unknown_model_triggers_force_refresh(self):
        """When data references a model not in the pricing table,
        _get_pricing_table should call get_pricing(force_refresh=True)."""
        initial = {"known-model": {"input": 1.0, "output": 2.0, "cache_read": 0.1}}
        refreshed = {
            "known-model": {"input": 1.0, "output": 2.0, "cache_read": 0.1},
            "unknown-model": {"input": 3.0, "output": 6.0, "cache_read": 0.5},
        }

        mock_get_pricing = MagicMock(side_effect=[initial, refreshed])
        fake_module = MagicMock()
        fake_module.get_pricing = mock_get_pricing

        data = {
            "modelUsage": {"unknown-model": {"inputTokens": 100}},
            "dailyModelTokens": [],
        }

        collector = ClaudeCollector()
        with patch.dict("sys.modules", {
            "claude_usage": MagicMock(),
            "claude_usage.pricing": fake_module,
        }):
            table = collector._get_pricing_table(data)

        # First call: get_pricing() -> initial (no unknown-model)
        # Second call: get_pricing(force_refresh=True) -> refreshed
        assert mock_get_pricing.call_count == 2
        mock_get_pricing.assert_called_with(force_refresh=True)
        assert "unknown-model" in table

    def test_unknown_model_in_daily_tokens(self):
        """Unknown models discovered in dailyModelTokens also trigger refresh."""
        initial = {"known": {"input": 1.0, "output": 2.0, "cache_read": 0.1}}
        refreshed = dict(initial)
        refreshed["new-model"] = {"input": 2.0, "output": 4.0, "cache_read": 0.2}

        mock_get_pricing = MagicMock(side_effect=[initial, refreshed])
        fake_module = MagicMock()
        fake_module.get_pricing = mock_get_pricing

        data = {
            "modelUsage": {},
            "dailyModelTokens": [
                {"date": "2026-03-10", "tokensByModel": {"new-model": 5000}},
            ],
        }

        collector = ClaudeCollector()
        with patch.dict("sys.modules", {
            "claude_usage": MagicMock(),
            "claude_usage.pricing": fake_module,
        }):
            table = collector._get_pricing_table(data)

        assert mock_get_pricing.call_count == 2
        assert "new-model" in table


# ---------------------------------------------------------------------------
# Gemini collector
# ---------------------------------------------------------------------------

from burnctl.collectors.gemini import GeminiCollector, _parse_iso


class TestGeminiParseIso:
    """Verify _parse_iso handles various timestamp formats."""

    def test_z_suffix(self):
        dt = _parse_iso("2026-03-10T14:30:00Z")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 3
        assert dt.day == 10

    def test_plus_offset(self):
        dt = _parse_iso("2026-03-10T14:30:00+00:00")
        assert dt is not None
        assert dt.year == 2026

    def test_invalid_string(self):
        assert _parse_iso("not-a-timestamp") is None

    def test_none_input(self):
        assert _parse_iso(None) is None

    def test_empty_string(self):
        assert _parse_iso("") is None


class TestGeminiIsAvailable:
    """is_available delegates to glob.glob."""

    def test_available_when_sessions_exist(self):
        with patch("burnctl.collectors.gemini.glob.glob", return_value=["/some/session.json"]):
            assert GeminiCollector().is_available() is True

    def test_unavailable_when_no_sessions(self):
        with patch("burnctl.collectors.gemini.glob.glob", return_value=[]):
            assert GeminiCollector().is_available() is False


class TestGeminiGetStats:
    """get_stats with temporary session JSON files."""

    @staticmethod
    def _make_session_file(tmp_path, name, session_data):
        """Write a session JSON file and return its path."""
        fpath = tmp_path / name
        fpath.write_text(json.dumps(session_data))
        return str(fpath)

    def test_with_mock_session_data(self, tmp_path):
        session = {
            "startTime": "2026-03-10T10:00:00Z",
            "messages": [
                {
                    "type": "user",
                    "timestamp": "2026-03-10T10:00:00Z",
                    "content": "hello",
                },
                {
                    "type": "gemini",
                    "timestamp": "2026-03-10T10:01:00Z",
                    "model": "gemini-2.5-flash",
                    "tokens": {"input": 100, "output": 200, "cached": 50},
                    "toolCalls": [{"name": "read_file"}, {"name": "edit_file"}],
                },
                {
                    "type": "user",
                    "timestamp": "2026-03-10T10:02:00Z",
                    "content": "more",
                },
                {
                    "type": "gemini",
                    "timestamp": "2026-03-10T10:03:00Z",
                    "model": "gemini-2.5-pro",
                    "tokens": {"input": 300, "output": 400, "cached": 0},
                    "toolCalls": [],
                },
            ],
        }
        fpath = self._make_session_file(tmp_path, "session-001.json", session)

        start = datetime(2026, 3, 10)
        end = datetime(2026, 3, 11)
        ref_date = datetime(2026, 3, 10)

        with patch("burnctl.collectors.gemini.glob.glob", return_value=[fpath]):
            stats = GeminiCollector().get_stats(start, end, ref_date)

        assert stats is not None
        assert stats["messages"] == 2
        assert stats["sessions"] == 1
        assert stats["output_tokens"] == 200 + 400
        assert stats["tool_calls"] == 2

    def test_period_vs_alltime(self, tmp_path):
        """Sessions outside the period should only count toward all-time."""
        in_period = {
            "startTime": "2026-03-10T10:00:00Z",
            "messages": [
                {"type": "user", "timestamp": "2026-03-10T10:00:00Z", "content": "hi"},
                {
                    "type": "gemini",
                    "timestamp": "2026-03-10T10:01:00Z",
                    "model": "gemini-2.5-flash",
                    "tokens": {"input": 100, "output": 200, "cached": 0},
                },
            ],
        }
        out_of_period = {
            "startTime": "2026-03-05T10:00:00Z",
            "messages": [
                {"type": "user", "timestamp": "2026-03-05T10:00:00Z", "content": "old"},
                {
                    "type": "gemini",
                    "timestamp": "2026-03-05T10:01:00Z",
                    "model": "gemini-2.5-flash",
                    "tokens": {"input": 500, "output": 600, "cached": 0},
                },
            ],
        }
        f1 = self._make_session_file(tmp_path, "session-in.json", in_period)
        f2 = self._make_session_file(tmp_path, "session-out.json", out_of_period)

        start = datetime(2026, 3, 10)
        end = datetime(2026, 3, 11)

        with patch("burnctl.collectors.gemini.glob.glob", return_value=[f1, f2]):
            stats = GeminiCollector().get_stats(start, end, datetime(2026, 3, 10))

        assert stats["messages"] == 1  # period only
        assert stats["sessions"] == 1  # period only
        assert stats["total_sessions"] == 2  # all-time
        assert stats["total_messages"] == 2  # all-time

    def test_model_usage_accumulation(self, tmp_path):
        session = {
            "startTime": "2026-03-10T10:00:00Z",
            "messages": [
                {"type": "user", "timestamp": "2026-03-10T10:00:00Z", "content": "q1"},
                {
                    "type": "gemini",
                    "timestamp": "2026-03-10T10:01:00Z",
                    "model": "gemini-2.5-flash",
                    "tokens": {"input": 100, "output": 200, "cached": 50},
                },
                {"type": "user", "timestamp": "2026-03-10T10:02:00Z", "content": "q2"},
                {
                    "type": "gemini",
                    "timestamp": "2026-03-10T10:03:00Z",
                    "model": "gemini-2.5-flash",
                    "tokens": {"input": 300, "output": 400, "cached": 0},
                },
            ],
        }
        fpath = self._make_session_file(tmp_path, "session-acc.json", session)

        with patch("burnctl.collectors.gemini.glob.glob", return_value=[fpath]):
            stats = GeminiCollector().get_stats(
                datetime(2026, 3, 10), datetime(2026, 3, 11), datetime(2026, 3, 10),
            )

        mu = stats["model_usage"]
        assert "gemini-2.5-flash" in mu
        assert mu["gemini-2.5-flash"]["inputTokens"] == 100 + 300
        assert mu["gemini-2.5-flash"]["outputTokens"] == 200 + 400
        assert mu["gemini-2.5-flash"]["cachedTokens"] == 50

    def test_daily_messages_map(self, tmp_path):
        session = {
            "startTime": "2026-03-10T10:00:00Z",
            "messages": [
                {"type": "user", "timestamp": "2026-03-10T10:00:00Z", "content": "a"},
                {"type": "gemini", "timestamp": "2026-03-10T10:01:00Z", "model": "m",
                 "tokens": {"input": 1, "output": 1, "cached": 0}},
                {"type": "user", "timestamp": "2026-03-10T10:05:00Z", "content": "b"},
                {"type": "gemini", "timestamp": "2026-03-10T10:06:00Z", "model": "m",
                 "tokens": {"input": 1, "output": 1, "cached": 0}},
            ],
        }
        fpath = self._make_session_file(tmp_path, "session-dm.json", session)

        with patch("burnctl.collectors.gemini.glob.glob", return_value=[fpath]):
            stats = GeminiCollector().get_stats(
                datetime(2026, 3, 10), datetime(2026, 3, 11), datetime(2026, 3, 10),
            )

        assert stats["daily_messages"] == {"2026-03-10": 2}

    def test_returns_none_when_no_sessions(self):
        with patch("burnctl.collectors.gemini.glob.glob", return_value=[]):
            stats = GeminiCollector().get_stats(
                datetime(2026, 3, 10), datetime(2026, 3, 11), datetime(2026, 3, 10),
            )
        assert stats is None

    def test_first_session_tracking(self, tmp_path):
        older = {
            "startTime": "2026-03-05T10:00:00Z",
            "messages": [
                {"type": "user", "timestamp": "2026-03-05T10:00:00Z", "content": "hi"},
                {"type": "gemini", "timestamp": "2026-03-05T10:01:00Z", "model": "m",
                 "tokens": {"input": 1, "output": 1, "cached": 0}},
            ],
        }
        newer = {
            "startTime": "2026-03-10T10:00:00Z",
            "messages": [
                {"type": "user", "timestamp": "2026-03-10T10:00:00Z", "content": "hi"},
                {"type": "gemini", "timestamp": "2026-03-10T10:01:00Z", "model": "m",
                 "tokens": {"input": 1, "output": 1, "cached": 0}},
            ],
        }
        f1 = self._make_session_file(tmp_path, "session-old.json", older)
        f2 = self._make_session_file(tmp_path, "session-new.json", newer)

        with patch("burnctl.collectors.gemini.glob.glob", return_value=[f1, f2]):
            stats = GeminiCollector().get_stats(
                datetime(2026, 3, 1), datetime(2026, 3, 31), datetime(2026, 3, 13),
            )

        assert stats["first_session"] == "2026-03-05"


class TestGeminiPlanInfo:
    def test_default(self):
        info = GeminiCollector().get_plan_info({})
        assert info["plan_name"] == "pay-as-you-go"
        assert info["plan_price"] == 0
        assert info["billing_day"] == 1

    def test_custom_billing_day(self):
        info = GeminiCollector().get_plan_info({"billing_day": 15})
        assert info["billing_day"] == 15


class TestGeminiUpgradeUrl:
    def test_returns_url(self):
        assert GeminiCollector().get_upgrade_url() == "https://aistudio.google.com/app/plan_management"


# ---------------------------------------------------------------------------
# Codex collector
# ---------------------------------------------------------------------------

from burnctl.collectors.codex import (
    CodexCollector,
    _parse_ts,
    _parse_session,
    _compute_session_cost,
    _default_model_pricing,
)


class TestCodexParseTs:
    """Verify _parse_ts with various timestamp formats."""

    def test_z_suffix(self):
        dt = _parse_ts("2026-03-10T14:30:00Z")
        assert dt is not None
        assert dt.tzinfo is not None
        assert dt.year == 2026

    def test_plus_offset(self):
        dt = _parse_ts("2026-03-10T14:30:00+05:00")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_invalid(self):
        assert _parse_ts("garbage") is None

    def test_none(self):
        assert _parse_ts(None) is None

    def test_empty_string(self):
        assert _parse_ts("") is None


class TestCodexParseSession:
    """_parse_session with mock JSONL data."""

    @staticmethod
    def _write_jsonl(path, events):
        with open(str(path), "w") as f:
            for evt in events:
                f.write(json.dumps(evt) + "\n")

    def test_full_session(self, tmp_path):
        events = [
            {
                "type": "session_meta",
                "timestamp": "2026-03-10T10:00:00Z",
                "payload": {"timestamp": "2026-03-10T10:00:00Z"},
            },
            {
                "type": "turn_context",
                "timestamp": "2026-03-10T10:00:01Z",
                "payload": {"model": "codex-mini"},
            },
            {
                "type": "event_msg",
                "timestamp": "2026-03-10T10:00:02Z",
                "payload": {"type": "user_message", "content": "hello"},
            },
            {
                "type": "event_msg",
                "timestamp": "2026-03-10T10:00:03Z",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 1000,
                            "output_tokens": 500,
                            "cached_input_tokens": 200,
                        }
                    },
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-03-10T10:00:04Z",
                "payload": {"type": "function_call", "name": "read_file"},
            },
            {
                "type": "event_msg",
                "timestamp": "2026-03-10T10:00:05Z",
                "payload": {"type": "exec_command", "command": "ls"},
            },
        ]
        fpath = tmp_path / "session.jsonl"
        self._write_jsonl(fpath, events)

        result = _parse_session(str(fpath))

        assert result is not None
        assert result["models"] == {"codex-mini"}
        assert len(result["user_messages"]) == 1
        assert result["total_token_usage"]["input_tokens"] == 1000
        assert result["total_token_usage"]["output_tokens"] == 500
        assert result["tool_calls"] == 2  # function_call + exec_command

    def test_empty_file_returns_none(self, tmp_path):
        fpath = tmp_path / "empty.jsonl"
        fpath.write_text("")
        assert _parse_session(str(fpath)) is None

    def test_malformed_lines_skipped(self, tmp_path):
        fpath = tmp_path / "bad.jsonl"
        fpath.write_text("not-json\n{invalid json}\n")
        # No session_meta and no user_messages -> returns None
        assert _parse_session(str(fpath)) is None

    def test_session_ts_from_meta(self, tmp_path):
        events = [
            {
                "type": "session_meta",
                "timestamp": "2026-03-10T10:00:00Z",
                "payload": {"timestamp": "2026-03-10T12:00:00Z"},
            },
        ]
        fpath = tmp_path / "meta-only.jsonl"
        self._write_jsonl(fpath, events)

        result = _parse_session(str(fpath))
        assert result is not None
        # payload timestamp should be preferred
        assert result["session_ts"].hour == 12


class TestCodexComputeSessionCost:
    """_compute_session_cost with known token usage."""

    def test_with_known_pricing(self):
        usage = {
            "input_tokens": 1_000_000,
            "cached_input_tokens": 200_000,
            "output_tokens": 500_000,
        }
        pricing = {"input": 2.50, "output": 10.0, "cache_read": 0.625}
        cost = _compute_session_cost(usage, pricing)

        non_cached = 800_000
        expected = (
            non_cached * 2.50
            + 200_000 * 0.625
            + 500_000 * 10.0
        ) / 1_000_000
        assert cost == pytest.approx(expected)

    def test_none_usage_returns_zero(self):
        assert _compute_session_cost(None, {}) == 0.0

    def test_empty_usage_returns_zero(self):
        assert _compute_session_cost({}, {"input": 1.0, "output": 2.0}) == 0.0

    def test_default_pricing_fallback(self):
        """When pricing dict doesn't have 'input', falls back to defaults."""
        usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cached_input_tokens": 0}
        cost = _compute_session_cost(usage, {"bad_key": 123})
        dp = _default_model_pricing()
        expected = (1_000_000 * dp["input"] + 1_000_000 * dp["output"]) / 1_000_000
        assert cost == pytest.approx(expected)


class TestCodexDefaultModelPricing:
    def test_returns_correct_structure(self):
        p = _default_model_pricing()
        assert isinstance(p, dict)
        assert "input" in p
        assert "output" in p
        assert "cache_read" in p
        assert p["input"] == 1.50
        assert p["output"] == 6.0
        assert p["cache_read"] == 0.375


class TestCodexIsAvailable:
    def test_available_when_dir_exists(self):
        with patch("burnctl.collectors.codex.os.path.isdir", return_value=True):
            assert CodexCollector().is_available() is True

    def test_unavailable_when_dir_missing(self):
        with patch("burnctl.collectors.codex.os.path.isdir", return_value=False):
            assert CodexCollector().is_available() is False


class TestCodexGetStats:
    """get_stats with temp JSONL files."""

    @staticmethod
    def _write_jsonl(path, events):
        with open(str(path), "w") as f:
            for evt in events:
                f.write(json.dumps(evt) + "\n")

    @staticmethod
    def _make_session_events(ts_str, model="codex-mini", input_tokens=1000,
                             output_tokens=500, cached=100, tool_calls=1):
        """Build a list of JSONL events for a complete session."""
        events = [
            {
                "type": "session_meta",
                "timestamp": ts_str,
                "payload": {"timestamp": ts_str},
            },
            {
                "type": "turn_context",
                "timestamp": ts_str,
                "payload": {"model": model},
            },
            {
                "type": "event_msg",
                "timestamp": ts_str,
                "payload": {"type": "user_message", "content": "hello"},
            },
            {
                "type": "event_msg",
                "timestamp": ts_str,
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "cached_input_tokens": cached,
                        }
                    },
                },
            },
        ]
        for _ in range(tool_calls):
            events.append({
                "type": "response_item",
                "timestamp": ts_str,
                "payload": {"type": "function_call", "name": "tool"},
            })
        return events

    def test_period_filtering(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        # In-period session
        in_events = self._make_session_events("2026-03-10T10:00:00Z")
        self._write_jsonl(sessions_dir / "session-in.jsonl", in_events)

        # Out-of-period session
        out_events = self._make_session_events("2026-03-05T10:00:00Z")
        self._write_jsonl(sessions_dir / "session-out.jsonl", out_events)

        start = datetime(2026, 3, 10, tzinfo=timezone.utc)
        end = datetime(2026, 3, 11, tzinfo=timezone.utc)
        ref = datetime(2026, 3, 10, tzinfo=timezone.utc)

        with patch("burnctl.collectors.codex.SESSIONS_DIR", str(sessions_dir)), \
             patch("burnctl.collectors.codex.os.path.isdir", return_value=True), \
             patch("burnctl.collectors.codex.HISTORY_FILE", str(tmp_path / "history.jsonl")):
            stats = CodexCollector().get_stats(start, end, ref)

        assert stats is not None
        assert stats["sessions"] == 1  # only in-period
        assert stats["total_sessions"] == 2  # all-time

    def test_returns_all_expected_keys(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        events = self._make_session_events("2026-03-10T10:00:00Z")
        self._write_jsonl(sessions_dir / "session.jsonl", events)

        start = datetime(2026, 3, 10, tzinfo=timezone.utc)
        end = datetime(2026, 3, 11, tzinfo=timezone.utc)
        ref = datetime(2026, 3, 10, tzinfo=timezone.utc)

        with patch("burnctl.collectors.codex.SESSIONS_DIR", str(sessions_dir)), \
             patch("burnctl.collectors.codex.os.path.isdir", return_value=True), \
             patch("burnctl.collectors.codex.HISTORY_FILE", str(tmp_path / "history.jsonl")):
            stats = CodexCollector().get_stats(start, end, ref)

        expected_keys = {
            "messages", "sessions", "output_tokens", "period_cost",
            "alltime_cost", "model_usage", "daily_messages",
            "first_session", "total_messages", "total_sessions",
            "tool_calls", "spark_data",
        }
        assert set(stats.keys()) == expected_keys

    def test_returns_none_when_no_sessions_dir(self):
        with patch("burnctl.collectors.codex.os.path.isdir", return_value=False):
            stats = CodexCollector().get_stats(
                datetime(2026, 3, 10), datetime(2026, 3, 11), datetime(2026, 3, 10),
            )
        assert stats is None

    def test_returns_none_when_no_session_files(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        # No .jsonl files inside

        with patch("burnctl.collectors.codex.SESSIONS_DIR", str(sessions_dir)), \
             patch("burnctl.collectors.codex.os.path.isdir", return_value=True):
            stats = CodexCollector().get_stats(
                datetime(2026, 3, 10, tzinfo=timezone.utc),
                datetime(2026, 3, 11, tzinfo=timezone.utc),
                datetime(2026, 3, 10, tzinfo=timezone.utc),
            )
        assert stats is None

    def test_timezone_aware_comparison(self, tmp_path):
        """Naive start/end should be made tz-aware before comparing."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        events = self._make_session_events("2026-03-10T10:00:00Z")
        self._write_jsonl(sessions_dir / "session.jsonl", events)

        # Pass naive datetimes -- the collector should coerce to UTC
        start = datetime(2026, 3, 10)
        end = datetime(2026, 3, 11)
        ref = datetime(2026, 3, 10)

        with patch("burnctl.collectors.codex.SESSIONS_DIR", str(sessions_dir)), \
             patch("burnctl.collectors.codex.os.path.isdir", return_value=True), \
             patch("burnctl.collectors.codex.HISTORY_FILE", str(tmp_path / "history.jsonl")):
            stats = CodexCollector().get_stats(start, end, ref)

        assert stats is not None
        assert stats["sessions"] == 1

    def test_model_usage_accumulated(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        events = self._make_session_events(
            "2026-03-10T10:00:00Z", model="o3", input_tokens=2000, output_tokens=1000,
        )
        self._write_jsonl(sessions_dir / "session.jsonl", events)

        start = datetime(2026, 3, 10, tzinfo=timezone.utc)
        end = datetime(2026, 3, 11, tzinfo=timezone.utc)
        ref = datetime(2026, 3, 10, tzinfo=timezone.utc)

        with patch("burnctl.collectors.codex.SESSIONS_DIR", str(sessions_dir)), \
             patch("burnctl.collectors.codex.os.path.isdir", return_value=True), \
             patch("burnctl.collectors.codex.HISTORY_FILE", str(tmp_path / "history.jsonl")):
            stats = CodexCollector().get_stats(start, end, ref)

        assert "o3" in stats["model_usage"]
        assert stats["model_usage"]["o3"]["inputTokens"] == 2000
        assert stats["model_usage"]["o3"]["outputTokens"] == 1000

    def test_first_session_date(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        old_events = self._make_session_events("2026-03-05T10:00:00Z")
        self._write_jsonl(sessions_dir / "session-old.jsonl", old_events)

        new_events = self._make_session_events("2026-03-10T10:00:00Z")
        self._write_jsonl(sessions_dir / "session-new.jsonl", new_events)

        start = datetime(2026, 3, 1, tzinfo=timezone.utc)
        end = datetime(2026, 3, 31, tzinfo=timezone.utc)
        ref = datetime(2026, 3, 13, tzinfo=timezone.utc)

        with patch("burnctl.collectors.codex.SESSIONS_DIR", str(sessions_dir)), \
             patch("burnctl.collectors.codex.os.path.isdir", return_value=True), \
             patch("burnctl.collectors.codex.HISTORY_FILE", str(tmp_path / "history.jsonl")):
            stats = CodexCollector().get_stats(start, end, ref)

        assert stats["first_session"] == "2026-03-05"

    def test_spark_data_length(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        events = self._make_session_events("2026-03-10T10:00:00Z")
        self._write_jsonl(sessions_dir / "session.jsonl", events)

        start = datetime(2026, 3, 10, tzinfo=timezone.utc)
        end = datetime(2026, 3, 13, tzinfo=timezone.utc)
        ref = datetime(2026, 3, 13, tzinfo=timezone.utc)

        with patch("burnctl.collectors.codex.SESSIONS_DIR", str(sessions_dir)), \
             patch("burnctl.collectors.codex.os.path.isdir", return_value=True), \
             patch("burnctl.collectors.codex.HISTORY_FILE", str(tmp_path / "history.jsonl")):
            stats = CodexCollector().get_stats(start, end, ref)

        # days_elapsed = min(3, 3) = 3 => range(4) => 4 entries
        assert len(stats["spark_data"]) == 4


class TestCodexCountHistory:
    """_count_history with/without history.jsonl."""

    def test_with_history_file(self, tmp_path):
        history = tmp_path / "history.jsonl"
        lines = [
            json.dumps({"session_id": "s1", "content": "msg1"}),
            json.dumps({"session_id": "s1", "content": "msg2"}),
            json.dumps({"session_id": "s2", "content": "msg3"}),
        ]
        history.write_text("\n".join(lines) + "\n")

        with patch("burnctl.collectors.codex.HISTORY_FILE", str(history)):
            msgs, sessions = CodexCollector._count_history()

        assert msgs == 3
        assert sessions == 2

    def test_without_history_file(self):
        with patch("burnctl.collectors.codex.HISTORY_FILE", "/nonexistent/history.jsonl"):
            msgs, sessions = CodexCollector._count_history()
        assert msgs == 0
        assert sessions == 0

    def test_empty_history_file(self, tmp_path):
        history = tmp_path / "history.jsonl"
        history.write_text("")

        with patch("burnctl.collectors.codex.HISTORY_FILE", str(history)):
            msgs, sessions = CodexCollector._count_history()

        assert msgs == 0
        assert sessions == 0

    def test_malformed_lines_skipped(self, tmp_path):
        history = tmp_path / "history.jsonl"
        history.write_text(
            "not json\n"
            + json.dumps({"session_id": "s1"}) + "\n"
            + "{bad\n"
        )

        with patch("burnctl.collectors.codex.HISTORY_FILE", str(history)):
            msgs, sessions = CodexCollector._count_history()

        assert msgs == 1
        assert sessions == 1


class TestCodexPlanInfo:
    def test_default(self):
        info = CodexCollector().get_plan_info({})
        assert info["plan_name"] == "pay-as-you-go"
        assert info["plan_price"] == 0
        assert info["billing_day"] == 1

    def test_custom_billing_day(self):
        info = CodexCollector().get_plan_info({"billing_day": 20})
        assert info["billing_day"] == 20


class TestCodexUpgradeUrl:
    def test_returns_url(self):
        assert CodexCollector().get_upgrade_url() == "https://platform.openai.com/usage"


# ---------------------------------------------------------------------------
# Codex: response_item tool counting (lines 136-143)
# ---------------------------------------------------------------------------


class TestCodexParseSessionResponseItem:
    """Cover response_item event parsing in _parse_session."""

    @staticmethod
    def _write_jsonl(path, events):
        with open(str(path), "w") as f:
            for evt in events:
                f.write(json.dumps(evt) + "\n")

    def test_response_item_function_call(self, tmp_path):
        """response_item with type=function_call increments tool_calls."""
        events = [
            {
                "type": "session_meta",
                "timestamp": "2026-03-10T10:00:00Z",
                "payload": {"timestamp": "2026-03-10T10:00:00Z"},
            },
            {
                "type": "response_item",
                "timestamp": "2026-03-10T10:00:01Z",
                "payload": {"type": "function_call", "name": "read_file"},
            },
            {
                "type": "response_item",
                "timestamp": "2026-03-10T10:00:02Z",
                "payload": {"type": "tool_call", "name": "write_file"},
            },
        ]
        fpath = tmp_path / "ri.jsonl"
        self._write_jsonl(fpath, events)

        result = _parse_session(str(fpath))
        assert result is not None
        assert result["tool_calls"] == 2

    def test_response_item_content_array_tool_use(self, tmp_path):
        """response_item with content array containing tool_use parts."""
        events = [
            {
                "type": "session_meta",
                "timestamp": "2026-03-10T10:00:00Z",
                "payload": {"timestamp": "2026-03-10T10:00:00Z"},
            },
            {
                "type": "response_item",
                "timestamp": "2026-03-10T10:00:01Z",
                "payload": {
                    "type": "message",
                    "content": [
                        {"type": "tool_use", "name": "bash"},
                        {"type": "text", "text": "hello"},
                        {"type": "function_call", "name": "edit"},
                    ],
                },
            },
        ]
        fpath = tmp_path / "ri_content.jsonl"
        self._write_jsonl(fpath, events)

        result = _parse_session(str(fpath))
        assert result is not None
        # Two content parts match: tool_use + function_call
        assert result["tool_calls"] == 2

    def test_response_item_both_type_and_content(self, tmp_path):
        """response_item that is itself a function_call AND has content parts."""
        events = [
            {
                "type": "session_meta",
                "timestamp": "2026-03-10T10:00:00Z",
                "payload": {"timestamp": "2026-03-10T10:00:00Z"},
            },
            {
                "type": "response_item",
                "timestamp": "2026-03-10T10:00:01Z",
                "payload": {
                    "type": "function_call",
                    "content": [
                        {"type": "tool_use", "name": "bash"},
                    ],
                },
            },
        ]
        fpath = tmp_path / "ri_both.jsonl"
        self._write_jsonl(fpath, events)

        result = _parse_session(str(fpath))
        assert result is not None
        # 1 from function_call type + 1 from content tool_use part
        assert result["tool_calls"] == 2


# ---------------------------------------------------------------------------
# Codex: _iter_session_files edge cases (line 51, 71, 73-74)
# ---------------------------------------------------------------------------

from burnctl.collectors.codex import _iter_session_files, _MAX_SESSION_BYTES


class TestCodexIterSessionFiles:
    """Cover skipping of large files and OSError on getsize."""

    def test_skips_large_files(self, tmp_path):
        """Files > 50 MB should be silently skipped."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        small = sessions_dir / "small.jsonl"
        small.write_text("{}\n")
        large = sessions_dir / "large.jsonl"
        # Write a file and use a wrapper that only intercepts getsize
        large.write_text("{}\n")

        _real_getsize = os.path.getsize

        def selective_getsize(path):
            if os.path.basename(str(path)) == "large.jsonl":
                return _MAX_SESSION_BYTES + 1
            return _real_getsize(path)

        # Patch at module attribute level so the codex code picks it up
        import burnctl.collectors.codex as codex_mod
        original = codex_mod.os.path.getsize
        try:
            codex_mod.os.path.getsize = selective_getsize
            with patch("burnctl.collectors.codex.SESSIONS_DIR",
                       str(sessions_dir)):
                result = list(_iter_session_files())
        finally:
            codex_mod.os.path.getsize = original

        assert len(result) == 1
        assert "small.jsonl" in result[0]

    def test_oserror_on_getsize_skips_file(self, tmp_path):
        """OSError on os.path.getsize should skip the file."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        f = sessions_dir / "bad.jsonl"
        f.write_text("{}\n")

        import burnctl.collectors.codex as codex_mod
        original = codex_mod.os.path.getsize
        try:
            codex_mod.os.path.getsize = MagicMock(
                side_effect=OSError("permission denied"),
            )
            with patch("burnctl.collectors.codex.SESSIONS_DIR",
                       str(sessions_dir)):
                result = list(_iter_session_files())
        finally:
            codex_mod.os.path.getsize = original

        assert len(result) == 0

    def test_non_jsonl_files_ignored(self, tmp_path):
        """Non-.jsonl files should not be yielded."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        (sessions_dir / "readme.txt").write_text("not a session")
        (sessions_dir / "valid.jsonl").write_text("{}\n")

        with patch("burnctl.collectors.codex.SESSIONS_DIR", str(sessions_dir)):
            result = list(_iter_session_files())

        assert len(result) == 1
        assert "valid.jsonl" in result[0]


# ---------------------------------------------------------------------------
# Codex get_stats edge cases: None msg_ts, alltime==0, history folding
# ---------------------------------------------------------------------------


class TestCodexGetStatsEdgeCases:
    """Cover lines 309, 318, 323, 325 in codex.py get_stats."""

    @staticmethod
    def _write_jsonl(path, events):
        with open(str(path), "w") as f:
            for evt in events:
                f.write(json.dumps(evt) + "\n")

    def test_user_message_with_none_timestamp_skipped(self, tmp_path):
        """Line 320: user_message with None timestamp -> continue."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        events = [
            {
                "type": "session_meta",
                "timestamp": "2026-03-10T10:00:00Z",
                "payload": {"timestamp": "2026-03-10T10:00:00Z"},
            },
            {
                "type": "event_msg",
                "timestamp": None,  # will parse to None
                "payload": {"type": "user_message", "content": "hi"},
            },
            {
                "type": "event_msg",
                "timestamp": "2026-03-10T10:01:00Z",
                "payload": {"type": "user_message", "content": "valid"},
            },
        ]
        self._write_jsonl(sessions_dir / "session.jsonl", events)

        start = datetime(2026, 3, 10, tzinfo=timezone.utc)
        end = datetime(2026, 3, 11, tzinfo=timezone.utc)
        ref = datetime(2026, 3, 10, tzinfo=timezone.utc)

        with patch("burnctl.collectors.codex.SESSIONS_DIR", str(sessions_dir)), \
             patch("burnctl.collectors.codex.os.path.isdir", return_value=True), \
             patch("burnctl.collectors.codex.HISTORY_FILE",
                   str(tmp_path / "history.jsonl")):
            stats = CodexCollector().get_stats(start, end, ref)

        assert stats is not None
        # Only the valid message should be counted
        assert stats["messages"] == 1

    def test_all_sessions_unparseable_returns_none(self, tmp_path):
        """Line 329: alltime_sessions == 0 -> return None."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        # Write a file with only malformed JSON lines
        f = sessions_dir / "bad.jsonl"
        f.write_text("not json at all\n{also bad\n")

        start = datetime(2026, 3, 10, tzinfo=timezone.utc)
        end = datetime(2026, 3, 11, tzinfo=timezone.utc)
        ref = datetime(2026, 3, 10, tzinfo=timezone.utc)

        with patch("burnctl.collectors.codex.SESSIONS_DIR", str(sessions_dir)), \
             patch("burnctl.collectors.codex.os.path.isdir", return_value=True), \
             patch("burnctl.collectors.codex.HISTORY_FILE",
                   str(tmp_path / "history.jsonl")):
            stats = CodexCollector().get_stats(start, end, ref)

        assert stats is None

    def test_history_counts_overwrite_when_larger(self, tmp_path):
        """Lines 334-337: history counts > alltime -> overwrite."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        # One session with 1 user message
        events = [
            {
                "type": "session_meta",
                "timestamp": "2026-03-10T10:00:00Z",
                "payload": {"timestamp": "2026-03-10T10:00:00Z"},
            },
            {
                "type": "event_msg",
                "timestamp": "2026-03-10T10:01:00Z",
                "payload": {"type": "user_message", "content": "hi"},
            },
        ]
        self._write_jsonl(sessions_dir / "session.jsonl", events)

        # History file with MORE messages and sessions than parsed
        history = tmp_path / "history.jsonl"
        lines = []
        for i in range(10):
            lines.append(json.dumps({"session_id": "s%d" % (i % 3), "content": "msg"}))
        history.write_text("\n".join(lines) + "\n")

        start = datetime(2026, 3, 1, tzinfo=timezone.utc)
        end = datetime(2026, 3, 31, tzinfo=timezone.utc)
        ref = datetime(2026, 3, 15, tzinfo=timezone.utc)

        with patch("burnctl.collectors.codex.SESSIONS_DIR", str(sessions_dir)), \
             patch("burnctl.collectors.codex.os.path.isdir", return_value=True), \
             patch("burnctl.collectors.codex.HISTORY_FILE", str(history)):
            stats = CodexCollector().get_stats(start, end, ref)

        assert stats is not None
        # History has 10 messages, parsed only had 1 -> history wins
        assert stats["total_messages"] == 10
        # History has 3 unique sessions (s0, s1, s2), parsed had 1 -> history wins
        assert stats["total_sessions"] == 3
