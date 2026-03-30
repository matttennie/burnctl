# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-03-17

### Added

- `burnctl proxy openrouter` for request-level OpenRouter instrumentation
- Local OpenRouter request ledger at `~/.local/share/burnctl/openrouter-usage.jsonl`
- Safe OpenRouter-only shell export helper via `burnctl proxy openrouter --print-shell`
- Proxy environment safety checks via `burnctl proxy openrouter --doctor`

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
- Stub collectors for Cline, OpenCode, DebGPT (future support)
- Multi-column terminal report with box drawing and ANSI colors
- JSON, compact, accessible, and CSV export output formats
- Persistent configuration at `~/.config/burnctl/config.json`
- `--watch` mode for continuous refresh
- `burnctl config` subcommand for viewing/setting preferences
- `burnctl upgrade` subcommand for opening billing pages
- Cross-platform support (Linux, macOS, Windows)
- Python 3.8–3.13 compatibility
- CI/CD with GitHub Actions (18 OS × Python matrix + vermin lint)
