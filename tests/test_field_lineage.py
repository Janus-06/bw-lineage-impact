from __future__ import annotations

import json
from pathlib import Path

from bwli.cli import app
from bwli.field_lineage import (
    FieldConfidence,
    SqlConfidence,
    parse_native_sql_view,
    parse_transformation_mapping_xml,
    render_field_lineage,
    render_sql_view_evidence,
)

TRANSFORMATION_XML = Path("tests/fixtures/sample-transformation.xml")
SQL_VIEW = Path("tests/fixtures/native_sql_view.sql")


def test_parse_transformation_mapping_xml_classifies_field_edges() -> None:
    document = parse_transformation_mapping_xml(
        TRANSFORMATION_XML.read_text(encoding="utf-8"),
        transformation_id="ZTR_SALES",
        source_object_id="ZADSO_SRC",
        target_object_id="ZADSO_TGT",
    )

    by_target = {edge.target_field: edge for edge in document.field_edges}

    assert by_target["NETVAL"].source_field == "AMOUNT"
    assert by_target["NETVAL"].confidence == FieldConfidence.DIRECT
    assert by_target["LC_AMOUNT"].confidence == FieldConfidence.EXPRESSION
    assert by_target["LC_AMOUNT"].expression == "AMOUNT * RATE"
    assert by_target["RISK_FLAG"].confidence == FieldConfidence.ROUTINE_OPAQUE
    assert by_target["RISK_FLAG"].routine_id == "ROUTINE_001"


def test_parse_transformation_mapping_xml_handles_element_ref_rules() -> None:
    xml_text = """<Transformation>
      <Rule>
        <target><elementRef>#///ZADSO_TGT/NETVAL</elementRef></target>
        <source><elementRef>#///ZADSO_SRC/AMOUNT</elementRef></source>
      </Rule>
    </Transformation>"""

    document = parse_transformation_mapping_xml(
        xml_text,
        transformation_id="ZTR_ELEMENT_REF",
        source_object_id="ZADSO_SRC",
        target_object_id="ZADSO_TGT",
    )

    assert len(document.field_edges) == 1
    assert document.field_edges[0].source_object_id == "ZADSO_SRC"
    assert document.field_edges[0].source_field == "AMOUNT"
    assert document.field_edges[0].target_object_id == "ZADSO_TGT"
    assert document.field_edges[0].target_field == "NETVAL"


def test_parse_transformation_mapping_xml_prefers_expression_over_source_metadata() -> None:
    xml_text = """<Transformation>
      <Mapping
        sourceObject="ZADSO_SRC" sourceField="AMOUNT"
        expression="AMOUNT * RATE"
        targetObject="ZADSO_TGT" targetField="LC_AMOUNT" />
      <Mapping
        sourceObject="ZADSO_SRC" sourceField="FLAG"
        routine="ROUTINE_002"
        targetObject="ZADSO_TGT" targetField="RISK_FLAG" />
    </Transformation>"""

    document = parse_transformation_mapping_xml(
        xml_text,
        transformation_id="ZTR_DERIVED",
        source_object_id="ZADSO_SRC",
        target_object_id="ZADSO_TGT",
    )

    by_target = {edge.target_field: edge for edge in document.field_edges}

    assert by_target["LC_AMOUNT"].confidence == FieldConfidence.EXPRESSION
    assert by_target["RISK_FLAG"].confidence == FieldConfidence.ROUTINE_OPAQUE


def test_parse_transformation_mapping_xml_handles_sap_element_ref_segments_and_steps() -> None:
    xml_text = """<trfn:transformation xmlns:trfn="urn:sap:test">
      <source id="0" name="ZADSO_SRC" type="ADSO">
        <segment id="segment1"><element name="AMOUNT" /></segment>
      </source>
      <target id="0" name="ZADSO_TGT" type="ADSO">
        <segment id="segment1"><element name="NETVAL" /></segment>
      </target>
      <group id="1">
        <rule id="1">
          <source><elementRef>#///source/segment1/AMOUNT</elementRef></source>
          <target><elementRef>#///target/segment1/NETVAL</elementRef></target>
          <step type="ROUTINE" classNameM="ZCL" methodNameM="M" />
        </rule>
      </group>
      <group id="2">
        <rule id="2">
          <source><elementRef>#///source/segment1/RATE</elementRef></source>
          <target><elementRef>#///target/segment1/LC_AMOUNT</elementRef></target>
          <step type="FORMULA" formula="AMOUNT * RATE" />
        </rule>
      </group>
    </trfn:transformation>"""

    document = parse_transformation_mapping_xml(
        xml_text,
        transformation_id="ZTR_SAP_XML",
        source_object_id="ZADSO_SRC",
        target_object_id="ZADSO_TGT",
    )

    by_target = {edge.target_field: edge for edge in document.field_edges}

    assert by_target["NETVAL"].source_object_id == "ZADSO_SRC"
    assert by_target["NETVAL"].target_object_id == "ZADSO_TGT"
    assert by_target["NETVAL"].confidence == FieldConfidence.ROUTINE_OPAQUE
    assert by_target["NETVAL"].routine_id == "ZCL.M"
    assert by_target["LC_AMOUNT"].confidence == FieldConfidence.EXPRESSION
    assert by_target["LC_AMOUNT"].expression == "AMOUNT * RATE"


