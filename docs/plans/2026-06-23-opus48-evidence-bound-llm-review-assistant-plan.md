# BW Lineage Impact — Evidence-bound LLM Review Assistant Implementation Plan

> Source: Claude Code `--model opus`; expected model usage should include `claude-opus-4-8`.
> Status: planning-only; no implementation changes.
> Date: 2026-06-23 KST.

## Context

The product is a local-first, read-only SAP BW/4HANA lineage / change-impact analyzer. `main` is clean after `S1-S8 UI API rework`. Today the LLM layer is a set of one-shot "advisor/tour/explainer" calls bolted onto deterministic payloads (`lineage_advisor`, `impact_advisor`, `tour`, `explainer`). The recent decision is that a raw BW Modeling MCP + LangChain/LangGraph "tool-call until the answer feels right" loop is **not** the product core. Instead we want a disciplined **role-separated** pipeline:

```
parse → plan → deterministic analyze → draft → critique → revise → validate
```

where the deterministic analyzer remains the authority on severity/confidence/affected objects, and the LLM is a bounded advisory/reporting layer whose every claim must cite an evidence id. Query XML analysis and Native SQL reference extraction should be folded into **Impact Review** as deterministic evidence providers, not kept as separate product destinations. Glossary is noise for the current focus and must be hidden now (UI off, backend kept for compatibility), deferred to a later phase.

This plan delivers that change as small sequential PRs (P0–P7) and is the document to be persisted at `docs/plans/2026-06-23-opus48-evidence-bound-llm-review-assistant-plan.md`.

---

## 1. Executive decision

- Adopt an **Evidence-bound Review Assistant** built as a deterministic state machine, not an open agent loop.
- Make **Impact Review** the primary product surface: Query XML analysis (`query_analysis.py`) and Native SQL reference extraction (`field_lineage.parse_native_sql_view`) become supporting evidence cards inside impact review, not standalone top-level workflows.
- Introduce a deterministic **`ImpactEvidencePack`** composition layer that combines core impact findings, query exposure, SQL/native-view references, freshness/capture scope, unknown gaps, and manual-verification requirements before any LLM report is generated.
- Reuse the existing safety spine verbatim: `sanitize_llm_evidence` / `sanitize_text` (`src/bwli/llm/sanitizer.py:166,180`), `OpenAICompatibleClient` with the local-only network guard (`src/bwli/llm/openai_compatible.py:63,155`), and the citation/safety validators (`src/bwli/llm/explainer.py:113,118`, `src/bwli/llm/tour.py:101`).
- LLM stays **disabled by default** (`LlmConfig.enabled = False`, `src/bwli/config.py:95`; `resolve_runtime()` returns `None`, `src/bwli/config.py:101`). The assistant must degrade to a deterministic-only review pack when LLM is off, mirroring the existing empty-payload gate at `src/bwli/server.py:2399`.
- LangGraph/MCP are **optional, last** (P7) and only as a read-only allowlist wrapper over the existing `BwReadClient` Protocol (`src/bwli/client.py:16`), never as a raw mutating tool surface.
- Stop conditions are mechanical, never "answer quality feel": evidence-requirement satisfied, policy pass, round budget, citation validation pass.
- Hide Glossary now (P0); keep backend endpoints; defer the feature.

---

## 2. Current code facts

