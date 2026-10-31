"""Tests for the Grok Build collector."""

import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from burnctl import pricing
from burnctl.collectors.grok import GrokCollector

FIXTURE = Path(__file__).parent / "fixtures" / "grok_home"
SESS = "sessions/%2FUsers%2Fme%2Fproj"
A = "aaaaaaaa-0000-4000-8000-000000000001"
B = "bbbbbbbb-0000-4000-8000-000000000002"


def _utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


PERIOD = (_utc(2026, 10, 15), _utc(2026, 10, 25))


@pytest.fixture
def home(tmp_path, monkeypatch):
    dst = tmp_path / "grok_home"
    shutil.copytree(FIXTURE, dst)
    monkeypatch.setenv("GROK_HOME", str(dst))
    # Keep pricing snapshots out of the real ~/.local/share.
    monkeypatch.setattr(pricing, "_PRICING_HISTORY_FILE", str(tmp_path / "hist.json"))
    return dst


def _stats(**kw):
    s, e = PERIOD
    return GrokCollector().get_stats(s, e, e, **kw)


def test_not_available(tmp_path, monkeypatch):
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "nope"))
    assert GrokCollector().is_available() is False
    assert _stats() is None


def test_available(home):
    assert GrokCollector().is_available() is True


def test_period_vs_alltime(home):
    r = _stats()
    # Period: A turn 2 (grok-4.3) + B turn 1 (grok-build).
    assert r["sessions"] == 2
    assert r["messages"] == 2
    assert r["tool_calls"] == 2
    assert r["daily_messages"] == {"2026-10-20": 1, "2026-10-21": 1}
    assert r["total_sessions"] == 2
    assert r["total_messages"] == 3
    assert r["first_session"] == "2026-10-10"
    assert r["last_active"] == "2026-10-21"
    # Narrow period containing only A turn 1.
    s = GrokCollector().get_stats(_utc(2026, 10, 1), _utc(2026, 10, 11), _utc(2026, 10, 11))
    assert s["sessions"] == 1 and s["messages"] == 1 and s["tool_calls"] == 1
    assert s["alltime_cost"] == r["alltime_cost"]


def test_cached_subtracted_and_models(home):
    r = _stats()
    assert r["input_tokens"] == 1_000_000 + 1_000_000  # (2M - 1M cached) + B 1M
    assert r["output_tokens"] == 600_000
    assert r["model_usage"]["grok-4.3"] == {"inputTokens": 1_000_000, "outputTokens": 100_000}
    # All-time includes turn 1: 1000 - 200 uncached.
    assert r["alltime_model_usage"]["grok-build-0.1"] == {"inputTokens": 800, "outputTokens": 100}


def test_table_math_and_fork_not_double_counted(home):
    # A t2: 1M*1.25 + 1M*0.20 + 0.1M*2.50 = 1.70; B t1: 1M*1.00 + 0.5M*2.00 = 2.00.
    # B's huge session totals / costUsdTicks must be ignored.
    assert _stats()["period_cost"] == pytest.approx(3.70)


def test_ticks_preferred_over_table(home):
    r = _stats()
    # A t1 is 50_000_000 ticks = $0.005 (the table would give ~$0.0012).
    assert r["alltime_cost"] - r["period_cost"] == pytest.approx(0.005)


def test_malformed_usage_skipped(home):
    bad = home / SESS / "cccccccc-0000-4000-8000-000000000003"
    bad.mkdir()
    (bad / "usage.json").write_text("{not json")
    r = _stats()
    assert r["total_sessions"] == 2
    assert r["period_cost"] == pytest.approx(3.70)


def test_missing_updates_falls_back_to_turns(home):
    # B has no updates.jsonl and still counts one message from its turn.
    assert not (home / SESS / B / "updates.jsonl").exists()
    assert _stats()["messages"] == 2
    (home / SESS / A / "updates.jsonl").unlink()
    r = _stats()
    assert r["messages"] == 2 and r["total_messages"] == 3 and r["tool_calls"] == 0


def test_live_matches_non_live(home):
    assert _stats(live=True) == _stats()


def test_grok_flag_in_parser():
    from burnctl import cli
    parser = cli._build_parser()
    assert parser.parse_args(["--grok"]).grok is True


def test_grok_build_alias_resolves_same_rates(tmp_path, monkeypatch):
    monkeypatch.setattr(pricing, "_PRICING_HISTORY_FILE", str(tmp_path / "hist.json"))
    a = pricing.get_model_pricing_for_time("grok", "grok-build")
    b = pricing.get_model_pricing_for_time("grok", "grok-build-0.1")
    assert a and a == b