def test_parse_transformation_mapping_xml_reads_step_child_formula_and_routine_name() -> None:
    xml_text = """<Transformation>
      <rule>
        <source><elementRef>#///source/segment1/AMOUNT</elementRef></source>
        <target><elementRef>#///target/segment1/LC_AMOUNT</elementRef></target>
        <step type="FORMULA"><formula>AMOUNT * RATE</formula></step>
      </rule>
      <rule>
        <source><elementRef>#///source/segment1/FLAG</elementRef></source>
        <target><elementRef>#///target/segment1/RISK_FLAG</elementRef></target>
        <step type="ROUTINE" routineName="ROUTINE_RISK" />
      </rule>
    </Transformation>"""

    document = parse_transformation_mapping_xml(
        xml_text,
        transformation_id="ZTR_STEP_CHILD",
        source_object_id="ZADSO_SRC",
        target_object_id="ZADSO_TGT",
    )

    by_target = {edge.target_field: edge for edge in document.field_edges}

    assert by_target["LC_AMOUNT"].confidence == FieldConfidence.EXPRESSION
    assert by_target["LC_AMOUNT"].expression == "AMOUNT * RATE"
    assert by_target["RISK_FLAG"].confidence == FieldConfidence.ROUTINE_OPAQUE
    assert by_target["RISK_FLAG"].routine_id == "ROUTINE_RISK"


def test_parse_transformation_mapping_xml_reads_kind_specific_step_tags() -> None:
    xml_text = """<Transformation>
      <rule>
        <source><elementRef>#///source/seg/AMOUNT</elementRef></source>
        <target><elementRef>#///target/seg/LC_AMOUNT</elementRef></target>
        <StepFormula><formula>AMOUNT * RATE</formula></StepFormula>
      </rule>
      <rule>
        <target><elementRef>#///target/seg/RISK</elementRef></target>
        <StepRoutine routineName="ROUTINE_RISK" />
      </rule>
    </Transformation>"""

    document = parse_transformation_mapping_xml(
        xml_text,
        transformation_id="ZTR_KIND_STEPS",
        source_object_id="ZADSO_SRC",
        target_object_id="ZADSO_TGT",
    )

    by_target = {edge.target_field: edge for edge in document.field_edges}

    assert by_target["LC_AMOUNT"].confidence == FieldConfidence.EXPRESSION
    assert by_target["LC_AMOUNT"].expression == "AMOUNT * RATE"
    assert by_target["RISK"].confidence == FieldConfidence.ROUTINE_OPAQUE
    assert by_target["RISK"].routine_id == "ROUTINE_RISK"


def test_parse_transformation_mapping_xml_keeps_inline_routine_opaque_without_id() -> None:
    xml_text = """<Transformation>
      <rule>
        <target><elementRef>#///target/seg/RISK</elementRef></target>
        <step type="ROUTINE"><code>RESULT = SOURCE-FLAG.</code></step>
      </rule>
    </Transformation>"""

    document = parse_transformation_mapping_xml(
        xml_text,
        transformation_id="ZTR_INLINE_ROUTINE",
        source_object_id="ZADSO_SRC",
        target_object_id="ZADSO_TGT",
    )

    edge = document.field_edges[0]

    assert edge.confidence == FieldConfidence.ROUTINE_OPAQUE
    assert edge.routine_id == "inline_routine"
    assert edge.expression is None


def test_parse_transformation_mapping_xml_keeps_named_routine_code_opaque() -> None:
    xml_text = """<Transformation>
      <rule>
        <target><elementRef>#///target/seg/RISK</elementRef></target>
        <step type="ROUTINE" routineName="ROUTINE_RISK">
          <code>RESULT = SOURCE-FLAG.</code>
        </step>
      </rule>
    </Transformation>"""

    document = parse_transformation_mapping_xml(
        xml_text,
        transformation_id="ZTR_NAMED_ROUTINE_CODE",
        source_object_id="ZADSO_SRC",
        target_object_id="ZADSO_TGT",
    )

    edge = document.field_edges[0]
    markdown = render_field_lineage(document, output_format="md")

    assert edge.confidence == FieldConfidence.ROUTINE_OPAQUE
    assert edge.routine_id == "ROUTINE_RISK"
    assert edge.expression is None
    assert "ROUTINE_RISK" in markdown
    assert "RESULT = SOURCE-FLAG" not in markdown


