# BW Lineage Impact Product Re-plan Implementation Plan

> **For Hermes:** Use subagent-driven-development or Codex OMX/RALPLAN follow-up to implement this plan slice-by-slice.
> **Status:** planning-only; no implementation, no live BW call, no credential/config mutation.
> **Date:** 2026-06-25 KST.
> **Codex RALPLAN:** `omx exec ... $ralplan` produced an APPROVE plan in stderr; the CLI stop hook repeated because read-only sandbox could not clear `.omx/state` (`EPERM`). Extracted working artifact: `/tmp/bwli-codex-ralplan-extracted.md`.

**Goal:** Recenter the product around three user-visible jobs: **Lineage 이해**, **Impact 판단**, and **Ask BW / Review** LLM assistance over deterministic evidence.

**Architecture:** Keep the existing local-first, read-only Python/FastAPI + React stack. Do not add a central service or expose BW write/runtime execution. Simplify the web IA first, then progressively refactor Lineage/Impact into task-first workspaces and consolidate LLM outputs into one evidence-bound assistant surface.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLite local stores, `uv`/pytest/ruff/mypy; React + TypeScript + Vite under `web/`; optional local OpenAI-compatible LLM through the existing guarded transport.

---

## 1. Executive verdict

The current backend direction is sound, but the UI is drifting into **feature inventory**. A BW analyst does not open this product to press “Impact Brief / Business Summary / Agentic Review / Query evidence / SQL evidence” as separate features. They open it to answer:

1. **이 BW 객체는 어디서 오고 어디로 가나?**
2. **이 변경을 릴리즈하면 무엇이 깨질 수 있나?**
3. **근거를 보고 BW 질문을 빠르게 검토할 수 있나?**

So the product should become:

```text
Object Finder / Snapshot basis
  -> Lineage Workspace       # understand flow
  -> Impact Workspace        # judge change risk
  -> Ask BW / Review         # citation-bound assistant over current evidence
```

**Key product decision:** move “LLM/review/summary/tour” from scattered buttons into one Assistant/Review plane. Keep deterministic lineage/impact as the source of truth.

---

## 2. Grounding evidence

### 2.1 Repository state checked

- Repo: `/Users/jclee/Projects/bw-lineage-impact`
- Branch: `main...origin/main`, clean before plan generation.
- Latest visible commit: `2c93147 Merge pull request #9 from Janus-06/feat/agentic-impact-review-assistant`.
- Baseline verification:
  - `uv run pytest -q` → **457 passed, 1 warning**
  - `uv run ruff check .` → **All checks passed**
  - `uv run mypy src` → **Success: no issues found in 39 source files**
  - `npm --prefix web run test:slice-g` → **25 passed**
  - `npm --prefix web run build` → **built successfully**
  - `git diff --check` → **clean**

### 2.2 Current code facts

- Project rules in `CLAUDE.md` preserve: local-first, read-only, deterministic first, optional bounded LLM, runtime-supplied secrets only.
- `web/src/App.tsx:81-82` currently has `GLOSSARY_VISIBLE=false`, `IMPACT_UNIFIED=true`, so the visible product already moves toward fewer top-level tabs.
- Visible tabs at `web/src/App.tsx:2050-2057`: Lineage and Impact are visible; Query/SQL hidden by `IMPACT_UNIFIED`, Glossary hidden by flag.
- However, Lineage still exposes separate “Lineage 실행”, “Evidence Walkthrough”, “Business Summary” actions (`web/src/App.tsx:2314-2322`).
- Impact still exposes “Impact 실행”, “Impact Brief”, “Business Summary”, and nested Agentic Review Workspace plus Query/SQL evidence controls (`web/src/App.tsx:2460-2563`). This is the main UX issue.
- Backend already has many needed primitives:
  - `src/bwli/client.py`: read-only `fetch_*` methods for search, dataflow, xref, repository, process chain/variant, DTP, DataSource/source system, query, CompositeProvider, request monitor.
  - `src/bwli/endpoints.py`: query Accept fallback includes 406/415-compatible path via `negotiate_accept`, but no full discovery-media-type cache yet.
  - `src/bwli/live.py`: read-only live smoke/capture supports query, DataSource, process chain, request freshness candidates.
  - `src/bwli/query_analysis.py`: deterministic parser for Query evidence.
  - `src/bwli/impact_evidence.py` and `src/bwli/llm/agentic_*`: evidence-bound LLM review foundation exists.

