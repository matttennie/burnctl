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
            "messages", "sessions", "input_tokens", "output_tokens", "period_cost",
            "alltime_cost", "model_usage",
            "first_session", "total_messages", "total_sessions",
            "tool_calls",
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

    def test_period_cost_calculation(self):
        """period_cost uses effective rate from alltime data when available."""
        data = self._build_mock_data()
        start = datetime(2026, 3, 10)
        end = datetime(2026, 3, 13)

        pricing = ClaudeCollector._fallback_pricing()
        collector = ClaudeCollector()
        with patch.object(collector, "_load_data", return_value=data), \
             patch.object(collector, "_get_pricing_table", return_value=pricing):
            stats = collector.get_stats(start, end, datetime(2026, 3, 13))

        # Mar 10: claude-sonnet-4-6 => 5000 tokens at effective rate
        #   alltime cost for sonnet = (100k*1 + 50k*5 + 10k*0.1 + 5k*1.25)/1M = 0.35725
        #   effective rate = 0.35725 / 50000 * 1M = 7.145
        #   period cost = 5000 * 7.145 / 1M = 0.035725
        # Mar 12: claude-opus-4-6 => 2000 tokens at $25/M (no alltime data, fallback)
        #   period cost = 2000 * 25.0 / 1M = 0.05
        expected = 0.035725 + 0.05
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

    def test_env_var_overrides_config(self):
        with patch.dict(os.environ, {"CLAUDE_PLAN": "pro"}):
            info = ClaudeCollector().get_plan_info({"claude_plan": "max20x"})
        assert info["plan_name"] == "pro"
        assert info["plan_price"] == 20

    def test_env_var_invalid_falls_back_to_config(self):
        with patch.dict(os.environ, {"CLAUDE_PLAN": "bogus"}):
            info = ClaudeCollector().get_plan_info({"claude_plan": "max20x"})
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

    # ── Warning when using default plan without explicit config ──

    def test_warning_printed_when_default_and_config_file_missing(self, capsys):
        """Warning emitted when plan is default max5x and config file absent."""
        with patch("builtins.open", side_effect=OSError("no such file")):
            info = ClaudeCollector().get_plan_info({})
        assert info["plan_name"] == "max5x"
        err = capsys.readouterr().err
        assert "Warning" in err
        assert "burnctl config claude_plan" in err

    def test_warning_printed_when_default_and_config_file_has_no_claude_plan(
        self, tmp_path, capsys,
    ):
        """Warning emitted when config.json exists but lacks claude_plan key."""
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"billing_day": 15}))
        with patch("builtins.open", mock_open(read_data=cfg.read_text())):
            info = ClaudeCollector().get_plan_info({})
        assert info["plan_name"] == "max5x"
        err = capsys.readouterr().err
        assert "Warning" in err
        assert "burnctl config claude_plan" in err

    def test_no_warning_when_config_file_has_claude_plan(self, tmp_path, capsys):
        """No warning when config.json explicitly includes claude_plan."""
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"claude_plan": "max5x"}))
        with patch("builtins.open", mock_open(read_data=cfg.read_text())):
            info = ClaudeCollector().get_plan_info({"claude_plan": "max5x"})
        assert info["plan_name"] == "max5x"
        err = capsys.readouterr().err
        assert err == ""

    def test_no_warning_when_non_default_plan_from_config(self, capsys):
        """No warning when config provides a plan other than max5x."""
        info = ClaudeCollector().get_plan_info({"claude_plan": "pro"})
        assert info["plan_name"] == "pro"
        err = capsys.readouterr().err
        assert err == ""

    def test_no_warning_when_env_var_set(self, capsys):
        """No warning when CLAUDE_PLAN env var selects a valid plan."""
        with patch.dict(os.environ, {"CLAUDE_PLAN": "pro"}):
            info = ClaudeCollector().get_plan_info({})
        assert info["plan_name"] == "pro"
        err = capsys.readouterr().err
        assert err == ""

    def test_no_warning_when_claude_plan_set_flag(self, capsys):
        """No warning when _claude_plan_set sentinel is truthy in config."""
        info = ClaudeCollector().get_plan_info({
            "claude_plan": "max5x",
            "_claude_plan_set": True,
        })
        assert info["plan_name"] == "max5x"
        err = capsys.readouterr().err
        assert err == ""

    def test_warning_message_contains_set_command(self, capsys):
        """Warning text includes the burnctl config command for discoverability."""
        with patch("builtins.open", side_effect=OSError("no file")):
            ClaudeCollector().get_plan_info({})
        err = capsys.readouterr().err
        assert "burnctl config claude_plan" in err

    def test_warning_mentions_max5x_default(self, capsys):
        """Warning text mentions the defaulted plan name."""
        with patch("builtins.open", side_effect=OSError("no file")):
            ClaudeCollector().get_plan_info({})
        err = capsys.readouterr().err
        assert "max5x" in err

    def test_env_var_max5x_no_warning(self, capsys):
        """Even max5x via env var should suppress the warning."""
        with patch.dict(os.environ, {"CLAUDE_PLAN": "max5x"}):
            info = ClaudeCollector().get_plan_info({})
        assert info["plan_name"] == "max5x"
        err = capsys.readouterr().err
        assert err == ""

    def test_invalid_env_var_falls_back_to_default_with_warning(self, capsys):
        """Invalid CLAUDE_PLAN falls back to default max5x and warns."""
        with patch.dict(os.environ, {"CLAUDE_PLAN": "nope"}), \
             patch("builtins.open", side_effect=OSError("no file")):
            info = ClaudeCollector().get_plan_info({})
        assert info["plan_name"] == "max5x"
        err = capsys.readouterr().err
        assert "Warning" in err

    def test_corrupt_config_file_triggers_warning(self, capsys):
        """Corrupt config.json triggers warning (JSONDecodeError caught)."""
        with patch("builtins.open", mock_open(read_data="{{not json")):
            info = ClaudeCollector().get_plan_info({})
        assert info["plan_name"] == "max5x"
        err = capsys.readouterr().err
        assert "burnctl config claude_plan" in err


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


