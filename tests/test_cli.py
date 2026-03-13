"""Tests for burnctl.cli module."""

import argparse
import sys
from unittest.mock import patch

import pytest

from burnctl.collectors.base import BaseCollector


# ── Helpers ──────────────────────────────────────────────────────────


class FakeCollector(BaseCollector):
    """Minimal collector for testing CLI logic."""

    def __init__(self, cid="fake", name="Fake Agent", available=True):
        self._id = cid
        self._name = name
        self._available = available

    @property
    def name(self):
        return self._name

    @property
    def id(self):
        return self._id

    def is_available(self):
        return self._available

    def get_stats(self, start, end, ref_date):
        return {
            "messages": 10,
            "sessions": 2,
            "output_tokens": 5000,
            "period_cost": 1.50,
            "alltime_cost": 10.00,
            "model_usage": {},
            "daily_messages": {},
            "first_session": None,
            "total_messages": 100,
            "total_sessions": 20,
            "tool_calls": {},
        }

    def get_upgrade_url(self):
        return "https://example.com/upgrade"


# ── _build_parser ────────────────────────────────────────────────────


class TestBuildParser:
    """Verify the argument parser has all expected arguments."""

    def _get_parser(self):
        from burnctl.cli import _build_parser

        return _build_parser()

    def test_returns_argparse_parser(self):
        parser = self._get_parser()
        assert isinstance(parser, argparse.ArgumentParser)

    def test_prog_name(self):
        parser = self._get_parser()
        assert parser.prog == "burnctl"

    @pytest.mark.parametrize(
        "flag",
        [
            "--claude",
            "--gemini",
            "--codex",
            "--aider",
            "--local",
            "--cline",
            "--opencode",
            "--debgpt",
            "--all",
        ],
    )
    def test_agent_selection_flags(self, flag):
        parser = self._get_parser()
        args = parser.parse_args([flag])
        # The flag should parse without error
        attr = flag.lstrip("-").replace("-", "_")
        assert getattr(args, attr) is True

    @pytest.mark.parametrize(
        "flag,attr",
        [
            (["-j"], "json"),
            (["--json"], "json"),
            (["-c"], "compact"),
            (["--compact"], "compact"),
            (["-s"], "simple"),
            (["--simple"], "simple"),
            (["-n"], "no_color"),
            (["--no-color"], "no_color"),
            (["-A"], "accessible"),
            (["--accessible"], "accessible"),
        ],
    )
    def test_output_format_flags(self, flag, attr):
        parser = self._get_parser()
        args = parser.parse_args(flag)
        assert getattr(args, attr) is True

    def test_theme_flag(self):
        parser = self._get_parser()
        args = parser.parse_args(["--theme", "classic"])
        assert args.theme == "classic"

    def test_theme_invalid_choice(self):
        parser = self._get_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--theme", "nonexistent"])

    @pytest.mark.parametrize(
        "flag,attr,value",
        [
            (["-p", "pro"], "plan", "pro"),
            (["--plan", "max5x"], "plan", "max5x"),
            (["-i", "yr"], "interval", "yr"),
            (["--interval", "mo"], "interval", "mo"),
            (["-b", "15"], "billing_day", 15),
            (["--billing-day", "1"], "billing_day", 1),
            (["-P", "last"], "period", "last"),
            (["--period", "current"], "period", "current"),
        ],
    )
    def test_billing_flags(self, flag, attr, value):
        parser = self._get_parser()
        args = parser.parse_args(flag)
        assert getattr(args, attr) == value

    def test_export_flag_default(self):
        parser = self._get_parser()
        args = parser.parse_args(["-e"])
        assert args.export == "burnctl.csv"

    def test_export_flag_custom(self):
        parser = self._get_parser()
        args = parser.parse_args(["-e", "output.csv"])
        assert args.export == "output.csv"

    def test_watch_flag(self):
        parser = self._get_parser()
        args = parser.parse_args(["-w", "5"])
        assert args.watch == 5

    def test_config_subcommand(self):
        parser = self._get_parser()
        args = parser.parse_args(["config"])
        assert args.command == "config"
        assert args.key is None
        assert args.value is None

    def test_config_subcommand_with_key(self):
        parser = self._get_parser()
        args = parser.parse_args(["config", "billing_day"])
        assert args.command == "config"
        assert args.key == "billing_day"
        assert args.value is None

    def test_config_subcommand_with_key_value(self):
        parser = self._get_parser()
        args = parser.parse_args(["config", "billing_day", "15"])
        assert args.command == "config"
        assert args.key == "billing_day"
        assert args.value == "15"

    def test_upgrade_subcommand(self):
        parser = self._get_parser()
        args = parser.parse_args(["upgrade"])
        assert args.command == "upgrade"

    def test_upgrade_subcommand_with_agent(self):
        parser = self._get_parser()
        args = parser.parse_args(["upgrade", "claude"])
        assert args.command == "upgrade"
        assert args.agent == "claude"

    def test_upgrade_subcommand_all(self):
        parser = self._get_parser()
        args = parser.parse_args(["upgrade", "--all"])
        assert args.command == "upgrade"
        assert args.upgrade_all is True

    def test_version_flag(self):
        parser = self._get_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--version"])
        assert exc_info.value.code == 0

    def test_defaults(self):
        parser = self._get_parser()
        args = parser.parse_args([])
        assert args.json is False
        assert args.compact is False
        assert args.simple is False
        assert args.no_color is False
        assert args.accessible is False
        assert args.plan is None
        assert args.interval is None
        assert args.billing_day is None
        assert args.period == "current"
        assert args.export is None
        assert args.watch is None
        assert args.command is None


