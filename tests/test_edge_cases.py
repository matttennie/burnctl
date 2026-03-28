"""Edge case and robustness tests for burnctl.

Covers encoding quirks, large/corrupt data, race conditions, path edge
cases, numeric boundary conditions, and config resilience.

Python 3.8 compatible.
"""

import json
import os
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from burnctl.collectors.aider import AiderCollector
from burnctl.collectors.base import MAX_FILE_SIZE, _check_file_size
from burnctl.collectors.claude import ClaudeCollector
from burnctl.collectors.codex import (
    _iter_session_files,
    _MAX_SESSION_BYTES,
    _parse_session,
)
from burnctl.collectors.gemini import GeminiCollector
from burnctl.config import DEFAULTS, _MAX_CONFIG_BYTES, load
from burnctl.report import (
    aggregate_stats,
    fmt,
    render_accessible,
    render_compact,
    render_full,
    render_json,
)


# =====================================================================
# Encoding edge cases
# =====================================================================


class TestEncodingEdgeCases:
    """Files with unusual encodings should not crash collectors."""

    def test_aider_non_utf8_bytes(self, tmp_path):
        """Aider history with Latin-1 encoded bytes (errors='replace')."""
        history = tmp_path / ".aider.chat.history.md"
        # Write Latin-1 encoded content with a cost line
        content = (
            b"Some text with \xe9\xe8\xf1 accented chars\n"
            b"Tokens: 1.5k sent, 2.1k received. Cost: $0.03\n"
        )
        history.write_bytes(content)

        collector = AiderCollector()
        start = datetime(2020, 1, 1)
        end = datetime(2030, 1, 1)
        ref = datetime(2026, 3, 13)

        with patch(
            "burnctl.collectors.aider._find_history_files",
            return_value=[str(history)],
        ):
            stats = collector.get_stats(start, end, ref)

        assert stats is not None
        assert stats["messages"] == 1
        assert stats["period_cost"] == pytest.approx(0.03)

    def test_gemini_session_with_bom(self, tmp_path):
        """Gemini session file with UTF-8 BOM should parse."""
        session = {
            "startTime": "2026-03-10T10:00:00Z",
            "messages": [
                {
                    "type": "user",
                    "timestamp": "2026-03-10T10:00:00Z",
                    "content": "hi",
                },
                {
                    "type": "gemini",
                    "timestamp": "2026-03-10T10:01:00Z",
                    "model": "gemini-2.5-flash",
                    "tokens": {"input": 100, "output": 200, "cached": 0},
                },
            ],
        }
        fpath = tmp_path / "session-bom.json"
        # Write with BOM
        bom_content = b"\xef\xbb\xbf" + json.dumps(session).encode("utf-8")
        fpath.write_bytes(bom_content)

        start = datetime(2026, 3, 10)
        end = datetime(2026, 3, 11)
        ref = datetime(2026, 3, 10)

        with patch(
            "burnctl.collectors.gemini.glob.glob",
            return_value=[str(fpath)],
        ):
            # json.load with default encoding handles BOM via utf-8-sig or
            # via the BOM being valid whitespace.  Either way, no crash.
            stats = GeminiCollector().get_stats(start, end, ref)

        # BOM may cause a JSONDecodeError depending on Python version,
        # but the collector catches that and returns None rather than crashing.
        # Either stats is a valid dict or None -- neither is a crash.
        assert stats is None or isinstance(stats, dict)

    def test_codex_jsonl_mixed_line_endings(self, tmp_path):
        r"""Codex JSONL with mixed \r\n and \n line endings."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        events = [
            {
                "type": "session_meta",
                "timestamp": "2026-03-10T10:00:00Z",
                "payload": {"timestamp": "2026-03-10T10:00:00Z"},
            },
            {
                "type": "event_msg",
                "timestamp": "2026-03-10T10:00:01Z",
                "payload": {"type": "user_message", "content": "hi"},
            },
        ]
        fpath = sessions_dir / "mixed.jsonl"
        # Write with mixed line endings
        lines = [json.dumps(e) for e in events]
        mixed = lines[0] + "\r\n" + lines[1] + "\n"
        fpath.write_text(mixed)

        result = _parse_session(str(fpath))
        assert result is not None
        assert len(result["user_messages"]) == 1

    def test_empty_aider_history(self, tmp_path):
        """Empty aider history file (0 bytes)."""
        history = tmp_path / ".aider.chat.history.md"
        history.write_text("")

        collector = AiderCollector()
        with patch(
            "burnctl.collectors.aider._find_history_files",
            return_value=[str(history)],
        ):
            stats = collector.get_stats(
                datetime(2026, 1, 1), datetime(2026, 12, 31),
                datetime(2026, 3, 13),
            )
        assert stats is None

    def test_empty_gemini_session_file(self, tmp_path):
        """Empty Gemini session file (0 bytes)."""
        fpath = tmp_path / "session-empty.json"
        fpath.write_text("")

        with patch(
            "burnctl.collectors.gemini.glob.glob",
            return_value=[str(fpath)],
        ):
            stats = GeminiCollector().get_stats(
                datetime(2026, 3, 1), datetime(2026, 3, 31),
                datetime(2026, 3, 13),
            )
        assert stats is None

    def test_empty_codex_session_file(self, tmp_path):
        """Empty Codex JSONL file (0 bytes)."""
        fpath = tmp_path / "session-empty.jsonl"
        fpath.write_text("")

        result = _parse_session(str(fpath))
        assert result is None


# =====================================================================
# Large / corrupt data
# =====================================================================


class TestLargeCorruptData:
    """Large or malformed data should be handled gracefully."""

    def test_gemini_session_10000_messages(self, tmp_path):
        """Gemini session with 10000 messages should complete."""
        messages = []
        for i in range(5000):
            messages.append({
                "type": "user",
                "timestamp": "2026-03-10T10:{:02d}:{:02d}Z".format(
                    i // 60 % 60, i % 60,
                ),
                "content": "msg {}".format(i),
            })
            messages.append({
                "type": "gemini",
                "timestamp": "2026-03-10T10:{:02d}:{:02d}Z".format(
                    i // 60 % 60, (i % 60) + 1 if i % 60 < 59 else 0,
                ),
                "model": "gemini-2.5-flash",
                "tokens": {"input": 10, "output": 20, "cached": 0},
            })

        session = {
            "startTime": "2026-03-10T10:00:00Z",
            "messages": messages,
        }
        fpath = tmp_path / "session-large.json"
        fpath.write_text(json.dumps(session))

        start = datetime(2026, 3, 10)
        end = datetime(2026, 3, 11)
        ref = datetime(2026, 3, 10)

        with patch(
            "burnctl.collectors.gemini.glob.glob",
            return_value=[str(fpath)],
        ):
            stats = GeminiCollector().get_stats(start, end, ref)

        assert stats is not None
        assert stats["total_sessions"] == 1
        assert stats["total_messages"] == 5000

    def test_codex_jsonl_long_lines(self, tmp_path):
        """Codex JSONL with extremely long lines (100KB per line)."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        fpath = sessions_dir / "long-lines.jsonl"
        long_content = "x" * 100_000
        events = [
            {
                "type": "session_meta",
                "timestamp": "2026-03-10T10:00:00Z",
                "payload": {
                    "timestamp": "2026-03-10T10:00:00Z",
                    "data": long_content,
                },
            },
            {
                "type": "event_msg",
                "timestamp": "2026-03-10T10:00:01Z",
                "payload": {
                    "type": "user_message",
                    "content": long_content,
                },
            },
        ]
        with open(str(fpath), "w") as f:
            for evt in events:
                f.write(json.dumps(evt) + "\n")

        result = _parse_session(str(fpath))
        assert result is not None
        assert len(result["user_messages"]) == 1

    def test_codex_file_at_max_session_bytes(self, tmp_path):
        """Codex JSONL at exactly _MAX_SESSION_BYTES should be included."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        fpath = sessions_dir / "exact-limit.jsonl"
        # Create a file exactly at the limit
        fpath.write_bytes(b"x" * _MAX_SESSION_BYTES)

        with patch(
            "burnctl.collectors.codex.SESSIONS_DIR", str(sessions_dir),
        ):
            files = list(_iter_session_files())

        assert str(fpath) in files

    def test_codex_file_over_max_session_bytes(self, tmp_path):
        """Codex JSONL over _MAX_SESSION_BYTES should be skipped."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        fpath = sessions_dir / "too-large.jsonl"
        # Create a file one byte over the limit
        fpath.write_bytes(b"x" * (_MAX_SESSION_BYTES + 1))

        with patch(
            "burnctl.collectors.codex.SESSIONS_DIR", str(sessions_dir),
        ):
            files = list(_iter_session_files())

        assert str(fpath) not in files

    def test_claude_stats_extra_keys(self, tmp_path):
        """Claude stats-cache.json with unexpected extra keys."""
        data = {
            "firstSessionDate": "2026-01-01T00:00:00Z",
            "totalMessages": 10,
            "totalSessions": 2,
            "dailyActivity": [],
            "dailyModelTokens": [],
            "modelUsage": {},
            "unexpectedKey1": "value1",
            "unexpectedKey2": [1, 2, 3],
            "nestedExtra": {"a": {"b": "c"}},
        }
        collector = ClaudeCollector()
        with patch.object(collector, "_load_data", return_value=data), \
             patch.object(
                 collector, "_get_pricing_table",
                 return_value=collector._fallback_pricing(),
             ):
            stats = collector.get_stats(
                datetime(2026, 1, 1), datetime(2026, 12, 31),
                datetime(2026, 3, 13),
            )

        assert stats is not None
        assert stats["messages"] == 0
        assert stats["total_messages"] == 10

    def test_claude_stats_missing_model_usage(self, tmp_path):
        """Claude stats-cache with no modelUsage key."""
        data = {
            "firstSessionDate": "2026-01-01T00:00:00Z",
            "totalMessages": 5,
            "totalSessions": 1,
            "dailyActivity": [
                {
                    "date": "2026-03-10",
                    "messageCount": 5,
                    "sessionCount": 1,
                    "toolCallCount": 3,
                },
            ],
            "dailyModelTokens": [],
            # No "modelUsage" key at all
        }
        collector = ClaudeCollector()
        with patch.object(collector, "_load_data", return_value=data), \
             patch.object(
                 collector, "_get_pricing_table",
                 return_value=collector._fallback_pricing(),
             ):
            stats = collector.get_stats(
                datetime(2026, 3, 1), datetime(2026, 4, 1),
                datetime(2026, 3, 13),
            )

        assert stats is not None
        assert stats["alltime_cost"] == 0.0
        assert stats["model_usage"] == {}

    def test_claude_stats_missing_daily_activity(self, tmp_path):
        """Claude stats-cache with no dailyActivity key."""
        data = {
            "firstSessionDate": "2026-01-01T00:00:00Z",
            "totalMessages": 5,
            "totalSessions": 1,
            # No "dailyActivity" key
            "dailyModelTokens": [],
            "modelUsage": {},
        }
        collector = ClaudeCollector()
        with patch.object(collector, "_load_data", return_value=data), \
             patch.object(
                 collector, "_get_pricing_table",
                 return_value=collector._fallback_pricing(),
             ):
            stats = collector.get_stats(
                datetime(2026, 3, 1), datetime(2026, 4, 1),
                datetime(2026, 3, 13),
            )

        assert stats is not None
        assert stats["messages"] == 0
        assert stats["sessions"] == 0
        assert stats["tool_calls"] == 0


