"""Tests for burnctl.openrouter_setup."""

from unittest.mock import patch

from burnctl import openrouter_setup as setup


class TestOpenRouterSetup:
    def test_print_setup_shell_is_openrouter_only(self):
        text = setup.print_setup_shell()
        assert 'export OPENROUTER_BASE_URL="' in text
        assert "unset OPENAI_BASE_URL" in text

    def test_shell_hook_detection(self, tmp_path):
        rc = tmp_path / ".zshrc"
        rc.write_text("echo hi\n", encoding="utf-8")
        assert setup._shell_rc_has_hook(str(rc)) is False
        rc.write_text("echo hi\n%s\n" % setup.RC_BEGIN, encoding="utf-8")
        assert setup._shell_rc_has_hook(str(rc)) is True

    def test_ensure_shell_hook_is_idempotent(self, tmp_path):
        rc = tmp_path / "rc"
        setup._ensure_shell_hook(str(rc), "zsh")
        first = rc.read_text(encoding="utf-8")
        setup._ensure_shell_hook(str(rc), "zsh")
        second = rc.read_text(encoding="utf-8")
        assert first == second
        assert setup.RC_BEGIN in first

    def test_ensure_shell_hook_fish(self, tmp_path):
        rc = tmp_path / "burnctl.fish"
        setup._ensure_shell_hook(str(rc), "fish")
        text = rc.read_text(encoding="utf-8")
        assert "if test -f" in text
        assert setup.RC_BEGIN in text

    def test_ensure_shell_hook_bash(self, tmp_path):
        rc = tmp_path / ".bashrc"
        setup._ensure_shell_hook(str(rc), "bash")
        text = rc.read_text(encoding="utf-8")
        assert "[ -f" in text
        assert "source" in text

    def test_write_env_file(self, tmp_path):
        env_file = tmp_path / "openrouter-proxy.env"
        setup._write_env_file(str(env_file))
        text = env_file.read_text(encoding="utf-8")
        assert "OPENROUTER_BASE_URL" in text
        assert "unset OPENAI_BASE_URL" in text

    def test_write_launch_agent(self, tmp_path):
        plist_path = tmp_path / "agent.plist"
        with patch("burnctl.openrouter_setup.sys.executable", "/usr/bin/python3"):
            setup._write_launch_agent(str(plist_path))
        assert plist_path.exists()
        text = plist_path.read_bytes()
        assert b"io.burnctl.openrouter-proxy" in text
        assert b"/usr/bin/python3" in text
        assert b"proxy" in text

    def test_is_setup_complete_false_when_missing(self, monkeypatch):
        monkeypatch.setattr(setup, "setup_status", lambda: {
            "env_file_exists": False,
            "launch_agent_exists": False,
            "shell_rc_hooked": False,
        })
        assert setup.is_setup_complete() is False

    def test_is_setup_complete_true(self, monkeypatch):
        monkeypatch.setattr(setup, "setup_status", lambda: {
            "env_file_exists": True,
            "launch_agent_exists": True,
            "shell_rc_hooked": True,
        })
        assert setup.is_setup_complete() is True

    def test_detect_shell_zsh(self, monkeypatch):
        monkeypatch.setenv("SHELL", "/bin/zsh")
        assert setup._detect_shell() == "zsh"

    def test_detect_shell_bash(self, monkeypatch):
        monkeypatch.setenv("SHELL", "/bin/bash")
        assert setup._detect_shell() == "bash"

    def test_detect_shell_fish(self, monkeypatch):
        monkeypatch.setenv("SHELL", "/usr/bin/fish")
        assert setup._detect_shell() == "fish"

    def test_detect_shell_unknown(self, monkeypatch):
        monkeypatch.setenv("SHELL", "/bin/csh")
        assert setup._detect_shell() == ""

    def test_maybe_auto_setup_skips_without_key(self, monkeypatch):
        monkeypatch.setattr(setup.sys, "platform", "darwin")
        monkeypatch.setattr(setup, "_has_openrouter_key", lambda: False)
        changed, message = setup.maybe_auto_setup()
        assert changed is False
        assert message == ""

    def test_maybe_auto_setup_skips_unknown_shell(self, monkeypatch):
        monkeypatch.setattr(setup.sys, "platform", "darwin")
        monkeypatch.setattr(setup, "_has_openrouter_key", lambda: True)
        monkeypatch.setattr(setup, "is_setup_complete", lambda: False)
        monkeypatch.setattr(setup, "_is_interactive_tty", lambda: True)
        monkeypatch.setattr(setup, "_shell_rc_path", lambda: "")
        changed, message = setup.maybe_auto_setup()
        assert changed is False
        assert "Could not detect shell" in message

    def test_maybe_auto_setup_runs_when_needed(self, monkeypatch):
        monkeypatch.setattr(setup.sys, "platform", "darwin")
        monkeypatch.setattr(setup, "_has_openrouter_key", lambda: True)
        monkeypatch.setattr(setup, "is_setup_complete", lambda: False)
        monkeypatch.setattr(setup, "_is_interactive_tty", lambda: True)
        monkeypatch.setattr(setup, "_shell_rc_path", lambda: "/tmp/fake_rc")
        called = {"value": False}

        def _install():
            called["value"] = True

        monkeypatch.setattr(setup, "install", _install)
        changed, message = setup.maybe_auto_setup()
        assert changed is True
        assert called["value"] is True
        assert "Configured burnctl OpenRouter live tracking." in message
