from __future__ import annotations

import json
from pathlib import Path

from bwli.cli import app
from bwli.graph import BwGraph
from bwli.impact import (
    ChangeSet,
    ChangeType,
    ImpactSeverity,
    diff_graphs,
    load_changes,
    render_impact_report,
    run_impact_analysis,
)
from bwli.lineage import load_graph

GRAPH = Path("tests/fixtures/sample-graph.json")
GRAPH_AFTER = Path("tests/fixtures/sample-graph-after.json")
CHANGES = Path("tests/fixtures/sample-changes.json")


def test_load_manual_change_file_schema() -> None:
    change_set = load_changes(CHANGES)

    assert isinstance(change_set, ChangeSet)
    assert [change.change_type for change in change_set.changes] == [
        ChangeType.FIELD_REMOVED,
        ChangeType.ROUTINE_CHANGED,
    ]
    assert change_set.changes[0].field == "AMOUNT"


def test_impact_rules_classify_downstream_field_removal_as_high() -> None:
    graph = load_graph(GRAPH)
    report = run_impact_analysis(graph, load_changes(CHANGES), max_depth=3)

    assert not any(
        finding.change_id == "chg-field-remove" and finding.impacted_object_id == "SRC"
        for finding in report.findings
    )
    query_findings = [
        finding
        for finding in report.findings
        if finding.change_id == "chg-field-remove" and finding.impacted_object_id == "QRY"
    ]
    assert len(query_findings) == 1
    assert query_findings[0].severity == ImpactSeverity.HIGH
    assert query_findings[0].confidence == "graph_rule"
    assert "e3" in query_findings[0].evidence_edge_ids


def test_impact_rules_mark_routine_changes_for_manual_verification() -> None:
    graph = load_graph(GRAPH)
    report = run_impact_analysis(graph, load_changes(CHANGES), max_depth=2)

    routine_findings = [
        finding for finding in report.findings if finding.change_id == "chg-routine"
    ]
    assert routine_findings
    assert all(finding.manual_verification for finding in routine_findings)
    assert {finding.severity for finding in routine_findings} == {ImpactSeverity.MEDIUM}


def test_render_impact_json_and_markdown() -> None:
    graph = load_graph(GRAPH)
    report = run_impact_analysis(graph, load_changes(CHANGES), max_depth=1)

    payload = json.loads(render_impact_report(report, output_format="json"))
    markdown = render_impact_report(report, output_format="md")

    assert payload["schema_version"] == "1.0"
    assert "# Impact Report" in markdown
    assert "chg-field-remove" in markdown


def test_snapshot_diff_schema_detects_added_and_removed_graph_items() -> None:
    before = load_graph(GRAPH)
    after = load_graph(GRAPH_AFTER)

    diff = diff_graphs(before, after)

    assert diff.removed_node_ids == ["LOOP", "QRY"]
    assert diff.removed_edge_ids == ["e3", "e4", "e5"]
    assert diff.added_node_ids == []
    assert diff.added_edge_ids == []
    assert diff.changed_node_ids == []
    assert diff.changed_edge_ids == []


def test_snapshot_diff_detects_changed_payloads_for_stable_ids() -> None:
    before = BwGraph.model_validate(
        {
            "nodes": [{"id": "A", "type": "ADSO"}, {"id": "B", "type": "QUERY"}],
            "edges": [{"id": "e1", "source": "A", "target": "B", "type": "feeds"}],
        }
    )
    after = BwGraph.model_validate(
        {
            "nodes": [{"id": "A", "type": "ADSO"}, {"id": "B", "type": "QUERY_V2"}],
            "edges": [{"id": "e1", "source": "A", "target": "B", "type": "loads"}],
        }
    )

    diff = diff_graphs(before, after)

    assert diff.changed_node_ids == ["B"]
    assert diff.changed_edge_ids == ["e1"]


def test_impact_evidence_is_limited_to_impacted_branch() -> None:
    graph = BwGraph.model_validate(
        {
            "nodes": [{"id": "SRC"}, {"id": "A"}, {"id": "B"}],
            "edges": [
                {"id": "ea", "source": "SRC", "target": "A"},
                {"id": "eb", "source": "SRC", "target": "B"},
            ],
        }
    )
    report = run_impact_analysis(graph, load_changes(CHANGES), max_depth=1)

    finding_by_object = {
        finding.impacted_object_id: finding
        for finding in report.findings
        if finding.change_id == "chg-field-remove"
    }

    assert finding_by_object["A"].evidence_node_ids == ["SRC", "A"]
    assert finding_by_object["A"].evidence_edge_ids == ["ea"]
    assert finding_by_object["B"].evidence_node_ids == ["SRC", "B"]
    assert finding_by_object["B"].evidence_edge_ids == ["eb"]


def test_impact_cli_writes_markdown_report(tmp_path, capsys) -> None:
    out = tmp_path / "impact_report.md"

    exit_code = app(
        [
            "impact",
            "--graph",
            str(GRAPH),
            "--changes",
            str(CHANGES),
            "--max-depth",
            "2",
            "--format",
            "md",
            "--out",
            str(out),
        ]
    )

    assert exit_code == 0
    assert "wrote" in capsys.readouterr().out
    assert "Impact Report" in out.read_text(encoding="utf-8")


def test_impact_cli_rejects_partial_real_input(capsys) -> None:
    exit_code = app(["impact", "--graph", str(GRAPH)])

    assert exit_code == 2
    assert "requires both --graph and --changes" in capsys.readouterr().err


def test_diff_cli_writes_snapshot_diff_json(tmp_path, capsys) -> None:
    out = tmp_path / "diff.json"

    exit_code = app(
        [
            "diff",
            "--before",
            str(GRAPH),
            "--after",
            str(GRAPH_AFTER),
            "--out",
            str(out),
        ]
    )

    assert exit_code == 0
    assert "wrote" in capsys.readouterr().out
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["removed_node_ids"] == ["LOOP", "QRY"]


def test_diff_cli_rejects_partial_real_input(capsys) -> None:
    exit_code = app(["diff", "--before", str(GRAPH)])

    assert exit_code == 2
    assert "requires both --before and --after" in capsys.readouterr().err