class TestClaudeScanSessionsAfter:
    """Verify _scan_sessions_after parses raw session JSONL files."""

    def _make_session_file(self, tmp_path, entries):
        """Write a fake session JSONL under a projects dir structure."""
        proj = tmp_path / "projects" / "test-project"
        proj.mkdir(parents=True)
        fpath = proj / "session-abc.jsonl"
        lines = [json.dumps(e) for e in entries]
        fpath.write_text("\n".join(lines) + "\n")
        return fpath

    def test_picks_up_iso_timestamps(self, tmp_path):
        entries = [
            {
                "type": "user",
                "timestamp": "2026-03-12T10:00:00.000Z",
                "sessionId": "s1",
                "message": {"role": "user", "content": "hi"},
            },
            {
                "type": "assistant",
                "timestamp": "2026-03-12T10:00:01.000Z",
                "sessionId": "s1",
                "message": {
                    "model": "claude-opus-4-6",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "hello"}],
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 50,
                        "cache_read_input_tokens": 100,
                        "cache_creation_input_tokens": 0,
                        "cache_creation": {"ephemeral_5m_input_tokens": 200},
                    },
                },
            },
        ]
        self._make_session_file(tmp_path, entries)

        with patch("burnctl.collectors.claude.PROJECTS_DIR", str(tmp_path / "projects")):
            act, tok, delta = ClaudeCollector._scan_sessions_after("2026-03-11", "2026-03-13")

        assert len(act) == 1
        assert act[0]["date"] == "2026-03-12"
        assert act[0]["messageCount"] == 1
        assert act[0]["sessionCount"] == 1

        assert len(tok) == 1
        assert tok[0]["tokensByModel"]["claude-opus-4-6"] == 50

        assert delta["claude-opus-4-6"]["outputTokens"] == 50
        assert delta["claude-opus-4-6"]["inputTokens"] == 10
        assert delta["claude-opus-4-6"]["cacheCreationInputTokens"] == 200

    def test_counts_tool_use_blocks(self, tmp_path):
        entries = [
            {
                "type": "assistant",
                "timestamp": "2026-03-12T10:00:00.000Z",
                "sessionId": "s1",
                "message": {
                    "model": "claude-opus-4-6",
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Read", "id": "t1", "input": {}},
                        {"type": "tool_use", "name": "Edit", "id": "t2", "input": {}},
                        {"type": "text", "text": "done"},
                    ],
                    "usage": {"input_tokens": 5, "output_tokens": 20},
                },
            },
        ]
        self._make_session_file(tmp_path, entries)

        with patch("burnctl.collectors.claude.PROJECTS_DIR", str(tmp_path / "projects")):
            act, _, _ = ClaudeCollector._scan_sessions_after("2026-03-11", "2026-03-13")

        assert act[0]["toolCallCount"] == 2

    def test_skips_entries_on_cutoff_date(self, tmp_path):
        entries = [
            {
                "type": "user",
                "timestamp": "2026-03-11T23:59:59.000Z",
                "sessionId": "s1",
                "message": {"role": "user", "content": "on cutoff day"},
            },
        ]
        self._make_session_file(tmp_path, entries)

        with patch("burnctl.collectors.claude.PROJECTS_DIR", str(tmp_path / "projects")):
            act, _, _ = ClaudeCollector._scan_sessions_after("2026-03-11", "2026-03-13")

        assert len(act) == 0

    def test_no_projects_dir(self):
        with patch("burnctl.collectors.claude.PROJECTS_DIR", "/nonexistent/path"):
            act, tok, delta = ClaudeCollector._scan_sessions_after("2026-03-11", "2026-03-13")
        assert act == []
        assert tok == []
        assert delta == {}

    def test_numeric_timestamps(self, tmp_path):
        """Epoch-ms timestamps (from history.jsonl format) also work."""
        # 2026-03-12 12:00:00 UTC in ms
        ts_ms = 1773331200000
        entries = [
            {
                "type": "user",
                "timestamp": ts_ms,
                "sessionId": "s2",
                "message": {"role": "user", "content": "epoch test"},
            },
        ]
        self._make_session_file(tmp_path, entries)

        with patch("burnctl.collectors.claude.PROJECTS_DIR", str(tmp_path / "projects")):
            act, _, _ = ClaudeCollector._scan_sessions_after("2026-03-11", "2026-03-13")

        assert len(act) == 1
        assert act[0]["messageCount"] == 1


