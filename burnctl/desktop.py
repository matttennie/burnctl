"""Read-only, versioned JSON interface for the native desktop reader.

The existing collectors remain authoritative. No settings, credentials, shell
files, proxy services, or pricing-history files are changed.
"""

import argparse
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
import math

from burnctl import config as config_module
from burnctl.pricing import read_only_history
from burnctl.report import aggregate_stats


_QUOTA = {"elevenlabs": "characters", "tavily": "credits"}
_ESTIMATED = {"claude", "codex", "antigravity", "gemini"}
_MISSING = "No readable usage source or configured API credential was found."
_NO_DATA = "The source returned no usable data. This is not a zero balance."


class _NoData(Exception):
    pass


class _Capture:
    """Preserve raw availability rather than the terminal's empty-row fallback."""

    def __init__(self, collector):
        self.collector = collector
        self.raw = None

    def __getattr__(self, name):
        return getattr(self.collector, name)

    def get_stats(self, *args, **kwargs):
        self.raw = self.collector.get_stats(*args, **kwargs)
        if self.raw is None:
            raise _NoData()
        return self.raw


def _basis(agent_id, raw=None):
    if agent_id in _ESTIMATED:
        return "estimated"
    if agent_id in _QUOTA:
        return "quota"
    if agent_id == "local":
        return "unmetered"
    if agent_id == "openrouter":
        return "reported"
    if agent_id == "huggingface" and raw and raw.get("input_tokens") is None:
        return "reported"
    return "recorded"


def _empty(collector, status, message):
    return {
        "id": collector.id, "name": collector.name,
        "source_status": status, "status_message": message,
        "cost_basis": _basis(collector.id),
        "usage_unit": _QUOTA.get(collector.id, "tokens"),
        "usage_count": None,
        "period_start": None, "period_end": None,
        "plan_name": None, "plan_price": None, "interval": None,
        "messages": None, "sessions": None,
        "input_tokens": None, "output_tokens": None,
        "period_cost": None, "alltime_cost": None,
        "model_usage": {}, "last_active": None,
    }


def _quota_zero(collector):
    """Recognize genuine zero counters that CLI collectors intentionally hide."""
    if collector.id == "elevenlabs":
        account = getattr(collector, "_sub", None)
        if isinstance(account, dict) and account.get("character_count") == 0:
            return 0
    if collector.id == "tavily":
        account = getattr(collector, "_usage", None)
        if isinstance(account, dict):
            values = [account.get("plan_usage"), account.get("paygo_usage")]
            present = [v for v in values if isinstance(v, int) and not isinstance(v, bool)]
            if present and all(v == 0 for v in present):
                return 0
    return None


def _read_provider(collector, config, period, ref_date):
    if period == "previous" and collector.id in _QUOTA:
        return _empty(collector, "unsupported", "This API only reports the current quota cycle.")
    if period == "previous" and collector.id == "openrouter":
        return _empty(collector, "unsupported", "The activity API does not establish full previous-window coverage.")
    if not collector.is_available():
        return _empty(collector, "unavailable", _MISSING)
    if collector.id == "local":
        return _empty(collector, "unsupported", "Local inference was detected; usage metering is not available.")
    capture = _Capture(collector)
    try:
        row = aggregate_stats(
            [capture], config, ref_date=ref_date,
            offset=-1 if period == "previous" else 0,
        )["agents"][0]
    except _NoData:
        count = _quota_zero(collector)
        if count is None:
            return _empty(collector, "unavailable", _NO_DATA)
        row = _empty(collector, "ok", "Current provider-reported quota; no dollar amount is supplied.")
        row["usage_count"] = count
        return row
    row.update({
        "source_status": "ok", "status_message": "",
        "cost_basis": _basis(collector.id, capture.raw),
        "usage_unit": _QUOTA.get(collector.id, "tokens"),
        "usage_count": None,
    })
    if collector.id in _ESTIMATED:
        row["period_cost_estimated"] = True
        row["status_message"] = "Estimated API-equivalent usage value, not your subscription bill."
        if collector.id == "claude":
            row["status_message"] += " Input tokens are not available for this period."
    elif collector.id == "grok":
        row["status_message"] = "Recorded server costs where available; list-price estimates otherwise."
    elif collector.id == "huggingface":
        row["status_message"] = "Inference, jobs and private storage only; other billing categories may be excluded."
    elif collector.id == "openrouter":
        row["status_message"] = "Provider activity, supplemented by the local request ledger when available."
    elif row["cost_basis"] == "recorded":
        row["status_message"] = "Costs recorded in the local usage ledger; not a verified invoice."
    if collector.id in _QUOTA:
        row["period_cost"] = row["alltime_cost"] = None
        row["plan_price"] = None
        if collector.id == "elevenlabs":
            row["usage_count"] = sum(
                value.get("outputTokens", 0) for value in row["model_usage"].values()
            )
        else:
            row["usage_count"] = row["messages"]
            row["messages"] = None
            # Tavily does not provide cycle dates; the CLI's configured dates
            # must not imply that current counters were filtered to that range.
            row["period_start"] = row["period_end"] = None
        row["model_usage"] = {}
        row["status_message"] = "Current provider-reported quota; no dollar amount is supplied."
    return row


