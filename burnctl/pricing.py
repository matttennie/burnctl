"""Multi-agent pricing dispatcher.

Each agent has its own pricing table (or delegates to an external package).
``get_agent_pricing`` is the single entry-point for the rest of burnctl.
"""

# ── Gemini (per-million-token rates, USD) ────────────────────────

GEMINI_PRICING = {
    "gemini-3.1-pro-preview": {"input": 2.00, "output": 12.0, "cache_read": 0.20},
    "gemini-3-flash-preview": {"input": 0.50, "output": 3.0, "cache_read": 0.05},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.0, "cache_read": 0.31},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60, "cache_read": 0.04},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40, "cache_read": 0.025},
    "gemini-2.0-flash-lite": {"input": 0.075, "output": 0.30, "cache_read": 0.02},
}

# ── OpenAI / Codex (per-million-token rates, USD) ───────────────

OPENAI_PRICING = {
    "gpt-5.4": {"input": 2.50, "output": 15.0, "cache_read": 0.25},
    "gpt-5.4-pro": {"input": 30.0, "output": 180.0},
    "gpt-5.3-codex": {"input": 2.50, "output": 15.0, "cache_read": 0.25},
    "gpt-5.2-codex": {"input": 2.50, "output": 15.0, "cache_read": 0.25},
    "gpt-4o": {"input": 2.50, "output": 10.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "o3": {"input": 10.0, "output": 40.0},
    "o3-mini": {"input": 1.10, "output": 4.40},
    "codex-mini": {"input": 1.50, "output": 6.0},
}


def get_agent_pricing(agent_id):
    """Return the pricing table for *agent_id*.

    Returns
    -------
    dict or None
        A ``{model_id: {input, output, ...}}`` mapping.
        *None* for agents that self-report costs (e.g. Aider).
        An empty ``{}`` for local/free models.
    """
    if agent_id == "claude":
        try:
            from claude_usage.pricing import get_pricing
            return get_pricing()
        except ImportError:
            return {
                "claude-opus-4-6": {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_create": 6.25},
                "claude-sonnet-4-6": {"input": 1.0, "output": 5.0, "cache_read": 0.10, "cache_create": 1.25},
                "claude-opus-4-5": {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_create": 6.25},
                "claude-sonnet-4-5": {"input": 1.0, "output": 5.0, "cache_read": 0.10, "cache_create": 1.25},
                "claude-haiku-4-5": {"input": 0.25, "output": 1.25, "cache_read": 0.025, "cache_create": 0.3125},
            }

    if agent_id == "gemini":
        return dict(GEMINI_PRICING)

    if agent_id == "codex":
        return dict(OPENAI_PRICING)

    if agent_id == "aider":
        # Aider tracks costs internally; no external pricing table needed.
        return None

    # Local models, unknown agents -- $0.
    return {}
