"""Integration tests with realistic fixture data for each collector.

These tests verify the full parsing pipeline works with real data formats,
not just mocked calls.  Each test creates temporary fixture files that
mirror the actual on-disk format produced by the respective AI coding
agent, then runs the collector's ``get_stats()`` against them.
"""

import csv
import json
import os
import re
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from burnctl.collectors.claude import ClaudeCollector
from burnctl.collectors.gemini import GeminiCollector
from burnctl.collectors.codex import CodexCollector
from burnctl.collectors.aider import AiderCollector
from burnctl.collectors.api_usage import ApiUsageCollector
from burnctl.report import (
    aggregate_stats,
    render_json,
    render_compact,
    render_accessible,
    render_full,
    export_csv,
)


# ── Helpers ──────────────────────────────────────────────────────────


_ANSI_RE = re.compile(r"\033\[[0-9;]*m")
_BOX_CHARS = set("\u2550\u2551\u2554\u2557\u255a\u255d\u2560\u2563\u255f\u2562\u2500")


def _strip_ansi(text):
    return _ANSI_RE.sub("", text)


# ── 1. Claude collector with realistic stats-cache.json ──────────────


class TestClaudeIntegration:
    """Full pipeline test for the Claude collector."""

    FIXTURE = {
        "firstSessionDate": "2026-01-10T08:30:00Z",
        "totalMessages": 29328,
        "totalSessions": 95,
        "modelUsage": {
            "claude-opus-4-5": {
                "inputTokens": 500000,
                "outputTokens": 473958,
                "cacheReadInputTokens": 100000,
                "cacheCreationInputTokens": 50000,
            },
            "claude-sonnet-4-5": {
                "inputTokens": 30000,
                "outputTokens": 22747,
                "cacheReadInputTokens": 5000,
                "cacheCreationInputTokens": 2000,
            },
            "claude-opus-4-6": {
                "inputTokens": 1200000,
                "outputTokens": 1034712,
                "cacheReadInputTokens": 200000,
                "cacheCreationInputTokens": 80000,
            },
        },
        "dailyActivity": [
            {
                "date": "2026-03-11",
                "messageCount": 965,
                "sessionCount": 3,
                "toolCallCount": 200,
            },
            {
                "date": "2026-03-12",
                "messageCount": 966,
                "sessionCount": 2,
                "toolCallCount": 261,
            },
        ],
        "dailyModelTokens": [
            {
                "date": "2026-03-11",
                "tokensByModel": {"claude-opus-4-6": 92540},
            },
            {
                "date": "2026-03-12",
                "tokensByModel": {"claude-opus-4-6": 92541},
            },
        ],
    }

    def _write_fixture(self, tmp_path):
        stats_file = tmp_path / "stats-cache.json"
        stats_file.write_text(json.dumps(self.FIXTURE))
        return str(stats_file)

    def test_full_parse(self, tmp_path):
        fpath = self._write_fixture(tmp_path)

        start = datetime(2026, 3, 10)
        end = datetime(2026, 4, 10)
        ref_date = datetime(2026, 3, 13)

        with patch("burnctl.collectors.claude.STATS_FILE", fpath):
            collector = ClaudeCollector()
            stats = collector.get_stats(start, end, ref_date)

        assert stats is not None

        # Messages: 965 + 966 = 1931
        assert stats["messages"] == 1931

        # Sessions: 3 + 2 = 5
        assert stats["sessions"] == 5

        # Output tokens: 92540 + 92541 = 185081
        assert stats["output_tokens"] == 185081

        # Period cost > 0 (output tokens priced at opus-4-6 rate)
        assert stats["period_cost"] > 0

        # All-time cost > 0 (computed from full modelUsage)
        assert stats["alltime_cost"] > 0

        # Model usage is period-scoped (only models in dailyModelTokens)
        assert len(stats["model_usage"]) == 1
        assert "claude-opus-4-6" in stats["model_usage"]
        assert stats["model_usage"]["claude-opus-4-6"]["outputTokens"] == 185081

        # Tool calls: 200 + 261 = 461
        assert stats["tool_calls"] == 461

        # First session date
        assert stats["first_session"] == "2026-01-10"

        # Totals from raw data
        assert stats["total_messages"] == 29328
        assert stats["total_sessions"] == 95

    def test_alltime_cost_calculation(self, tmp_path):
        """Verify the all-time cost is plausible given known pricing."""
        fpath = self._write_fixture(tmp_path)

        with patch("burnctl.collectors.claude.STATS_FILE", fpath):
            collector = ClaudeCollector()
            stats = collector.get_stats(
                datetime(2026, 3, 10),
                datetime(2026, 4, 10),
                datetime(2026, 3, 13),
            )

        # opus-4-5: 500k*5/1M + 473958*25/1M + 100k*0.5/1M + 50k*6.25/1M
        #         = 2.50 + 11.849 + 0.05 + 0.3125 = ~14.71
        # sonnet-4-5: 30k*1/1M + 22747*5/1M + 5k*0.1/1M + 2k*1.25/1M
        #           = 0.03 + 0.1137 + 0.0005 + 0.0025 = ~0.147
        # opus-4-6: 1200k*5/1M + 1034712*25/1M + 200k*0.5/1M + 80k*6.25/1M
        #         = 6.0 + 25.868 + 0.10 + 0.50 = ~32.47
        # Total ~ 47.32
        assert 40 < stats["alltime_cost"] < 55

    def test_period_cost_uses_output_pricing(self, tmp_path):
        """Period cost uses effective rate derived from alltime data."""
        fpath = self._write_fixture(tmp_path)

        with patch("burnctl.collectors.claude.STATS_FILE", fpath):
            stats = ClaudeCollector().get_stats(
                datetime(2026, 3, 10),
                datetime(2026, 4, 10),
                datetime(2026, 3, 13),
            )

        # 185081 output tokens at effective rate (includes input + cache costs)
        # Effective rate is higher than raw output rate of $25/M
        assert 4.0 < stats["period_cost"] < 8.0


