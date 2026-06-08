from __future__ import annotations

from collections import deque
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bwli.graph import BwEdge, BwGraph, BwNode, Direction


class LineageGraphNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str | None = None
    type: str = "UNKNOWN"
    label: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)


class LineageGraphEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    target: str
    type: str = "depends_on"
    confidence: str = "unknown"
    metadata: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)


class LineageTruncation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_cap_reached: bool = False
    edge_cap_reached: bool = False
    depth_limit_reached: bool = False
    omitted_neighbor_total: int = 0


class BoundedLineageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    snapshot_id: str
    start_id: str
    direction: Direction
    depth: int
    node_cap: int
    edge_cap: int
    nodes: list[LineageGraphNode]
    edges: list[LineageGraphEdge]
    levels: dict[str, int]
    truncated: bool
    truncation: LineageTruncation
    omitted_neighbor_counts: dict[str, int]
    cycles_detected: bool
    evidence_ids: list[str]


def bounded_lineage(
    graph: BwGraph,
    *,
    snapshot_id: str,
    start_id: str,
    direction: Direction | str = Direction.DOWNSTREAM,
    depth: int = 1,
    node_cap: int = 25,
    edge_cap: int = 60,
) -> BoundedLineageResult:
    if depth < 0:
        raise ValueError("depth must be >= 0")
    if node_cap < 1:
        raise ValueError("node_cap must be >= 1")
    if edge_cap < 0:
        raise ValueError("edge_cap must be >= 0")

    direction_value = Direction(direction)
    nodes_by_id = graph.node_map()
    edges_by_id = graph.edge_map()
    if start_id not in nodes_by_id:
        raise ValueError(f"start node not found: {start_id}")

    visited_depth: dict[str, int] = {start_id: 0}
    included_edge_ids: set[str] = set()
    omitted_neighbor_counts: dict[str, int] = {}
    queue: deque[tuple[str, int]] = deque([(start_id, 0)])
    node_cap_reached = False
    edge_cap_reached = False
    depth_limit_reached = False
    cycles_detected = False

    while queue:
        current, current_depth = queue.popleft()
        neighbors = _next_edges(graph, current, direction_value)
        if current_depth >= depth:
            omitted_neighbors = [
                (edge, next_id)
                for edge, next_id in neighbors
                if edge.id not in included_edge_ids
            ]
            if omitted_neighbors:
                depth_limit_reached = True
                _count_omitted(omitted_neighbor_counts, current, len(omitted_neighbors))
            continue

        for edge, next_id in neighbors:
            next_known = next_id in visited_depth
            if not next_known and len(visited_depth) >= node_cap:
                node_cap_reached = True
                _count_omitted(omitted_neighbor_counts, current, 1)
                continue
            if edge.id not in included_edge_ids and len(included_edge_ids) >= edge_cap:
                edge_cap_reached = True
                _count_omitted(omitted_neighbor_counts, current, 1)
                continue
            included_edge_ids.add(edge.id)
            if next_known:
                continue
            visited_depth[next_id] = current_depth + 1
            queue.append((next_id, current_depth + 1))

    ordered_nodes = sorted(
        (nodes_by_id[node_id] for node_id in visited_depth),
        key=lambda node: (visited_depth[node.id], node.id),
    )
    ordered_edges = [edges_by_id[edge_id] for edge_id in sorted(included_edge_ids)]
    cycles_detected = _has_directed_cycle(ordered_edges)
    node_payloads = [_node_payload(node) for node in ordered_nodes]
    edge_payloads = [_edge_payload(edge) for edge in ordered_edges]
    evidence_ids = sorted(
        {
            *[evidence_id for node in node_payloads for evidence_id in node.evidence_ids],
            *[evidence_id for edge in edge_payloads for evidence_id in edge.evidence_ids],
            *[edge.id for edge in ordered_edges],
        }
    )
    omitted_total = sum(omitted_neighbor_counts.values())
    truncation = LineageTruncation(
        node_cap_reached=node_cap_reached,
        edge_cap_reached=edge_cap_reached,
        depth_limit_reached=depth_limit_reached,
        omitted_neighbor_total=omitted_total,
    )
    return BoundedLineageResult(
        snapshot_id=snapshot_id,
        start_id=start_id,
        direction=direction_value,
        depth=depth,
        node_cap=node_cap,
        edge_cap=edge_cap,
        nodes=node_payloads,
        edges=edge_payloads,
        levels=dict(sorted(visited_depth.items(), key=lambda item: (item[1], item[0]))),
        truncated=omitted_total > 0,
        truncation=truncation,
        omitted_neighbor_counts=dict(sorted(omitted_neighbor_counts.items())),
        cycles_detected=cycles_detected,
        evidence_ids=evidence_ids,
    )


def _next_edges(graph: BwGraph, node_id: str, direction: Direction) -> list[tuple[BwEdge, str]]:
    pairs: list[tuple[BwEdge, str]] = []
    if direction in {Direction.DOWNSTREAM, Direction.BOTH}:
        pairs.extend((edge, edge.target) for edge in graph.outgoing(node_id))
    if direction in {Direction.UPSTREAM, Direction.BOTH}:
        pairs.extend((edge, edge.source) for edge in graph.incoming(node_id))
    return sorted(pairs, key=lambda pair: (pair[0].id, pair[1]))


def _node_payload(node: BwNode) -> LineageGraphNode:
    return LineageGraphNode(
        id=node.id,
        name=node.name,
        type=node.type,
        label=node.label,
        metadata={key: value for key, value in node.metadata.items() if key != "evidence_ids"},
        evidence_ids=_metadata_evidence_ids(node.metadata),
    )


def _edge_payload(edge: BwEdge) -> LineageGraphEdge:
    return LineageGraphEdge(
        id=edge.id,
        source=edge.source,
        target=edge.target,
        type=edge.type,
        confidence=edge.confidence,
        metadata={key: value for key, value in edge.metadata.items() if key != "evidence_ids"},
        evidence_ids=_metadata_evidence_ids(edge.metadata),
    )


def _metadata_evidence_ids(metadata: dict[str, Any]) -> list[str]:
    value = metadata.get("evidence_ids")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _count_omitted(counts: dict[str, int], node_id: str, amount: int) -> None:
    counts[node_id] = counts.get(node_id, 0) + amount


def _has_directed_cycle(edges: list[BwEdge]) -> bool:
    adjacency: dict[str, list[str]] = {}
    node_ids: set[str] = set()
    for edge in edges:
        adjacency.setdefault(edge.source, []).append(edge.target)
        node_ids.add(edge.source)
        node_ids.add(edge.target)

    visiting = 1
    visited = 2
    states: dict[str, int] = {}

    def visit(node_id: str) -> bool:
        state = states.get(node_id)
        if state == visiting:
            return True
        if state == visited:
            return False
        states[node_id] = visiting
        if any(visit(next_id) for next_id in adjacency.get(node_id, [])):
            return True
        states[node_id] = visited
        return False

    return any(visit(node_id) for node_id in sorted(node_ids))
