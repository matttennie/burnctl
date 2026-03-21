"""Multi-agent report renderer.

Aggregates stats from multiple collectors and renders them in a
multi-column box layout suitable for the terminal.
"""

import calendar
import csv
import json
import math
import os
import re
import sys
from datetime import datetime


# ── Color helpers ──────────────────────────────────────────────────

_ANSI_RE = re.compile(r'\033\[[0-9;]*m')
_R = "\033[0m"
_BD = "\033[1m"
_DM = "\033[2m"


def _rgb(r, g, b):
    return f"\033[38;2;{r};{g};{b}m"


def _lerp(c1, c2, t):
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


# ── Agent color palettes ──────────────────────────────────────────

# Claude: warm terracotta gradient (unchanged)
_CLAUDE_GRAD = [
    (193, 95, 60), (206, 107, 73), (218, 119, 86),
    (222, 115, 86), (232, 140, 105), (244, 180, 148),
]
# Gemini: blue → purple → soft lavender
_GEMINI_GRAD = [
    (66, 133, 244), (120, 93, 239), (147, 51, 234),
    (172, 102, 243), (211, 227, 253),
]
# Codex: white → dark gray
_CODEX_GRAD = [
    (240, 240, 240), (200, 200, 200), (160, 160, 160),
    (120, 120, 120), (80, 80, 80),
]
# OpenRouter: dark gray gradient
_OPENROUTER_GRAD = [
    (180, 180, 180), (150, 150, 150), (120, 120, 120),
    (90, 90, 90), (60, 60, 60),
]
# HuggingFace: yellow/amber gradient
_HUGGINGFACE_GRAD = [
    (255, 213, 79), (255, 183, 77), (255, 152, 0),
    (245, 124, 0), (230, 81, 0),
]
# Default / unknown agent
_DEFAULT_GRAD = [
    (160, 180, 200), (130, 155, 180), (100, 130, 160),
]
# Border & section titles
_BORDER_GRAD = [(100, 115, 135), (25, 30, 40)]  # light → very dark, left to right
_TITLE_COLOR = (120, 140, 165)

_AGENT_GRADIENTS = {
    "claude": _CLAUDE_GRAD,
    "gemini": _GEMINI_GRAD,
    "codex": _CODEX_GRAD,
    "openrouter": _OPENROUTER_GRAD,
    "huggingface": _HUGGINGFACE_GRAD,
}


def _agent_gradient(agent_id):
    """Return the gradient palette for a given agent id."""
    for key, grad in _AGENT_GRADIENTS.items():
        if key in agent_id.lower():
            return grad
    return _DEFAULT_GRAD


# ── Fallback theme (when claude_usage is not installed) ────────────


class _FallbackTheme:
    """Minimal theme that works without claude_usage.colors."""

    def __init__(self, use_color=True):
        self.enabled = use_color
        self._grad = []

    def _wrap(self, code, text):
        if not self.enabled:
            return str(text)
        return f"\033[{code}m{text}\033[0m"

    def border(self, ch):
        return self._wrap("36", ch)

    def border_line(self, text):
        return self._wrap("36", text)

    def title(self, text):
        return self._wrap("1;97", text)

    def accent(self, text):
        return self._wrap("36", text)

    def highlight(self, text):
        return self._wrap("33", text)

    def warm(self, text):
        return self._wrap("33", text)

    def success(self, text):
        return self._wrap("32", text)

    def muted(self, text):
        return self._wrap("2", text)

    def bold(self, text):
        return self._wrap("1", text)

    def stat_icon_color(self, index):
        colors = ["36", "34", "33", "32", "35"]
        return f"\033[{colors[index % len(colors)]}m" if self.enabled else ""

    def progress_bar(self, filled, empty, width):
        f_str = "\u2588" * filled
        e_str = "\u2591" * empty
        if not self.enabled:
            return f_str + e_str
        return f"\033[34m{f_str}\033[2m{e_str}\033[0m"

    def value_bar(self, paid_w, value_w):
        block = "\u2588"
        if not self.enabled:
            return block * (paid_w + value_w)
        paid = block * paid_w
        val = block * value_w
        return f"\033[31m{paid}\033[32m{val}\033[0m"

    def model_bar(self, filled, empty, model_name):
        f_str = "\u2593" * filled
        e_str = "\u2591" * empty
        if not self.enabled:
            return f_str + e_str
        return f"\033[36m{f_str}\033[2m{e_str}\033[0m"