# ── 2. Gemini collector with realistic session files ─────────────────


class TestGeminiIntegration:
    """Full pipeline test for the Gemini collector."""

    @staticmethod
    def _make_session(
        session_id, start_time, messages, project_hash="deadbeef"
    ):
        return {
            "sessionId": session_id,
            "projectHash": project_hash,
            "startTime": start_time,
            "lastUpdated": start_time,
            "messages": messages,
        }

    def _build_sessions(self, tmp_path):
        """Create two session files: one in-period, one outside."""
        chat_dir = tmp_path / "tmp" / "abc123" / "chats"
        chat_dir.mkdir(parents=True)

        # Session 1: in period (2026-03-11)
        in_period = self._make_session(
            "sess-in",
            "2026-03-11T14:30:00Z",
            [
                {
                    "id": "1",
                    "timestamp": "2026-03-11T14:30:00Z",
                    "type": "user",
                    "content": "hello",
                },
                {
                    "id": "2",
                    "timestamp": "2026-03-11T14:30:05Z",
                    "type": "gemini",
                    "content": "response",
                    "model": "gemini-2.5-flash",
                    "tokens": {
                        "input": 8797,
                        "output": 89,
                        "cached": 0,
                        "thoughts": 203,
                        "tool": 0,
                        "total": 9089,
                    },
                    "toolCalls": [
                        {"name": "read_file", "args": {"path": "foo.py"}},
                    ],
                },
                {
                    "id": "3",
                    "timestamp": "2026-03-11T14:31:00Z",
                    "type": "user",
                    "content": "thanks",
                },
                {
                    "id": "4",
                    "timestamp": "2026-03-11T14:31:05Z",
                    "type": "gemini",
                    "content": "response2",
                    "model": "gemini-2.5-flash",
                    "tokens": {
                        "input": 9000,
                        "output": 150,
                        "cached": 100,
                        "thoughts": 50,
                        "tool": 0,
                        "total": 9300,
                    },
                },
            ],
        )

        # Session 2: outside period (2025-01-15)
        out_of_period = self._make_session(
            "sess-out",
            "2025-01-15T10:00:00Z",
            [
                {
                    "id": "10",
                    "timestamp": "2025-01-15T10:00:00Z",
                    "type": "user",
                    "content": "old question",
                },
                {
                    "id": "11",
                    "timestamp": "2025-01-15T10:00:05Z",
                    "type": "gemini",
                    "content": "old answer",
                    "model": "gemini-2.5-flash",
                    "tokens": {
                        "input": 5000,
                        "output": 200,
                        "cached": 0,
                        "thoughts": 0,
                        "tool": 0,
                        "total": 5200,
                    },
                },
            ],
        )

        (chat_dir / "session-001.json").write_text(json.dumps(in_period))
        (chat_dir / "session-002.json").write_text(json.dumps(out_of_period))

        return str(chat_dir / "session-*.json")

    def test_full_parse(self, tmp_path):
        pattern = self._build_sessions(tmp_path)

        start = datetime(2026, 3, 10)
        end = datetime(2026, 4, 10)
        ref_date = datetime(2026, 3, 13)

        with patch(
            "burnctl.collectors.gemini._CHAT_PATTERN", pattern
        ):
            collector = GeminiCollector()
            stats = collector.get_stats(start, end, ref_date)

        assert stats is not None

        # In-period user messages: 2
        assert stats["messages"] == 2

        # Period sessions: 1 (only the in-period session)
        assert stats["sessions"] == 1

        # Output tokens: 89 + 150 = 239
        assert stats["output_tokens"] == 239

        # Tool calls: 1 (from the first gemini response)
        assert stats["tool_calls"] == 1

        # Model usage should have gemini-2.5-flash
        assert "gemini-2.5-flash" in stats["model_usage"]
        model = stats["model_usage"]["gemini-2.5-flash"]
        assert model["outputTokens"] == 239
        assert model["inputTokens"] == 8797 + 8900  # non-cached only

        # All-time: 2 sessions total, 3 user messages total
        assert stats["total_sessions"] == 2
        assert stats["total_messages"] == 3

        # First session should be the earlier one
        assert stats["first_session"] == "2025-01-15"

        # Costs should be positive
        assert stats["period_cost"] > 0
        assert stats["alltime_cost"] > 0
        assert stats["alltime_cost"] > stats["period_cost"]

    def test_period_vs_alltime(self, tmp_path):
        """Alltime cost includes out-of-period sessions."""
        pattern = self._build_sessions(tmp_path)

        with patch(
            "burnctl.collectors.gemini._CHAT_PATTERN", pattern
        ):
            stats = GeminiCollector().get_stats(
                datetime(2026, 3, 10),
                datetime(2026, 4, 10),
                datetime(2026, 3, 13),
            )

        # alltime_cost covers both sessions; period_cost covers only one
        assert stats["alltime_cost"] > stats["period_cost"]


