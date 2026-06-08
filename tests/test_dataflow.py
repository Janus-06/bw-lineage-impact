from __future__ import annotations

import json
from textwrap import dedent

from bwli.dataflow import parse_dataflow_xml, render_dataflow

SAMPLE_DATAFLOW_XML = dedent(
    """
    <dmod:dataFlow>
      <node
        nodeID="1"
        objectName="ZADSO_SRC"
        objectType="ADSO"
        objectSubType=""
        objectDescription="Source ADSO"
        objectStatus="active"
        persistent="true"
        exists="true"
      >
        <targetNode>#///2</targetNode>
      </node>
      <node
        nodeID="2"
        objectName="ZHCPR_MAIN"
        objectType="HCPR"
        objectSubType=""
        objectDescription="Main Provider"
        objectStatus="active"
        persistent="true"
        exists="true"
      >
        <sourceNode>#///1</sourceNode>
        <targetNode>#///3</targetNode>
      </node>
      <node
        nodeID="3"
        objectName="ZQUERY"
        objectType="ELEM"
        objectSubType=""
        objectDescription="Query"
        objectStatus="active"
        persistent="false"
        exists="true"
      >
        <sourceNode>#///2</sourceNode>
      </node>
    </dmod:dataFlow>
    """
).strip()


def test_parse_dataflow_xml_extracts_nodes_and_edges() -> None:
    graph = parse_dataflow_xml(SAMPLE_DATAFLOW_XML)

    assert [node.id for node in graph.nodes] == [1, 2, 3]
    assert graph.nodes[1].object_name == "ZHCPR_MAIN"
    assert graph.edges == ((1, 2), (2, 3))


def test_parse_dataflow_xml_preserves_self_closing_leaf_nodes() -> None:
    graph = parse_dataflow_xml(
        '<dataflow><node nodeID="1" objectName="ZADSO" objectType="ADSO" /></dataflow>'
    )

    assert len(graph.nodes) == 1
    assert graph.nodes[0].object_name == "ZADSO"
    assert graph.nodes[0].object_type == "ADSO"
    assert graph.edges == ()


def test_render_dataflow_mermaid_visualizes_bw_flow() -> None:
    rendered = render_dataflow(SAMPLE_DATAFLOW_XML, output_format="mermaid")

    assert rendered.startswith("flowchart LR")
    assert "N1[\"ADSO ZADSO_SRC" in rendered
    assert "N1 --> N2" in rendered
    assert "N2 --> N3" in rendered


def test_render_dataflow_json_is_deterministic() -> None:
    payload = json.loads(render_dataflow(SAMPLE_DATAFLOW_XML, output_format="json"))

    assert payload["node_count"] == 3
    assert payload["edge_count"] == 2
    assert payload["nodes"][0]["object_type"] == "ADSO"
    assert payload["edges"] == [{"source": 1, "target": 2}, {"source": 2, "target": 3}]