### LLM layer (`src/bwli/llm/`)
- `openai_compatible.py`: `OpenAICompatibleClient.chat(LlmChatRequest) -> LlmCompletion` (`:77`). Defenses: `_validate_local_llm_base_url` per request (`:78,134`), `_GuardedNetworkBackend.connect_tcp` blocks non-local/link-local/metadata addresses (`:155-174`), `trust_env=False` (`:88`). Audit model `LlmAuditMetadata` carries `prompt_sha256`, `sanitized_input_sha256`, `citation_validation: "not_validated"|"passed"` (`:39-53`). `write_llm_audit_log` persists secret-free audit json (`:115`).
- `sanitizer.py`: `sanitize_llm_evidence(value) -> SanitizedPayload{data, redacted_paths, omitted_paths}` (`:166`) and `sanitize_text(value) -> str` (`:180`). Key omit (`_should_omit_key`, raw-snapshot/BW-credential tokens, `:260`) + key redact (`_should_redact_key`, `:271`) + regex scrub of secrets/URLs/hosts/emails/SQL string literals.
- `explainer.py`: validators reused by every advisor — `_validate_completion_safety` (sensitive-text regex, `:113`), `_validate_completion_citations` (every non-empty line must cite an allowed id, `:118`), `_line_has_citation` (`:130`); errors `LlmCitationError` (`:36`), `LlmEvidenceError` (`:40`). `_CITATION_TOKEN_RE = \[([^\[\]]+)\]` (`:19`).
- `tour.py`: `build_guided_tour_request_context(...)` strict-JSON prompt (`:23`) and `validate_tour_completion_content(...)` fail-closed (node/edge id allowlist + `TourStep.model_validate` + citation check, `:101`).
- `lineage_advisor.py` / `impact_advisor.py`: `build_*_request`, `create_*_advice`, `build_*_tour_request`, `create_*_tour`. Bounded evidence: caps `_MAX_GRAPH_NODES=80`, `_MAX_GRAPH_EDGES=120`, `_MAX_LLM_EVIDENCE_JSON_CHARS=16_000`, `_MAX_AFFECTED_OBJECTS=60`. Citation ids minted as `node:N`, `edge:N`, `affected:N`, `scenario:change`.
- **No** intent/scenario/plan/policy models, **no** critic/revision loop, **no** capture-plan gate exist yet. `sql_assistant.py` is a thin SQL draft helper.

### Deterministic core
- `impact.py`: `ImpactFinding{impacted_object_id, severity: ImpactSeverity, confidence: str, reason, evidence_ids, manual_verification}` (`:52`), `ImpactSeverity` enum (`:25`), `ChangeType` (`:15`), `ChangeEvent`/`ChangeSet`/`ImpactReport`, `ChangeGrade`/`ChangeGradeResult`. Severity from `_severity_for` (`:380`), manual flag from `_requires_manual_verification` (`:402`). This is the authority layer.
- `query_analysis.py`: `parse_query_xml(xml, source) -> QueryAnalysisResult` (`:59`), models `QueryVariable`/`QueryKeyFigure`/`QueryProvider`/`QueryAnalysisResult` (`:12-43`).
- `field_lineage.py`: `parse_native_sql_view(sql_text, *, view_id) -> SqlParseResult` (`:183`) extracts Native SQL View referenced objects/fields and evidence fragments with parser confidence; `render_sql_view_evidence` (`:241`) documents the boundary that optimization notes are advisory only and no SQL rewrite or DB change is applied.
- Frontend currently exposes `Query Analysis` and `SQL Analysis` as top-level tabs (`web/src/App.tsx:1979,1980`). Product direction should migrate those into Impact evidence sections/cards while keeping the underlying parsers/routes available for compatibility and advanced drill-down.
- Read-only capability surface (the natural allowlist for capture plans): `BwReadClient` Protocol exposes only `fetch_search`, `fetch_dataflow`, `fetch_xref`, `fetch_repository_contents`, `fetch_process_chain`, `fetch_process_variant`, `fetch_dtp`, `fetch_datasource`, `fetch_source_system`, `fetch_query`, `fetch_composite_provider`, `fetch_list_requests`, `fetch_request` (`src/bwli/client.py:16-64`). Endpoint builders in `endpoints.py:71-285`. `live.py` names operations `bw_search`, `bw_get_dataflow`, `bw_get_query`, … and is hard-typed `read_only = True` (`LiveSmokeResult`/`LiveCollectionResponse`, `src/bwli/live.py:82,102`). There is **no** write/activate/transport/run_dtp/query-data method anywhere in the client — the allowlist is naturally bounded.

### Server (FastAPI, `src/bwli/server.py`, 3249 lines)
- LLM-backed routes: `/api/v1/snapshots/{id}/lineage/advice` (`:959`), `/lineage/tour` (`:972`), `/impact/advice` (`:1019`), `/impact/tour` (`:1033`), `/sql/explain` (`:1048`), `/sql/draft` (`:1062`). All gate on `runtime_config.llm.enabled and configured` and return empty payloads when off (`_lineage_advice_payload`, `:2393-2434`). Existing `/query`/`/sql` style routes should remain during migration, but the new integrated endpoint should be `POST /api/v1/snapshots/{id}/impact/review` returning a unified `ImpactEvidencePack`.
- Runtime config plumbing: `RuntimeConfigState.llm_enabled_by_default = False` (`:95`), env state `_env_llm_state` (`:1290`), `_build_llm_state` validates local base url (`:1555,1580`), public state always redacts api key.

