"""Multi-agent report renderer.

Aggregates stats from multiple collectors and renders them in a
multi-column box layout suitable for the terminal.
"""

import calendar
import csv
import json
import os
import re
import sys
from datetime import datetime, timedelta


# ── Period calculation ──────────────────────────────────────────────


def _safe_replace_day(dt, day):
    """Replace the day component of *dt*, clamping to month length."""
    max_day = calendar.monthrange(dt.year, dt.month)[1]
    return dt.replace(day=min(day, max_day))


def compute_period(billing_day, offset=0):
    """Compute billing period boundaries.

    Parameters
    ----------
    billing_day : int
        Day of the month when the billing period starts.
    offset : int
        ``0`` for the current period, ``-1`` for the previous one, etc.

    Returns
    -------
    tuple[datetime, datetime, datetime]
        ``(start, end, today_dt)`` as naive :class:`datetime` objects.
    """
    today_dt = datetime.now()

    if offset != 0:
        month = today_dt.month + offset
        year = today_dt.year
        while month < 1:
            month += 12
            year -= 1
        while month > 12:
            month -= 12
            year += 1
        max_day = calendar.monthrange(year, month)[1]
        today_dt = today_dt.replace(
            year=year, month=month, day=min(today_dt.day, max_day),
        )

    if today_dt.day >= billing_day:
        start = _safe_replace_day(today_dt, billing_day)
        next_month = (today_dt.month % 12) + 1
        next_year = today_dt.year + (1 if next_month == 1 else 0)
        end = _safe_replace_day(
            today_dt.replace(year=next_year, month=next_month, day=1),
            billing_day,
        )
    else:
        prev_month = today_dt.month - 1 or 12
        prev_year = today_dt.year - (1 if prev_month == 12 else 0)
        start = _safe_replace_day(
            today_dt.replace(year=prev_year, month=prev_month, day=1),
            billing_day,
        )
        end = _safe_replace_day(today_dt, billing_day)

    return start, end, today_dt


# ── Data aggregation ────────────────────────────────────────────────


def aggregate_stats(collectors, config, ref_date=None, offset=0):
    """Orchestrate data collection from all *collectors*.

    Parameters
    ----------
    collectors : list[BaseCollector]
        Collector instances to query.
    config : dict
        Merged configuration (CLI flags > config file > defaults).
    ref_date : datetime | None
        Override "today" for testing. ``None`` means ``datetime.now()``.
    offset : int
        Period offset (``0`` = current, ``-1`` = previous).

    Returns
    -------
    dict
        Aggregate report structure with an ``"agents"`` list and
        ``"total_period_cost"`` / ``"today"`` summary fields.
    """
    if ref_date is None:
        ref_date = datetime.now()

    agents = []
    total_period_cost = 0.0

    for collector in collectors:
        plan_info = collector.get_plan_info(config)
        billing_day = plan_info["billing_day"]
        plan_name = plan_info["plan_name"]
        plan_price = plan_info["plan_price"]
        interval = plan_info["interval"]

        start, end, today_dt = compute_period(billing_day, offset)
        stats = collector.get_stats(start, end, ref_date)
        if stats is None:
            continue

        total_days = (end - start).days
        days_elapsed = min((ref_date - start).days, total_days)
        days_remaining = total_days - days_elapsed

        period_cost = stats.get("period_cost", 0.0)
        alltime_cost = stats.get("alltime_cost", 0.0)

        if plan_price > 0:
            pace_pct = period_cost / plan_price * 100
        else:
            pace_pct = 0.0

        if days_elapsed > 0 and total_days > 0:
            projected_cost = period_cost / (days_elapsed / total_days)
        else:
            projected_cost = 0.0

        # Value ratio: how much API-equivalent value vs total paid
        first_session = stats.get("first_session", "")
        if first_session and plan_price > 0:
            try:
                fs_dt = datetime.strptime(first_session, "%Y-%m-%d")
                months_active = max(
                    1,
                    (ref_date.year - fs_dt.year) * 12
                    + ref_date.month - fs_dt.month,
                )
            except ValueError:
                months_active = 1
            total_paid = plan_price * months_active
        else:
            months_active = 1
            total_paid = plan_price if plan_price > 0 else 0

        value_ratio = alltime_cost / total_paid if total_paid > 0 else 0.0

        agent_data = {
            "id": collector.id,
            "name": collector.name,
            "plan_name": plan_name,
            "plan_price": plan_price,
            "interval": interval,
            "period_start": start.strftime("%Y-%m-%d"),
            "period_end": end.strftime("%Y-%m-%d"),
            "days_elapsed": days_elapsed,
            "days_remaining": days_remaining,
            "total_days": total_days,
            "pace_pct": round(pace_pct, 1),
            "projected_cost": round(projected_cost, 2),
            "messages": stats.get("messages", 0),
            "sessions": stats.get("sessions", 0),
            "output_tokens": stats.get("output_tokens", 0),
            "tool_calls": stats.get("tool_calls", 0),
            "period_cost": round(period_cost, 2),
            "alltime_cost": round(alltime_cost, 2),
            "value_ratio": round(value_ratio, 1),
            "model_usage": stats.get("model_usage", {}),
            "daily_messages": stats.get("daily_messages", {}),
            "spark_data": stats.get("spark_data", []),
            "first_session": first_session,
            "total_messages": stats.get("total_messages", 0),
            "total_sessions": stats.get("total_sessions", 0),
        }
        agents.append(agent_data)
        total_period_cost += period_cost

    return {
        "agents": agents,
        "total_period_cost": round(total_period_cost, 2),
        "today": ref_date.strftime("%Y-%m-%d"),
    }


