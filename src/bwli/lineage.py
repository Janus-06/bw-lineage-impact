from __future__ import annotations

import json
from pathlib import Path

from bwli.graph import BwEdge, BwGraph, BwNode, LineageResult, OutputFormat


def load_graph(path: Path) -> BwGraph:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("graph file must contain a JSON object")
    return BwGraph.from_payload(payload)


def render_lineage(result: LineageResult, *, output_format: OutputFormat) -> str:
    if output_format == "json":
        return json.dumps(result.to_payload(), ensure_ascii=False, indent=2, sort_keys=True)
    if output_format == "mermaid":
        return render_mermaid(result)
    if output_format == "md":
        return render_markdown(result)
    raise ValueError(f"unsupported output format: {output_format}")


def render_mermaid(result: LineageResult) -> str:
    node_ids = _unique_mermaid_ids([node.id for node in result.nodes])
    lines = ["flowchart LR"]
    for node in result.nodes:
        lines.append(f"  {node_ids[node.id]}[{json.dumps(_node_label(node))}]")
    for edge in result.edges:
        label = edge.type
        lines.append(
            f"  {node_ids[edge.source]} -- {json.dumps(label)} --> "
            f"{node_ids[edge.target]}"
        )
    return "\n".join(lines) + "\n"


def render_markdown(result: LineageResult) -> str:
    lines = [
        "# Lineage Report",
        "",
        f"- Start object: `{result.start_id}`",
        f"- Direction: `{result.direction.value}`",
        f"- Max depth: `{result.max_depth}`",
        f"- Nodes: {len(result.nodes)}",
        f"- Edges: {len(result.edges)}",
        "",
        "## Objects",
        "",
    ]
    for node in result.nodes:
        lines.append(
            f"- `{node.id}` ({node.type}) depth={result.levels[node.id]} — "
            f"{node.display_label}"
        )
    lines.extend(["", "## Relationships", ""])
    if result.edges:
        for edge in result.edges:
            lines.append(
                f"- `{edge.id}`: `{edge.source}` -> `{edge.target}` "
                f"type=`{edge.type}` confidence=`{edge.confidence}`"
            )
    else:
        lines.append("- No relationships in selected traversal.")
    return "\n".join(lines) + "\n"


def mermaid_id(value: str) -> str:
    safe = "".join(char if char.isalnum() else "_" for char in value)
    if not safe or safe[0].isdigit():
        safe = f"n_{safe}"
    return safe


def _unique_mermaid_ids(values: list[str]) -> dict[str, str]:
    used: set[str] = set()
    result: dict[str, str] = {}
    for value in values:
        base = mermaid_id(value)
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used.add(candidate)
        result[value] = candidate
    return result


def _node_label(node: BwNode) -> str:
    return f"{node.display_label}\n{node.type}"


def edge_node_ids(edge: BwEdge) -> tuple[str, str]:
    return edge.source, edge.target