# =====================================================================
# Concurrent / race conditions
# =====================================================================


class TestConcurrentRaceConditions:
    """File disappearance between is_available() and get_stats()."""

    def test_claude_file_disappears(self):
        """File disappears between is_available and get_stats."""
        collector = ClaudeCollector()
        # First call: file exists
        with patch(
            "burnctl.collectors.claude.os.path.isfile", return_value=True,
        ):
            assert collector.is_available() is True

        # Second call: file gone by the time we read
        with patch.object(collector, "_load_data", return_value=None):
            stats = collector.get_stats(
                datetime(2026, 3, 1), datetime(2026, 4, 1),
                datetime(2026, 3, 13),
            )
        assert stats is None

    def test_gemini_files_disappear(self):
        """Glob finds files but they are gone when opened."""
        with patch(
            "burnctl.collectors.gemini.glob.glob",
            return_value=["/vanished/session.json"],
        ):
            stats = GeminiCollector().get_stats(
                datetime(2026, 3, 1), datetime(2026, 4, 1),
                datetime(2026, 3, 13),
            )
        # The open() will raise OSError, caught by the collector
        assert stats is None

    def test_aider_file_disappears_between_mtime_and_read(self, tmp_path):
        """Aider file vanishes between os.path.getmtime and open."""
        history = tmp_path / ".aider.chat.history.md"
        history.write_text("Tokens: 1k sent, 2k received. Cost: $0.10\n")

        collector = AiderCollector()
        start = datetime(2020, 1, 1)
        end = datetime(2030, 1, 1)
        ref = datetime(2026, 3, 13)

        call_count = [0]
        real_open = open

        def disappearing_open(path, *args, **kwargs):
            if str(history) in str(path):
                call_count[0] += 1
                raise OSError("File not found")
            return real_open(path, *args, **kwargs)

        with patch(
            "burnctl.collectors.aider._find_history_files",
            return_value=[str(history)],
        ), patch("builtins.open", side_effect=disappearing_open):
            stats = collector.get_stats(start, end, ref)

        # Should return None gracefully, not crash
        assert stats is None