### 2.3 Latest BW Modeling MCP facts checked

- `npm view bw-modeling-mcp version dist-tags.latest ...` → latest npm version **0.8.0**, modified `2026-06-09T21:16:18.463Z`.
- Latest Git clone: `/tmp/bw-modeling-mcp-latest`, commit `9a9e394e2ec3466da275632a042c98cf056ec9d6`, package version `0.8.0`, MIT.
- v0.8.0 adds:
  - `bw_run_dtp`
  - `bw_list_requests`
  - `bw_get_request`
  - `bw_activate_request`
  - `BW_COOKIE_FILE` cookie auth for BW Bridge / SAML/OAuth-fronted systems
  - RSDS lifecycle support
  - query media-type discovery fix
- Important API patterns from the latest source:
  - `/sap/bw/modeling/discovery` publishes media types; `loadMediaTypes()` picks the highest-versioned `+xml` `<app:accept>` per collection.
  - `bw_get_query` should lead with discovered `MEDIA_TYPES['query']`, then static query Accept fallback.
  - request monitor GETs use `/sap/bc/http/sap/bw4/v1/manage/...` and require `Content-Type: application/json`, `Accept: */*` even for GET.
  - dataflow remains `/sap/bw/modeling/dmod/8TRANSIENT` with `objecttype`, `objectname`, `levelupwards`, `leveldownwards`.
- External BW/MCP article signal: the MCP author’s demo frames the useful read-only job as walking the dependency chain via aDSO → where-used → transformations/source objects, then explaining data flow and business logic. This supports task-first lineage/review rather than tool-list UI.

---

## 3. SAP BW workflow assumptions for the product

### 3.1 Analyst mental model

A BW user thinks in a chain, not in API tools:

```text
Source System / DataSource (RSDS)
  -> Transformation (TRFN) + DTP (DTPA)
  -> aDSO / InfoSource / InfoObject
  -> CompositeProvider / Query / CKF / RKF / Structure
  -> Process Chain / Request freshness / consuming reports or roles
```

### 3.2 Product implications

- **Lineage** should answer “flow and evidence” first. The UI should not start with depth/node/edge caps.
- **Impact** should start from a **change scenario**, not a graph algorithm setting.
- **Request monitor evidence** is useful for freshness/release-risk context, but not as a standalone operations console.
- **Query/SQL analysis** should be evidence inside Impact/Ask BW, not separate top-level tabs for the current MVP.
- **LLM** should be a reviewer over known evidence. It must never become the source of truth for affected objects, severity, confidence, or BW API execution.

---

## 4. Proposed IA

### 4.1 Navigation

```text
Left rail:
  Object Search
  Repository browser
  Analysis targets
  Snapshot / capture basis
  Settings / connection diagnostics

Main workspaces:
  Lineage
  Impact
  Ask BW / Review
```

### 4.2 What moves where

| Current surface | New home | Rationale |
|---|---|---|
| Evidence Walkthrough | Lineage evidence panel + Ask BW preset | It is a way to inspect lineage evidence, not a separate primary action. |
| Business Summary | Ask BW / Review preset | LLM summary belongs in the assistant. |
| Impact Brief | Impact result summary + Ask BW preset | Deterministic grade stays in Impact; prose explanation goes to assistant. |
| Agentic Review Workspace | Ask BW / Review workspace | Too large inside Impact; should be a contextual review tool. |
| Query Analysis / SQL Analysis | Impact evidence + assistant context | Hidden tabs are correct for MVP; expose as evidence, not destinations. |
| Glossary | Keep hidden/deferred | Useful later, but currently distracts from core jobs. |

