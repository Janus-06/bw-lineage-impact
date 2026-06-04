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
npm --prefix web install
npm --prefix web run build
```

## Local backend + frontend

Run both local servers for development:

```bash
./scripts/dev-local.sh
```

Then open:

```text
http://127.0.0.1:5173
```

This starts:

- Backend API: `http://127.0.0.1:8787`
- Frontend dev server: `http://127.0.0.1:5173`

The frontend talks to the local backend through Vite's `/api` proxy. If you use the `Runtime Settings` tab, BW credentials temporarily pass through browser memory and are sent only to the local backend.

Use the `Runtime Settings` tab to enter BW and optional local LLM runtime values from the web UI. The backend keeps these values in process memory only:

- Not written to `.env`, config files, snapshots, reports, or Git.
- Secret values are not returned by API responses; the UI only receives `[REDACTED]` status.
- Values are cleared when the backend process stops or when you click `Clear`.

Current live-BW readiness note: the project has a GET-only `BwClient` and endpoint builders for `bw_search`, `bw_get_dataflow`, and `bw_xref`, but the `collect --live` path is still intentionally gated/placeholder. Use the web settings now to prepare runtime config safely; actual SAP BW live collection still needs the collector wiring and sandbox smoke test before it should be considered production-ready.

You can also build the frontend and serve it from the Python backend only:

```bash
npm --prefix web install
npm --prefix web run build
uv run bwli serve --host 127.0.0.1 --port 8787
```

Then open:

```text
http://127.0.0.1:8787
```

Live BW collection is gated and should only be run with a read-only account after explicit setup:

```bash
BWLI_LIVE=1 BW_URL=<...> BW_USER=<...> BW_PASSWORD=<...> BW_CLIENT=<...> uv run bwli collect --live
```

The current live path is intentionally a safe placeholder for M1.

### Live read-only smoke and snapshot collection

The web UI includes a **Live BW Smoke** tab. It uses the BW runtime settings stored in the
local backend process memory and requires an explicit read-only confirmation checkbox before
making SAP BW metadata calls. API responses include operation summaries and local manifest
metadata only; BW passwords and LLM API keys are never returned.

CLI live collection remains gated by `BWLI_LIVE=1` and runtime environment variables:

```bash
BWLI_LIVE=1 \
BW_URL=<user-supplied> \
BW_USER=<user-supplied> \
BW_PASSWORD=<user-supplied> \
BW_CLIENT=<user-supplied> \
BW_LANGUAGE=EN \
BW_VERIFY_SSL=true \
uv run bwli collect --live --search-term Z* --object ZCUBE --out .tmp/live-snapshot
```

This command performs read-only `bw_search`, `bw_get_dataflow`, and `bw_xref` metadata calls
and writes a local snapshot manifest under the chosen output directory. Do not commit `.tmp/`
or any live snapshot/report that may contain internal BW object metadata.