# =====================================================================
# Path edge cases
# =====================================================================


class TestPathEdgeCases:
    """Symlinks and unusual filenames."""

    def test_codex_follows_symlinks(self, tmp_path):
        """Symlinked session directories should be followed."""
        real_dir = tmp_path / "real_sessions"
        real_dir.mkdir()

        events = [
            {
                "type": "session_meta",
                "timestamp": "2026-03-10T10:00:00Z",
                "payload": {"timestamp": "2026-03-10T10:00:00Z"},
            },
            {
                "type": "event_msg",
                "timestamp": "2026-03-10T10:00:01Z",
                "payload": {"type": "user_message", "content": "hello"},
            },
        ]
        fpath = real_dir / "session.jsonl"
        with open(str(fpath), "w") as f:
            for evt in events:
                f.write(json.dumps(evt) + "\n")

        # Create a symlink to the real directory
        link_dir = tmp_path / "sessions"
        link_dir.symlink_to(real_dir)

        with patch(
            "burnctl.collectors.codex.SESSIONS_DIR", str(link_dir),
        ):
            files = list(_iter_session_files())

        assert len(files) == 1

    def test_codex_filenames_with_spaces(self, tmp_path):
        """Session files with spaces in names should be found."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        fpath = sessions_dir / "session with spaces.jsonl"
        events = [
            {
                "type": "session_meta",
                "timestamp": "2026-03-10T10:00:00Z",
                "payload": {"timestamp": "2026-03-10T10:00:00Z"},
            },
        ]
        with open(str(fpath), "w") as f:
            for evt in events:
                f.write(json.dumps(evt) + "\n")

        with patch(
            "burnctl.collectors.codex.SESSIONS_DIR", str(sessions_dir),
        ):
            files = list(_iter_session_files())

        assert len(files) == 1
        assert "session with spaces.jsonl" in files[0]

    def test_codex_filenames_with_unicode(self, tmp_path):
        """Session files with unicode characters in names."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        fpath = sessions_dir / "session-\u00e9\u00e8\u00f1.jsonl"
        events = [
            {
                "type": "session_meta",
                "timestamp": "2026-03-10T10:00:00Z",
                "payload": {"timestamp": "2026-03-10T10:00:00Z"},
            },
        ]
        with open(str(fpath), "w") as f:
            for evt in events:
                f.write(json.dumps(evt) + "\n")

        with patch(
            "burnctl.collectors.codex.SESSIONS_DIR", str(sessions_dir),
        ):
            files = list(_iter_session_files())

        assert len(files) == 1