---

## 5. Lineage redesign

### 5.1 User journey

```text
Select object
  -> choose direction chip: Upstream / Downstream / Both
  -> Run Lineage
  -> see lane graph + selected-node details + evidence health
  -> optional advanced controls and Ask BW presets
```

### 5.2 Default visible controls

Only show:

- selected object
- direction chips
- one primary CTA: `Lineage 보기`
- high-level evidence state: `Dataflow`, `Where-used`, `Object detail`, `Freshness available?`

Move these into “Advanced”:

- depth
- node cap
- edge cap
- raw evidence IDs
- request freshness details
- dataflow/xref payload source list

### 5.3 Result layout

```text
Header: object + type + layer + freshness
Graph: Source -> Transform -> Model -> Semantic -> Runtime lanes
Right panel:
  - selected node details
  - evidence health
  - missing evidence / unknown reason
  - Ask BW presets for this lineage
```

### 5.4 Copy changes

- `Lineage 실행` → `흐름 보기`
- `Evidence Walkthrough` → `증거 보기` inside panel
- `Business Summary` → remove from Lineage; replace with Ask BW preset `이 흐름을 요약해줘`

### 5.5 Backend changes needed

No core algorithm change in the first slice. Later improve evidence health fields if missing:

- `src/bwli/lineage.py`
- `src/bwli/traversal.py`
- `src/bwli/store/catalog.py`
- `src/bwli/server.py` lineage response models

---

## 6. Impact redesign

### 6.1 User journey

```text
Choose change scenario
  -> select target object / field if needed
  -> Analyze impact
  -> review risk tier, affected objects, evidence, manual checklist
  -> Ask BW / Review for narrative or release checklist
```

### 6.2 Scenario-first model

Replace the generic “Change type” dropdown as the lead concept with scenario cards:

| Scenario card | Maps to existing change types | Primary evidence |
|---|---|---|
| ADSO / InfoObject field change | `field_removed`, `field_type_changed`, `infoobject_*` | xref, dataflow, fields, query evidence |
| Transformation logic change | `routine_changed` | TRFN mappings/routines, downstream DTP/ADSO/query |
| DTP / Process Chain change | `dtp_filter_changed` + process chain | DTP XML, process chain, request freshness |
| CompositeProvider / Query change | `compositeprovider_mapping_changed`, Query evidence | HCPR mappings, query parser, CKF/RKF later |
| Recent load / freshness risk | no mutation; request monitor evidence | `bw_list_requests`, `bw_get_request` |

Implementation can still map to the existing `ChangeType` enum initially. The user-facing model should be “scenario”.

### 6.3 Result hierarchy

Impact result should default to:

1. **Risk grade** — deterministic grade from existing impact summary.
2. **Affected objects** — grouped by object type and severity.
3. **Evidence review** — dataflow/xref/query/HCPR/DTP/process-chain/request evidence.
4. **Manual-check checklist** — always visible, deterministic fallback first.
5. **Ask BW / Review** — optional narrative, CAB summary, reviewer questions.

### 6.4 What to hide/de-emphasize

- Query evidence names and SQL evidence controls should live under `Evidence scope (Advanced)`, not above the main CTA.
- LLM review should not occupy half the Impact default screen before the user asks for it.
- `Impact Brief` and `Business Summary` buttons should be replaced by assistant presets.

### 6.5 Deterministic manual checklist defaults

Even with LLM disabled, show checklist items based on scenario and evidence gaps:

- Check impacted BW Queries / CompositeProviders in BWMT.
- Check Transformation routines / AMDP dependencies manually when routine edges are unknown.
- Check DTP filters and Process Chain scheduling for affected targets.
- Check latest request/load status for changed ADSO/DataSource where available.
- Confirm business owner / report owner for high-severity Query impacts.

