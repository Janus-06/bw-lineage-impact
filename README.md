# BW Lineage Impact

Local-first, read-only SAP BW/4HANA lineage and change-impact analyzer.

## Scope

- Runs locally per user/environment for security.
- No central hosted analyzer backend in the MVP.
- Does not create, update, delete, activate, transport, or otherwise mutate BW objects.
- Deterministic collection and analysis remain usable without an LLM.
- Optional LLM support targets a user-supplied local OpenAI-compatible endpoint only.

## Runtime inputs

BW connection values are supplied by the user at runtime. The project supports the reference MCP-style names:

- `BW_URL`
- `BW_USER`
- `BW_PASSWORD`
- `BW_CLIENT`
- optional `BW_LANGUAGE`

Optional local LLM values are also supplied by the user at runtime:

- `BWLI_LLM_BASE_URL`
- `BWLI_LLM_MODEL`
- `BWLI_LLM_API_KEY`

Do not commit real endpoints, users, passwords, tokens, snapshots, or audit logs.

## Quickstart

```bash
uv run bwli --version
uv run bwli collect --fixture tests/fixtures/sample-search.json --out .tmp/snapshot
uv run pytest -q
uv run ruff check .
uv run mypy src
```

Live BW collection is gated and should only be run with a read-only account after explicit setup:

```bash
BWLI_LIVE=1 BW_URL=<...> BW_USER=<...> BW_PASSWORD=<...> BW_CLIENT=<...> uv run bwli collect --live
```

The current live path is intentionally a safe placeholder for M1.