class _MultiAgentTheme:
    """Wraps a base theme and adds per-agent colors + custom border/title."""

    def __init__(self, base):
        self._base = base

    @property
    def enabled(self):
        return self._base.enabled

    # ── Delegate unchanged methods ──
    def muted(self, text):
        return self._base.muted(text)

    def bold(self, text):
        return self._base.bold(text)

    def highlight(self, text):
        return self._base.highlight(text)

    def warm(self, text):
        return self._base.warm(text)

    def stat_icon_color(self, index):
        return self._base.stat_icon_color(index)

    def value_bar(self, paid_w, value_w):
        return self._base.value_bar(paid_w, value_w)

    # ── Overridden: border & title ──
    def border(self, ch):
        if not self.enabled:
            return str(ch)
        return f"{_rgb(*_BORDER_GRAD[0])}{ch}{_R}"

    def border_right(self, ch):
        if not self.enabled:
            return str(ch)
        return f"{_rgb(*_BORDER_GRAD[-1])}{ch}{_R}"

    def border_line(self, text):
        if not self.enabled:
            return str(text)
        grad = _BORDER_GRAD
        n = max(len(text) - 1, 1)
        parts = []
        for i, ch in enumerate(text):
            t = i / n
            r, g, b = _lerp(grad[0], grad[1], t)
            parts.append(f"{_rgb(r, g, b)}{ch}")
        parts.append(_R)
        return "".join(parts)

    def title(self, text):
        if not self.enabled:
            return str(text)
        return f"{_BD}{_rgb(*_TITLE_COLOR)}{text}{_R}"

    def success(self, text):
        if not self.enabled:
            return str(text)
        return f"{_rgb(*_TITLE_COLOR)}{text}{_R}"

    def accent(self, text):
        if not self.enabled:
            return str(text)
        return f"{_rgb(*_BORDER_GRAD[0])}{text}{_R}"

    # ── Agent-aware methods ──
    def agent_name(self, text, agent_id):
        """Color agent name — gradient for Gemini, solid primary for others."""
        if not self.enabled:
            return str(text)
        grad = _agent_gradient(agent_id)
        # Only Gemini gets the text gradient
        if "gemini" in agent_id.lower() and len(grad) >= 2:
            n = max(len(text) - 1, 1)
            segs = len(grad) - 1
            parts = []
            for i, ch in enumerate(text):
                t = i / n
                seg = min(int(t * segs), segs - 1)
                local_t = (t * segs) - seg
                r, g, b = _lerp(grad[seg], grad[seg + 1], local_t)
                parts.append(f"{_BD}{_rgb(r, g, b)}{ch}")
            parts.append(_R)
            return "".join(parts)
        return f"{_BD}{_rgb(*grad[0])}{text}{_R}"

    def agent_bar(self, text, agent_id):
        """Color text using the agent's primary gradient color."""
        if not self.enabled:
            return str(text)
        grad = _agent_gradient(agent_id)
        return f"{_rgb(*grad[0])}{text}{_R}"

    def agent_model_bar(self, filled, empty, model_name, agent_id):
        """Model breakdown bar colored by agent palette."""
        f_str = "\u2593" * filled
        e_str = "\u2591" * empty
        if not self.enabled:
            return f_str + e_str
        grad = _agent_gradient(agent_id)
        # Pick color by model role within the agent's palette
        if any(k in model_name for k in ("opus", "pro", "5.3")):
            color = grad[0]
        elif any(k in model_name for k in ("sonnet", "flash")):
            mid = len(grad) // 2
            color = grad[mid]
        else:
            color = grad[-1]
        return f"{_rgb(*color)}{f_str}{_DM}{e_str}{_R}"

    def agent_progress_bar(self, filled, empty, width, agent_id):
        """Gradient progress bar in agent colors."""
        if not self.enabled:
            return "\u2588" * filled + "\u2591" * empty
        grad = _agent_gradient(agent_id)
        parts = []
        for i in range(filled):
            t = i / max(width - 1, 1)
            seg = min(int(t * (len(grad) - 1)), len(grad) - 2)
            local_t = (t * (len(grad) - 1)) - seg
            r, g, b = _lerp(grad[seg], grad[seg + 1], local_t)
            parts.append(f"{_rgb(r, g, b)}\u2588")
        empty_str = "\u2591" * empty
        parts.append(f"{_DM}{empty_str}{_R}")
        return "".join(parts)

    # Keep base methods accessible for non-agent contexts
    def progress_bar(self, filled, empty, width):
        return self._base.progress_bar(filled, empty, width)

    def model_bar(self, filled, empty, model_name):
        return self._base.model_bar(filled, empty, model_name)


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


