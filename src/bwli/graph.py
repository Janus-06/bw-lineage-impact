from __future__ import annotations

from collections import deque
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Direction(StrEnum):
    UPSTREAM = "upstream"
    DOWNSTREAM = "downstream"
    BOTH = "both"


class BwNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str | None = None
    type: str = "UNKNOWN"
    label: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def display_label(self) -> str:
        return self.label or self.name or self.id


class BwEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    target: str
    type: str = "depends_on"
    confidence: str = "unknown"
    metadata: dict[str, Any] = Field(default_factory=dict)


class BwGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    nodes: list[BwNode]
    edges: list[BwEdge]

    @model_validator(mode="after")
    def validate_edge_endpoints(self) -> Self:
        node_ids = {node.id for node in self.nodes}
        if len(node_ids) != len(self.nodes):
            raise ValueError("duplicate node id in graph")

        edge_ids = {edge.id for edge in self.edges}
        if len(edge_ids) != len(self.edges):
            raise ValueError("duplicate edge id in graph")

        for edge in self.edges:
            if edge.source not in node_ids:
                raise ValueError(f"edge {edge.id} references unknown source node: {edge.source}")
            if edge.target not in node_ids:
                raise ValueError(f"edge {edge.id} references unknown target node: {edge.target}")
        return self

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> BwGraph:
        return cls.model_validate(payload)

    def node_map(self) -> dict[str, BwNode]:
        return {node.id: node for node in self.nodes}

    def edge_map(self) -> dict[str, BwEdge]:
        return {edge.id: edge for edge in self.edges}

    def outgoing(self, node_id: str) -> list[BwEdge]:
        return [edge for edge in self.edges if edge.source == node_id]

    def incoming(self, node_id: str) -> list[BwEdge]:
        return [edge for edge in self.edges if edge.target == node_id]

    def traverse(
        self,
        start_id: str,
        *,
        direction: Direction | str = Direction.DOWNSTREAM,
        max_depth: int = 3,
    ) -> LineageResult:
        if max_depth < 0:
            raise ValueError("max_depth must be >= 0")
        direction_value = Direction(direction)
        nodes_by_id = self.node_map()
        if start_id not in nodes_by_id:
            raise ValueError(f"start node not found: {start_id}")

        visited_depth: dict[str, int] = {start_id: 0}
        included_edge_ids: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(start_id, 0)])

        while queue:
            current, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for edge, next_id in self._next_edges(current, direction_value):
                included_edge_ids.add(edge.id)
                next_depth = depth + 1
                previous_depth = visited_depth.get(next_id)
                if previous_depth is None or next_depth < previous_depth:
                    visited_depth[next_id] = next_depth
                    queue.append((next_id, next_depth))

        ordered_nodes = sorted(
            (nodes_by_id[node_id] for node_id in visited_depth),
            key=lambda node: (visited_depth[node.id], node.id),
        )
        edges_by_id = self.edge_map()
        ordered_edges = [edges_by_id[edge_id] for edge_id in sorted(included_edge_ids)]
        return LineageResult(
            start_id=start_id,
            direction=direction_value,
            max_depth=max_depth,
            nodes=ordered_nodes,
            edges=ordered_edges,
            levels=visited_depth,
        )

    def _next_edges(self, node_id: str, direction: Direction) -> list[tuple[BwEdge, str]]:
        pairs: list[tuple[BwEdge, str]] = []
        if direction in {Direction.DOWNSTREAM, Direction.BOTH}:
            pairs.extend((edge, edge.target) for edge in self.outgoing(node_id))
        if direction in {Direction.UPSTREAM, Direction.BOTH}:
            pairs.extend((edge, edge.source) for edge in self.incoming(node_id))
        return pairs


class LineageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_id: str
    direction: Direction
    max_depth: int
    nodes: list[BwNode]
    edges: list[BwEdge]
    levels: dict[str, int]

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "start_id": self.start_id,
            "direction": self.direction.value,
            "max_depth": self.max_depth,
            "nodes": [node.model_dump(mode="json") for node in self.nodes],
            "edges": [edge.model_dump(mode="json") for edge in self.edges],
            "levels": dict(sorted(self.levels.items(), key=lambda item: (item[1], item[0]))),
        }


OutputFormat = Literal["json", "mermaid", "md"]