class TestClaudeLoadDataGapFill:
    """Verify _load_data merges stale cache with live session data."""

    def test_stale_cache_triggers_scan(self):
        cache = {
            "lastComputedDate": "2026-03-11",
            "dailyActivity": [
                {"date": "2026-03-10", "messageCount": 10, "sessionCount": 1, "toolCallCount": 5},
            ],
            "dailyModelTokens": [],
            "modelUsage": {},
            "totalMessages": 100,
            "totalSessions": 10,
        }
        extra_act = [{"date": "2026-03-12", "messageCount": 20, "sessionCount": 3, "toolCallCount": 8}]
        extra_tok = [{"date": "2026-03-12", "tokensByModel": {"claude-opus-4-6": 5000}}]
        model_delta = {"claude-opus-4-6": {"outputTokens": 5000, "inputTokens": 100}}

        collector = ClaudeCollector()
        with patch("burnctl.collectors.claude.os.path.isfile", return_value=True), \
             patch("builtins.open", mock_open(read_data=json.dumps(cache))), \
             patch("burnctl.collectors.claude.datetime") as mock_dt, \
             patch.object(ClaudeCollector, "_scan_sessions_after",
                          return_value=(extra_act, extra_tok, model_delta)):
            mock_dt.now.return_value = datetime(2026, 3, 13)
            mock_dt.strptime = datetime.strptime
            result = collector._load_data()

        assert len(result["dailyActivity"]) == 2
        assert result["totalMessages"] == 120
        assert result["totalSessions"] == 13
        assert result["modelUsage"]["claude-opus-4-6"]["outputTokens"] == 5000

    def test_fresh_cache_skips_scan(self):
        cache = {"lastComputedDate": "2026-03-13", "totalMessages": 50}

        collector = ClaudeCollector()
        with patch("burnctl.collectors.claude.os.path.isfile", return_value=True), \
             patch("builtins.open", mock_open(read_data=json.dumps(cache))), \
             patch("burnctl.collectors.claude.datetime") as mock_dt, \
             patch.object(ClaudeCollector, "_scan_sessions_after") as mock_scan:
            mock_dt.now.return_value = datetime(2026, 3, 13)
            mock_dt.strptime = datetime.strptime
            result = collector._load_data()

        mock_scan.assert_not_called()
        assert result["totalMessages"] == 50


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
        assert mu["gemini-2.5-flash"]["inputTokens"] == 50 + 300  # non-cached only
        assert mu["gemini-2.5-flash"]["outputTokens"] == 200 + 400
        assert mu["gemini-2.5-flash"]["cachedTokens"] == 50

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
        assert info["plan_name"] == "none"
        assert info["plan_price"] == 0
        assert info["billing_day"] == 1

    def test_ai_pro(self):
        info = GeminiCollector().get_plan_info({"gemini_plan": "ai_pro"})
        assert info["plan_name"] == "ai_pro"
        assert info["plan_price"] == 25

    def test_ai_ultra(self):
        info = GeminiCollector().get_plan_info({"gemini_plan": "ai_ultra"})
        assert info["plan_name"] == "ai_ultra"
        assert info["plan_price"] == 250

    def test_unknown_plan_defaults_zero(self):
        info = GeminiCollector().get_plan_info({"gemini_plan": "bogus"})
        assert info["plan_price"] == 0

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
        assert p["input"] == 2.50
        assert p["output"] == 15.0
        assert p["cache_read"] == 0.25


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
            "messages", "sessions", "input_tokens", "output_tokens", "period_cost",
            "alltime_cost", "model_usage",
            "first_session", "total_messages", "total_sessions",
            "tool_calls",
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
        assert stats["model_usage"]["o3"]["inputTokens"] == 1900  # non-cached only
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
        assert info["plan_name"] == "none"
        assert info["plan_price"] == 0
        assert info["billing_day"] == 1

    def test_plus(self):
        info = CodexCollector().get_plan_info({"codex_plan": "plus"})
        assert info["plan_name"] == "plus"
        assert info["plan_price"] == 20

    def test_pro(self):
        info = CodexCollector().get_plan_info({"codex_plan": "pro"})
        assert info["plan_name"] == "pro"
        assert info["plan_price"] == 200

    def test_unknown_plan_defaults_zero(self):
        info = CodexCollector().get_plan_info({"codex_plan": "bogus"})
        assert info["plan_price"] == 0

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


