# BW MCP v0.8.0 + Understand Anything Integration Plan

> **Status:** Planning artifact only. No implementation in this file.
> **Primary model run:** Claude Code `--model opus`, actual `modelUsage` included `claude-opus-4-8`.
> **Raw Claude artifact:** `.hermes/plans/claude-runs/2026-06-17-bw-mcp-v080-understand-anything/result.md`
> **Project branch inspected:** `feat/live-object-workbench`

## 0. Current verified state

- Current repo checks passed before planning:
  - `uv run pytest -q` → `241 passed, 1 warning`
  - `uv run ruff check .` → passed
  - `uv run mypy src` → passed
- Latest `bw-modeling-mcp` clone inspected:
  - repo: `dnic-dev/bw-modeling-mcp`
  - commit: `9a9e394 feat: v0.8.0 - runtime request tools, BW Bridge cookie auth, RSDS lifecycle`
  - version: `0.8.0`
  - `npm install && npm run build` succeeded in temp clone; initial `npm ci` failed due lock/package metadata mismatch in temp verification.
- Latest `Understand-Anything` clone inspected:
  - repo: `Egonex-AI/Understand-Anything`
  - commit: `ba9ba1f Merge pull request #364 from smjeong84/feat/add-kiro-platform-support`
  - relevant docs/source: Korean README, plugin skills, graph schema/types, fingerprint/change-classifier/diff/context builders.

## 1. Verdict

### Adopt now

1. **Read-only request monitoring from BW MCP v0.8.0**
   - Adopt `bw_list_requests` and `bw_get_request` only.
   - Use them as freshness/runtime evidence attached to BW graph nodes.
   - Do **not** add DTP run or request activation.
2. **BW MCP read metadata coverage from v0.4–v0.7**
   - Process chains and variants.
   - Source systems and DataSources.
   - Query/CKF/RKF/Structure/CompositeProvider evidence.
   - Discovery-driven media type negotiation and query content-type version handling.
3. **Understand Anything graph concepts, not the plugin runtime**
   - Extend our graph with optional layer/tour/summary/tags/complexity/edge weight concepts.
   - Add deterministic fingerprints and change-grade classification.
   - Later, use sanitized LLM to generate Korean explanation/tour/domain summaries.

### Defer behind gates

- `BW_COOKIE_FILE` support for BW Bridge / SAML / OAuth-fronted systems.
- Real data preview/query-data tools (`bw_query_data`, `bw_get_filter_values`, `bw_preview_datasource`).
- Domain graph, semantic search, graph chat, rich dashboard exploration.

### Reject for MVP

- Any write/execute/activation/transport behavior:
  - `bw_run_dtp`
  - `bw_activate_request`
  - `bw_create_*`, `bw_update_*`, `bw_activate`, `bw_set_query_roles`, `BwClient.rawPut`
- Central hosted analyzer backend.
- Cloud LLM dependency as a default path.
- Direct dependency on Understand Anything plugin runtime.

## 2. BW MCP v0.8.0 mapping

| BW MCP capability | Type | Our use | Decision |
|---|---:|---|---|
| `bw_list_requests` | read | Add recent load/request status to InfoProvider nodes: status, last process/action, records, timestamp, TSN | Adopt now |
| `bw_get_request` | read | Drill into one request: header, DTP info, process steps, message log; cite as runtime evidence | Adopt now |
| `bw_run_dtp` | execute | Starts a DTP load | Reject |
| `bw_activate_request` | write/runtime activation | Moves inbound data into active table/change log | Reject |
| `BW_COOKIE_FILE` | auth | Browser-exported cookie auth for BW Bridge/SAML/OAuth systems | Defer, security gate |
| Discovery-driven media type negotiation | robustness | Improve `client.py` live compatibility; reduce 415/406 style failures | Adopt now |
| Query content-type version negotiation | robustness | Improve query read path on higher SP/backend versions | Adopt now |
| Process chain / process variant reads | read | Add RSPC nodes, steps, conditional branches, variant details to lineage/impact graph | Adopt now |
| Source system / DataSource reads | read | Add LSYS/RSDS nodes, fields, source-system metadata | Adopt now |
| Query/CKF/RKF/Structure/CompositeProvider reads | read | Improve report/query-side downstream impact and field-level dependencies | Adopt now |
| Query/data preview/filter values/DataSource preview | read but data-bearing | Could help validation, but risks real data exposure | Defer, explicit data gate |

## 3. Understand Anything mapping