### Glossary (to hide in P0)
- Backend (keep): `/api/v1/glossary/aggregate` (`server.py:819`), `/api/v1/glossary/{term_id}/lifecycle` (`:829`), `/api/v1/snapshots/{id}/glossary` (`:842`). GET handlers auto-backfill via `_sync_glossary_for_snapshot` (`:2826`) / `_sync_glossary_for_all_snapshots` (`:2834`). Object detail embeds `glossary_terms` (`:2577,2792`, `_glossary_terms_for_object :3045`).
- Frontend: `AppTab` union includes `'glossary'` (`web/src/api.ts:5`); api fns `getGlossary`/`getGlossaryAggregate`/`postGlossaryLifecycle` (`api.ts:496,503,510`). In `App.tsx`: **auto-fetch** inside `loadContext` `Promise.all([... getGlossary(snapshotId), getGlossaryAggregate()])` (`:697-707`); refresh rerun (`:1046-1049`); state (`:222-224`); `searchGlossary` (`:1061`), `confirmGlossaryTerm` (`:1345`); entry points `<TermsOverview …/>` (`:1903`), `<TabButton id="glossary" label="Glossary"/>` (`:1980`), `GlossaryTab` render (`:2092`), object-detail `GlossaryList` (`:2269,2566`).
- **Test coupling risk:** `web/tests/sliceG.test.mjs` asserts glossary behavior against `App.tsx` source: the `GlossaryTab … onSelectObject … onAddTarget=` handler block (`:125-133`) and the refresh ordering `getGlossary(refreshedSnapshotId, glossaryQuery.trim() || undefined)` clear-before-reload (`:590-601`). Hiding glossary naively breaks these — P0 must update them in lockstep.

### Verification baseline (must stay green)
- `uv run pytest -q` → 390 passed, 1 warning
- `uv run ruff check .` → pass
- `uv run mypy src` → pass
- `npm --prefix web run build` → pass
- (web unit) `npm --prefix web run test:slice-g` → pass (`web/package.json:9`)

---

## 3. Target architecture

A single deterministic orchestrator, `ReviewAssistant`, drives a fixed pipeline. Each LLM step is a bounded call through the existing `OpenAICompatibleClient`; everything between steps is plain Python with explicit stop conditions.

```
NL question ─▶ [LLM] parse_intent ──▶ AnalysisIntent / ChangeScenario   (schema-validated, fail-closed)
                                   │
                                   ▼
                        [LLM] propose_capture_plan ──▶ CapturePlan(EvidenceRequirement[])
                                   │
                                   ▼
                  [DETERMINISTIC] PolicyGate.evaluate(CapturePlan) ──▶ PolicyResult(allowed | rejected[reasons])
                                   │ (allowed only)
                                   ▼
        [DETERMINISTIC] execute read-only capture/analyze and compose ImpactEvidencePack
                                   │  → deterministic ReviewPayload (authority: impact severity/confidence/affected/unknown;
                                   │     query/sql only enrich coverage and manual-verification gaps)
                                   ▼
        [LLM] draft_report ──▶ [LLM] critic ──▶ [LLM] revise   (≤ MAX_ROUNDS; each fed only sanitized ReviewPayload)
                                   │
                                   ▼
        [DETERMINISTIC] FinalValidator: citation exactness + safety + unsupported-claim + unknown/gap preservation
                                   │
                                   ▼
                              ReviewPack (report + citations + audit trail + deterministic findings verbatim)
```

The deterministic pre-LLM result is an **`ImpactEvidencePack`**, not separate query/sql reports. It should include:

- core impact findings from `impact.py` (`affected_objects`, severity, confidence, evidence ids, `manual_verification`),
- Query XML exposure evidence from `query_analysis.py` (providers, variables, calculated/restricted key figures, exposed fields),
- Native SQL reference evidence from `field_lineage.py` (referenced objects/columns, parser confidence, evidence fragments),
- freshness/capture-scope metadata, truncation/unknown reasons, and explicit manual-verification gaps.

New modules (all under `src/bwli/llm/`, mirroring existing style — pydantic `ConfigDict(extra="forbid")`, `from __future__ import annotations`):

