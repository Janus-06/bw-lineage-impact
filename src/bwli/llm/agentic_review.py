from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from typing import Literal, TypeAlias, get_args

from pydantic import BaseModel, ConfigDict, Field

from bwli.config import LlmRuntimeConfig
from bwli.impact import ImpactFinding, ImpactSeverity
from bwli.impact_evidence import ImpactEvidencePack
from bwli.llm.explainer import (
    LlmCitationError,
    LlmEvidenceError,
    _line_has_citation,
    _validate_completion_safety,
)
from bwli.llm.openai_compatible import (
    ChatMessage,
    LlmAuditMetadata,
    LlmChatRequest,
    OpenAICompatibleClient,
    TransportLike,
)
from bwli.llm.sanitizer import REDACTED, sanitize_llm_evidence, sanitize_text

EnricherName: TypeAlias = Literal[
    "reparse_query_xml",
    "reparse_native_sql_view",
    "lookup_request_freshness",
    "recompute_impact_pack",
]
ReviewCardKind: TypeAlias = Literal[
    "deterministic_finding",
    "llm_proposed_concern",
    "manual_verification_required",
]
ReviewRunStatus: TypeAlias = Literal["completed", "disabled", "fallback", "failed"]
HypothesisStatus: TypeAlias = Literal["proposed", "supported", "refuted"]
ManualCheckTool: TypeAlias = Literal["BWMT", "Eclipse", "HANA_Studio", "manual"]
CitationValidationStatus: TypeAlias = Literal["not_validated", "passed", "failed"]

_ALLOWED_ENRICHERS = frozenset(str(item) for item in get_args(EnricherName))
_MAX_AGENTIC_EVIDENCE_ITEMS = 80
_MAX_AGENTIC_EVIDENCE_TEXT_CHARS = 600


