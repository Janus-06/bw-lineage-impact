from __future__ import annotations

from bwli.graph import BwEdge, BwGraph, BwLayer, BwNode, TourStep


def test_graph_schema_v11_loads_v10_payload_unchanged() -> None:
    payload = {
        "schema_version": "1.0",
        "nodes": [{"id": "SRC", "name": "Source", "type": "ADSO"}],
        "edges": [],
    }

    graph = BwGraph.model_validate(payload)

    assert graph.schema_version == "1.0"
    assert graph.nodes[0].summary is None
    assert graph.nodes[0].tags == []
    assert graph.nodes[0].complexity is None
    assert graph.nodes[0].layer is None
    assert graph.layers == []
    assert graph.tour == []
    assert graph.model_dump(mode="json", exclude_none=True, exclude_defaults=True) == payload


def test_graph_schema_missing_version_is_legacy_v10() -> None:
    graph = BwGraph.model_validate(
        {
            "nodes": [{"id": "SRC", "name": "Source", "type": "ADSO"}],
            "edges": [],
        }
    )

    assert graph.schema_version == "1.0"
    payload = graph.traverse("SRC").to_payload()
    assert payload["schema_version"] == "1.0"
    assert not {"summary", "tags", "complexity", "layer"}.intersection(payload["nodes"][0])


def test_graph_schema_missing_version_with_v11_fields_infers_v11() -> None:
    graph = BwGraph.model_validate(
        {
            "nodes": [{"id": "SRC", "type": "LSYS", "layer": "source"}],
            "edges": [],
        }
    )

    assert graph.schema_version == "1.1"
    assert graph.nodes[0].layer == BwLayer.SOURCE


def test_bwnode_optional_fields_default_empty() -> None:
    node = BwNode(id="N")

    assert node.summary is None
    assert node.tags == []
    assert node.complexity is None
    assert node.layer is None

    node.tags.append("critical")
    assert BwNode(id="OTHER").tags == []


def test_bwedge_weight_and_description_optional_confidence_preserved() -> None:
    edge = BwEdge(id="e1", source="SRC", target="TGT", confidence="high")

    assert edge.weight is None
    assert edge.description is None
    assert edge.confidence == "high"

    enriched = BwEdge(
        id="e2",
        source="SRC",
        target="TGT",
        confidence="medium",
        weight=0.75,
        description="Feeds target",
    )
    assert enriched.weight == 0.75
    assert enriched.description == "Feeds target"
    assert enriched.confidence == "medium"


def test_tour_step_roundtrip_serialization() -> None:
    graph = BwGraph.model_validate(
        {
            "schema_version": "1.1",
            "nodes": [{"id": "SRC", "type": "LSYS", "layer": "source"}],
            "edges": [],
            "layers": [
                {
                    "id": "source",
                    "name": "Source",
                    "node_ids": ["SRC"],
                    "description": "Source objects",
                }
            ],
            "tour": [
                {
                    "id": "start",
                    "title": "Start here",
                    "description": "Begin with the source object.",
                    "node_ids": ["SRC"],
                    "edge_ids": [],
                }
            ],
        }
    )

    assert graph.nodes[0].layer == BwLayer.SOURCE
    assert graph.tour == [
        TourStep(
            id="start",
            title="Start here",
            description="Begin with the source object.",
            node_ids=["SRC"],
            edge_ids=[],
        )
    ]
    assert graph.model_dump(mode="json")["tour"] == [
        {
            "id": "start",
            "title": "Start here",
            "description": "Begin with the source object.",
            "node_ids": ["SRC"],
            "edge_ids": [],
        }
    ]
