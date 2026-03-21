"""Tests for burnctl.config module."""

import json
import os
from unittest.mock import patch

import pytest

from burnctl.config import (
    ANNUAL_PRICES,
    DEFAULTS,
    PLAN_PRICES,
    THEMES,
    _INTERVAL_ALIASES,
    effective_price,
    load,
    save,
    set_value,
    show,
)


# ── Constants ────────────────────────────────────────────────────────


class TestConstants:
    """Verify module-level constants are well-formed."""

    def test_defaults_has_expected_keys(self):
        expected = {
            "billing_day",
            "billing_interval",
            "default_agents",
            "theme",
            "no_color",
            "simple",
            "compact",
            "claude_plan",
            "gemini_plan",
            "codex_plan",
        }
        assert set(DEFAULTS.keys()) == expected

    def test_defaults_billing_day(self):
        assert DEFAULTS["billing_day"] == 10

    def test_defaults_claude_plan(self):
        assert DEFAULTS["claude_plan"] == "max5x"

    def test_defaults_theme(self):
        assert DEFAULTS["theme"] == "gradient"

    def test_defaults_no_color_is_false(self):
        assert DEFAULTS["no_color"] is False

    def test_defaults_simple_is_false(self):
        assert DEFAULTS["simple"] is False

    def test_defaults_compact_is_false(self):
        assert DEFAULTS["compact"] is False

    def test_themes_tuple(self):
        assert isinstance(THEMES, tuple)
        assert "gradient" in THEMES
        assert "classic" in THEMES
        assert "colorblind" in THEMES
        assert "accessible" in THEMES

    def test_plan_prices(self):
        assert PLAN_PRICES["free"] == 0
        assert PLAN_PRICES["pro"] == 20
        assert PLAN_PRICES["max5x"] == 100
        assert PLAN_PRICES["max20x"] == 200

    def test_annual_prices(self):
        assert ANNUAL_PRICES["pro"] == 200
        assert "free" not in ANNUAL_PRICES
        assert "max5x" not in ANNUAL_PRICES

    def test_interval_aliases(self):
        assert _INTERVAL_ALIASES["mo"] == "mo"
        assert _INTERVAL_ALIASES["month"] == "mo"
        assert _INTERVAL_ALIASES["monthly"] == "mo"
        assert _INTERVAL_ALIASES["yr"] == "yr"
        assert _INTERVAL_ALIASES["year"] == "yr"
        assert _INTERVAL_ALIASES["yearly"] == "yr"
        assert _INTERVAL_ALIASES["annual"] == "yr"
        assert _INTERVAL_ALIASES["annually"] == "yr"


# ── effective_price ──────────────────────────────────────────────────


class TestEffectivePrice:
    """Verify monthly effective price calculations."""

    def test_pro_monthly(self):
        result = effective_price("pro", "mo")
        assert result == 20

    def test_pro_annual(self):
        result = effective_price("pro", "yr")
        assert result == pytest.approx(200 / 12)

    def test_max5x_monthly(self):
        result = effective_price("max5x", "mo")
        assert result == 100

    def test_max5x_annual_falls_back_to_monthly(self):
        # max5x has no annual pricing
        result = effective_price("max5x", "yr")
        assert result == 100

    def test_max20x_monthly(self):
        result = effective_price("max20x", "mo")
        assert result == 200

    def test_max20x_annual_falls_back_to_monthly(self):
        result = effective_price("max20x", "yr")
        assert result == 200

    def test_free_monthly(self):
        result = effective_price("free", "mo")
        assert result == 0

    def test_free_annual(self):
        result = effective_price("free", "yr")
        assert result == 0

    def test_unknown_plan(self):
        result = effective_price("nonexistent_plan", "mo")
        assert result == 0

    def test_unknown_plan_annual(self):
        result = effective_price("nonexistent_plan", "yr")
        assert result == 0


# ── load ─────────────────────────────────────────────────────────────


