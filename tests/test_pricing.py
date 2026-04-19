"""Tests for burnctl.pricing module.

Covers get_agent_pricing for every known agent ID, verifies the structure
of GEMINI_PRICING and OPENAI_PRICING, and ensures returned dicts are copies.
Python 3.8 compatible -- no walrus operator, no match/case.
"""

import json
from datetime import datetime
from unittest.mock import patch

from burnctl.pricing import (
    get_agent_pricing,
    get_agent_pricing_for_time,
    get_model_pricing_for_time,
    GEMINI_PRICING,
    OPENAI_PRICING,
)


# ---------------------------------------------------------------------------
# get_agent_pricing -- per-agent dispatch
# ---------------------------------------------------------------------------


class TestGetAgentPricingClaude:
    """get_agent_pricing('claude') with and without claude_usage installed."""

    def test_without_claude_usage(self):
        """When claude_usage is not installed, returns hardcoded fallback."""
        with patch.dict("sys.modules", {"claude_usage": None, "claude_usage.pricing": None}):
            result = get_agent_pricing("claude")

        assert result is not None
        assert isinstance(result, dict)
        assert "claude-opus-4-6" in result
        assert "claude-sonnet-4-6" in result
        assert "claude-opus-4-5" in result
        assert "claude-sonnet-4-5" in result
        assert "claude-haiku-4-5" in result

    def test_without_claude_usage_pricing_structure(self):
        """Each model entry should have input/output/cache_read/cache_create."""
        with patch.dict("sys.modules", {"claude_usage": None, "claude_usage.pricing": None}):
            result = get_agent_pricing("claude")

        for model, rates in result.items():
            assert "input" in rates, "{} missing 'input'".format(model)
            assert "output" in rates, "{} missing 'output'".format(model)
            assert "cache_read" in rates, "{} missing 'cache_read'".format(model)
            assert "cache_create" in rates, "{} missing 'cache_create'".format(model)

    def test_with_claude_usage_installed(self):
        """When claude_usage is installed, delegates to get_pricing()."""
        mock_pricing = {"mock-model": {"input": 99.0, "output": 99.0}}

        # Build a fake module with a get_pricing callable
        import types
        fake_module = types.ModuleType("claude_usage.pricing")
        fake_module.get_pricing = lambda: mock_pricing
        fake_parent = types.ModuleType("claude_usage")

        with patch.dict("sys.modules", {
            "claude_usage": fake_parent,
            "claude_usage.pricing": fake_module,
        }):
            result = get_agent_pricing("claude")

        assert result == mock_pricing


class TestGetAgentPricingGemini:
    def test_returns_gemini_pricing_copy(self):
        result = get_agent_pricing("gemini")
        assert result == GEMINI_PRICING

    def test_returns_copy_not_original(self):
        result = get_agent_pricing("gemini")
        assert result is not GEMINI_PRICING

    def test_mutation_does_not_affect_module(self):
        result = get_agent_pricing("gemini")
        result["mutated-model"] = {"input": 0}
        assert "mutated-model" not in GEMINI_PRICING


class TestGetAgentPricingCodex:
    def test_returns_openai_pricing_copy(self):
        result = get_agent_pricing("codex")
        assert result == OPENAI_PRICING

    def test_returns_copy_not_original(self):
        result = get_agent_pricing("codex")
        assert result is not OPENAI_PRICING

    def test_mutation_does_not_affect_module(self):
        result = get_agent_pricing("codex")
        result["mutated-model"] = {"input": 0}
        assert "mutated-model" not in OPENAI_PRICING