# ---------------------------------------------------------------------------
# API usage collector
# ---------------------------------------------------------------------------

from burnctl.collectors.api_usage import (
    ApiUsageCollector,
    _parse_ts as _parse_ts_api,
    _parse_entry,
)


class TestApiUsageParseTs:
    """Verify _parse_ts() handles various timestamp formats."""

    def test_iso8601_with_z_suffix(self):
        result = _parse_ts_api("2026-03-17T14:30:00.000Z")
        assert result is not None
        assert result.year == 2026
        assert result.month == 3
        assert result.day == 17
        assert result.hour == 14
        assert result.minute == 30
        assert result.tzinfo is None  # naive after stripping

    def test_iso8601_with_offset(self):
        result = _parse_ts_api("2026-03-17T14:30:00+00:00")
        assert result is not None
        assert result.year == 2026
        assert result.month == 3
        assert result.day == 17
        assert result.tzinfo is None

    def test_returns_none_for_empty_string(self):
        assert _parse_ts_api("") is None

    def test_returns_none_for_none(self):
        assert _parse_ts_api(None) is None

    def test_returns_none_for_non_string(self):
        assert _parse_ts_api(12345) is None

    def test_returns_none_for_invalid_date_string(self):
        assert _parse_ts_api("not-a-date") is None


