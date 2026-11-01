"""Desktop JSON contract and data-provenance tests (no live accounts)."""

from copy import deepcopy
from datetime import datetime
import json
from unittest.mock import patch

import pytest

from burnctl.collectors.base import BaseCollector
from burnctl.config import DEFAULTS
from burnctl.desktop import build_report, demo_report, main


class Collector(BaseCollector):
    id = "codex"
    name = "Test provider"

    def __init__(self, data=None, available=True, error=None):
        self.data = data
        self.available = available
        self.error = error

    def is_available(self):
        return self.available

    def get_stats(self, start, end, ref_date, live=False):
        self.bounds = (start, end)
        if self.error:
            raise self.error
        return self.data


def stats(**overrides):
    result = {
        "period_cost": 12.5, "alltime_cost": 20,
        "messages": 10, "sessions": 2,
        "input_tokens": None, "output_tokens": 50,
        "model_usage": {"model": {"outputTokens": 50}},
    }
    result.update(overrides)
    return result


def report(collectors, period="current"):
    return build_report(period, collectors, deepcopy(DEFAULTS), datetime(2026, 10, 15))


def test_current_and_previous_use_existing_billing_period_math():
    collector = Collector(stats())
    row = report([collector])["agents"][0]
    assert row["period_start"] == "2026-10-01"
    assert row["period_end"] == "2026-11-01"
    assert row["period_cost"] == 12.5
    assert row["input_tokens"] is None
    assert row["cost_basis"] == "estimated"
    row = report([collector], "previous")["agents"][0]
    assert row["period_start"] == "2026-09-01"
    assert row["period_end"] == "2026-10-01"


def test_one_failure_does_not_hide_healthy_provider_or_leak_exception():
    bad = Collector(error=RuntimeError("secret-token=/private/home"))
    good = Collector(stats())
    payload = report([bad, good])
    assert payload["agents"][0]["source_status"] == "error"
    assert payload["agents"][0]["period_cost"] is None
    assert payload["agents"][1]["source_status"] == "ok"
    assert "secret-token" not in json.dumps(payload)


@pytest.mark.parametrize("available", [True, False])
def test_missing_data_never_becomes_zero(available):
    row = report([Collector(available=available)])["agents"][0]
    assert row["source_status"] == "unavailable"
    assert row["period_cost"] is None
    assert row["messages"] is None


@pytest.mark.parametrize("agent_id", ["elevenlabs", "tavily", "openrouter"])
def test_previous_unsupported_apis_are_not_called(agent_id):
    collector = Collector(error=AssertionError("must not fetch"))
    collector.id = agent_id
    row = report([collector], "previous")["agents"][0]
    assert row["source_status"] == "unsupported"
    assert row["period_cost"] is None


def test_local_detection_is_not_usage():
    collector = Collector(stats())
    collector.id = "local"
    row = report([collector])["agents"][0]
    assert row["source_status"] == "unsupported"
    assert row["cost_basis"] == "unmetered"
    assert row["period_cost"] is None


@pytest.mark.parametrize("agent_id,unit,count", [("elevenlabs", "characters", 42), ("tavily", "credits", 10)])
def test_quota_units_are_not_dollars_or_tokens(agent_id, unit, count):
    collector = Collector(stats(model_usage={"characters": {"outputTokens": 42}}))
    collector.id = agent_id
    row = report([collector])["agents"][0]
    assert row["usage_unit"] == unit
    assert row["usage_count"] == count
    assert row["period_cost"] is None
    assert row["model_usage"] == {}
    if agent_id == "tavily":
        assert row["period_start"] is None
        assert row["messages"] is None


def test_genuine_zero_quota_is_not_missing():
    collector = Collector()
    collector.id = "elevenlabs"
    collector._sub = {"character_count": 0}
    row = report([collector])["agents"][0]
    assert row["source_status"] == "ok"
    assert row["usage_count"] == 0
    assert row["period_cost"] is None


