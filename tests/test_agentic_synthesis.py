from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from bwli.config import LlmRuntimeConfig
from bwli.field_lineage import parse_native_sql_view
from bwli.impact import ChangeEvent, ChangeType, ImpactFinding, ImpactReport, ImpactSeverity
from bwli.impact_evidence import ImpactEvidencePack, build_impact_evidence_pack
from bwli.llm.agentic_review import (
    AgenticReviewPlan,
    ReviewObjective,
    create_agentic_synthesis,
    derive_agentic_citation_ids,
    deterministic_review_cards,
    parse_synthesis_content,
)
from bwli.query_analysis import parse_query_xml

QUERY_XML = Path("tests/fixtures/query-analysis.xml")
SQL_VIEW = Path("tests/fixtures/native_sql_view.sql")


def test_deterministic_review_cards_mirror_finding_severities_and_kind() -> None:
    pack = _sample_pack()

    cards = deterministic_review_cards(pack)

    assert [card.kind for card in cards] == [
        "deterministic_finding",
        "deterministic_finding",
    ]
    assert [card.source_finding_id for card in cards] == [
        finding.id for finding in pack.impact.findings
    ]
    assert [card.severity_label for card in cards] == [
        finding.severity for finding in pack.impact.findings
    ]
    assert [card.review_priority for card in cards] == [1, 2]
    assert "affected:1" in cards[0].citation_ids
    assert "affected:ZQ_SALES_MARGIN" in cards[0].body


def test_mocked_llm_synthesis_accepts_valid_cards_and_preserves_kind() -> None:
    observed: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["url"] = str(request.url)
        observed["payload"] = json.loads(request.content.decode("utf-8"))
        return _chat_response(_valid_synthesis_payload())

    result = create_agentic_synthesis(
        _sample_pack(),
        _plan(),
        [],
        [],
        [],
        runtime=_runtime(),
        transport=httpx.MockTransport(handler),
    )

    assert str(observed["url"]).endswith("/v1/chat/completions")
    assert observed["payload"]["model"] == "local-fixture-model"
    assert result.status == "completed"
    assert result.llm_calls == 1
    assert result.cards[0].kind == "llm_proposed_concern"
    assert result.cards[0].severity_label == ImpactSeverity.HIGH
    assert result.cab_summary.startswith("- Validate impacted query semantics")
    assert [audit.citation_validation for audit in result.audit_trail] == ["passed"]
    assert [step.citation_validation for step in result.trace] == ["passed"]


def test_mocked_deterministic_card_severity_override_returns_fallback() -> None:
    pack = _sample_pack()
    payload = {
        "cards": [
            {
                "id": "card-det-override",
                "kind": "deterministic_finding",
                "title": "Incorrect low finding [affected:ZQ_SALES_MARGIN]",
                "body": "Attempts to lower deterministic severity [affected:ZQ_SALES_MARGIN].",
                "severity_label": "LOW",
                "review_priority": 1,
                "source_finding_id": "finding:chg-provider:ZQ_SALES_MARGIN",
                "citation_ids": ["affected:ZQ_SALES_MARGIN"],
            }
        ],
        "cab_summary": "- Incorrectly lowers the deterministic finding [affected:ZQ_SALES_MARGIN]",
    }

    result = create_agentic_synthesis(
        pack,
        _plan(),
        [],
        [],
        [],
        runtime=_runtime(),
        transport=httpx.MockTransport(lambda _request: _chat_response(payload)),
    )

    assert result.status == "fallback"
    assert result.llm_calls == 1
    assert result.fallback_reason is not None
    assert "override deterministic severity" in result.fallback_reason
    assert result.cards[0].kind == "deterministic_finding"
    assert result.cards[0].severity_label == ImpactSeverity.HIGH


def test_parse_synthesis_content_enforces_max_cards_fail_closed() -> None:
    payload = _valid_synthesis_payload()
    payload["cards"] = [
        payload["cards"][0],
        {
            "id": "card-2",
            "kind": "llm_proposed_concern",
            "title": "Second card",
            "body": "Second cited concern [affected:zsales_fact].",
            "severity_label": "MEDIUM",
            "review_priority": 2,
            "source_finding_id": None,
            "citation_ids": ["affected:zsales_fact"],
        },
    ]

    with pytest.raises(ValueError, match="max_cards"):
        parse_synthesis_content(
            json.dumps(payload),
            allowed_citation_ids=derive_agentic_citation_ids(_sample_pack()),
            pack=_sample_pack(),
            max_cards=1,
        )


def test_runtime_none_returns_deterministic_cards_and_summary_without_transport_call() -> None:
    calls = 0

    def forbidden_transport(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError("runtime=None must not call the LLM transport")

    result = create_agentic_synthesis(
        _sample_pack(),
        _plan(),
        [],
        [],
        [],
        runtime=None,
        transport=forbidden_transport,
    )

    assert calls == 0
    assert result.status == "completed"
    assert result.llm_calls == 0
    assert result.cards
    assert {card.kind for card in result.cards} == {"deterministic_finding"}
    assert result.cab_summary.strip()
    assert "[affected:1]" in result.cab_summary


def test_fabricated_citation_or_sensitive_completion_fails_closed() -> None:
    fabricated_citation = _valid_synthesis_payload()
    fabricated_citation["cards"][0]["citation_ids"] = ["query:not-real"]  # type: ignore[index]
    fabricated_citation["cards"][0]["body"] = "Fabricated concern [query:not-real]."  # type: ignore[index]

    sensitive_completion = _valid_synthesis_payload()
    sensitive_completion["cards"][0]["body"] = (  # type: ignore[index]
        "Leaked password=hunter2 should fail closed [query:ZQ_SALES_MARGIN]."
    )

    for payload in [fabricated_citation, sensitive_completion]:
        result = create_agentic_synthesis(
            _sample_pack(),
            _plan(),
            [],
            [],
            [],
            runtime=_runtime(),
            transport=httpx.MockTransport(lambda _request, body=payload: _chat_response(body)),
        )

        assert result.status == "fallback"
        assert result.llm_calls == 1
        assert result.fallback_reason
        assert {card.kind for card in result.cards} == {"deterministic_finding"}


def _chat_response(payload: dict[str, object]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-agentic-synthesis-fixture",
            "choices": [{"message": {"content": json.dumps(payload)}}],
            "usage": {"prompt_tokens": 14, "completion_tokens": 8},
        },
    )


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
        "cab_summary": (
            "- Validate impacted query semantics before CAB approval "
            "[query:ZQ_SALES_MARGIN]"
        ),
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
