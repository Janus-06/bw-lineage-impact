from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from bwli.field_lineage import parse_native_sql_view
from bwli.impact import ChangeEvent, ChangeType, ImpactFinding, ImpactReport, ImpactSeverity
from bwli.impact_evidence import ImpactEvidencePack, build_impact_evidence_pack
from bwli.llm.agentic_review import (
    AgenticReviewPlan,
    ReviewObjective,
    derive_agentic_citation_ids,
    parse_review_plan_content,
)
from bwli.llm.explainer import LlmCitationError
from bwli.query_analysis import parse_query_xml

QUERY_XML = Path("tests/fixtures/query-analysis.xml")
SQL_VIEW = Path("tests/fixtures/native_sql_view.sql")


def test_agentic_review_plan_model_round_trip() -> None:
    payload = _valid_plan_payload()

    plan = AgenticReviewPlan.model_validate(payload)
    restored = AgenticReviewPlan.model_validate_json(plan.model_dump_json())

    assert restored == plan
    assert restored.objectives[0].citation_ids == ["query:ZQ_SALES_MARGIN"]
    assert restored.evidence_requests[0].enricher == "reparse_query_xml"


def test_agentic_review_models_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ReviewObjective.model_validate(
            {
                "id": "obj-1",
                "title": "Review impacted query",
                "rationale": "Query exposure is present [query:ZQ_SALES_MARGIN].",
                "citation_ids": ["query:ZQ_SALES_MARGIN"],
                "unexpected": "blocked",
            }
        )


def test_parse_review_plan_content_rejects_malformed_json_fail_closed() -> None:
    with pytest.raises(json.JSONDecodeError):
        parse_review_plan_content(
            "{not-json",
            allowed_citation_ids=derive_agentic_citation_ids(_sample_pack()),
            allowed_enrichers=frozenset({"reparse_query_xml"}),
        )


def test_parse_review_plan_content_rejects_wrong_shape_fail_closed() -> None:
    with pytest.raises(ValueError):
        parse_review_plan_content(
            json.dumps([_valid_plan_payload()]),
            allowed_citation_ids=derive_agentic_citation_ids(_sample_pack()),
            allowed_enrichers=frozenset({"reparse_query_xml"}),
        )


def test_parse_review_plan_content_rejects_fabricated_citation_ids() -> None:
    payload = _valid_plan_payload()
    payload["objectives"][0]["citation_ids"] = ["query:not-real"]

    with pytest.raises(LlmCitationError):
        parse_review_plan_content(
            json.dumps(payload),
            allowed_citation_ids=derive_agentic_citation_ids(_sample_pack()),
            allowed_enrichers=frozenset({"reparse_query_xml"}),
        )


def test_parse_review_plan_content_rejects_fabricated_enricher_names() -> None:
    payload = _valid_plan_payload()
    payload["evidence_requests"][0]["enricher"] = "execute_sql"

    with pytest.raises(ValidationError):
        parse_review_plan_content(
            json.dumps(payload),
            allowed_citation_ids=derive_agentic_citation_ids(_sample_pack()),
            allowed_enrichers=frozenset({"reparse_query_xml"}),
        )


def test_parse_review_plan_content_rejects_unallowed_enricher_subset() -> None:
    payload = _valid_plan_payload()
    payload["evidence_requests"][0]["enricher"] = "recompute_impact_pack"

    with pytest.raises(ValueError, match="unallowed"):
        parse_review_plan_content(
            json.dumps(payload),
            allowed_citation_ids=derive_agentic_citation_ids(_sample_pack()),
            allowed_enrichers=frozenset({"reparse_query_xml"}),
        )


def test_derive_agentic_citation_ids_includes_expected_pack_ids() -> None:
    citations = derive_agentic_citation_ids(_sample_pack())

    assert citations[:3] == ["scenario:change", "affected:1", "affected:ZQ_SALES_MARGIN"]
    assert "affected:2" in citations
    assert "affected:zsales_fact" in citations
    assert "query:ZQ_SALES_MARGIN" in citations
    assert "sqlref:ZSQL_VIEW:zsales_fact" in citations
    assert "gap:query:ZQ_SALES_MARGIN" in citations
    assert "freshness:ZQ_SALES_MARGIN" in citations
    assert len(citations) == len(set(citations))


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


def _sample_pack() -> ImpactEvidencePack:
    report = _impact_report()
    query_result = parse_query_xml(
        QUERY_XML.read_text(encoding="utf-8"),
        source="bw://bw_get_query?queryName=ZQ_SALES_MARGIN",
    )
    sql_result = parse_native_sql_view(
        SQL_VIEW.read_text(encoding="utf-8"),
        view_id="ZSQL_VIEW",
    )
    return build_impact_evidence_pack(
        report,
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