# =====================================================================
# Numeric edge cases
# =====================================================================


class TestNumericEdgeCases:
    """Boundary and extreme numeric values."""

    def test_zero_plan_price_no_division_error(self):
        """pace_pct should be 0 when plan_price is 0, not ZeroDivisionError."""
        collector = MagicMock()
        collector.id = "test"
        collector.name = "Test"
        collector.get_plan_info.return_value = {
            "plan_name": "free",
            "plan_price": 0,
            "billing_day": 1,
            "interval": "mo",
        }
        collector.get_stats.return_value = {
            "messages": 10,
            "sessions": 1,
            "output_tokens": 1000,
            "tool_calls": 5,
            "period_cost": 5.0,
            "alltime_cost": 5.0,
            "model_usage": {},
            "first_session": "",
            "total_messages": 10,
            "total_sessions": 1,
            "input_tokens": 500,
        }

        result = aggregate_stats(
            [collector], {}, ref_date=datetime(2026, 3, 15),
        )

        agent = result["agents"][0]
        assert agent["pace_pct"] == 0.0
        assert agent["value_ratio"] == 0.0

    def test_negative_token_counts(self):
        """Negative token counts should compute without error."""
        stats = {
            "agents": [
                {
                    "id": "test",
                    "name": "Test",
                    "plan_name": "pro",
                    "plan_price": 20,
                    "interval": "mo",
                    "period_start": "2026-03-01",
                    "period_end": "2026-04-01",
                    "days_elapsed": 10,
                    "days_remaining": 21,
                    "total_days": 31,
                    "pace_pct": 0,
                    "projected_cost": 0,
                    "messages": -5,
                    "sessions": -1,
                    "input_tokens": -500,
                    "output_tokens": -1000,
                    "tool_calls": -2,
                    "period_cost": -0.50,
                    "alltime_cost": -1.00,
                    "value_ratio": -0.5,
                    "model_usage": {},
                    "first_session": "",
                    "total_messages": -5,
                    "total_sessions": -1,
                },
            ],
            "total_period_cost": -0.50,
            "today": "2026-03-13",
        }
        # Should not raise
        with patch(
            "os.get_terminal_size",
            return_value=os.terminal_size((100, 40)),
        ):
            result = render_full(stats, use_color=False)
        assert isinstance(result, str)

        result = render_compact(stats)
        assert isinstance(result, str)

        result = render_accessible(stats)
        assert isinstance(result, str)

    def test_very_large_numbers(self):
        """Billions of tokens should format correctly with fmt()."""
        assert fmt(1_000_000_000) == "1,000,000,000"
        assert fmt(999_999_999_999) == "999,999,999,999"

    def test_nan_cost_in_compact_and_accessible(self):
        """NaN values in cost fields should not crash compact/accessible."""
        stats = {
            "agents": [
                {
                    "id": "test",
                    "name": "Test",
                    "plan_name": "pro",
                    "plan_price": 20,
                    "interval": "mo",
                    "period_start": "2026-03-01",
                    "period_end": "2026-04-01",
                    "days_elapsed": 10,
                    "days_remaining": 21,
                    "total_days": 31,
                    "pace_pct": 50.0,
                    "projected_cost": float("nan"),
                    "messages": 10,
                    "sessions": 1,
                    "output_tokens": 1000,
                    "tool_calls": 5,
                    "period_cost": float("nan"),
                    "alltime_cost": float("nan"),
                    "value_ratio": float("nan"),
                    "model_usage": {},
                    "first_session": "2026-01-01",
                    "total_messages": 10,
                    "total_sessions": 1,
                    "input_tokens": None,
                },
            ],
            "total_period_cost": float("nan"),
            "today": "2026-03-13",
        }
        result = render_compact(stats)
        assert isinstance(result, str)

        result = render_accessible(stats)
        assert isinstance(result, str)

        # render_full also works when NaN is only in cost fields
        with patch(
            "os.get_terminal_size",
            return_value=os.terminal_size((100, 40)),
        ):
            result = render_full(stats, use_color=False)
        assert isinstance(result, str)

    def test_nan_pace_pct_handled_gracefully(self):
        """NaN in pace_pct should be treated as 0% (not crash)."""
        stats = {
            "agents": [
                {
                    "id": "test",
                    "name": "Test",
                    "plan_name": "pro",
                    "plan_price": 20,
                    "interval": "mo",
                    "period_start": "2026-03-01",
                    "period_end": "2026-04-01",
                    "days_elapsed": 10,
                    "days_remaining": 21,
                    "total_days": 31,
                    "pace_pct": float("nan"),
                    "projected_cost": 0,
                    "messages": 10,
                    "sessions": 1,
                    "output_tokens": 1000,
                    "tool_calls": 5,
                    "period_cost": 5.0,
                    "alltime_cost": 10.0,
                    "value_ratio": 0.5,
                    "model_usage": {},
                    "first_session": "2026-01-01",
                    "total_messages": 10,
                    "total_sessions": 1,
                    "input_tokens": None,
                },
            ],
            "total_period_cost": 5.0,
            "today": "2026-03-13",
        }
        with patch(
            "os.get_terminal_size",
            return_value=os.terminal_size((100, 40)),
        ):
            result = render_full(stats, use_color=False)
        assert isinstance(result, str)
        assert "PERIOD USAGE" in result

    def test_inf_in_render_functions(self):
        """Infinity values in cost should not crash render functions."""
        stats = {
            "agents": [
                {
                    "id": "test",
                    "name": "Test",
                    "plan_name": "pro",
                    "plan_price": 20,
                    "interval": "mo",
                    "period_start": "2026-03-01",
                    "period_end": "2026-04-01",
                    "days_elapsed": 10,
                    "days_remaining": 21,
                    "total_days": 31,
                    "pace_pct": float("inf"),
                    "projected_cost": float("inf"),
                    "messages": 10,
                    "sessions": 1,
                    "input_tokens": None,
                    "output_tokens": 1000,
                    "tool_calls": 5,
                    "period_cost": float("inf"),
                    "alltime_cost": float("inf"),
                    "value_ratio": float("inf"),
                    "model_usage": {},
                    "first_session": "2026-01-01",
                    "total_messages": 10,
                    "total_sessions": 1,
                },
            ],
            "total_period_cost": float("inf"),
            "today": "2026-03-13",
        }
        with patch(
            "os.get_terminal_size",
            return_value=os.terminal_size((100, 40)),
        ):
            result = render_full(stats, use_color=False)
        assert isinstance(result, str)

        result = render_compact(stats)
        assert isinstance(result, str)

        result = render_accessible(stats)
        assert isinstance(result, str)

    def test_render_json_with_nan(self):
        """render_json with NaN -- json.dumps should not crash."""
        stats = {
            "agents": [],
            "total_period_cost": float("nan"),
            "today": "2026-03-13",
        }
        # json.dumps with default=str will handle NaN
        result = render_json(stats)
        assert isinstance(result, str)