def _finite(value):
    """Strict JSON forbids NaN/Infinity; missing is safer than invented zero."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _finite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(item) for item in value]
    return value


def build_report(period="current", collectors=None, config=None, ref_date=None):
    """Collect independently; errors cannot hide other healthy providers."""
    if period not in ("current", "previous"):
        raise ValueError("period must be current or previous")
    notices = []
    # Do not forward collector diagnostics: network exceptions may contain
    # credentials or paths. Only bounded, generic status messages reach the UI.
    with read_only_history(), redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
        if collectors is None:
            from burnctl.collectors import ALL_COLLECTORS
            collectors = ALL_COLLECTORS
        if config is None:
            warnings = io.StringIO()
            try:
                with redirect_stderr(warnings):
                    config = config_module.load()
            except (TypeError, ValueError, AttributeError):
                config = deepcopy(config_module.DEFAULTS)
                notices.append("Saved configuration could not be read; default billing periods are shown.")
            if warnings.getvalue():
                notices.append("Billing settings may be defaults. This reader does not change configuration.")
        rows = []
        for collector in collectors:
            warnings = io.StringIO()
            try:
                with redirect_stderr(warnings):
                    row = _read_provider(collector, config, period, ref_date)
                if warnings.getvalue() and row["source_status"] == "ok":
                    row["status_message"] += " Some source data could not be read; these figures may be incomplete."
            except Exception:
                row = _empty(
                    collector, "error",
                    "Unable to read this source. Other providers are unaffected; try Refresh.",
                )
            rows.append(row)
    return _finite({
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": period, "demo": False, "agents": rows,
        "notices": notices,
    })


def demo_report(period="current"):
    """Synthetic, reproducible data for UI testing; never blended with live data."""
    previous = period == "previous"
    scale = 0.65 if previous else 1
    start, end = ("2026-09-01", "2026-10-01") if previous else ("2026-10-01", "2026-11-01")
    rows = []
    samples = [
        ("claude", "Claude Code", 1248.64, None, 923000, "claude-sonnet-4-5", "estimated"),
        ("codex", "Codex CLI", 842.18, 4780000, 614000, "gpt-5-codex", "estimated"),
        ("antigravity", "Antigravity CLI", 68.43, 512000, 92000, "gemini-2.5-pro", "estimated"),
        ("openrouter", "OpenRouter", 37.26, 820000, 190000, "anthropic/claude-sonnet-4-5", "reported"),
    ]
    for index, (agent_id, name, cost, input_count, output, model, basis) in enumerate(samples):
        row: dict = {
            "id": agent_id, "name": name, "source_status": "ok",
            "status_message": "Synthetic example, not real account data.",
            "cost_basis": basis, "usage_unit": "tokens", "usage_count": None,
            "period_start": start, "period_end": end,
            "plan_name": "pro" if basis == "estimated" else "pay-as-you-go",
            "plan_price": 20 if basis == "estimated" else 0, "interval": "mo",
            "messages": int((1824 - index * 367) * scale),
            "sessions": int((84 - index * 13) * scale) if basis == "estimated" else None,
            "input_tokens": int(input_count * scale) if input_count is not None else None,
            "output_tokens": int(output * scale), "period_cost": round(cost * scale, 2),
            "last_active": "2026-10-28" if not previous else "2026-09-29",
            "model_usage": {model: {"outputTokens": int(output * scale)}},
            "period_cost_estimated": basis == "estimated", "rolling_window": 30 if basis == "reported" else 0,
        }
        if input_count is not None:
            row["model_usage"][model]["inputTokens"] = int(input_count * scale)
        rows.append(row)
    rows.append({
        "id": "elevenlabs", "name": "ElevenLabs", "source_status": "ok" if not previous else "unsupported",
        "status_message": "Synthetic current-cycle character quota." if not previous else "Current cycle only.",
        "cost_basis": "quota", "usage_unit": "characters", "usage_count": 32140 if not previous else None,
        "period_cost": None, "model_usage": {}, "period_start": None, "period_end": None,
    })
    rows.append({
        "id": "huggingface", "name": "HuggingFace", "source_status": "unavailable",
        "status_message": _MISSING, "cost_basis": "reported", "usage_unit": "tokens",
        "period_cost": None, "model_usage": {}, "period_start": None, "period_end": None,
    })
    return {
        "schema_version": 1, "generated_at": "2026-10-28T16:42:00+00:00",
        "period": period, "demo": True, "agents": rows,
        "notices": ["Demonstration only. All figures are synthetic."],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--period", choices=("current", "previous"), default="current")
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args(argv)
    report = demo_report(args.period) if args.demo else build_report(args.period)
    print(json.dumps(report, allow_nan=False))


if __name__ == "__main__":
    main()