class TestLoad:
    """Verify config loading logic."""

    def test_fresh_install_returns_defaults(self, tmp_path):
        fake_config = str(tmp_path / "config.json")
        with patch("burnctl.config.CONFIG_FILE", fake_config):
            config = load()
        assert config == DEFAULTS

    def test_saved_config_merges_over_defaults(self, tmp_path):
        config_file = tmp_path / "config.json"
        saved = {"billing_day": 25, "claude_plan": "pro"}
        config_file.write_text(json.dumps(saved))

        with patch("burnctl.config.CONFIG_FILE", str(config_file)):
            config = load()
        assert config["billing_day"] == 25
        assert config["claude_plan"] == "pro"
        # Defaults for unset keys should still be present
        assert config["theme"] == DEFAULTS["theme"]
        assert config["no_color"] == DEFAULTS["no_color"]

    def test_malformed_json_returns_defaults(self, tmp_path, capsys):
        config_file = tmp_path / "config.json"
        config_file.write_text("{not valid json!!!")

        with patch("burnctl.config.CONFIG_FILE", str(config_file)):
            config = load()
        assert config == DEFAULTS
        err = capsys.readouterr().err
        assert "Warning" in err
        assert "malformed" in err

    def test_missing_file_returns_defaults(self, tmp_path):
        fake_config = str(tmp_path / "nonexistent" / "config.json")
        with patch("burnctl.config.CONFIG_FILE", fake_config):
            config = load()
        assert config == DEFAULTS

    def test_returns_copy_of_defaults(self, tmp_path):
        """Mutating the returned config should not affect DEFAULTS."""
        fake_config = str(tmp_path / "config.json")
        with patch("burnctl.config.CONFIG_FILE", fake_config):
            config = load()
        config["billing_day"] = 999
        assert DEFAULTS["billing_day"] != 999

    def test_extra_keys_in_saved_config_preserved(self, tmp_path):
        config_file = tmp_path / "config.json"
        saved = {"custom_extra_key": "hello"}
        config_file.write_text(json.dumps(saved))

        with patch("burnctl.config.CONFIG_FILE", str(config_file)):
            config = load()
        assert config["custom_extra_key"] == "hello"

    def test_oserror_on_read_returns_defaults(self, tmp_path, capsys):
        """Lines 83-84: OSError reading config file -> warning, defaults."""
        config_file = tmp_path / "config.json"
        config_file.write_text("{}")  # file exists

        def bad_open(*args, **kwargs):
            raise OSError("disk error")

        with patch("burnctl.config.CONFIG_FILE", str(config_file)), \
             patch("burnctl.config.os.path.isfile", return_value=True), \
             patch("builtins.open", side_effect=bad_open):
            config = load()

        assert config == DEFAULTS
        err = capsys.readouterr().err
        assert "Warning: could not read config" in err


# ── save ─────────────────────────────────────────────────────────────


class TestSave:
    """Verify config persistence."""

    def test_creates_directory_and_writes(self, tmp_path):
        config_dir = str(tmp_path / "subdir" / "burnctl")
        config_file = os.path.join(config_dir, "config.json")

        with patch("burnctl.config.CONFIG_DIR", config_dir), \
             patch("burnctl.config.CONFIG_FILE", config_file):
            save({"billing_day": 20, "theme": "classic"})

        assert os.path.isdir(config_dir)
        assert os.path.isfile(config_file)

        with open(config_file) as f:
            data = json.load(f)
        assert data["billing_day"] == 20
        assert data["theme"] == "classic"

    def test_overwrites_existing(self, tmp_path):
        config_dir = str(tmp_path)
        config_file = str(tmp_path / "config.json")

        # Write initial
        with open(config_file, "w") as f:
            json.dump({"old_key": "old_value"}, f)

        with patch("burnctl.config.CONFIG_DIR", config_dir), \
             patch("burnctl.config.CONFIG_FILE", config_file):
            save({"billing_day": 5})

        with open(config_file) as f:
            data = json.load(f)
        assert data == {"billing_day": 5}
        assert "old_key" not in data

    def test_writes_valid_json(self, tmp_path):
        config_dir = str(tmp_path)
        config_file = str(tmp_path / "config.json")

        with patch("burnctl.config.CONFIG_DIR", config_dir), \
             patch("burnctl.config.CONFIG_FILE", config_file):
            save(DEFAULTS)

        with open(config_file) as f:
            content = f.read()
        # Should be indented JSON
        assert "  " in content
        # Should end with newline
        assert content.endswith("\n")
        # Should parse cleanly
        data = json.loads(content)
        assert data == DEFAULTS

    def test_os_error_exits(self, tmp_path):
        with patch("burnctl.config.CONFIG_DIR", "/nonexistent/path"), \
             patch("burnctl.config.CONFIG_FILE", "/nonexistent/path/config.json"), \
             patch("os.makedirs", side_effect=OSError("permission denied")):
            with pytest.raises(SystemExit) as exc_info:
                save({"billing_day": 10})
            assert exc_info.value.code == 1


