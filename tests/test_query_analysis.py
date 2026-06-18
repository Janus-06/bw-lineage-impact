from __future__ import annotations

from pathlib import Path

from bwli.query_analysis import parse_query_xml


def test_parse_query_extracts_variables_ckf_rkf() -> None:
    result = parse_query_xml(
        Path("tests/fixtures/query-analysis.xml").read_text(encoding="utf-8"),
        source="bw://bw_get_query?queryName=ZQ_SALES_MARGIN",
    )

    assert result.query_id == "ZQ_SALES_MARGIN"
    assert result.description == "Sales Margin Query"
    assert result.variables[0].technical_name == "ZVAR_CALMONTH"
    assert result.variables[0].info_object == "0CALMONTH"
    assert result.calculated_key_figures[0].technical_name == "ZCKF_MARGIN"
    assert result.calculated_key_figures[0].formula == "NET_VALUE - COST"
    assert result.restricted_key_figures[0].technical_name == "ZRKF_CURR_YEAR"
    assert result.restricted_key_figures[0].selections[0]["info_object"] == "0CALYEAR"
    assert result.filters[0]["info_object"] == "0COUNTRY"
    assert result.layout["rows"] == ["0CUSTOMER"]
    assert result.layout["columns"] == ["CKF_MARGIN"]
    assert result.local_members[0]["technical_name"] == "ZLM_MARGIN_RATE"


def test_parse_query_provider_link_resolves_type() -> None:
    result = parse_query_xml(
        Path("tests/fixtures/query-analysis.xml").read_text(encoding="utf-8"),
        source="bw://bw_get_query?queryName=ZQ_SALES_MARGIN",
    )

    assert [(provider.object_id, provider.object_type) for provider in result.providers] == [
        ("ZC_SALES", "HCPR")
    ]


def test_parse_query_ignores_related_self_provider_from_source_query_name() -> None:
    result = parse_query_xml(
        """
        <Qry:queryResource
            xmlns:Qry="http://www.sap.com/bw/modeling/query"
            xmlns:atom="http://www.w3.org/2005/Atom"
            description="Self-linked query without root technical name">
          <atom:link rel="related" href="/sap/bw/modeling/query/zq_self/m" />
        </Qry:queryResource>
        """,
        source="bw://bw_get_query?queryName=ZQ_SELF",
    )

    assert result.query_id == "ZQ_SELF"
    assert result.providers == []


def test_parse_query_does_not_treat_filter_object_name_as_provider() -> None:
    result = parse_query_xml(
        """
        <Qry:queryResource
            xmlns:Qry="http://www.sap.com/bw/modeling/query"
            xmlns:atom="http://www.w3.org/2005/Atom"
            technicalName="ZQ_FILTER_OBJECT">
          <atom:link rel="related" href="/sap/bw/modeling/hcpr/zc_sales/m" />
          <Qry:filters>
            <Qry:filter
                objectName="0CUSTOMER"
                objectType="CHARACTERISTIC"
                operator="EQ"
                value="C1000" />
          </Qry:filters>
          <Qry:layout>
            <Qry:element objectName="0MATERIAL" objectType="CHARACTERISTIC" />
          </Qry:layout>
        </Qry:queryResource>
        """,
        source="bw://bw_get_query?queryName=ZQ_FILTER_OBJECT",
    )

    assert [(provider.object_id, provider.object_type) for provider in result.providers] == [
        ("ZC_SALES", "HCPR")
    ]


def test_parse_query_handles_inactive_fallback_note() -> None:
    result = parse_query_xml(
        "<Qry:queryResource xmlns:Qry='urn:q' description='Inactive only' />",
        source="bw://bw_get_query?queryName=ZQ_INACTIVE&active=false",
    )

    assert result.query_id == "ZQ_INACTIVE"
    assert result.metadata["active"] is False
    assert result.metadata["fallback"] == "inactive"