class TestHistoricalPricing:
    def test_records_snapshot_for_tracked_static_agents(self, tmp_path):
        history_file = tmp_path / "pricing-history.json"
        history_dir = tmp_path / "share"
        import burnctl.pricing as pricing_mod
        pricing_mod._OPENROUTER_PRICING_CACHE = None

        with patch("burnctl.pricing._PRICING_HISTORY_FILE", str(history_file)), \
             patch("burnctl.pricing._PRICING_HISTORY_DIR", str(history_dir)), \
             patch("burnctl.pricing._snapshot_now_iso", return_value="2026-04-17T12:00:00+00:00"):
            result = get_agent_pricing("gemini")

        assert result == GEMINI_PRICING
        payload = json.loads(history_file.read_text())
        assert payload["gemini"][0]["effective_from"] == "2026-04-17T12:00:00+00:00"
        assert payload["gemini"][0]["pricing"]["gemini-3-flash-preview"]["input"] == 0.50

    def test_resolves_snapshot_effective_at_timestamp(self, tmp_path):
        history_file = tmp_path / "pricing-history.json"
        history_file.write_text(json.dumps({
            "codex": [
                {
                    "effective_from": "2026-04-10T00:00:00+00:00",
                    "pricing": {"gpt-5.4": {"input": 3.0, "output": 18.0, "cache_read": 0.3}},
                },
                {
                    "effective_from": "2026-04-17T00:00:00+00:00",
                    "pricing": {"gpt-5.4": {"input": 2.5, "output": 15.0, "cache_read": 0.25}},
                },
            ],
        }))
        history_dir = tmp_path / "share"

        with patch("burnctl.pricing._PRICING_HISTORY_FILE", str(history_file)), \
             patch("burnctl.pricing._PRICING_HISTORY_DIR", str(history_dir)):
            before = get_model_pricing_for_time(
                "codex", "gpt-5.4",
                datetime.fromisoformat("2026-04-16T12:00:00+00:00"),
            )
            now = get_model_pricing_for_time(
                "codex", "gpt-5.4",
                datetime.fromisoformat("2026-04-17T12:00:00+00:00"),
            )

        assert before["input"] == 3.0
        assert now["input"] == 2.5


class TestGetAgentPricingLocal:
    def test_returns_empty_dict(self):
        result = get_agent_pricing("local")
        assert result == {}
        assert isinstance(result, dict)


class TestGetAgentPricingUnknown:
    def test_returns_empty_dict(self):
        result = get_agent_pricing("unknown_agent_xyz")
        assert result == {}
        assert isinstance(result, dict)


class TestGetAgentPricingOpenRouter:
    def test_returns_models_api_pricing(self):
        payload = {
            "data": [
                {
                    "id": "minimax/minimax-m2.7",
                    "pricing": {
                        "prompt": "0.0000003",
                        "completion": "0.0000012",
                        "internal_reasoning": "0.0000012",
                    },
                },
            ],
        }

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(payload).encode("utf-8")

        import burnctl.pricing as pricing_mod
        pricing_mod._OPENROUTER_PRICING_CACHE = None
        with patch("burnctl.pricing.urllib.request.urlopen", return_value=_Resp()):
            result = get_agent_pricing("openrouter")

        assert result["minimax/minimax-m2.7"]["input"] == 0.30
        assert result["minimax/minimax-m2.7"]["output"] == 1.20
        assert result["minimax/minimax-m2.7"]["reasoning"] == 1.20

    def test_returns_empty_dict_on_fetch_failure(self):
        import burnctl.pricing as pricing_mod
        pricing_mod._OPENROUTER_PRICING_CACHE = None
        pricing_mod._OPENROUTER_PRICING_CACHE_TS = 0
        with patch(
            "burnctl.pricing.urllib.request.urlopen",
            side_effect=OSError("offline"),
        ):
            result = get_agent_pricing("openrouter")

        assert result == {}

    def test_refreshes_stale_cache(self):
        payload = {
            "data": [
                {
                    "id": "minimax/minimax-m2.7",
                    "pricing": {
                        "prompt": "0.0000003",
                        "completion": "0.0000012",
                    },
                },
            ],
        }

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(payload).encode("utf-8")

        import burnctl.pricing as pricing_mod
        pricing_mod._OPENROUTER_PRICING_CACHE = {
            "minimax/minimax-m2.7": {"input": 0.10, "output": 0.20},
        }
        pricing_mod._OPENROUTER_PRICING_CACHE_TS = 0
        with patch("burnctl.pricing.time.time", return_value=10_000), \
             patch("burnctl.pricing.urllib.request.urlopen", return_value=_Resp()):
            result = get_agent_pricing("openrouter")

        assert result["minimax/minimax-m2.7"]["input"] == 0.30
        assert result["minimax/minimax-m2.7"]["output"] == 1.20

    def test_stale_cache_survives_refresh_failure(self):
        import burnctl.pricing as pricing_mod
        pricing_mod._OPENROUTER_PRICING_CACHE = {
            "minimax/minimax-m2.7": {"input": 0.30, "output": 1.20},
        }
        pricing_mod._OPENROUTER_PRICING_CACHE_TS = 0
        with patch("burnctl.pricing.time.time", return_value=10_000), \
             patch(
                 "burnctl.pricing.urllib.request.urlopen",
                 side_effect=OSError("offline"),
             ):
            result = get_agent_pricing("openrouter")

        assert result["minimax/minimax-m2.7"]["input"] == 0.30
        assert result["minimax/minimax-m2.7"]["output"] == 1.20


