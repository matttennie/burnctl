# burnctl — AI Coding Agent Usage Reporter

## What it does

burnctl is a unified CLI tool that aggregates usage and cost data across multiple AI coding agents (Claude Code, Gemini CLI, OpenAI Codex CLI, Aider, Ollama) into a single terminal dashboard.

## Quick reference

```bash
burnctl              # All detected agents
burnctl --claude     # Claude only
burnctl -j           # JSON output
burnctl -c           # Compact single-line
burnctl -A           # Accessible / screen-reader
burnctl -w 30        # Watch mode (refresh every 30s)
burnctl config       # View/set preferences
burnctl upgrade      # Open billing pages
```

## Project structure

- `burnctl/cli.py` — Entry point, argument parsing
- `burnctl/config.py` — Persistent config at `~/.config/burnctl/config.json`
- `burnctl/pricing.py` — Per-agent pricing tables
- `burnctl/report.py` — Data aggregation and multi-column rendering
- `burnctl/collectors/` — One module per agent (base.py, claude.py, gemini.py, codex.py, aider.py, local.py, stubs.py)
- `tests/` — pytest test suite

## Development

```bash
pip install -e .          # Editable install
pytest tests/ -v          # Run tests
flake8 burnctl/ tests/    # Lint
```