- `intent.py` — `AnalysisIntent`, `ChangeScenario`, `EvidenceRequirement`, `CapturePlan`, `PolicyResult`, `ReviewPayload`, `ReviewPack` models; prompt builders + JSON validators (no live calls).
- `policy.py` — `PolicyGate` deterministic evaluator: allowlist, budgets, wildcard ban, mutating-name ban, data-preview ban.
- `planner.py` — `parse_analysis_intent(...)`, `propose_capture_plan(...)` (LLM prompt + fail-closed validators); executor that maps an *approved* plan onto existing read-only operations.
- `review.py` — `ReviewAssistant.run(...)` draft→critic→revise loop bounded by `MAX_ROUNDS`.
- `validator.py` — `validate_review_pack(...)` final programmatic checks (reusing `_validate_completion_citations` / `_validate_completion_safety` and extending with unsupported-claim + unknown-preservation checks).

Server: add a deterministic `POST /api/v1/snapshots/{snapshot_id}/impact/review` route that returns `ImpactEvidencePack`, then one gated LLM route group `POST /api/v1/snapshots/{snapshot_id}/review/assist` (plus an optional `/review/intent` for the parse-only preview) that consumes that pack and is wired exactly like the existing advice routes (gate → sanitize → validate → audit).

Web: make Impact the primary review surface. Query XML and SQL reference details should render as evidence cards/drawers inside Impact; standalone `Query Analysis` and `SQL Analysis` top-level tabs become transitional advanced surfaces and can later be hidden behind an `IMPACT_UNIFIED` flag. A Review Assistant panel consumes the unified impact review route, with an explicit "LLM disabled — deterministic findings only" fallback and a visible audit trail (citations, redacted/omitted paths, rounds, policy decisions).

---

## 4. Non-goals and hard safety boundaries

- **No BW writes.** No create/update/delete/activate/transport/`run_dtp`/query-data/data-preview. The capture allowlist is restricted to the `BwReadClient` `fetch_*` methods (`client.py:16`); any plan naming anything else is rejected by `PolicyGate`.
- **No query/SQL execution.** Query XML and SQL/Native SQL View handling remain parse-only evidence extraction. They may expand review coverage and manual-check notes, but must not execute BW queries, preview rows, execute DB SQL, or infer final severity.
- **No raw agent loop / no raw MCP tool surface.** LangGraph/MCP (P7) only via `bwli_*` high-level safe tools or a read-only allowlist wrapper.
- **LLM is not source of truth.** Deterministic `ImpactFinding`/lineage/query results are copied into the `ReviewPack` verbatim; the LLM may summarize but may not alter severity/confidence/affected sets.
- **Every non-empty report line cites an evidence id.** Enforced by reusing `_validate_completion_citations` (`explainer.py:118`) and the final `validator.py`.
- **Unknown / gap / manual_verification must be preserved**, never paraphrased away — a dedicated final-validator check fails closed if a deterministic `manual_verification=true` finding or an `unknown` severity is dropped from the report.
- **LLM disabled by default**, local/company OpenAI-compatible endpoint only; public cloud default forbidden (`_validate_local_llm_base_url` / `_GuardedNetworkBackend`).
- **Secrets runtime-only**, never persisted; audit logs go through `sanitize_llm_evidence` + `assert_no_persisted_secrets` (already used at `server.py:2433`).
- **No new network egress paths**; all LLM I/O continues through the single guarded transport.

---

## 5. PR slices

Small, sequential, each independently green against the §2 baseline.

