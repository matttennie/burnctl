"""Tests for burnctl collector infrastructure."""

from datetime import datetime
from unittest.mock import patch

import pytest

from burnctl.collectors import ALL_COLLECTORS, get_collector, get_available
from burnctl.collectors.base import BaseCollector
from burnctl.collectors.local import LocalCollector
from burnctl.collectors.stubs import OpenCodeCollector


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

    @pytest.mark.parametrize("cls", [OpenCodeCollector])
    def test_is_available_returns_false(self, cls):
        assert cls().is_available() is False

    @pytest.mark.parametrize("cls", [OpenCodeCollector])
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