def aggregate_stats(
    collectors, config, ref_date=None, offset=0,
    start_override=None, end_override=None,
):
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
    start_override, end_override : datetime | None
        Explicit date range (from ``--since`` / ``--until``).
        When set, *offset* is ignored.

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

        if start_override is not None:
            start = start_override
            end = end_override or ref_date
            today_dt = ref_date
        else:
            start, end, today_dt = compute_period(billing_day, offset)
        stats = collector.get_stats(start, end, ref_date)
        if stats is None:
            if collector.is_available():
                # Agent detected but no activity in this period
                total_days = (end - start).days
                days_elapsed = min(
                    (ref_date.date() - start.date()).days, total_days,
                )
                agents.append({
                    "id": collector.id,
                    "name": collector.name,
                    "plan_name": plan_name,
                    "plan_price": plan_price,
                    "interval": interval,
                    "period_start": start.strftime("%Y-%m-%d"),
                    "period_end": end.strftime("%Y-%m-%d"),
                    "days_elapsed": days_elapsed,
                    "days_remaining": total_days - days_elapsed,
                    "total_days": total_days,
                    "pace_pct": 0.0,
                    "projected_cost": 0.0,
                    "messages": 0,
                    "sessions": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "tool_calls": 0,
                    "period_cost": 0.0,
                    "alltime_cost": 0.0,
                    "value_ratio": 0.0,
                    "model_usage": {},
                    "first_session": "",
                    "total_messages": 0,
                    "total_sessions": 0,
                    "inactive": True,
                })
            continue

        total_days = (end - start).days
        # Use date-only math to avoid sub-day drift between datetime.now() calls
        days_elapsed = min((ref_date.date() - start.date()).days, total_days)
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
            "input_tokens": stats.get("input_tokens"),
            "output_tokens": stats.get("output_tokens", 0),
            "tool_calls": stats.get("tool_calls", 0),
            "period_cost": round(period_cost, 2),
            "alltime_cost": round(alltime_cost, 2),
            "value_ratio": round(value_ratio, 1),
            "model_usage": stats.get("model_usage", {}),
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


