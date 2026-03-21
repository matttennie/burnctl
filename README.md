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
| API providers (OpenRouter, HuggingFace, etc.) | Full | `~/.config/orchard/usage.jsonl` |
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
```

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
