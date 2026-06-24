from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr

from bwli.config import LlmRuntimeConfig
from bwli.field_lineage import parse_native_sql_view
from bwli.impact import ChangeEvent, ChangeType, ImpactFinding, ImpactReport, ImpactSeverity
from bwli.impact_evidence import ImpactEvidencePack, build_impact_evidence_pack
from bwli.llm.agentic_review import (
    AgenticReviewBudget,
    AgenticReviewPlan,
    ReviewObjective,
    run_hypothesis_review,
)
from bwli.query_analysis import parse_query_xml

QUERY_XML = Path("tests/fixtures/query-analysis.xml")
SQL_VIEW = Path("tests/fixtures/native_sql_view.sql")


def test_clean_critic_stops_after_one_reviewer_and_one_critic_call() -> None:
    transport, call_kinds = _sequenced_transport(
        [_hypothesis_payload("hyp-1", "Initial supported query exposure"), _clean_critic_payload()]
    )

    result = run_hypothesis_review(
        _sample_pack(),
        _plan(),
        runtime=_runtime(),
        transport=transport,
    )

    assert call_kinds == ["reviewer", "critic"]
    assert result.status == "completed"
    assert result.review_rounds == 1
    assert result.llm_calls == 2
    assert result.hypotheses[0].id == "hyp-1"
    assert result.critic_defects == []
    assert [audit.citation_validation for audit in result.audit_trail] == ["passed", "passed"]
    assert [step.citation_validation for step in result.trace] == ["passed", "passed"]


def test_critic_defect_triggers_one_revision_then_clean_critic_completes() -> None:
    transport, call_kinds = _sequenced_transport(
        [
            _hypothesis_payload("hyp-1", "Initial unsupported overreach"),
            _critic_defect_payload(),
            _hypothesis_payload("hyp-2", "Revised supported query exposure"),
            _clean_critic_payload(),
        ]
    )

    result = run_hypothesis_review(
        _sample_pack(),
        _plan(),
        runtime=_runtime(),
        transport=transport,
    )

    assert call_kinds == ["reviewer", "critic", "reviewer", "critic"]
    assert result.status == "completed"
    assert result.review_rounds == 2
    assert result.llm_calls == 4
    assert result.hypotheses[0].id == "hyp-2"
    assert result.hypotheses[0].statement.startswith("Revised supported")
    assert result.critic_defects == []


def test_fabricated_citation_or_unsafe_completion_returns_fallback_without_exception() -> None:
    bad_citation = _hypothesis_payload("hyp-bad", "Fabricated citation")
    bad_citation["hypotheses"][0]["citation_ids"] = ["query:not-real"]  # type: ignore[index]
    unsafe_completion = _hypothesis_payload("hyp-unsafe", "Leaked password=hunter2")

    for reviewer_payload in [bad_citation, unsafe_completion]:
        transport, call_kinds = _sequenced_transport([reviewer_payload])

        result = run_hypothesis_review(
            _sample_pack(),
            _plan(),
            runtime=_runtime(),
            transport=transport,
        )

        assert call_kinds == ["reviewer"]
        assert result.status == "fallback"
        assert result.llm_calls == 1
        assert result.fallback_reason


def test_defects_remain_with_one_review_round_returns_fallback() -> None:
    transport, call_kinds = _sequenced_transport(
        [_hypothesis_payload("hyp-1", "Initial supported query exposure"), _critic_defect_payload()]
    )

    result = run_hypothesis_review(
        _sample_pack(),
        _plan(),
        runtime=_runtime(),
        budget=AgenticReviewBudget(max_review_rounds=1),
        transport=transport,
    )

    assert call_kinds == ["reviewer", "critic"]
    assert result.status == "fallback"
    assert result.review_rounds == 1
    assert result.llm_calls == 2
    assert result.critic_defects[0].id == "def-1"
    assert result.fallback_reason is not None
    assert "Critic defects" in result.fallback_reason
    assert "budget exhausted" in result.fallback_reason


def test_runtime_none_returns_disabled_fallback_without_transport_call() -> None:
    calls = 0

    def forbidden_transport(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError("runtime=None must not call the LLM transport")

    result = run_hypothesis_review(
        _sample_pack(),
        _plan(),
        runtime=None,
        transport=forbidden_transport,
    )

    assert calls == 0
    assert result.status == "fallback"
    assert result.llm_calls == 0
    assert result.review_rounds == 0
    assert result.fallback_reason is not None
    assert "LLM disabled" in result.fallback_reason
    assert result.trace[0].stage == "hypothesis_review_disabled"


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
        return _chat_response(responses[index])

    return httpx.MockTransport(handler), call_kinds


def _request_kind(payload: dict[str, Any]) -> str:
    messages = payload.get("messages")
    assert isinstance(messages, list)
    system = messages[0].get("content")
    assert isinstance(system, str)
    if "hypothesis/risk reviewer" in system:
        return "reviewer"
    if "read-only critic" in system:
        return "critic"
    raise AssertionError(f"unexpected prompt kind: {system}")


def _chat_response(payload: dict[str, object]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-agentic-reviewer-fixture",
            "choices": [{"message": {"content": json.dumps(payload)}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 6},
        },
    )


def _hypothesis_payload(hypothesis_id: str, statement_prefix: str) -> dict[str, object]:
    return {
        "hypotheses": [
            {
                "id": hypothesis_id,
                "statement": f"{statement_prefix} [query:ZQ_SALES_MARGIN].",
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
        "evidence_gaps": [
            {
                "id": "gap-1",
                "description": "Freshness should be checked locally [freshness:ZQ_SALES_MARGIN].",
                "missing_evidence": "No query execution or data preview is available.",
                "suggested_local_action": "lookup_request_freshness",
                "related_object_id": "ZQ_SALES_MARGIN",
                "citation_ids": ["freshness:ZQ_SALES_MARGIN"],
            }
        ],
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


def _critic_defect_payload() -> dict[str, object]:
    return {
        "defects": [
            {
                "id": "def-1",
                "category": "unsupported_claim",
                "description": "Revise unsupported scope wording [query:ZQ_SALES_MARGIN].",
                "citation_ids": ["query:ZQ_SALES_MARGIN"],
            }
        ]
    }


def _runtime() -> LlmRuntimeConfig:
    return LlmRuntimeConfig(
        base_url="http://127.0.0.1:11434/v1",
        model="local-fixture-model",
        api_key=SecretStr("fixture-runtime-key"),
    )


def _plan() -> AgenticReviewPlan:
    return AgenticReviewPlan(
        objectives=[
            ReviewObjective(
                id="obj-1",
                title="Review impacted query",
                rationale="Focus on deterministic query exposure [query:ZQ_SALES_MARGIN].",
                citation_ids=["query:ZQ_SALES_MARGIN"],
            )
        ],
        evidence_requests=[],
        notes="Planner remains bounded to deterministic evidence [scenario:change].",
    )


def _sample_pack() -> ImpactEvidencePack:
    query_result = parse_query_xml(
        QUERY_XML.read_text(encoding="utf-8"),
        source="bw://bw_get_query?queryName=ZQ_SALES_MARGIN",
    )
    sql_result = parse_native_sql_view(
        SQL_VIEW.read_text(encoding="utf-8"),
        view_id="ZSQL_VIEW",
    )
    return build_impact_evidence_pack(
        _impact_report(),
        snapshot_id="snap-agentic",
        query_results=[query_result],
        sql_results=[sql_result],
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