# ── 3. Codex collector with realistic JSONL session files ────────────


class TestCodexIntegration:
    """Full pipeline test for the Codex collector."""

    @staticmethod
    def _build_session_lines(ts_base, session_id, model="gpt-5.3-codex"):
        """Build a list of JSONL lines for a single Codex session."""
        lines = [
            json.dumps({
                "timestamp": ts_base,
                "type": "session_meta",
                "payload": {
                    "id": session_id,
                    "timestamp": ts_base,
                },
            }),
            json.dumps({
                "timestamp": ts_base,
                "type": "turn_context",
                "payload": {"model": model},
            }),
            json.dumps({
                "timestamp": ts_base,
                "type": "event_msg",
                "payload": {"type": "user_message"},
            }),
            json.dumps({
                "timestamp": ts_base,
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 9246,
                            "cached_input_tokens": 7552,
                            "output_tokens": 158,
                            "reasoning_output_tokens": 64,
                            "total_tokens": 9404,
                        },
                    },
                },
            }),
            json.dumps({
                "timestamp": ts_base,
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                },
            }),
        ]
        return lines

    def _build_sessions(self, tmp_path):
        """Create JSONL session files: one in-period, one outside."""
        sessions_dir = tmp_path / "sessions"

        # In-period session: 2026-03-11
        in_dir = sessions_dir / "2026" / "03" / "11"
        in_dir.mkdir(parents=True)
        in_lines = self._build_session_lines(
            "2026-03-11T14:00:00Z", "sess-in",
        )
        (in_dir / "rollout-in.jsonl").write_text("\n".join(in_lines) + "\n")

        # Out-of-period session: 2025-06-15
        out_dir = sessions_dir / "2025" / "06" / "15"
        out_dir.mkdir(parents=True)
        out_lines = self._build_session_lines(
            "2025-06-15T10:00:00Z", "sess-out",
        )
        (out_dir / "rollout-out.jsonl").write_text(
            "\n".join(out_lines) + "\n",
        )

        return str(sessions_dir)

    def test_full_parse(self, tmp_path):
        sessions_dir = self._build_sessions(tmp_path)

        start = datetime(2026, 3, 10, tzinfo=timezone.utc)
        end = datetime(2026, 4, 10, tzinfo=timezone.utc)
        ref_date = datetime(2026, 3, 13, tzinfo=timezone.utc)

        with patch("burnctl.collectors.codex.SESSIONS_DIR", sessions_dir), \
             patch("burnctl.collectors.codex.HISTORY_FILE", "/nonexistent"):
            collector = CodexCollector()
            stats = collector.get_stats(start, end, ref_date)

        assert stats is not None

        # Period: 1 user message from the in-period session
        assert stats["messages"] == 1

        # Period sessions: 1
        assert stats["sessions"] == 1

        # Output tokens: 158
        assert stats["output_tokens"] == 158

        # Tool calls: 1 (function_call response_item)
        assert stats["tool_calls"] == 1

        # Model usage should have gpt-5.3-codex
        assert "gpt-5.3-codex" in stats["model_usage"]
        model_u = stats["model_usage"]["gpt-5.3-codex"]
        assert model_u["outputTokens"] == 158
        assert model_u["inputTokens"] == 1694  # non-cached only (9246 - 7552)

        # Period cost > 0
        assert stats["period_cost"] > 0

        # All-time: 2 sessions total
        assert stats["total_sessions"] == 2
        assert stats["total_messages"] == 2

        # First session is the earlier one
        assert stats["first_session"] == "2025-06-15"

        # Alltime cost covers both sessions
        assert stats["alltime_cost"] > stats["period_cost"]

    def test_timezone_handling(self, tmp_path):
        """Ensure naive datetimes get promoted to UTC for comparison."""
        sessions_dir = self._build_sessions(tmp_path)

        # Pass naive datetimes -- the collector should handle them
        start = datetime(2026, 3, 10)
        end = datetime(2026, 4, 10)
        ref_date = datetime(2026, 3, 13)

        with patch("burnctl.collectors.codex.SESSIONS_DIR", sessions_dir), \
             patch("burnctl.collectors.codex.HISTORY_FILE", "/nonexistent"):
            stats = CodexCollector().get_stats(start, end, ref_date)

        assert stats is not None
        assert stats["sessions"] == 1


