from __future__ import annotations

from collections.abc import Iterable, Sequence

from bwli.impact import ImpactFinding, ImpactSeverity
from bwli.impact_evidence import ImpactEvidencePack
from bwli.llm.agentic_review import (
    AgenticReviewRun,
    EvidenceGap,
    ManualCheck,
    ReviewHypothesis,
    derive_agentic_citation_ids,
)
from bwli.llm.explainer import (
    LlmCitationError,
    LlmEvidenceError,
    _line_has_citation,
    _validate_completion_safety,
)
from bwli.llm.openai_compatible import LlmAuditMetadata, LlmCompletion

_SYNTHETIC_AUDIT = LlmAuditMetadata(
    model="agentic-validator",
    prompt_sha256="0" * 64,
    sanitized_input_sha256="0" * 64,
    request_citation_ids=[],
    response_timestamp="1970-01-01T00:00:00Z",
)


def validate_agentic_run(run: AgenticReviewRun, *, pack: ImpactEvidencePack) -> AgenticReviewRun:
    """Validate final agentic review output against deterministic evidence authority."""

    allowed_citation_ids = derive_agentic_citation_ids(pack)
    _validate_budget(run)
    _validate_run_citations(run, allowed_citation_ids)
    _validate_run_safety(run)
    _validate_deterministic_authority(run, pack)
    _validate_gap_preservation(run, pack)
    return _stamp_validated_run(run)


def _validate_budget(run: AgenticReviewRun) -> None:
    if len(run.cards) > run.budget.max_cards:
        raise LlmEvidenceError("Agentic review produced more cards than the configured budget")


def _validate_run_citations(
    run: AgenticReviewRun,
    allowed_citation_ids: Sequence[str],
) -> None:
    allowed = set(allowed_citation_ids)
    for card in run.cards:
        _validate_citation_list(card.citation_ids, allowed)
        _validate_bracketed_lines(card.title, allowed_citation_ids)
        _validate_bracketed_lines(card.body, allowed_citation_ids)
    if run.cab_summary:
        for line in run.cab_summary.splitlines():
            if line.strip() and not _line_has_citation(line, list(allowed_citation_ids)):
                raise LlmCitationError("Agentic review CAB summary cited invalid evidence")
    for hypothesis in run.hypotheses:
        _validate_hypothesis_citations(hypothesis, allowed, allowed_citation_ids)
    for gap in run.evidence_gaps:
        _validate_gap_citations(gap, allowed, allowed_citation_ids)
    for check in run.manual_checks:
        _validate_manual_check_citations(check, allowed, allowed_citation_ids)


def _validate_hypothesis_citations(
    hypothesis: ReviewHypothesis,
    allowed: set[str],
    allowed_citation_ids: Sequence[str],
) -> None:
    _validate_citation_list(hypothesis.citation_ids, allowed)
    _validate_bracketed_lines(hypothesis.statement, allowed_citation_ids)
    _validate_bracketed_lines(hypothesis.confidence_rationale, allowed_citation_ids)


def _validate_gap_citations(
    gap: EvidenceGap,
    allowed: set[str],
    allowed_citation_ids: Sequence[str],
) -> None:
    _validate_citation_list(gap.citation_ids, allowed)
    _validate_bracketed_lines(gap.description, allowed_citation_ids)
    _validate_bracketed_lines(gap.missing_evidence, allowed_citation_ids)


def _validate_manual_check_citations(
    check: ManualCheck,
    allowed: set[str],
    allowed_citation_ids: Sequence[str],
) -> None:
    _validate_citation_list(check.citation_ids, allowed)
    _validate_bracketed_lines(check.title, allowed_citation_ids)
    _validate_bracketed_lines(check.steps_summary, allowed_citation_ids)


def _validate_citation_list(citation_ids: Iterable[str], allowed: set[str]) -> None:
    fabricated = [citation_id for citation_id in citation_ids if citation_id not in allowed]
    if fabricated:
        raise LlmCitationError("Agentic review cited unknown deterministic evidence IDs")


def _validate_bracketed_lines(text: str, allowed_citation_ids: Sequence[str]) -> None:
    allowed_list = list(allowed_citation_ids)
    for line in text.splitlines():
        if line.strip() and ("[" in line or "]" in line) and not _line_has_citation(
            line, allowed_list
        ):
            raise LlmCitationError("Agentic review cited unknown deterministic evidence IDs")


def _validate_run_safety(run: AgenticReviewRun) -> None:
    _validate_text_safety(run.cab_summary)
    for card in run.cards:
        _validate_text_safety(card.title)
        _validate_text_safety(card.body)
    for hypothesis in run.hypotheses:
        _validate_text_safety(hypothesis.statement)
        _validate_text_safety(hypothesis.confidence_rationale)
    for gap in run.evidence_gaps:
        _validate_text_safety(gap.description)
        _validate_text_safety(gap.missing_evidence)
    for check in run.manual_checks:
        _validate_text_safety(check.title)
        _validate_text_safety(check.steps_summary)


def _validate_text_safety(text: str) -> None:
    if not text:
        return
    _validate_completion_safety(LlmCompletion(content=text, audit=_SYNTHETIC_AUDIT))


def _validate_deterministic_authority(
    run: AgenticReviewRun,
    pack: ImpactEvidencePack,
) -> None:
    if run.deterministic_pack.impact.findings != pack.impact.findings:
        raise LlmEvidenceError("Agentic review mutated deterministic impact findings")

    findings_by_id = {finding.id: finding for finding in pack.impact.findings}
    for card in run.cards:
        if card.kind != "deterministic_finding":
            continue
        if card.source_finding_id is None:
            raise LlmEvidenceError("Agentic deterministic card omitted source_finding_id")
        finding = findings_by_id.get(card.source_finding_id)
        if finding is None:
            raise LlmEvidenceError("Agentic deterministic card referenced an unknown finding")
        if card.severity_label != finding.severity:
            raise LlmEvidenceError("Agentic review attempted to override deterministic severity")


def _validate_gap_preservation(run: AgenticReviewRun, pack: ImpactEvidencePack) -> None:
    for finding in pack.impact.findings:
        if finding.manual_verification or finding.severity == ImpactSeverity.UNKNOWN:
            _validate_finding_represented(run, finding)


def _validate_finding_represented(run: AgenticReviewRun, finding: ImpactFinding) -> None:
    if any(card.source_finding_id == finding.id for card in run.cards):
        return
    if any(finding.id in check.related_finding_ids for check in run.manual_checks):
        return
    if any(_gap_matches_finding(gap, finding) for gap in run.evidence_gaps):
        return
    raise LlmEvidenceError(
        "Agentic review dropped a manual-verification or UNKNOWN severity finding"
    )


def _gap_matches_finding(gap: EvidenceGap, finding: ImpactFinding) -> bool:
    return gap.related_object_id in {finding.id, finding.impacted_object_id}


def _stamp_validated_run(run: AgenticReviewRun) -> AgenticReviewRun:
    status = "disabled" if run.status == "disabled" else "completed"
    trace = [step.model_copy(update={"citation_validation": "passed"}) for step in run.trace]
    audit_trail = [
        audit.model_copy(update={"citation_validation": "passed"})
        for audit in run.audit_trail
    ]
    budget_usage = run.budget_usage.model_copy(update={"cards": len(run.cards)})
    return run.model_copy(
        deep=True,
        update={
            "status": status,
            "trace": trace,
            "audit_trail": audit_trail,
            "budget_usage": budget_usage,
        },
    )
