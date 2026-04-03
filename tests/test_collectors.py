"""Tests for burnctl collector infrastructure."""

from datetime import datetime
from unittest.mock import patch

import pytest

from burnctl.collectors import ALL_COLLECTORS, get_collector, get_available
from burnctl.collectors.base import BaseCollector
from burnctl.collectors.aider import (
    AiderCollector, _expand_suffix, _COST_RE, _find_history_files,
)
from burnctl.collectors.local import LocalCollector
from burnctl.collectors.stubs import ClineCollector, OpenCodeCollector


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

    @pytest.mark.parametrize("cls", [ClineCollector, OpenCodeCollector])
    def test_is_available_returns_false(self, cls):
        assert cls().is_available() is False

    @pytest.mark.parametrize("cls", [ClineCollector, OpenCodeCollector])
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


# ── Aider _find_history_files ────────────────────────────────────


class TestAiderFindHistoryFiles:
    """Cover lines 53, 63-65 of aider.py: directory walking with depth limit."""

    def test_finds_file_in_home_root(self, tmp_path):
        """Line 53: history file found directly in a search root."""
        # Create .aider.chat.history.md at the root of "home"
        hist = tmp_path / ".aider.chat.history.md"
        hist.write_text("cost line\n")

        with patch(
            "burnctl.collectors.aider.os.path.expanduser",
            return_value=str(tmp_path),
        ):
            result = _find_history_files()

        assert any(str(hist) in r for r in result)

    def test_finds_files_in_subdirectory_walk(self, tmp_path):
        """Lines 63-65: walk up to depth 2 under search roots."""
        # Create Desktop/project/.aider.chat.history.md
        desktop = tmp_path / "Desktop"
        project = desktop / "project"
        project.mkdir(parents=True)
        hist = project / ".aider.chat.history.md"
        hist.write_text("cost line\n")

        with patch(
            "burnctl.collectors.aider.os.path.expanduser",
            return_value=str(tmp_path),
        ):
            result = _find_history_files()

        assert str(hist) in result

    def test_depth_limit_skips_deep_files(self, tmp_path):
        """Walk should stop at depth 2, so depth-3 files are not found."""
        desktop = tmp_path / "Desktop"
        deep = desktop / "a" / "b" / "c"
        deep.mkdir(parents=True)
        hist = deep / ".aider.chat.history.md"
        hist.write_text("cost line\n")

        with patch(
            "burnctl.collectors.aider.os.path.expanduser",
            return_value=str(tmp_path),
        ):
            result = _find_history_files()

        assert str(hist) not in result

    def test_deduplicates_files(self, tmp_path):
        """Line 64: files found at root should not be re-added by walk."""
        # Create Desktop/.aider.chat.history.md -- found by root check AND walk
        desktop = tmp_path / "Desktop"
        desktop.mkdir()
        hist = desktop / ".aider.chat.history.md"
        hist.write_text("cost line\n")

        with patch(
            "burnctl.collectors.aider.os.path.expanduser",
            return_value=str(tmp_path),
        ):
            result = _find_history_files()

        # The file should appear exactly once
        assert result.count(str(hist)) == 1


# ── Aider get_stats error paths ──────────────────────────────────


class TestAiderGetStatsErrorPaths:
    """Cover lines 110-111, 115, 120-121, 134 in aider.py get_stats."""

    def test_getmtime_oserror_skips_file(self, tmp_path):
        """OSError on getmtime -> file counted for alltime but not period."""
        hist = tmp_path / ".aider.chat.history.md"
        hist.write_text("Tokens: 1k sent, 1k received. Cost: $0.05\n")

        with patch(
            "burnctl.collectors.aider._find_history_files",
            return_value=[str(hist)],
        ), patch(
            "burnctl.collectors.aider.os.path.getmtime",
            side_effect=OSError("permission denied"),
        ):
            collector = AiderCollector()
            stats = collector.get_stats(
                datetime(2020, 1, 1), datetime(2030, 1, 1), datetime.now(),
            )

        # File is still read for all-time cost, but mtime=0 means
        # it won't match the period (start_ts > 0)
        assert stats is not None
        assert stats["alltime_cost"] == 0.05
        assert stats["period_cost"] == 0.0

    def test_old_mtime_excludes_from_period_but_counts_alltime(self, tmp_path):
        """Old mtime -> file excluded from period but counted for alltime."""
        hist = tmp_path / ".aider.chat.history.md"
        hist.write_text("Tokens: 1k sent, 1k received. Cost: $0.05\n")

        # Set mtime to 2019 (before the billing period start of 2025)
        old_ts = datetime(2019, 1, 1).timestamp()

        with patch(
            "burnctl.collectors.aider._find_history_files",
            return_value=[str(hist)],
        ), patch(
            "burnctl.collectors.aider.os.path.getmtime",
            return_value=old_ts,
        ):
            collector = AiderCollector()
            start = datetime(2025, 1, 1)
            stats = collector.get_stats(
                start, datetime(2025, 2, 1), datetime(2025, 1, 15),
            )

        assert stats is not None
        assert stats["alltime_cost"] == 0.05
        assert stats["period_cost"] == 0.0
        assert stats["messages"] == 0  # no period messages

    def test_open_oserror_skips_file(self, tmp_path):
        """Lines 120-121: OSError on file read -> continue."""
        hist = tmp_path / ".aider.chat.history.md"
        hist.write_text("Tokens: 1k sent, 1k received. Cost: $0.05\n")

        def fake_open(path, **kwargs):
            raise OSError("cannot read")

        with patch(
            "burnctl.collectors.aider._find_history_files",
            return_value=[str(hist)],
        ), patch(
            "burnctl.collectors.aider.os.path.getmtime",
            return_value=datetime(2026, 1, 1).timestamp(),
        ), patch("builtins.open", side_effect=fake_open):
            collector = AiderCollector()
            stats = collector.get_stats(
                datetime(2020, 1, 1), datetime(2030, 1, 1), datetime.now(),
            )

        assert stats is None

    def test_no_cost_lines_returns_none(self, tmp_path):
        """Line 134: match_count == 0 returns None."""
        hist = tmp_path / ".aider.chat.history.md"
        hist.write_text("Just some chat text\nNo cost lines here\n")

        with patch(
            "burnctl.collectors.aider._find_history_files",
            return_value=[str(hist)],
        ):
            collector = AiderCollector()
            stats = collector.get_stats(
                datetime(2020, 1, 1), datetime(2030, 1, 1), datetime.now(),
            )

        assert stats is None


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