def fmt_short(n):
    """Compact number: 1,234 → 1.2K, 1,234,567 → 1.2M."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


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
    try:
        from claude_usage.colors import get_theme
        base_th = get_theme(theme, use_color=use_color)
    except ImportError:
        base_th = _FallbackTheme(use_color)
    th = _MultiAgentTheme(base_th)

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
        rv = th.border_right("\u2551")
        return f"{lv} {th.title(title)}{' ' * max(0, pad)} {rv}"

    def box_line(content="", raw_len=0):
        if raw_len == 0:
            raw_len = len(_strip_ansi(content))
        pad = box_w - 4 - raw_len
        lv = th.border("\u2551")
        rv = th.border_right("\u2551")
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
        styled_parts = []
        raw_parts = []
        for a in agents:
            name = a["name"][:col_w].center(col_w)
            if a.get("inactive"):
                suffix = " (inactive)"
                name_raw = (a["name"] + suffix)[:col_w].center(col_w)
                styled_parts.append(th.muted(name_raw))
                raw_parts.append(name_raw)
            else:
                styled_parts.append(
                    th.agent_name(name, a.get("id", "")),
                )
                raw_parts.append(name)
        cols = ("  ").join(styled_parts)
        raw = label_str + ("  ").join(raw_parts)
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

    # ── PERIOD USAGE ──
    lines.append(box_sep_light())
    lines.append(box_title("PERIOD USAGE"))
    lines.append(box_empty())
    lines.append(
        _row_bold("Sessions", [fmt(a["sessions"]) for a in agents]),
    )
    lines.append(
        _row_bold(
            "Input Tokens",
            [
                "N/A" if a["input_tokens"] is None
                else fmt(a["input_tokens"])
                for a in agents
            ],
        ),
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
        # Value ratio bars
        vr_bar_w = max(8, col_w - 6)
        vr_cells = []
        vr_raw_cells = []
        for a in agents:
            if a["plan_price"] == 0:
                na_str = "N/A".rjust(vr_bar_w + 4)
                vr_cells.append(th.muted(na_str))
                vr_raw_cells.append(na_str)
                continue
            vr = a["value_ratio"]
            if math.isnan(vr) or math.isinf(vr):
                nan_str = "N/A".rjust(vr_bar_w + 4)
                vr_cells.append(th.muted(nan_str))
                vr_raw_cells.append(nan_str)
                continue
            # Scale bar: 1x = empty, 10x = full
            fill_frac = min(vr / 10.0, 1.0)
            filled = int(vr_bar_w * fill_frac)
            empty = vr_bar_w - filled
            vr_label = f" {vr:.1f}x"
            bar = th.agent_progress_bar(filled, empty, vr_bar_w, a.get("id", ""))
            vr_cells.append(f"{bar}{th.bold(vr_label)}")
            vr_raw_cells.append(
                "\u2588" * filled + "\u2591" * empty + vr_label,
            )

        label_str = th.muted(f"{'Value Ratio':<{label_w}}")
        cols = ("  ").join(
            c.rjust(col_w + len(c) - len(r))
            for c, r in zip(vr_cells, vr_raw_cells)
        )
        raw = f"{'Value Ratio':<{label_w}}" + ("  ").join(
            r.rjust(col_w) for r in vr_raw_cells
        )
        lines.append(box_line(f"{label_str}{cols}", raw_len=len(raw)))

    # ── MODEL BREAKDOWN ──
    agents_with_models = [a for a in agents if a.get("model_usage")]
    if agents_with_models:
        from burnctl.pricing import get_agent_pricing

        lines.append(box_sep_light())
        lines.append(box_title("MODEL BREAKDOWN"))
        lines.append(box_empty())

        for a in agents_with_models:
            lines.append(
                box_line(
                    f"  {th.agent_name(a['name'], a.get('id', ''))}",
                    raw_len=len(f"  {a['name']}"),
                ),
            )
            model_usage = a["model_usage"]
            agent_pricing = get_agent_pricing(a.get("id", "")) or {}
            total_out = sum(
                u.get("outputTokens", 0) for u in model_usage.values()
            )
            _CH_FILL = "\u2593"
            _CH_EMPTY = "\u2591"
            for model, usage in model_usage.items():
                # Shorten model name for display
                short = model
                for prefix in ("claude-", "gemini-", "codex-"):
                    short = short.replace(prefix, "")
                short = re.sub(r"-(\d{8}|latest)$", "", short)

                inp = usage.get("inputTokens", 0)
                out = usage.get("outputTokens", 0)
                pct = int(out * 100 / total_out) if total_out else 0
                pct_label = "<1%" if pct == 0 and out > 0 else "%d%%" % pct

                mini_w = 8
                mini_filled = int(mini_w * pct / 100)
                mini_empty = mini_w - mini_filled
                bar = th.agent_model_bar(
                    mini_filled, mini_empty, short, a.get("id", ""),
                )
                fill_chars = _CH_FILL * mini_filled
                empty_chars = _CH_EMPTY * mini_empty

                # Look up pricing (try exact, then strip date suffix)
                mp = agent_pricing.get(model)
                if mp is None:
                    stripped = re.sub(r'-\d{8}$', '', model)
                    mp = agent_pricing.get(stripped, {})
                in_rate = mp.get("input", 0)
                out_rate = mp.get("output", 0)
                in_p = f"${in_rate:g}/M"
                out_p = f"${out_rate:g}/M"

                # Compact layout: name bar pct  In: NNN $X/M  Out: NNN $X/M
                in_s = fmt_short(inp)
                out_s = fmt_short(out)
                detail = (
                    f"    {short:<14}"
                    + fill_chars + empty_chars
                    + f" {pct_label:>4}"
                    + f"  In:{in_s:>6} {in_p:>6}"
                    + f"  Out:{out_s:>6} {out_p:>6}"
                )
                detail_styled = (
                    f"    {th.muted(f'{short:<14}')}{bar}"
                    f" {pct_label:>4}"
                    f"  {th.muted('In:')}{in_s:>6} {th.muted(in_p):>6}"
                    f"  {th.muted('Out:')}{out_s:>6} {th.muted(out_p):>6}"
                )
                lines.append(
                    box_line(detail_styled, raw_len=len(detail)),
                )
            lines.append(box_empty())

    lines.append(box_bottom())

    # Footer
    today_str = stats["today"]
    lines.append(f"  {th.muted(f'Generated: {today_str}')}")
    lines.append("")

    return "\n".join(lines)


# ── Period-over-period diff ─────────────────────────────────────────


def _diff_str(cur, prev, is_usd=False):
    """Format a delta value as ``+X`` / ``-X`` with sign."""
    delta = cur - prev
    if is_usd:
        if delta >= 0:
            return "+$%.2f" % delta
        return "-$%.2f" % abs(delta)
    sign = "+" if delta >= 0 else ""
    return "%s%s" % (sign, fmt(delta))


def render_diff(current, previous):
    """Plain-text period-over-period comparison.

    Parameters
    ----------
    current, previous : dict
        Output of :func:`aggregate_stats` for the current and
        previous billing periods.
    """
    lines = [""]
    lines.append("BURNCTL PERIOD-OVER-PERIOD DIFF")
    lines.append("")

    cur_agents = {a["id"]: a for a in current.get("agents", [])}
    prev_agents = {a["id"]: a for a in previous.get("agents", [])}
    all_ids = list(dict.fromkeys(
        list(cur_agents.keys()) + list(prev_agents.keys()),
    ))

    if not all_ids:
        return "No agent data available."

    for aid in all_ids:
        cur = cur_agents.get(aid)
        prev = prev_agents.get(aid)
        agent = cur if cur is not None else prev
        name = agent["name"]  # type: ignore[index]

        lines.append(f"  {name}")
        lines.append(
            f"    Period: "
            f"{prev['period_start'] if prev else '?'}"
            f" - {prev['period_end'] if prev else '?'}"
            f"  vs  "
            f"{cur['period_start'] if cur else '?'}"
            f" - {cur['period_end'] if cur else '?'}"
        )
        lines.append("")

        metrics = [
            ("Sessions", "sessions", False),
            ("Input Tokens", "input_tokens", False),
            ("Output Tokens", "output_tokens", False),
            ("Tool Calls", "tool_calls", False),
            ("Est. API Cost", "period_cost", True),
        ]
        hdr = f"    {'Metric':<16} {'Last':>10}  {'Current':>10}  {'Delta':>10}"
        lines.append(hdr)
        lines.append("    " + "-" * (len(hdr) - 4))

        for label, key, is_usd in metrics:
            c = cur.get(key, 0) if cur else 0
            p = prev.get(key, 0) if prev else 0
            if c is None and p is None:
                lines.append(
                    f"    {label:<16} {'N/A':>10}  {'N/A':>10}  {'N/A':>10}",
                )
                continue
            c = c if c is not None else 0
            p = p if p is not None else 0
            if is_usd:
                c_str = fmt_usd(c)
                p_str = fmt_usd(p)
            else:
                c_str = fmt(c)
                p_str = fmt(p)
            delta = _diff_str(c, p, is_usd)
            lines.append(
                f"    {label:<16} {p_str:>10}  {c_str:>10}  {delta:>10}",
            )
        lines.append("")

    # System total
    cur_total = current.get("total_period_cost", 0)
    prev_total = previous.get("total_period_cost", 0)
    lines.append(
        f"  System Total: {fmt_usd(prev_total)} -> "
        f"{fmt_usd(cur_total)}  "
        f"({_diff_str(cur_total, prev_total, True)})",
    )
    lines.append("")
    lines.append(f"  Generated: {current.get('today', '')}")
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
        lines.append(f"  Sessions: {fmt(a['sessions'])}")
        in_tok = a.get("input_tokens")
        lines.append(
            f"  Input tokens: {'N/A' if in_tok is None else fmt(in_tok)}"
        )
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
