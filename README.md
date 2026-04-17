# burnctl

Know what you're burning. One terminal, all your AI agents.

Reads local session data and provider APIs. Spits out tokens, costs, model breakdowns, and ROI — no telemetry, no phoning home beyond what you already auth'd.

## Agents

| Agent | Source |
|-------|--------|
| Claude Code | `~/.claude/stats-cache.json` |
| Gemini CLI | `~/.gemini/` sessions |
| Codex CLI | `~/.codex/sessions/*.jsonl` |
| Aider | `.aider.chat.history.md` |
| OpenRouter | OpenRouter activity API + local request ledger |
| HuggingFace et al. | `~/.config/orchard/usage.jsonl` |
| Ollama | Detection only, $0 |
| OpenCode | Stub — PRs welcome |

## Install

```
pip install git+https://github.com/matttennie/burnctl.git
```

Local checkout:

```bash
git clone https://github.com/matttennie/burnctl.git
cd burnctl
python -m pip install -U .
python -m pip install -U ".[claude]"   # pulls claude-usage for tighter pricing
```

Manual page:

```bash
man burnctl
```

## Usage

```
burnctl                     # all agents, current period
burnctl --claude --gemini   # just those two
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
```

### OpenRouter proxy

Routes OpenRouter-aware traffic through a local proxy for live request-level tracking. Does not touch Claude, Gemini, or Codex subscription flows.

```
burnctl setup openrouter            # one-time install (LaunchAgent + shell hook)
burnctl setup openrouter --status   # health check
burnctl proxy openrouter            # run the proxy directly
burnctl proxy openrouter --print-shell  # emit safe shell exports
burnctl proxy openrouter --doctor   # env safety audit
```

Ledger: `~/.local/share/burnctl/openrouter-usage.jsonl`

### Config

`~/.config/burnctl/config.json`

```
burnctl config                      # dump
burnctl config billing_day 15
burnctl config claude_plan max5x
burnctl config codex_billing_day 29 # per-agent billing day (0 = global)
burnctl config theme colorblind
```

Keys: `billing_day`, `billing_interval`, `claude_plan`, `claude_billing_day`, `gemini_plan`, `gemini_billing_day`, `codex_plan`, `codex_billing_day`, `default_agents`, `theme`, `no_color`, `simple`, `compact`.

### Upgrade

```
burnctl upgrade claude    # opens billing page
burnctl upgrade --all     # all of them
```

## Internals

Collector pattern. Each agent implements `BaseCollector`. `discover_collectors()` auto-detects API providers from the Orchard log.

```
burnctl/
├── cli.py                # arg parsing, dispatch
├── config.py             # ~/.config/burnctl/config.json
├── pricing.py            # per-model rate tables
├── report.py             # aggregation + rendering
├── openrouter_ledger.py  # local request ledger
├── openrouter_proxy.py   # MITM-lite for OpenRouter
├── openrouter_setup.py   # LaunchAgent + shell bootstrap
└── collectors/
    ├── base.py           # ABC
    ├── claude.py
    ├── gemini.py
    ├── codex.py
    ├── aider.py
    ├── api_usage.py      # OpenRouter, HuggingFace, etc.
    ├── local.py          # Ollama
    └── stubs.py          # OpenCode
```

## Requirements

Python 3.8+. Zero required deps. Optional: `claude-usage`.

## License

MIT
