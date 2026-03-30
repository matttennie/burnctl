# burnctl

Unified CLI dashboard for AI coding agent usage and costs.

Aggregates usage data from multiple AI coding agents into a single terminal report — sessions, tokens, estimated API costs, value ratios, and model breakdowns.

## Supported Agents

| Agent | Status | Data Source |
|-------|--------|-------------|
| Claude Code | Full | `~/.claude/stats-cache.json` |
| Gemini CLI | Full | `~/.gemini/` session history |
| OpenAI Codex CLI | Full | `~/.codex/sessions/` JSONL |
| Aider | Full | `.aider.chat.history.md` |
| OpenRouter | Full | OpenRouter API activity + optional local request ledger |
| Other API providers (HuggingFace, etc.) | Full | `~/.config/orchard/usage.jsonl` |
| Ollama (local) | Detection | Always $0 |
| Cline, OpenCode, DebGPT | Stub | Planned |

## Installation

```bash
pip install burnctl
```

With Claude Code integration (imports pricing from `claude-usage`):

```bash
pip install burnctl[claude]
```

## Usage

```bash
# Report for all detected agents
burnctl

# Specific agents only
burnctl --claude --gemini

# Output formats
burnctl -j          # JSON
burnctl -c          # Compact single-line
burnctl -A          # Accessible (screen-reader friendly)
burnctl -e          # Export to CSV (default: burnctl.csv)
burnctl -e out.csv  # Export to specific file

# Display options
burnctl -s              # Simple (skip VALUE & ROI section)
burnctl -n              # No color
burnctl -t classic      # Theme: gradient, classic, colorblind, accessible

# Billing overrides
burnctl -p max5x    # Override Claude plan
burnctl -b 15       # Override billing day
burnctl -P last     # Show previous billing period

# Live dashboard
burnctl -w 30       # Refresh every 30 seconds

# OpenRouter proxy / request ledger
burnctl proxy openrouter
burnctl proxy openrouter --print-shell
burnctl proxy openrouter --doctor
burnctl setup openrouter
burnctl setup openrouter --status
```

## OpenRouter Accuracy

OpenRouter is now handled differently from the other provider rows:

- Settled usage comes from OpenRouter's provider-side daily activity API.
- Live current-day usage can be merged in from a local request ledger.
- The local request ledger is populated by running the built-in OpenRouter proxy and routing OpenRouter-aware clients through it.

The ledger file lives at:

```bash
~/.local/share/burnctl/openrouter-usage.jsonl
```

Normal `burnctl` runs now auto-bootstrap the OpenRouter integration on macOS when:

- an OpenRouter API key is present
- the proxy setup is missing
- the run is interactive

That one-time bootstrap installs:

- a LaunchAgent that keeps the local OpenRouter proxy running
- a shell snippet that sets only `OPENROUTER_BASE_URL`
- no global `OPENAI_BASE_URL` override

You can also run the setup explicitly:

```bash
burnctl setup openrouter
```

Check setup health:

```bash
burnctl setup openrouter --status
```

Run the proxy directly:

```bash
burnctl proxy openrouter
```

Print safe shell exports for OpenRouter-only proxying:

```bash
burnctl proxy openrouter --print-shell
```

That output intentionally sets only `OPENROUTER_BASE_URL` and unsets `OPENAI_BASE_URL` so you do not accidentally redirect native OpenAI-compatible clients you wanted to keep direct.

Check your current environment safety:

```bash
burnctl proxy openrouter --doctor
```

Important:

- `burnctl` does not automatically proxy Anthropic/Claude, Google/Gemini, or native OpenAI/Codex clients.
- The automatic setup only targets OpenRouter-aware clients and leaves native Claude, Gemini, and Codex subscription flows alone.
- OpenRouter current-day usage is only truly live for traffic that actually goes through the burnctl proxy.
- If no proxied traffic has been logged yet, the OpenRouter row remains provider-daily, not realtime.

## Configuration

Persistent settings live at `~/.config/burnctl/config.json`.

```bash
burnctl config                    # Show all settings
burnctl config billing_day 15     # Set billing day
burnctl config claude_plan max5x  # Set Claude plan
burnctl config theme colorblind   # Set color theme
```

Available settings: `billing_day`, `billing_interval`, `claude_plan`, `default_agents`, `theme`, `no_color`, `simple`, `compact`.

## Upgrade

Open billing/upgrade pages in your browser:

```bash
burnctl upgrade claude   # Open Claude billing page
burnctl upgrade --all    # Open all agent billing pages
```

## Architecture

burnctl uses a collector-based architecture. Each AI agent has a collector that implements `BaseCollector`:

```
burnctl/
├── cli.py              # Argument parsing, entry point
├── config.py           # Persistent configuration
├── openrouter_ledger.py# Local OpenRouter request ledger
├── openrouter_proxy.py # Local OpenRouter logging proxy
├── pricing.py          # Multi-agent pricing tables
├── report.py           # Aggregation and rendering
└── collectors/
    ├── base.py         # BaseCollector ABC
    ├── claude.py       # Claude Code collector
    ├── gemini.py       # Gemini CLI collector
    ├── codex.py        # OpenAI Codex collector
    ├── aider.py        # Aider collector
    ├── api_usage.py    # API provider collector (OpenRouter, HuggingFace, etc.)
    ├── local.py        # Ollama/local models
    └── stubs.py        # Future agent stubs
```

## Requirements

- Python 3.8+
- No required dependencies (optional: `claude-usage` for enhanced Claude pricing)

## License

MIT