class ReviewObjective(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    citation_ids: list[str] = Field(default_factory=list)


class EvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    enricher: EnricherName
    target: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    citation_hint: str | None = None


class EvidenceRequestDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    allowed: bool
    reason: str = Field(min_length=1)


class AgenticReviewPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objectives: list[ReviewObjective] = Field(default_factory=list)
    evidence_requests: list[EvidenceRequest] = Field(default_factory=list)
    notes: str = ""


class ReviewHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    status: HypothesisStatus
    severity_opinion: ImpactSeverity | None = None
    supports_finding_ids: list[str] = Field(default_factory=list)
    confidence_rationale: str = Field(min_length=1)
    citation_ids: list[str] = Field(default_factory=list)


class EvidenceGap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    missing_evidence: str = Field(min_length=1)
    suggested_local_action: EnricherName | None = None
    related_object_id: str | None = None
    citation_ids: list[str] = Field(default_factory=list)


class ManualCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    tool: ManualCheckTool
    steps_summary: str = Field(min_length=1)
    priority: ImpactSeverity
    related_finding_ids: list[str] = Field(default_factory=list)
    citation_ids: list[str] = Field(default_factory=list)


class CriticDefect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    category: Literal[
        "citation",
        "safety",
        "unsupported_claim",
        "severity_override",
        "gap_omission",
    ]
    description: str = Field(min_length=1)
    citation_ids: list[str] = Field(default_factory=list)


class AgenticReviewCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kind: ReviewCardKind
    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    severity_label: ImpactSeverity | None = None
    review_priority: int = Field(ge=1)
    source_finding_id: str | None = None
    citation_ids: list[str] = Field(default_factory=list)


class ReviewTraceStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str = Field(min_length=1)
    round: int = Field(ge=0)
    summary: str = Field(min_length=1)
    llm_audit: LlmAuditMetadata | None = None
    citation_validation: CitationValidationStatus = "not_validated"


class AgenticReviewBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_planner_rounds: int = Field(default=1, ge=0, le=2)
    max_evidence_requests: int = Field(default=6, ge=0, le=10)
    max_review_rounds: int = Field(default=2, ge=0, le=3)
    max_llm_calls: int = Field(default=6, ge=0, le=8)
    max_cards: int = Field(default=12, ge=0, le=20)
    max_latency_ms: int = Field(default=60_000, ge=0, le=120_000)


class AgenticReviewBudgetUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    planner_rounds: int = Field(default=0, ge=0)
    evidence_requests: int = Field(default=0, ge=0)
    enrichers_executed: int = Field(default=0, ge=0)
    review_rounds: int = Field(default=0, ge=0)
    llm_calls: int = Field(default=0, ge=0)
    cards: int = Field(default=0, ge=0)
    elapsed_ms: int = Field(default=0, ge=0)


class AgenticReviewRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    snapshot_id: str | None = None
    llm_enabled: bool
    status: ReviewRunStatus
    objective_question: str | None = None
    objectives: list[ReviewObjective] = Field(default_factory=list)
    hypotheses: list[ReviewHypothesis] = Field(default_factory=list)
    evidence_gaps: list[EvidenceGap] = Field(default_factory=list)
    manual_checks: list[ManualCheck] = Field(default_factory=list)
    cards: list[AgenticReviewCard] = Field(default_factory=list)
    cab_summary: str = ""
    deterministic_pack: ImpactEvidencePack
    trace: list[ReviewTraceStep] = Field(default_factory=list)
    budget: AgenticReviewBudget = Field(default_factory=AgenticReviewBudget)
    budget_usage: AgenticReviewBudgetUsage = Field(default_factory=AgenticReviewBudgetUsage)
    policy_decisions: list[EvidenceRequestDecision] = Field(default_factory=list)
    audit_trail: list[LlmAuditMetadata] = Field(default_factory=list)
    llm_disabled: bool = False


class HypothesisReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypotheses: list[ReviewHypothesis] = Field(default_factory=list)
    evidence_gaps: list[EvidenceGap] = Field(default_factory=list)
    manual_checks: list[ManualCheck] = Field(default_factory=list)
    critic_defects: list[CriticDefect] = Field(default_factory=list)
    review_rounds: int = Field(ge=0)
    llm_calls: int = Field(ge=0)
    audit_trail: list[LlmAuditMetadata] = Field(default_factory=list)
    trace: list[ReviewTraceStep] = Field(default_factory=list)
    status: Literal["completed", "fallback"]
    fallback_reason: str | None = None


class AgenticSynthesisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cards: list[AgenticReviewCard] = Field(default_factory=list)
    cab_summary: str = ""
    llm_calls: int = Field(ge=0)
    audit_trail: list[LlmAuditMetadata] = Field(default_factory=list)
    trace: list[ReviewTraceStep] = Field(default_factory=list)
    status: Literal["completed", "fallback"]
    fallback_reason: str | None = None


class _HypothesisResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypotheses: list[ReviewHypothesis] = Field(default_factory=list)
    evidence_gaps: list[EvidenceGap] = Field(default_factory=list)
    manual_checks: list[ManualCheck] = Field(default_factory=list)


class _CriticResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    defects: list[CriticDefect] = Field(default_factory=list)


class _SynthesisResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cards: list[AgenticReviewCard]
    cab_summary: str


def derive_agentic_citation_ids(pack: ImpactEvidencePack) -> list[str]:
    """Derive deterministic, prompt-safe citation ids from an impact evidence pack."""

    citation_ids: list[str] = []
    _append_unique(citation_ids, _safe_citation_id("scenario:change"))
    for index, finding in enumerate(pack.impact.findings, start=1):
        _append_unique(citation_ids, _safe_citation_id(f"affected:{index}"))
        _append_unique(citation_ids, _safe_citation_id(f"affected:{finding.impacted_object_id}"))
    for query_item in pack.query_evidence:
        _append_unique(citation_ids, _safe_citation_id(f"query:{query_item.query_id}"))
    for sql_item in pack.sql_evidence:
        for edge_id in sql_item.reference_edge_ids:
            _append_unique(citation_ids, _safe_citation_id(edge_id))
    for gap in pack.manual_verification_gaps:
        _append_unique(citation_ids, _safe_citation_id(f"gap:{gap.id}"))
    for freshness_item in pack.freshness_evidence:
        if freshness_item.evidence_available:
            _append_unique(citation_ids, _safe_citation_id(f"freshness:{freshness_item.object_id}"))
    return citation_ids


def build_review_plan_request(
    pack: ImpactEvidencePack,
    *,
    question: str | None = None,
) -> LlmChatRequest:
    """Build a bounded strict-JSON review-planner request from deterministic evidence."""

    citation_ids = derive_agentic_citation_ids(pack)
    evidence = _review_plan_evidence(pack)
    sanitized = sanitize_llm_evidence(evidence)
    bounded = _truncate_evidence_text(sanitized.data)
    evidence_json = json.dumps(bounded, ensure_ascii=False, indent=2, sort_keys=True)
    citation_text = ", ".join(f"[{citation_id}]" for citation_id in citation_ids) or "none"
    question_text = sanitize_text(question.strip()) if question and question.strip() else "none"
    return LlmChatRequest(
        messages=[
            ChatMessage(
                role="system",
                content=(
                    "You are an SAP BW read-only impact-review planner. Use only the "
                    "provided sanitized deterministic evidence. Return strict JSON only. "
                    "Do not invent BW objects, credentials, owners, transports, activations, "
                    "writes, execution results, data previews, SQL execution, or external lookups. "
                    "Evidence requests are proposals only and must be parse-only, in-scope, and "
                    "chosen from the allowed enricher list."
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    "Boundary: planner output only; no BW call, BW write, activation, transport, "
                    "SQL execution, query execution, data preview, or external lookup.\n"
                    f"Allowed citation IDs: {citation_text}\n"
                    f"Allowed enrichers: {', '.join(sorted(_ALLOWED_ENRICHERS))}\n"
                    f"Objective question: {question_text}\n"
                    "Return JSON object shape exactly: {\"objectives\": "
                    "[{\"id\": string, \"title\": string, \"rationale\": string, "
                    "\"citation_ids\": string[]}], \"evidence_requests\": "
                    "[{\"id\": string, \"enricher\": string, \"target\": string, "
                    "\"reason\": string, \"citation_hint\": string|null}], \"notes\": string}. "
                    "Use exact allowed citation IDs in citation_ids and citation_hint. If evidence "
                    "is insufficient, return empty arrays and explain the gap in notes.\n"
                    "Sanitized bounded ImpactEvidencePack JSON:\n"
                    f"{evidence_json}"
                ),
            ),
        ],
        citation_ids=citation_ids,
        metadata={"task": "agentic_review_plan"},
    )


def parse_review_plan_content(
    content: str,
    *,
    allowed_citation_ids: list[str],
    allowed_enrichers: set[str] | frozenset[str],
) -> AgenticReviewPlan:
    """Parse a strict planner JSON response and fail closed on unsafe references."""

    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("LLM agentic review plan response must be a JSON object")
    plan = AgenticReviewPlan.model_validate(payload)
    _validate_plan_citations(plan, allowed_citation_ids)
    _validate_plan_enrichers(plan, allowed_enrichers)
    return plan


def create_agentic_review_plan(
    pack: ImpactEvidencePack,
    *,
    runtime: LlmRuntimeConfig | None,
    question: str | None = None,
    transport: TransportLike | None = None,
) -> AgenticReviewPlan:
    """Create the bounded planner step; disabled runtime never performs network I/O."""

    if runtime is None:
        return AgenticReviewPlan(
            objectives=[],
            evidence_requests=[],
            notes="LLM disabled; returning an empty deterministic agentic review plan.",
        )
    request = build_review_plan_request(pack, question=question)
    completion = OpenAICompatibleClient(runtime=runtime, transport=transport).chat(request)
    _validate_completion_safety(completion)
    return parse_review_plan_content(
        completion.content,
        allowed_citation_ids=request.citation_ids,
        allowed_enrichers=_ALLOWED_ENRICHERS,
    )


def build_hypothesis_request(
    pack: ImpactEvidencePack,
    plan: AgenticReviewPlan,
    *,
    question: str | None = None,
    defects: Sequence[CriticDefect] = (),
) -> LlmChatRequest:
    """Build a strict-JSON read-only hypothesis reviewer request."""

    citation_ids = derive_agentic_citation_ids(pack)
    prompt_payload = {
        "impact_evidence_pack": _review_plan_evidence(pack),
        "review_plan": plan.model_dump(mode="json"),
        "critic_defects_to_revise": [
            defect.model_dump(mode="json") for defect in defects
        ],
    }
    evidence_json = _bounded_sanitized_json(prompt_payload)
    citation_text = _citation_prompt_text(citation_ids)
    question_text = sanitize_text(question.strip()) if question and question.strip() else "none"
    defect_instruction = (
        "Revise the prior hypothesis review to address every listed critic defect. "
        "If a defect cannot be fixed from deterministic evidence, add a cited evidence gap "
        "or manual check instead of inventing facts."
        if defects
        else "No prior critic defects; produce the first hypothesis review."
    )
    return LlmChatRequest(
        messages=[
            ChatMessage(
                role="system",
                content=(
                    "You are an SAP BW read-only hypothesis/risk reviewer. Use only the "
                    "provided sanitized deterministic evidence and exact allowed citation IDs. "
                    "Return strict JSON only. Do not invent BW objects, credentials, owners, "
                    "transports, activations, writes, execution results, data previews, SQL "
                    "execution, query execution, external lookups, or live BW calls. Severity "
                    "opinions are advisory only and must not rewrite impact.py."
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    "Boundary: no BW call, BW write, activation, transport, SQL execution, "
                    "query execution, data preview, or external lookup. Use citations only; "
                    "every citation in text or citation_ids must be one exact allowed ID. "
                    "Use deterministic evidence only. Severity opinions are advisory only and "
                    "must not rewrite impact.py.\n"
                    f"Allowed citation IDs: {citation_text}\n"
                    f"Allowed severity values: {_impact_severity_values_text()} or null\n"
                    "Allowed hypothesis status values: proposed, supported, refuted\n"
                    "Allowed suggested_local_action values: "
                    f"{', '.join(sorted(_ALLOWED_ENRICHERS))} "
                    "or null\n"
                    "Allowed manual check tools: BWMT, Eclipse, HANA_Studio, manual\n"
                    f"Objective question: {question_text}\n"
                    f"Revision instruction: {defect_instruction}\n"
                    "Return JSON object shape exactly: {\"hypotheses\": "
                    "[{\"id\": string, \"statement\": string, \"status\": string, "
                    "\"severity_opinion\": string|null, \"supports_finding_ids\": string[], "
                    "\"confidence_rationale\": string, \"citation_ids\": string[]}], "
                    "\"evidence_gaps\": [{\"id\": string, \"description\": string, "
                    "\"missing_evidence\": string, \"suggested_local_action\": string|null, "
                    "\"related_object_id\": string|null, \"citation_ids\": string[]}], "
                    "\"manual_checks\": [{\"id\": string, \"title\": string, "
                    "\"tool\": string, \"steps_summary\": string, \"priority\": string, "
                    "\"related_finding_ids\": string[], \"citation_ids\": string[]}]}. "
                    "If evidence is insufficient, return no unsupported hypothesis and add a "
                    "cited evidence gap or manual check instead.\n"
                    "Sanitized bounded review evidence JSON:\n"
                    f"{evidence_json}"
                ),
            ),
        ],
        citation_ids=citation_ids,
        metadata={"task": "agentic_hypothesis_review"},
    )


def parse_hypothesis_content(
    content: str,
    *,
    allowed_citation_ids: Sequence[str],
) -> tuple[list[ReviewHypothesis], list[EvidenceGap], list[ManualCheck]]:
    """Parse strict reviewer JSON and fail closed on unsafe citations."""

    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("LLM hypothesis review response must be a JSON object")
    response = _HypothesisResponse.model_validate(payload)
    _validate_hypothesis_response_citations(
        response.hypotheses,
        response.evidence_gaps,
        response.manual_checks,
        allowed_citation_ids,
    )
    return response.hypotheses, response.evidence_gaps, response.manual_checks


def build_critic_request(
    pack: ImpactEvidencePack,
    plan: AgenticReviewPlan,
    hypotheses: Sequence[ReviewHypothesis],
    evidence_gaps: Sequence[EvidenceGap],
    manual_checks: Sequence[ManualCheck],
) -> LlmChatRequest:
    """Build a strict-JSON critic request for the current hypothesis review."""

    citation_ids = derive_agentic_citation_ids(pack)
    prompt_payload = {
        "impact_evidence_pack": _review_plan_evidence(pack),
        "review_plan": plan.model_dump(mode="json"),
        "hypothesis_review": {
            "hypotheses": [
                hypothesis.model_dump(mode="json") for hypothesis in hypotheses
            ],
            "evidence_gaps": [gap.model_dump(mode="json") for gap in evidence_gaps],
            "manual_checks": [check.model_dump(mode="json") for check in manual_checks],
        },
    }
    evidence_json = _bounded_sanitized_json(prompt_payload)
    citation_text = _citation_prompt_text(citation_ids)
    return LlmChatRequest(
        messages=[
            ChatMessage(
                role="system",
                content=(
                    "You are an SAP BW read-only critic for a hypothesis/risk review. Use only "
                    "provided sanitized deterministic evidence and exact allowed citation IDs. "
                    "Return strict JSON only. Do not invent BW objects, credentials, owners, "
                    "transports, activations, writes, execution results, data previews, SQL "
                    "execution, query execution, external lookups, or live BW calls. Severity "
                    "opinions are advisory only and must not rewrite impact.py."
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    "Boundary: no BW call, BW write, activation, transport, SQL execution, "
                    "query execution, data preview, or external lookup. Use citations only; "
                    "every citation in text or citation_ids must be one exact allowed ID. "
                    "Use deterministic evidence only. Severity opinions are advisory only and "
                    "must not rewrite impact.py.\n"
                    f"Allowed citation IDs: {citation_text}\n"
                    "Critique for these defect categories only: citation, safety, "
                    "unsupported_claim, severity_override, gap_omission. Report defects for "
                    "fabricated/absent citations, unsafe or mutating suggestions, claims not "
                    "supported by deterministic evidence, any attempt to override impact.py "
                    "severity/authority, or omitted evidence gaps/manual checks. If clean, "
                    "return {\"defects\": []}.\n"
                    "Return JSON object shape exactly: {\"defects\": "
                    "[{\"id\": string, \"category\": string, \"description\": string, "
                    "\"citation_ids\": string[]}]}.\n"
                    "Sanitized bounded critic evidence JSON:\n"
                    f"{evidence_json}"
                ),
            ),
        ],
        citation_ids=citation_ids,
        metadata={"task": "agentic_hypothesis_critic"},
    )


def parse_critic_content(
    content: str,
    *,
    allowed_citation_ids: Sequence[str],
) -> list[CriticDefect]:
    """Parse strict critic JSON and fail closed on unsafe citations."""

    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("LLM critic response must be a JSON object")
    response = _CriticResponse.model_validate(payload)
    _validate_critic_citations(response.defects, allowed_citation_ids)
    return response.defects


def run_hypothesis_review(
    pack: ImpactEvidencePack,
    plan: AgenticReviewPlan,
    *,
    runtime: LlmRuntimeConfig | None,
    budget: AgenticReviewBudget | None = None,
    question: str | None = None,
    transport: TransportLike | None = None,
) -> HypothesisReviewResult:
    """Run bounded reviewer + critic rounds, failing closed on validation defects."""

    active_budget = budget or AgenticReviewBudget()
    hypotheses: list[ReviewHypothesis] = []
    evidence_gaps: list[EvidenceGap] = []
    manual_checks: list[ManualCheck] = []
    critic_defects: list[CriticDefect] = []
    audit_trail: list[LlmAuditMetadata] = []
    trace: list[ReviewTraceStep] = []
    review_rounds = 0
    llm_calls = 0

    def fallback(reason: str) -> HypothesisReviewResult:
        return HypothesisReviewResult(
            hypotheses=hypotheses,
            evidence_gaps=evidence_gaps,
            manual_checks=manual_checks,
            critic_defects=critic_defects,
            review_rounds=review_rounds,
            llm_calls=llm_calls,
            audit_trail=audit_trail,
            trace=trace,
            status="fallback",
            fallback_reason=reason,
        )

    if runtime is None:
        trace.append(
            ReviewTraceStep(
                stage="hypothesis_review_disabled",
                round=0,
                summary="LLM disabled; hypothesis review skipped before any transport call.",
            )
        )
        return fallback("LLM disabled; hypothesis review requires an explicit runtime.")

    if active_budget.max_review_rounds <= 0:
        trace.append(
            ReviewTraceStep(
                stage="review_budget",
                round=0,
                summary="Review budget exhausted before first hypothesis reviewer round.",
            )
        )
        return fallback("Review budget exhausted before hypothesis review could start.")

    client = OpenAICompatibleClient(runtime=runtime, transport=transport)
    allowed_citation_ids = derive_agentic_citation_ids(pack)

    for round_number in range(1, active_budget.max_review_rounds + 1):
        if llm_calls >= active_budget.max_llm_calls:
            trace.append(
                ReviewTraceStep(
                    stage="llm_budget",
                    round=round_number,
                    summary="LLM call budget exhausted before reviewer call.",
                )
            )
            return fallback("LLM call budget exhausted before required reviewer call.")

        reviewer_request = build_hypothesis_request(
            pack,
            plan,
            question=question,
            defects=critic_defects,
        )
        reviewer_completion = None
        try:
            reviewer_completion = client.chat(reviewer_request)
            llm_calls += 1
            review_rounds = round_number
            _validate_completion_safety(reviewer_completion)
            hypotheses, evidence_gaps, manual_checks = parse_hypothesis_content(
                reviewer_completion.content,
                allowed_citation_ids=allowed_citation_ids,
            )
        except Exception as exc:
            if reviewer_completion is not None:
                audit_trail.append(reviewer_completion.audit)
                trace.append(
                    ReviewTraceStep(
                        stage="hypothesis_reviewer",
                        round=round_number,
                        summary="Reviewer completion failed safety or citation validation.",
                        llm_audit=reviewer_completion.audit,
                        citation_validation="failed",
                    )
                )
            return fallback(_fallback_reason("Hypothesis reviewer validation failed", exc))

        reviewer_audit = reviewer_completion.audit.model_copy(
            update={"citation_validation": "passed"}
        )
        audit_trail.append(reviewer_audit)
        trace.append(
            ReviewTraceStep(
                stage="hypothesis_reviewer",
                round=round_number,
                summary=(
                    f"Reviewer produced {len(hypotheses)} hypotheses, "
                    f"{len(evidence_gaps)} evidence gaps, and {len(manual_checks)} manual checks."
                ),
                llm_audit=reviewer_audit,
                citation_validation="passed",
            )
        )

        if llm_calls >= active_budget.max_llm_calls:
            trace.append(
                ReviewTraceStep(
                    stage="llm_budget",
                    round=round_number,
                    summary="LLM call budget exhausted before critic call.",
                )
            )
            return fallback("LLM call budget exhausted before required critic call.")

        critic_request = build_critic_request(
            pack,
            plan,
            hypotheses,
            evidence_gaps,
            manual_checks,
        )
        critic_completion = None
        try:
            critic_completion = client.chat(critic_request)
            llm_calls += 1
            _validate_completion_safety(critic_completion)
            critic_defects = parse_critic_content(
                critic_completion.content,
                allowed_citation_ids=allowed_citation_ids,
            )
        except Exception as exc:
            if critic_completion is not None:
                audit_trail.append(critic_completion.audit)
                trace.append(
                    ReviewTraceStep(
                        stage="hypothesis_critic",
                        round=round_number,
                        summary="Critic completion failed safety or citation validation.",
                        llm_audit=critic_completion.audit,
                        citation_validation="failed",
                    )
                )
            return fallback(_fallback_reason("Hypothesis critic validation failed", exc))

        critic_audit = critic_completion.audit.model_copy(update={"citation_validation": "passed"})
        audit_trail.append(critic_audit)
        trace.append(
            ReviewTraceStep(
                stage="hypothesis_critic",
                round=round_number,
                summary=f"Critic returned {len(critic_defects)} defects.",
                llm_audit=critic_audit,
                citation_validation="passed",
            )
        )

        if not critic_defects:
            return HypothesisReviewResult(
                hypotheses=hypotheses,
                evidence_gaps=evidence_gaps,
                manual_checks=manual_checks,
                critic_defects=[],
                review_rounds=review_rounds,
                llm_calls=llm_calls,
                audit_trail=audit_trail,
                trace=trace,
                status="completed",
                fallback_reason=None,
            )

    return fallback("Critic defects remain after review budget exhausted.")


def build_synthesis_request(
    pack: ImpactEvidencePack,
    plan: AgenticReviewPlan,
    hypotheses: Sequence[ReviewHypothesis],
    evidence_gaps: Sequence[EvidenceGap],
    manual_checks: Sequence[ManualCheck],
) -> LlmChatRequest:
    """Build a strict-JSON final synthesis request from deterministic/A4 evidence."""

    citation_ids = derive_agentic_citation_ids(pack)
    prompt_payload = {
        "impact_evidence_pack": _review_plan_evidence(pack),
        "review_plan": plan.model_dump(mode="json"),
        "a4_outputs": {
            "hypotheses": [
                hypothesis.model_dump(mode="json") for hypothesis in hypotheses
            ],
            "evidence_gaps": [gap.model_dump(mode="json") for gap in evidence_gaps],
            "manual_checks": [check.model_dump(mode="json") for check in manual_checks],
        },
        "deterministic_review_cards": [
            card.model_dump(mode="json") for card in deterministic_review_cards(pack)
        ],
    }
    evidence_json = _bounded_sanitized_json(prompt_payload)
    citation_text = _citation_prompt_text(citation_ids)
    return LlmChatRequest(
        messages=[
            ChatMessage(
                role="system",
                content=(
                    "You are an SAP BW read-only final review synthesizer. Use only the "
                    "provided sanitized deterministic evidence plus prior agentic review outputs. "
                    "Return strict JSON only. Do not make BW calls, BW writes, activations, "
                    "transports, live lookups, SQL execution, query execution, or data previews. "
                    "Deterministic impact.py remains the authority for impact findings and "
                    "severity. If you add a concern that diverges from deterministic findings, "
                    "it must be kind llm_proposed_concern and must never modify deterministic "
                    "finding severity."
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    "Boundary: no BW calls, BW writes, activation, transport, live lookup, "
                    "SQL execution, query execution, or data preview. Use only citations from "
                    "the exact allowed IDs. Deterministic impact.py remains authority; do not "
                    "change source_finding_id severity. Divergent or advisory LLM concerns must "
                    "use kind=llm_proposed_concern, never deterministic_finding.\n"
                    f"Allowed citation IDs: {citation_text}\n"
                    f"Allowed severity_label values: {_impact_severity_values_text()} or null\n"
                    "Allowed card kind values: deterministic_finding, llm_proposed_concern, "
                    "manual_verification_required\n"
                    "Every card must cite deterministic evidence in citation_ids or text. Every "
                    "non-empty CAB summary line must cite an allowed ID.\n"
                    "Return JSON object shape exactly: {\"cards\": "
                    "[{\"id\": string, \"kind\": string, \"title\": string, \"body\": string, "
                    "\"severity_label\": string|null, \"review_priority\": integer, "
                    "\"source_finding_id\": string|null, \"citation_ids\": string[]}], "
                    "\"cab_summary\": string}. The CAB summary should be newline-separated "
                    "bullet lines, each with an allowed citation. If evidence is insufficient, "
                    "return deterministic cards only and a cited CAB summary line naming the "
                    "gap.\n"
                    "Sanitized bounded synthesis evidence JSON:\n"
                    f"{evidence_json}"
                ),
            ),
        ],
        citation_ids=citation_ids,
        metadata={"task": "agentic_synthesis"},
    )


def parse_synthesis_content(
    content: str,
    *,
    allowed_citation_ids: Sequence[str],
    pack: ImpactEvidencePack,
    max_cards: int,
) -> tuple[list[AgenticReviewCard], str]:
    """Parse strict final synthesis JSON and fail closed on citation/authority defects."""

    if max_cards < 0:
        raise ValueError("max_cards must be non-negative")
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("LLM synthesis response must be a JSON object")
    response = _SynthesisResponse.model_validate(_normalize_synthesis_payload(payload))
    if len(response.cards) > max_cards:
        raise ValueError("LLM synthesis returned more cards than max_cards")
    _validate_synthesis_citations(
        response.cards,
        response.cab_summary,
        allowed_citation_ids,
    )
    _validate_deterministic_card_authority(response.cards, pack)
    return response.cards, response.cab_summary


def deterministic_review_cards(
    pack: ImpactEvidencePack,
    *,
    max_cards: int | None = None,
) -> list[AgenticReviewCard]:
    """Create deterministic cards from impact.py findings without LLM input."""

    if max_cards is not None and max_cards < 0:
        raise ValueError("max_cards must be non-negative")
    cards: list[AgenticReviewCard] = []
    for original_index, finding in _ordered_findings(pack):
        if max_cards is not None and len(cards) >= max_cards:
            break
        citations = _affected_citation_ids(original_index, finding)
        title = (
            f"{finding.severity.value} deterministic finding for "
            f"{sanitize_text(finding.impacted_object_id)} [{citations[0]}]"
        )
        body_parts = [
            (
                f"impact.py reports {sanitize_text(finding.impacted_object_type)} "
                f"{sanitize_text(finding.impacted_object_id)} as {finding.severity.value} "
                f"for change {sanitize_text(finding.change_id)}: "
                f"{sanitize_text(finding.reason)} [{citations[-1]}]."
            ),
            "Deterministic severity remains unchanged; LLM synthesis may only add advisory "
            f"concerns, not override this finding [{citations[0]}].",
        ]
        if finding.manual_verification or finding.severity == ImpactSeverity.UNKNOWN:
            body_parts.append(
                "Manual verification is required before operational action because the "
                "deterministic pack marks this finding as manual or unknown "
                f"[{citations[0]}]."
            )
        cards.append(
            AgenticReviewCard(
                id=f"card-det-{original_index:03d}",
                kind="deterministic_finding",
                title=title,
                body=" ".join(body_parts),
                severity_label=finding.severity,
                review_priority=len(cards) + 1,
                source_finding_id=finding.id,
                citation_ids=citations,
            )
        )
    return cards


def create_agentic_synthesis(
    pack: ImpactEvidencePack,
    plan: AgenticReviewPlan,
    hypotheses: Sequence[ReviewHypothesis],
    evidence_gaps: Sequence[EvidenceGap],
    manual_checks: Sequence[ManualCheck],
    *,
    runtime: LlmRuntimeConfig | None,
    budget: AgenticReviewBudget | None = None,
    transport: TransportLike | None = None,
) -> AgenticSynthesisResult:
    """Create final cards/CAB summary, falling back to deterministic cards fail-closed."""

    active_budget = budget or AgenticReviewBudget()
    deterministic_cards = deterministic_review_cards(pack, max_cards=active_budget.max_cards)
    deterministic_summary = _deterministic_cab_summary(
        pack,
        max_cards=active_budget.max_cards,
    )
    audit_trail: list[LlmAuditMetadata] = []
    trace: list[ReviewTraceStep] = []
    llm_calls = 0

    def fallback(reason: str) -> AgenticSynthesisResult:
        return AgenticSynthesisResult(
            cards=deterministic_cards,
            cab_summary=deterministic_summary,
            llm_calls=llm_calls,
            audit_trail=audit_trail,
            trace=trace,
            status="fallback",
            fallback_reason=reason,
        )

    if runtime is None:
        trace.append(
            ReviewTraceStep(
                stage="synthesis_deterministic",
                round=0,
                summary=(
                    "LLM disabled; returned deterministic review cards and CAB summary "
                    "without any transport call."
                ),
                citation_validation="passed",
            )
        )
        return AgenticSynthesisResult(
            cards=deterministic_cards,
            cab_summary=deterministic_summary,
            llm_calls=0,
            audit_trail=[],
            trace=trace,
            status="completed",
            fallback_reason=None,
        )

    if active_budget.max_llm_calls <= 0:
        trace.append(
            ReviewTraceStep(
                stage="llm_budget",
                round=0,
                summary="LLM call budget exhausted before synthesis.",
            )
        )
        return fallback("LLM call budget exhausted before synthesis.")

    client = OpenAICompatibleClient(runtime=runtime, transport=transport)
    request = build_synthesis_request(
        pack,
        plan,
        hypotheses,
        evidence_gaps,
        manual_checks,
    )
    completion = None
    try:
        completion = client.chat(request)
        llm_calls += 1
        _validate_completion_safety(completion)
        cards, cab_summary = parse_synthesis_content(
            completion.content,
            allowed_citation_ids=request.citation_ids,
            pack=pack,
            max_cards=active_budget.max_cards,
        )
    except Exception as exc:
        if completion is not None:
            audit_trail.append(completion.audit)
            trace.append(
                ReviewTraceStep(
                    stage="synthesis",
                    round=0,
                    summary=(
                        "Synthesis completion failed safety, citation, or "
                        "authority validation."
                    ),
                    llm_audit=completion.audit,
                    citation_validation="failed",
                )
            )
        else:
            trace.append(
                ReviewTraceStep(
                    stage="synthesis",
                    round=0,
                    summary="Synthesis call failed before a completion was available.",
                    citation_validation="failed",
                )
            )
        return fallback(_fallback_reason("Synthesis validation failed", exc))

    synthesis_audit = completion.audit.model_copy(update={"citation_validation": "passed"})
    audit_trail.append(synthesis_audit)
    trace.append(
        ReviewTraceStep(
            stage="synthesis",
            round=0,
            summary=f"Synthesis produced {len(cards)} cards and a CAB summary.",
            llm_audit=synthesis_audit,
            citation_validation="passed",
        )
    )
    return AgenticSynthesisResult(
        cards=cards,
        cab_summary=cab_summary,
        llm_calls=llm_calls,
        audit_trail=audit_trail,
        trace=trace,
        status="completed",
        fallback_reason=None,
    )


def _review_plan_evidence(pack: ImpactEvidencePack) -> dict[str, object]:
    return {
        "schema_version": pack.schema_version,
        "snapshot_id": pack.snapshot_id,
        "authority": {
            "deterministic": pack.deterministic,
            "read_only": pack.read_only,
            "execution_blocked": pack.execution_blocked,
            "final_authority": pack.final_authority,
            "authority_note": pack.authority_note,
            "citation_id": _safe_citation_id("scenario:change"),
        },
        "changes": [
            {
                "id": change.id,
                "object_id": change.object_id,
                "object_type": change.object_type,
                "change_type": change.change_type.value,
                "field": change.field,
                "citation_id": _safe_citation_id("scenario:change"),
            }
            for change in pack.impact.changes
        ],
        "affected_objects": [
            {
                "citation_ids": [
                    _safe_citation_id(f"affected:{index}"),
                    _safe_citation_id(f"affected:{finding.impacted_object_id}"),
                ],
                "finding_id": finding.id,
                "change_id": finding.change_id,
                "object_id": finding.impacted_object_id,
                "object_type": finding.impacted_object_type,
                "severity": finding.severity.value,
                "confidence": finding.confidence,
                "reason": finding.reason,
                "manual_verification": finding.manual_verification,
                "evidence_node_ids": finding.evidence_node_ids,
                "evidence_edge_ids": finding.evidence_edge_ids,
            }
            for index, finding in enumerate(pack.impact.findings, start=1)
        ],
        "query_evidence": [
            {
                "citation_id": _safe_citation_id(f"query:{item.query_id}"),
                "query_id": item.query_id,
                "description": item.description,
                "provider_object_ids": item.provider_object_ids,
                "variable_names": item.variable_names,
                "calculated_key_figure_names": item.calculated_key_figure_names,
                "restricted_key_figure_names": item.restricted_key_figure_names,
                "field_names": item.field_names,
                "filter_count": item.filter_count,
                "layout_fields": item.layout_fields,
                "exposed_object_ids": item.exposed_object_ids,
                "matched_finding_ids": item.matched_finding_ids,
                "manual_check_notes": item.manual_check_notes,
                "metadata": item.metadata,
            }
            for item in pack.query_evidence
        ],
        "sql_evidence": [
            {
                "view_id": item.view_id,
                "parser": item.parser,
                "confidence": item.confidence.value,
                "referenced_object_ids": item.referenced_object_ids,
                "referenced_column_names": item.referenced_column_names,
                "citation_ids": [_safe_citation_id(edge_id) for edge_id in item.reference_edge_ids],
                "fragment_ids": item.fragment_ids,
                "matched_finding_ids": item.matched_finding_ids,
                "manual_check_notes": item.manual_check_notes,
            }
            for item in pack.sql_evidence
        ],
        "freshness_evidence": [
            {
                "citation_id": _safe_citation_id(f"freshness:{item.object_id}"),
                "object_id": item.object_id,
                "object_type": item.object_type,
                "request_count": item.request_count,
                "latest_status": item.latest_status,
                "latest_timestamp": item.latest_timestamp,
                "latest_records": item.latest_records,
                "evidence_available": item.evidence_available,
                "manual_check_notes": item.manual_check_notes,
            }
            for item in pack.freshness_evidence
            if item.evidence_available
        ],
        "manual_verification_gaps": [
            {
                "citation_id": _safe_citation_id(f"gap:{gap.id}"),
                "id": gap.id,
                "source": gap.source,
                "reason": gap.reason,
                "object_id": gap.object_id,
                "object_type": gap.object_type,
                "finding_id": gap.finding_id,
                "evidence_ids": gap.evidence_ids,
            }
            for gap in pack.manual_verification_gaps
        ],
        "coverage_summary": pack.coverage_summary,
    }


def _validate_plan_citations(plan: AgenticReviewPlan, allowed_citation_ids: Sequence[str]) -> None:
    allowed = set(allowed_citation_ids)
    for objective in plan.objectives:
        _validate_citation_list(objective.citation_ids, allowed)
        _validate_optional_bracket_citations(objective.rationale, allowed)
    for request in plan.evidence_requests:
        _validate_optional_bracket_citations(request.reason, allowed)
        if request.citation_hint:
            _validate_citation_hint(request.citation_hint, allowed)
    _validate_optional_bracket_citations(plan.notes, allowed)


def _validate_plan_enrichers(
    plan: AgenticReviewPlan,
    allowed_enrichers: set[str] | frozenset[str],
) -> None:
    for request in plan.evidence_requests:
        if request.enricher not in allowed_enrichers:
            raise ValueError(f"LLM requested an unallowed evidence enricher: {request.enricher}")


def _validate_hypothesis_response_citations(
    hypotheses: Sequence[ReviewHypothesis],
    evidence_gaps: Sequence[EvidenceGap],
    manual_checks: Sequence[ManualCheck],
    allowed_citation_ids: Sequence[str],
) -> None:
    allowed = set(allowed_citation_ids)
    for hypothesis in hypotheses:
        _validate_citation_list(hypothesis.citation_ids, allowed)
        _validate_optional_bracket_citations(hypothesis.statement, allowed)
        _validate_optional_bracket_citations(hypothesis.confidence_rationale, allowed)
    for gap in evidence_gaps:
        _validate_citation_list(gap.citation_ids, allowed)
        _validate_optional_bracket_citations(gap.description, allowed)
        _validate_optional_bracket_citations(gap.missing_evidence, allowed)
    for check in manual_checks:
        _validate_citation_list(check.citation_ids, allowed)
        _validate_optional_bracket_citations(check.title, allowed)
        _validate_optional_bracket_citations(check.steps_summary, allowed)


def _validate_critic_citations(
    defects: Sequence[CriticDefect],
    allowed_citation_ids: Sequence[str],
) -> None:
    allowed = set(allowed_citation_ids)
    for defect in defects:
        _validate_citation_list(defect.citation_ids, allowed)
        _validate_optional_bracket_citations(defect.description, allowed)


def _validate_synthesis_citations(
    cards: Sequence[AgenticReviewCard],
    cab_summary: str,
    allowed_citation_ids: Sequence[str],
) -> None:
    allowed = set(allowed_citation_ids)
    for card in cards:
        _validate_citation_list(card.citation_ids, allowed)
        _validate_optional_bracket_citations(card.title, allowed)
        _validate_optional_bracket_citations(card.body, allowed)
        if not card.citation_ids and not (
            _text_has_allowed_citation(card.title, allowed)
            or _text_has_allowed_citation(card.body, allowed)
        ):
            raise LlmCitationError("LLM synthesis card did not cite deterministic evidence")
    allowed_list = list(allowed)
    for line in cab_summary.splitlines():
        if line.strip() and not _line_has_citation(line, allowed_list):
            raise LlmCitationError("LLM CAB summary line did not cite deterministic evidence")


def _validate_deterministic_card_authority(
    cards: Sequence[AgenticReviewCard],
    pack: ImpactEvidencePack,
) -> None:
    findings_by_id = {finding.id: finding for finding in pack.impact.findings}
    for card in cards:
        if card.source_finding_id is not None and card.source_finding_id not in findings_by_id:
            raise LlmEvidenceError("LLM synthesis referenced an unknown source finding")
        if card.kind != "deterministic_finding":
            continue
        if card.source_finding_id is None:
            raise LlmEvidenceError("LLM deterministic synthesis card omitted source_finding_id")
        finding = findings_by_id[card.source_finding_id]
        if card.severity_label != finding.severity:
            raise LlmEvidenceError("LLM synthesis attempted to override deterministic severity")


def _validate_citation_list(citation_ids: Iterable[str], allowed: set[str]) -> None:
    fabricated = [citation_id for citation_id in citation_ids if citation_id not in allowed]
    if fabricated:
        raise LlmCitationError("LLM agentic review cited unknown deterministic evidence IDs")


def _validate_citation_hint(citation_hint: str, allowed: set[str]) -> None:
    hint = citation_hint.strip()
    if not hint:
        return
    if hint in allowed:
        return
    if "[" in hint or "]" in hint:
        _validate_optional_bracket_citations(hint, allowed)
        return
    raise LlmCitationError("LLM agentic review plan used an unknown citation hint")


def _validate_optional_bracket_citations(text: str, allowed: set[str]) -> None:
    for line in text.splitlines():
        if not line.strip() or ("[" not in line and "]" not in line):
            continue
        if not _line_has_citation(line, list(allowed)):
            raise LlmCitationError(
                "LLM agentic review cited unknown deterministic evidence IDs"
            )


def _text_has_allowed_citation(text: str, allowed: set[str]) -> bool:
    allowed_list = list(allowed)
    return any(
        bool(line.strip()) and _line_has_citation(line, allowed_list)
        for line in text.splitlines()
    )


def _normalize_synthesis_payload(payload: dict[str, object]) -> dict[str, object]:
    cards = payload.get("cards")
    if not isinstance(cards, list):
        return payload
    normalized_cards: list[object] = []
    for card in cards:
        if not isinstance(card, dict):
            normalized_cards.append(card)
            continue
        normalized_card = dict(card)
        severity_label = normalized_card.get("severity_label")
        if isinstance(severity_label, str):
            normalized_card["severity_label"] = severity_label.upper()
        normalized_cards.append(normalized_card)
    normalized_payload = dict(payload)
    normalized_payload["cards"] = normalized_cards
    return normalized_payload


def _deterministic_cab_summary(
    pack: ImpactEvidencePack,
    *,
    max_cards: int | None,
) -> str:
    if max_cards is not None and max_cards < 0:
        raise ValueError("max_cards must be non-negative")
    ordered_findings = _ordered_findings(pack)
    if not ordered_findings:
        return "- No deterministic impact findings were produced by impact.py [scenario:change]."
    displayed_findings = (
        ordered_findings if max_cards is None else ordered_findings[:max_cards]
    )
    if not displayed_findings:
        return (
            "- Deterministic impact findings exist, but max_cards is 0 so no cards are "
            "displayed [scenario:change]."
        )
    lines: list[str] = []
    for original_index, finding in displayed_findings:
        citation_id = _affected_citation_ids(original_index, finding)[0]
        manual_note = (
            " Manual verification is required before operational action."
            if finding.manual_verification or finding.severity == ImpactSeverity.UNKNOWN
            else ""
        )
        lines.append(
            f"- {finding.severity.value} impact: "
            f"{sanitize_text(finding.impacted_object_type)} "
            f"{sanitize_text(finding.impacted_object_id)} remains in impact.py scope."
            f"{manual_note} [{citation_id}]"
        )
    omitted_count = len(ordered_findings) - len(displayed_findings)
    if omitted_count > 0:
        lines.append(
            f"- {omitted_count} additional deterministic finding(s) omitted by max_cards "
            "[scenario:change]."
        )
    return "\n".join(lines)


def _ordered_findings(pack: ImpactEvidencePack) -> list[tuple[int, ImpactFinding]]:
    return sorted(
        enumerate(pack.impact.findings, start=1),
        key=lambda item: (_severity_rank(item[1].severity), item[0]),
    )


def _severity_rank(severity: ImpactSeverity) -> int:
    return {
        ImpactSeverity.HIGH: 0,
        ImpactSeverity.MEDIUM: 1,
        ImpactSeverity.LOW: 2,
        ImpactSeverity.UNKNOWN: 3,
    }[severity]


def _affected_citation_ids(original_index: int, finding: ImpactFinding) -> list[str]:
    citations: list[str] = []
    _append_unique(citations, _safe_citation_id(f"affected:{original_index}"))
    _append_unique(citations, _safe_citation_id(f"affected:{finding.impacted_object_id}"))
    return citations


def _bounded_sanitized_json(payload: object) -> str:
    sanitized = sanitize_llm_evidence(payload)
    bounded = _truncate_evidence_text(sanitized.data)
    return json.dumps(bounded, ensure_ascii=False, indent=2, sort_keys=True)


def _citation_prompt_text(citation_ids: Sequence[str]) -> str:
    return ", ".join(f"[{citation_id}]" for citation_id in citation_ids) or "none"


def _impact_severity_values_text() -> str:
    return ", ".join(severity.value for severity in ImpactSeverity)


def _fallback_reason(prefix: str, exc: Exception) -> str:
    message = sanitize_text(str(exc)).strip()
    return f"{prefix}: {message}" if message else prefix


def _safe_citation_id(citation_id: str) -> str:
    sanitized = sanitize_text(citation_id)
    cleaned = sanitized.replace(REDACTED, "REDACTED").replace("[", "").replace("]", "")
    cleaned = cleaned.strip()
    if cleaned and cleaned == citation_id:
        return cleaned
    digest = hashlib.sha256(citation_id.encode("utf-8")).hexdigest()[:8]
    prefix = cleaned or "citation"
    return f"{prefix}:h{digest}"


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _truncate_evidence_text(value: object) -> object:
    if isinstance(value, str):
        if len(value) <= _MAX_AGENTIC_EVIDENCE_TEXT_CHARS:
            return value
        return f"{value[:_MAX_AGENTIC_EVIDENCE_TEXT_CHARS]}…(truncated)"
    if isinstance(value, dict):
        return {key: _truncate_evidence_text(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        items = list(value[:_MAX_AGENTIC_EVIDENCE_ITEMS])
        bounded = [_truncate_evidence_text(item) for item in items]
        if len(value) > len(items):
            bounded.append(
                {
                    "truncated": True,
                    "included": len(items),
                    "total": len(value),
                }
            )
        return bounded
    return value
