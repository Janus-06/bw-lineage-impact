from __future__ import annotations

import json
from pathlib import Path

import pytest

from bwli.cli import app
from bwli.graph import BwGraph, Direction
from bwli.lineage import load_graph, mermaid_id, render_lineage

FIXTURE = Path("tests/fixtures/sample-graph.json")


def test_traverse_downstream_with_depth_cap_and_cycle_guard() -> None:
    graph = load_graph(FIXTURE)

    result = graph.traverse("SRC", direction=Direction.DOWNSTREAM, max_depth=3)

    assert [node.id for node in result.nodes] == ["SRC", "TR", "TGT", "QRY"]
    assert [edge.id for edge in result.edges] == ["e1", "e2", "e3"]
    assert result.levels == {"SRC": 0, "TR": 1, "TGT": 2, "QRY": 3}


def test_traverse_upstream() -> None:
    graph = load_graph(FIXTURE)

    result = graph.traverse("QRY", direction="upstream", max_depth=3)

    assert [node.id for node in result.nodes] == ["QRY", "TGT", "LOOP", "TR", "SRC"]
    assert [edge.id for edge in result.edges] == ["e1", "e2", "e3", "e4", "e5"]


def test_traverse_both_handles_cycles_without_revisiting_forever() -> None:
    graph = load_graph(FIXTURE)

    result = graph.traverse("TGT", direction="both", max_depth=10)

    assert {node.id for node in result.nodes} == {"SRC", "TR", "TGT", "QRY", "LOOP"}
    assert len(result.edges) == 5
    assert max(result.levels.values()) <= 2


def test_render_json_mermaid_and_markdown() -> None:
    graph = load_graph(FIXTURE)
    result = graph.traverse("SRC", direction="downstream", max_depth=1)

    payload = json.loads(render_lineage(result, output_format="json"))
    mermaid = render_lineage(result, output_format="mermaid")
    markdown = render_lineage(result, output_format="md")

    assert payload["start_id"] == "SRC"
    assert payload["direction"] == "downstream"
    assert "flowchart LR" in mermaid
    assert "SRC" in mermaid
    assert "# Lineage Report" in markdown
    assert "`SRC`" in markdown


def test_graph_rejects_dangling_edge_endpoint() -> None:
    payload = {
        "nodes": [{"id": "A"}],
        "edges": [{"id": "e1", "source": "A", "target": "B"}],
    }

    with pytest.raises(ValueError, match="unknown target node"):
        BwGraph.model_validate(payload)


def test_graph_rejects_duplicate_node_ids() -> None:
    payload = {
        "nodes": [{"id": "A", "type": "ADSO"}, {"id": "A", "type": "QUERY"}],
        "edges": [],
    }

    with pytest.raises(ValueError, match="duplicate node id"):
        BwGraph.model_validate(payload)


def test_graph_rejects_duplicate_edge_ids() -> None:
    payload = {
        "nodes": [{"id": "A"}, {"id": "B"}, {"id": "C"}],
        "edges": [
            {"id": "e1", "source": "A", "target": "B"},
            {"id": "e1", "source": "A", "target": "C"},
        ],
    }

    with pytest.raises(ValueError, match="duplicate edge id"):
        BwGraph.model_validate(payload)


def test_mermaid_renderer_uses_unique_ids_after_sanitizing_collisions() -> None:
    graph = BwGraph.model_validate(
        {
            "nodes": [{"id": "A-B"}, {"id": "A_B"}],
            "edges": [{"id": "e1", "source": "A-B", "target": "A_B"}],
        }
    )

    mermaid = render_lineage(graph.traverse("A-B"), output_format="mermaid")

    assert 'A_B["A-B\\nUNKNOWN"]' in mermaid
    assert 'A_B_2["A_B\\nUNKNOWN"]' in mermaid
    assert 'A_B -- "depends_on" --> A_B_2' in mermaid


def test_markdown_renderer_keeps_object_bullets_single_line() -> None:
    graph = load_graph(FIXTURE)
    markdown = render_lineage(
        graph.traverse("SRC", direction="downstream", max_depth=1),
        output_format="md",
    )

    assert "— Source ADSO\nADSO" not in markdown
    assert "- `SRC` (ADSO) depth=0 — Source ADSO" in markdown


def test_mermaid_id_is_safe_for_bw_names() -> None:
    assert mermaid_id("/BIC/A SALES-01") == "_BIC_A_SALES_01"
    assert mermaid_id("1START") == "n_1START"


def test_lineage_cli_writes_requested_format(tmp_path, capsys) -> None:
    out = tmp_path / "lineage.md"

    exit_code = app(
        [
            "lineage",
            "--graph",
            str(FIXTURE),
            "--object",
            "SRC",
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
    assert "Target ADSO" in out.read_text(encoding="utf-8")
