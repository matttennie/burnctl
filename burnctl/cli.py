"""Main CLI entry point for burnctl.

Provides a unified dashboard for AI coding agent usage across
Claude Code, Gemini CLI, and other supported agents.
"""

import argparse
import os
import re
import signal
import sys
import time
import webbrowser
from shlex import quote

from burnctl import __version__
from burnctl.collectors import ALL_COLLECTORS
from burnctl.config import DEFAULTS, THEMES

_COLLECTOR_MAP = {c.id: c for c in ALL_COLLECTORS}


# ── Argument parsing ────────────────────────────────────────────────

def _build_parser():
    """Construct and return the argument parser."""
    parser = argparse.ArgumentParser(
        prog="burnctl",
        description="Unified AI coding agent usage dashboard.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "metrics:\n"
            "  Est. API Cost   = sum of (input_tokens * input_rate\n"
            "                    + output_tokens * output_rate\n"
            "                    + cache_read_tokens * cache_read_rate\n"
            "                    + cache_create_tokens * cache_create_rate) / 1M\n"
            "  Value Ratio     = all-time API value / (plan_price * months_active)\n"
        ),
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"burnctl {__version__}",
    )

    # Agent selection flags (one per registered collector)
    agent_group = parser.add_argument_group("agent selection")
    for c in ALL_COLLECTORS:
        agent_group.add_argument(
            f"--{c.id}",
            action="store_true",
            help=f"Include {c.name}",
        )
    agent_group.add_argument(
        "--all",
        action="store_true",
        help="Include all detected agents",
    )

    # Output format
    fmt_group = parser.add_argument_group("output format")
    fmt_group.add_argument(
        "-j", "--json",
        action="store_true",
        help="Output raw JSON",
    )
    fmt_group.add_argument(
        "-c", "--compact",
        dest="compact",
        action="store_true",
        help="Single-line period summary",
    )
    fmt_group.add_argument(
        "--full",
        dest="compact",
        action="store_false",
        help="Disable compact mode for this run",
    )
    fmt_group.add_argument(
        "-s", "--simple",
        dest="simple",
        action="store_true",
        help="Show period usage only (hide all-time VALUE & ROI)",
    )
    fmt_group.add_argument(
        "--detailed",
        dest="simple",
        action="store_false",
        help="Re-enable the all-time VALUE & ROI section for this run",
    )
    fmt_group.add_argument(
        "--color",
        dest="color",
        action="store_true",
        help="Force ANSI colors on for this run",
    )
    fmt_group.add_argument(
        "-n", "--no-color",
        dest="color",
        action="store_false",
        help="Force ANSI colors off for this run",
    )
    fmt_group.add_argument(
        "-t", "--theme",
        choices=list(THEMES),
        help="Color theme (default: config value or gradient)",
    )
    fmt_group.add_argument(
        "-A", "--accessible",
        action="store_true",
        help="Plain text, screen-reader friendly output",
    )
    parser.set_defaults(compact=None, simple=None, color=None)

    # Billing
    billing_group = parser.add_argument_group("billing")
    billing_group.add_argument(
        "-p", "--plan",
        choices=["free", "pro", "max5x", "max20x"],
        help="Override Claude plan for this run",
    )
    billing_group.add_argument(
        "-i", "--interval",
        help="Billing interval (mo, yr)",
    )
    billing_group.add_argument(
        "-b", "--billing-day",
        type=int,
        metavar="DAY",
        help="Day of month billing period starts",
    )
    billing_group.add_argument(
        "-P", "--period",
        choices=["current", "last", "diff"],
        default="current",
        help="Which billing period to report (default: current). "
        "'diff' shows both periods side by side.",
    )
    billing_group.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        help="Start date for custom date range (overrides billing period)",
    )
    billing_group.add_argument(
        "--until",
        metavar="YYYY-MM-DD",
        help="End date for custom date range (default: today)",
    )

    # Other
    other_group = parser.add_argument_group("other")
    other_group.add_argument(
        "-e", "--export",
        metavar="FILE",
        nargs="?",
        const="burnctl.csv",
        help="Append data to CSV file (default: burnctl.csv)",
    )
    other_group.add_argument(
        "-w", "--watch",
        type=int,
        metavar="SECS",
        help="Refresh every N seconds (alias for --top-mode)",
    )
    other_group.add_argument(
        "--top-mode",
        type=int,
        nargs="?",
        const=10,
        metavar="SECS",
        help="Live dashboard with countdown (default: 10s refresh)",
    )

    # Subcommands
    sub = parser.add_subparsers(dest="command")

    # config
    cfg = sub.add_parser("config", help="View or set preferences")
    cfg.add_argument("key", nargs="?", help="Config key to set")
    cfg.add_argument("value", nargs="?", help="Value to set")

    # upgrade
    upg = sub.add_parser(
        "upgrade", help="Open billing/upgrade pages in browser",
    )
    upg.add_argument("agent", nargs="?", help="Agent to upgrade")
    upg.add_argument(
        "--all", dest="upgrade_all",
        action="store_true",
        help="Open upgrade pages for all agents",
    )

    proxy = sub.add_parser(
        "proxy", help="Run a local proxy for request-level instrumentation",
    )
    proxy.add_argument(
        "provider",
        choices=["openrouter"],
        help="Provider to proxy",
    )
    proxy.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host (default: 127.0.0.1)",
    )
    proxy.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Bind port (default: 8765)",
    )
    proxy.add_argument(
        "--ledger",
        help="Override request ledger path",
    )
    proxy.add_argument(
        "--print-shell",
        action="store_true",
        help="Print safe shell exports for OpenRouter-only proxying and exit",
    )
    proxy.add_argument(
        "--doctor",
        action="store_true",
        help="Check proxy-related environment safety and ledger location",
    )

    return parser


