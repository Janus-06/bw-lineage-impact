from __future__ import annotations

from collections.abc import Callable

import pytest

from bwli.impact import ChangeEvent, ChangeType, ImpactFinding, ImpactReport, ImpactSeverity
from bwli.impact_evidence import ImpactEvidencePack, build_impact_evidence_pack
from bwli.llm.agentic_review import (
    AgenticReviewBudget,
    AgenticReviewBudgetUsage,
    AgenticReviewCard,
    AgenticReviewRun,
    EvidenceGap,
    ManualCheck,
    ReviewHypothesis,
    ReviewTraceStep,
)
from bwli.llm.agentic_validator import validate_agentic_run
from bwli.llm.explainer import LlmCitationError, LlmEvidenceError
from bwli.llm.openai_compatible import LlmAuditMetadata


def test_validate_agentic_run_happy_path_stamps_completed_and_passed() -> None:
    pack = _sample_pack()
    run = _valid_run(pack, status="fallback")

    validated = validate_agentic_run(run, pack=pack)

    assert validated is not run
    assert run.status == "fallback"
    assert run.budget_usage.cards == 0
    assert validated.status == "completed"
    assert [step.citation_validation for step in validated.trace] == ["passed"]
    assert [audit.citation_validation for audit in validated.audit_trail] == ["passed"]
    assert validated.budget_usage.cards == len(validated.cards)


def test_validate_agentic_run_preserves_disabled_status() -> None:
    pack = _sample_pack()
    run = _valid_run(pack, status="disabled")

    validated = validate_agentic_run(run, pack=pack)

    assert validated.status == "disabled"
    assert validated.budget_usage.cards == len(validated.cards)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda run: run.model_copy(
            update={
                "cards": [
                    run.cards[0].model_copy(
                        update={
                            "body": "Fabricated card citation [query:not-real].",
                            "citation_ids": ["query:not-real"],
                        }
                    ),
                    *run.cards[1:],
                ]
            }
        ),
        lambda run: run.model_copy(
            update={"cab_summary": "- Fabricated CAB citation [query:not-real]"}
        ),
        lambda run: run.model_copy(
            update={
                "hypotheses": [
                    run.hypotheses[0].model_copy(
                        update={
                            "statement": "Fabricated hypothesis citation [query:not-real].",
                            "citation_ids": ["query:not-real"],
                        }
                    )
                ]
            }
        ),
    ],
)
def test_validate_agentic_run_rejects_fabricated_card_cab_or_hypothesis_citations(
    mutate: Callable[[AgenticReviewRun], AgenticReviewRun],
) -> None:
    pack = _sample_pack()

    with pytest.raises(LlmCitationError):
        validate_agentic_run(mutate(_valid_run(pack)), pack=pack)


def test_validate_agentic_run_rejects_deterministic_severity_override() -> None:
    pack = _sample_pack()
    run = _valid_run(pack)
    mutated = run.model_copy(
        update={
            "cards": [
                run.cards[0].model_copy(update={"severity_label": ImpactSeverity.LOW}),
                *run.cards[1:],
            ]
        }
    )

    with pytest.raises(LlmEvidenceError):
        validate_agentic_run(mutated, pack=pack)


def test_validate_agentic_run_rejects_dropped_manual_verification_finding() -> None:
    pack = _sample_pack()
    run = _valid_run(pack)
    mutated = run.model_copy(
        update={
            "cards": [
                card
                for card in run.cards
                if card.source_finding_id != "finding:chg-manual:ZQ_MANUAL"
            ]
        }
    )

    with pytest.raises(LlmEvidenceError):
        validate_agentic_run(mutated, pack=pack)


def test_validate_agentic_run_rejects_dropped_unknown_severity_finding() -> None:
    pack = _sample_pack()
    run = _valid_run(pack)
    mutated = run.model_copy(
        update={
            "cards": [
                card
                for card in run.cards
                if card.source_finding_id != "finding:chg-unknown:ZQ_UNKNOWN"
            ]
        }
    )

    with pytest.raises(LlmEvidenceError):
        validate_agentic_run(mutated, pack=pack)


def test_validate_agentic_run_rejects_mutated_deterministic_pack_findings() -> None:
    pack = _sample_pack()
    run = _valid_run(pack)
    mutated_finding = pack.impact.findings[0].model_copy(update={"severity": ImpactSeverity.LOW})
    mutated_report = pack.impact.model_copy(
        update={"findings": [mutated_finding, *pack.impact.findings[1:]]}
    )
    mutated_pack = pack.model_copy(update={"impact": mutated_report})
    mutated_run = run.model_copy(update={"deterministic_pack": mutated_pack})

    with pytest.raises(LlmEvidenceError):
        validate_agentic_run(mutated_run, pack=pack)


def test_validate_agentic_run_rejects_too_many_cards() -> None:
    pack = _sample_pack()
    run = _valid_run(pack).model_copy(update={"budget": AgenticReviewBudget(max_cards=2)})

    with pytest.raises(LlmEvidenceError):
        validate_agentic_run(run, pack=pack)


