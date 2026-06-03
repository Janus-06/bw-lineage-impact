# CLAUDE.md

This project is a new read-only SAP BW/4HANA lineage and change-impact analyzer.

## Operating rules

- Start read-only. Do not add BW create/update/delete/activate/transport behavior to MVP.
- Deterministic graph extraction and impact rules come first.
- LLM features must be optional, bounded, auditable, and fed with sanitized inputs.
- LLM integration targets a local OpenAI-compatible API endpoint first; do not assume cloud LLM availability.
- LLM endpoint/model/API key and BW connection variables must be supplied by the user at runtime through prompts, CLI flags, env vars, or secret references. Do not hardcode or persist real values.
- Preserve compatibility with the reference MCP environment variable names where practical: `BW_URL`, `BW_USER`, `BW_PASSWORD`, `BW_CLIENT`, optional `BW_LANGUAGE`.
- All normal usage must run locally per user/environment. Do not design a central hosted analyzer backend for MVP.
- For non-graphical Native SQL View objects, LLM may explain SQL logic and suggest advisory optimizations, but deterministic SQL parsing/evidence extraction must come first.
- Prefer deployment simplicity: CLI first, Docker second, optional web UI/MCP later.
- Keep secrets out of git and out of persisted snapshots.

## Reference material

- Planning brief: `docs/brief.md`
- Reference repo inspected by Hermes: `/tmp/bw-modeling-mcp-inspect`
- Reference GitHub: https://github.com/dnic-dev/bw-modeling-mcp

## Current phase

Planning only until JC님 approves implementation slices.
