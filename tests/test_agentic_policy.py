from __future__ import annotations

from pathlib import Path

from bwli.field_lineage import parse_native_sql_view
from bwli.impact import ChangeEvent, ChangeType, ImpactFinding, ImpactReport, ImpactSeverity
from bwli.impact_evidence import ImpactEvidencePack, build_impact_evidence_pack
from bwli.llm.agentic_policy import PolicyGate
from bwli.llm.agentic_review import AgenticReviewBudget, AgenticReviewPlan, EvidenceRequest
from bwli.query_analysis import parse_query_xml

QUERY_XML = Path("tests/fixtures/query-analysis.xml")
SQL_VIEW = Path("tests/fixtures/native_sql_view.sql")


def test_policy_gate_allows_valid_parse_only_request() -> None:
    request = _request("req-1", "reparse_query_xml", "ZQ_SALES_MARGIN")

    allowed, decisions = PolicyGate().evaluate(
        AgenticReviewPlan(evidence_requests=[request]),
        pack=_sample_pack(),
        budget=AgenticReviewBudget(max_evidence_requests=3),
    )

    assert allowed == [request]
    assert decisions[0].allowed is True
    assert decisions[0].reason


def test_policy_gate_rejects_non_allowlisted_enricher() -> None:
    request = EvidenceRequest.model_construct(
        id="req-bad",
        enricher="execute_sql",
        target="ZQ_SALES_MARGIN",
        reason="Try a forbidden action.",
        citation_hint=None,
    )

    allowed, decisions = PolicyGate().evaluate(
        AgenticReviewPlan.model_construct(evidence_requests=[request], objectives=[], notes=""),
        pack=_sample_pack(),
        budget=AgenticReviewBudget(max_evidence_requests=3),
    )

    assert allowed == []
    assert decisions[0].allowed is False
    assert "not allowlisted" in decisions[0].reason


def test_policy_gate_rejects_mutating_execution_and_data_preview_tokens() -> None:
    request = _request(
        "req-mutate",
        "reparse_query_xml",
        "ZQ_SALES_MARGIN",
        reason="Execute the query and preview data rows before parsing.",
    )

    allowed, decisions = PolicyGate().evaluate(
        AgenticReviewPlan(evidence_requests=[request]),
        pack=_sample_pack(),
        budget=AgenticReviewBudget(max_evidence_requests=3),
    )

    assert allowed == []
    assert decisions[0].allowed is False
    assert "mutating" in decisions[0].reason


def test_policy_gate_rejects_wildcard_and_empty_targets() -> None:
    requests = [
        _request("req-empty", "reparse_query_xml", ""),
        _request("req-wild", "lookup_request_freshness", "ZQ_*"),
    ]

    allowed, decisions = PolicyGate().evaluate(
        AgenticReviewPlan.model_construct(evidence_requests=requests, objectives=[], notes=""),
        pack=_sample_pack(),
        budget=AgenticReviewBudget(max_evidence_requests=3),
    )

    assert allowed == []
    assert len(decisions) == 2
    assert all(decision.allowed is False for decision in decisions)
    assert "empty" in decisions[0].reason
    assert "wildcard" in decisions[1].reason


def test_policy_gate_rejects_out_of_scope_targets() -> None:
    request = _request("req-scope", "lookup_request_freshness", "Z_NOT_IN_SCOPE")

    allowed, decisions = PolicyGate().evaluate(
        AgenticReviewPlan(evidence_requests=[request]),
        pack=_sample_pack(),
        budget=AgenticReviewBudget(max_evidence_requests=3),
    )

    assert allowed == []
    assert decisions[0].allowed is False
    assert "outside" in decisions[0].reason


def test_policy_gate_enforces_budget_cap_with_explicit_reject_decisions() -> None:
    requests = [
        _request("req-query", "reparse_query_xml", "ZQ_SALES_MARGIN"),
        _request("req-sql", "reparse_native_sql_view", "ZSQL_VIEW"),
        _request("req-fresh", "lookup_request_freshness", "ZQ_SALES_MARGIN"),
    ]

    allowed, decisions = PolicyGate().evaluate(
        AgenticReviewPlan(evidence_requests=requests),
        pack=_sample_pack(),
        budget=AgenticReviewBudget(max_evidence_requests=2),
    )

    assert [request.id for request in allowed] == ["req-query", "req-sql"]
    assert decisions[-1].allowed is False
    assert "budget" in decisions[-1].reason


def test_policy_gate_dedupes_duplicate_requests_with_reason() -> None:
    requests = [
        _request("req-1", "reparse_query_xml", "ZQ_SALES_MARGIN"),
        _request("req-2", "reparse_query_xml", "ZQ_SALES_MARGIN"),
    ]

    allowed, decisions = PolicyGate().evaluate(
        AgenticReviewPlan(evidence_requests=requests),
        pack=_sample_pack(),
        budget=AgenticReviewBudget(max_evidence_requests=3),
    )

    assert [request.id for request in allowed] == ["req-1"]
    assert decisions[1].allowed is False
    assert "Duplicate" in decisions[1].reason


def test_policy_gate_every_decision_reason_is_non_empty() -> None:
    requests = [
        _request("req-valid", "reparse_query_xml", "ZQ_SALES_MARGIN"),
        _request("req-invalid", "lookup_request_freshness", "*"),
    ]

    _allowed, decisions = PolicyGate().evaluate(
        AgenticReviewPlan(evidence_requests=requests),
        pack=_sample_pack(),
        budget=AgenticReviewBudget(max_evidence_requests=3),
    )

    assert decisions
    assert all(decision.reason.strip() for decision in decisions)


def _request(
    request_id: str,
    enricher: str,
    target: str,
    *,
    reason: str = "Parse-only evidence refresh for scoped review.",
) -> EvidenceRequest:
    return EvidenceRequest.model_construct(
        id=request_id,
        enricher=enricher,
        target=target,
        reason=reason,
        citation_hint="query:ZQ_SALES_MARGIN",
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
        freshness_by_object_id={"ZQ_SALES_MARGIN": {"target_type": "QUERY", "requests": [{}]}},
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