def test_parse_transformation_mapping_xml_treats_rule_level_routinetype_as_opaque() -> None:
    xml_text = """<Transformation>
      <rule routinetype="ROUTINE">
        <source><elementRef>#///source/seg/FLAG</elementRef></source>
        <target><elementRef>#///target/seg/RISK</elementRef></target>
        <step><code>RESULT = SOURCE-FLAG.</code></step>
      </rule>
    </Transformation>"""

    document = parse_transformation_mapping_xml(
        xml_text,
        transformation_id="ZTR_RULE_ROUTINETYPE",
        source_object_id="ZADSO_SRC",
        target_object_id="ZADSO_TGT",
    )

    edge = document.field_edges[0]
    markdown = render_field_lineage(document, output_format="md")

    assert edge.confidence == FieldConfidence.ROUTINE_OPAQUE
    assert edge.routine_id == "inline_routine"
    assert edge.expression is None
    assert "RESULT = SOURCE-FLAG" not in markdown


def test_parse_transformation_mapping_xml_preserves_multi_source_formula_edges() -> None:
    xml_text = """<Transformation>
      <rule>
        <source><elementRef>#///source/seg/AMOUNT</elementRef></source>
        <source><elementRef>#///source/seg/RATE</elementRef></source>
        <target><elementRef>#///target/seg/LC_AMOUNT</elementRef></target>
        <step type="FORMULA" formula="AMOUNT * RATE" />
      </rule>
    </Transformation>"""

    document = parse_transformation_mapping_xml(
        xml_text,
        transformation_id="ZTR_MULTI_SOURCE",
        source_object_id="ZADSO_SRC",
        target_object_id="ZADSO_TGT",
    )

    edges = [edge for edge in document.field_edges if edge.target_field == "LC_AMOUNT"]

    assert [edge.source_field for edge in edges] == ["AMOUNT", "RATE"]
    assert {edge.confidence for edge in edges} == {FieldConfidence.EXPRESSION}
    assert {edge.expression for edge in edges} == {"AMOUNT * RATE"}


def test_parse_transformation_mapping_xml_preserves_constant_mapping_evidence() -> None:
    xml_text = """<Transformation>
      <rule>
        <target><elementRef>#///target/seg/LOAD_FLAG</elementRef></target>
        <step type="CONSTANT" constant="X" />
      </rule>
    </Transformation>"""

    document = parse_transformation_mapping_xml(
        xml_text,
        transformation_id="ZTR_CONSTANT",
        source_object_id="ZADSO_SRC",
        target_object_id="ZADSO_TGT",
    )

    edge = document.field_edges[0]

    assert edge.target_field == "LOAD_FLAG"
    assert edge.confidence == FieldConfidence.EXPRESSION
    assert edge.expression == "CONSTANT X"
    assert edge.source_field is None


def test_render_field_lineage_json_is_citation_ready() -> None:
    document = parse_transformation_mapping_xml(
        TRANSFORMATION_XML.read_text(encoding="utf-8"),
        transformation_id="ZTR_SALES",
        source_object_id="ZADSO_SRC",
        target_object_id="ZADSO_TGT",
    )

    payload = json.loads(render_field_lineage(document, output_format="json"))

    assert payload["schema_version"] == "1.0"
    assert payload["transformation_id"] == "ZTR_SALES"
    assert payload["field_edges"][0]["evidence_fragment_id"].startswith("xml:")


def test_render_field_lineage_markdown_shows_derived_evidence_before_source() -> None:
    xml_text = """<Transformation>
      <Mapping
        sourceObject="ZADSO_SRC" sourceField="AMOUNT"
        expression="AMOUNT * RATE"
        targetObject="ZADSO_TGT" targetField="LC_AMOUNT" />
    </Transformation>"""
    document = parse_transformation_mapping_xml(
        xml_text,
        transformation_id="ZTR_MD",
        source_object_id="ZADSO_SRC",
        target_object_id="ZADSO_TGT",
    )

    markdown = render_field_lineage(document, output_format="md")

    assert "`LC_AMOUNT` <= `AMOUNT * RATE`" in markdown
    assert "source=`AMOUNT`" in markdown


