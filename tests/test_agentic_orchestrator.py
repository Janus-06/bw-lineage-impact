from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr

from bwli.config import LlmRuntimeConfig
from bwli.field_lineage import SqlParseResult, parse_native_sql_view
from bwli.impact import ChangeEvent, ChangeType, ImpactFinding, ImpactReport, ImpactSeverity
from bwli.impact_evidence import ImpactEvidencePack, build_impact_evidence_pack
from bwli.llm.agentic_enricher import AgenticEvidenceEnricher, AgenticEvidenceSources
from bwli.llm.agentic_orchestrator import AgenticReviewAssistant
from bwli.llm.agentic_review import AgenticReviewBudget, deterministic_review_cards
from bwli.query_analysis import QueryAnalysisResult, parse_query_xml

QUERY_XML = Path("tests/fixtures/query-analysis.xml")
SQL_VIEW = Path("tests/fixtures/native_sql_view.sql")


def test_end_to_end_mock_run_completes_with_audit_policy_and_validator_stamps() -> None:
    pack = _sample_pack()
    transport, call_kinds = _sequenced_transport(
        [
            _planner_payload(citation_id="query:ZQ_SALES_MARGIN"),
            _hypothesis_payload(),
            _clean_critic_payload(),
            _valid_synthesis_payload(),
        ]
    )

    run = AgenticReviewAssistant(transport=transport).run(
        pack,
        runtime=_runtime(),
        question="Which findings need CAB attention?",
    )

    assert call_kinds == ["planner", "reviewer", "critic", "synthesis"]
    assert run.status == "completed"
    assert run.llm_enabled is True
    assert run.llm_disabled is False
    assert run.budget_usage.planner_rounds == 1
    assert run.budget_usage.review_rounds == 1
    assert run.budget_usage.llm_calls == 4
    assert run.policy_decisions
    assert run.policy_decisions[0].allowed is True
    assert run.deterministic_pack == pack
    assert {audit.citation_validation for audit in run.audit_trail} == {"passed"}
    assert {step.citation_validation for step in run.trace} == {"passed"}
    assert run.cards[0].kind == "llm_proposed_concern"