# ── show ─────────────────────────────────────────────────────────────


class TestShow:
    """Verify config display output."""

    def test_shows_config_file_path(self, capsys, tmp_path):
        fake_config = str(tmp_path / "config.json")
        with patch("burnctl.config.CONFIG_FILE", fake_config), \
             patch("burnctl.config.load", return_value=dict(DEFAULTS)):
            show()
        out = capsys.readouterr().out
        assert fake_config in out

    def test_shows_all_keys(self, capsys):
        with patch("burnctl.config.load", return_value=dict(DEFAULTS)):
            show()
        out = capsys.readouterr().out
        for key in DEFAULTS:
            assert key in out

    def test_modified_marker(self, capsys):
        modified_config = dict(DEFAULTS)
        modified_config["billing_day"] = 25  # Different from default of 10

        with patch("burnctl.config.load", return_value=modified_config):
            show()
        out = capsys.readouterr().out
        assert "(modified)" in out

    def test_no_modified_marker_for_defaults(self, capsys):
        with patch("burnctl.config.load", return_value=dict(DEFAULTS)):
            show()
        out = capsys.readouterr().out
        assert "(modified)" not in out

    def test_shows_usage_instructions(self, capsys):
        with patch("burnctl.config.load", return_value=dict(DEFAULTS)):
            show()
        out = capsys.readouterr().out
        assert "burnctl config" in out


# ── set_value ────────────────────────────────────────────────────────


