# burnctl

[![CI](https://github.com/matttennie/burnctl/actions/workflows/ci.yml/badge.svg)](https://github.com/matttennie/burnctl/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg)](pyproject.toml)

Local AI usage and cost tracking for Claude Code, Antigravity CLI, Codex CLI, Grok Build, OpenRouter, and related tools.

## macOS desktop preview

A focused, read-only native dashboard is available as a local build. Run
`bash macos/build-app.sh`, then open `dist/Burnctl.app`. The app bundles its
Python runtime; this preview targets Intel macOS 26 and is not published or
notarized. See [the desktop guide](docs/desktop.md) for setup, testing, and data
limitations.

The desktop app and CLI stay in this repository and share the same Python
collector and reporting engine. Native app sources and packaging live under
`macos/`; there is no separate private desktop repository or duplicate engine.

## What it does

Reads local session data plus provider APIs you already use, then shows tokens, costs, billing-period totals, model breakdowns, and ROI in one terminal view. No extra telemetry, no separate hosted service. Zero required deps. Optional: `claude-usage`.

## Supported agents

| Agent | Source |
|-------|--------|
| Claude Code | `~/.claude/stats-cache.json` |
| Antigravity CLI | `~/.gemini/` sessions |
| Codex CLI | `~/.codex/sessions/*.jsonl` |
| Grok Build | `~/.grok/sessions/*/*/usage.json` (honors `GROK_HOME`); cost from the server-reported ledger when present, else xAI list prices |
| OpenRouter | OpenRouter activity API + local request ledger |
| HuggingFace | HF billing API (`HF_TOKEN` with billing read permission) + local router proxy ledger for model names |
| ElevenLabs | Subscription API (`ELEVENLABS_API_KEY`) — character quota |
| Tavily | Usage API (`TAVILY_API_KEY`) — plan credits |
| Inworld, Groq, Mistral, Brave, Mercury, Jina | `~/.config/burnctl/usage.jsonl` (no public usage APIs) |

API providers (OpenRouter, HuggingFace, usage-log providers) report a
rolling last-30-days window instead of a billing-day period. Quota
providers (ElevenLabs, Tavily) show provider-reported current-cycle
counters and are hidden while usage is zero.

## Install

Requires Python 3.10 or later.

```bash
pip install git+https://github.com/matttennie/burnctl.git
```

Local checkout:

```bash
git clone https://github.com/matttennie/burnctl.git
cd burnctl
python -m pip install -U .
```

Manual page:

```bash
man burnctl
```

## Use

```
burnctl                     # all agents, current period
burnctl --claude --antigravity  # just those two
burnctl --grok              # just Grok Build
burnctl -p max5x -b 15     # Claude Max 5×, billing day 15
burnctl -P last             # previous billing period
```

### Output

```
burnctl -j            # JSON
burnctl -c            # one-liner
burnctl -A            # screen-reader friendly
burnctl -s            # skip the VALUE/ROI box
burnctl -n            # no ANSI
burnctl -t colorblind # theme: gradient | classic | colorblind | accessible
burnctl -e            # append to burnctl.csv
burnctl -e out.csv    # append to specific file
burnctl -L            # live auto-refreshing dashboard (top mode)
```

### OpenRouter proxy

Routes OpenRouter-aware traffic through a local proxy for live request-level tracking. Does not touch Claude, Antigravity, or Codex subscription flows. Nothing is installed automatically. `burnctl setup openrouter` prints what it will change (env file, a block in your shell rc file, a LaunchAgent) and asks `Proceed? [y/N]`; `--yes` skips the prompt and `--uninstall` reverses it.

```
burnctl setup openrouter            # prints changes, asks Proceed? [y/N]
burnctl setup openrouter --yes      # skip the prompt
burnctl setup openrouter --uninstall  # remove shell hook, LaunchAgent, env file
burnctl setup openrouter --status   # health check
burnctl proxy openrouter            # run the proxy directly
burnctl proxy openrouter --print-shell  # emit safe shell exports
burnctl proxy openrouter --doctor   # env safety audit
```

Ledger: `~/.local/share/burnctl/openrouter-usage.jsonl`

HuggingFace's billing API only reports per-provider usage; to see model
names in the breakdown, route OpenAI-compatible HF router clients
through the local proxy:

```
burnctl proxy huggingface               # run the proxy (port 8766)
burnctl proxy huggingface --print-shell # emit HF_BASE_URL export
```

Ledger: `~/.local/share/burnctl/huggingface-usage.jsonl`

### Config

`~/.config/burnctl/config.json`

```
burnctl config                      # dump
burnctl config billing_day 15
burnctl config theme colorblind
burnctl config --claude billing_plan max5x
burnctl config --codex billing_plan plus billing_day 29
burnctl config --openrouter billing_plan enterprise billing_day 10
```

Global keys: `billing_day`, `billing_interval`, `default_agents`, `theme`, `no_color`, `simple`, `compact`.

Scoped agent keys via `--<agent>`: `billing_plan`, `billing_day`.

### Upgrade

```
burnctl upgrade claude    # opens billing page
burnctl upgrade --all     # all of them
```

## Architecture

Collector pattern. Each agent implements `BaseCollector`; `ALL_COLLECTORS` registers them, and provider lists (flags, config ids, upgrade pages) are derived from the collectors. `discover_collectors()` also auto-detects provider rows from the burnctl usage log.

```
burnctl/
├── cli.py                # arg parsing, dispatch
├── config.py             # ~/.config/burnctl/config.json
├── pricing.py            # per-model rate tables
├── report.py             # aggregation + rendering
├── openrouter_ledger.py  # local request ledger
├── openrouter_proxy.py   # local logging proxy for OpenRouter
├── openrouter_setup.py   # LaunchAgent + shell bootstrap
└── collectors/
    ├── base.py           # ABC
    ├── claude.py
    ├── antigravity.py
    ├── codex.py
    ├── grok.py
    ├── api_usage.py      # OpenRouter, HuggingFace, etc.
    └── local.py          # local-model detection
```

Adding a provider:

1. Add a collector module under `burnctl/collectors/`.
2. Register it in `ALL_COLLECTORS` in `collectors/__init__.py`.
3. Set the class attributes `plan_prices`, `top_report`, and `model_prefixes`.
4. Optionally add a `_STATIC_PRICING` entry in `pricing.py`.

## Compliance

This tool was designed to comply with each provider's Terms of Service. However, users should review ToS themselves and use at their own discretion.

## Contributing

Contributions welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT
