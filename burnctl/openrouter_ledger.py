"""OpenRouter request-level usage ledger helpers.

The ledger is an append-only JSONL file written by local instrumentation
such as the burnctl OpenRouter proxy. Each line represents one completed
request with enough metadata to support realtime usage reporting.
"""

import json
import os
from datetime import datetime

LEDGER_FILE = os.path.join(
    os.path.expanduser("~"), ".local", "share", "burnctl", "openrouter-usage.jsonl",
)

_MAX_LEDGER_BYTES = 100 * 1024 * 1024


def _parse_ts(ts_str):
    if not ts_str or not isinstance(ts_str, str):
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def parse_entry(line):
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None

    ts = _parse_ts(obj.get("ts"))
    if ts is None:
        return None

    try:
        return {
            "ts": ts,
            "provider": str(obj.get("provider", "openrouter")),
            "model": str(obj.get("model") or obj.get("model_name") or "unknown"),
            "request_id": str(obj.get("request_id", "") or obj.get("generation_id", "")),
            "source": str(obj.get("source", "")),
            "input_tokens": int(obj.get("input_tokens", 0)),
            "output_tokens": int(obj.get("output_tokens", 0)),
            "reasoning_tokens": int(obj.get("reasoning_tokens", 0)),
            "cost": float(obj.get("cost", 0.0)),
        }
    except (TypeError, ValueError):
        return None


def load_entries(filepath=None):
    filepath = filepath or LEDGER_FILE
    if not os.path.isfile(filepath):
        return []

    try:
        if os.path.getsize(filepath) > _MAX_LEDGER_BYTES:
            return []
    except OSError:
        return []

    out = []
    try:
        with open(filepath, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                entry = parse_entry(line)
                if entry is not None:
                    out.append(entry)
    except OSError:
        return []
    return out


def append_entry(entry, filepath=None):
    filepath = filepath or LEDGER_FILE
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    record = dict(entry)
    ts = record.get("ts")
    if isinstance(ts, datetime):
        record["ts"] = ts.isoformat()
    with open(filepath, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
