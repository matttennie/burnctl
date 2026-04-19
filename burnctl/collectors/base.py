"""Base collector interface for AI coding agents."""

import os
import sys
from abc import ABC, abstractmethod

# Maximum file size (bytes) we'll attempt to parse.  Protects against
# OOM if a data file is unexpectedly huge (e.g., corruption, symlink to
# /dev/urandom, or multi-GB log growth).  50 MiB is generous for any
# reasonable session/config file.
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MiB


def _check_file_size(path, limit=MAX_FILE_SIZE):
    """Return *True* if *path* is within *limit* bytes.

    Prints a warning to stderr and returns *False* when the file is
    too large or un-stat-able.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return True  # can't stat → let open() raise naturally
    if size > limit:
        print(
            f"Warning: skipping oversized file ({size:,} bytes): {path}",
            file=sys.stderr,
        )
        return False
    return True


class BaseCollector(ABC):
    """Abstract base class for agent usage collectors.

    Every collector must implement ``name``, ``id``, ``is_available`` and
    ``get_stats``.  Optional hooks (``get_upgrade_url``, ``get_plan_info``)
    have sensible defaults for pay-as-you-go agents.
    """

    @property
    @abstractmethod
    def name(self):
        """Human-readable display name (e.g., 'Claude Code')."""

    @property
    @abstractmethod
    def id(self):
        """Internal identifier used for CLI flags (e.g., 'claude')."""

    @abstractmethod
    def is_available(self):
        """Return *True* if this agent's usage data exists on the system."""

    @abstractmethod
    def get_stats(self, start, end, ref_date, live=False):
        """Collect usage stats for the billing period [*start*, *end*).

        Parameters
        ----------
        start : datetime.datetime
            Inclusive start of the billing period.
        end : datetime.datetime
            Exclusive end of the billing period.
        ref_date : datetime.datetime
            "Today" reference used for elapsed-day calculations.
        live : bool
            True if running in a high-frequency "live" loop. Collectors
            may use this to reduce timeouts or skip expensive scans.

        Returns
        -------
        dict or None
            A dict with standardised keys when data is available:

            - ``messages``, ``sessions``, ``output_tokens``
            - ``period_cost``, ``alltime_cost``
            - ``model_usage``, ``daily_messages``
            - ``first_session``, ``last_active``
            - ``total_messages``, ``total_sessions``
            - ``tool_calls``

            *None* when there is no data for the requested period.
        """

    def get_upgrade_url(self):
        """URL for the agent's billing / upgrade page (empty by default)."""
        return ""

    def get_plan_info(self, config):
        """Return plan details derived from *config*.

        Returns
        -------
        dict
            Keys: ``plan_name``, ``plan_price``, ``billing_day``, ``interval``.
        """
        agent_plan = config.get("agent_plans", {}).get(self.id)
        if not agent_plan:
            agent_plan = config.get(f"{self.id}_plan", "")
        agent_bd = config.get("agent_billing_days", {}).get(self.id)
        if not agent_bd:
            agent_bd = config.get(f"{self.id}_billing_day", 0)
        return {
            "plan_name": agent_plan or "pay-as-you-go",
            "plan_price": 0,
            "billing_day": agent_bd if agent_bd else config.get("billing_day", 1),
            "interval": "mo",
        }