| Understand Anything concept | BW Lineage Impact adaptation | Layer |
|---|---|---|
| `KnowledgeGraph{nodes,edges,layers,tour}` | Evolve `BwGraph` to schema v1.1 with optional `layers` and `tour` | deterministic core + optional LLM |
| `GraphNode.summary/tags/complexity` | Object summaries/tags/complexity; complexity computed deterministically from field count, joins, routines, request/error evidence | deterministic first, LLM optional for prose |
| `GraphEdge.direction/weight/description` | Keep existing `confidence`; add numeric `weight` and optional description for visualization/scoring | deterministic |
| `Layer{nodeIds}` | BW layers: source, acquisition, staging, transformation, provider, query/reporting, process/runtime | deterministic rules |
| `TourStep` | Guided lineage/impact walkthrough for UI | optional LLM post-processing |
| `DomainMeta` | Business-domain graph from InfoAreas/object names/descriptions/tags | gated LLM |
| Fingerprint store | Snapshot object fingerprint for incremental analysis | deterministic |
| Change classifier: `SKIP / PARTIAL_UPDATE / ARCHITECTURE_UPDATE / FULL_UPDATE` | Diff triage and impact recalculation priority | deterministic |
| Dashboard + diff overlay | Local web workbench graph overlays and changed/affected highlighting | later UI |
| Chat/context builder | Bounded graph question answering over sanitized evidence | later LLM |
| `--language ko` | Korean report/tour/explanation mode | config + LLM prompt |

## 4. Recommended architecture

```text
CLI / Local Web Workbench
  ├─ Deterministic core
  │   ├─ graph.py: BwGraph v1.1, Layer, TourStep, weight/summary/tags/complexity
  │   ├─ lineage.py / impact.py / field_lineage.py
  │   ├─ snapshot.py / repository.py / store/catalog.py with fingerprints
  │   └─ diff/change-grade classifier
  ├─ GET-only collection adapters
  │   ├─ client.py: discovery/media type/content-type negotiation
  │   ├─ endpoints.py: search/dataflow/xref + process/query/RSDS/request-read endpoints
  │   └─ live.py: explicit read-only gate, no write verbs exposed
  ├─ Optional LLM post-processing
  │   ├─ sanitizer.py
  │   ├─ openai_compatible.py endpoint guard + audit log
  │   └─ citation-bound explanation/tour/domain summaries
  └─ Local artifacts only
      ├─ snapshots
      ├─ reports
      ├─ graph JSON
      └─ audit logs
```

Boundary rules:

1. Runtime BW calls stay GET-only for MVP.
2. No `POST/PUT/DELETE` BW operations, even if BW MCP has implementations.
3. Deterministic graph/lineage/impact works offline from fixtures/snapshots.
4. LLM sees sanitized graph slices only and cannot call BW APIs.
5. Secrets and cookies are runtime inputs only; never persisted to snapshots, reports, or git.

## 5. Implementation slices

### Slice A — Graph schema v1.1 and layers/tour scaffolding

Lowest risk: no live BW, no LLM, no web dependency.

Files:

- `src/bwli/graph.py`
- `src/bwli/dataflow.py`
- `src/bwli/lineage.py`
- `src/bwli/impact.py`
- tests around graph serialization and backwards compatibility

Tasks:

1. Add optional `summary`, `tags`, `complexity` to `BwNode`.
2. Add optional `weight` and `description` to `BwEdge` while preserving `confidence`.
3. Add `Layer` and `TourStep` models to `BwGraph` with default empty lists.
4. Add deterministic BW layer assignment rules by object type.
5. Ensure old graph JSON still loads.

### Slice B — Fingerprints and change-grade classifier

Files:

- `src/bwli/snapshot.py`
- `src/bwli/store/catalog.py`
- `src/bwli/impact.py`
- `src/bwli/cli.py`
- tests for fingerprints and diff grades

Tasks:

1. Store normalized object fingerprints.
2. Classify snapshot diff as `SKIP`, `PARTIAL_UPDATE`, `ARCHITECTURE_UPDATE`, or `FULL_UPDATE`.
3. Use grade to prioritize downstream impact calculation.

### Slice C — Read-only metadata endpoint expansion

Files:

- `src/bwli/client.py`
- `src/bwli/endpoints.py`
- `src/bwli/live.py`
- parsers/tests for endpoint payloads

Tasks:

1. Add discovery-driven media type negotiation.
2. Add process-chain and process-variant read path.
3. Add LSYS/RSDS read path.
4. Add Query/CKF/RKF/Structure/CompositeProvider read path.
5. Enforce GET-only and `--confirm-read-only` gates.

