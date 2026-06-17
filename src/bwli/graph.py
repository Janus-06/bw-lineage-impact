from __future__ import annotations

from collections import deque
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Direction(StrEnum):
    UPSTREAM = "upstream"
    DOWNSTREAM = "downstream"
    BOTH = "both"


class BwLayer(StrEnum):
    SOURCE = "source"
    ACQUISITION = "acquisition"
    STAGING = "staging"
    TRANSFORMATION = "transformation"
    PROVIDER = "provider"
    REPORTING = "reporting"
    RUNTIME = "runtime"


class BwNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str | None = None
    type: str = "UNKNOWN"
    label: str | None = None
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)
    complexity: int | None = None
    layer: BwLayer | None = None
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
    weight: float | None = None
    description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphLayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    node_ids: list[str] = Field(default_factory=list)
    description: str | None = None


class TourStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    description: str
    node_ids: list[str] = Field(default_factory=list)
    edge_ids: list[str] = Field(default_factory=list)


_V10_NODE_OMITTED_FIELDS = frozenset({"summary", "tags", "complexity", "layer"})
_V10_EDGE_OMITTED_FIELDS = frozenset({"weight", "description"})


def _has_explicit_v11_graph_fields(payload: dict[str, Any]) -> bool:
    if "layers" in payload or "tour" in payload:
        return True
    return any(_has_v11_node_fields(node) for node in payload.get("nodes", [])) or any(
        _has_v11_edge_fields(edge) for edge in payload.get("edges", [])
    )


def _has_v11_node_fields(node: Any) -> bool:
    if isinstance(node, dict):
        return bool(_V10_NODE_OMITTED_FIELDS.intersection(node))
    return bool(
        getattr(node, "summary", None)
        or getattr(node, "tags", None)
        or getattr(node, "complexity", None) is not None
        or getattr(node, "layer", None) is not None
    )


def _has_v11_edge_fields(edge: Any) -> bool:
    if isinstance(edge, dict):
        return bool(_V10_EDGE_OMITTED_FIELDS.intersection(edge))
    return bool(getattr(edge, "weight", None) is not None or getattr(edge, "description", None))


class BwGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.1"
    nodes: list[BwNode]
    edges: list[BwEdge]
    layers: list[GraphLayer] = Field(default_factory=list)
    tour: list[TourStep] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def infer_legacy_schema_version(cls, data: Any) -> Any:
        if isinstance(data, dict) and "schema_version" not in data:
            payload = dict(data)
            payload["schema_version"] = "1.1" if _has_explicit_v11_graph_fields(payload) else "1.0"
            return payload
        return data

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
            schema_version=self.schema_version,
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

    schema_version: str = "1.1"
    start_id: str
    direction: Direction
    max_depth: int
    nodes: list[BwNode]
    edges: list[BwEdge]
    levels: dict[str, int]

    def to_payload(self) -> dict[str, Any]:
        node_payloads = [node.model_dump(mode="json") for node in self.nodes]
        edge_payloads = [edge.model_dump(mode="json") for edge in self.edges]
        if self.schema_version == "1.0":
            node_payloads = [
                {key: value for key, value in node.items() if key not in _V10_NODE_OMITTED_FIELDS}
                for node in node_payloads
            ]
            edge_payloads = [
                {key: value for key, value in edge.items() if key not in _V10_EDGE_OMITTED_FIELDS}
                for edge in edge_payloads
            ]

        return {
            "schema_version": self.schema_version,
            "start_id": self.start_id,
            "direction": self.direction.value,
            "max_depth": self.max_depth,
            "nodes": node_payloads,
            "edges": edge_payloads,
            "levels": dict(sorted(self.levels.items(), key=lambda item: (item[1], item[0]))),
        }


OutputFormat = Literal["json", "mermaid", "md"]
