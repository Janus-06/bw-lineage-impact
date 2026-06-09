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
- optional `BW_VERIFY_SSL`
- optional `BW_CA_BUNDLE` for a corporate CA PEM file when internal TLS inspection is used
- optional `BW_TRUST_ENV` to let HTTPX honor proxy/`NO_PROXY` environment settings

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

Current live-BW readiness note: the project has GET-only calls for `bw_search`, `bw_get_dataflow`, `bw_xref`, `fetch_hcpr`, and `fetch_adso`. Live smoke/snapshot/dataflow rendering is available for controlled sandbox use only: it remains opt-in, local-only, and requires explicit read-only confirmation before any SAP BW metadata call.

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
BWLI_LIVE=1 \
BW_URL=<user-supplied> \
BW_USER=<user-supplied> \
BW_PASSWORD=<user-supplied> \
BW_CLIENT=<user-supplied> \
BW_CA_BUNDLE=<optional-corporate-ca-pem-path> \
NO_PROXY=<optional-bw-host> \
uv run bwli collect --live --confirm-read-only --search-term Z* --out .tmp/live-snapshot
```

### Live read-only smoke and snapshot collection

The web UI's **Settings** drawer drives the read-only live flow in three steps:

1. **Settings → Runtime / Diagnostics → 설정 저장** — store BW URL/USER/PASSWORD/CLIENT in
   the backend process memory only (never persisted to disk).
2. **Settings → Test connection → 연결 테스트 실행** — calls `POST /api/v1/connection/test`,
   which runs a single read-only `bw_search` probe. Per-operation status, item counts, and
   redacted error messages are shown inline (BW host, query string, and password are scrubbed
   from any visible error).
3. **Settings → Snapshot capture → Live GET capture** — disabled until the current session
   has at least one successful Test connection. Use the *Object type* select (ADSO/HCPR/RSDS/
   DSO/IOBJ/MPRO/CPRO/BCT/TRFN/QUERY/NATIVE_SQL_VIEW) and, if desired, a narrow *Search terms*
   prefix (e.g. `ZADSO_`). Broad `*` searches are not auto-run.

Backend snapshot capture survives per-object failures: when one object's dataflow/xref call
fails, the successful payloads are still persisted and the response includes a `capture`
field with `succeeded`, `failed`, and redacted `operations[]` summaries.

The read-only `/api/live/dataflow` endpoint renders BW Dataflow XML into Mermaid/Markdown/JSON.
For `RSDS`, also provide the source system so the 30-character-padded object name can be
built correctly.

CLI live collection remains gated by `BWLI_LIVE=1` and runtime environment variables:

```bash
BWLI_LIVE=1 \
BW_URL=<user-supplied> \
BW_USER=<user-supplied> \
BW_PASSWORD=<user-supplied> \
BW_CLIENT=<user-supplied> \
BW_LANGUAGE=EN \
BW_VERIFY_SSL=true \
BW_CA_BUNDLE=<optional-corporate-ca-pem-path> \
NO_PROXY=<optional-bw-host> \
uv run bwli collect --live --confirm-read-only --search-term Z* --object ZCUBE --object-type ADSO --dataflow-direction downwards --dataflow-levels 3 --out .tmp/live-snapshot
```

If a corporate certificate is provided as a `.cer`, convert it to a PEM file outside the repo,
then point `BW_CA_BUNDLE` at that PEM path. Never commit real endpoints, user IDs, passwords,
certificates, snapshots, or audit logs.

This command performs read-only `bw_search`, `bw_get_dataflow`, and `bw_xref` metadata calls
and writes a local snapshot manifest under the chosen output directory. Do not commit `.tmp/`
or any live snapshot/report that may contain internal BW object metadata.
