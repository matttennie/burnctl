"""Placeholder collectors for agents not yet implemented.

Each class satisfies the ``BaseCollector`` ABC but always reports as
unavailable.  They exist so the registry can enumerate every known
agent for ``--help`` output, tab-completion, etc.
"""

from burnctl.collectors.base import BaseCollector


class ClineCollector(BaseCollector):
    """Stub for the Cline VS Code extension.

    Data would live under ``~/.vscode/extensions/saoudrizwan.claude-dev-*/``
    once a parser is written.
    """

    @property
    def name(self):
        return "Cline"

    @property
    def id(self):
        return "cline"

    def is_available(self):
        return False

    def get_stats(self, start, end, ref_date):
        return None


class OpenCodeCollector(BaseCollector):
    """Stub for the OpenCode agent."""

    @property
    def name(self):
        return "OpenCode"

    @property
    def id(self):
        return "opencode"

    def is_available(self):
        return False

    def get_stats(self, start, end, ref_date):
        return None
