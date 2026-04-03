"""Collector registry for all supported AI coding agents."""

from burnctl.collectors.claude import ClaudeCollector
from burnctl.collectors.gemini import GeminiCollector
from burnctl.collectors.codex import CodexCollector
from burnctl.collectors.aider import AiderCollector
from burnctl.collectors.local import LocalCollector
from burnctl.collectors.api_usage import discover_collectors
from burnctl.collectors.stubs import ClineCollector, OpenCodeCollector

ALL_COLLECTORS = [
    ClaudeCollector(),
    GeminiCollector(),
    CodexCollector(),
    AiderCollector(),
    *discover_collectors(),
    LocalCollector(),
    ClineCollector(),
    OpenCodeCollector(),
]


def get_collector(agent_id):
    """Look up a collector by its id."""
    for c in ALL_COLLECTORS:
        if c.id == agent_id:
            return c
    return None


def get_available():
    """Return list of collectors that have data on this system."""
    return [c for c in ALL_COLLECTORS if c.is_available()]