- **P0 — Hide Glossary (UI off, backend kept).** Remove the Glossary tab button, `TermsOverview` entry point, and the auto `getGlossary`/`getGlossaryAggregate` fetch from `loadContext`; gate behind a `GLOSSARY_VISIBLE = false` constant so re-enable is a one-line change. Keep backend routes and `GlossaryTab`/`GlossaryList` components in source. Update the two glossary-coupled `sliceG.test.mjs` assertions.
- **P1 — Intent/schema foundation (no live calls).** `src/bwli/llm/intent.py`: `AnalysisIntent`, `ChangeScenario`, `EvidenceRequirement`, `CapturePlan`, `PolicyResult`, `ReviewPayload`, `ReviewPack`; prompt builders + JSON parse/validate. Pure models + parsers, unit-tested with fixtures.
- **P1a — Unify Query/SQL as Impact evidence providers.** New deterministic `src/bwli/impact_evidence.py` composes `ImpactEvidencePack` from `impact.py`, `query_analysis.py`, `field_lineage.py`, freshness, capture scope, and unknown/manual-verification gaps. Add `POST /api/v1/snapshots/{id}/impact/review`. Keep old Query/SQL routes for compatibility.
- **P2 — Deterministic policy gate.** `src/bwli/llm/policy.py`: `PolicyGate` enforcing allowlist (`BwReadClient.fetch_*` names), per-plan operation/budget caps, wildcard rejection, mutating-name rejection, data-preview rejection. No LLM.
- **P3 — Evidence planner + validator.** `src/bwli/llm/planner.py`: `parse_analysis_intent`, `propose_capture_plan` (LLM behind `resolve_runtime()`), fail-closed validators; executor that only runs a `PolicyResult.allowed` plan against existing read-only ops/snapshot analysis.
- **P4 — Report quality loop.** `src/bwli/llm/review.py`: `ReviewAssistant.run` → draft → critic → revise, `MAX_ROUNDS` = 2 (configurable, hard cap 3), every call fed only sanitized deterministic `ReviewPayload`.
- **P5 — Programmatic final validator.** `src/bwli/llm/validator.py`: citation exactness, unsupported-claim detection, safety regex, unknown/gap/`manual_verification` preservation; fail-closed.
- **P6 — UI wiring.** "Review Assistant" panel inside Impact Review: NL input → review pack; LLM-disabled deterministic fallback; audit trail (citations, redacted/omitted paths, rounds, policy decisions). New server route `POST /api/v1/snapshots/{id}/review/assist` consumes `ImpactEvidencePack`.
- **P6a — Impact-centered navigation.** Add `IMPACT_UNIFIED=false`/default-off migration flag. When enabled, hide standalone `Query Analysis` and `SQL Analysis` top-level tabs and surface Query XML / Native SQL evidence as Impact cards or drawers. This mirrors the P0 `GLOSSARY_VISIBLE` hide-not-delete pattern.
- **P7 — Optional safe MCP/LangGraph adapter.** Read-only allowlist wrapper exposing only `bwli_*` high-level tools over `BwReadClient`; stop conditions = evidence requirement + policy pass + budget + citation validation. Behind a default-off flag.

Dependency order: P0 independent; P1→P1a→P2→P3→P4→P5 strictly sequential for the integrated assistant; P6 after P5; P6a after the deterministic `impact/review` and Review Assistant are usable; P7 last/optional.

---

## 6. Detailed tasks

### P0 — Hide Glossary now
- `web/src/App.tsx`: add `const GLOSSARY_VISIBLE = false;` near the top of the component module. Guard the tab button render at `:1980` and the `TermsOverview` render at `:1903` with `{GLOSSARY_VISIBLE && …}`. In `loadContext` (`:697-707`) drop `getGlossary`/`getGlossaryAggregate` from the `Promise.all` when `!GLOSSARY_VISIBLE` (keep `getScope`/context fetch); in the refresh rerun (`:1046-1049`) skip the glossary reload when hidden. Force `activeTab` away from `'glossary'` if it was ever set. Leave `GlossaryTab`/`GlossaryList`/`searchGlossary`/`confirmGlossaryTerm` defined (dead-but-compiling) so re-enable is trivial; object-detail `GlossaryList` (`:2269,2566`) may stay rendered or be guarded — recommend guarding to fully silence glossary fetches.
- `web/src/api.ts`: leave `AppTab` union and glossary fns intact (backend compat). No change required beyond what `App.tsx` references.
- `src/bwli/server.py`: **no deletion**. Optionally add a comment noting glossary endpoints are deferred. (Auto-sync only runs on GET, and the UI no longer calls those GETs, so no behavior change server-side.)
- `web/tests/sliceG.test.mjs`: update the `'Glossary object selection clears stale tour …'` test (`:125`) and the refresh-ordering glossary assertions (`:590-601`) to reflect hidden glossary — either assert the auto-fetch is gated by `GLOSSARY_VISIBLE` or remove the glossary-specific assertions while keeping the tour/freshness assertions. Add one test asserting the Glossary tab button is not rendered when `GLOSSARY_VISIBLE` is false.

### P1 — `src/bwli/llm/intent.py`
- Models (all `ConfigDict(extra="forbid")`): `AnalysisIntent{question, intent_type: Literal["lineage","impact","query","sql","mixed"], focus_object_ids: list[str], notes}`; `ChangeScenario{object_id, object_type, change_type, field?, value_description?, description}` (align with `impact.ChangeType`/`ChangeEvent`, `impact.py:15,32`); `EvidenceRequirement{operation: str, target: str, reason, citation_hint}`; `CapturePlan{requirements: list[EvidenceRequirement], max_operations: int}`; `PolicyResult{allowed: bool, rejected_reasons: list[str], allowed_requirements: list[EvidenceRequirement]}`; `ReviewPayload` (deterministic findings + lineage/impact/query evidence, citation ids); `ReviewPack{report, citations, deterministic_findings, audit, rounds}`.
- Prompt builders `build_intent_request(question)` and a strict-JSON parser `parse_intent_content(content) -> AnalysisIntent` (fail-closed like `validate_tour_completion_content`, `tour.py:101`). No network.