class TestApiUsageParseEntry:
    """Verify _parse_entry() validates and normalises JSONL lines."""

    def test_valid_complete_entry(self):
        line = json.dumps({
            "ts": "2026-03-17T14:30:00.000Z",
            "provider": "openrouter",
            "model_id": "anthropic/claude-opus-4-6",
            "model_name": "Claude Opus 4.6",
            "input_tokens": 1500,
            "output_tokens": 800,
            "cost": 0.024,
            "node_id": "node-abc",
            "estimated": False,
        })
        result = _parse_entry(line)
        assert result is not None
        assert result["provider"] == "openrouter"
        assert result["model_id"] == "anthropic/claude-opus-4-6"
        assert result["model_name"] == "Claude Opus 4.6"
        assert result["input_tokens"] == 1500
        assert result["output_tokens"] == 800
        assert result["cost"] == pytest.approx(0.024)
        assert result["node_id"] == "node-abc"
        assert result["estimated"] is False
        assert result["ts"].year == 2026

    def test_missing_timestamp_returns_none(self):
        line = json.dumps({
            "provider": "openrouter",
            "model_id": "anthropic/claude-opus-4-6",
        })
        assert _parse_entry(line) is None

    def test_missing_provider_returns_none(self):
        line = json.dumps({
            "ts": "2026-03-17T14:30:00.000Z",
            "model_id": "anthropic/claude-opus-4-6",
        })
        assert _parse_entry(line) is None

    def test_missing_model_id_returns_none(self):
        line = json.dumps({
            "ts": "2026-03-17T14:30:00.000Z",
            "provider": "openrouter",
        })
        assert _parse_entry(line) is None

    def test_empty_line_returns_none(self):
        assert _parse_entry("") is None
        assert _parse_entry("   ") is None

    def test_invalid_json_returns_none(self):
        assert _parse_entry("{not valid json}") is None

    def test_non_dict_json_returns_none(self):
        assert _parse_entry('"just a string"') is None
        assert _parse_entry("[1, 2, 3]") is None

    def test_estimated_defaults_to_false(self):
        line = json.dumps({
            "ts": "2026-03-17T14:30:00.000Z",
            "provider": "openrouter",
            "model_id": "anthropic/claude-opus-4-6",
        })
        result = _parse_entry(line)
        assert result is not None
        assert result["estimated"] is False

    def test_input_tokens_defaults_to_zero(self):
        line = json.dumps({
            "ts": "2026-03-17T14:30:00.000Z",
            "provider": "openrouter",
            "model_id": "anthropic/claude-opus-4-6",
        })
        result = _parse_entry(line)
        assert result is not None
        assert result["input_tokens"] == 0


class TestApiUsageCollectorAvailability:
    """is_available checks whether the usage JSONL file has matching entries."""

    def test_available_when_file_has_matching_provider(self, tmp_path):
        usage_file = tmp_path / "usage.jsonl"
        entry = json.dumps({
            "ts": "2026-03-17T14:30:00.000Z",
            "provider": "openrouter",
            "model_id": "anthropic/claude-opus-4-6",
        })
        usage_file.write_text(entry + "\n")
        collector = ApiUsageCollector("openrouter", "OpenRouter", usage_file=str(usage_file))
        assert collector.is_available() is True

    def test_unavailable_when_file_missing(self, tmp_path):
        usage_file = tmp_path / "nonexistent.jsonl"
        collector = ApiUsageCollector("openrouter", "OpenRouter", usage_file=str(usage_file))
        assert collector.is_available() is False


