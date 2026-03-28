"""Main CLI entry point for burnctl.

Provides a unified dashboard for AI coding agent usage across
Claude Code, Gemini CLI, and other supported agents.
"""

import argparse
import sys
import time
import webbrowser

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
        action="store_true",
        help="Single-line compact output",
    )
    fmt_group.add_argument(
        "-s", "--simple",
        action="store_true",
        help="Skip VALUE & ROI section",
    )
    fmt_group.add_argument(
        "-n", "--no-color",
        action="store_true",
        help="Disable ANSI colors",
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
        help="Refresh every N seconds",
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
    if args.no_color:
        config["no_color"] = True
    if args.simple:
        config["simple"] = True
    if args.compact:
        config["compact"] = True
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

    # Default: report
    from burnctl.config import load as load_config

    config = load_config()
    config = _merge_config(args, config)

    collectors = _resolve_collectors(args)

    if args.watch:
        _watch_loop(args, config, collectors)
    else:
        output = _render_report(args, config, collectors)
        print(output)


def _watch_loop(args, config, collectors):
    """Continuously re-render the report every N seconds.

    Uses the alternate screen buffer and cursor-home rewriting so the
    display updates atomically — no visible blank gap between refreshes,
    similar to ``top`` or ``htop``.
    """
    interval = max(1, args.watch)
    use_alt_screen = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

    if use_alt_screen:
        # Enter alternate screen buffer; hide cursor during redraws
        sys.stdout.write("\033[?1049h\033[?25l")
        sys.stdout.flush()

    try:
        while True:
            output = _render_report(args, config, collectors)

            if use_alt_screen:
                # Atomic redraw: cursor home → content → clear leftover
                sys.stdout.write("\033[H")
                sys.stdout.write(output)
                sys.stdout.write("\n\033[J")
                sys.stdout.flush()
            else:
                # Non-tty: simple clear + print (no escape sequences)
                sys.stdout.write("\033[2J\033[H")
                sys.stdout.flush()
                print(output)

            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        if use_alt_screen:
            sys.stdout.write("\033[?25h\033[?1049l")
            sys.stdout.flush()
