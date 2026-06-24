from __future__ import annotations

from pathlib import Path

from bwli.field_lineage import SqlConfidence, parse_native_sql_view
from bwli.impact import (
    ChangeEvent,
    ChangeType,
    ImpactFinding,
    ImpactReport,
    ImpactSeverity,
)
from bwli.impact_evidence import build_impact_evidence_pack
from bwli.query_analysis import parse_query_xml

QUERY_XML = Path("tests/fixtures/query-analysis.xml")
SQL_VIEW = Path("tests/fixtures/native_sql_view.sql")


def test_impact_evidence_pack_composes_impact_only_contract() -> None:
    report = _impact_report(
        finding_object_id="ZQ_SALES_MARGIN",
        finding_object_type="QUERY",
        severity=ImpactSeverity.HIGH,
        confidence="graph_rule",
        manual_verification=False,
    )

    pack = build_impact_evidence_pack(report, snapshot_id="snap-1")

    assert pack.snapshot_id == "snap-1"
    assert pack.deterministic is True
    assert pack.read_only is True
    assert pack.execution_blocked is True
    assert pack.final_authority == "impact.py"
    assert pack.impact.findings == report.findings
    assert pack.query_evidence == []
    assert pack.sql_evidence == []
    assert pack.coverage_summary["impact_finding_count"] == 1


def test_impact_evidence_pack_adds_query_xml_exposure_without_inference() -> None:
    report = _impact_report(
        finding_object_id="ZQ_SALES_MARGIN",
        finding_object_type="QUERY",
        severity=ImpactSeverity.HIGH,
        confidence="graph_rule",
        manual_verification=False,
    )
    query_result = parse_query_xml(
        QUERY_XML.read_text(encoding="utf-8"),
        source="bw://bw_get_query?queryName=ZQ_SALES_MARGIN",
    )

    pack = build_impact_evidence_pack(report, query_results=[query_result])

    assert len(pack.query_evidence) == 1
    query_evidence = pack.query_evidence[0]
    assert query_evidence.query_id == "ZQ_SALES_MARGIN"
    assert query_evidence.provider_object_ids == ["ZC_SALES"]
    assert query_evidence.variable_names == ["ZVAR_CALMONTH"]
    assert query_evidence.calculated_key_figure_names == ["ZCKF_MARGIN"]
    assert query_evidence.restricted_key_figure_names == ["ZRKF_CURR_YEAR"]
    assert query_evidence.matched_finding_ids == ["finding:chg-provider:ZQ_SALES_MARGIN"]
    assert any(
        "does not execute the BW query" in note for note in query_evidence.manual_check_notes
    )
    assert pack.impact.findings[0].severity == ImpactSeverity.HIGH
    assert pack.impact.findings[0].confidence == "graph_rule"


def test_impact_evidence_pack_adds_sql_reference_evidence_without_execution() -> None:
    report = _impact_report(
        finding_object_id="zsales_fact",
        finding_object_type="ADSO",
        severity=ImpactSeverity.MEDIUM,
        confidence="graph_rule",
        manual_verification=False,
    )
    sql_result = parse_native_sql_view(
        SQL_VIEW.read_text(encoding="utf-8"),
        view_id="ZSQL_VIEW",
    )

    pack = build_impact_evidence_pack(report, sql_results=[sql_result])

    assert len(pack.sql_evidence) == 1
    sql_evidence = pack.sql_evidence[0]
    assert sql_evidence.view_id == "ZSQL_VIEW"
    assert sql_evidence.confidence == SqlConfidence.SQL_PARSED
    assert sql_evidence.referenced_object_ids == ["zsales_fact", "zcustomer_dim"]
    assert "net_amount" in sql_evidence.referenced_column_names
    assert sql_evidence.reference_edge_ids == [
        "sqlref:ZSQL_VIEW:zsales_fact",
        "sqlref:ZSQL_VIEW:zcustomer_dim",
    ]
    assert sql_evidence.matched_finding_ids == ["finding:chg-provider:zsales_fact"]
    assert any("without executing database SQL" in note for note in sql_evidence.manual_check_notes)
    assert pack.impact.findings[0].severity == ImpactSeverity.MEDIUM


def test_impact_evidence_pack_preserves_impact_manual_authority_with_extra_evidence() -> None:
    report = _impact_report(
        finding_object_id="ZQ_SALES_MARGIN",
        finding_object_type="QUERY",
        severity=ImpactSeverity.LOW,
        confidence="graph_rule",
        manual_verification=True,
    )
    query_result = parse_query_xml(
        QUERY_XML.read_text(encoding="utf-8"),
        source="bw://bw_get_query?queryName=ZQ_SALES_MARGIN",
    )
    sql_result = parse_native_sql_view("SELECT * FROM ZQ_SALES_MARGIN", view_id="ZSQL_VIEW")

    pack = build_impact_evidence_pack(
        report,
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

    finding = pack.impact.findings[0]
    assert finding.severity == ImpactSeverity.LOW
    assert finding.confidence == "graph_rule"
    assert finding.manual_verification is True
    assert {gap.source for gap in pack.manual_verification_gaps} == {
        "impact",
        "query",
        "sql",
        "freshness",
    }
    assert pack.freshness_evidence[0].latest_request_tsn == "000123"
    assert pack.freshness_evidence[0].latest_records == 42


def _impact_report(
    *,
    finding_object_id: str,
    finding_object_type: str,
    severity: ImpactSeverity,
    confidence: str,
    manual_verification: bool,
) -> ImpactReport:
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
                id=f"finding:{change.id}:{finding_object_id}",
                change_id=change.id,
                impacted_object_id=finding_object_id,
                impacted_object_type=finding_object_type,
                severity=severity,
                confidence=confidence,
                reason="deterministic graph finding",
                evidence_node_ids=["ZC_SALES", finding_object_id],
                evidence_edge_ids=[f"edge:{finding_object_id}"],
                manual_verification=manual_verification,
            )
        ],
    )
