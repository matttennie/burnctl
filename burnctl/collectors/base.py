"""Base collector interface for AI coding agents."""

from abc import ABC, abstractmethod


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
    def get_stats(self, start, end, ref_date):
        """Collect usage stats for the billing period [*start*, *end*).

        Parameters
        ----------
        start : datetime.datetime
            Inclusive start of the billing period.
        end : datetime.datetime
            Exclusive end of the billing period.
        ref_date : datetime.datetime
            "Today" reference used for elapsed-day calculations.

        Returns
        -------
        dict or None
            A dict with standardised keys when data is available:

            - ``messages``, ``sessions``, ``output_tokens``
            - ``period_cost``, ``alltime_cost``
            - ``model_usage``, ``daily_messages``
            - ``first_session``, ``total_messages``, ``total_sessions``
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
        return {
            "plan_name": "pay-as-you-go",
            "plan_price": 0,
            "billing_day": config.get("billing_day", 1),
            "interval": "mo",
        }