# ── 4. Aider collector with realistic history file ───────────────────


class TestAiderIntegration:
    """Full pipeline test for the Aider collector."""

    HISTORY_CONTENT = """\
# aider chat started at 2026-03-11 14:00:00

> /ask how do I fix this?

Some response about fixing the issue.

Tokens: 1.5k sent, 2.1k received. Cost: $0.03

> /code fix it

Applied changes to foo.py

Tokens: 3.2k sent, 5.7k received. Cost: $0.12
Tokens: 500 sent, 200 received. Cost: $0.01
"""

    def test_full_parse(self, tmp_path):
        history_file = tmp_path / ".aider.chat.history.md"
        history_file.write_text(self.HISTORY_CONTENT)

        # Touch the file to ensure mtime is recent
        now_ts = datetime.now().timestamp()
        os.utime(str(history_file), (now_ts, now_ts))

        start = datetime(2026, 3, 1)
        end = datetime(2026, 4, 1)
        ref_date = datetime(2026, 3, 13)

        with patch(
            "burnctl.collectors.aider._find_history_files",
            return_value=[str(history_file)],
        ):
            collector = AiderCollector()
            stats = collector.get_stats(start, end, ref_date)

        assert stats is not None

        # 3 cost lines = 3 messages
        assert stats["messages"] == 3

        # Output tokens: 2100 + 5700 + 200 = 8000
        assert stats["output_tokens"] == 8000

        # Period cost: 0.03 + 0.12 + 0.01 = 0.16
        assert stats["period_cost"] == pytest.approx(0.16)

        # Aider sets alltime_cost == period_cost
        assert stats["alltime_cost"] == pytest.approx(0.16)

    def test_old_file_skipped(self, tmp_path):
        """Files with mtime before the period start are skipped."""
        history_file = tmp_path / ".aider.chat.history.md"
        history_file.write_text(self.HISTORY_CONTENT)

        # Set mtime to well before the period
        old_ts = datetime(2020, 1, 1).timestamp()
        os.utime(str(history_file), (old_ts, old_ts))

        start = datetime(2026, 3, 1)
        end = datetime(2026, 4, 1)

        with patch(
            "burnctl.collectors.aider._find_history_files",
            return_value=[str(history_file)],
        ):
            stats = AiderCollector().get_stats(
                start, end, datetime(2026, 3, 13),
            )

        # File was too old, so no matches
        assert stats is None


# ── 5. API usage collector with realistic usage.jsonl ───────────────────