### P1a — `src/bwli/impact_evidence.py` and unified impact review
- Create deterministic models: `ImpactEvidencePack`, `QueryExposureEvidence`, `SqlReferenceEvidence`, `FreshnessEvidence`, `ManualVerificationGap`. Keep these independent from LLM modules so the pack is usable with LLM disabled.
- Compose the pack from existing sources: `_run_v1_impact_scenario(...)` / `impact.py` findings, `parse_query_xml(...)` query evidence where a query object/XML is present, `parse_native_sql_view(...)` SQL evidence for provided SQL text/file/native-view metadata, capture scope, request freshness, truncation, and unknown reasons.
- Add route `POST /api/v1/snapshots/{snapshot_id}/impact/review` with request fields such as `object_id`, `change_type`, `field`, `description`, `include_query_evidence`, `include_sql_evidence`, `sql_text`, `sql_file`, and `view_id`. Return deterministic `ImpactEvidencePack`; do not require LLM.
- Keep existing Query/SQL endpoints and frontend functions during migration. They become compatibility/advanced drill-down paths, not the product's primary review flow.
- Hard boundary: Query evidence and SQL evidence may raise coverage confidence or manual-check visibility, but final severity/confidence/affected-object authority remains `impact.py`.

### P2 — `src/bwli/llm/policy.py`
- `ALLOWED_OPERATIONS = frozenset({"fetch_search","fetch_dataflow","fetch_xref","fetch_repository_contents","fetch_process_chain","fetch_process_variant","fetch_dtp","fetch_datasource","fetch_source_system","fetch_query","fetch_composite_provider","fetch_list_requests","fetch_request"})` (mirror `client.py:16`).
- `MUTATING_NAME_TOKENS = {"create","update","delete","activate","transport","run","execute","write","preview","data"}` — reject any requirement whose `operation`/`target` matches.
- `PolicyGate.evaluate(plan: CapturePlan) -> PolicyResult`: reject wildcards (`*`, empty target, overly broad repository path), enforce `max_operations` budget (default 8) and per-operation cap, drop non-allowlisted operations with explicit reasons. Pure deterministic; no LLM.

### P3 — `src/bwli/llm/planner.py`
- `parse_analysis_intent(question, *, runtime) -> AnalysisIntent` and `propose_capture_plan(intent, *, runtime) -> CapturePlan` using `OpenAICompatibleClient` only when `resolve_runtime()` is not `None`; otherwise raise/return a deterministic fallback plan derived from the focus objects.
- `execute_capture_plan(policy_result, *, snapshot ctx) -> ReviewPayload`: only iterate `policy_result.allowed_requirements`, mapping each to existing deterministic analysis (reuse snapshot lineage/impact/query/sql code paths already invoked by `server.py:959-1062`). Never call BW write paths (none exist). Sanitize via `sanitize_llm_evidence` before any LLM use.

### P4 — `src/bwli/llm/review.py`
- `MAX_ROUNDS = 2` (hard cap 3). `ReviewAssistant.run(payload, *, runtime, rounds=MAX_ROUNDS) -> ReviewPack`:
  1. `draft` prompt fed sanitized `ReviewPayload` + citation ids (reuse bounding helpers from `lineage_advisor`/`impact_advisor`).
  2. `critic` prompt: "list only citation/safety/unsupported-claim/unknown-omission defects as JSON"; if none, stop early.
  3. `revise` prompt consuming critic defects. Each completion passes `_validate_completion_safety`/`_validate_completion_citations` before acceptance; on validation failure, retry within remaining rounds, else fail closed.
- Stop conditions are explicit: critic returns no defects, or rounds exhausted, or budget reached — never a quality heuristic.

