"""Tests for burnctl collector infrastructure."""

from datetime import datetime
from unittest.mock import patch

import pytest

from burnctl.collectors import ALL_COLLECTORS, get_collector, get_available
from burnctl.collectors.base import BaseCollector
from burnctl.collectors.aider import AiderCollector, _expand_suffix, _COST_RE
from burnctl.collectors.local import LocalCollector
from burnctl.collectors.stubs import ClineCollector, OpenCodeCollector, DebGPTCollector


# ── Instantiation & interface ────────────────────────────────────


class TestAllCollectorsInterface:
    """Every collector in the registry must satisfy the BaseCollector ABC."""

    def test_all_collectors_instantiated(self):
        assert len(ALL_COLLECTORS) > 0, "registry should not be empty"

    @pytest.mark.parametrize("collector", ALL_COLLECTORS, ids=lambda c: c.id)
    def test_has_name(self, collector):
        assert isinstance(collector.name, str) and len(collector.name) > 0

    @pytest.mark.parametrize("collector", ALL_COLLECTORS, ids=lambda c: c.id)
    def test_has_id(self, collector):
        assert isinstance(collector.id, str) and len(collector.id) > 0

    @pytest.mark.parametrize("collector", ALL_COLLECTORS, ids=lambda c: c.id)
    def test_is_subclass(self, collector):
        assert isinstance(collector, BaseCollector)

    @pytest.mark.parametrize("collector", ALL_COLLECTORS, ids=lambda c: c.id)
    def test_is_available_returns_bool(self, collector):
        result = collector.is_available()
        assert isinstance(result, bool)


# ── Stub collectors ──────────────────────────────────────────────


class TestStubCollectors:
    """Stubs should always be unavailable and return None stats."""

    @pytest.mark.parametrize("cls", [ClineCollector, OpenCodeCollector, DebGPTCollector])
    def test_is_available_returns_false(self, cls):
        assert cls().is_available() is False

    @pytest.mark.parametrize("cls", [ClineCollector, OpenCodeCollector, DebGPTCollector])
    def test_get_stats_returns_none(self, cls):
        now = datetime.now()
        assert cls().get_stats(now, now, now) is None


# ── Registry functions ───────────────────────────────────────────


class TestRegistry:
    def test_get_collector_known(self):
        c = get_collector("claude")
        assert c is not None
        assert c.id == "claude"

    def test_get_collector_unknown(self):
        assert get_collector("nonexistent_agent") is None

    def test_get_collector_all_ids_unique(self):
        ids = [c.id for c in ALL_COLLECTORS]
        assert len(ids) == len(set(ids)), "duplicate collector ids found"

    def test_get_available_returns_list(self):
        available = get_available()
        assert isinstance(available, list)
        for c in available:
            assert isinstance(c, BaseCollector)
            assert c.is_available() is True


# ── Aider token suffix parsing ───────────────────────────────────


class TestAiderTokenParsing:
    """Verify the k/M suffix expansion used by the Aider collector."""

    def test_plain_integer(self):
        assert _expand_suffix("500") == 500

    def test_plain_float(self):
        assert _expand_suffix("1.5") == 1

    def test_k_lowercase(self):
        assert _expand_suffix("1.5k") == 1500

    def test_k_uppercase(self):
        assert _expand_suffix("2K") == 2000

    def test_m_lowercase(self):
        assert _expand_suffix("2.1m") == 2_100_000

    def test_m_uppercase(self):
        assert _expand_suffix("1M") == 1_000_000

    def test_empty_string(self):
        assert _expand_suffix("") == 0

    def test_regex_matches_typical_line(self):
        line = "Tokens: 1.5k sent, 2.1k received. Cost: $0.03"
        m = _COST_RE.search(line)
        assert m is not None
        assert _expand_suffix(m.group(1)) == 1500
        assert _expand_suffix(m.group(2)) == 2100
        assert float(m.group(3)) == pytest.approx(0.03)

    def test_regex_matches_large_values(self):
        line = "Tokens: 2.1M sent, 500k received. Cost: $12.50"
        m = _COST_RE.search(line)
        assert m is not None
        assert _expand_suffix(m.group(1)) == 2_100_000
        assert _expand_suffix(m.group(2)) == 500_000
        assert float(m.group(3)) == pytest.approx(12.50)

    def test_regex_matches_plain_numbers(self):
        line = "Tokens: 500 sent, 200 received. Cost: $0.01"
        m = _COST_RE.search(line)
        assert m is not None
        assert _expand_suffix(m.group(1)) == 500
        assert _expand_suffix(m.group(2)) == 200


# ── Aider collector integration ──────────────────────────────────


class TestAiderCollector:
    def test_is_available_no_files(self, tmp_path):
        """With no history files, should be unavailable."""
        with patch("burnctl.collectors.aider._find_history_files", return_value=[]):
            collector = AiderCollector()
            assert collector.is_available() is False

    def test_get_stats_no_files(self):
        """With no history files, get_stats returns None."""
        with patch("burnctl.collectors.aider._find_history_files", return_value=[]):
            collector = AiderCollector()
            now = datetime.now()
            assert collector.get_stats(now, now, now) is None

    def test_get_stats_parses_file(self, tmp_path):
        """Create a temp history file and verify parsing."""
        history = tmp_path / ".aider.chat.history.md"
        history.write_text(
            "Some chat content\n"
            "Tokens: 1.5k sent, 2.1k received. Cost: $0.03\n"
            "More chat\n"
            "Tokens: 500 sent, 200 received. Cost: $0.01\n"
        )

        with patch("burnctl.collectors.aider._find_history_files", return_value=[str(history)]):
            collector = AiderCollector()
            start = datetime(2020, 1, 1)
            end = datetime(2030, 1, 1)
            stats = collector.get_stats(start, end, datetime.now())

        assert stats is not None
        assert stats["messages"] == 2
        assert stats["output_tokens"] == 2100 + 200
        assert stats["period_cost"] == pytest.approx(0.04)

    def test_plan_info(self):
        collector = AiderCollector()
        info = collector.get_plan_info({"billing_day": 15})
        assert info["plan_name"] == "pay-as-you-go"
        assert info["plan_price"] == 0
        assert info["billing_day"] == 15

    def test_upgrade_url(self):
        assert AiderCollector().get_upgrade_url() == "https://aider.chat/"


# ── Local collector ──────────────────────────────────────────────


class TestLocalCollector:
    def test_get_stats_returns_zeroed(self):
        collector = LocalCollector()
        now = datetime.now()
        stats = collector.get_stats(now, now, now)
        assert stats is not None
        assert stats["period_cost"] == 0.0
        assert stats["messages"] == 0
        assert stats["output_tokens"] == 0

    def test_plan_info(self):
        collector = LocalCollector()
        info = collector.get_plan_info({})
        assert info["plan_name"] == "local"
        assert info["plan_price"] == 0

    def test_upgrade_url(self):
        assert LocalCollector().get_upgrade_url() == "https://ollama.com/"