def test_runtime_none_returns_disabled_deterministic_run_without_transport_call() -> None:
    calls = 0

    def forbidden_transport(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError("runtime=None must not call transport")

    pack = _sample_pack()
    run = AgenticReviewAssistant(transport=forbidden_transport).run(
        pack,
        runtime=None,
    )

    assert calls == 0
    assert run.status == "disabled"
    assert run.llm_enabled is False
    assert run.llm_disabled is True
    assert run.cards == deterministic_review_cards(pack)
    assert run.cab_summary.strip()
    assert run.budget_usage.cards == len(run.cards)
    assert "LLM runtime disabled" in run.trace[0].summary


def test_max_planner_rounds_zero_fallbacks_before_transport() -> None:
    transport, call_kinds = _sequenced_transport([_planner_payload()])

    run = AgenticReviewAssistant(transport=transport).run(
        _sample_pack(),
        runtime=_runtime(),
        budget=AgenticReviewBudget(max_planner_rounds=0),
    )

    assert call_kinds == []
    assert run.status == "fallback"
    assert run.budget_usage.llm_calls == 0
    assert run.cards
    assert "Planner budget exhausted" in run.trace[-1].summary


def test_max_review_rounds_zero_fallbacks_after_planner_policy_before_reviewer() -> None:
    transport, call_kinds = _sequenced_transport([_planner_payload()])

    run = AgenticReviewAssistant(transport=transport).run(
        _sample_pack(),
        runtime=_runtime(),
        budget=AgenticReviewBudget(max_review_rounds=0),
    )

    assert call_kinds == ["planner"]
    assert run.status == "fallback"
    assert run.budget_usage.planner_rounds == 1
    assert run.budget_usage.llm_calls == 1
    assert run.policy_decisions
    assert not any(kind in call_kinds for kind in ["reviewer", "critic", "synthesis"])
    assert "Review budget exhausted" in run.trace[-1].summary


def test_max_llm_calls_one_fallbacks_before_reviewer_critic_or_synthesis() -> None:
    transport, call_kinds = _sequenced_transport([_planner_payload(), _hypothesis_payload()])

    run = AgenticReviewAssistant(transport=transport).run(
        _sample_pack(),
        runtime=_runtime(),
        budget=AgenticReviewBudget(max_llm_calls=1),
    )

    assert call_kinds == ["planner"]
    assert run.status == "fallback"
    assert run.budget_usage.llm_calls == 1
    assert "LLM call budget exhausted" in run.trace[-1].summary


def test_max_cards_zero_with_findings_returns_deterministic_zero_card_fallback() -> None:
    transport, call_kinds = _sequenced_transport([_planner_payload()])

    run = AgenticReviewAssistant(transport=transport).run(
        _sample_pack(),
        runtime=_runtime(),
        budget=AgenticReviewBudget(max_cards=0),
    )

    assert call_kinds == []
    assert run.status == "fallback"
    assert run.cards == []
    assert run.budget_usage.cards == 0
    assert "max_cards is 0" in run.trace[-1].summary
    assert "max_cards is 0" in run.cab_summary


def test_max_latency_zero_fallbacks_before_llm_call_with_injected_clock() -> None:
    transport, call_kinds = _sequenced_transport([_planner_payload()])

    run = AgenticReviewAssistant(transport=transport, clock=lambda: 10.0).run(
        _sample_pack(),
        runtime=_runtime(),
        budget=AgenticReviewBudget(max_latency_ms=0),
    )

    assert call_kinds == []
    assert run.status == "fallback"
    assert run.budget_usage.llm_calls == 0
    assert "Latency budget" in run.trace[-1].summary


def test_planner_fabricated_citation_returns_fallback_with_deterministic_pack() -> None:
    bad_plan = _planner_payload()
    bad_plan["objectives"][0]["citation_ids"] = ["query:not-real"]  # type: ignore[index]
    transport, call_kinds = _sequenced_transport([bad_plan])
    pack = _sample_pack()

    run = AgenticReviewAssistant(transport=transport).run(pack, runtime=_runtime())

    assert call_kinds == ["planner"]
    assert run.status == "fallback"
    assert run.deterministic_pack == pack
    assert run.cards == deterministic_review_cards(pack)
    assert run.budget_usage.llm_calls == 1
    assert "Fallback" in run.trace[-1].summary


def test_allowed_evidence_enricher_augments_pack_before_review() -> None:
    prior_pack = _pack_without_optional_evidence()
    query_calls: list[str] = []

    def query_result(request: Any) -> QueryAnalysisResult:
        query_calls.append(request.target)
        return _query_result()

    enricher = AgenticEvidenceEnricher(
        AgenticEvidenceSources(query_result=query_result)
    )
    transport, call_kinds = _sequenced_transport(
        [
            _planner_payload(citation_id="affected:ZQ_SALES_MARGIN"),
            _hypothesis_payload(),
            _clean_critic_payload(),
            _valid_synthesis_payload(),
        ]
    )

    run = AgenticReviewAssistant(transport=transport, enricher=enricher).run(
        prior_pack,
        runtime=_runtime(),
    )

    assert call_kinds == ["planner", "reviewer", "critic", "synthesis"]
    assert query_calls == ["ZQ_SALES_MARGIN"]
    assert run.status == "completed"
    assert run.policy_decisions[0].allowed is True
    assert run.budget_usage.evidence_requests == 1
    assert run.budget_usage.enrichers_executed == 1
    assert [item.query_id for item in run.deterministic_pack.query_evidence] == [
        "ZQ_SALES_MARGIN"
    ]


def _sequenced_transport(
    responses: Sequence[dict[str, object]],
) -> tuple[httpx.MockTransport, list[str]]:
    call_kinds: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        call_kinds.append(_request_kind(payload))
        index = len(call_kinds) - 1
        if index >= len(responses):
            raise AssertionError(f"unexpected extra LLM call: {call_kinds}")
        return _chat_response(responses[index], response_id=f"chatcmpl-orch-{index}")

    return httpx.MockTransport(handler), call_kinds


def _request_kind(payload: dict[str, Any]) -> str:
    messages = payload.get("messages")
    assert isinstance(messages, list)
    first = messages[0]
    assert isinstance(first, dict)
    system = first.get("content")
    assert isinstance(system, str)
    if "impact-review planner" in system:
        return "planner"
    if "hypothesis/risk reviewer" in system:
        return "reviewer"
    if "read-only critic" in system:
        return "critic"
    if "final review synthesizer" in system:
        return "synthesis"
    raise AssertionError(f"unexpected prompt kind: {system}")


def _chat_response(payload: dict[str, object], *, response_id: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": response_id,
            "choices": [{"message": {"content": json.dumps(payload)}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
    )


def _planner_payload(*, citation_id: str = "query:ZQ_SALES_MARGIN") -> dict[str, object]:
    return {
        "objectives": [
            {
                "id": "obj-1",
                "title": "Review impacted query",
                "rationale": f"Focus on deterministic query exposure [{citation_id}].",
                "citation_ids": [citation_id],
            }
        ],
        "evidence_requests": [
            {
                "id": "req-1",
                "enricher": "reparse_query_xml",
                "target": "ZQ_SALES_MARGIN",
                "reason": f"Refresh parse-only query XML evidence [{citation_id}].",
                "citation_hint": citation_id,
            }
        ],
        "notes": "Planner remains bounded to deterministic evidence [scenario:change].",
    }


def _hypothesis_payload() -> dict[str, object]:
    return {
        "hypotheses": [
            {
                "id": "hyp-1",
                "statement": (
                    "Query exposure is supported by parsed evidence "
                    "[query:ZQ_SALES_MARGIN]."
                ),
                "status": "supported",
                "severity_opinion": "HIGH",
                "supports_finding_ids": ["finding:chg-provider:ZQ_SALES_MARGIN"],
                "confidence_rationale": (
                    "The deterministic impact finding and parsed query evidence intersect "
                    "[affected:ZQ_SALES_MARGIN]."
                ),
                "citation_ids": ["query:ZQ_SALES_MARGIN", "affected:ZQ_SALES_MARGIN"],
            }
        ],
        "evidence_gaps": [],
        "manual_checks": [
            {
                "id": "check-1",
                "title": "Review query variables",
                "tool": "BWMT",
                "steps_summary": (
                    "Open the query definition locally and inspect variables without activation "
                    "[query:ZQ_SALES_MARGIN]."
                ),
                "priority": "HIGH",
                "related_finding_ids": ["finding:chg-provider:ZQ_SALES_MARGIN"],
                "citation_ids": ["query:ZQ_SALES_MARGIN"],
            }
        ],
    }


def _clean_critic_payload() -> dict[str, object]:
    return {"defects": []}


def _valid_synthesis_payload() -> dict[str, object]:
    return {
        "cards": [
            {
                "id": "card-1",
                "kind": "llm_proposed_concern",
                "title": "Review query exposure",
                "body": (
                    "Parsed query evidence intersects the deterministic impact scope "
                    "[query:ZQ_SALES_MARGIN]."
                ),
                "severity_label": "HIGH",
                "review_priority": 1,
                "source_finding_id": None,
                "citation_ids": ["query:ZQ_SALES_MARGIN"],
            }
        ],
        "cab_summary": "- Validate impacted query semantics [query:ZQ_SALES_MARGIN]",
    }


def _runtime() -> LlmRuntimeConfig:
    return LlmRuntimeConfig(
        base_url="http://127.0.0.1:11434/v1",
        model="local-fixture-model",
        api_key=SecretStr("fixture-runtime-key"),
    )


def _sample_pack() -> ImpactEvidencePack:
    return build_impact_evidence_pack(
        _impact_report(),
        snapshot_id="snap-agentic",
        query_results=[_query_result()],
        sql_results=[_sql_result()],
        freshness_by_object_id={
            "ZQ_SALES_MARGIN": {
                "target_type": "QUERY",
                "requests": [
                    {
                        "request_tsn": "000123",
                        "status": "GREEN",
                        "timestamp": "2026-06-23T00:00:00Z",
                        "records": 42,
                    }
                ],
            }
        },
    )


def _pack_without_optional_evidence() -> ImpactEvidencePack:
    return build_impact_evidence_pack(_impact_report(), snapshot_id="snap-agentic")


def _query_result() -> QueryAnalysisResult:
    return parse_query_xml(
        QUERY_XML.read_text(encoding="utf-8"),
        source="bw://bw_get_query?queryName=ZQ_SALES_MARGIN",
    )


def _sql_result() -> SqlParseResult:
    return parse_native_sql_view(
        SQL_VIEW.read_text(encoding="utf-8"),
        view_id="ZSQL_VIEW",
    )


def _impact_report() -> ImpactReport:
    change = ChangeEvent(
        id="chg-provider",
        object_id="ZC_SALES",
        object_type="HCPR",
        change_type=ChangeType.FIELD_REMOVED,
        field="NET_VALUE",
    )
    return ImpactReport(
        changes=[change],
        findings=[
            ImpactFinding(
                id="finding:chg-provider:ZQ_SALES_MARGIN",
                change_id=change.id,
                impacted_object_id="ZQ_SALES_MARGIN",
                impacted_object_type="QUERY",
                severity=ImpactSeverity.HIGH,
                confidence="graph_rule",
                reason="deterministic graph finding",
                evidence_node_ids=["ZC_SALES", "ZQ_SALES_MARGIN"],
                evidence_edge_ids=["edge:query"],
                manual_verification=False,
            ),
            ImpactFinding(
                id="finding:chg-provider:zsales_fact",
                change_id=change.id,
                impacted_object_id="zsales_fact",
                impacted_object_type="ADSO",
                severity=ImpactSeverity.MEDIUM,
                confidence="graph_rule",
                reason="deterministic graph finding",
                evidence_node_ids=["ZC_SALES", "zsales_fact"],
                evidence_edge_ids=["edge:sql"],
                manual_verification=False,
            ),
        ],
    )
