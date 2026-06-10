"""Tests for burnctl.openrouter_proxy helpers."""

from burnctl.openrouter_proxy import _parse_json_usage, _parse_sse_line


def _replay_sse(lines):
    """Replay SSE lines through _parse_sse_line the same way _forward does."""
    record = None
    model = "unknown"
    request_id = ""
    for raw in lines:
        maybe, model, request_id = _parse_sse_line(raw, model, request_id)
        if maybe:
            record = maybe
    return record


class TestParseJsonUsage:
    def test_extracts_non_stream_usage(self):
        payload = {
            "id": "gen_123",
            "model": "minimax/minimax-m2.7",
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 2000,
                "completion_tokens_details": {"reasoning_tokens": 300},
            },
            "cost": 0.42,
        }
        record = _parse_json_usage(payload)
        assert record is not None
        assert record["request_id"] == "gen_123"
        assert record["model"] == "minimax/minimax-m2.7"
        assert record["input_tokens"] == 1000
        assert record["output_tokens"] == 2300
        assert record["reasoning_tokens"] == 300
        assert record["cost"] == 0.42

    def test_returns_none_when_usage_missing(self):
        # An SSE delta chunk without a usage object must not overwrite the
        # real ledger record with zeros.
        payload = {"id": "gen_x", "choices": [{"delta": {"content": "hi"}}]}
        assert _parse_json_usage(payload) is None

    def test_returns_none_when_usage_is_empty(self):
        assert _parse_json_usage({"usage": {}}) is None

    def test_returns_none_when_usage_wrong_type(self):
        assert _parse_json_usage({"usage": "not a dict"}) is None

    def test_returns_none_when_payload_not_dict(self):
        assert _parse_json_usage(None) is None
        assert _parse_json_usage([]) is None

    def test_top_level_reasoning_tokens_used_without_details(self):
        # Regression: a provider that exposes reasoning_tokens at the top
        # level (no completion_tokens_details wrapper) must still be honoured.
        payload = {
            "id": "gen_flat",
            "model": "some/model",
            "usage": {
                "prompt_tokens": 50,
                "completion_tokens": 100,
                "reasoning_tokens": 25,
            },
        }
        record = _parse_json_usage(payload)
        assert record is not None
        assert record["reasoning_tokens"] == 25
        assert record["output_tokens"] == 125

    def test_cost_falls_back_to_top_level(self):
        payload = {
            "id": "gen_cost",
            "model": "m",
            "usage": {"prompt_tokens": 1, "completion_tokens": 2},
            "cost": 1.5,
        }
        record = _parse_json_usage(payload)
        assert record is not None
        assert record["cost"] == 1.5

    def test_usage_nested_cost_takes_precedence(self):
        payload = {
            "id": "g",
            "model": "m",
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "cost": 0.7},
            "cost": 9.9,
        }
        record = _parse_json_usage(payload)
        assert record["cost"] == 0.7


class TestParseSseStream:
    def test_extracts_usage_from_final_event(self):
        lines = [
            (
                b'data: {"id":"gen_123","model":"minimax/minimax-m2.7",'
                b'"choices":[{"delta":{"content":"hi"}}]}\n'
            ),
            b'\n',
            (
                b'data: {"usage":{"prompt_tokens":1000,"completion_tokens":2000,'
                b'"completion_tokens_details":{"reasoning_tokens":300}}}\n'
            ),
            b'\n',
            b'data: [DONE]\n',
        ]
        record = _replay_sse(lines)
        assert record is not None
        assert record["request_id"] == "gen_123"
        assert record["model"] == "minimax/minimax-m2.7"
        assert record["input_tokens"] == 1000
        assert record["output_tokens"] == 2300
        assert record["reasoning_tokens"] == 300

    def test_trailing_empty_chunk_does_not_overwrite_usage(self):
        # Regression: after the usage frame, a downstream empty-delta frame
        # must not replace the good record with zeros.
        lines = [
            b'data: {"id":"g","model":"m","choices":[{"delta":{"content":"a"}}]}\n',
            b'data: {"usage":{"prompt_tokens":10,"completion_tokens":20}}\n',
            b'data: {"choices":[{"delta":{}}]}\n',
            b'data: [DONE]\n',
        ]
        record = _replay_sse(lines)
        assert record is not None
        assert record["input_tokens"] == 10
        assert record["output_tokens"] == 20

    def test_no_usage_frame_returns_none(self):
        lines = [
            b'data: {"id":"g","model":"m","choices":[{"delta":{"content":"a"}}]}\n',
            b'data: [DONE]\n',
        ]
        assert _replay_sse(lines) is None

    def test_malformed_json_chunk_skipped(self):
        lines = [
            b'data: not-json\n',
            b'data: {"usage":{"prompt_tokens":1,"completion_tokens":2}}\n',
        ]
        record = _replay_sse(lines)
        assert record is not None
        assert record["input_tokens"] == 1
