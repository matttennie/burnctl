# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-04-03

### Added

- Per-agent billing day config (`claude_billing_day`, `gemini_billing_day`, `codex_billing_day`); set to 0 to use global `billing_day`
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

- OpenRouter usage no longer relies on Orchard logs as the primary source
- OpenRouter model pricing now comes from the provider models API
- Report output now labels OpenRouter data provenance and freshness explicitly
- When present, local OpenRouter ledger data is merged after the provider activity cutoff for current-day visibility

### Previously Added

- API usage collector: auto-discovers providers (OpenRouter, HuggingFace, etc.) from `~/.config/orchard/usage.jsonl`
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
- Aider collector (parses `.aider.chat.history.md` cost lines)
- Local/Ollama collector (detection stub, always $0)
- Stub collectors for Cline, OpenCode (future support)
- Multi-column terminal report with box drawing and ANSI colors
- JSON, compact, accessible, and CSV export output formats
- Persistent configuration at `~/.config/burnctl/config.json`
- `burnctl config` subcommand for viewing/setting preferences
- `burnctl upgrade` subcommand for opening billing pages
- Cross-platform support (Linux, macOS, Windows)
- Python 3.8–3.13 compatibility
- CI/CD with GitHub Actions (18 OS × Python matrix + vermin lint)