---

## 7. Ask BW / Review assistant redesign

### 7.1 Product role

The assistant is not another analyzer. It is a **citation-bound reviewer over current deterministic evidence**.

It may:

- summarize lineage/impact
- explain BW concepts using current evidence
- prioritize manual checks
- draft CAB/release-review text
- identify missing evidence
- ask for additional deterministic captures from an allowed catalog

It must not:

- invent BW objects or business owners
- execute BW queries or preview rows
- call mutating BW APIs
- decide severity/affected objects over deterministic results
- expose credentials/snapshots/secrets

### 7.2 UI shape

Add one workspace/surface:

```text
Ask BW / Review
  Context selector:
    current object
    current lineage result
    current impact result
    selected evidence pack
  Prompt box
  Preset chips:
    “이 lineage를 설명해줘”
    “릴리즈 위험을 검토해줘”
    “근거가 부족한 지점을 알려줘”
    “CAB 요약 작성”
  Answer panel:
    answer
    citations
    unknowns / evidence gaps
    confidence label
    manual checklist
```

### 7.3 Backend design

Prefer a thin service that reuses existing pieces:

- Create: `src/bwli/llm/assistant_context.py`
  - builds a bounded `AssistantEvidenceContext` from selected object, lineage response, impact review, query/SQL/request evidence.
- Create or extend: `src/bwli/llm/assistant.py`
  - uses existing `OpenAICompatibleClient`, sanitizer, validators, audit logs.
- Add endpoint in `src/bwli/server.py`:
  - `POST /api/v1/assistant/review`
  - returns deterministic fallback when LLM disabled.
- Extend `web/src/api.ts` with types and client.
- Add UI component, ideally extracted from the monolithic `App.tsx`:
  - `web/src/features/assistant/AskBwReviewPanel.tsx`

---

## 8. BW Modeling MCP v0.8.0 integration matrix

| MCP v0.8.0 capability / API | Decision | Product use | Notes |
|---|---|---|---|
| `bw_search` / `/repo/is/bwsearch` | Adopt | Object Finder | Already supported; keep as entry point. |
| `bw_xref` / `/repo/is/xref` | Adopt | Lineage + Impact direct dependencies | Core deterministic impact evidence. |
| `bw_get_dataflow` / `/dmod/8TRANSIENT` | Adopt | Lineage graph | Keep as main structural graph evidence. |
| `bw_get_adso` | Adopt | object details + fields | Already supported; improve field evidence if needed. |
| `bw_get_infoobject` | Adopt next | field/infoobject impact | Add when scenario cards need it. |
| `bw_get_transformation` | Adopt next | transformation scenario | Needed for routine/mapping impact; parse-only. |
| `bw_get_dtps`, `bw_get_dtp` | Adopt | DTP impact | Already partly supported; expose as evidence, not a tab. |
| `bw_get_process_chain`, `bw_get_process_variant` | Adopt | operational impact / scheduling context | Already supported; keep read-only. |
| `bw_get_query` | Adopt | Query impact evidence | Already supported with parser; add discovery media cache. |
| `bw_get_composite_provider` | Adopt | semantic/provider lineage | Already supported. |
| `bw_list_requests`, `bw_get_request` | Adopt | freshness/load risk evidence | v0.8.0 confirms request monitor API. Keep top bounded. |
| `BW_COOKIE_FILE` | Adopt carefully | BW Bridge / SAML/OAuth | Already present in config/client; UI/docs should clarify runtime-only local file, no persistence of cookie contents. |
| Discovery media-type parser | Adopt next | query and object GET compatibility | Current `negotiate_accept()` supports discovered value, but client lacks full discovery cache. |
| `bw_list_source_systems`, `bw_list_datasources`, `bw_get_source_system`, `bw_get_datasource` | Adopt | upstream/source lineage | Keep as evidence behind object capture. |
| `bw_get_ckf`, `bw_get_rkf`, `bw_get_structure` | Defer | deeper Query semantics | Useful, but secondary after IA cleanup. |
| Role read tools | Defer | report/owner/authorization context | Useful later; can be evidence in Impact. |
| Repository `bw_list_contents` | Keep but de-emphasize | object discovery | Useful left rail, not main workflow. |
| `bw_query_data` | Reject MVP / approval-gated future | business data query | Data-bearing; violates metadata-only default. |
| `bw_get_filter_values` | Reject MVP / approval-gated future | value help | Can reveal business values. |
| `bw_preview_datasource` | Reject MVP / approval-gated future | source data preview | Data-bearing. |
| `bw_run_dtp` | Reject | runtime mutation | Starts load. |
| `bw_activate_request` | Reject | runtime mutation | Activates loaded data. |
| create/update/delete/activate/unlock/move/set/push APIs | Reject | BW mutation | Not in MVP runtime surface. |