### P5 — `src/bwli/llm/validator.py`
- `validate_review_pack(pack, payload) -> ReviewPack`: (a) citation exactness — every cited token ∈ payload citation ids, every non-empty line cites (reuse `_line_has_citation`); (b) safety regex (reuse `_validate_completion_safety`); (c) unsupported-claim — flag report sentences asserting objects/severities not present in deterministic findings; (d) preservation — assert every `manual_verification=true` and every `unknown`-severity finding from `payload.deterministic_findings` appears in the report; fail closed with `LlmCitationError`/`LlmEvidenceError`. Stamp `audit.citation_validation = "passed"`.

### P6 — Server + Web wiring
- `src/bwli/server.py`: add request models `V1ReviewAssistRequest{question, scenario?, include_korean_summary?}` and route `POST /api/v1/snapshots/{snapshot_id}/review/assist` (and optional `/review/intent`), gated on `runtime_config.llm.enabled and configured` exactly like `_lineage_advice_payload` (`:2393`); when disabled, return deterministic findings + `llm_disabled: true`. Run through `assert_no_persisted_secrets` before returning. Audit via existing `LlmAuditMetadata`.
- `web/src/api.ts`: add `ReviewPack`/`ReviewAssistResponse` types and `postReviewAssist(snapshotId, body)`; extend `AppTab` with `'review'` only if a temporary standalone route is needed; preferred placement is inside Impact Review.
- `web/src/App.tsx`: add a Review Assistant panel inside the Impact workflow: NL textarea → `postReviewAssist`; render report with inline citation chips, deterministic findings table (verbatim), and an audit panel (rounds, policy decisions, redacted/omitted paths). Show "LLM disabled — deterministic findings only" when `llm_disabled`.

### P6a — Impact-centered UI navigation
- Add `const IMPACT_UNIFIED: boolean = false;` while migrating. When true, hide top-level `<TabButton id="query" ...>` and `<TabButton id="sql" ...>` (`web/src/App.tsx:1979,1980`) and keep their components compiled for advanced drawers.
- Extend `ImpactTab` with evidence sections: **Change scenario**, **Affected BW objects**, **Query exposure evidence**, **SQL / Native SQL references**, **Manual verification gaps**, and **Evidence brief**.
- Move Query XML input/output and SQL reference extraction controls into collapsible Impact evidence cards. The cards must keep existing copy boundaries: "No live query execution, preview, or data rows" and "Parse only · DB execution disabled".
- Update UI tests in `web/tests/sliceG.test.mjs` to assert that, under `IMPACT_UNIFIED`, Query/SQL are not primary nav and are rendered as Impact evidence sections. Keep compatibility tests for the hidden-but-compiled advanced components.

### P7 — Optional safe MCP/LangGraph adapter
- New `src/bwli/llm/mcp_adapter.py` (default-off): expose only `bwli_*` high-level read-only tools wrapping `BwReadClient`; the LangGraph stop condition is `PolicyResult.allowed && evidence_requirement_satisfied && within_budget && citation_validation_passed`. No raw BW Modeling MCP tool surface. Documented as exploratory.

---

## 7. Tests and verification matrix

New/updated tests (pytest, `tests/` mirrors module names):
- P0: `web/tests/sliceG.test.mjs` updated glossary assertions + new "Glossary tab hidden when `GLOSSARY_VISIBLE` false" test. Command: `npm --prefix web run test:slice-g`.
- P1: `tests/test_llm_intent.py` — model round-trip, `extra="forbid"` rejection, `parse_intent_content` fail-closed on malformed JSON.
- P1a: `tests/test_impact_evidence.py` — `ImpactEvidencePack` composition includes deterministic impact findings, query exposure evidence, SQL reference evidence, freshness/capture scope, unknown/manual-verification gaps; query/sql evidence must not override `impact.py` severity/confidence.
- P1a server/API: extend `tests/test_server.py` / `tests/test_v1_api.py` — `POST /api/v1/snapshots/{id}/impact/review` returns parse-only query/sql evidence when requested, keeps legacy `/sql/explain` compatibility, and never claims query or SQL execution.
- P2: `tests/test_llm_policy.py` — allowlist pass, mutating-name reject, wildcard reject, budget cap, data-preview reject, mixed plan partial-allow with reasons.
- P3: `tests/test_llm_planner.py` — intent parse via `httpx.MockTransport`, plan execution only touches allowed requirements, LLM-off fallback path, sanitization applied.
- P4: `tests/test_llm_review.py` — draft→critic→revise with mock transport; early-stop when critic clean; round cap enforced; validation-failure retry then fail-closed.
- P5: `tests/test_llm_validator.py` — citation-exactness fail, unsupported-claim fail, dropped-`manual_verification` fail, dropped-`unknown` fail, happy path stamps `passed`.
- P6: extend `tests/test_server.py` / `tests/test_v1_api.py` — `/review/assist` gated-off returns deterministic `ImpactEvidencePack` + `llm_disabled`; gated-on (mock) returns validated pack; no secrets persisted (`assert_no_persisted_secrets`).
- P6a: `web/tests/sliceG.test.mjs` — under `IMPACT_UNIFIED`, Query/SQL top-level tab buttons are hidden and Query exposure / SQL reference evidence cards are present inside Impact. Existing Query/SQL components remain compiled behind advanced drawers.
- P7: `tests/test_llm_mcp_adapter.py` — adapter exposes only read-only tools; rejects mutating tool names; default-off.