class TestApiUsageIntegration:
    """Full pipeline test for the API usage collector."""

    @staticmethod
    def _make_entry(
        ts, provider="openrouter", model_id="anthropic/claude-opus-4-6",
        model_name="Claude Opus 4.6", input_tokens=1000, output_tokens=500,
        cost=0.01, node_id="node-1", estimated=False,
    ):
        return json.dumps({
            "ts": ts,
            "provider": provider,
            "model_id": model_id,
            "model_name": model_name,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost": cost,
            "node_id": node_id,
            "estimated": estimated,
        })

    def _write_fixture(self, tmp_path):
        """Write a realistic usage.jsonl with in-period and out-of-period entries."""
        lines = [
            # In-period: 2026-03-11
            self._make_entry(
                "2026-03-11T10:00:00Z", provider="openrouter",
                model_id="anthropic/claude-opus-4-6", model_name="Claude Opus 4.6",
                input_tokens=1500, output_tokens=800, cost=0.024,
                node_id="node-abc",
            ),
            # In-period: 2026-03-11, different model & node
            self._make_entry(
                "2026-03-11T14:00:00Z", provider="huggingface",
                model_id="meta/llama-3-70b", model_name="Llama 3 70B",
                input_tokens=2000, output_tokens=1200, cost=0.005,
                node_id="node-def",
            ),
            # In-period: 2026-03-12
            self._make_entry(
                "2026-03-12T09:00:00Z", provider="openrouter",
                model_id="anthropic/claude-opus-4-6", model_name="Claude Opus 4.6",
                input_tokens=1000, output_tokens=600, cost=0.018,
                node_id="node-abc", estimated=True,
            ),
            # Out-of-period: 2025-06-15
            self._make_entry(
                "2025-06-15T10:00:00Z", provider="openrouter",
                model_id="anthropic/claude-opus-4-6", model_name="Claude Opus 4.6",
                input_tokens=500, output_tokens=300, cost=0.008,
                node_id="node-old",
            ),
        ]
        usage_file = tmp_path / "usage.jsonl"
        usage_file.write_text("\n".join(lines) + "\n")
        return str(usage_file)

    def test_full_parse(self, tmp_path):
        fpath = self._write_fixture(tmp_path)

        start = datetime(2026, 3, 10)
        end = datetime(2026, 4, 10)
        ref_date = datetime(2026, 3, 13)

        collector = ApiUsageCollector("openrouter", "OpenRouter", usage_file=fpath)
        stats = collector.get_stats(start, end, ref_date)

        assert stats is not None

        # Period messages: 2 in-period openrouter entries (huggingface excluded)
        assert stats["messages"] == 2

        # Sessions: 1 distinct node_id in period for openrouter (node-abc)
        assert stats["sessions"] == 1

        # Output tokens: 800 + 600 = 1400 (openrouter only)
        assert stats["output_tokens"] == 1400

        # Period cost: 0.024 + 0.018 = 0.042 (openrouter only)
        assert stats["period_cost"] == pytest.approx(0.042)

        # All-time cost: 0.024 + 0.018 + 0.008 = 0.050 (openrouter only)
        assert stats["alltime_cost"] == pytest.approx(0.050)

        # Model usage keyed by model_name: only openrouter models in period
        assert "Claude Opus 4.6" in stats["model_usage"]
        assert len(stats["model_usage"]) == 1

        opus = stats["model_usage"]["Claude Opus 4.6"]
        assert opus["inputTokens"] == 1500 + 1000
        assert opus["outputTokens"] == 800 + 600

        # First session: earliest openrouter entry across all time
        assert stats["first_session"] == "2025-06-15"

        # Totals (openrouter only)
        assert stats["total_messages"] == 3
        assert stats["total_sessions"] == 2  # node-abc, node-old

    def test_period_vs_alltime(self, tmp_path):
        """alltime_cost includes out-of-period entry."""
        fpath = self._write_fixture(tmp_path)

        collector = ApiUsageCollector("openrouter", "OpenRouter", usage_file=fpath)
        stats = collector.get_stats(
            datetime(2026, 3, 10),
            datetime(2026, 4, 10),
            datetime(2026, 3, 13),
        )

        assert stats["alltime_cost"] > stats["period_cost"]

    def test_provider_filtering(self, tmp_path):
        """A collector only counts entries matching its provider_id."""
        lines = [
            self._make_entry(
                "2026-03-11T10:00:00Z", provider="openrouter",
                model_id="model-a", model_name="Model A", cost=0.01,
            ),
            self._make_entry(
                "2026-03-11T11:00:00Z", provider="huggingface",
                model_id="model-b", model_name="Model B", cost=0.02,
            ),
            self._make_entry(
                "2026-03-11T12:00:00Z", provider="openrouter",
                model_id="model-c", model_name="Model C", cost=0.03,
            ),
        ]
        usage_file = tmp_path / "usage.jsonl"
        usage_file.write_text("\n".join(lines) + "\n")

        collector = ApiUsageCollector(
            "openrouter", "OpenRouter", usage_file=str(usage_file),
        )
        stats = collector.get_stats(
            datetime(2026, 3, 10),
            datetime(2026, 4, 10),
            datetime(2026, 3, 13),
        )

        assert stats is not None
        # Only the 2 openrouter entries are counted
        assert stats["messages"] == 2
        assert stats["period_cost"] == pytest.approx(0.04)
        # huggingface entry excluded
        assert "Model B" not in stats["model_usage"]

    def test_malformed_lines_skipped(self, tmp_path):
        """Malformed JSON lines are skipped; valid entries still counted."""
        valid_entry = self._make_entry(
            "2026-03-11T10:00:00Z", cost=0.05,
        )
        lines = [
            valid_entry,
            "{invalid json",
            "",
            "not json at all",
            '{"ts": "2026-03-11T11:00:00Z"}',  # missing provider & model_id
            self._make_entry("2026-03-12T10:00:00Z", cost=0.03),
        ]
        usage_file = tmp_path / "usage.jsonl"
        usage_file.write_text("\n".join(lines) + "\n")

        collector = ApiUsageCollector(
            "openrouter", "OpenRouter", usage_file=str(usage_file),
        )
        stats = collector.get_stats(
            datetime(2026, 3, 10),
            datetime(2026, 4, 10),
            datetime(2026, 3, 13),
        )

        assert stats is not None
        # Only 2 valid entries
        assert stats["messages"] == 2
        assert stats["period_cost"] == pytest.approx(0.08)


