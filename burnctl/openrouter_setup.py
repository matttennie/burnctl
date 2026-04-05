"""Automatic setup for the local OpenRouter proxy workflow."""

import os
import plistlib
import subprocess
import sys

from burnctl.config import CONFIG_DIR
from burnctl.openrouter_ledger import LEDGER_FILE

PROXY_HOST = "127.0.0.1"
PROXY_PORT = 8765
PROXY_URL = "http://%s:%s" % (PROXY_HOST, PROXY_PORT)
ENV_FILE = os.path.join(CONFIG_DIR, "openrouter-proxy.env")
LAUNCH_AGENT_LABEL = "io.burnctl.openrouter-proxy"
LAUNCH_AGENT_FILE = os.path.join(
    os.path.expanduser("~"),
    "Library",
    "LaunchAgents",
    LAUNCH_AGENT_LABEL + ".plist",
)

RC_BEGIN = "# >>> burnctl openrouter proxy >>>"
RC_END = "# <<< burnctl openrouter proxy <<<"

# Shell RC file paths keyed by shell name.
_SHELL_RC_MAP = {
    "zsh": os.path.join(os.path.expanduser("~"), ".zshrc"),
    "bash": os.path.join(os.path.expanduser("~"), ".bashrc"),
    "fish": os.path.join(
        os.path.expanduser("~"), ".config", "fish", "conf.d", "burnctl.fish",
    ),
}


def _detect_shell():
    """Return the short name of the user's login shell (zsh, bash, fish)."""
    shell_path = os.environ.get("SHELL", "")
    basename = os.path.basename(shell_path)
    if basename in _SHELL_RC_MAP:
        return basename
    return ""


def _shell_rc_path():
    """Return the RC file path for the detected shell, or empty string."""
    shell = _detect_shell()
    return _SHELL_RC_MAP.get(shell, "")


def _rc_block(shell=""):
    """Return the shell hook block appropriate for the detected shell."""
    if shell == "fish":
        return (
            RC_BEGIN + "\n"
            + "if test -f %s\n" % ENV_FILE
            + "    source %s\n" % ENV_FILE
            + "end\n"
            + RC_END + "\n"
        )
    # POSIX shells (bash, zsh)
    return (
        RC_BEGIN + "\n"
        + "[ -f %s ] && source %s\n" % (ENV_FILE, ENV_FILE)
        + RC_END + "\n"
    )


def setup_status():
    """Return installation/health state for the OpenRouter proxy setup."""
    shell = _detect_shell()
    rc_path = _shell_rc_path()
    return {
        "proxy_url": PROXY_URL,
        "ledger_file": LEDGER_FILE,
        "env_file": ENV_FILE,
        "launch_agent_file": LAUNCH_AGENT_FILE,
        "env_file_exists": os.path.isfile(ENV_FILE),
        "launch_agent_exists": os.path.isfile(LAUNCH_AGENT_FILE),
        "shell": shell or os.environ.get("SHELL", ""),
        "shell_rc": rc_path,
        "shell_rc_hooked": _shell_rc_has_hook(rc_path) if rc_path else False,
        "openrouter_base_url": os.environ.get("OPENROUTER_BASE_URL", ""),
        "openai_base_url": os.environ.get("OPENAI_BASE_URL", ""),
    }


def is_setup_complete():
    status = setup_status()
    return (
        status["env_file_exists"]
        and status["launch_agent_exists"]
        and status["shell_rc_hooked"]
    )


def maybe_auto_setup():
    """Install the OpenRouter proxy workflow when it is safe and relevant."""
    if sys.platform != "darwin":
        return False, ""
    if not _has_openrouter_key():
        return False, ""
    if is_setup_complete():
        return False, ""
    if not _is_interactive_tty():
        return False, ""
    rc_path = _shell_rc_path()
    if not rc_path:
        return False, (
            "Could not detect shell (SHELL=%s). "
            "Run `burnctl setup openrouter` manually." % os.environ.get("SHELL", "")
        )
    install()
    return True, (
        "Configured burnctl OpenRouter live tracking. "
        "New shells will route OpenRouter-aware clients through the local proxy "
        "without touching OPENAI_BASE_URL."
    )


def install():
    """Install shell wiring plus a LaunchAgent for the OpenRouter proxy."""
    rc_path = _shell_rc_path()
    if not rc_path:
        print(
            "Warning: unsupported shell (SHELL=%s). "
            "Skipping shell hook — set OPENROUTER_BASE_URL manually."
            % os.environ.get("SHELL", ""),
            file=sys.stderr,
        )
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    _write_env_file(ENV_FILE)
    if rc_path:
        _ensure_shell_hook(rc_path, _detect_shell())
    _write_launch_agent(LAUNCH_AGENT_FILE)
    _load_launch_agent(LAUNCH_AGENT_FILE)


def print_setup_shell():
    """Return the safe shell exports used by the automatic setup."""
    return "\n".join([
        "# Route only OpenRouter-aware clients through the burnctl proxy.",
        'export OPENROUTER_BASE_URL="%s"' % PROXY_URL,
        "# Keep generic OpenAI-compatible clients direct unless you opt in explicitly.",
        "unset OPENAI_BASE_URL",
    ])


def _has_openrouter_key():
    for name in (
        "OPENROUTER_MGMT_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENROUTER_ORCHARD_API_KEY",
    ):
        if os.environ.get(name, "").strip():
            return True
    return False


def _is_interactive_tty():
    return hasattr(sys.stderr, "isatty") and sys.stderr.isatty()


def _shell_rc_has_hook(path):
    if not path:
        return False
    try:
        with open(path, encoding="utf-8") as fh:
            return RC_BEGIN in fh.read()
    except OSError:
        return False


def _write_env_file(path):
    content = print_setup_shell() + "\n"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _ensure_shell_hook(path, shell=""):
    existing = ""
    try:
        with open(path, encoding="utf-8") as fh:
            existing = fh.read()
    except OSError:
        existing = ""
    if RC_BEGIN in existing:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        if existing and not existing.endswith("\n"):
            fh.write("\n")
        fh.write("\n" + _rc_block(shell))


def _write_launch_agent(path):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    payload = {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [
            sys.executable,
            "-m",
            "burnctl",
            "proxy",
            "openrouter",
            "--host",
            PROXY_HOST,
            "--port",
            str(PROXY_PORT),
        ],
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": os.path.join(CONFIG_DIR, "openrouter-proxy.stdout.log"),
        "StandardErrorPath": os.path.join(CONFIG_DIR, "openrouter-proxy.stderr.log"),
        "EnvironmentVariables": {
            "BURNCTL_OPENROUTER_LEDGER": LEDGER_FILE,
        },
    }
    with open(path, "wb") as fh:
        plistlib.dump(payload, fh)


def _load_launch_agent(path):
    commands = [
        ["launchctl", "unload", path],
        ["launchctl", "load", "-w", path],
    ]
    for cmd in commands:
        try:
            subprocess.run(
                cmd,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            return
