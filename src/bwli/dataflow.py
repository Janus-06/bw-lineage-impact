from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

DataflowOutputFormat = Literal["json", "md", "mermaid"]


@dataclass(frozen=True)
class DataflowNode:
    id: int
    object_name: str
    object_type: str
    object_subtype: str
    object_description: str
    object_status: str
    persistent: bool
    exists: bool
    source_node_ids: tuple[int, ...]
    target_node_ids: tuple[int, ...]


@dataclass(frozen=True)
class DataflowGraph:
    nodes: tuple[DataflowNode, ...]
    edges: tuple[tuple[int, int], ...]


def parse_dataflow_xml(xml: str) -> DataflowGraph:
    """Parse SAP BW dmod 8TRANSIENT XML into deterministic node/edge evidence."""

    nodes: list[DataflowNode] = []
    for match in re.finditer(r"<node\b([\s\S]*?)>([\s\S]*?)</node>", xml):
        attrs = match.group(1)
        body = match.group(2)
        node_id = _int_attr(attrs, "nodeID")
        if node_id is None:
            continue
        nodes.append(
            DataflowNode(
                id=node_id,
                object_name=_attr(attrs, "objectName"),
                object_type=_attr(attrs, "objectType"),
                object_subtype=_attr(attrs, "objectSubType"),
                object_description=_attr(attrs, "objectDescription"),
                object_status=_attr(attrs, "objectStatus"),
                persistent=_bool_attr(attrs, "persistent"),
                exists=_bool_attr(attrs, "exists", default=True),
                source_node_ids=_refs(body, "sourceNode"),
                target_node_ids=_refs(body, "targetNode"),
            )
        )

    node_ids = {node.id for node in nodes}
    edges: set[tuple[int, int]] = set()
    for node in nodes:
        for source_id in node.source_node_ids:
            if source_id in node_ids:
                edges.add((source_id, node.id))
        for target_id in node.target_node_ids:
            if target_id in node_ids:
                edges.add((node.id, target_id))
    return DataflowGraph(nodes=tuple(sorted(nodes, key=lambda n: n.id)), edges=tuple(sorted(edges)))


def render_dataflow(xml: str, *, output_format: DataflowOutputFormat) -> str:
    graph = parse_dataflow_xml(xml)
    if output_format == "json":
        return json.dumps(_to_payload(graph), ensure_ascii=False, indent=2)
    if output_format == "mermaid":
        return _render_mermaid(graph)
    return _render_markdown(graph)


def _to_payload(graph: DataflowGraph) -> dict[str, object]:
    return {
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "nodes": [
            {
                "id": node.id,
                "object_name": node.object_name,
                "object_type": node.object_type,
                "object_subtype": node.object_subtype,
                "object_description": node.object_description,
                "object_status": node.object_status,
                "persistent": node.persistent,
                "exists": node.exists,
                "source_node_ids": list(node.source_node_ids),
                "target_node_ids": list(node.target_node_ids),
            }
            for node in graph.nodes
        ],
        "edges": [{"source": source, "target": target} for source, target in graph.edges],
    }


def _render_mermaid(graph: DataflowGraph) -> str:
    lines = ["flowchart LR"]
    if not graph.nodes:
        lines.append('  empty["No dataflow nodes"]')
        return "\n".join(lines)
    for node in graph.nodes:
        label = _mermaid_label(node)
        lines.append(f'  N{node.id}["{label}"]')
    for source, target in graph.edges:
        lines.append(f"  N{source} --> N{target}")
    return "\n".join(lines)


def _render_markdown(graph: DataflowGraph) -> str:
    lines = ["# BW Dataflow", "", f"Nodes: {len(graph.nodes)}", f"Edges: {len(graph.edges)}", ""]
    lines.append("## Nodes")
    for node in graph.nodes:
        description = f" — {node.object_description}" if node.object_description else ""
        status = f" ({node.object_status})" if node.object_status else ""
        lines.append(f"- `{node.id}` {node.object_type} `{node.object_name}`{description}{status}")
    if graph.edges:
        lines.extend(["", "## Edges"])
        by_id = {node.id: node for node in graph.nodes}
        for source, target in graph.edges:
            source_name = by_id[source].object_name if source in by_id else str(source)
            target_name = by_id[target].object_name if target in by_id else str(target)
            lines.append(f"- `{source_name}` → `{target_name}`")
    return "\n".join(lines)


def _attr(attrs: str, key: str) -> str:
    match = re.search(rf"\b{re.escape(key)}=\"([^\"]*)\"", attrs)
    return match.group(1) if match else ""


def _int_attr(attrs: str, key: str) -> int | None:
    value = _attr(attrs, key)
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _bool_attr(attrs: str, key: str, *, default: bool = False) -> bool:
    value = _attr(attrs, key).lower()
    if value == "true":
        return True
    if value == "false":
        return False
    return default


def _refs(body: str, tag: str) -> tuple[int, ...]:
    refs: list[int] = []
    for value in re.findall(rf"<{re.escape(tag)}>#///(\d+)</{re.escape(tag)}>", body):
        refs.append(int(value))
    return tuple(refs)


def _mermaid_label(node: DataflowNode) -> str:
    subtype = f":{node.object_subtype}" if node.object_subtype else ""
    description = f"\\n{node.object_description}" if node.object_description else ""
    status = f"\\n{node.object_status}" if node.object_status else ""
    label = f"{node.object_type}{subtype} {node.object_name}{description}{status}"
    return label.replace("\\", "\\\\").replace('"', "'")