# ── _resolve_collectors ──────────────────────────────────────────────


class TestResolveCollectors:
    """Verify agent resolution logic."""

    def test_explicit_flags(self):
        from burnctl.cli import _resolve_collectors

        c1 = FakeCollector(cid="alpha", available=True)
        c2 = FakeCollector(cid="beta", available=True)
        c3 = FakeCollector(cid="gamma", available=True)

        with patch("burnctl.cli.ALL_COLLECTORS", [c1, c2, c3]):
            args = argparse.Namespace(alpha=True, beta=False, gamma=False)
            result = _resolve_collectors(args)
        assert result == [c1]

    def test_explicit_multiple_flags(self):
        from burnctl.cli import _resolve_collectors

        c1 = FakeCollector(cid="alpha", available=True)
        c2 = FakeCollector(cid="beta", available=True)

        with patch("burnctl.cli.ALL_COLLECTORS", [c1, c2]):
            args = argparse.Namespace(alpha=True, beta=True)
            result = _resolve_collectors(args)
        assert result == [c1, c2]

    def test_all_flag_uses_available(self):
        """--all flag: no explicit per-agent flags, so falls through to auto-detect."""
        from burnctl.cli import _resolve_collectors

        c1 = FakeCollector(cid="alpha", available=True)
        c2 = FakeCollector(cid="beta", available=False)
        c3 = FakeCollector(cid="gamma", available=True)

        with patch("burnctl.cli.ALL_COLLECTORS", [c1, c2, c3]):
            args = argparse.Namespace(alpha=False, beta=False, gamma=False)
            result = _resolve_collectors(args)
        assert c1 in result
        assert c3 in result
        assert c2 not in result

    def test_no_flags_auto_detect(self):
        from burnctl.cli import _resolve_collectors

        c1 = FakeCollector(cid="alpha", available=False)
        c2 = FakeCollector(cid="beta", available=True)

        with patch("burnctl.cli.ALL_COLLECTORS", [c1, c2]):
            args = argparse.Namespace(alpha=False, beta=False)
            result = _resolve_collectors(args)
        assert result == [c2]

    def test_no_available_agents_exits(self):
        from burnctl.cli import _resolve_collectors

        c1 = FakeCollector(cid="alpha", available=False)
        c2 = FakeCollector(cid="beta", available=False)

        with patch("burnctl.cli.ALL_COLLECTORS", [c1, c2]):
            args = argparse.Namespace(alpha=False, beta=False)
            with pytest.raises(SystemExit) as exc_info:
                _resolve_collectors(args)
            assert exc_info.value.code == 1

    def test_no_available_agents_error_message(self, capsys):
        from burnctl.cli import _resolve_collectors

        c1 = FakeCollector(cid="alpha", available=False)

        with patch("burnctl.cli.ALL_COLLECTORS", [c1]):
            args = argparse.Namespace(alpha=False)
            with pytest.raises(SystemExit):
                _resolve_collectors(args)
        err = capsys.readouterr().err
        assert "No agent data found" in err
        assert "--alpha" in err