# ---------------------------------------------------------------------------
# GEMINI_PRICING structure
# ---------------------------------------------------------------------------


class TestGeminiPricingStructure:
    """Verify GEMINI_PRICING has expected models and rate keys."""

    def test_expected_models_present(self):
        expected_models = {
            "gemini-2.5-pro",
            "gemini-2.5-flash",
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
        }
        assert expected_models.issubset(set(GEMINI_PRICING.keys()))

    def test_each_model_has_rate_keys(self):
        for model, rates in GEMINI_PRICING.items():
            assert "input" in rates, "{} missing 'input'".format(model)
            assert "output" in rates, "{} missing 'output'".format(model)
            assert "cache_read" in rates, "{} missing 'cache_read'".format(model)

    def test_rates_are_positive_numbers(self):
        for model, rates in GEMINI_PRICING.items():
            for key in ("input", "output", "cache_read"):
                assert isinstance(rates[key], (int, float)), \
                    "{}.{} should be numeric".format(model, key)
                assert rates[key] > 0, \
                    "{}.{} should be positive".format(model, key)

    def test_pro_more_expensive_than_flash(self):
        pro = GEMINI_PRICING["gemini-2.5-pro"]
        flash = GEMINI_PRICING["gemini-2.5-flash"]
        assert pro["input"] > flash["input"]
        assert pro["output"] > flash["output"]


# ---------------------------------------------------------------------------
# OPENAI_PRICING structure
# ---------------------------------------------------------------------------


class TestOpenaiPricingStructure:
    """Verify OPENAI_PRICING has expected models and rate keys."""

    def test_expected_models_present(self):
        expected_models = {
            "gpt-5.4",
            "gpt-5.4-pro",
            "gpt-5.3-codex",
            "gpt-5.2-codex",
            "gpt-4o",
            "gpt-4o-mini",
            "o3",
            "o3-mini",
            "codex-mini",
        }
        assert expected_models.issubset(set(OPENAI_PRICING.keys()))

    def test_each_model_has_input_and_output(self):
        for model, rates in OPENAI_PRICING.items():
            assert "input" in rates, "{} missing 'input'".format(model)
            assert "output" in rates, "{} missing 'output'".format(model)

    def test_rates_are_positive_numbers(self):
        for model, rates in OPENAI_PRICING.items():
            for key in ("input", "output"):
                assert isinstance(rates[key], (int, float)), \
                    "{}.{} should be numeric".format(model, key)
                assert rates[key] > 0, \
                    "{}.{} should be positive".format(model, key)

    def test_pro_is_most_expensive(self):
        """gpt-5.4-pro should have the highest input rate."""
        pro_input = OPENAI_PRICING["gpt-5.4-pro"]["input"]
        for model, rates in OPENAI_PRICING.items():
            if model == "gpt-5.4-pro":
                continue
            assert pro_input >= rates["input"], \
                "gpt-5.4-pro input should be >= {} input".format(model)


# ---------------------------------------------------------------------------
# Returned dicts are copies (defensive copying)
# ---------------------------------------------------------------------------


class TestPricingCopySemantics:
    """Callers should receive copies so module-level dicts stay pristine."""

    def test_gemini_copy_independence(self):
        a = get_agent_pricing("gemini")
        b = get_agent_pricing("gemini")
        a["injected"] = True
        assert "injected" not in b
        assert "injected" not in GEMINI_PRICING

    def test_codex_copy_independence(self):
        a = get_agent_pricing("codex")
        b = get_agent_pricing("codex")
        a["injected"] = True
        assert "injected" not in b
        assert "injected" not in OPENAI_PRICING
