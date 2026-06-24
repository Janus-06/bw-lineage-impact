# Agentic Impact Review Assistant Implementation Plan

> Source: Claude Code `--model opus`; expected model usage should include `claude-opus-4-8`.
> Raw run artifact: `.hermes/plans/claude-runs/2026-06-23-opus48-agentic-impact-review-plan/result.md`.
> Status: planning-only; no implementation, no commit, no live BW call, no credential change in this run.
> Date: 2026-06-23 KST.

**Goal:** Give the LLM *bounded analytical autonomy* over the deterministic `ImpactEvidencePack` — it chooses review angles, forms hypotheses, flags missing evidence, ranks manual checks, and drafts review narratives across a budgeted multi-call loop — then present that autonomous analysis as a structured, auditable **Agentic Review Workspace** inside the existing unified Impact view. The deterministic core (`impact.py`) remains the sole authority on severity/confidence/affected objects; the LLM never executes BW, runs SQL, previews data rows, or mutates state.

**Architecture:** A deterministic orchestrator (`AgenticReviewAssistant`) drives a *fixed-stage* pipeline over the existing `ImpactEvidencePack`. Autonomy lives in the *content* each stage produces (which objectives, which hypotheses, which parse-only evidence to request) — not in arbitrary control flow. The only branching step (LLM-requested evidence enrichment) passes through a deterministic `PolicyGate` and a fixed enricher catalog restricted to parse-only operations already reachable from the snapshot store. Every LLM call goes through the single guarded `OpenAICompatibleClient`; every output line is citation-bound and validated fail-closed. LLM disabled-by-default degrades to the deterministic pack verbatim.

**Tech Stack:** Python 3.11+ / FastAPI / Pydantic v2 (`ConfigDict(extra="forbid")`), `uv` + `pytest`/`ruff`/`mypy`; React + TypeScript web (`web/`, Vitest slice-G). LLM I/O only via `src/bwli/llm/openai_compatible.py` (local/private OpenAI-compatible endpoint, network-guarded). No new third-party agent framework (no raw LangChain/LangGraph/MCP tool loop) in this plan.

---

## 1. Context and how this differs from the prior plan

The sibling plan `docs/plans/2026-06-23-opus48-evidence-bound-llm-review-assistant-plan.md` delivered the **passive** posture: deterministic analyze → single LLM draft/critique/revise → validate, with the LLM as a reporting layer. That work has largely landed:

- `src/bwli/impact_evidence.py` — deterministic `ImpactEvidencePack` (`build_impact_evidence_pack`, `:95`) composing `impact.py` findings + Query XML exposure + Native SQL references + freshness + `manual_verification_gaps` + `coverage_summary`. It explicitly **does not** call BW or import LLM modules; `final_authority = "impact.py"` (`impact_evidence.py:82`).
- `POST /api/v1/snapshots/{snapshot_id}/impact/review` (`server.py:1038`) returns the pack deterministically via `_run_v1_impact_review` (`server.py:2651`), with helpers `_impact_review_query_results` (`:2736`), `_impact_review_sql_results` (`:2760`), `_impact_review_freshness` (`:2778`).
- Unified Impact UI: `postImpactReview` (`web/src/api.ts:798`), `ImpactReviewResponse`/`QueryExposureEvidence`/`SqlReferenceEvidence`/`ManualVerificationGap` types (`api.ts:429-491`), `ImpactEvidenceCards` + `AuthorityCallout` (`App.tsx:2526,2638`). Query/SQL are evidence cards, not top-level destinations.

This plan adds the **active** posture *on top of* that contract. The LLM now reasons in multiple bounded steps, may pull additional parse-only evidence within policy, and emits structured review artifacts (objectives, hypotheses, gaps, ranked manual checks, review cards, CAB summary) rather than one prose blob. Nothing here regresses the deterministic `/impact/review` route, the existing `/impact/advice` (`server.py:1050`) / `/impact/tour` (`server.py:1064`) routes, or current E2E.

### Current code facts to build on