# ── 6. Full pipeline integration test ────────────────────────────────


class _FakeCollector:
    """Minimal collector that returns canned data."""

    def __init__(self, agent_id, agent_name, stats_data):
        self._id = agent_id
        self._name = agent_name
        self._stats = stats_data

    @property
    def id(self):
        return self._id

    @property
    def name(self):
        return self._name

    def is_available(self):
        return True

    def get_stats(self, start, end, ref_date):
        return self._stats

    def get_plan_info(self, config):
        return {
            "plan_name": "max5x",
            "plan_price": 100,
            "billing_day": config.get("billing_day", 10),
            "interval": "mo",
        }

    def get_upgrade_url(self):
        return ""


def _make_fake_stats(
    messages=100, sessions=5, output_tokens=50000,
    period_cost=12.50, alltime_cost=300.0,
):
    return {
        "messages": messages,
        "sessions": sessions,
        "input_tokens": 25000,
        "output_tokens": output_tokens,
        "period_cost": period_cost,
        "alltime_cost": alltime_cost,
        "model_usage": {
            "test-model": {
                "inputTokens": 100000,
                "outputTokens": output_tokens,
            },
        },
        "first_session": "2026-01-01",
        "total_messages": 1000,
        "total_sessions": 50,
        "tool_calls": 200,
    }


class TestFullPipeline:
    """Test aggregate_stats + all render functions."""

    def _get_aggregate(self):
        collectors = [
            _FakeCollector("agent_a", "Agent A", _make_fake_stats()),
            _FakeCollector(
                "agent_b", "Agent B",
                _make_fake_stats(
                    messages=200, period_cost=25.0, alltime_cost=500.0,
                ),
            ),
        ]
        config = {"billing_day": 10}
        return aggregate_stats(
            collectors, config, ref_date=datetime(2026, 3, 13),
        )

    def test_aggregate_structure(self):
        result = self._get_aggregate()
        assert "agents" in result
        assert "total_period_cost" in result
        assert "today" in result
        assert len(result["agents"]) == 2
        assert result["total_period_cost"] == pytest.approx(37.50)

    def test_render_json(self):
        result = self._get_aggregate()
        output = render_json(result)

        # Must be valid JSON
        parsed = json.loads(output)
        assert "agents" in parsed
        assert len(parsed["agents"]) == 2
        assert parsed["agents"][0]["id"] == "agent_a"
        assert parsed["agents"][1]["id"] == "agent_b"

    def test_render_compact(self):
        result = self._get_aggregate()
        output = render_compact(result)

        # Single line with pipe separators
        assert "|" in output
        assert "Agent A" in output
        assert "Agent B" in output
        assert "Total" in output

    def test_render_accessible(self):
        result = self._get_aggregate()
        output = render_accessible(result)

        # No ANSI escape sequences
        assert "\033[" not in output

        # No box-drawing characters
        for ch in _BOX_CHARS:
            assert ch not in output

        # Contains expected content
        assert "Agent A" in output
        assert "Agent B" in output
        assert "System total" in output

    def test_render_full(self):
        result = self._get_aggregate()
        output = render_full(result, use_color=False)

        # Should contain box-drawing characters
        raw = _strip_ansi(output)
        has_box = any(ch in raw for ch in _BOX_CHARS)
        assert has_box, "render_full should contain box-drawing characters"

        assert "Agent A" in raw
        assert "Agent B" in raw

    def test_aggregate_with_api_usage_collector(self):
        """API usage collector integrates with the multi-agent pipeline."""
        collectors = [
            _FakeCollector("claude", "Claude Code", _make_fake_stats()),
            _FakeCollector(
                "openrouter", "OpenRouter",
                _make_fake_stats(
                    messages=50, sessions=3, output_tokens=10000,
                    period_cost=1.50, alltime_cost=25.0,
                ),
            ),
        ]
        config = {"billing_day": 10}
        result = aggregate_stats(
            collectors, config, ref_date=datetime(2026, 3, 13),
        )

        assert len(result["agents"]) == 2
        assert result["agents"][1]["id"] == "openrouter"
        assert result["total_period_cost"] == pytest.approx(14.0)

    def test_render_full_with_color(self):
        result = self._get_aggregate()
        output = render_full(result, use_color=True)

        # Should still produce output (not crash)
        assert len(output) > 0

    def test_export_csv(self, tmp_path):
        result = self._get_aggregate()
        csv_path = str(tmp_path / "test.csv")

        export_csv(result, filepath=csv_path)

        assert os.path.isfile(csv_path)

        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 2
        assert rows[0]["agent"] == "agent_a"
        assert rows[1]["agent"] == "agent_b"
        assert float(rows[0]["period_cost"]) == pytest.approx(12.50)
        assert float(rows[1]["period_cost"]) == pytest.approx(25.00)

        # All expected columns present
        expected_cols = {
            "agent", "period_start", "period_end",
            "messages", "sessions", "output_tokens",
            "period_cost", "alltime_cost",
        }
        assert expected_cols == set(reader.fieldnames)