class TestApiUsageCollectorGetStats:
    """get_stats with controlled JSONL data via tmp_path."""

    @staticmethod
    def _make_entry(
        ts, provider="openrouter", model_id="anthropic/claude-opus-4-6",
        model_name="Claude Opus 4.6", input_tokens=1000, output_tokens=500,
        cost=0.01, node_id="node-1", estimated=False,
    ):
        return json.dumps({
            "ts": ts,
            "provider": provider,
            "model_id": model_id,
            "model_name": model_name,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost": cost,
            "node_id": node_id,
            "estimated": estimated,
        })

    def test_returns_none_when_file_missing(self, tmp_path):
        collector = ApiUsageCollector("openrouter", "OpenRouter", usage_file=str(tmp_path / "nope.jsonl"))
        stats = collector.get_stats(
            datetime(2026, 3, 10), datetime(2026, 4, 10), datetime(2026, 3, 13),
        )
        assert stats is None

    def test_returns_none_when_file_empty(self, tmp_path):
        usage_file = tmp_path / "usage.jsonl"
        usage_file.write_text("")
        collector = ApiUsageCollector("openrouter", "OpenRouter", usage_file=str(usage_file))
        stats = collector.get_stats(
            datetime(2026, 3, 10), datetime(2026, 4, 10), datetime(2026, 3, 13),
        )
        assert stats is None

    def test_period_filtering(self, tmp_path):
        """Only entries within [start, end) are counted in period stats."""
        lines = [
            self._make_entry("2026-03-11T10:00:00Z", cost=0.05),
            self._make_entry("2026-03-12T10:00:00Z", cost=0.03),
            # Outside period
            self._make_entry("2025-06-15T10:00:00Z", cost=0.10),
        ]
        usage_file = tmp_path / "usage.jsonl"
        usage_file.write_text("\n".join(lines) + "\n")

        start = datetime(2026, 3, 10)
        end = datetime(2026, 4, 10)
        ref_date = datetime(2026, 3, 13)

        collector = ApiUsageCollector("openrouter", "OpenRouter", usage_file=str(usage_file))
        stats = collector.get_stats(start, end, ref_date)

        assert stats is not None
        assert stats["messages"] == 2
        assert stats["period_cost"] == pytest.approx(0.08)

    def test_multiple_entries_models_nodes(self, tmp_path):
        """Multiple entries with different models and node IDs."""
        lines = [
            self._make_entry(
                "2026-03-11T10:00:00Z", model_id="model-a",
                model_name="Model A",
                input_tokens=1000, output_tokens=500, cost=0.02, node_id="n1",
            ),
            self._make_entry(
                "2026-03-11T14:00:00Z", model_id="model-b",
                model_name="Model B",
                input_tokens=2000, output_tokens=800, cost=0.05, node_id="n2",
            ),
            self._make_entry(
                "2026-03-12T10:00:00Z", model_id="model-a",
                model_name="Model A",
                input_tokens=500, output_tokens=300, cost=0.01, node_id="n3",
            ),
        ]
        usage_file = tmp_path / "usage.jsonl"
        usage_file.write_text("\n".join(lines) + "\n")

        start = datetime(2026, 3, 10)
        end = datetime(2026, 4, 10)
        ref_date = datetime(2026, 3, 13)

        collector = ApiUsageCollector("openrouter", "OpenRouter", usage_file=str(usage_file))
        stats = collector.get_stats(start, end, ref_date)

        assert stats is not None
        assert stats["messages"] == 3
        assert stats["sessions"] == 3  # 3 distinct node_ids
        assert stats["output_tokens"] == 500 + 800 + 300
        assert stats["period_cost"] == pytest.approx(0.08)

        # Model usage keyed by model_name
        assert "Model A" in stats["model_usage"]
        assert "Model B" in stats["model_usage"]
        assert stats["model_usage"]["Model A"]["inputTokens"] == 1500
        assert stats["model_usage"]["Model A"]["outputTokens"] == 800
        assert stats["model_usage"]["Model B"]["inputTokens"] == 2000
        assert stats["model_usage"]["Model B"]["outputTokens"] == 800

    def test_alltime_cost_includes_all_entries(self, tmp_path):
        """alltime_cost includes entries outside the billing period."""
        lines = [
            self._make_entry("2026-03-11T10:00:00Z", cost=0.05),
            self._make_entry("2025-06-15T10:00:00Z", cost=0.10),
        ]
        usage_file = tmp_path / "usage.jsonl"
        usage_file.write_text("\n".join(lines) + "\n")

        collector = ApiUsageCollector("openrouter", "OpenRouter", usage_file=str(usage_file))
        stats = collector.get_stats(
            datetime(2026, 3, 10), datetime(2026, 4, 10), datetime(2026, 3, 13),
        )

        assert stats is not None
        assert stats["alltime_cost"] == pytest.approx(0.15)
        assert stats["period_cost"] == pytest.approx(0.05)
        assert stats["alltime_cost"] > stats["period_cost"]

    def test_sessions_count_distinct_node_ids(self, tmp_path):
        """Sessions = number of distinct node_ids in the period."""
        lines = [
            self._make_entry("2026-03-11T10:00:00Z", node_id="n1"),
            self._make_entry("2026-03-11T11:00:00Z", node_id="n1"),
            self._make_entry("2026-03-11T12:00:00Z", node_id="n2"),
        ]
        usage_file = tmp_path / "usage.jsonl"
        usage_file.write_text("\n".join(lines) + "\n")

        collector = ApiUsageCollector("openrouter", "OpenRouter", usage_file=str(usage_file))
        stats = collector.get_stats(
            datetime(2026, 3, 10), datetime(2026, 4, 10), datetime(2026, 3, 13),
        )

        assert stats is not None
        assert stats["sessions"] == 2  # n1 and n2

    def test_first_session_date(self, tmp_path):
        """first_session is the earliest entry across all time."""
        lines = [
            self._make_entry("2026-03-11T10:00:00Z"),
            self._make_entry("2025-01-01T10:00:00Z"),
            self._make_entry("2026-03-12T10:00:00Z"),
        ]
        usage_file = tmp_path / "usage.jsonl"
        usage_file.write_text("\n".join(lines) + "\n")

        collector = ApiUsageCollector("openrouter", "OpenRouter", usage_file=str(usage_file))
        stats = collector.get_stats(
            datetime(2026, 3, 10), datetime(2026, 4, 10), datetime(2026, 3, 13),
        )

        assert stats is not None
        assert stats["first_session"] == "2025-01-01"

    def test_returns_all_expected_keys(self, tmp_path):
        """Stats dict contains all required keys."""
        lines = [
            self._make_entry("2026-03-11T10:00:00Z"),
        ]
        usage_file = tmp_path / "usage.jsonl"
        usage_file.write_text("\n".join(lines) + "\n")

        collector = ApiUsageCollector("openrouter", "OpenRouter", usage_file=str(usage_file))
        stats = collector.get_stats(
            datetime(2026, 3, 10), datetime(2026, 4, 10), datetime(2026, 3, 13),
        )

        expected_keys = {
            "messages", "sessions", "input_tokens", "output_tokens", "period_cost",
            "alltime_cost", "model_usage",
            "first_session", "total_messages", "total_sessions",
            "tool_calls",
        }
        assert set(stats.keys()) == expected_keys

    def test_total_messages_and_sessions(self, tmp_path):
        """total_messages and total_sessions cover all entries."""
        lines = [
            self._make_entry("2026-03-11T10:00:00Z", node_id="n1"),
            self._make_entry("2025-06-15T10:00:00Z", node_id="n2"),
            self._make_entry("2026-03-12T10:00:00Z", node_id="n1"),
        ]
        usage_file = tmp_path / "usage.jsonl"
        usage_file.write_text("\n".join(lines) + "\n")

        collector = ApiUsageCollector("openrouter", "OpenRouter", usage_file=str(usage_file))
        stats = collector.get_stats(
            datetime(2026, 3, 10), datetime(2026, 4, 10), datetime(2026, 3, 13),
        )

        assert stats is not None
        assert stats["total_messages"] == 3
        assert stats["total_sessions"] == 2  # n1 and n2


class TestApiUsageCollectorPlanInfo:
    """get_plan_info returns pay-as-you-go details."""

    def test_pay_as_you_go_defaults(self):
        info = ApiUsageCollector("openrouter", "OpenRouter").get_plan_info({})
        assert info["plan_name"] == "pay-as-you-go"
        assert info["plan_price"] == 0
        assert info["interval"] == "mo"
        assert info["billing_day"] == 1

    def test_billing_day_from_config(self):
        info = ApiUsageCollector("openrouter", "OpenRouter").get_plan_info({"billing_day": 15})
        assert info["billing_day"] == 15
