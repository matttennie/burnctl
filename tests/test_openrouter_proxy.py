"""Tests for burnctl.openrouter_proxy helpers."""

from burnctl.openrouter_proxy import _parse_json_usage, _parse_sse_usage


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


class TestParseSseUsage:
    def test_extracts_usage_from_final_event(self):
        lines = [
            b'data: {"id":"gen_123","model":"minimax/minimax-m2.7","choices":[{"delta":{"content":"hi"}}]}\n',
            b'\n',
            b'data: {"usage":{"prompt_tokens":1000,"completion_tokens":2000,"completion_tokens_details":{"reasoning_tokens":300}}}\n',
            b'\n',
            b'data: [DONE]\n',
        ]
        record = _parse_sse_usage(lines)
        assert record is not None
        assert record["request_id"] == "gen_123"
        assert record["model"] == "minimax/minimax-m2.7"
        assert record["input_tokens"] == 1000
        assert record["output_tokens"] == 2300
        assert record["reasoning_tokens"] == 300