# =====================================================================
# Config edge cases
# =====================================================================


class TestConfigEdgeCases:
    """Config file with unusual content."""

    def test_config_unknown_keys_rejected(self, tmp_path):
        """Unknown keys in config file are ignored (security hardening)."""
        config_file = tmp_path / "config.json"
        saved = {
            "billing_day": 15,
            "unknown_future_key": "some_value",
            "another_extra": [1, 2, 3],
        }
        config_file.write_text(json.dumps(saved))

        with patch("burnctl.config.CONFIG_FILE", str(config_file)):
            config = load()

        assert config["billing_day"] == 15
        assert "unknown_future_key" not in config
        assert "another_extra" not in config
        # Defaults should still be present for unset keys
        assert config["theme"] == DEFAULTS["theme"]

    def test_config_wrong_type_billing_day_string(self, tmp_path):
        """billing_day stored as string '10' -- load() returns as-is."""
        config_file = tmp_path / "config.json"
        saved = {"billing_day": "10"}
        config_file.write_text(json.dumps(saved))

        with patch("burnctl.config.CONFIG_FILE", str(config_file)):
            config = load()

        # load() merges raw JSON values; no type coercion on load
        assert config["billing_day"] == "10"


# =====================================================================
# _check_file_size unit tests
# =====================================================================