### Slice D — Runtime request freshness evidence

Files:

- `src/bwli/endpoints.py`
- `src/bwli/live.py`
- `src/bwli/graph.py`
- `src/bwli/server.py`
- `web/src/*`

Tasks:

1. Add `list_requests` and `get_request` GET endpoints.
2. Attach request status to graph node metadata.
3. Render freshness/status badges in the web workbench.
4. Explicitly exclude run/activate request actions from CLI/API surface.

### Slice E — Guided tour and domain graph

Files:

- `src/bwli/llm/lineage_advisor.py`
- `src/bwli/llm/impact_advisor.py`
- optional new `src/bwli/domain.py`
- `web/src/*`

Tasks:

1. Generate citation-bound guided impact tours.
2. Add opt-in Korean explanations.
3. Add domain graph only after sanitizer/audit gates are validated.

### Slice F — BW Bridge cookie auth and data preview gates

Files:

- `src/bwli/config.py`
- `src/bwli/client.py`
- `src/bwli/store/secret_guard.py`
- `src/bwli/redact.py`
- tests around cookie/file path redaction and permissions

Tasks:

1. Add `BW_COOKIE_FILE` as optional read-only auth input.
2. Validate file permissions and redact cookie names/values.
3. Keep cookie file out of snapshots/reports/UI responses.
4. Data preview/query tools remain separate and require explicit data-bearing approval.

## 6. UX/demo proposal

Current local web workbench can evolve in this order:

1. **Layered lineage graph**
   - Show BW layers as lanes/bands: Source → Acquisition → Staging → Transformation → Provider → Query/Reporting → Runtime.
2. **Impact diff panel**
   - Show change grade and affected downstream nodes.
3. **Request freshness badge**
   - Show last request status/records/timestamp/TSN for ADSO/InfoProvider nodes.
4. **Guided tour**
   - Next/previous steps through an impact path.
5. **Optional Korean LLM summary**
   - Render only from sanitized, cited evidence.

Demo should work fixture-only for steps 1–4. LLM and live BW should be optional.

## 7. Security and approval gates

| Gate | Applies to | Requirement |
|---|---|---|
| GET-only gate | All live metadata collection | Test that client/adapters expose no BW mutating verbs in MVP |
| Runtime request gate | `/sap/bc/http/sap/bw4/v1/manage` | Only list/get requests allowed; no execute/activate |
| Cookie gate | `BW_COOKIE_FILE` | Explicit opt-in; redact; no snapshot/report persistence; permission checks |
| Data-bearing gate | query/data preview/filter values | Separate explicit approval; row caps; no LLM by default |
| LLM gate | summaries/tours/domain graph/chat | Sanitized evidence only; citation validation; local endpoint first; audit log |
| Artifact gate | snapshots/reports/graphs | No credentials, cookies, internal host tokens, or raw secrets |

## 8. Tests to add

- `test_graph_schema_v11_backward_compatibility`
- `test_layer_assignment_by_bw_object_type`
- `test_tour_roundtrip_serialization`
- `test_fingerprint_same_payload_no_change`
- `test_change_grade_skip_partial_architecture_full`
- `test_client_media_type_discovery_highest_xml_variant`
- `test_query_read_415_negotiation_fallback`
- `test_endpoints_get_only_for_runtime_request_monitor`
- `test_live_gate_blocks_without_confirm_read_only`
- `test_cookie_file_redaction_and_no_snapshot_persistence`
- `test_llm_tour_requires_sanitized_cited_evidence`

Standard verification commands:

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src
npm --prefix web run build
```

## 9. Recommended next PR

**Title:** `feat: add BW graph schema v1.1 layers and deterministic change grades`

Scope:

- Slice A + the minimal deterministic part of Slice B.
- No live endpoint additions.
- No LLM changes except preserving compatibility with existing output.
- No web UI changes except only if required by existing tests/build.

Acceptance criteria:

1. Existing 241 tests still pass.
2. New graph schema tests pass.
3. Old graph JSON fixtures load unchanged.
4. `diff` can emit a change grade.
5. No live BW, secrets, LLM, or external network needed for tests.

Rationale:

- This gives us the Understand Anything value foundation—layers, tours-ready structure, and diff/change triage—without touching SAP systems or credentials.
- It prepares the graph model for later BW MCP read-only endpoint expansion.
- It keeps the MVP boundary clean and safe.