Per-PR gate (all must pass before merge):
```
uv run pytest -q                 # ≥ 390 baseline; grows per PR
uv run ruff check .
uv run mypy src
npm --prefix web run build
npm --prefix web run test:slice-g
```
Targeted during dev: `uv run pytest tests/test_impact_evidence.py tests/test_llm_policy.py tests/test_llm_review.py tests/test_llm_validator.py -q`.

LLM tests must use `httpx.MockTransport` injected via the existing `transport` parameter (`OpenAICompatibleClient.__init__`, `openai_compatible.py:66`) — **no real endpoint** in CI.

---

## 8. Rollout / rollback

- Feature flags: keep LLM default-off (`LlmConfig.enabled=False`); the Review Assistant route returns deterministic-only output until a user supplies a local endpoint at runtime. `GLOSSARY_VISIBLE` gates glossary re-enable; `IMPACT_UNIFIED` gates the Query/SQL top-nav-to-Impact migration.
- Sequential merge P0→P1→P1a→P2→P3→P4→P5→P6→P6a→P7; each PR self-contained and green. P0 ships immediately (pure UI hide, lowest risk). P1a should ship before serious LLM orchestration so the Review Assistant consumes the unified impact evidence contract from day one.
- Rollback: P0 revert = flip `GLOSSARY_VISIBLE = true` and restore the two test assertions. P1a rollback = keep legacy Query/SQL tabs/routes and remove only `impact/review` + `impact_evidence.py`; core `impact.py`, `query_analysis.py`, and `field_lineage.py` remain untouched. P3–P7 revert = remove the new module + route; deterministic analyzer and existing advisors untouched, so no regression to current LLM advice/tour features.
- No data migration; no persisted schema change; audit logs remain secret-free.

---

## 9. Glossary deferral / hide-now plan

- **Hide (P0):** `GLOSSARY_VISIBLE = false` in `web/src/App.tsx` gates the tab button (`:1980`), `TermsOverview` (`:1903`), auto-fetch in `loadContext` (`:697-707`), and refresh rerun (`:1046-1049`); object-detail `GlossaryList` (`:2269,2566`) guarded to stop incidental fetches. Components/handlers remain compiled.
- **Keep backend:** `/api/v1/glossary/*` (`server.py:819,829,842`) and the object-detail `glossary_terms` embed (`:2577,2792,3045`) stay for compatibility; auto-sync (`_sync_glossary_for_*`, `:2826,2834`) only triggers on GETs the UI no longer issues, so it goes dormant without code change.
- **Tests:** update `web/tests/sliceG.test.mjs` glossary-coupled assertions (`:125-133`, `:590-601`) in the same PR.
- **Defer:** track a later phase (P-later) to redesign Glossary as evidence-bound terminology surfacing; not in this plan's scope.

---

## 10. Open questions

1. **Output filename** for the persisted plan: proposed `docs/plans/2026-06-23-opus48-evidence-bound-llm-review-assistant-plan.md` (matches existing `docs/plans/` naming). Confirm.
2. **`MAX_ROUNDS`** default — 2 (recommended) vs 3 for the draft→critic→revise loop.
3. **Glossary object-detail embed** — fully guard the object-detail `GlossaryList`/`glossary_terms` fetch (recommended, fully silences glossary) or leave the inline object-detail terms visible while hiding only the dedicated tab?
4. **ImpactEvidencePack scope** — start with query provider/field exposure + SQL referenced objects/columns only, or also include calculated/restricted key figure dependency summaries in the first P1a slice?
5. **P7 scope** — build the read-only MCP/LangGraph adapter now (default-off) or document-only until the P0–P6a core is validated in real use?