---

## 9. Implementation slices

### Slice 1 — Product IA simplification (frontend only)

**Objective:** Make the app visibly about Lineage, Impact, Ask BW/Review; remove scattered LLM buttons from default flow.

**Files:**

- Modify: `web/src/App.tsx`
- Modify: `web/src/styles.css`
- Modify: `web/tests/sliceG.test.mjs`
- Optional create: `web/src/features/assistant/AskBwReviewPanel.tsx` if extraction is safe.

**Steps:**

1. Add product-level labels/copy constants for the three jobs.
2. Add a third visible tab or persistent right-side assistant launcher: `Ask BW / Review`.
3. Replace default Lineage/Impact LLM buttons with preset chips/links into the assistant surface.
4. Keep existing backend calls available but hide/de-emphasize them in the default screen.
5. Update sliceG tests to assert scattered Business Summary / Agentic Review controls are not primary default CTAs.

**Verification:**

```bash
npm --prefix web run test:slice-g
npm --prefix web run build
git diff --check
```

**Acceptance:**

- First screen shows the three jobs clearly.
- Lineage has one primary action.
- Impact has one primary action.
- LLM/review is visible as one assistant surface, not three unrelated buttons.

---

### Slice 2 — Lineage task-first workspace

**Objective:** Make Lineage a “flow understanding” workflow with minimal default controls and progressive advanced settings.

**Files:**

- Modify: `web/src/App.tsx`
- Modify/create: `web/src/features/lineage/LineageWorkspace.tsx`
- Modify: `web/src/sliceG.ts`
- Modify: `web/src/styles.css`
- Test: `web/tests/sliceG.test.mjs`

**Steps:**

1. Extract or isolate `LineageTab` rendering if feasible.
2. Show only selected object, direction chips, and `흐름 보기` by default.
3. Move depth/node cap/edge cap into `<details>` advanced settings.
4. Rename Evidence Walkthrough as evidence panel content.
5. Add evidence health summary: dataflow/xref/object detail/freshness availability.
6. Preserve graph lane behavior and selected node details.

**Verification:**

```bash
npm --prefix web run test:slice-g
npm --prefix web run build
git diff --check
```

---

### Slice 3 — Impact scenario-first workspace

**Objective:** Replace function-first Impact controls with scenario cards and deterministic risk/evidence/manual-check result hierarchy.

**Files:**

- Modify: `web/src/App.tsx`
- Modify/create: `web/src/features/impact/ImpactWorkspace.tsx`
- Modify/create: `web/src/features/impact/impactScenarios.ts`
- Modify: `web/src/sliceG.ts`
- Modify: `web/src/api.ts` only if scenario request type is formalized.
- Test: `web/tests/sliceG.test.mjs`

**Steps:**

