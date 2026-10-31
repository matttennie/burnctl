# Security

## Threat model

burnctl is a **local-only** reporting tool. It reads session files under your home directory and calls provider APIs only with keys you already have in your environment. It has no telemetry and no hosted service.

- **Local files**: reads agent session and stats files under your home directory.
- **Credentials**: reads `OPENROUTER_API_KEY` / `OPENROUTER_MGMT_API_KEY`, `HF_TOKEN` / `HUGGINGFACE_API_KEY`, `ELEVENLABS_API_KEY`, and `TAVILY_API_KEY` from the environment. Keys are used only for requests to the matching provider and are never stored or transmitted elsewhere.
- **Proxies**: the optional OpenRouter and HuggingFace proxies bind `127.0.0.1` only. Their ledgers record usage metadata (model, ids, token counts, cost, timestamps), never prompt or completion text or headers.
- **Telemetry**: none.

## Hardening

- **File modes**: the config, env file, and ledgers are written `0600` inside `0700` directories.
- **Size guards**: parsed files are size-capped (config 1 MiB, session and stats files 50 MiB, ledgers and usage logs 100 MiB); larger files are skipped with a warning.
- **Config loading**: the loader merges known keys only.
- **Shell changes**: `burnctl setup openrouter` shows what it will change, requires explicit confirmation, and has `--uninstall`.

## Supported Versions

| Version | Supported |
| ------- | --------- |
| 0.4.x   | Yes       |
| < 0.4.0 | No        |

## Reporting a Vulnerability

Report security issues privately via [GitHub Security Advisories](https://github.com/matttennie/burnctl/security/advisories/new). Do not open a public issue for security-sensitive bugs.

We aim to acknowledge all reports within 48 hours and provide regular status updates until the issue is resolved.
