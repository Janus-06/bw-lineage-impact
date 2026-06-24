from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from bwli.config import LlmRuntimeConfig
from bwli.field_lineage import parse_native_sql_view
from bwli.impact import ChangeEvent, ChangeType, ImpactFinding, ImpactReport, ImpactSeverity
from bwli.impact_evidence import ImpactEvidencePack, build_impact_evidence_pack
from bwli.llm.agentic_review import (
    build_review_plan_request,
    create_agentic_review_plan,
)
from bwli.llm.explainer import LlmCitationError
from bwli.llm.sanitizer import REDACTED
from bwli.query_analysis import parse_query_xml

QUERY_XML = Path("tests/fixtures/query-analysis.xml")
SQL_VIEW = Path("tests/fixtures/native_sql_view.sql")


def test_create_agentic_review_plan_runtime_none_returns_empty_plan_without_network() -> None:
    calls = 0

    def forbidden_transport(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError("runtime=None must not call the LLM transport")

    plan = create_agentic_review_plan(
        _sample_pack(),
        runtime=None,
        question="What should be reviewed?",
        transport=forbidden_transport,
    )

    assert calls == 0
    assert plan.objectives == []
    assert plan.evidence_requests == []
    assert "LLM disabled" in plan.notes


def test_create_agentic_review_plan_accepts_mocked_openai_compatible_response() -> None:
    observed: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["url"] = str(request.url)
        observed["payload"] = json.loads(request.content.decode("utf-8"))
        return _chat_response(_valid_plan_payload())

    plan = create_agentic_review_plan(
        _sample_pack(),
        runtime=_runtime(),
        question="Focus on impacted queries.",
        transport=httpx.MockTransport(handler),
    )

    assert str(observed["url"]).endswith("/v1/chat/completions")
    assert observed["payload"]["model"] == "local-fixture-model"
    assert plan.objectives[0].id == "obj-1"
    assert plan.evidence_requests[0].target == "ZQ_SALES_MARGIN"


def test_create_agentic_review_plan_rejects_mocked_fabricated_citation() -> None:
    payload = _valid_plan_payload()
    payload["objectives"][0]["citation_ids"] = ["query:not-real"]

    with pytest.raises(LlmCitationError):
        create_agentic_review_plan(
            _sample_pack(),
            runtime=_runtime(),
            transport=httpx.MockTransport(lambda _request: _chat_response(payload)),
        )


def test_create_agentic_review_plan_rejects_mocked_fabricated_enricher() -> None:
    payload = _valid_plan_payload()
    payload["evidence_requests"][0]["enricher"] = "execute_sql"

    with pytest.raises(ValidationError):
        create_agentic_review_plan(
            _sample_pack(),
            runtime=_runtime(),
            transport=httpx.MockTransport(lambda _request: _chat_response(payload)),
        )


def test_build_review_plan_request_sanitizes_secret_like_pack_and_question_text() -> None:
    request = build_review_plan_request(
        _secret_like_pack(),
        question="Investigate password=hunter2 with Authorization: Bearer abc123 and sk-testkey.",
    )
    prompt = "\n".join(message.content for message in request.messages)
    lower_prompt = prompt.lower()

    for forbidden in [
        "zpassword_secret",
        "hunter2",
        "authorization: bearer",
        "abc123",
        "sk-testkey",
        "field_token",
    ]:
        assert forbidden not in lower_prompt
    assert REDACTED in prompt
    assert all("zpassword_secret" not in citation.lower() for citation in request.citation_ids)


def _chat_response(payload: dict[str, object]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-agentic-fixture",
            "choices": [{"message": {"content": json.dumps(payload)}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
    )


def _valid_plan_payload() -> dict[str, object]:
    return {
        "objectives": [
            {
                "id": "obj-1",
                "title": "Review impacted query",
                "rationale": (
                    "Query XML exposure intersects the impact scope "
                    "[query:ZQ_SALES_MARGIN]."
                ),
                "citation_ids": ["query:ZQ_SALES_MARGIN"],
            }
        ],
        "evidence_requests": [
            {
                "id": "req-1",
                "enricher": "reparse_query_xml",
                "target": "ZQ_SALES_MARGIN",
                "reason": "Refresh parse-only query XML evidence [query:ZQ_SALES_MARGIN].",
                "citation_hint": "query:ZQ_SALES_MARGIN",
            }
        ],
        "notes": "Planner remains bounded to deterministic evidence [scenario:change].",
    }


def _runtime() -> LlmRuntimeConfig:
    return LlmRuntimeConfig(
        base_url="http://127.0.0.1:11434/v1",
        model="local-fixture-model",
        api_key=SecretStr("fixture-runtime-key"),
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
        _impact_report("ZC_SALES", "NET_VALUE"),
        snapshot_id="snap-agentic",
        query_results=[query_result],
        sql_results=[sql_result],
        freshness_by_object_id={"ZQ_SALES_MARGIN": {"target_type": "QUERY", "requests": [{}]}},
    )


def _secret_like_pack() -> ImpactEvidencePack:
    return build_impact_evidence_pack(
        _impact_report("ZPASSWORD_SECRET", "FIELD_TOKEN"),
        snapshot_id="snap-secret",
    )


def _impact_report(change_object_id: str, field: str) -> ImpactReport:
    change = ChangeEvent(
        id="chg-provider",
        object_id=change_object_id,
        object_type="HCPR",
        change_type=ChangeType.FIELD_REMOVED,
        field=field,
        metadata={"password": "hunter2", "api_key": "sk-testkey"},
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
                evidence_node_ids=[change_object_id, "ZQ_SALES_MARGIN"],
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
                evidence_node_ids=[change_object_id, "zsales_fact"],
                evidence_edge_ids=["edge:sql"],
                manual_verification=False,
            ),
        ],
    )