# ── Agent resolution ────────────────────────────────────────────────

def _resolve_collectors(args):
    """Determine which collectors to use based on CLI flags.

    Returns a list of collector instances, or prints an error and
    exits if none are available.
    """
    # Check if any per-agent flags were explicitly set
    explicit = [
        c for c in ALL_COLLECTORS if getattr(args, c.id, False)
    ]
    if explicit:
        selected = explicit
    else:
        # --all or no flags: use everything that's available
        selected = [c for c in ALL_COLLECTORS if c.is_available()]

    if not selected:
        print(
            "No agent data found. Ensure at least one agent "
            "(Claude Code, Gemini CLI, etc.) has been used on this system.",
            file=sys.stderr,
        )
        known = ", ".join(f"--{c.id}" for c in ALL_COLLECTORS) or "(none)"
        print(f"Available agent flags: {known}", file=sys.stderr)
        sys.exit(1)

    return selected


# ── Config merging ──────────────────────────────────────────────────

def _merge_config(args, config):
    """Merge CLI flags over the loaded config dict (in-place).

    CLI flags take precedence over config file values.
    """
    if args.plan:
        config["claude_plan"] = args.plan
    if args.interval:
        config["billing_interval"] = args.interval
    if args.billing_day is not None:
        if not 1 <= args.billing_day <= 31:
            print(
                "Error: --billing-day must be between 1 and 31.",
                file=sys.stderr,
            )
            sys.exit(1)
        config["billing_day"] = args.billing_day
    if args.theme:
        config["theme"] = args.theme
    if args.color is not None:
        config["no_color"] = not args.color
    if args.simple is not None:
        config["simple"] = args.simple
    if args.compact is not None:
        config["compact"] = args.compact
    return config


# ── Subcommand handlers ─────────────────────────────────────────────