# ── Formatting helpers ──────────────────────────────────────────────


def fmt(n):
    """Comma-formatted integer."""
    return f"{n:,}"


def fmt_usd(n):
    """Dollar-formatted float (``$X,XXX.XX``)."""
    return f"${n:,.2f}"


def sparkline(values):
    """Unicode sparkline from a list of numeric values."""
    if not values:
        return ""
    blocks = " \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
    mn, mx = min(values), max(values)
    rng = mx - mn if mx != mn else 1
    return "".join(
        blocks[min(8, int((v - mn) / rng * 8))] for v in values
    )


_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _strip_ansi(text):
    """Remove ANSI escape sequences from *text*."""
    return _ANSI_RE.sub("", text)


# ── Full multi-column renderer ──────────────────────────────────────


def render_full(stats, simple=False, use_color=True, theme="gradient"):
    """Multi-column box report.

    Parameters
    ----------
    stats : dict
        Output of :func:`aggregate_stats`.
    simple : bool
        Skip VALUE & ROI section when ``True``.
    use_color : bool
        Enable ANSI colors.
    theme : str
        One of ``"gradient"``, ``"classic"``, ``"colorblind"``, ``"accessible"``.
    """
    from claude_usage.colors import get_theme  # noqa: F401 — gradient_str/R used via theme

    th = get_theme(theme, use_color=use_color)
    agents = stats["agents"]
    if not agents:
        return "No agent data available."

    # ── Layout metrics ──
    try:
        term_w = os.get_terminal_size().columns
    except (ValueError, OSError):
        term_w = 80

    label_w = 16
    num_agents = len(agents)
    # Each agent column gets equal space; leave room for borders + label + gaps
    # Layout: ║ <label_w> <col> <gap> <col> ... ║
    # Inner width = label_w + num_agents * col_w + (num_agents - 1) * 2
    # Total box width = inner + 4 (borders + padding)
    gap = 2
    available = term_w - 4 - label_w - gap * max(num_agents - 1, 0)
    col_w = max(12, available // max(num_agents, 1))
    inner_w = label_w + num_agents * col_w + gap * max(num_agents - 1, 0)
    box_w = inner_w + 4  # ║ + space + content + space + ║

    # ── Box drawing characters ──
    _CH_DBL = "\u2550"   # ═
    _CH_SNG = "\u2500"   # ─
    _CH_FILL = "\u2593"  # ▓
    _CH_EMPTY = "\u2591"  # ░

    # ── Box drawing helpers ──

    def box_top():
        hbar = _CH_DBL * (box_w - 2)
        return th.border_line("\u2554" + hbar + "\u2557")

    def box_bottom():
        hbar = _CH_DBL * (box_w - 2)
        return th.border_line("\u255a" + hbar + "\u255d")

    def box_sep():
        hbar = _CH_DBL * (box_w - 2)
        return th.border_line("\u2560" + hbar + "\u2563")

    def box_sep_light():
        hbar = _CH_SNG * (box_w - 2)
        return th.border_line("\u255f" + hbar + "\u2562")

    def box_title(title):
        pad = box_w - 4 - len(title)
        lv = th.border("\u2551")
        rv = th.border("\u2551")
        return f"{lv} {th.title(title)}{' ' * max(0, pad)} {rv}"

    def box_line(content="", raw_len=0):
        if raw_len == 0:
            raw_len = len(_strip_ansi(content))
        pad = box_w - 4 - raw_len
        lv = th.border("\u2551")
        rv = th.border("\u2551")
        return f"{lv} {content}{' ' * max(0, pad)} {rv}"

    def box_empty():
        return box_line()

    # ── Row helpers for multi-column layout ──

    def _row(label, values):
        """Render a label + per-agent values row."""
        label_str = th.muted(f"{label:<{label_w}}")
        cols = ("  ").join(v.rjust(col_w) for v in values)
        raw = f"{label:<{label_w}}" + ("  ").join(
            _strip_ansi(v).rjust(col_w) for v in values
        )
        return box_line(f"{label_str}{cols}", raw_len=len(raw))

    def _row_bold(label, values):
        """Row with bold values."""
        label_str = th.muted(f"{label:<{label_w}}")
        cols = ("  ").join(th.bold(v.rjust(col_w)) for v in values)
        raw = f"{label:<{label_w}}" + ("  ").join(
            v.rjust(col_w) for v in values
        )
        return box_line(f"{label_str}{cols}", raw_len=len(raw))

    def _row_highlight(label, values):
        """Row with success-colored values."""
        label_str = th.muted(f"{label:<{label_w}}")
        cols = ("  ").join(
            th.bold(th.success(v.rjust(col_w))) for v in values
        )
        raw = f"{label:<{label_w}}" + ("  ").join(
            v.rjust(col_w) for v in values
        )
        return box_line(f"{label_str}{cols}", raw_len=len(raw))

    def _header_row():
        """Agent names as column headers."""
        label_str = " " * label_w
        cols = ("  ").join(th.bold(a["name"][:col_w].center(col_w)) for a in agents)
        raw = label_str + ("  ").join(
            a["name"][:col_w].center(col_w) for a in agents
        )
        return box_line(f"{label_str}{cols}", raw_len=len(raw))

    # ── Build output ──
    lines = [""]

    # Title
    lines.append(box_top())
    if num_agents == 1:
        title = f"{agents[0]['name'].upper()} USAGE REPORT"
    else:
        title = "BURNCTL MULTI-AGENT REPORT"
    lines.append(box_title(title))
    lines.append(box_sep())

    # Agent column headers
    lines.append(_header_row())
    lines.append(box_empty())

    # ── Plan info ──
    lines.append(_row("Plan", [a["plan_name"] for a in agents]))
    lines.append(_row("Period Start", [a["period_start"] for a in agents]))
    lines.append(_row("Period End", [a["period_end"] for a in agents]))
    lines.append(
        _row("Days Left", [str(a["days_remaining"]) for a in agents]),
    )
    lines.append(box_empty())

    # ── Pace bars ──
    pace_bar_w = max(8, col_w - 6)  # leave room for percentage label
    pace_cells = []
    pace_raw_cells = []
    for a in agents:
        pct = min(a["pace_pct"], 100)
        filled = int(pace_bar_w * pct / 100)
        empty = pace_bar_w - filled
        pct_label = f" {pct:.0f}%"
        bar = th.progress_bar(filled, empty, pace_bar_w)
        pace_cells.append(f"{bar}{th.bold(pct_label)}")
        pace_raw_cells.append(
            "\u2588" * filled + "\u2591" * empty + pct_label,
        )

    label_str = th.muted(f"{'Pace':<{label_w}}")
    cols = ("  ").join(
        c.rjust(col_w + len(c) - len(r))
        for c, r in zip(pace_cells, pace_raw_cells)
    )
    raw = f"{'Pace':<{label_w}}" + ("  ").join(
        r.rjust(col_w) for r in pace_raw_cells
    )
    lines.append(box_line(f"{label_str}{cols}", raw_len=len(raw)))

    # ── PERIOD USAGE ──
    lines.append(box_sep_light())
    lines.append(box_title("PERIOD USAGE"))
    lines.append(box_empty())
    lines.append(
        _row_bold("Messages", [fmt(a["messages"]) for a in agents]),
    )
    lines.append(
        _row_bold("Sessions", [fmt(a["sessions"]) for a in agents]),
    )
    lines.append(
        _row_bold(
            "Output Tokens",
            [fmt(a["output_tokens"]) for a in agents],
        ),
    )
    lines.append(
        _row_highlight(
            "Est. API Cost",
            [fmt_usd(a["period_cost"]) for a in agents],
        ),
    )

    # System total (only if more than one agent)
    if num_agents > 1:
        lines.append(box_empty())
        total_label = th.muted(f"{'System Total':<{label_w}}")
        total_val = th.bold(
            th.success(fmt_usd(stats["total_period_cost"]).rjust(col_w)),
        )
        # Pad remaining columns with blanks
        blank_cols = ("  ").join(" " * col_w for _ in range(num_agents - 1))
        total_raw = (
            f"{'System Total':<{label_w}}"
            + fmt_usd(stats["total_period_cost"]).rjust(col_w)
        )
        if blank_cols:
            total_content = f"{total_label}{total_val}  {blank_cols}"
            total_raw += "  " + ("  ").join(
                " " * col_w for _ in range(num_agents - 1)
            )
        else:
            total_content = f"{total_label}{total_val}"
        lines.append(box_line(total_content, raw_len=len(total_raw)))

    # ── VALUE & ROI ──
    if not simple:
        lines.append(box_sep_light())
        lines.append(box_title("VALUE & ROI"))
        lines.append(box_empty())
        lines.append(
            _row_bold(
                "All-time Value",
                [fmt_usd(a["alltime_cost"]) for a in agents],
            ),
        )
        lines.append(
            _row_bold(
                "Value Ratio",
                [f"{a['value_ratio']:.1f}x" for a in agents],
            ),
        )

    # ── MODEL BREAKDOWN ──
    agents_with_models = [a for a in agents if a.get("model_usage")]
    if agents_with_models:
        lines.append(box_sep_light())
        lines.append(box_title("MODEL BREAKDOWN"))
        lines.append(box_empty())

        for a in agents_with_models:
            lines.append(
                box_line(
                    th.bold(f"  {a['name']}"),
                    raw_len=len(f"  {a['name']}"),
                ),
            )
            model_usage = a["model_usage"]
            total_out = sum(
                u.get("outputTokens", 0) for u in model_usage.values()
            )
            for model, usage in model_usage.items():
                # Shorten model name for display
                short = model
                for prefix in ("claude-", "gemini-", "codex-"):
                    short = short.replace(prefix, "")
                for suffix in (
                    "-20251101", "-20250929", "-20250219",
                    "-latest",
                ):
                    short = short.replace(suffix, "")

                out = usage.get("outputTokens", 0)
                pct = int(out * 100 / total_out) if total_out else 0

                mini_w = min(12, col_w - 2)
                mini_filled = int(mini_w * pct / 100)
                mini_empty = mini_w - mini_filled
                bar = th.model_bar(mini_filled, mini_empty, short)

                fill_chars = _CH_FILL * mini_filled
                empty_chars = _CH_EMPTY * mini_empty
                detail = (
                    "    " + fill_chars + empty_chars
                    + f" {pct}%  {fmt(out)} tok"
                )
                detail_styled = (
                    f"    {bar}"
                    f" {pct}%  {th.muted(f'{fmt(out)} tok')}"
                )
                lines.append(
                    box_line(detail_styled, raw_len=len(detail)),
                )
            lines.append(box_empty())

    # ── DAILY ACTIVITY ──
    lines.append(box_sep_light())
    lines.append(box_title("DAILY ACTIVITY"))
    lines.append(box_empty())

    for a in agents:
        spark_data = a.get("spark_data", [])
        if not spark_data:
            # Build from daily_messages
            dm = a.get("daily_messages", {})
            start_dt = datetime.strptime(a["period_start"], "%Y-%m-%d")
            elapsed = a["days_elapsed"]
            spark_data = []
            for i in range(elapsed + 1):
                day_str = (start_dt + timedelta(days=i)).strftime("%Y-%m-%d")
                spark_data.append(dm.get(day_str, 0))

        spark = sparkline(spark_data)
        label = a["name"]
        spark_line = f"  {label:<14}{spark}"
        spark_styled = f"  {th.muted(f'{label:<14}')}{th.accent(spark)}"
        lines.append(box_line(spark_styled, raw_len=len(spark_line)))

    lines.append(box_empty())
    lines.append(box_bottom())

    # Footer
    today_str = stats["today"]
    lines.append(f"  {th.muted(f'Generated: {today_str}')}")
    lines.append("")

    return "\n".join(lines)


# ── Alternative output formats ──────────────────────────────────────


def render_json(stats):
    """JSON dump of the aggregate structure."""
    return json.dumps(stats, indent=2, default=str)


def render_compact(stats):
    """Single-line summary: ``Agent: $X.XX | Agent: $Y.YY | Total: $Z.ZZ``."""
    agents = stats.get("agents", [])
    if not agents:
        return "No agent data available."
    parts = [f"{a['name']}: {fmt_usd(a['period_cost'])}" for a in agents]
    if len(agents) > 1:
        parts.append(f"Total: {fmt_usd(stats['total_period_cost'])}")
    return " | ".join(parts)


def render_accessible(stats):
    """Plain-text, screen-reader friendly. No box drawing, no ANSI."""
    agents = stats.get("agents", [])
    if not agents:
        return "No agent data available."

    lines = []
    if len(agents) == 1:
        lines.append(f"{agents[0]['name']} Usage Report")
    else:
        lines.append("Burnctl Multi-Agent Usage Report")
    lines.append("")

    for a in agents:
        lines.append(f"Agent: {a['name']}")
        lines.append(f"  Plan: {a['plan_name']}")
        lines.append(
            f"  Billing period: {a['period_start']} to {a['period_end']}",
        )
        lines.append(
            f"  Days remaining: {a['days_remaining']} of {a['total_days']}",
        )
        if a["plan_price"] > 0:
            lines.append(
                f"  Pace: {a['pace_pct']:.0f} percent of plan value used",
            )
            if a["projected_cost"] > 0:
                lines.append(
                    f"  Projected period cost: {fmt_usd(a['projected_cost'])}",
                )
        lines.append(f"  Messages: {fmt(a['messages'])}")
        lines.append(f"  Sessions: {fmt(a['sessions'])}")
        lines.append(f"  Output tokens: {fmt(a['output_tokens'])}")
        lines.append(f"  Tool calls: {fmt(a['tool_calls'])}")
        lines.append(
            f"  API-equivalent cost: {fmt_usd(a['period_cost'])}",
        )
        lines.append(
            f"  All-time value: {fmt_usd(a['alltime_cost'])}",
        )
        lines.append(f"  Value ratio: {a['value_ratio']:.1f}x")
        lines.append(f"  First session: {a['first_session']}")
        lines.append(
            f"  Total messages: {fmt(a['total_messages'])}",
        )
        lines.append(
            f"  Total sessions: {fmt(a['total_sessions'])}",
        )
        lines.append("")

    if len(agents) > 1:
        lines.append(
            f"System total period cost: "
            f"{fmt_usd(stats['total_period_cost'])}",
        )
        lines.append("")

    lines.append(f"Report date: {stats['today']}")
    lines.append("")
    return "\n".join(lines)


def export_csv(stats, filepath="burnctl.csv"):
    """Append one row per agent to a CSV file.

    Columns: agent, period_start, period_end, messages, sessions,
    output_tokens, period_cost, alltime_cost
    """
    agents = stats.get("agents", [])
    if not agents:
        print("No agent data to export.", file=sys.stderr)
        return

    fieldnames = [
        "agent",
        "period_start",
        "period_end",
        "messages",
        "sessions",
        "output_tokens",
        "period_cost",
        "alltime_cost",
    ]
    file_exists = os.path.isfile(filepath)

    try:
        with open(filepath, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            for a in agents:
                writer.writerow({
                    "agent": a["id"],
                    "period_start": a["period_start"],
                    "period_end": a["period_end"],
                    "messages": a["messages"],
                    "sessions": a["sessions"],
                    "output_tokens": a["output_tokens"],
                    "period_cost": round(a["period_cost"], 2),
                    "alltime_cost": round(a["alltime_cost"], 2),
                })
    except OSError as exc:
        print(f"Error: could not write to {filepath}: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Exported {len(agents)} agent(s) to {filepath}")
