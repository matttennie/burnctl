"""OpenRouter proxy that logs per-request usage to the burnctl ledger."""

import json
import os
import signal
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from burnctl.openrouter_ledger import append_entry

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8765
_UPSTREAM_BASE = "https://openrouter.ai/api/v1"
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def _now_utc():
    return datetime.now(timezone.utc)


def _parse_json_usage(payload):
    """Extract a ledger record from a non-streaming JSON payload."""
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage", {})
    if not isinstance(usage, dict):
        return None
    prompt = int(usage.get("prompt_tokens", 0) or 0)
    completion = int(usage.get("completion_tokens", 0) or 0)
    reasoning = int(
        usage.get("reasoning_tokens", 0)
        or usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0)
        if isinstance(usage.get("completion_tokens_details"), dict)
        else 0
    )
    cost = payload.get("usage", {}).get("cost")
    if cost is None:
        cost = payload.get("cost")
    return {
        "ts": _now_utc(),
        "provider": "openrouter",
        "model": str(payload.get("model", "unknown")),
        "request_id": str(payload.get("id", "") or payload.get("generation_id", "")),
        "input_tokens": prompt,
        "output_tokens": completion + reasoning,
        "reasoning_tokens": reasoning,
        "cost": float(cost or 0.0),
    }


def _parse_sse_line(raw, current_model="unknown", current_id=""):
    """Extract usage or model info from a single SSE data line."""
    if not raw.startswith(b"data: "):
        return None, current_model, current_id
    data = raw[6:].strip()
    if not data or data == b"[DONE]":
        return None, current_model, current_id
    try:
        obj = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, current_model, current_id

    if not isinstance(obj, dict):
        return None, current_model, current_id

    model = str(obj.get("model", current_model))
    request_id = str(obj.get("id", "") or obj.get("generation_id", "") or current_id)
    maybe = _parse_json_usage(obj)
    if maybe is not None:
        maybe["model"] = model
        if request_id:
            maybe["request_id"] = request_id
        return maybe, model, request_id

    return None, model, request_id


def _parse_sse_usage(lines):
    """Extract a ledger record from streamed SSE lines if usage appears.
    (Kept for backward compatibility and testing).
    """
    record = None
    model = "unknown"
    request_id = ""
    for raw in lines:
        maybe, model, request_id = _parse_sse_line(raw, model, request_id)
        if maybe:
            record = maybe
    return record


class _ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    upstream_base = _UPSTREAM_BASE
    ledger_path = None

    def do_GET(self):
        self._forward()

    def do_POST(self):
        self._forward()

    def do_PUT(self):
        self._forward()

    def do_DELETE(self):
        self._forward()

    def do_PATCH(self):
        self._forward()

    def log_message(self, fmt, *args):
        print("burnctl proxy:", fmt % args, file=sys.stderr)

    def _forward(self):
        body = None
        length = self.headers.get("Content-Length")
        if length:
            body = self.rfile.read(int(length))

        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in _HOP_BY_HOP and key.lower() != "host"
        }
        req = urllib.request.Request(
            self.upstream_base + self.path,
            data=body,
            headers=headers,
            method=self.command,
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                content_type = resp.headers.get("Content-Type", "")
                self.send_response(resp.status)
                for key, value in resp.headers.items():
                    if key.lower() in _HOP_BY_HOP or key.lower() == "content-length":
                        continue
                    self.send_header(key, value)
                self.end_headers()

                ledger_record = None
                if "text/event-stream" in content_type:
                    model = "unknown"
                    request_id = ""
                    while True:
                        chunk = resp.readline()
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
                        maybe, model, request_id = _parse_sse_line(chunk, model, request_id)
                        if maybe:
                            ledger_record = maybe
                else:
                    payload = resp.read()
                    self.wfile.write(payload)
                    try:
                        obj = json.loads(payload.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        obj = None
                    ledger_record = _parse_json_usage(obj)

                if ledger_record is not None:
                    ledger_record["source"] = "openrouter-proxy"
                    append_entry(ledger_record, filepath=self.ledger_path)
        except urllib.error.HTTPError as err:
            payload = err.read()
            self.send_response(err.code)
            for key, value in err.headers.items():
                if key.lower() in _HOP_BY_HOP or key.lower() == "content-length":
                    continue
                self.send_header(key, value)
            self.end_headers()
            if payload:
                self.wfile.write(payload)
        except urllib.error.URLError as err:
            self.send_error(502, "Upstream OpenRouter request failed: %s" % err)


def run_proxy(host=None, port=None, ledger_path=None):
    host = host or os.environ.get("BURNCTL_PROXY_HOST", _DEFAULT_HOST)
    port = int(port or os.environ.get("BURNCTL_PROXY_PORT", _DEFAULT_PORT))
    ledger_path = ledger_path or os.environ.get("BURNCTL_OPENROUTER_LEDGER", "")
    _ProxyHandler.ledger_path = ledger_path or None

    server = ThreadingHTTPServer((host, port), _ProxyHandler)

    # Handle SIGTERM for graceful shutdown (e.g. from launchd/systemd)
    def handle_sigterm(signum, frame):
        print("burnctl proxy: received SIGTERM, shutting down...", file=sys.stderr)
        server.shutdown()

    signal.signal(signal.SIGTERM, handle_sigterm)

    print("burnctl OpenRouter proxy listening on http://%s:%s" % (host, port))
    if ledger_path:
        print("ledger:", ledger_path)
    else:
        print("ledger: default burnctl OpenRouter ledger path")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