- **LLM transport/audit:** `OpenAICompatibleClient.chat(LlmChatRequest) -> LlmCompletion` (`openai_compatible.py:77`); per-request `_validate_local_llm_base_url` (`:78`) + `_EndpointGuardTransport` (`:129`) + `_GuardedNetworkBackend.connect_tcp` (`:159`) blocking non-local/link-local/metadata hosts; `trust_env=False` (`:88`). `LlmAuditMetadata` carries `prompt_sha256`, `sanitized_input_sha256`, `request_citation_ids`, `citation_validation: "not_validated"|"passed"`, `usage` (sanitized) (`:39-53`). `write_llm_audit_log` writes secret-free JSON (`:115`). Tests inject `httpx.MockTransport` via the `transport=` param (`:70`).
- **Validators (reuse verbatim):** `_validate_completion_safety` (sensitive-text regex, `explainer.py:113`), `_validate_completion_citations` (every non-empty line must cite an allowed id, `:118`), `_line_has_citation` (`:130`, enforces `cited ⊆ allowed` and non-empty intersection). Errors: `LlmCitationError` (`:36`), `LlmEvidenceError` (`:40`). Strict-JSON tour validation pattern in `tour.py:101`.
- **Bounding helpers (mirror):** `impact_advisor.py` caps `_MAX_AFFECTED_OBJECTS = 60` (`:22`), `_MAX_LLM_EVIDENCE_TEXT_CHARS` (`:23`), `_truncate_evidence_text` (`:309`); `explainer.py` `_MAX_LLM_EVIDENCE_ITEMS = 80` (`:32`), `_bound_evidence_for_prompt` (`:136`). Citation ids are minted deterministically (`scenario:change`, `affected:N`, `sqlref:...`, fragment/column ids).
- **Config gate:** `LlmConfig.enabled = False` (`config.py:95`), `resolve_runtime() -> LlmRuntimeConfig | None` returns `None` when disabled (`config.py:101`); `LlmRuntimeConfig{base_url, model, api_key: SecretStr}` (`config.py:84`). Server gate pattern: `if not runtime_config.llm.enabled or not runtime_config.llm.configured: return <disabled payload>` (e.g. `server.py:2808`). Secret guard `assert_no_persisted_secrets` (`server.py:84` import, used `:2657`, `:2411`, …).
- **Authority models:** `ImpactFinding{severity: ImpactSeverity, confidence: str, manual_verification: bool, evidence_node_ids, evidence_edge_ids}` (`impact.py:52`), `ImpactSeverity{HIGH,MEDIUM,LOW,UNKNOWN}` (`:25`), `ChangeType` (`:15`).

### Verification baseline (must stay green)

```bash
uv run pytest -q                 # 397 passed, 1 warning (current); grows per slice
uv run ruff check .
uv run mypy src
npm --prefix web run test:slice-g   # 21 passed (current)
npm --prefix web run build
git diff --check
```

---

## 2. What "LLM autonomy" means here (and what it does not)

**Bounded analytical autonomy — the LLM MAY:**