1. Add user-facing scenario cards that map to existing `ChangeType` values.
2. Keep field auto-select, but only show it for field scenarios.
3. Move Query/SQL evidence inputs under `Evidence scope (Advanced)`.
4. Render deterministic result hierarchy: grade → affected objects → evidence review → manual checklist.
5. Convert Impact Brief / Business Summary / Agentic Review into assistant presets or links.

**Verification:**

```bash
npm --prefix web run test:slice-g
npm --prefix web run build
git diff --check
```

---

### Slice 4 — Assistant context and unified Ask BW / Review

**Objective:** Consolidate LLM help into a single citation-bound assistant over selected deterministic context.

**Files:**

- Create: `src/bwli/llm/assistant_context.py`
- Create/modify: `src/bwli/llm/assistant.py`
- Modify: `src/bwli/server.py`
- Modify: `src/bwli/llm/openai_compatible.py` only if a reusable request wrapper is needed.
- Modify: `web/src/api.ts`
- Create/modify: `web/src/features/assistant/AskBwReviewPanel.tsx`
- Test: `tests/test_llm.py`, `tests/test_server.py`, `web/tests/sliceG.test.mjs`

**Steps:**

1. Define strict models with `extra="forbid"`:
   - `AssistantContextKind`
   - `AssistantEvidenceContext`
   - `AssistantReviewRequest`
   - `AssistantReviewResponse`
2. Build context from current object/lineage/impact review without live calls.
3. Reuse sanitizer + citation validators.
4. Return deterministic fallback if LLM disabled.
5. Frontend sends current context selector + prompt.
6. Render answer, citations, unknowns, confidence, manual checks.

**Verification:**

```bash
uv run pytest tests/test_llm.py tests/test_server.py -q
uv run pytest -q
uv run ruff check .
uv run mypy src
npm --prefix web run test:slice-g
npm --prefix web run build
git diff --check
```

**Safety acceptance:**

- LLM disabled by default.
- Assistant cannot trigger BW live calls directly.
- Assistant answer lines must cite allowed evidence or explicitly say unknown.

---

### Slice 5 — MCP v0.8.0 read-only compatibility alignment

**Objective:** Adopt latest v0.8.0 read-only compatibility lessons without broadening the product into a full MCP tool clone.

**Files:**

- Modify: `src/bwli/endpoints.py`
- Modify: `src/bwli/client.py`
- Modify: `src/bwli/config.py` and `.env.example` if docs/copy need cookie-mode clarification.
- Modify: `src/bwli/live.py`
- Modify/add tests: `tests/test_client.py`, `tests/test_config.py`, `tests/test_live.py`, `tests/test_v1_api.py`
- Docs: `README.md` only if user approves docs update in implementation.

**Steps:**

1. Add discovery document fetch/parser equivalent to v0.8.0 `loadMediaTypes()` but limited to read-only Accept negotiation.
2. Cache discovered Accept media types in memory only; do not persist system details.
3. Wire `fetch_query()` to lead with discovered query media type when available.
4. Keep 404/406/415 fallback for active→inactive and media mismatch.
5. Ensure request monitor GETs retain `Content-Type: application/json`, `Accept: */*`, top cap, and redacted errors.
6. Add guard tests preventing public client methods or API routes for run/activate/push/update/delete/preview/query-data/filter-values.

**Verification:**

```bash
uv run pytest tests/test_client.py tests/test_config.py tests/test_live.py tests/test_v1_api.py -q
uv run pytest -q
uv run ruff check .
uv run mypy src
git diff --check
```

---

### Slice 6 — Visual/browser usability smoke

**Objective:** Verify that simplification actually improves usability, not just tests.

**Files:**

- No required source changes unless visual smoke finds issues.
- Optional docs/artifacts: `.hermes/artifacts/ui-smoke/` (gitignored) for screenshots.

**Steps:**

1. Run local dev server.
2. Inspect 1440, 1280, 900, and 700 px widths.
3. Check no default screen is dominated by advanced controls.
4. Check no long BW technical names overlap.
5. Check assistant surface is discoverable but not intrusive.
6. Check console has no errors.