def test_parse_native_sql_view_extracts_tables_columns_and_fragments() -> None:
    result = parse_native_sql_view(
        SQL_VIEW.read_text(encoding="utf-8"),
        view_id="ZSQL_SALES_VIEW",
    )

    assert result.confidence == SqlConfidence.SQL_PARSED
    assert {edge.source_object_id for edge in result.reference_edges} == {
        "zsales_fact",
        "zcustomer_dim",
    }
    assert any(ref.table_alias == "s" and ref.column_name == "net_amount" for ref in result.columns)
    assert {fragment.kind for fragment in result.fragments} >= {
        "join",
        "where",
        "group",
        "function",
    }


def test_parse_native_sql_view_filters_create_target_and_cte_aliases() -> None:
    sql = """
    CREATE VIEW ZSQL_SALES_VIEW AS
    WITH recent AS (SELECT * FROM sales.orders)
    SELECT * FROM recent JOIN finance.orders f ON recent.id = f.id
    """

    result = parse_native_sql_view(sql, view_id="ZSQL_SALES_VIEW")

    assert {edge.source_object_id for edge in result.reference_edges} == {
        "sales.orders",
        "finance.orders",
    }


def test_parse_native_sql_view_edge_citations_include_referenced_source_text() -> None:
    sql = """
    CREATE VIEW ZSQL_SALES_VIEW AS
    WITH recent AS (SELECT * FROM sales.orders)
    SELECT * FROM recent JOIN finance.orders f ON recent.id = f.id
    """

    result = parse_native_sql_view(sql, view_id="ZSQL_SALES_VIEW")
    fragments_by_id = {fragment.id: fragment.text for fragment in result.fragments}

    assert {edge.source_object_id for edge in result.reference_edges} == {
        "sales.orders",
        "finance.orders",
    }
    for edge in result.reference_edges:
        cited_text = "\n".join(
            fragments_by_id[fragment_id] for fragment_id in edge.evidence_fragment_ids
        )
        assert edge.source_object_id in cited_text


def test_parse_native_sql_view_preserves_qualified_source_object_ids() -> None:
    sql = """
    SELECT * FROM sales.orders s
    JOIN finance.orders f ON s.id = f.id
    """

    result = parse_native_sql_view(sql, view_id="ZSQL_ORDERS")

    assert [edge.source_object_id for edge in result.reference_edges] == [
        "sales.orders",
        "finance.orders",
    ]


def test_parse_native_sql_view_keeps_qualified_table_when_leaf_name_matches_cte() -> None:
    sql = """
    WITH orders AS (SELECT * FROM raw.orders)
    SELECT * FROM orders
    """

    result = parse_native_sql_view(sql, view_id="ZSQL_CTE_COLLISION")

    assert [edge.source_object_id for edge in result.reference_edges] == ["raw.orders"]


def test_parse_native_sql_view_extracts_create_view_select_fragments() -> None:
    sql = """CREATE VIEW ZSQL_SALES_VIEW AS
    SELECT a, SUM(b) AS total_b FROM raw.sales
    WHERE a > 0 GROUP BY a"""

    result = parse_native_sql_view(sql, view_id="ZSQL_SALES_VIEW")

    assert {fragment.kind for fragment in result.fragments} >= {"where", "group", "function"}


def test_parse_native_sql_view_filters_create_schema_target_with_column_list() -> None:
    sql = """
    CREATE VIEW analytics.orders_view (id, amount) AS
    SELECT id, amount FROM raw.orders
    """

    result = parse_native_sql_view(sql, view_id="ZSQL_ORDERS_VIEW")

    assert [edge.source_object_id for edge in result.reference_edges] == ["raw.orders"]


def test_parse_native_sql_view_keeps_base_table_named_like_cte_alias() -> None:
    sql = "WITH orders AS (SELECT * FROM orders) SELECT * FROM orders"

    result = parse_native_sql_view(sql, view_id="ZSQL_CTE_SAME_NAME")

    assert [edge.source_object_id for edge in result.reference_edges] == ["orders"]


def test_parse_native_sql_view_filters_chained_cte_alias_dependencies() -> None:
    sql = "WITH a AS (SELECT * FROM raw.orders), b AS (SELECT * FROM a) SELECT * FROM b"

    result = parse_native_sql_view(sql, view_id="ZSQL_CHAINED_CTE")

    assert [edge.source_object_id for edge in result.reference_edges] == ["raw.orders"]


def test_parse_native_sql_view_filters_mixed_case_cte_alias_dependencies() -> None:
    sql = "WITH Recent AS (SELECT * FROM raw.orders) SELECT * FROM recent"

    result = parse_native_sql_view(sql, view_id="ZSQL_MIXED_CASE_CTE")

    assert [edge.source_object_id for edge in result.reference_edges] == ["raw.orders"]