1. Choose **review objectives / angles** from the evidence (e.g. "focus on downstream queries with stale loads", "scrutinise Native SQL references that intersect impact scope").
2. Formulate **hypotheses** about likely real-world impact, likely false positives, and confidence rationale — *as opinions parallel to* the deterministic findings.
3. Identify **evidence gaps** ("no freshness evidence for ADSO X; impacted query Y has no parsed XML").
4. **Request additional local, parse-only evidence** from a fixed enricher catalog (re-parse an impacted query's XML, re-parse a referenced Native SQL view, look up request freshness for an in-scope object). Requests are *proposals*; the deterministic `PolicyGate` decides.
5. **Rank manual BWMT/Eclipse checks** and draft a **review narrative** + **CAB/change summary**.
6. Perform **bounded multi-call reasoning** over the `ImpactEvidencePack` across a small, budgeted set of stages.

**The LLM MUST NOT:**

- Call raw BW tools, `fetch_*` live, run/execute BW queries, execute DB SQL, preview/return data rows, activate, transport, write, or mutate any project/snapshot state.
- Issue arbitrary control flow or open-ended tool loops. There is no raw agent/tool surface; the only branch is enrichment-request → gate → fixed enricher.
- Overwrite deterministic severity/confidence/affected-object sets. It may *propose* a divergent concern, but only under an explicit `llm_proposed_concern` label that the validator keeps separate.
- Emit any line not backed by a provided citation id; drop any `manual_verification=true` or `UNKNOWN`-severity finding; or surface any sensitive/internal text.

Autonomy is in **content**, not **capability**. The capability envelope is identical to today's parse-only, snapshot-local, read-only surface.

---

## 3. Target loop

```
ImpactEvidencePack  (deterministic; impact.py authority; built offline from snapshot store)
        │
        ▼
[LLM] Review Planner ──▶ AgenticReviewPlan{ objectives[], evidence_requests[] }      (strict JSON, fail-closed)
        │
        ▼
[DET] PolicyGate.evaluate(plan) ──▶ decisions[] (allow|reject + reason), budget check
        │ (allowed requests only)
        ▼
[DET] EvidenceEnricher.run(allowed) ──▶ augmented ImpactEvidencePack               (parse-only, snapshot-local)
        │   (≤ MAX_ENRICHERS; ≤ MAX_PLANNER_ROUNDS re-plan passes)
        ▼
[LLM] Hypothesis / Risk Reviewer ──▶ hypotheses[], evidence_gaps[], manual_checks[]  (each citation-bound)
        │
        ▼
[LLM] Critic ──▶ defects[] (citation / safety / unsupported-claim / severity-override / gap-omission)
        │   (if none → stop; else one revise pass, ≤ MAX_REVIEW_ROUNDS)
        ▼
[LLM] Final Synthesis ──▶ review cards[] + narrative + CAB summary
        │
        ▼
[DET] FinalValidator ──▶ citation exactness + safety + deterministic-authority preservation
        │                 + manual_verification/UNKNOWN preservation  (fail-closed)
        ▼
AgenticReviewRun  ──▶  Web Agentic Review Workspace
```

### Hard budgets (defaults; all configurable with hard caps)

| Budget | Default | Hard cap | Enforced by |
| --- | --- | --- | --- |
| `max_planner_rounds` | 1 | 2 | orchestrator |
| `max_evidence_requests` (per run) | 6 | 10 | `PolicyGate` |
| `max_enrichers_executed` | 6 | 10 | `PolicyGate` |
| `max_review_rounds` (reviewer→critic→revise) | 2 | 3 | orchestrator |
| `max_llm_calls` (total) | 6 | 8 | orchestrator |
| `max_cards` | 12 | 20 | `FinalValidator` |
| per-call evidence size | reuse `_MAX_LLM_EVIDENCE_ITEMS=80`, `_MAX_*_TEXT_CHARS=600` | — | bounding helpers |
| `max_latency_ms` | 60_000 | 120_000 | orchestrator (wall clock) |

### Stop conditions (mechanical — never "answer quality feel")

- Critic returns zero defects, OR review rounds exhausted; AND `FinalValidator` passes; OR
- Any budget reached (rounds / calls / latency / requests), OR
- `PolicyGate` rejects every enrichment request and the reviewer produced no new hypotheses, OR
- Any LLM error / parse failure / validation failure → **fail closed**: return the deterministic `ImpactEvidencePack` with `status="fallback"` and the partial trace; no LLM artifacts shown as authoritative.

---

## 4. Deterministic authority boundary

`impact.py` remains the only authority for `severity`, `confidence`, `affected_objects`, and `manual_verification`. They are copied **verbatim** into `AgenticReviewRun.deterministic_pack` and into every `kind="deterministic_finding"` card.

The LLM produces **parallel, clearly-labeled** fields only: `review_priority`, `hypothesis`, `recommended_manual_check`, `confidence_rationale`. A divergent severity opinion is allowed **only** as a `ReviewHypothesis.severity_opinion` rendered under a `kind="llm_proposed_concern"` card — it can never rewrite `ImpactFinding.severity`. `FinalValidator` fails closed if a synthesis card claims a deterministic finding's severity differs from the pack. Every card carries `kind ∈ {deterministic_finding, llm_proposed_concern, manual_verification_required}` so the UI labels provenance unambiguously.

---

## 5. New backend modules and models

All new modules under `src/bwli/llm/`, mirroring existing style: `from __future__ import annotations`, Pydantic `ConfigDict(extra="forbid")`, no live calls in model/parser modules, reuse of existing sanitizer/validators/transport.

### 5.1 `src/bwli/llm/agentic_review.py` — models + prompt/JSON parsers

```text
Literal["reparse_query_xml","reparse_native_sql_view","lookup_request_freshness","recompute_impact_pack"]  EnricherName
Literal["deterministic_finding","llm_proposed_concern","manual_verification_required"]                     ReviewCardKind
Literal["completed","disabled","fallback","failed"]                                                        ReviewRunStatus

ReviewObjective       { id, title, rationale, citation_ids: list[str] }
EvidenceRequest       { id, enricher: EnricherName, target: str, reason: str, citation_hint: str | None }
EvidenceRequestDecision { request_id, allowed: bool, reason: str }
AgenticReviewPlan     { objectives: list[ReviewObjective], evidence_requests: list[EvidenceRequest], notes: str }

ReviewHypothesis      { id, statement, status: Literal["proposed","supported","refuted"],
                        severity_opinion: ImpactSeverity | None, supports_finding_ids: list[str],
                        confidence_rationale: str, citation_ids: list[str] }
EvidenceGap           { id, description, missing_evidence: str, suggested_local_action: EnricherName | None,
                        related_object_id: str | None, citation_ids: list[str] }
ManualCheck           { id, title, tool: Literal["BWMT","Eclipse","HANA_Studio","manual"],
                        steps_summary, priority: ImpactSeverity, related_finding_ids: list[str],
                        citation_ids: list[str] }
AgenticReviewCard     { id, kind: ReviewCardKind, title, body, severity_label: ImpactSeverity | None,
                        review_priority: int, source_finding_id: str | None, citation_ids: list[str] }

ReviewTraceStep       { stage: str, round: int, summary: str, llm_audit: LlmAuditMetadata | None,
                        citation_validation: Literal["not_validated","passed","failed"] }
AgenticReviewBudget   { max_planner_rounds, max_evidence_requests, max_review_rounds,
                        max_llm_calls, max_cards, max_latency_ms }
AgenticReviewBudgetUsage { planner_rounds, evidence_requests, enrichers_executed, review_rounds,
                           llm_calls, cards, elapsed_ms }

AgenticReviewRun      { schema_version: str = "1.0", snapshot_id: str | None, llm_enabled: bool,
                        status: ReviewRunStatus, objective_question: str | None,
                        objectives: list[ReviewObjective], hypotheses: list[ReviewHypothesis],
                        evidence_gaps: list[EvidenceGap], manual_checks: list[ManualCheck],
                        cards: list[AgenticReviewCard], cab_summary: str,
                        deterministic_pack: ImpactEvidencePack,
                        trace: list[ReviewTraceStep], budget: AgenticReviewBudget,
                        budget_usage: AgenticReviewBudgetUsage,
                        policy_decisions: list[EvidenceRequestDecision],
                        audit_trail: list[LlmAuditMetadata], llm_disabled: bool = False }
```

Prompt builders + strict-JSON parsers (fail-closed like `validate_tour_completion_content`, `tour.py:101`), all sanitizing input with `sanitize_llm_evidence` and bounding with the existing truncation helpers:

- `build_review_plan_request(pack, *, question, citation_ids) -> LlmChatRequest`
- `parse_review_plan_content(content, *, allowed_citation_ids, allowed_enrichers) -> AgenticReviewPlan`
- `build_hypothesis_request(pack, plan, *, citation_ids) -> LlmChatRequest`
- `parse_hypothesis_content(content, *, allowed_citation_ids) -> tuple[hypotheses, gaps, manual_checks]`
- `build_critic_request(...) -> LlmChatRequest`; `parse_critic_content(content) -> list[CriticDefect]`
- `build_synthesis_request(...) -> LlmChatRequest`; `parse_synthesis_content(content, *, allowed_citation_ids) -> tuple[cards, cab_summary]`

Citation id space is derived deterministically from the pack (no live ids): `scenario:change`, `affected:N`, `query:<query_id>`, `sqlref:<edge_id>`, `freshness:<object_id>`, `gap:<gap_id>` — the same ids already minted by `impact_evidence.py` plus stable derivations. The parser rejects any cited id ∉ this allowlist.

### 5.2 `src/bwli/llm/agentic_policy.py` — deterministic gate (no LLM)

```python
ALLOWED_ENRICHERS = frozenset({
    "reparse_query_xml", "reparse_native_sql_view",
    "lookup_request_freshness", "recompute_impact_pack",
})
MUTATING_TOKENS = {"create","update","delete","activate","transport","run",
                   "execute","write","preview","data","row","rows","drop","insert"}

class PolicyGate:
    def evaluate(self, plan: AgenticReviewPlan, *, pack: ImpactEvidencePack,
                 budget: AgenticReviewBudget) -> tuple[list[EvidenceRequest], list[EvidenceRequestDecision]]:
        # reject enricher ∉ ALLOWED_ENRICHERS
        # reject wildcard / empty target; reject any target/enricher containing MUTATING_TOKENS
        # reject target object_id ∉ pack object scope (impacted findings + scenario + evidence ids)
        # enforce max_evidence_requests; truncate with explicit decisions; dedupe
        # every decision carries a human-readable reason
```

The gate guarantees the only side effects the loop can produce are re-running the same parse-only providers already used to build the pack, scoped to objects already in the snapshot/pack. No new BW network call, no new file outside the snapshot store, no data rows.

### 5.3 `src/bwli/llm/agentic_enricher.py` — deterministic executor (no LLM)

`run_enrichers(allowed, *, root, store, snapshot_id, request, prior_pack) -> ImpactEvidencePack` maps each allowed `EvidenceRequest` onto the **existing** deterministic helpers and recomposes the pack via `build_impact_evidence_pack`:

- `reparse_query_xml` → `_impact_review_query_results`-style call (`server.py:2736`) for the named in-scope query.
- `reparse_native_sql_view` → `_impact_review_sql_results`-style `_parse_v1_sql` (`server.py:2760`) for an in-scope/provided view.
- `lookup_request_freshness` → `_impact_review_freshness` (`server.py:2778`).
- `recompute_impact_pack` → re-run `build_impact_evidence_pack` with the union of evidence.

To avoid circular imports, the enricher takes injected callables (the orchestrator passes thin adapters over the server helpers, or the helpers are lifted into a small `impact_review_service` module — see Q5). No BW write paths exist anywhere in `BwReadClient`, so the surface is naturally bounded.

### 5.4 `src/bwli/llm/agentic_orchestrator.py` — `AgenticReviewAssistant`

`AgenticReviewAssistant.run(pack, *, runtime, question, budget, enricher) -> AgenticReviewRun` drives §3 with explicit stop conditions and a wall-clock latency guard. Each LLM call:

1. builds a request with bounded, sanitized evidence + the derived citation id allowlist;
2. calls `OpenAICompatibleClient(runtime=...).chat(...)`;
3. runs `_validate_completion_safety` + `_validate_completion_citations`;
4. parses strict JSON fail-closed;
5. appends a secret-free `ReviewTraceStep` + the `LlmAuditMetadata` to `audit_trail`.

On any failure → return `status="fallback"` with the deterministic pack and the partial trace. When `runtime is None` (LLM disabled) the orchestrator short-circuits to `status="disabled"`, `llm_disabled=True`, empty agentic artifacts, deterministic pack populated.

### 5.5 `src/bwli/llm/agentic_validator.py` — final programmatic gate (no LLM)

`validate_agentic_run(run, *, pack) -> AgenticReviewRun`:

- **citation exactness** — every card/hypothesis/gap/manual-check citation ∈ derived allowlist (reuse `_line_has_citation` on rendered text); every non-empty narrative/CAB line cites.
- **safety** — reuse `_validate_completion_safety` over `cab_summary` + card bodies.
- **authority preservation** — for every `kind="deterministic_finding"` card, `severity_label` must equal the matching `ImpactFinding.severity`; a divergent opinion must be a `llm_proposed_concern` card, never deterministic.
- **gap preservation** — every `manual_verification=true` finding and every `UNKNOWN`-severity finding in `pack` must appear as a card or manual check; fail closed otherwise.
- **budget** — `len(cards) ≤ max_cards`.
- On pass, stamp every `audit_trail` entry / trace step `citation_validation = "passed"` and `status = "completed"`. On any failure raise `LlmCitationError`/`LlmEvidenceError`; orchestrator converts to `status="fallback"`.

---

## 6. New endpoint

`src/bwli/server.py`:

- Request model `V1AgenticReviewRequest(V1ImpactReviewRequest)` adding `question: str | None = None`, `objectives_hint: list[str] = []`, `include_korean_summary: bool = False`, optional budget overrides (`max_review_rounds`, `max_planner_rounds`, …, each `ge`/`le` bounded and clamped to hard caps).
- Route `POST /api/v1/snapshots/{snapshot_id}/impact/review/agentic` returning `AgenticReviewRun`:

```python
@app.post("/api/v1/snapshots/{snapshot_id}/impact/review/agentic", response_model=AgenticReviewRun)
def impact_review_agentic_v1(snapshot_id: str, request: V1AgenticReviewRequest) -> AgenticReviewRun:
    try:
        assert_no_persisted_secrets(request.model_dump(mode="json"))
        pack = _run_v1_impact_review(root, catalog_store, snapshot_id, _as_review_request(request))
        run = _agentic_review_payload(runtime_config=runtime_config, root=root,
                                      store=catalog_store, snapshot_id=snapshot_id,
                                      request=request, pack=pack)
        assert_no_persisted_secrets(run.model_dump(mode="json"))
        return run
    except Exception as exc:
        raise _http_error(exc) from exc
```

- `_agentic_review_payload(...)` mirrors `_impact_advice_payload` (`server.py:2802`): if `not runtime_config.llm.enabled or not runtime_config.llm.configured`, return `AgenticReviewRun(status="disabled", llm_disabled=True, deterministic_pack=pack, cards=<deterministic cards derived from pack findings>, ...)`. Otherwise resolve runtime and run `AgenticReviewAssistant`.
- Even in the disabled path, emit `kind="deterministic_finding"` + `manual_verification_required` cards derived **deterministically** from the pack so the workspace is useful with LLM off.
- Keep `/impact/review`, `/impact/advice`, `/impact/tour`, `/sql/*` unchanged. Audit trail is the list of `LlmAuditMetadata`; `write_llm_audit_log` may persist each call locally (optional, behind existing audit-dir config). `assert_no_persisted_secrets` runs on request and response.

---

## 7. Web UI — Agentic Review Workspace

Present autonomous analysis as a **structured workspace**, not a chat transcript. Lives inside the existing Impact view, below `ImpactEvidenceCards` (`App.tsx:2500`) / `AuthorityCallout` (`App.tsx:2501`). `AppTab` stays `'impact'` (no new top-level tab).

`web/src/api.ts`: add `AgenticReviewRun` and child interfaces mirroring §5.1; `postAgenticReview(snapshotId, body): Promise<AgenticReviewRun>` calling the new route.

`web/src/App.tsx`: new `AgenticReviewWorkspace` component with sections:

1. **Review objective** — NL `question` textarea + run button + chosen `objectives[]` (rationale + citation chips).
2. **Autonomous reasoning trace summary** — compact stage/round list from `trace[]` (stage, round, one-line summary, citation_validation badge). Not raw chat.
3. **Prioritized review cards** — `cards[]` ordered by `review_priority`, each with a provenance badge: `Deterministic finding` / `LLM proposed concern` / `Manual verification required`, inline citation chips, `severity_label`.
4. **Evidence map** — maps each card/hypothesis to backing citation ids and the deterministic pack object; reuse coverage metrics (`coverageValue`, `App.tsx:2665`).
5. **Missing evidence / gaps** — `evidence_gaps[]` with `suggested_local_action`; never hidden.
6. **Manual BWMT checklist** — `manual_checks[]` ranked by `priority`, with tool + steps summary.
7. **CAB / change summary** — `cab_summary`, citation-bound, copy-to-clipboard.
8. **Validator + budget + audit** — `status`, `budget_usage`, `policy_decisions[]` (allowed/rejected + reason), redacted/omitted paths from sanitizer, rounds, per-call `citation_validation`.

Disabled/fallback states must be explicit and non-hiding:

- `status="disabled"` → banner **"LLM disabled — deterministic findings only"**, deterministic cards still rendered.
- `status="fallback"` → banner **"Autonomous review failed validation — showing deterministic findings"** + partial trace + rejection reason.

Reuse existing copy boundaries verbatim: `No BW query execution · No data preview` (`App.tsx:2547`) and `Parse only · DB execution disabled` (`App.tsx:2583`). Keep `AuthorityCallout` visible above the workspace so the deterministic boundary is always on screen.

---

## 8. Safety / security boundaries (non-negotiable)

- **Local-first, LLM disabled by default.** `LlmConfig.enabled=False`; route returns deterministic `status="disabled"` until the user supplies a local endpoint at runtime. No central hosted backend.
- **Local/company OpenAI-compatible endpoint only.** All LLM I/O through `OpenAICompatibleClient` with `_validate_local_llm_base_url` + `_GuardedNetworkBackend` (`openai_compatible.py:78,159`). Public/cloud endpoints remain blocked; a future controlled-remote mode would be a separate, approval-gated change — out of scope here.
- **No raw tool/agent surface.** The only branch is enrichment-request → `PolicyGate` → fixed enricher catalog. No LangChain/LangGraph/MCP. The catalog is parse-only and snapshot-local; it issues **no** BW network call.
- **No live BW call** added; **no SQL/query execution**; **no data rows/preview** to the LLM. A future `data_gate.allow_llm_rows` would be a separate approval-gated change; this plan never sends rows.
- **No secrets** in prompts, audit, snapshots, or UI. Reuse `sanitize_llm_evidence`/`sanitize_text`; `assert_no_persisted_secrets` on request and response; audit logs via `write_llm_audit_log` (no endpoint/key).
- **Deterministic authority preserved**; `manual_verification`/`UNKNOWN` never dropped; every output line citation-bound and validated fail-closed.
- **Bounded everything**: rounds, calls, requests, cards, evidence size, wall-clock latency.

---

## 9. Implementation slices (PR-sized, sequential)

Named `Ax` to avoid collision with the prior plan's `Px`. Each independently green against the §1 baseline.

- **A0 — Models + parsers (no live).** `src/bwli/llm/agentic_review.py`: §5.1 models + prompt builders + strict-JSON parsers. *Acceptance:* round-trip + `extra="forbid"` rejection + malformed-JSON fail-closed + citation-allowlist rejection.
- **A1 — PolicyGate.** `src/bwli/llm/agentic_policy.py` (§5.2). No LLM. *Acceptance:* allow valid parse-only request; reject non-allowlisted enricher, mutating token, wildcard, out-of-scope target, over-budget; every decision has a reason.
- **A2 — Review Planner step.** Planner prompt + parser through `OpenAICompatibleClient` (MockTransport), LLM-off → deterministic empty plan. *Acceptance:* parses valid plan; rejects fabricated citations/enrichers; sanitization applied.
- **A3 — EvidenceEnricher.** `src/bwli/llm/agentic_enricher.py` (§5.3) reusing `_impact_review_*` helpers. No LLM. *Acceptance:* allowed requests recompose pack; query/SQL evidence never alters `impact.py` severity; no BW call (assert via injected fakes).
- **A4 — Hypothesis/Risk Reviewer + Critic + revise.** Bounded by `max_review_rounds`. *Acceptance:* clean critic → early stop; defects → one revise; validation failure within budget → fail closed.
- **A5 — Final Synthesis.** Cards + CAB summary parser; `max_cards` enforced. *Acceptance:* cards carry correct `kind`; deterministic cards mirror pack severities.
- **A6 — FinalValidator.** `src/bwli/llm/agentic_validator.py` (§5.5). *Acceptance:* citation-exactness fail; severity-override fail; dropped `manual_verification`/`UNKNOWN` fail; happy path stamps `passed`/`completed`.
- **A7 — Orchestrator.** `AgenticReviewAssistant.run` (§5.4) with budgets, stop conditions, latency guard, audit trail, fallback. *Acceptance:* end-to-end mock run completes; each budget independently triggers stop; any error → `status="fallback"` with deterministic pack.
- **A8 — Server endpoint.** `V1AgenticReviewRequest` + route + `_agentic_review_payload` (§6). *Acceptance:* disabled → `status="disabled"` + deterministic cards; enabled (mock) → validated run; secret-guard on req/resp; no secrets in audit.
- **A9 — UI workspace.** `web/src/api.ts` types + `postAgenticReview`; `AgenticReviewWorkspace` in `App.tsx`; styles in `web/src/styles.css`. *Acceptance:* deterministic-only render with LLM off; provenance labels visible; gaps/validator/budget shown; copy boundaries preserved.
- **A10 — Optional Korean summary + docs.** `include_korean_summary` into synthesis (mirror `tour.py:48`); update `docs/brief.md`. *Acceptance:* Korean summary lines remain citation-bound.

**Dependency order:** A0 → A1 → A2 → A3 → A4 → A5 → A6 → A7 → A8 → A9; A10 last/optional. A1/A3/A6 (deterministic) can land slightly ahead of their LLM consumers.

---

## 10. Tests and verification

New pytest modules (mirror module names under `tests/`):

- `tests/test_agentic_review_models.py` (A0) — model round-trip, `extra="forbid"`, parser fail-closed, citation allowlist.
- `tests/test_agentic_policy.py` (A1) — allow/reject matrix, scope check, budget cap, reasons present.
- `tests/test_agentic_planner.py` (A2) — `httpx.MockTransport` plan parse, fabricated-citation/enricher rejection, LLM-off path.
- `tests/test_agentic_enricher.py` (A3) — pack recomposition, severity invariance, no BW call.
- `tests/test_agentic_reviewer.py` (A4) — early-stop, revise, fail-closed.
- `tests/test_agentic_synthesis.py` (A5) — card `kind` correctness, `max_cards`.
- `tests/test_agentic_validator.py` (A6) — citation/severity-override/gap-preservation fails + happy path.
- `tests/test_agentic_orchestrator.py` (A7) — full mock run, each stop condition, fallback.
- `tests/test_v1_api.py` (A8, extend) — `/impact/review/agentic` disabled → deterministic run; enabled (mock) → validated run; secret-guard; existing routes unaffected.
- `web/tests/sliceG.test.mjs` (A9, extend) — workspace renders deterministic cards with LLM off; provenance labels present; gaps + validator + budget sections present; copy-boundary strings present.

All LLM tests inject `httpx.MockTransport` via the `transport=` param (`openai_compatible.py:70`) — **no real endpoint in CI**.

Per-slice gate (all must pass before merge):

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src
npm --prefix web run test:slice-g
npm --prefix web run build
git diff --check
```

Targeted during dev:

```bash
uv run pytest tests/test_agentic_policy.py tests/test_agentic_validator.py \
              tests/test_agentic_orchestrator.py tests/test_v1_api.py -q
```

### Browser / E2E verification (built app, A9)

1. LLM **off**: Impact → run Agentic review → "LLM disabled — deterministic findings only", deterministic cards = pack findings, `AuthorityCallout` intact (`final_authority=impact.py`).
2. LLM **on** (local mock/stub endpoint): objectives, prioritized cards with provenance badges, evidence map, gaps, ranked manual checks, CAB summary, validator `completed`, budget usage, policy decisions all render.
3. Provenance labels distinct: `Deterministic finding` vs `LLM proposed concern` vs `Manual verification required`.
4. No data rows / no execution claims anywhere; copy boundaries `No BW query execution · No data preview` and `Parse only · DB execution disabled` present.
5. Force a validator failure (stub bad citation) → `status="fallback"` banner + deterministic cards still shown.

---

## 11. Migration from current state

- Build directly on the existing deterministic `ImpactEvidencePack` and `/impact/review` — the agentic route calls `_run_v1_impact_review` to obtain the pack, then layers autonomy on top. No change to `impact_evidence.py` semantics or `final_authority`.
- Preserve `/impact/review`, `/impact/advice`, `/impact/tour`, `/sql/*` and their tests. The existing `Impact Brief` / `Business Summary` single-shot advice evolves into the synthesis-stage cards/CAB summary; the old advice route stays for compatibility and can be deprecated later behind a flag.
- The unified Impact UI gains the workspace as an additive section; `ImpactEvidenceCards` and `AuthorityCallout` are unchanged. No `AppTab` change, so current slice-G assertions about Impact remain valid (extend, don't rewrite).
- No data migration, no persisted schema change, audit logs remain secret-free.

---

## 12. Rollback

- Feature-flag the workspace + route behind `AGENTIC_REVIEW_ENABLED` (server) / a UI constant. With LLM off the route is already inert (deterministic-only), so the lowest-risk default is safe.
- Per-slice revert: remove the new module(s) + route + UI section. Because all new code is additive and the deterministic pack/route are untouched, reverting any `Ax` cannot regress the deterministic `/impact/review`, the existing advisors, or current E2E.
- Hard rollback: delete `src/bwli/llm/agentic_*.py`, the agentic route + request model, the `web` workspace component/types, and the new tests; the prior plan's evidence-bound surface remains fully functional.

---

## 13. Open questions

1. **Default budgets** — `max_review_rounds=2`, `max_planner_rounds=1`, `max_llm_calls=6`, `max_latency_ms=60_000`. Confirm or tune for the local endpoint's latency.
2. **Enricher catalog scope** — start with all four §5.2 enrichers, or restrict A-phase to freshness + recompute only and add re-parse enrichers in a later slice?
3. **Disabled-mode cards** — derive deterministic cards from the pack even when LLM is off (recommended), or render only the raw pack and hide the workspace?
4. **Audit persistence** — call `write_llm_audit_log` per LLM call by default, or keep audit only in the response `audit_trail` unless the user opts in?
5. **`impact_review_service` extraction** — lift `_impact_review_query_results`/`_impact_review_sql_results`/`_impact_review_freshness` out of `server.py` into a shared service module so the enricher reuses them without import cycles? (Recommended; low risk, improves testability.)

---

## 한국어 요약

LLM에게 **제한된 분석 자율성**을 주는 계획입니다. 기존 결정론적 `ImpactEvidencePack` 위에서 LLM이 ① 리뷰 관점/목표 선택, ② 가설·오탐 추정, ③ 증거 공백 식별, ④ 수동 점검(BWMT) 우선순위화, ⑤ 리뷰/CAB 서술 작성을 **여러 단계의 예산 제한 호출**로 수행합니다. 단, 자율성은 "내용"에만 있고 "권한"은 오늘과 동일 — BW 호출·SQL 실행·쿼리 실행·데이터 행 노출·쓰기/활성화/이송은 전부 금지입니다.

핵심 안전장치:
- **단계 고정 파이프라인** (계획→정책게이트→파싱 전용 보강→가설/리스크→비평→종합→검증). 자유로운 에이전트 루프 없음.
- `impact.py`가 severity/confidence/영향객체의 **유일 권위**. LLM 의견은 `LLM 제안 우려(llm_proposed_concern)`로 별도 라벨, 결코 결정론적 값을 덮어쓰지 못함.
- 모든 출력 줄은 **citation 강제**, `manual_verification`/`UNKNOWN`은 절대 누락 금지, 위반 시 **fail-closed → 결정론 결과로 폴백**.
- LLM 기본 비활성, 로컬 엔드포인트 전용, 비밀값 미저장.
- 예산: 최대 LLM 호출 6회, 리뷰 라운드 2, 카드 12개, 지연 60초.

UI는 채팅이 아니라 **구조화된 Agentic Review Workspace** (목표 / 추론 트레이스 요약 / 우선순위 카드 / 증거 맵 / 공백 / 수동 점검 체크리스트 / CAB 요약 / 검증·예산·감사). 기존 통합 Impact 뷰 안에 추가되며 현재 E2E·테스트를 회귀시키지 않습니다.

구현은 A0~A10 작은 PR로 순차 진행하고, 슬라이스마다 `uv run pytest -q` / `ruff` / `mypy` / `test:slice-g` / `build` / `git diff --check` 게이트를 통과시킵니다.

---

**Note on this run:** the `Write` tool is disabled in this planning context (consistent with "do not edit repository files"), so I produced the plan as output above rather than writing it to disk. To persist it, save the content to `docs/plans/2026-06-23-opus48-agentic-impact-review-assistant-plan.md`. Want me to also draft the A0 model stubs or the `PolicyGate` test matrix as a follow-up once you approve a slice?