**Verification:**

```bash
./scripts/dev-local.sh
# browser smoke at http://127.0.0.1:5173
npm --prefix web run build
git diff --check
```

---

## 10. Validation matrix

| Area | Command / check | Expected |
|---|---|---|
| Python tests | `uv run pytest -q` | all pass |
| Python lint | `uv run ruff check .` | pass |
| Python typing | `uv run mypy src` | pass |
| Frontend unit/slice | `npm --prefix web run test:slice-g` | pass |
| Frontend build | `npm --prefix web run build` | pass |
| Diff hygiene | `git diff --check` | clean |
| Read-only surface | tests assert no mutating route/client method exposed | pass |
| LLM safety | citation/unknown/fallback tests | pass |
| Visual UX | browser smoke for desktop/tablet/narrow | no overlap, no console error |

---

## 11. Non-goals and safety gates

### Non-goals for this re-plan

- No BW create/update/delete/activate/transport/unlock/move/set/run/push.
- No `bw_run_dtp`.
- No `bw_activate_request`.
- No `bw_query_data`, `bw_get_filter_values`, or `bw_preview_datasource` in MVP UI.
- No central hosted analyzer backend.
- No cloud LLM default.
- No automatic SQL/BW query execution.
- No credential/cookie/snapshot persistence outside approved local gitignored locations.

### Approval-gated future only

- Business data previews.
- Query execution / reporting endpoint usage.
- Value-help calls if they reveal business values.
- Role/authorization analysis if it exposes sensitive ownership/security context.
- Any runtime or activation operation.

---

## 12. First implementation prompt for Codex

```text
Implement Slice 1 only in /Users/jclee/Projects/bw-lineage-impact.

Goal:
Simplify the current web UI IA so the visible product is centered on:
1. Lineage
2. Impact
3. Ask BW / Review

Constraints:
- Do not run live BW.
- Do not add dependencies.
- Do not change backend behavior in this slice.
- Preserve local-first/read-only SAP BW analyzer rules.
- Do not expose or persist credentials, cookies, snapshots, or audit logs.
- Keep changes small and reversible.

Grounding:
- web/src/App.tsx currently has visible Lineage + Impact tabs (`IMPACT_UNIFIED=true`) but Lineage exposes Lineage 실행 / Evidence Walkthrough / Business Summary and Impact exposes Impact 실행 / Impact Brief / Business Summary / Agentic Review Workspace / Query-SQL evidence controls.
- This is function-first complexity. Reorganize the UI copy/layout so default screens are task-first.

Required changes:
- Add or expose an Ask BW / Review surface/launcher using existing capabilities or a safe placeholder; do not implement new backend assistant logic yet.
- De-emphasize scattered LLM/review/summary buttons in Lineage and Impact; represent them as assistant presets/links.
- Keep one primary CTA in Lineage and one primary CTA in Impact.
- Keep advanced evidence controls behind progressive disclosure.
- Preserve existing data flow and tests.

Files to inspect first:
- web/src/App.tsx
- web/src/styles.css
- web/src/sliceG.ts
- web/tests/sliceG.test.mjs
- web/package.json

Validation:
- npm --prefix web run test:slice-g
- npm --prefix web run build
- git diff --check

Deliverable:
- Changed files
- UX simplifications made
- Validation output
- Remaining risks / next slice
```

---

## 13. Recommended execution order

1. **Slice 1 first** — low-risk UI IA simplification; no backend/live/LLM behavior change.
2. **Slice 2 + Slice 3** — make the two core workspaces task-first.
3. **Slice 4** — unify LLM assistant once UX placement is stable.
4. **Slice 5** — tighten v0.8.0 read-only compatibility and discovery media handling.
5. **Slice 6** — visual/browser smoke and polish.

This avoids touching BW API compatibility and LLM orchestration before the product surface is simplified.
