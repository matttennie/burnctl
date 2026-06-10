# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- HuggingFace collector backed by the account billing API
  (`/api/settings/billing/usage-v2`); reads `HF_TOKEN` /
  `HUGGINGFACE_API_KEY` (billing read permission required) and falls back
  to local usage-log rows when the API is unavailable. Unrecognized API
  payloads are never guessed at — they warn and fall back
- ElevenLabs collector: character-quota counters from the subscription
  API, period bounds from the provider's own quota reset timestamp, plan
  name from the account tier. Quota units are labeled explicitly and no
  USD cost is invented
- Tavily collector: plan-credit counters from the usage API (credits map
  to the messages column), plan name from the account
- Collector hooks: `get_period` (provider-reported cycle bounds) and
  `hide_when_empty` (quota providers with zero usage render no row)
- Inworld, Groq, Mistral, Brave Search, Mercury, and Jina registered as
  usage-log providers (none expose a public usage API as of 2026-06)
- Scoped config (`burnctl config --<agent> ...`) now accepts all
  registered provider ids
- Rolling last-30-days reporting window for pay-as-you-go API providers
  (OpenRouter, HuggingFace, usage-log providers); billing-day periods now
  apply only to subscription agents. Days Left shows N/A for rolling
  windows, and providers with no data are omitted instead of rendering a
  misleading $0.00 row

### Fixed