def _valid_run(
    pack: ImpactEvidencePack,
    *,
    status: str = "completed",
) -> AgenticReviewRun:
    return AgenticReviewRun(
        snapshot_id="snap-validator",
        llm_enabled=True,
        status=status,  # type: ignore[arg-type]
        objective_question="Which deterministic impact findings need CAB attention?",
        hypotheses=[
            ReviewHypothesis(
                id="hyp-1",
                statement="High impact query exposure is supported [affected:ZQ_HIGH].",
                status="supported",
                severity_opinion=ImpactSeverity.HIGH,
                supports_finding_ids=["finding:chg-high:ZQ_HIGH"],
                confidence_rationale="The deterministic finding is authoritative [affected:1].",
                citation_ids=["affected:ZQ_HIGH", "affected:1"],
            )
        ],
        evidence_gaps=[
            EvidenceGap(
                id="gap-1",
                description="Check user-facing semantics locally [affected:ZQ_HIGH].",
                missing_evidence="No query execution or data preview is available.",
                suggested_local_action="reparse_query_xml",
                related_object_id="ZQ_HIGH",
                citation_ids=["affected:ZQ_HIGH"],
            )
        ],
        manual_checks=[
            ManualCheck(
                id="check-1",
                title="Review high impact query definition [affected:ZQ_HIGH]",
                tool="BWMT",
                steps_summary=(
                    "Open the local query definition and inspect semantics without activation "
                    "[affected:ZQ_HIGH]."
                ),
                priority=ImpactSeverity.HIGH,
                related_finding_ids=["finding:chg-high:ZQ_HIGH"],
                citation_ids=["affected:ZQ_HIGH"],
            )
        ],
        cards=_valid_cards(pack),
        cab_summary="- CAB should review deterministic findings [affected:ZQ_HIGH]",
        deterministic_pack=pack,
        trace=[
            ReviewTraceStep(
                stage="final_validator",
                round=0,
                summary="Pending final validation",
            )
        ],
        budget=AgenticReviewBudget(max_cards=3),
        budget_usage=AgenticReviewBudgetUsage(cards=0),
        audit_trail=[_audit()],
    )


def _valid_cards(pack: ImpactEvidencePack) -> list[AgenticReviewCard]:
    cards: list[AgenticReviewCard] = []
    for index, finding in enumerate(pack.impact.findings, start=1):
        object_citation = f"affected:{finding.impacted_object_id}"
        cards.append(
            AgenticReviewCard(
                id=f"card-{index}",
                kind="deterministic_finding",
                title=f"Review {finding.impacted_object_id} [{object_citation}]",
                body=f"Impact severity is copied from impact.py [{object_citation}].",
                severity_label=finding.severity,
                review_priority=index,
                source_finding_id=finding.id,
                citation_ids=[object_citation],
            )
        )
    return cards


def _audit() -> LlmAuditMetadata:
    return LlmAuditMetadata(
        model="local-fixture-model",
        prompt_sha256="1" * 64,
        sanitized_input_sha256="2" * 64,
        request_citation_ids=["affected:ZQ_HIGH"],
        response_timestamp="2026-06-23T00:00:00Z",
    )


def _sample_pack() -> ImpactEvidencePack:
    change_high = ChangeEvent(
        id="chg-high",
        object_id="ZC_PROVIDER",
        object_type="HCPR",
        change_type=ChangeType.FIELD_REMOVED,
        field="NET_VALUE",
    )
    change_unknown = ChangeEvent(
        id="chg-unknown",
        object_id="ZC_MISSING",
        object_type="HCPR",
        change_type=ChangeType.ROUTINE_CHANGED,
    )
    change_manual = ChangeEvent(
        id="chg-manual",
        object_id="ZC_ROUTINE",
        object_type="HCPR",
        change_type=ChangeType.ROUTINE_CHANGED,
    )
    return build_impact_evidence_pack(
        ImpactReport(
            changes=[change_high, change_unknown, change_manual],
            findings=[
                ImpactFinding(
                    id="finding:chg-high:ZQ_HIGH",
                    change_id=change_high.id,
                    impacted_object_id="ZQ_HIGH",
                    impacted_object_type="QUERY",
                    severity=ImpactSeverity.HIGH,
                    confidence="graph_rule",
                    reason="deterministic high finding",
                    evidence_node_ids=["ZC_PROVIDER", "ZQ_HIGH"],
                    evidence_edge_ids=["edge:high"],
                    manual_verification=False,
                ),
                ImpactFinding(
                    id="finding:chg-unknown:ZQ_UNKNOWN",
                    change_id=change_unknown.id,
                    impacted_object_id="ZQ_UNKNOWN",
                    impacted_object_type="QUERY",
                    severity=ImpactSeverity.UNKNOWN,
                    confidence="missing_source",
                    reason="source object was not present in the local graph",
                    evidence_node_ids=["ZC_MISSING"],
                    evidence_edge_ids=[],
                    manual_verification=False,
                ),
                ImpactFinding(
                    id="finding:chg-manual:ZQ_MANUAL",
                    change_id=change_manual.id,
                    impacted_object_id="ZQ_MANUAL",
                    impacted_object_type="QUERY",
                    severity=ImpactSeverity.MEDIUM,
                    confidence="graph_rule",
                    reason="routine change requires local manual review",
                    evidence_node_ids=["ZC_ROUTINE", "ZQ_MANUAL"],
                    evidence_edge_ids=["edge:manual"],
                    manual_verification=True,
                ),
            ],
        ),
        snapshot_id="snap-validator",
    )
