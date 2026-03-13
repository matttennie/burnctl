"""Multi-agent pricing dispatcher.

Each agent has its own pricing table (or delegates to an external package).
``get_agent_pricing`` is the single entry-point for the rest of burnctl.
"""

# ── Gemini (per-million-token rates, USD) ────────────────────────

GEMINI_PRICING = {
    "gemini-2.5-pro": {"input": 1.25, "output": 10.0, "cache_read": 0.31},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60, "cache_read": 0.04},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40, "cache_read": 0.025},
    "gemini-2.0-flash-lite": {"input": 0.075, "output": 0.30, "cache_read": 0.02},
}

# ── OpenAI / Codex (per-million-token rates, USD) ───────────────

OPENAI_PRICING = {
    "gpt-5.3-codex": {"input": 2.50, "output": 10.0},
    "gpt-4o": {"input": 2.50, "output": 10.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "o3": {"input": 10.0, "output": 40.0},
    "o3-mini": {"input": 1.10, "output": 4.40},
    "o1": {"input": 15.0, "output": 60.0},
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
        from claude_usage.pricing import get_pricing
        return get_pricing()

    if agent_id == "gemini":
        return dict(GEMINI_PRICING)

    if agent_id == "codex":
        return dict(OPENAI_PRICING)

    if agent_id == "aider":
        # Aider tracks costs internally; no external pricing table needed.
        return None

    # Local models, unknown agents -- $0.
    return {}