- MODEL BREAKDOWN shows `In: N/A` instead of `In: 0` when input tokens are
  not tracked per period (Claude's stats cache is output-only); explicit
  zeros still render as 0
- HuggingFace now appears in MODEL BREAKDOWN with its per-provider
  breakdown (requests + cost share); the HF API exposes no model-level or
  token-level data. Tiny nonzero costs render as `<$0.01`, never `$0.00`
- Cell values longer than a column (e.g. `pay-as-you-go` in narrow
  multi-agent layouts) no longer push the box border out of alignment
- `default_agents` config key is now honored: with no agent flags, the report
  is limited to the configured agents (explicit flags and `--all` still
  override). It was documented and persisted but never read
- Codex `go` plan is now priced at $8/mo from `CODEX_PLAN_PRICES`; it was
  accepted by config validation but silently priced at $0 by the collector
- fish shell hook now emits fish-native syntax (`set -gx` / `set -e`); it
  previously sourced a POSIX `export`/`unset` env file, which fish cannot
  parse, breaking every new fish shell after setup
- OpenRouter proxy now sends `Content-Length` for buffered responses and
  closes the connection after SSE streams; HTTP/1.1 keep-alive clients
  previously hung waiting for the end of the response body
- `--until` without `--since` now errors instead of being silently ignored
- `-i/--interval` now validates and normalizes interval aliases
  (e.g. `yearly` → `yr`) the same way `burnctl config billing_interval` does
- `ApiUsageCollector._file` referenced the old `USAGE_FILE` constant after the Orchard removal — any caller that did not pass an explicit `usage_file` would hit a `NameError` at runtime
- OpenRouter proxy `_parse_json_usage` wrote zero-token ledger records for streamed SSE chunks that contained no `usage` object, overwriting the real record on non-final frames
- OpenRouter proxy `_parse_json_usage` ignored a top-level `reasoning_tokens` field when `completion_tokens_details` was absent
- Stale `User-Agent: burnctl/0.1.0` in OpenRouter and pricing HTTP clients; now derives from `burnctl.__version__`
- `pyproject.toml` `keywords` was misplaced under `[project.optional-dependencies]`, producing a phantom `burnctl[keywords]` extra and no real keyword metadata
- `pyproject.toml` `[claude]` extra pointed at `claude-usage`, which is not on PyPI; the README install step would fail for every public user
- Dangling lint, type, and long-line issues surfaced by the post-Orchard/post-Aider refactors

### Changed

- VALUE & ROI is now scoped to the current billing cycle: API Value shows
  the period's API-equivalent value and Value Ratio divides it by the plan
  price for that cycle. The all-time ratio was dropped because subscription
  levels change over time, making an all-time ratio against the current
  price incorrect. `alltime_cost` is still exported in JSON and CSV
- Gemini and Codex collectors now cache parsed session files by
  (mtime, size) in top-mode, implementing the stale-file optimization the
  0.3.2 notes claimed; unchanged files are no longer re-parsed every refresh
- Pricing history file is cached in memory and the static-table snapshot
  check runs once per process; previously the history JSON was re-read from
  disk twice per pricing lookup, per message/checkpoint
- Codex token checkpoints are walked once instead of twice (all-time and
  period accounting share each delta and pricing lookup)
- `usage.jsonl` provider entries are parsed once per file state instead of
  once each for discovery, availability, and stats
- Upgraded project classifier from Alpha to Beta to reflect shipping maturity
- `.pre-commit-config.yaml` now also runs mypy so local hooks match CI enforcement
- `.flake8` no longer blanket-suppresses `E501`/`F541` inside `burnctl/report.py`
- Dropped the `_parse_sse_usage` helper in favour of streaming `_parse_sse_line` directly; tests exercise the same replay path the request handler uses

## [0.3.2] - 2026-04-19

### Added

- `burnctl --top-mode` (-L) auto-refreshing dashboard for real-time token burn monitoring
- "Last Active" field for all agents and providers to show the most recent activity date
- SIGTERM handling for OpenRouter proxy to support clean shutdowns in background services
- Timestamped pricing history for Gemini and Codex so historical totals use the price in effect at event time

### Changed

- Bumped version to 0.3.2
- Optimized GeminiCollector and CodexCollector to skip stale session files in top-mode
- Refactored OpenRouter proxy to stream SSE responses line-by-line (zero buffering OOM protection)
- Increased OpenRouter API reliability with auto-adjusting timeouts in live mode
- Standardized all collectors to return historical ROI data even when current period usage is zero
- Removed Aider support and the repo-local Claude scaffolding files
- Removed unsupported private-only provider integrations from the public branch
- Billing periods now start at local midnight on the configured billing day
- Report totals now consistently use only the agents shown in the main grid
- Claude period cost is now explicitly labeled as estimated when derived from all-time model ratios
- Model breakdown shares now use displayed total tokens and no longer show misleading current-price columns

### Fixed

- CodexCollector 0-usage bug: history.jsonl messages are now correctly attributed to the current period
- `NoneType` and `AttributeError` crashes in several collectors when handling `null` JSON payloads
- Snapshot mismatches and test mock regressions from report structure changes
- OpenRouter proxy now correctly handles hop-by-hop headers and Content-Length during streaming

## [0.3.1] - 2026-04-17

### Added

- Installed man page at `man burnctl`
- Manual reference in `burnctl --help`

### Changed

- Bumped version to 0.3.1
- README install instructions now use GitHub/local install paths instead of PyPI
- Package metadata now uses the GitHub noreply contact email
- Per-agent billing config now uses scoped syntax like `burnctl config --codex billing_plan plus billing_day 18`

### Fixed

- Codex per-agent billing day now honors `codex_billing_day`
- Gemini current-period activity no longer gets hidden by stale session file mtimes
- Python installs now place the man page under `share/man/man1` so `man burnctl` works

## [0.3.0] - 2026-04-03

### Added

- Per-agent billing day support for Claude, Gemini, and Codex
- Cache hit % visibility in MODEL BREAKDOWN for Claude and Gemini models
- New models in pricing tables: gpt-5.4-mini, gpt-5.4-nano, gpt-5.3-chat, o3-pro, o4-mini, gemini-3.1-flash-lite, gemini-2.5-flash-lite

### Changed

- Bumped version to 0.3.0
- MODEL BREAKDOWN column alignment now uses two-pass rendering; name column width computed from longest model name across all rows
- Pricing column width increased from 6 to 8 chars to accommodate rates like `$0.30/M`
- Updated Gemini ai_pro plan price from $25 to $19.99 (Google One Premium)

### Fixed

- Claude Sonnet 4.5/4.6 pricing: was $1/$5 (Haiku prices), corrected to $3/$15
- Claude Haiku 4.5 pricing: was $0.25/$1.25 (Haiku 3 prices), corrected to $1/$5
- Gemini 2.5 Flash pricing: was $0.15/$0.60, corrected to $0.30/$2.50
- Gemini 2.5 Pro cache_read: was $0.31, corrected to $0.125
- OpenAI o3 pricing: was $10/$40 (o3-deep-research prices), corrected to $2/$8
- OpenAI gpt-5.3-codex/gpt-5.2-codex: was $2.50/$15, corrected to $1.75/$14
- OpenAI codex-mini: was $1.50/$6, corrected to $0.75/$3
- Added missing cache_read rates for gpt-4o, gpt-4o-mini, o3-mini
- Colored output pricing alignment (ANSI codes no longer break column padding)

### Removed

- `--watch` / `-w` and `--top-mode` CLI flags and all related code
- DebGPT stub collector and `--debgpt` flag
- `.github/workflows/build-deb.yml` and `scripts/build-deb.sh` (Debian package build)
- `test-debian` CI job

## [0.2.0] - 2026-03-17

### Added

- `burnctl proxy openrouter` for request-level OpenRouter instrumentation
- Local OpenRouter request ledger at `~/.local/share/burnctl/openrouter-usage.jsonl`
- Safe OpenRouter-only shell export helper via `burnctl proxy openrouter --print-shell`
- Proxy environment safety checks via `burnctl proxy openrouter --doctor`
- `burnctl setup openrouter` for explicit one-time installation
- Automatic OpenRouter bootstrap on normal interactive runs when keys are present and setup is missing

### Changed

- OpenRouter usage no longer relies on the generic provider usage log as the primary source
- OpenRouter model pricing now comes from the provider models API
- Report output now labels OpenRouter data provenance and freshness explicitly
- When present, local OpenRouter ledger data is merged after the provider activity cutoff for current-day visibility

### Previously Added

- API usage collector: auto-discovers providers (OpenRouter, HuggingFace, etc.) from `~/.config/burnctl/usage.jsonl`
- Each provider appears as its own agent in the report with per-model breakdown
- `--no-activity` flag and `no_activity` config to hide the DAILY ACTIVITY section
- N/A display for Pace and Value Ratio on pay-as-you-go providers
- Provider-specific gradient colors (OpenRouter: dark gray, HuggingFace: amber)
- Claude collector gap-fill: scans raw session JSONLs when stats-cache is stale

### Changed

- Bumped version to 0.2.0
- Updated snapshot tests for new render output

## [0.1.0] - 2026-03-13

### Added

- Initial release of burnctl — unified AI coding agent usage reporter
- Collector-based architecture with per-agent plugins
- Claude Code collector (reads `~/.claude/stats-cache.json`)
- Gemini CLI collector (parses `~/.gemini/` session history)
- OpenAI Codex CLI collector (parses `~/.codex/sessions/` JSONL)
- Local/Ollama collector (detection stub, always $0)
- Stub collector for OpenCode (future support)
- Multi-column terminal report with box drawing and ANSI colors
- JSON, compact, accessible, and CSV export output formats
- Persistent configuration at `~/.config/burnctl/config.json`
- `burnctl config` subcommand for viewing/setting preferences
- `burnctl upgrade` subcommand for opening billing pages
- Cross-platform support (Linux, macOS, Windows)
- Python 3.8–3.13 compatibility
- CI/CD with GitHub Actions (18 OS × Python matrix + vermin lint)