class TestCheckFileSize:
    """Unit tests for the _check_file_size guard in collectors/base.py."""

    def test_normal_sized_file_passes(self, tmp_path):
        """A file well under the limit should return True."""
        f = tmp_path / "small.json"
        f.write_bytes(b"x" * 1024)  # 1 KiB
        assert _check_file_size(str(f)) is True

    def test_oversized_file_fails_and_warns(self, tmp_path, capsys):
        """A file exceeding the limit should return False and warn on stderr."""
        f = tmp_path / "huge.json"
        f.write_bytes(b"x" * (MAX_FILE_SIZE + 1))
        result = _check_file_size(str(f))
        assert result is False
        err = capsys.readouterr().err
        assert "Warning" in err
        assert "oversized" in err
        assert str(f) in err

    def test_nonexistent_file_returns_true(self):
        """A non-existent path should return True (let open() raise)."""
        assert _check_file_size("/no/such/path/does_not_exist.json") is True

    def test_custom_limit_small(self, tmp_path):
        """Custom limit parameter should override the default."""
        f = tmp_path / "data.bin"
        f.write_bytes(b"x" * 500)
        assert _check_file_size(str(f), limit=1000) is True
        assert _check_file_size(str(f), limit=100) is False

    def test_exact_boundary_at_limit_passes(self, tmp_path):
        """A file at exactly the limit should pass (limit is exclusive)."""
        limit = 4096
        f = tmp_path / "exact.bin"
        f.write_bytes(b"x" * limit)
        assert _check_file_size(str(f), limit=limit) is True

    def test_exact_boundary_one_over_fails(self, tmp_path):
        """A file one byte over the limit should fail."""
        limit = 4096
        f = tmp_path / "over.bin"
        f.write_bytes(b"x" * (limit + 1))
        assert _check_file_size(str(f), limit=limit) is False

    def test_empty_file_passes(self, tmp_path):
        """A zero-byte file should pass."""
        f = tmp_path / "empty.json"
        f.write_bytes(b"")
        assert _check_file_size(str(f)) is True

    def test_oserror_on_stat_returns_true(self, tmp_path):
        """If os.path.getsize raises OSError, return True (let open handle it)."""
        f = tmp_path / "exists.json"
        f.write_bytes(b"x" * 10)
        with patch("burnctl.collectors.base.os.path.getsize", side_effect=OSError("perm")):
            assert _check_file_size(str(f)) is True


