from __future__ import annotations

from bwli.graph import BwGraph
from bwli.traversal import bounded_lineage


def test_bounded_lineage_does_not_flag_converging_dag_as_cycle() -> None:
    graph = BwGraph.model_validate(
        {
            "nodes": [{"id": "A"}, {"id": "B"}, {"id": "C"}, {"id": "D"}],
            "edges": [
                {"id": "ab", "source": "A", "target": "B"},
                {"id": "ac", "source": "A", "target": "C"},
                {"id": "bd", "source": "B", "target": "D"},
                {"id": "cd", "source": "C", "target": "D"},
            ],
        }
    )

    result = bounded_lineage(
        graph,
        snapshot_id="snap",
        start_id="A",
        direction="downstream",
        depth=2,
    )

    assert result.cycles_detected is False


def test_bounded_lineage_both_direction_single_edge_is_not_cycle() -> None:
    graph = BwGraph.model_validate(
        {
            "nodes": [{"id": "A"}, {"id": "B"}],
            "edges": [{"id": "ab", "source": "A", "target": "B"}],
        }
    )

    result = bounded_lineage(
        graph,
        snapshot_id="snap",
        start_id="A",
        direction="both",
        depth=2,
    )

    assert result.cycles_detected is False


def test_bounded_lineage_both_direction_complete_one_hop_is_not_truncated() -> None:
    graph = BwGraph.model_validate(
        {
            "nodes": [{"id": "A"}, {"id": "B"}],
            "edges": [{"id": "ab", "source": "A", "target": "B"}],
        }
    )

    result = bounded_lineage(
        graph,
        snapshot_id="snap",
        start_id="A",
        direction="both",
        depth=1,
    )

    assert [node.id for node in result.nodes] == ["A", "B"]
    assert [edge.id for edge in result.edges] == ["ab"]
    assert result.truncated is False
    assert result.truncation.depth_limit_reached is False
    assert result.omitted_neighbor_counts == {}


def test_bounded_lineage_counts_cycle_closing_boundary_edges_as_truncated() -> None:
    graph = BwGraph.model_validate(
        {
            "nodes": [{"id": "TGT"}, {"id": "QRY"}, {"id": "LOOP"}],
            "edges": [
                {"id": "e3", "source": "TGT", "target": "QRY"},
                {"id": "e4", "source": "QRY", "target": "LOOP"},
                {"id": "e5", "source": "LOOP", "target": "TGT"},
            ],
        }
    )

    result = bounded_lineage(
        graph,
        snapshot_id="snap",
        start_id="TGT",
        direction="downstream",
        depth=2,
    )

    assert [edge.id for edge in result.edges] == ["e3", "e4"]
    assert result.truncated is True
    assert result.truncation.depth_limit_reached is True
    assert result.omitted_neighbor_counts == {"LOOP": 1}
    assert result.cycles_detected is False


def test_bounded_lineage_flags_real_directed_cycle() -> None:
    graph = BwGraph.model_validate(
        {
            "nodes": [{"id": "A"}, {"id": "B"}],
            "edges": [
                {"id": "ab", "source": "A", "target": "B"},
                {"id": "ba", "source": "B", "target": "A"},
            ],
        }
    )

    result = bounded_lineage(
        graph,
        snapshot_id="snap",
        start_id="A",
        direction="downstream",
        depth=2,
    )

    assert result.cycles_detected is True
