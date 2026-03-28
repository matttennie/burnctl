"""Local model (Ollama) collector — detection-only stub.

Reports zero usage.  Included so that ``burnctl`` can show that local
inference is available without incurring API costs.
"""

import os

from burnctl.collectors.base import BaseCollector


_OLLAMA_DIR = os.path.join(os.path.expanduser("~"), ".ollama")


class LocalCollector(BaseCollector):
    """Detection-only collector for locally-running models (Ollama)."""

    @property
    def name(self):
        return "Local Models"

    @property
    def id(self):
        return "local"

    def is_available(self):
        """Return *True* if the ``~/.ollama`` directory exists."""
        return os.path.isdir(_OLLAMA_DIR)

    def get_stats(self, start, end, ref_date):
        """Return zeroed stats — local models have no metered usage."""
        return {
            "messages": 0,
            "sessions": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "period_cost": 0.0,
            "alltime_cost": 0.0,
            "model_usage": {},
            "first_session": "",
            "total_messages": 0,
            "total_sessions": 0,
            "tool_calls": 0,
        }

    def get_upgrade_url(self):
        return "https://ollama.com/"

    def get_plan_info(self, config):
        return {
            "plan_name": "local",
            "plan_price": 0,
            "billing_day": config.get("billing_day", 1),
            "interval": "mo",
        }
