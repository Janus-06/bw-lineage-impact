from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from bwli.field_lineage import SqlParseResult, parse_native_sql_view
from bwli.impact import ChangeEvent, ChangeType, ImpactFinding, ImpactReport, ImpactSeverity
from bwli.impact_evidence import build_impact_evidence_pack
from bwli.llm.agentic_enricher import AgenticEvidenceEnricher, AgenticEvidenceSources
from bwli.llm.agentic_review import EvidenceRequest
from bwli.query_analysis import QueryAnalysisResult, parse_query_xml

QUERY_XML = Path("tests/fixtures/query-analysis.xml")
SQL_VIEW = Path("tests/fixtures/native_sql_view.sql")


def test_allowed_query_sql_and_freshness_requests_recompose_pack() -> None:
    prior_pack = build_impact_evidence_pack(_impact_report())
    calls: list[str] = []

    def query_result(request: EvidenceRequest) -> QueryAnalysisResult:
        calls.append(f"query:{request.target}")
        return _query_result()

    def sql_result(request: EvidenceRequest) -> SqlParseResult:
        calls.append(f"sql:{request.target}")
        return _sql_result()

    def freshness(
        request: EvidenceRequest,
    ) -> Mapping[str, Mapping[str, object]]:
        calls.append(f"freshness:{request.target}")
        return {
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
        }

    enriched = AgenticEvidenceEnricher(
        AgenticEvidenceSources(
            query_result=query_result,
            sql_result=sql_result,
            freshness=freshness,
        )
    ).run_enrichers(
        [
            _request("req-query", "reparse_query_xml", "ZQ_SALES_MARGIN"),
            _request("req-sql", "reparse_native_sql_view", "ZSQL_VIEW"),
            _request("req-fresh", "lookup_request_freshness", "ZQ_SALES_MARGIN"),
        ],
        prior_pack=prior_pack,
    )

    assert calls == ["query:ZQ_SALES_MARGIN", "sql:ZSQL_VIEW", "freshness:ZQ_SALES_MARGIN"]
    assert len(enriched.query_evidence) >= len(prior_pack.query_evidence) + 1
    assert len(enriched.sql_evidence) >= len(prior_pack.sql_evidence) + 1
    assert len(enriched.freshness_evidence) >= len(prior_pack.freshness_evidence) + 1
    assert enriched.coverage_summary["query_evidence_count"] == 1
    assert enriched.coverage_summary["sql_evidence_count"] == 1
    assert enriched.coverage_summary["freshness_evidence_count"] == 1
    assert {gap.source for gap in enriched.manual_verification_gaps} >= {
        "query",
        "sql",
        "freshness",
    }


def test_query_and_sql_refresh_does_not_alter_impact_authority_fields() -> None:
    prior_pack = build_impact_evidence_pack(_impact_report(manual_verification=True))
    before = [
        (
            finding.id,
            finding.severity,
            finding.confidence,
            finding.manual_verification,
        )
        for finding in prior_pack.impact.findings
    ]

    enriched = AgenticEvidenceEnricher(
        AgenticEvidenceSources(
            query_result=lambda _request: _query_result(),
            sql_result=lambda _request: _sql_result(),
        )
    ).run_enrichers(
        [
            _request("req-query", "reparse_query_xml", "ZQ_SALES_MARGIN"),
            _request("req-sql", "reparse_native_sql_view", "ZSQL_VIEW"),
        ],
        prior_pack=prior_pack,
    )

    after = [
        (
            finding.id,
            finding.severity,
            finding.confidence,
            finding.manual_verification,
        )
        for finding in enriched.impact.findings
    ]
    assert after == before
    assert enriched.impact.findings[0].severity == ImpactSeverity.HIGH
    assert enriched.impact.findings[0].confidence == "graph_rule"
    assert enriched.impact.findings[0].manual_verification is True


def test_enricher_uses_only_specific_injected_parser_fakes_without_bw_call_path() -> None:
    calls: list[str] = []

    def forbidden_impact_report() -> ImpactReport:
        raise AssertionError("unexpected live impact/BW call path")

    def forbidden_freshness(
        _request: EvidenceRequest,
    ) -> Mapping[str, Mapping[str, object]]:
        raise AssertionError("unexpected live freshness/BW call path")

    enriched = AgenticEvidenceEnricher(
        AgenticEvidenceSources(
            impact_report=forbidden_impact_report,
            query_result=lambda request: _record_query_call(request, calls),
            sql_result=lambda request: _record_sql_call(request, calls),
            freshness=forbidden_freshness,
        )
    ).run_enrichers(
        [
            _request("req-query", "reparse_query_xml", "ZQ_SALES_MARGIN"),
            _request("req-sql", "reparse_native_sql_view", "ZSQL_VIEW"),
        ],
        prior_pack=build_impact_evidence_pack(_impact_report()),
    )

    assert calls == ["query:ZQ_SALES_MARGIN", "sql:ZSQL_VIEW"]
    assert len(enriched.query_evidence) == 1
    assert len(enriched.sql_evidence) == 1


def test_missing_adapter_and_none_result_preserve_prior_pack_fail_safe() -> None:
    prior_pack = build_impact_evidence_pack(
        _impact_report(),
        query_results=[_query_result()],
    )

    result = AgenticEvidenceEnricher(
        AgenticEvidenceSources(query_result=lambda _request: None)
    ).run_enrichers(
        [
            _request("req-query", "reparse_query_xml", "ZQ_SALES_MARGIN"),
            _request("req-sql", "reparse_native_sql_view", "ZSQL_VIEW"),
            _request("req-fresh", "lookup_request_freshness", "ZQ_SALES_MARGIN"),
        ],
        prior_pack=prior_pack,
    )

    assert result == prior_pack


def test_unknown_enricher_value_raises_value_error() -> None:
    unknown_request = EvidenceRequest.model_construct(
        id="req-unknown",
        enricher="execute_sql",
        target="ZQ_SALES_MARGIN",
        reason="PolicyGate should have rejected this request.",
        citation_hint=None,
    )

    with pytest.raises(ValueError, match="unknown agentic evidence enricher"):
        AgenticEvidenceEnricher(AgenticEvidenceSources()).run_enrichers(
            [unknown_request],
            prior_pack=build_impact_evidence_pack(_impact_report()),
        )


def _record_query_call(request: EvidenceRequest, calls: list[str]) -> QueryAnalysisResult:
    calls.append(f"query:{request.target}")
    return _query_result()


def _record_sql_call(request: EvidenceRequest, calls: list[str]) -> SqlParseResult:
    calls.append(f"sql:{request.target}")
    return _sql_result()


def _request(request_id: str, enricher: str, target: str) -> EvidenceRequest:
    return EvidenceRequest.model_construct(
        id=request_id,
        enricher=enricher,
        target=target,
        reason="Parse-only evidence refresh for scoped agentic review.",
        citation_hint=None,
    )


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


def _impact_report(*, manual_verification: bool = False) -> ImpactReport:
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
                manual_verification=manual_verification,
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