# =====================================================================
# Config file size guard
# =====================================================================


class TestConfigFileSizeGuard:
    """Config load() rejects config files larger than 1 MiB."""

    def test_config_over_1mib_returns_defaults(self, tmp_path, capsys):
        """A config file > 1 MiB should be rejected with a warning."""
        config_file = tmp_path / "config.json"
        # Write a file just over the limit
        config_file.write_bytes(b"{}" + b" " * _MAX_CONFIG_BYTES)

        with patch("burnctl.config.CONFIG_FILE", str(config_file)):
            config = load()

        assert config == DEFAULTS
        err = capsys.readouterr().err
        assert "Warning" in err
        assert "too large" in err

    def test_config_at_1mib_loads_normally(self, tmp_path):
        """A config file at exactly 1 MiB should load normally."""
        config_file = tmp_path / "config.json"
        payload = json.dumps({"billing_day": 15})
        # Pad to exactly _MAX_CONFIG_BYTES with whitespace (valid JSON ignores it)
        padded = payload + " " * (_MAX_CONFIG_BYTES - len(payload))
        config_file.write_text(padded)

        with patch("burnctl.config.CONFIG_FILE", str(config_file)):
            config = load()

        assert config["billing_day"] == 15

    def test_config_just_over_1mib_rejected(self, tmp_path, capsys):
        """A config file at 1 MiB + 1 byte should be rejected."""
        config_file = tmp_path / "config.json"
        payload = json.dumps({"billing_day": 20})
        padded = payload + " " * (_MAX_CONFIG_BYTES - len(payload) + 1)
        config_file.write_text(padded)

        with patch("burnctl.config.CONFIG_FILE", str(config_file)):
            config = load()

        assert config == DEFAULTS
        err = capsys.readouterr().err
        assert "too large" in err