class TestSetValue:
    """Verify config value setting with validation and coercion."""

    def _patch_load_save(self, tmp_path, initial=None):
        """Return a context manager that patches load and save for set_value."""
        if initial is None:
            initial = dict(DEFAULTS)
        config_dir = str(tmp_path)
        config_file = str(tmp_path / "config.json")
        return (
            patch("burnctl.config.CONFIG_DIR", config_dir),
            patch("burnctl.config.CONFIG_FILE", config_file),
            patch("burnctl.config.load", return_value=initial),
            patch("burnctl.config.save") if not os.path.isdir(config_dir) else
            patch("burnctl.config.save"),
        )

    # ── Unknown key ──

    def test_unknown_key_exits(self):
        with pytest.raises(SystemExit) as exc_info:
            set_value("nonexistent_key", "value")
        assert exc_info.value.code == 1

    def test_unknown_key_error_message(self, capsys):
        with pytest.raises(SystemExit):
            set_value("nonexistent_key", "value")
        err = capsys.readouterr().err
        assert "unknown config key" in err
        assert "nonexistent_key" in err

    # ── billing_day validation ──

    def test_billing_day_valid(self, tmp_path, capsys):
        config_dir = str(tmp_path)
        config_file = str(tmp_path / "config.json")
        with patch("burnctl.config.CONFIG_DIR", config_dir), \
             patch("burnctl.config.CONFIG_FILE", config_file), \
             patch("burnctl.config.load", return_value=dict(DEFAULTS)), \
             patch("burnctl.config.save") as mock_save:
            set_value("billing_day", "15")
        mock_save.assert_called_once()
        saved_config = mock_save.call_args[0][0]
        assert saved_config["billing_day"] == 15
        out = capsys.readouterr().out
        assert "Set billing_day = 15" in out

    def test_billing_day_boundary_1(self, tmp_path):
        config_dir = str(tmp_path)
        config_file = str(tmp_path / "config.json")
        with patch("burnctl.config.CONFIG_DIR", config_dir), \
             patch("burnctl.config.CONFIG_FILE", config_file), \
             patch("burnctl.config.load", return_value=dict(DEFAULTS)), \
             patch("burnctl.config.save") as mock_save:
            set_value("billing_day", "1")
        saved_config = mock_save.call_args[0][0]
        assert saved_config["billing_day"] == 1

    def test_billing_day_boundary_31(self, tmp_path):
        config_dir = str(tmp_path)
        config_file = str(tmp_path / "config.json")
        with patch("burnctl.config.CONFIG_DIR", config_dir), \
             patch("burnctl.config.CONFIG_FILE", config_file), \
             patch("burnctl.config.load", return_value=dict(DEFAULTS)), \
             patch("burnctl.config.save") as mock_save:
            set_value("billing_day", "31")
        saved_config = mock_save.call_args[0][0]
        assert saved_config["billing_day"] == 31

    def test_billing_day_zero_exits(self):
        with patch("burnctl.config.load", return_value=dict(DEFAULTS)):
            with pytest.raises(SystemExit) as exc_info:
                set_value("billing_day", "0")
            assert exc_info.value.code == 1

    def test_billing_day_32_exits(self):
        with patch("burnctl.config.load", return_value=dict(DEFAULTS)):
            with pytest.raises(SystemExit) as exc_info:
                set_value("billing_day", "32")
            assert exc_info.value.code == 1

    def test_billing_day_not_integer_exits(self):
        with patch("burnctl.config.load", return_value=dict(DEFAULTS)):
            with pytest.raises(SystemExit) as exc_info:
                set_value("billing_day", "abc")
            assert exc_info.value.code == 1

    # ── claude_plan validation ──

    def test_plan_valid(self, tmp_path):
        config_dir = str(tmp_path)
        config_file = str(tmp_path / "config.json")
        with patch("burnctl.config.CONFIG_DIR", config_dir), \
             patch("burnctl.config.CONFIG_FILE", config_file), \
             patch("burnctl.config.load", return_value=dict(DEFAULTS)), \
             patch("burnctl.config.save") as mock_save:
            set_value("claude_plan", "pro")
        saved_config = mock_save.call_args[0][0]
        assert saved_config["claude_plan"] == "pro"

    def test_plan_invalid_exits(self):
        with patch("burnctl.config.load", return_value=dict(DEFAULTS)):
            with pytest.raises(SystemExit) as exc_info:
                set_value("claude_plan", "enterprise")
            assert exc_info.value.code == 1

    # ── billing_interval + alias normalization ──

    def test_interval_mo(self, tmp_path):
        config_dir = str(tmp_path)
        config_file = str(tmp_path / "config.json")
        with patch("burnctl.config.CONFIG_DIR", config_dir), \
             patch("burnctl.config.CONFIG_FILE", config_file), \
             patch("burnctl.config.load", return_value=dict(DEFAULTS)), \
             patch("burnctl.config.save") as mock_save:
            set_value("billing_interval", "mo")
        saved_config = mock_save.call_args[0][0]
        assert saved_config["billing_interval"] == "mo"

    def test_interval_alias_month(self, tmp_path):
        config_dir = str(tmp_path)
        config_file = str(tmp_path / "config.json")
        with patch("burnctl.config.CONFIG_DIR", config_dir), \
             patch("burnctl.config.CONFIG_FILE", config_file), \
             patch("burnctl.config.load", return_value=dict(DEFAULTS)), \
             patch("burnctl.config.save") as mock_save:
            set_value("billing_interval", "month")
        saved_config = mock_save.call_args[0][0]
        assert saved_config["billing_interval"] == "mo"

    def test_interval_alias_monthly(self, tmp_path):
        config_dir = str(tmp_path)
        config_file = str(tmp_path / "config.json")
        with patch("burnctl.config.CONFIG_DIR", config_dir), \
             patch("burnctl.config.CONFIG_FILE", config_file), \
             patch("burnctl.config.load", return_value=dict(DEFAULTS)), \
             patch("burnctl.config.save") as mock_save:
            set_value("billing_interval", "monthly")
        saved_config = mock_save.call_args[0][0]
        assert saved_config["billing_interval"] == "mo"

    def test_interval_alias_year(self, tmp_path):
        config_dir = str(tmp_path)
        config_file = str(tmp_path / "config.json")
        with patch("burnctl.config.CONFIG_DIR", config_dir), \
             patch("burnctl.config.CONFIG_FILE", config_file), \
             patch("burnctl.config.load", return_value=dict(DEFAULTS)), \
             patch("burnctl.config.save") as mock_save:
            set_value("billing_interval", "year")
        saved_config = mock_save.call_args[0][0]
        assert saved_config["billing_interval"] == "yr"

    def test_interval_alias_annual(self, tmp_path):
        config_dir = str(tmp_path)
        config_file = str(tmp_path / "config.json")
        with patch("burnctl.config.CONFIG_DIR", config_dir), \
             patch("burnctl.config.CONFIG_FILE", config_file), \
             patch("burnctl.config.load", return_value=dict(DEFAULTS)), \
             patch("burnctl.config.save") as mock_save:
            set_value("billing_interval", "annual")
        saved_config = mock_save.call_args[0][0]
        assert saved_config["billing_interval"] == "yr"

    def test_interval_alias_annually(self, tmp_path):
        config_dir = str(tmp_path)
        config_file = str(tmp_path / "config.json")
        with patch("burnctl.config.CONFIG_DIR", config_dir), \
             patch("burnctl.config.CONFIG_FILE", config_file), \
             patch("burnctl.config.load", return_value=dict(DEFAULTS)), \
             patch("burnctl.config.save") as mock_save:
            set_value("billing_interval", "annually")
        saved_config = mock_save.call_args[0][0]
        assert saved_config["billing_interval"] == "yr"

    def test_interval_invalid_exits(self):
        with patch("burnctl.config.load", return_value=dict(DEFAULTS)):
            with pytest.raises(SystemExit) as exc_info:
                set_value("billing_interval", "weekly")
            assert exc_info.value.code == 1

    def test_interval_yr_warns_no_annual_pricing(self, tmp_path, capsys):
        """Setting interval to yr with a non-pro plan should warn."""
        config_dir = str(tmp_path)
        config_file = str(tmp_path / "config.json")
        initial = dict(DEFAULTS)
        initial["claude_plan"] = "max5x"  # No annual pricing
        with patch("burnctl.config.CONFIG_DIR", config_dir), \
             patch("burnctl.config.CONFIG_FILE", config_file), \
             patch("burnctl.config.load", return_value=initial), \
             patch("burnctl.config.save"):
            set_value("billing_interval", "yr")
        err = capsys.readouterr().err
        assert "doesn't have annual pricing" in err

    # ── Bool coercion ──

    @pytest.mark.parametrize("input_val", ["true", "1", "yes"])
    def test_bool_coercion_true(self, input_val, tmp_path):
        config_dir = str(tmp_path)
        config_file = str(tmp_path / "config.json")
        with patch("burnctl.config.CONFIG_DIR", config_dir), \
             patch("burnctl.config.CONFIG_FILE", config_file), \
             patch("burnctl.config.load", return_value=dict(DEFAULTS)), \
             patch("burnctl.config.save") as mock_save:
            set_value("no_color", input_val)
        saved_config = mock_save.call_args[0][0]
        assert saved_config["no_color"] is True

    @pytest.mark.parametrize("input_val", ["false", "0", "no"])
    def test_bool_coercion_false(self, input_val, tmp_path):
        config_dir = str(tmp_path)
        config_file = str(tmp_path / "config.json")
        with patch("burnctl.config.CONFIG_DIR", config_dir), \
             patch("burnctl.config.CONFIG_FILE", config_file), \
             patch("burnctl.config.load", return_value=dict(DEFAULTS)), \
             patch("burnctl.config.save") as mock_save:
            set_value("no_color", input_val)
        saved_config = mock_save.call_args[0][0]
        assert saved_config["no_color"] is False

    def test_bool_invalid_exits(self):
        with patch("burnctl.config.load", return_value=dict(DEFAULTS)):
            with pytest.raises(SystemExit) as exc_info:
                set_value("no_color", "maybe")
            assert exc_info.value.code == 1

    def test_bool_invalid_error_message(self, capsys):
        with patch("burnctl.config.load", return_value=dict(DEFAULTS)):
            with pytest.raises(SystemExit):
                set_value("no_color", "maybe")
        err = capsys.readouterr().err
        assert "must be true/false" in err

    # ── Bool fields: simple, compact ──

    def test_simple_true(self, tmp_path):
        config_dir = str(tmp_path)
        config_file = str(tmp_path / "config.json")
        with patch("burnctl.config.CONFIG_DIR", config_dir), \
             patch("burnctl.config.CONFIG_FILE", config_file), \
             patch("burnctl.config.load", return_value=dict(DEFAULTS)), \
             patch("burnctl.config.save") as mock_save:
            set_value("simple", "yes")
        saved_config = mock_save.call_args[0][0]
        assert saved_config["simple"] is True

    def test_compact_true(self, tmp_path):
        config_dir = str(tmp_path)
        config_file = str(tmp_path / "config.json")
        with patch("burnctl.config.CONFIG_DIR", config_dir), \
             patch("burnctl.config.CONFIG_FILE", config_file), \
             patch("burnctl.config.load", return_value=dict(DEFAULTS)), \
             patch("burnctl.config.save") as mock_save:
            set_value("compact", "1")
        saved_config = mock_save.call_args[0][0]
        assert saved_config["compact"] is True

    # ── theme validation ──

    def test_theme_valid(self, tmp_path):
        config_dir = str(tmp_path)
        config_file = str(tmp_path / "config.json")
        with patch("burnctl.config.CONFIG_DIR", config_dir), \
             patch("burnctl.config.CONFIG_FILE", config_file), \
             patch("burnctl.config.load", return_value=dict(DEFAULTS)), \
             patch("burnctl.config.save") as mock_save:
            set_value("theme", "classic")
        saved_config = mock_save.call_args[0][0]
        assert saved_config["theme"] == "classic"

    def test_theme_invalid_exits(self):
        with patch("burnctl.config.load", return_value=dict(DEFAULTS)):
            with pytest.raises(SystemExit) as exc_info:
                set_value("theme", "neon")
            assert exc_info.value.code == 1

    # ── Plan + interval interaction warning ──

    def test_plan_change_warns_when_interval_yr(self, tmp_path, capsys):
        """Setting plan to non-pro when interval=yr should warn."""
        config_dir = str(tmp_path)
        config_file = str(tmp_path / "config.json")
        initial = dict(DEFAULTS)
        initial["billing_interval"] = "yr"
        with patch("burnctl.config.CONFIG_DIR", config_dir), \
             patch("burnctl.config.CONFIG_FILE", config_file), \
             patch("burnctl.config.load", return_value=initial), \
             patch("burnctl.config.save"):
            set_value("claude_plan", "max5x")
        err = capsys.readouterr().err
        assert "doesn't have annual pricing" in err

    def test_plan_change_no_warn_for_pro(self, tmp_path, capsys):
        """Setting plan to pro when interval=yr should NOT warn."""
        config_dir = str(tmp_path)
        config_file = str(tmp_path / "config.json")
        initial = dict(DEFAULTS)
        initial["billing_interval"] = "yr"
        with patch("burnctl.config.CONFIG_DIR", config_dir), \
             patch("burnctl.config.CONFIG_FILE", config_file), \
             patch("burnctl.config.load", return_value=initial), \
             patch("burnctl.config.save"):
            set_value("claude_plan", "pro")
        err = capsys.readouterr().err
        assert "doesn't have annual pricing" not in err

    # ── default_agents ──

    def test_default_agents_valid(self, tmp_path):
        config_dir = str(tmp_path)
        config_file = str(tmp_path / "config.json")
        with patch("burnctl.config.CONFIG_DIR", config_dir), \
             patch("burnctl.config.CONFIG_FILE", config_file), \
             patch("burnctl.config.load", return_value=dict(DEFAULTS)), \
             patch("burnctl.config.save") as mock_save:
            set_value("default_agents", "claude,gemini")
        saved_config = mock_save.call_args[0][0]
        assert saved_config["default_agents"] == "claude,gemini"

    # ── Int coercion for billing_day ──

    def test_int_coercion(self, tmp_path):
        config_dir = str(tmp_path)
        config_file = str(tmp_path / "config.json")
        with patch("burnctl.config.CONFIG_DIR", config_dir), \
             patch("burnctl.config.CONFIG_FILE", config_file), \
             patch("burnctl.config.load", return_value=dict(DEFAULTS)), \
             patch("burnctl.config.save") as mock_save:
            set_value("billing_day", "20")
        saved_config = mock_save.call_args[0][0]
        assert saved_config["billing_day"] == 20
        assert isinstance(saved_config["billing_day"], int)

    # ── Case insensitivity for bool ──

    def test_bool_uppercase_true(self, tmp_path):
        config_dir = str(tmp_path)
        config_file = str(tmp_path / "config.json")
        with patch("burnctl.config.CONFIG_DIR", config_dir), \
             patch("burnctl.config.CONFIG_FILE", config_file), \
             patch("burnctl.config.load", return_value=dict(DEFAULTS)), \
             patch("burnctl.config.save") as mock_save:
            set_value("no_color", "True")
        saved_config = mock_save.call_args[0][0]
        assert saved_config["no_color"] is True

    def test_bool_uppercase_false(self, tmp_path):
        config_dir = str(tmp_path)
        config_file = str(tmp_path / "config.json")
        with patch("burnctl.config.CONFIG_DIR", config_dir), \
             patch("burnctl.config.CONFIG_FILE", config_file), \
             patch("burnctl.config.load", return_value=dict(DEFAULTS)), \
             patch("burnctl.config.save") as mock_save:
            set_value("no_color", "False")
        saved_config = mock_save.call_args[0][0]
        assert saved_config["no_color"] is False

    def test_bool_yes_uppercase(self, tmp_path):
        config_dir = str(tmp_path)
        config_file = str(tmp_path / "config.json")
        with patch("burnctl.config.CONFIG_DIR", config_dir), \
             patch("burnctl.config.CONFIG_FILE", config_file), \
             patch("burnctl.config.load", return_value=dict(DEFAULTS)), \
             patch("burnctl.config.save") as mock_save:
            set_value("no_color", "YES")
        saved_config = mock_save.call_args[0][0]
        assert saved_config["no_color"] is True

    def test_bool_no_uppercase(self, tmp_path):
        config_dir = str(tmp_path)
        config_file = str(tmp_path / "config.json")
        with patch("burnctl.config.CONFIG_DIR", config_dir), \
             patch("burnctl.config.CONFIG_FILE", config_file), \
             patch("burnctl.config.load", return_value=dict(DEFAULTS)), \
             patch("burnctl.config.save") as mock_save:
            set_value("no_color", "NO")
        saved_config = mock_save.call_args[0][0]
        assert saved_config["no_color"] is False