def test_parse_native_sql_view_keeps_set_operation_evidence_for_all_sources() -> None:
    sql = "SELECT * FROM raw.actuals UNION ALL SELECT * FROM raw.plan"

    result = parse_native_sql_view(sql, view_id="ZSQL_UNION_VIEW")

    assert [edge.source_object_id for edge in result.reference_edges] == [
        "raw.actuals",
        "raw.plan",
    ]
    evidence_text = "\n".join(fragment.text for fragment in result.fragments)
    assert "raw.actuals" in evidence_text
    assert "raw.plan" in evidence_text


def test_parse_native_sql_view_scopes_nested_cte_aliases_without_hiding_outer_table() -> None:
    sql = "SELECT * FROM x WHERE EXISTS (WITH x AS (SELECT * FROM y) SELECT * FROM x)"

    result = parse_native_sql_view(sql, view_id="ZSQL_NESTED_CTE_SCOPE")

    assert [edge.source_object_id for edge in result.reference_edges] == ["x", "y"]


def test_parse_native_sql_view_collects_nested_filter_and_group_fragments() -> None:
    sql = """
    WITH recent AS (
      SELECT customer_id, SUM(amount) AS amount
      FROM raw.orders
      WHERE calday >= '20240101'
      GROUP BY customer_id
    )
    SELECT * FROM recent
    """

    result = parse_native_sql_view(sql, view_id="ZSQL_NESTED_FILTERS")

    fragment_text = "\n".join(fragment.text for fragment in result.fragments)
    assert any(fragment.kind == "where" for fragment in result.fragments)
    assert any(fragment.kind == "group" for fragment in result.fragments)
    assert "calday" in fragment_text
    assert "customer_id" in fragment_text


def test_sql_view_parser_returns_unknown_with_raw_fragment_on_parse_failure() -> None:
    result = parse_native_sql_view("SELECT FROM", view_id="BROKEN_SQL_VIEW")

    assert result.confidence == SqlConfidence.SQL_UNKNOWN
    assert result.reference_edges == []
    assert result.fragments[0].kind == "raw_sql"


def test_sql_view_parser_returns_unknown_for_sqlglot_command_fallback() -> None:
    sql = "CREATE VIEW ZSQL_TOP AS SELECT TOP 10 * FROM raw.orders"

    result = parse_native_sql_view(sql, view_id="ZSQL_TOP")

    assert result.confidence == SqlConfidence.SQL_UNKNOWN
    assert result.reference_edges == []
    assert result.fragments[0].kind == "raw_sql"
    assert "raw.orders" in result.fragments[0].text


def test_parse_native_sql_view_keeps_unqualified_source_matching_qualified_target_leaf() -> None:
    sql = "CREATE VIEW analytics.orders AS SELECT * FROM orders"

    result = parse_native_sql_view(sql, view_id="analytics.orders")

    assert [edge.source_object_id for edge in result.reference_edges] == ["orders"]


def test_render_sql_view_evidence_markdown_mentions_advisory_only_boundary() -> None:
    result = parse_native_sql_view(
        SQL_VIEW.read_text(encoding="utf-8"),
        view_id="ZSQL_SALES_VIEW",
    )

    markdown = render_sql_view_evidence(result, output_format="md")

    assert "# Native SQL View Evidence" in markdown
    assert "advisory only" in markdown
    assert "ZSQL_SALES_VIEW" in markdown


def test_field_lineage_cli_writes_json(tmp_path, capsys) -> None:
    out = tmp_path / "field_lineage.json"

    exit_code = app(
        [
            "field-lineage",
            "--xml",
            str(TRANSFORMATION_XML),
            "--transformation-id",
            "ZTR_SALES",
            "--source-object",
            "ZADSO_SRC",
            "--target-object",
            "ZADSO_TGT",
            "--out",
            str(out),
        ]
    )

    assert exit_code == 0
    assert "wrote" in capsys.readouterr().out
    assert json.loads(out.read_text(encoding="utf-8"))["field_edges"]


def test_sql_view_cli_writes_markdown(tmp_path, capsys) -> None:
    out = tmp_path / "sql_view.md"

    exit_code = app(
        [
            "sql-view",
            "--id",
            "ZSQL_SALES_VIEW",
            "--sql-file",
            str(SQL_VIEW),
            "--format",
            "md",
            "--out",
            str(out),
        ]
    )

    assert exit_code == 0
    assert "wrote" in capsys.readouterr().out
    assert "Native SQL View Evidence" in out.read_text(encoding="utf-8")