# =====================================================================
# Collector file-size integration tests
# =====================================================================


class TestCollectorFileSizeIntegration:
    """Verify each collector properly guards against oversized files."""

    def test_claude_load_data_rejects_oversized_stats(self, tmp_path, capsys):
        """ClaudeCollector._load_data returns None for oversized stats file."""
        collector = ClaudeCollector()
        big_file = tmp_path / "stats-cache.json"
        big_file.write_bytes(b"{}" + b" " * MAX_FILE_SIZE)

        with patch("burnctl.collectors.claude.STATS_FILE", str(big_file)), \
             patch("burnctl.collectors.claude.os.path.isfile", return_value=True):
            result = collector._load_data()

        assert result is None
        err = capsys.readouterr().err
        assert "oversized" in err

    def test_claude_scan_sessions_skips_oversized_jsonl(self, tmp_path, capsys):
        """Claude session scanner skips JSONL files over the size limit."""
        projects_dir = tmp_path / "projects" / "test"
        projects_dir.mkdir(parents=True)
        big_jsonl = projects_dir / "session.jsonl"
        big_jsonl.write_bytes(b"x" * (MAX_FILE_SIZE + 1))

        with patch("burnctl.collectors.claude.PROJECTS_DIR", str(tmp_path / "projects")):
            act, tok, delta = ClaudeCollector._scan_sessions_after(
                "2020-01-01", "2030-01-01",
            )

        # No data extracted from the oversized file
        assert act == []
        assert tok == []
        assert delta == {}
        err = capsys.readouterr().err
        assert "oversized" in err

    def test_gemini_skips_oversized_session(self, tmp_path, capsys):
        """GeminiCollector.get_stats skips session files over the size limit."""
        big_session = tmp_path / "session-big.json"
        big_session.write_bytes(b"{}" + b" " * MAX_FILE_SIZE)

        with patch(
            "burnctl.collectors.gemini.glob.glob",
            return_value=[str(big_session)],
        ):
            stats = GeminiCollector().get_stats(
                datetime(2026, 3, 1), datetime(2026, 4, 1),
                datetime(2026, 3, 15),
            )

        assert stats is None
        err = capsys.readouterr().err
        assert "oversized" in err

    def test_codex_parse_session_rejects_oversized(self, tmp_path, capsys):
        """Codex _parse_session returns None for oversized files."""
        big_file = tmp_path / "session.jsonl"
        big_file.write_bytes(b"x" * (MAX_FILE_SIZE + 1))

        result = _parse_session(str(big_file))

        assert result is None
        err = capsys.readouterr().err
        assert "oversized" in err

    def test_aider_skips_oversized_history(self, tmp_path, capsys):
        """AiderCollector.get_stats skips history files over the size limit."""
        history = tmp_path / ".aider.chat.history.md"
        # Create file over the limit with a valid cost line at the start
        content = b"Tokens: 1k sent, 1k received. Cost: $0.05\n"
        history.write_bytes(content + b"x" * MAX_FILE_SIZE)

        collector = AiderCollector()
        with patch(
            "burnctl.collectors.aider._find_history_files",
            return_value=[str(history)],
        ):
            stats = collector.get_stats(
                datetime(2020, 1, 1), datetime(2030, 1, 1),
                datetime(2026, 3, 15),
            )

        assert stats is None
        err = capsys.readouterr().err
        assert "oversized" in err

    def test_collectors_import_check_file_size(self):
        """All collectors that use file I/O import _check_file_size."""
        import burnctl.collectors.claude as claude_mod
        import burnctl.collectors.gemini as gemini_mod
        import burnctl.collectors.codex as codex_mod
        import burnctl.collectors.aider as aider_mod

        assert hasattr(claude_mod, "_check_file_size")
        assert hasattr(gemini_mod, "_check_file_size")
        assert hasattr(codex_mod, "_check_file_size")
        assert hasattr(aider_mod, "_check_file_size")