def test_warnings_are_sanitized_but_incompleteness_is_visible():
    import sys

    class WarningCollector(Collector):
        def get_stats(self, *args, **kwargs):
            print("private bearer secret", file=sys.stderr)
            print("noisy optional library")
            return stats()

    payload = report([WarningCollector()])
    assert "incomplete" in payload["agents"][0]["status_message"]
    assert "secret" not in json.dumps(payload)


def test_demo_is_labeled_deterministic_and_has_optional_metrics():
    first = demo_report()
    assert first == demo_report()
    assert first["demo"] is True
    assert first["schema_version"] == 1
    assert first["agents"][0]["input_tokens"] is None
    assert demo_report("previous")["period"] == "previous"


def test_cli_outputs_single_json_document(capsys):
    main(["--demo", "--period", "previous"])
    captured = capsys.readouterr()
    assert json.loads(captured.out)["demo"] is True
    assert captured.err == ""


def test_config_is_read_only(tmp_path):
    with patch("burnctl.config.CONFIG_FILE", str(tmp_path / "absent.json")):
        payload = build_report(collectors=[])
    assert not list(tmp_path.iterdir())
    assert payload["notices"]


def test_nonfinite_values_cannot_break_swift_json_decoder():
    payload = report([Collector(stats(alltime_cost=float("nan")))])
    assert payload["agents"][0]["alltime_cost"] is None
    json.dumps(payload, allow_nan=False)


def test_huggingface_without_tokens_keeps_request_only_model_data():
    collector = Collector(stats(model_usage={"via provider": {"requests": 3, "cost": 1.5}}))
    collector.id = "huggingface"
    row = report([collector])["agents"][0]
    assert row["cost_basis"] == "reported"
    assert row["model_usage"] == {"via provider": {"requests": 3, "cost": 1.5}}


def test_desktop_does_not_record_pricing_history(tmp_path):
    from burnctl import pricing

    class PricingCollector(Collector):
        def get_stats(self, *args, **kwargs):
            pricing.get_agent_pricing("codex")
            return stats()

    destination = tmp_path / "pricing-history.json"
    with patch.object(pricing, "_PRICING_HISTORY_FILE", str(destination)), \
            patch.object(pricing, "_PRICING_HISTORY_DIR", str(tmp_path)):
        assert report([PricingCollector()])["agents"][0]["source_status"] == "ok"
    assert not destination.exists()
    assert pricing._HISTORY_READ_ONLY.get() is False


def test_malformed_config_does_not_break_reader(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("[")
    with patch("burnctl.config.CONFIG_FILE", str(path)):
        payload = build_report(collectors=[Collector(stats())])
    assert payload["notices"]
    assert payload["agents"][0]["source_status"] == "ok"
    assert path.read_text() == "["


@pytest.mark.parametrize("method", ["get_plan_info", "is_available"])
def test_provider_setup_failures_are_isolated(method):
    bad, good = Collector(stats()), Collector(stats())
    with patch.object(bad, method, side_effect=RuntimeError("secret")):
        payload = report([bad, good])
    assert [x["source_status"] for x in payload["agents"]] == ["error", "ok"]
    assert "secret" not in json.dumps(payload)


@pytest.mark.parametrize("account,status,count", [
    ({"plan_usage": 0, "paygo_usage": 0}, "ok", 0),
    (None, "unavailable", None),
    ({}, "unavailable", None),
])
def test_zero_and_failed_tavily_are_distinct(account, status, count):
    collector = Collector()
    collector.id = "tavily"
    collector._usage = account
    row = report([collector])["agents"][0]
    assert row["source_status"] == status
    assert row["usage_count"] == count
    assert row["period_cost"] is None


def test_pricing_readonly_context_restores_after_exception():
    from burnctl.pricing import _HISTORY_READ_ONLY, read_only_history
    with pytest.raises(RuntimeError):
        with read_only_history():
            assert _HISTORY_READ_ONLY.get() is True
            raise RuntimeError()
    assert _HISTORY_READ_ONLY.get() is False