# ── _merge_config ────────────────────────────────────────────────────


class TestMergeConfig:
    """Verify CLI flags override config values."""

    def _make_args(self, **overrides):
        defaults = dict(
            plan=None,
            interval=None,
            billing_day=None,
            theme=None,
            no_color=False,
            simple=False,
            compact=False,
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def _base_config(self):
        return {
            "claude_plan": "pro",
            "billing_interval": "mo",
            "billing_day": 10,
            "theme": "gradient",
            "no_color": False,
            "simple": False,
            "compact": False,
        }

    def test_plan_override(self):
        from burnctl.cli import _merge_config

        args = self._make_args(plan="max20x")
        config = self._base_config()
        result = _merge_config(args, config)
        assert result["claude_plan"] == "max20x"

    def test_interval_override(self):
        from burnctl.cli import _merge_config

        args = self._make_args(interval="yr")
        config = self._base_config()
        result = _merge_config(args, config)
        assert result["billing_interval"] == "yr"

    def test_billing_day_override(self):
        from burnctl.cli import _merge_config

        args = self._make_args(billing_day=25)
        config = self._base_config()
        result = _merge_config(args, config)
        assert result["billing_day"] == 25

    def test_theme_override(self):
        from burnctl.cli import _merge_config

        args = self._make_args(theme="classic")
        config = self._base_config()
        result = _merge_config(args, config)
        assert result["theme"] == "classic"

    def test_no_color_override(self):
        from burnctl.cli import _merge_config

        args = self._make_args(no_color=True)
        config = self._base_config()
        result = _merge_config(args, config)
        assert result["no_color"] is True

    def test_simple_override(self):
        from burnctl.cli import _merge_config

        args = self._make_args(simple=True)
        config = self._base_config()
        result = _merge_config(args, config)
        assert result["simple"] is True

    def test_compact_override(self):
        from burnctl.cli import _merge_config

        args = self._make_args(compact=True)
        config = self._base_config()
        result = _merge_config(args, config)
        assert result["compact"] is True

    def test_unset_flags_do_not_override(self):
        from burnctl.cli import _merge_config

        args = self._make_args()
        config = self._base_config()
        original = dict(config)
        _merge_config(args, config)
        assert config == original

    def test_returns_config(self):
        from burnctl.cli import _merge_config

        args = self._make_args()
        config = self._base_config()
        result = _merge_config(args, config)
        assert result is config

    def test_billing_day_zero_exits(self):
        from burnctl.cli import _merge_config

        args = self._make_args(billing_day=0)
        config = self._base_config()
        with pytest.raises(SystemExit) as exc_info:
            _merge_config(args, config)
        assert exc_info.value.code == 1

    def test_billing_day_32_exits(self):
        from burnctl.cli import _merge_config

        args = self._make_args(billing_day=32)
        config = self._base_config()
        with pytest.raises(SystemExit) as exc_info:
            _merge_config(args, config)
        assert exc_info.value.code == 1

    def test_billing_day_negative_exits(self):
        from burnctl.cli import _merge_config

        args = self._make_args(billing_day=-1)
        config = self._base_config()
        with pytest.raises(SystemExit) as exc_info:
            _merge_config(args, config)
        assert exc_info.value.code == 1

    def test_billing_day_99_exits(self):
        from burnctl.cli import _merge_config

        args = self._make_args(billing_day=99)
        config = self._base_config()
        with pytest.raises(SystemExit) as exc_info:
            _merge_config(args, config)
        assert exc_info.value.code == 1

    def test_billing_day_error_message(self, capsys):
        from burnctl.cli import _merge_config

        args = self._make_args(billing_day=0)
        config = self._base_config()
        with pytest.raises(SystemExit):
            _merge_config(args, config)
        err = capsys.readouterr().err
        assert "Error: --billing-day must be between 1 and 31." in err

    def test_billing_day_boundary_1_ok(self):
        from burnctl.cli import _merge_config

        args = self._make_args(billing_day=1)
        config = self._base_config()
        result = _merge_config(args, config)
        assert result["billing_day"] == 1

    def test_billing_day_boundary_31_ok(self):
        from burnctl.cli import _merge_config

        args = self._make_args(billing_day=31)
        config = self._base_config()
        result = _merge_config(args, config)
        assert result["billing_day"] == 31


# ── _handle_config ───────────────────────────────────────────────────


class TestHandleConfig:
    """Verify the config subcommand handler."""

    def test_show_all_no_key(self):
        from burnctl.cli import _handle_config

        args = argparse.Namespace(key=None, value=None)
        with patch("burnctl.config.show") as mock_show:
            _handle_config(args)
        mock_show.assert_called_once()

    def test_show_single_key(self, capsys):
        from burnctl.cli import _handle_config

        args = argparse.Namespace(key="billing_day", value=None)
        fake_cfg = {"billing_day": 15, "claude_plan": "pro"}
        with patch("burnctl.config.load", return_value=fake_cfg):
            _handle_config(args)
        out = capsys.readouterr().out
        assert "billing_day: 15" in out

    def test_show_unknown_key_exits(self):
        from burnctl.cli import _handle_config

        args = argparse.Namespace(key="nonexistent_key", value=None)
        fake_cfg = {"billing_day": 15}
        with patch("burnctl.config.load", return_value=fake_cfg):
            with pytest.raises(SystemExit) as exc_info:
                _handle_config(args)
            assert exc_info.value.code == 1

    def test_show_unknown_key_error_message(self, capsys):
        from burnctl.cli import _handle_config

        args = argparse.Namespace(key="nonexistent_key", value=None)
        fake_cfg = {"billing_day": 15}
        with patch("burnctl.config.load", return_value=fake_cfg):
            with pytest.raises(SystemExit):
                _handle_config(args)
        err = capsys.readouterr().err
        assert "Unknown key" in err
        assert "nonexistent_key" in err

    def test_set_value(self):
        from burnctl.cli import _handle_config

        args = argparse.Namespace(key="billing_day", value="15")
        with patch("burnctl.config.set_value") as mock_set:
            _handle_config(args)
        mock_set.assert_called_once_with("billing_day", "15")


# ── _handle_upgrade ──────────────────────────────────────────────────


class TestHandleUpgrade:
    """Verify the upgrade subcommand handler."""

    def test_specific_agent(self, capsys):
        from burnctl.cli import _handle_upgrade

        c1 = FakeCollector(cid="alpha", name="Alpha Agent")
        collector_map = {"alpha": c1}

        with patch("burnctl.cli._COLLECTOR_MAP", collector_map), \
             patch("webbrowser.open") as mock_open:
            args = argparse.Namespace(agent="alpha", upgrade_all=False)
            _handle_upgrade(args, [c1])

        mock_open.assert_called_once_with("https://example.com/upgrade")
        out = capsys.readouterr().out
        assert "Alpha Agent" in out

    def test_unknown_agent_exits(self):
        from burnctl.cli import _handle_upgrade

        with patch("burnctl.cli._COLLECTOR_MAP", {}):
            args = argparse.Namespace(agent="unknown_thing", upgrade_all=False)
            with pytest.raises(SystemExit) as exc_info:
                _handle_upgrade(args, [])
            assert exc_info.value.code == 1

    def test_unknown_agent_error_message(self, capsys):
        from burnctl.cli import _handle_upgrade

        with patch("burnctl.cli._COLLECTOR_MAP", {"alpha": FakeCollector(cid="alpha")}):
            args = argparse.Namespace(agent="unknown_thing", upgrade_all=False)
            with pytest.raises(SystemExit):
                _handle_upgrade(args, [])
        err = capsys.readouterr().err
        assert "Unknown agent" in err
        assert "unknown_thing" in err

    def test_upgrade_all(self, capsys):
        from burnctl.cli import _handle_upgrade

        c1 = FakeCollector(cid="alpha", name="Alpha")
        c2 = FakeCollector(cid="beta", name="Beta")

        with patch("webbrowser.open") as mock_open:
            args = argparse.Namespace(agent=None, upgrade_all=True)
            _handle_upgrade(args, [c1, c2])
        assert mock_open.call_count == 2
        out = capsys.readouterr().out
        assert "Alpha" in out
        assert "Beta" in out

    def test_upgrade_no_agent_uses_available(self, capsys):
        from burnctl.cli import _handle_upgrade

        c1 = FakeCollector(cid="alpha", available=True)
        c2 = FakeCollector(cid="beta", available=False)

        with patch("webbrowser.open") as mock_open:
            args = argparse.Namespace(agent=None, upgrade_all=False)
            _handle_upgrade(args, [c1, c2])
        # Only the available collector should be opened
        mock_open.assert_called_once()

    def test_upgrade_no_agents_available_exits(self):
        from burnctl.cli import _handle_upgrade

        c1 = FakeCollector(cid="alpha", available=False)

        with patch("webbrowser.open"):
            args = argparse.Namespace(agent=None, upgrade_all=False)
            with pytest.raises(SystemExit) as exc_info:
                _handle_upgrade(args, [c1])
            assert exc_info.value.code == 1

    def test_agent_with_no_upgrade_url(self, capsys):
        from burnctl.cli import _handle_upgrade

        c1 = FakeCollector(cid="alpha", name="Alpha")
        # Override to return empty URL
        c1.get_upgrade_url = lambda: ""
        collector_map = {"alpha": c1}

        with patch("burnctl.cli._COLLECTOR_MAP", collector_map), \
             patch("webbrowser.open") as mock_open:
            args = argparse.Namespace(agent="alpha", upgrade_all=False)
            _handle_upgrade(args, [c1])
        mock_open.assert_not_called()
        out = capsys.readouterr().out
        assert "no upgrade URL" in out


# ── _render_report ───────────────────────────────────────────────────


class TestRenderReport:
    """Verify report rendering dispatch."""

    def _make_args(self, **overrides):
        defaults = dict(
            period="current",
            export=None,
            json=False,
            accessible=False,
            compact=False,
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def _make_agg(self):
        return {
            "agents": {"fake": {"messages": 10}},
            "totals": {"messages": 10},
        }

    def test_json_output(self):
        from burnctl.cli import _render_report

        args = self._make_args(json=True)
        config = {"no_color": False, "theme": "gradient", "simple": False, "compact": False}
        collectors = [FakeCollector()]

        with patch("burnctl.report.aggregate_stats", return_value=self._make_agg()), \
             patch("burnctl.report.render_json", return_value='{"data": 1}') as mock_json:
            result = _render_report(args, config, collectors)
        mock_json.assert_called_once()
        assert result == '{"data": 1}'

    def test_accessible_output_via_flag(self):
        from burnctl.cli import _render_report

        args = self._make_args(accessible=True)
        config = {"no_color": False, "theme": "gradient", "simple": False, "compact": False}
        collectors = [FakeCollector()]

        with patch("burnctl.report.aggregate_stats", return_value=self._make_agg()), \
             patch("burnctl.report.render_accessible", return_value="accessible text") as mock_acc:
            result = _render_report(args, config, collectors)
        mock_acc.assert_called_once()
        assert result == "accessible text"

    def test_accessible_output_via_theme(self):
        from burnctl.cli import _render_report

        args = self._make_args(accessible=False)
        config = {"no_color": False, "theme": "accessible", "simple": False, "compact": False}
        collectors = [FakeCollector()]

        with patch("burnctl.report.aggregate_stats", return_value=self._make_agg()), \
             patch("burnctl.report.render_accessible", return_value="accessible text") as mock_acc:
            _render_report(args, config, collectors)
        mock_acc.assert_called_once()

    def test_compact_output_via_flag(self):
        from burnctl.cli import _render_report

        args = self._make_args(compact=True)
        config = {"no_color": False, "theme": "gradient", "simple": False, "compact": False}
        collectors = [FakeCollector()]

        with patch("burnctl.report.aggregate_stats", return_value=self._make_agg()), \
             patch("burnctl.report.render_compact", return_value="compact line") as mock_cmp:
            result = _render_report(args, config, collectors)
        mock_cmp.assert_called_once()
        assert result == "compact line"

    def test_compact_output_via_config(self):
        from burnctl.cli import _render_report

        args = self._make_args(compact=False)
        config = {"no_color": False, "theme": "gradient", "simple": False, "compact": True}
        collectors = [FakeCollector()]

        with patch("burnctl.report.aggregate_stats", return_value=self._make_agg()), \
             patch("burnctl.report.render_compact", return_value="compact line") as mock_cmp:
            _render_report(args, config, collectors)
        mock_cmp.assert_called_once()

    def test_full_output(self):
        from burnctl.cli import _render_report

        args = self._make_args()
        config = {"no_color": False, "theme": "gradient", "simple": False, "compact": False}
        collectors = [FakeCollector()]

        with patch("burnctl.report.aggregate_stats", return_value=self._make_agg()), \
             patch("burnctl.report.render_full", return_value="full report") as mock_full:
            result = _render_report(args, config, collectors)
        mock_full.assert_called_once_with(
            self._make_agg(),
            simple=False,
            use_color=True,
            theme="gradient",
        )
        assert result == "full report"

    def test_full_output_no_color(self):
        from burnctl.cli import _render_report

        args = self._make_args()
        config = {"no_color": True, "theme": "classic", "simple": True, "compact": False}
        collectors = [FakeCollector()]

        with patch("burnctl.report.aggregate_stats", return_value=self._make_agg()), \
             patch("burnctl.report.render_full", return_value="full report") as mock_full:
            _render_report(args, config, collectors)
        mock_full.assert_called_once_with(
            self._make_agg(),
            simple=True,
            use_color=False,
            theme="classic",
        )

    def test_no_data_exits(self):
        from burnctl.cli import _render_report

        args = self._make_args()
        config = {"no_color": False, "theme": "gradient", "simple": False, "compact": False}
        collectors = [FakeCollector()]
        empty_agg = {"agents": {}, "totals": {}}

        with patch("burnctl.report.aggregate_stats", return_value=empty_agg):
            with pytest.raises(SystemExit) as exc_info:
                _render_report(args, config, collectors)
            assert exc_info.value.code == 1

    def test_export_csv_called(self):
        from burnctl.cli import _render_report

        args = self._make_args(export="out.csv", json=True)
        config = {"no_color": False, "theme": "gradient", "simple": False, "compact": False}
        collectors = [FakeCollector()]

        with patch("burnctl.report.aggregate_stats", return_value=self._make_agg()), \
             patch("burnctl.report.export_csv") as mock_export, \
             patch("burnctl.report.render_json", return_value="{}"):
            _render_report(args, config, collectors)
        mock_export.assert_called_once_with(self._make_agg(), filepath="out.csv")

    def test_last_period_offset(self):
        from burnctl.cli import _render_report

        args = self._make_args(period="last", json=True)
        config = {"no_color": False, "theme": "gradient", "simple": False, "compact": False}
        collectors = [FakeCollector()]

        with patch("burnctl.report.aggregate_stats", return_value=self._make_agg()) as mock_agg, \
             patch("burnctl.report.render_json", return_value="{}"):
            _render_report(args, config, collectors)
        # offset should be -1 for "last" period
        mock_agg.assert_called_once()
        _, kwargs = mock_agg.call_args
        assert kwargs["offset"] == -1


# ── main() ───────────────────────────────────────────────────────────


class TestMain:
    """Integration tests for the main() entry point."""

    def test_version_flag(self, capsys):
        from burnctl.cli import main
        from burnctl import __version__

        with patch("sys.argv", ["burnctl", "--version"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert __version__ in out

    def test_help_flag(self, capsys):
        from burnctl.cli import main

        with patch("sys.argv", ["burnctl", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "burnctl" in out

    def test_config_subcommand_dispatches(self):
        from burnctl.cli import main

        with patch("sys.argv", ["burnctl", "config"]), \
             patch("burnctl.cli._handle_config") as mock_handle:
            main()
        mock_handle.assert_called_once()

    def test_upgrade_subcommand_dispatches(self):
        from burnctl.cli import main

        with patch("sys.argv", ["burnctl", "upgrade"]), \
             patch("burnctl.cli._handle_upgrade") as mock_handle:
            main()
        mock_handle.assert_called_once()

    def test_report_with_mocked_collectors(self, capsys):
        from burnctl.cli import main

        fake_config = {
            "billing_day": 10,
            "billing_interval": "mo",
            "claude_plan": "pro",
            "theme": "gradient",
            "no_color": False,
            "simple": False,
            "compact": False,
        }
        c1 = FakeCollector(cid="alpha", available=True)

        with patch("sys.argv", ["burnctl"]), \
             patch("burnctl.config.load", return_value=fake_config), \
             patch("burnctl.cli._resolve_collectors", return_value=[c1]), \
             patch("burnctl.cli._render_report", return_value="report output"):
            main()
        out = capsys.readouterr().out
        assert "report output" in out

    def test_billing_day_zero_via_main(self, capsys):
        """End-to-end: -b 0 should exit with error."""
        from burnctl.cli import main

        fake_config = {
            "billing_day": 10,
            "billing_interval": "mo",
            "claude_plan": "pro",
            "theme": "gradient",
            "no_color": False,
            "simple": False,
            "compact": False,
        }
        c1 = FakeCollector(cid="alpha", available=True)

        with patch("sys.argv", ["burnctl", "-b", "0"]), \
             patch("burnctl.config.load", return_value=fake_config), \
             patch("burnctl.cli._resolve_collectors", return_value=[c1]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "Error: --billing-day must be between 1 and 31." in err

    def test_billing_day_99_via_main(self, capsys):
        """End-to-end: -b 99 should exit with error."""
        from burnctl.cli import main

        fake_config = {
            "billing_day": 10,
            "billing_interval": "mo",
            "claude_plan": "pro",
            "theme": "gradient",
            "no_color": False,
            "simple": False,
            "compact": False,
        }
        c1 = FakeCollector(cid="alpha", available=True)

        with patch("sys.argv", ["burnctl", "-b", "99"]), \
             patch("burnctl.config.load", return_value=fake_config), \
             patch("burnctl.cli._resolve_collectors", return_value=[c1]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "Error: --billing-day must be between 1 and 31." in err

    def test_watch_mode_dispatches(self):
        from burnctl.cli import main

        fake_config = {
            "billing_day": 10,
            "billing_interval": "mo",
            "claude_plan": "pro",
            "theme": "gradient",
            "no_color": False,
            "simple": False,
            "compact": False,
        }
        c1 = FakeCollector(cid="alpha", available=True)

        with patch("sys.argv", ["burnctl", "-w", "5"]), \
             patch("burnctl.config.load", return_value=fake_config), \
             patch("burnctl.cli._resolve_collectors", return_value=[c1]), \
             patch("burnctl.cli._watch_loop") as mock_watch:
            main()
        mock_watch.assert_called_once()


# ── __main__.py ──────────────────────────────────────────────────────


class TestDunderMain:
    """Verify burnctl/__main__.py invokes main()."""

    def test_main_is_called(self):
        import importlib
        with patch("burnctl.cli.main") as mock_main:
            # Force-remove the cached module so reload actually re-executes
            sys.modules.pop("burnctl.__main__", None)
            importlib.import_module("burnctl.__main__")
            mock_main.assert_called_once()


# ── _handle_upgrade: no upgrade URL in loop ──────────────────────────


class TestHandleUpgradeNoUrl:
    """Cover the 'no upgrade URL available' branch in the upgrade loop."""

    def test_upgrade_loop_no_url_prints_message(self, capsys):
        from burnctl.cli import _handle_upgrade

        c1 = FakeCollector(cid="alpha", name="Alpha")
        c1.get_upgrade_url = lambda: ""
        c2 = FakeCollector(cid="beta", name="Beta")
        # c2 has a normal URL

        with patch("webbrowser.open") as mock_open:
            args = argparse.Namespace(agent=None, upgrade_all=True)
            _handle_upgrade(args, [c1, c2])

        out = capsys.readouterr().out
        # Alpha has no URL
        assert "Alpha: no upgrade URL available." in out
        # Beta should have been opened
        assert "Beta" in out
        mock_open.assert_called_once_with("https://example.com/upgrade")


# ── _watch_loop ──────────────────────────────────────────────────────


class TestWatchLoop:
    """Cover the _watch_loop body (lines 343-353)."""

    def test_watch_loop_renders_and_clears(self):
        from burnctl.cli import _watch_loop

        args = argparse.Namespace(
            watch=2, period="current", export=None,
            json=False, accessible=False, compact=False,
        )
        config = {
            "no_color": False, "theme": "gradient",
            "simple": False, "compact": False,
        }
        collectors = [FakeCollector()]

        writes = []

        def capture_write(s):
            writes.append(s)
            return len(s)

        with patch("burnctl.cli._render_report", return_value="mock report"), \
             patch("time.sleep", side_effect=KeyboardInterrupt), \
             patch("sys.stdout.write", side_effect=capture_write), \
             patch("sys.stdout.flush"):
            _watch_loop(args, config, collectors)

        # Should have written the clear-screen sequence
        assert any("\033[2J\033[H" in w for w in writes)
