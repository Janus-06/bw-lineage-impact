from __future__ import annotations

import pytest

from bwli.graph import BwGraph, BwLayer, BwNode
from bwli.layers import assign_layer, assign_layers


@pytest.mark.parametrize(
    ("object_type", "expected"),
    [
        ("LSYS", BwLayer.SOURCE),
        ("source_system", BwLayer.SOURCE),
        ("SOURCESYSTEM", BwLayer.SOURCE),
        ("RSDS", BwLayer.ACQUISITION),
        ("datasource", BwLayer.ACQUISITION),
        ("ADSO", BwLayer.PROVIDER),
        ("ADSOO", BwLayer.PROVIDER),
        ("CUBE", BwLayer.PROVIDER),
        ("INFOCUBE", BwLayer.PROVIDER),
        ("HCPR", BwLayer.PROVIDER),
        ("COMPOSITE_PROVIDER", BwLayer.PROVIDER),
        ("compositeprovider", BwLayer.PROVIDER),
        ("TRFN", BwLayer.TRANSFORMATION),
        ("transformation", BwLayer.TRANSFORMATION),
        ("DTP", BwLayer.TRANSFORMATION),
        ("QUERY", BwLayer.REPORTING),
        ("CKF", BwLayer.REPORTING),
        ("RKF", BwLayer.REPORTING),
        ("STRUCTURE", BwLayer.REPORTING),
        ("report", BwLayer.REPORTING),
        ("RSPC", BwLayer.RUNTIME),
        ("process_chain", BwLayer.RUNTIME),
        ("PROCESSCHAIN", BwLayer.RUNTIME),
        ("REQUEST", BwLayer.RUNTIME),
    ],
)
def test_assign_layer_by_bw_object_type(object_type: str, expected: BwLayer) -> None:
    assert assign_layer(BwNode(id=object_type, type=object_type)) == expected


def test_assign_layer_unknown_type_returns_none() -> None:
    assert assign_layer(BwNode(id="custom", type="CUSTOM_OBJECT")) is None


def test_assign_layers_populates_node_layers_without_mutating_original_graph() -> None:
    graph = BwGraph.model_validate(
        {
            "schema_version": "1.1",
            "nodes": [
                {
                    "id": "SRC",
                    "type": "LSYS",
                    "tags": ["source"],
                    "metadata": {"owner": "original", "nested": {"status": "stable"}},
                },
                {"id": "TR", "type": "TRFN"},
                {"id": "TGT", "type": "UNKNOWN"},
            ],
            "edges": [
                {"id": "e1", "source": "SRC", "target": "TR"},
                {"id": "e2", "source": "TR", "target": "TGT"},
            ],
        }
    )

    assigned = assign_layers(graph)

    assert assigned is not graph
    assert [node.layer for node in graph.nodes] == [None, None, None]
    assert [node.layer for node in assigned.nodes] == [
        BwLayer.SOURCE,
        BwLayer.TRANSFORMATION,
        None,
    ]
    assert [edge.id for edge in assigned.edges] == ["e1", "e2"]

    assigned.nodes[0].tags.append("assigned-only")
    assigned.nodes[0].metadata["owner"] = "assigned"
    assigned.nodes[0].metadata["nested"]["status"] = "changed"

    assert graph.nodes[0].tags == ["source"]
    assert graph.nodes[0].metadata == {"owner": "original", "nested": {"status": "stable"}}


@pytest.mark.parametrize(
    ("object_type", "expected"),
    [
        ("DTPA", BwLayer.TRANSFORMATION),
        ("ALVL", BwLayer.REPORTING),
        ("AGGR_LEVEL", BwLayer.REPORTING),
        ("INFOSOURCE", BwLayer.ACQUISITION),
        ("TRCS", BwLayer.TRANSFORMATION),
    ],
)
def test_assign_layer_additional_bw_modeling_types(object_type: str, expected: BwLayer) -> None:
    assert assign_layer(BwNode(id=object_type, type=object_type)) == expected