# ── 7. Edge cases ────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases that should not crash the pipeline."""

    def test_gemini_session_no_messages(self, tmp_path):
        """A session file with an empty messages list is skipped."""
        chat_dir = tmp_path / "tmp" / "hash1" / "chats"
        chat_dir.mkdir(parents=True)

        empty_session = {
            "sessionId": "empty",
            "startTime": "2026-03-11T10:00:00Z",
            "messages": [],
        }
        (chat_dir / "session-empty.json").write_text(
            json.dumps(empty_session),
        )

        pattern = str(chat_dir / "session-*.json")

        with patch("burnctl.collectors.gemini._CHAT_PATTERN", pattern):
            stats = GeminiCollector().get_stats(
                datetime(2026, 3, 10),
                datetime(2026, 4, 10),
                datetime(2026, 3, 13),
            )

        # No valid sessions -> None
        assert stats is None

    def test_codex_session_only_meta(self, tmp_path):
        """A JSONL file with only session_meta should parse but yield 0 messages."""
        sessions_dir = tmp_path / "sessions" / "2026" / "03" / "11"
        sessions_dir.mkdir(parents=True)

        line = json.dumps({
            "timestamp": "2026-03-11T14:00:00Z",
            "type": "session_meta",
            "payload": {
                "id": "meta-only",
                "timestamp": "2026-03-11T14:00:00Z",
            },
        })
        (sessions_dir / "rollout-meta.jsonl").write_text(line + "\n")

        sess_dir = str(tmp_path / "sessions")
        start = datetime(2026, 3, 10, tzinfo=timezone.utc)
        end = datetime(2026, 4, 10, tzinfo=timezone.utc)
        ref_date = datetime(2026, 3, 13, tzinfo=timezone.utc)

        with patch("burnctl.collectors.codex.SESSIONS_DIR", sess_dir), \
             patch("burnctl.collectors.codex.HISTORY_FILE", "/nonexistent"):
            stats = CodexCollector().get_stats(start, end, ref_date)

        assert stats is not None
        assert stats["messages"] == 0
        assert stats["sessions"] == 1
        assert stats["output_tokens"] == 0

    def test_claude_empty_daily_activity(self, tmp_path):
        """stats-cache with empty dailyActivity returns 0 messages."""
        data = {
            "firstSessionDate": "2026-01-10T08:30:00Z",
            "totalMessages": 100,
            "totalSessions": 5,
            "modelUsage": {},
            "dailyActivity": [],
            "dailyModelTokens": [],
        }
        stats_file = tmp_path / "stats-cache.json"
        stats_file.write_text(json.dumps(data))

        with patch(
            "burnctl.collectors.claude.STATS_FILE", str(stats_file)
        ):
            stats = ClaudeCollector().get_stats(
                datetime(2026, 3, 10),
                datetime(2026, 4, 10),
                datetime(2026, 3, 13),
            )

        assert stats is not None
        assert stats["messages"] == 0
        assert stats["sessions"] == 0
        assert stats["output_tokens"] == 0

    def test_collector_returning_none_shown_inactive(self):
        """Available collector returning None appears as inactive."""
        none_collector = _FakeCollector("none_agent", "None Agent", None)
        real_collector = _FakeCollector(
            "real", "Real Agent", _make_fake_stats(),
        )

        result = aggregate_stats(
            [none_collector, real_collector],
            {"billing_day": 10},
            ref_date=datetime(2026, 3, 13),
        )

        # Both appear: one inactive, one active
        assert len(result["agents"]) == 2
        assert result["agents"][0]["id"] == "none_agent"
        assert result["agents"][0].get("inactive") is True
        assert result["agents"][1]["id"] == "real"
        assert result["agents"][1].get("inactive") is not True

    def test_gemini_empty_model_name(self, tmp_path):
        """A gemini message with empty-string model should not crash."""
        chat_dir = tmp_path / "tmp" / "hash2" / "chats"
        chat_dir.mkdir(parents=True)

        session = {
            "sessionId": "empty-model",
            "startTime": "2026-03-11T10:00:00Z",
            "messages": [
                {
                    "id": "1",
                    "timestamp": "2026-03-11T10:00:00Z",
                    "type": "user",
                    "content": "hi",
                },
                {
                    "id": "2",
                    "timestamp": "2026-03-11T10:00:05Z",
                    "type": "gemini",
                    "content": "hello",
                    "model": "",
                    "tokens": {
                        "input": 100,
                        "output": 50,
                        "cached": 0,
                        "thoughts": 0,
                        "tool": 0,
                        "total": 150,
                    },
                },
            ],
        }
        (chat_dir / "session-empty-model.json").write_text(
            json.dumps(session),
        )

        pattern = str(chat_dir / "session-*.json")

        with patch("burnctl.collectors.gemini._CHAT_PATTERN", pattern):
            stats = GeminiCollector().get_stats(
                datetime(2026, 3, 10),
                datetime(2026, 4, 10),
                datetime(2026, 3, 13),
            )

        assert stats is not None
        assert stats["messages"] == 1
        assert stats["output_tokens"] == 50
        # Empty string model should still appear in model_usage
        assert "" in stats["model_usage"]

    def test_aggregate_no_collectors(self):
        """Empty collector list produces empty agents list."""
        result = aggregate_stats(
            [],
            {"billing_day": 10},
            ref_date=datetime(2026, 3, 13),
        )
        assert result["agents"] == []
        assert result["total_period_cost"] == 0

    def test_render_compact_single_agent(self):
        """Compact render with one agent omits 'Total' suffix."""
        collector = _FakeCollector(
            "solo", "Solo Agent", _make_fake_stats(),
        )
        result = aggregate_stats(
            [collector],
            {"billing_day": 10},
            ref_date=datetime(2026, 3, 13),
        )
        output = render_compact(result)

        assert "Solo Agent" in output
        assert "Total" not in output

    def test_api_usage_empty_file(self, tmp_path):
        """Empty usage.jsonl returns None."""
        usage_file = tmp_path / "usage.jsonl"
        usage_file.write_text("")

        collector = ApiUsageCollector(
            "openrouter", "OpenRouter", usage_file=str(usage_file),
        )
        stats = collector.get_stats(
            datetime(2026, 3, 10),
            datetime(2026, 4, 10),
            datetime(2026, 3, 13),
        )
        assert stats is None

    def test_api_usage_all_outside_period(self, tmp_path):
        """All entries outside billing period: 0 period messages, non-zero alltime."""
        lines = [
            json.dumps({
                "ts": "2025-01-15T10:00:00Z",
                "provider": "openrouter",
                "model_id": "anthropic/claude-opus-4-6",
                "input_tokens": 1000,
                "output_tokens": 500,
                "cost": 0.02,
                "node_id": "n1",
            }),
            json.dumps({
                "ts": "2025-06-20T10:00:00Z",
                "provider": "openrouter",
                "model_id": "anthropic/claude-opus-4-6",
                "input_tokens": 800,
                "output_tokens": 400,
                "cost": 0.015,
                "node_id": "n2",
            }),
        ]
        usage_file = tmp_path / "usage.jsonl"
        usage_file.write_text("\n".join(lines) + "\n")

        collector = ApiUsageCollector(
            "openrouter", "OpenRouter", usage_file=str(usage_file),
        )
        stats = collector.get_stats(
            datetime(2026, 3, 10),
            datetime(2026, 4, 10),
            datetime(2026, 3, 13),
        )

        assert stats is not None
        assert stats["messages"] == 0
        assert stats["sessions"] == 0
        assert stats["period_cost"] == pytest.approx(0.0)
        assert stats["alltime_cost"] == pytest.approx(0.035)
        assert stats["total_messages"] == 2
        assert stats["total_sessions"] == 2

    def test_render_accessible_no_agents(self):
        """Accessible render with no data returns a clear message."""
        result = {"agents": [], "total_period_cost": 0, "today": "2026-03-13"}
        output = render_accessible(result)
        assert "No agent data" in output