def _handle_config(args):
    """Handle the ``config`` subcommand."""
    from burnctl.config import show, set_value

    if args.key is None:
        show()
    elif args.value is None:
        # Show a single key
        from burnctl.config import load
        cfg = load()
        if args.key in cfg:
            print(f"{args.key}: {cfg[args.key]}")
        else:
            valid = ", ".join(sorted(DEFAULTS.keys()))
            print(
                f"Unknown key '{args.key}'. Valid keys: {valid}",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        set_value(args.key, args.value)


def _handle_upgrade(args, collectors):
    """Handle the ``upgrade`` subcommand.

    When no agent is specified and ``--all`` is not set, opens the
    billing page for whichever agent is most over-pacing.
    """
    if args.agent:
        c = _COLLECTOR_MAP.get(args.agent)
        if c is None:
            known = ", ".join(sorted(_COLLECTOR_MAP.keys())) or "(none)"
            print(
                f"Unknown agent '{args.agent}'. Known: {known}",
                file=sys.stderr,
            )
            sys.exit(1)
        url = c.get_upgrade_url()
        if url:
            print(f"Opening {c.name} billing page: {url}")
            webbrowser.open(url)
        else:
            print(f"{c.name} has no upgrade URL.")
        return

    if args.upgrade_all:
        targets = [c for c in collectors if c.is_available()]
        if not targets:
            print("No agents available to upgrade.", file=sys.stderr)
            sys.exit(1)
        for c in targets:
            url = c.get_upgrade_url()
            if url:
                print(f"Opening {c.name}: {url}")
                webbrowser.open(url)
            else:
                print(f"{c.name}: no upgrade URL available.")
        return

    # No agent specified: find the most over-pacing agent
    from burnctl.config import load as load_config
    from burnctl.report import aggregate_stats

    config = load_config()
    available = [c for c in collectors if c.is_available()]
    if not available:
        print("No agents available to upgrade.", file=sys.stderr)
        sys.exit(1)

    agg = aggregate_stats(available, config)
    active = [a for a in agg["agents"] if not a.get("inactive")]
    if active:
        worst = max(active, key=lambda a: a["pace_pct"])
        c = _COLLECTOR_MAP.get(worst["id"])
        if c:
            url = c.get_upgrade_url()
            if url:
                pct = worst["pace_pct"]
                print(
                    f"Opening billing page for {worst['name']} "
                    f"({pct:.0f}% pacing): {url}",
                )
                webbrowser.open(url)
                return

    # Fallback: open all
    for c in available:
        url = c.get_upgrade_url()
        if url:
            print(f"Opening {c.name}: {url}")
            webbrowser.open(url)


def _handle_proxy(args):
    if args.provider != "openrouter":
        print("Only OpenRouter proxying is supported right now.", file=sys.stderr)
        sys.exit(1)
    if args.print_shell:
        print(_proxy_shell_exports(args.host, args.port))
        return
    if args.doctor:
        _proxy_doctor(args.host, args.port, args.ledger)
        return
    from burnctl.openrouter_proxy import run_proxy

    run_proxy(host=args.host, port=args.port, ledger_path=args.ledger)


def _proxy_shell_exports(host, port):
    """Return shell exports that only redirect OpenRouter-aware clients."""
    proxy_url = "http://%s:%s" % (host, port)
    return "\n".join([
        "# Route only OpenRouter-aware clients through the burnctl proxy.",
        "export OPENROUTER_BASE_URL=%s" % quote(proxy_url),
        "# Keep generic OpenAI-compatible clients direct unless you opt in explicitly.",
        "unset OPENAI_BASE_URL",
    ])


def _proxy_doctor(host, port, ledger_path):
    """Print current proxy environment state and common safety warnings."""
    from burnctl.openrouter_ledger import LEDGER_FILE

    proxy_url = "http://%s:%s" % (host, port)
    effective_ledger = ledger_path or os.environ.get("BURNCTL_OPENROUTER_LEDGER") or LEDGER_FILE
    openrouter_base = os.environ.get("OPENROUTER_BASE_URL") or "(unset)"
    openai_base = os.environ.get("OPENAI_BASE_URL") or "(unset)"

    print("OpenRouter proxy target: %s" % proxy_url)
    print("Ledger path: %s" % effective_ledger)
    print("OPENROUTER_BASE_URL: %s" % openrouter_base)
    print("OPENAI_BASE_URL: %s" % openai_base)

    if openrouter_base == proxy_url:
        print("Status: OpenRouter-aware clients are configured to use the proxy.")
    else:
        print(
            "Status: OpenRouter-aware clients are not yet pointing at the proxy."
        )

    if openai_base == proxy_url:
        print(
            "Warning: OPENAI_BASE_URL points at the burnctl proxy. "
            "This may redirect OpenAI-compatible clients you did not intend to proxy."
        )
    elif openai_base != "(unset)":
        print(
            "Warning: OPENAI_BASE_URL is set. Verify it is intentional and does not "
            "redirect unrelated OpenAI-compatible clients."
        )
    else:
        print(
            "Safety: OPENAI_BASE_URL is unset, so native OpenAI-compatible clients stay direct by default."
        )


# ── Report rendering ────────────────────────────────────────────────

def _render_report(args, config, collectors):
    """Run data collection and render the report."""
    from datetime import datetime as _dt

    from burnctl.report import (
        aggregate_stats,
        export_csv,
        render_accessible,
        render_compact,
        render_diff,
        render_full,
        render_json,
    )

    start_override = end_override = None
    since = getattr(args, "since", None)
    until = getattr(args, "until", None)
    if since:
        try:
            start_override = _dt.strptime(since, "%Y-%m-%d")
        except ValueError:
            print(
                "Error: --since must be YYYY-MM-DD.", file=sys.stderr,
            )
            sys.exit(1)
        if until:
            try:
                end_override = _dt.strptime(until, "%Y-%m-%d")
            except ValueError:
                print(
                    "Error: --until must be YYYY-MM-DD.",
                    file=sys.stderr,
                )
                sys.exit(1)

    # Period-over-period diff mode
    if args.period == "diff":
        cur = aggregate_stats(collectors, config, offset=0)
        prev = aggregate_stats(collectors, config, offset=-1)
        return render_diff(cur, prev)

    offset = -1 if args.period == "last" else 0
    agg = aggregate_stats(
        collectors, config, offset=offset,
        start_override=start_override,
        end_override=end_override,
    )

    if not agg["agents"]:
        print("No data available for the selected period.", file=sys.stderr)
        sys.exit(1)

    # Export if requested
    if args.export:
        export_csv(agg, filepath=args.export)

    # Choose output format
    if args.json:
        return render_json(agg)

    if args.accessible or config.get("theme") == "accessible":
        return render_accessible(agg)

    if args.compact or config.get("compact"):
        return render_compact(agg)

    use_color = not config.get("no_color", False)
    theme_name = config.get("theme", "gradient")
    simple = config.get("simple", False)
    return render_full(
        agg,
        simple=simple,
        use_color=use_color,
        theme=theme_name,
    )


# ── Entry point ─────────────────────────────────────────────────────

def main():
    """CLI entry point for ``burnctl``."""
    parser = _build_parser()
    args = parser.parse_args()

    # Subcommand: config
    if args.command == "config":
        _handle_config(args)
        return

    # Subcommand: upgrade
    if args.command == "upgrade":
        _handle_upgrade(args, ALL_COLLECTORS)
        return

    if args.command == "proxy":
        _handle_proxy(args)
        return

    # Default: report
    from burnctl.config import load as load_config

    config = load_config()
    config = _merge_config(args, config)

    collectors = _resolve_collectors(args)

    top_interval = getattr(args, "top_mode", None) or args.watch
    if top_interval:
        args.watch = top_interval  # normalize for _watch_loop
        _watch_loop(args, config, collectors)
    else:
        output = _render_report(args, config, collectors)
        print(output)


# Pattern matching the "Generated: YYYY-MM-DD" footer line (with or
# without ANSI wrapping).  Used by _watch_loop to stamp a live spinner.
_GENERATED_RE = re.compile(r"(Generated:\s*)\d{4}-\d{2}-\d{2}([ \t]*\S*)")

# Braille-dot spinner — smooth rotation, one frame per second.
_SPINNER = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"


def _stamp_spinner(report, tick, generated_at):
    """Replace the ``Generated:`` date with a fixed timestamp + spinner.

    *generated_at* is the time string captured when the report was last
    rendered.  Only the spinner changes each tick.

    Uses a callable replacement to avoid regex backreference injection
    if *generated_at* ever contains special characters.
    """
    dot = _SPINNER[tick % len(_SPINNER)]

    def _repl(m):
        return m.group(1) + generated_at + "   " + dot + m.group(2)

    return _GENERATED_RE.sub(_repl, report, count=1)


def _watch_loop(args, config, collectors):
    """Continuously re-render the report with a live spinner.

    Uses the alternate screen buffer and cursor-home rewriting so the
    display updates atomically — no visible blank gap between refreshes,
    similar to ``top`` or ``htop``.

    The ``Generated:`` footer shows the real data-refresh timestamp
    plus a spinning dot that ticks every second to show liveness.
    A monotonic clock keeps the 1-second tick accurate regardless of
    how long data collection or rendering takes.
    """
    from datetime import datetime as _dt

    interval = max(1, args.watch)
    use_alt_screen = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

    if use_alt_screen:
        sys.stdout.write("\033[?1049h\033[?25l")
        sys.stdout.flush()

    # Handle Ctrl-Z (SIGTSTP) gracefully: exit alt-screen before
    # suspending, re-enter on resume.  Without this the terminal is
    # left in a garbled state when the process is backgrounded.
    old_tstp = None
    old_cont = None
    if use_alt_screen and hasattr(signal, "SIGTSTP"):
        def _on_suspend(signum, frame):
            sys.stdout.write("\033[?25h\033[?1049l")
            sys.stdout.flush()
            signal.signal(signal.SIGTSTP, signal.SIG_DFL)
            os.kill(os.getpid(), signal.SIGTSTP)

        def _on_resume(signum, frame):
            sys.stdout.write("\033[?1049h\033[?25l")
            sys.stdout.flush()
            signal.signal(signal.SIGTSTP, _on_suspend)

        old_tstp = signal.signal(signal.SIGTSTP, _on_suspend)
        old_cont = signal.signal(signal.SIGCONT, _on_resume)

    try:
        cached_output = ""
        generated_at = ""
        remaining = 0
        tick = 0
        next_tick = time.monotonic()

        while True:
            if remaining <= 0:
                generated_at = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
                cached_output = _render_report(args, config, collectors)
                remaining = interval

            frame = _stamp_spinner(cached_output, tick, generated_at)

            if use_alt_screen:
                sys.stdout.write("\033[H")
                sys.stdout.write(frame)
                sys.stdout.write("\n\033[J")
                sys.stdout.flush()
            else:
                sys.stdout.write("\033[2J\033[H")
                sys.stdout.flush()
                print(frame)

            # Sleep only the remainder of the 1-second tick so render
            # time doesn't cause drift.
            next_tick += 1
            sleep_for = next_tick - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)

            remaining -= 1
            tick += 1
    except KeyboardInterrupt:
        pass
    finally:
        if use_alt_screen:
            sys.stdout.write("\033[?25h\033[?1049l")
            sys.stdout.flush()
        if old_tstp is not None:
            signal.signal(signal.SIGTSTP, old_tstp)
        if old_cont is not None:
            signal.signal(signal.SIGCONT, old_cont)
