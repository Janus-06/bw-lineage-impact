from __future__ import annotations

import json
from collections import deque
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from bwli.fingerprint import ChangeLevel, classify_node_change
from bwli.graph import BwGraph, BwNode


class ChangeType(StrEnum):
    FIELD_REMOVED = "field_removed"
    FIELD_TYPE_CHANGED = "field_type_changed"
    INFOOBJECT_ATTRIBUTE_CHANGED = "infoobject_attribute_changed"
    INFOOBJECT_TYPE_CHANGED = "infoobject_type_changed"
    ROUTINE_CHANGED = "routine_changed"
    DTP_FILTER_CHANGED = "dtp_filter_changed"
    COMPOSITEPROVIDER_MAPPING_CHANGED = "compositeprovider_mapping_changed"


class ImpactSeverity(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class ChangeEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    object_id: str
    object_type: str
    change_type: ChangeType
    field: str | None = None
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChangeSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    changes: list[ChangeEvent]


class ImpactFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    change_id: str
    impacted_object_id: str
    impacted_object_type: str
    severity: ImpactSeverity
    confidence: str
    reason: str
    evidence_node_ids: list[str]
    evidence_edge_ids: list[str]
    manual_verification: bool = False


class ImpactReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    changes: list[ChangeEvent]
    findings: list[ImpactFinding]


class ChangeGrade(StrEnum):
    SKIP = "SKIP"
    PARTIAL_UPDATE = "PARTIAL_UPDATE"
    ARCHITECTURE_UPDATE = "ARCHITECTURE_UPDATE"
    FULL_UPDATE = "FULL_UPDATE"


class ChangeGradeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grade: ChangeGrade
    change_level: ChangeLevel
    total_nodes: int
    structural_change_count: int
    changed_node_ids: list[str]
    changed_edge_ids: list[str]
    added_node_ids: list[str]
    removed_node_ids: list[str]
    added_edge_ids: list[str]
    removed_edge_ids: list[str]
    cosmetic_node_ids: list[str] = Field(default_factory=list)
    structural_node_ids: list[str] = Field(default_factory=list)
    layer_shift_node_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


FULL_UPDATE_NODE_COUNT_THRESHOLD = 30
FULL_UPDATE_NODE_RATIO_THRESHOLD = 0.5
ARCHITECTURE_UPDATE_NODE_COUNT_THRESHOLD = 10


class SnapshotDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    added_node_ids: list[str]
    removed_node_ids: list[str]
    changed_node_ids: list[str]
    added_edge_ids: list[str]
    removed_edge_ids: list[str]
    changed_edge_ids: list[str]


ImpactOutputFormat = Literal["json", "md"]
PathEvidence = tuple[list[str], list[str]]


def load_changes(path: Path) -> ChangeSet:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        payload = {"changes": payload}
    if not isinstance(payload, dict):
        raise ValueError("change file must contain a JSON object or list")
    return ChangeSet.model_validate(payload)


def run_impact_analysis(graph: BwGraph, changes: ChangeSet, *, max_depth: int = 3) -> ImpactReport:
    findings: list[ImpactFinding] = []
    nodes_by_id = graph.node_map()
    for change in changes.changes:
        if change.object_id not in nodes_by_id:
            findings.append(_unknown_source_finding(change))
            continue
        result = graph.traverse(change.object_id, direction="downstream", max_depth=max_depth)
        path_evidence = _downstream_path_evidence(graph, change.object_id, max_depth=max_depth)
        for node in result.nodes:
            if node.id == change.object_id:
                continue
            evidence_node_ids, evidence_edge_ids = path_evidence[node.id]
            findings.append(
                ImpactFinding(
                    id=f"finding:{change.id}:{node.id}",
                    change_id=change.id,
                    impacted_object_id=node.id,
                    impacted_object_type=node.type,
                    severity=_severity_for(change, node),
                    confidence="graph_rule",
                    reason=_reason_for(change, node),
                    evidence_node_ids=evidence_node_ids,
                    evidence_edge_ids=evidence_edge_ids,
                    manual_verification=_requires_manual_verification(change),
                )
            )
    return ImpactReport(changes=changes.changes, findings=findings)


def diff_graphs(before: BwGraph, after: BwGraph) -> SnapshotDiff:
    before_nodes = {node.id: node.model_dump(mode="json") for node in before.nodes}
    after_nodes = {node.id: node.model_dump(mode="json") for node in after.nodes}
    before_edges = {edge.id: edge.model_dump(mode="json") for edge in before.edges}
    after_edges = {edge.id: edge.model_dump(mode="json") for edge in after.edges}
    before_node_ids = set(before_nodes)
    after_node_ids = set(after_nodes)
    before_edge_ids = set(before_edges)
    after_edge_ids = set(after_edges)
    common_node_ids = before_node_ids & after_node_ids
    common_edge_ids = before_edge_ids & after_edge_ids
    return SnapshotDiff(
        added_node_ids=sorted(after_node_ids - before_node_ids),
        removed_node_ids=sorted(before_node_ids - after_node_ids),
        changed_node_ids=sorted(
            node_id for node_id in common_node_ids if before_nodes[node_id] != after_nodes[node_id]
        ),
        added_edge_ids=sorted(after_edge_ids - before_edge_ids),
        removed_edge_ids=sorted(before_edge_ids - after_edge_ids),
        changed_edge_ids=sorted(
            edge_id for edge_id in common_edge_ids if before_edges[edge_id] != after_edges[edge_id]
        ),
    )


def grade_diff(
    diff: SnapshotDiff,
    *,
    total_nodes: int,
    before: BwGraph | None = None,
    after: BwGraph | None = None,
) -> ChangeGradeResult:
    added_node_ids = sorted(diff.added_node_ids)
    removed_node_ids = sorted(diff.removed_node_ids)
    changed_node_ids = sorted(diff.changed_node_ids)
    added_edge_ids = sorted(diff.added_edge_ids)
    removed_edge_ids = sorted(diff.removed_edge_ids)
    changed_edge_ids = sorted(diff.changed_edge_ids)

    cosmetic_node_ids: list[str] = []
    structural_node_ids: list[str] = []
    layer_shift_node_ids: list[str] = []

    if before is not None and after is not None:
        before_nodes = before.node_map()
        after_nodes = after.node_map()
        for node_id in changed_node_ids:
            before_node = before_nodes.get(node_id)
            after_node = after_nodes.get(node_id)
            if before_node is None or after_node is None:
                structural_node_ids.append(node_id)
                continue
            change_level = classify_node_change(before_node, after_node)
            if change_level == ChangeLevel.STRUCTURAL:
                structural_node_ids.append(node_id)
                if before_node.layer != after_node.layer:
                    layer_shift_node_ids.append(node_id)
            elif change_level == ChangeLevel.COSMETIC:
                cosmetic_node_ids.append(node_id)
    else:
        structural_node_ids = changed_node_ids.copy()

    structural_change_count = (
        len(added_node_ids)
        + len(removed_node_ids)
        + len(structural_node_ids)
        + len(added_edge_ids)
        + len(removed_edge_ids)
        + len(changed_edge_ids)
    )

    if structural_change_count == 0:
        change_level = ChangeLevel.COSMETIC if cosmetic_node_ids else ChangeLevel.NONE
        return ChangeGradeResult(
            grade=ChangeGrade.SKIP,
            change_level=change_level,
            total_nodes=total_nodes,
            structural_change_count=0,
            changed_node_ids=changed_node_ids,
            changed_edge_ids=changed_edge_ids,
            added_node_ids=added_node_ids,
            removed_node_ids=removed_node_ids,
            added_edge_ids=added_edge_ids,
            removed_edge_ids=removed_edge_ids,
            cosmetic_node_ids=cosmetic_node_ids,
            structural_node_ids=structural_node_ids,
            layer_shift_node_ids=layer_shift_node_ids,
            reasons=["cosmetic-only changes"] if cosmetic_node_ids else ["no structural changes"],
        )

    grade, reasons = _grade_structural_change(
        structural_change_count,
        total_nodes=total_nodes,
        has_layer_shift=bool(layer_shift_node_ids),
    )
    return ChangeGradeResult(
        grade=grade,
        change_level=ChangeLevel.STRUCTURAL,
        total_nodes=total_nodes,
        structural_change_count=structural_change_count,
        changed_node_ids=changed_node_ids,
        changed_edge_ids=changed_edge_ids,
        added_node_ids=added_node_ids,
        removed_node_ids=removed_node_ids,
        added_edge_ids=added_edge_ids,
        removed_edge_ids=removed_edge_ids,
        cosmetic_node_ids=cosmetic_node_ids,
        structural_node_ids=structural_node_ids,
        layer_shift_node_ids=layer_shift_node_ids,
        reasons=reasons,
    )


def render_snapshot_diff(diff: SnapshotDiff) -> str:
    return (
        json.dumps(diff.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    )


def render_snapshot_diff_with_grade(diff: SnapshotDiff, grade: ChangeGradeResult) -> str:
    return (
        json.dumps(
            {"diff": diff.model_dump(mode="json"), "grade": grade.model_dump(mode="json")},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _grade_structural_change(
    structural_change_count: int,
    *,
    total_nodes: int,
    has_layer_shift: bool,
) -> tuple[ChangeGrade, list[str]]:
    denominator = max(total_nodes, 1)
    structural_ratio = structural_change_count / denominator
    if structural_change_count > FULL_UPDATE_NODE_COUNT_THRESHOLD:
        return ChangeGrade.FULL_UPDATE, ["structural changes exceed full-update count threshold"]
    if structural_ratio > FULL_UPDATE_NODE_RATIO_THRESHOLD:
        return ChangeGrade.FULL_UPDATE, ["structural changes exceed full-update ratio threshold"]
    if has_layer_shift:
        return ChangeGrade.ARCHITECTURE_UPDATE, ["node layer shifted"]
    if structural_change_count > ARCHITECTURE_UPDATE_NODE_COUNT_THRESHOLD:
        return ChangeGrade.ARCHITECTURE_UPDATE, [
            "structural changes exceed architecture-update count threshold"
        ]
    return ChangeGrade.PARTIAL_UPDATE, ["localized structural changes"]


def render_impact_report(report: ImpactReport, *, output_format: ImpactOutputFormat) -> str:
    if output_format == "json":
        return (
            json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        )
    if output_format == "md":
        return _render_impact_markdown(report)
    raise ValueError(f"unsupported impact output format: {output_format}")


def _render_impact_markdown(report: ImpactReport) -> str:
    lines = [
        "# Impact Report",
        "",
        f"- Changes: {len(report.changes)}",
        f"- Findings: {len(report.findings)}",
        "",
        "## Changes",
        "",
    ]
    for change in report.changes:
        field = f" field=`{change.field}`" if change.field else ""
        lines.append(
            f"- `{change.id}`: `{change.object_id}` ({change.object_type}) "
            f"change=`{change.change_type.value}`{field}"
        )
    lines.extend(["", "## Findings", ""])
    if not report.findings:
        lines.append("- No impacted objects found.")
    for finding in report.findings:
        manual = " manual_verification=true" if finding.manual_verification else ""
        lines.append(
            f"- `{finding.id}`: `{finding.impacted_object_id}` "
            f"severity=`{finding.severity.value}` confidence=`{finding.confidence}`{manual}"
        )
        lines.append(f"  - Reason: {finding.reason}")
        lines.append(f"  - Evidence nodes: {', '.join(finding.evidence_node_ids)}")
        lines.append(f"  - Evidence edges: {', '.join(finding.evidence_edge_ids) or '(none)'}")
    return "\n".join(lines) + "\n"


def _downstream_path_evidence(
    graph: BwGraph,
    start_id: str,
    *,
    max_depth: int,
) -> dict[str, PathEvidence]:
    paths: dict[str, PathEvidence] = {start_id: ([start_id], [])}
    queue: deque[tuple[str, int]] = deque([(start_id, 0)])
    while queue:
        current, depth = queue.popleft()
        if depth >= max_depth:
            continue
        current_nodes, current_edges = paths[current]
        for edge in graph.outgoing(current):
            if edge.target in paths:
                continue
            paths[edge.target] = (
                [*current_nodes, edge.target],
                [*current_edges, edge.id],
            )
            queue.append((edge.target, depth + 1))
    return paths


def _severity_for(change: ChangeEvent, node: BwNode) -> ImpactSeverity:
    if change.change_type in {ChangeType.FIELD_REMOVED, ChangeType.FIELD_TYPE_CHANGED}:
        return ImpactSeverity.HIGH
    if change.change_type == ChangeType.INFOOBJECT_TYPE_CHANGED:
        return ImpactSeverity.HIGH
    if change.change_type == ChangeType.INFOOBJECT_ATTRIBUTE_CHANGED:
        return ImpactSeverity.MEDIUM
    if change.change_type in {ChangeType.ROUTINE_CHANGED, ChangeType.DTP_FILTER_CHANGED}:
        return ImpactSeverity.MEDIUM
    if change.change_type == ChangeType.COMPOSITEPROVIDER_MAPPING_CHANGED:
        return ImpactSeverity.HIGH if node.type.upper() == "QUERY" else ImpactSeverity.MEDIUM
    return ImpactSeverity.UNKNOWN


def _reason_for(change: ChangeEvent, node: BwNode) -> str:
    field = f" field {change.field}" if change.field else ""
    return (
        f"{node.id} is reachable downstream from {change.object_id}; "
        f"change type {change.change_type.value}{field} may affect this object."
    )


def _requires_manual_verification(change: ChangeEvent) -> bool:
    return change.change_type in {
        ChangeType.ROUTINE_CHANGED,
        ChangeType.DTP_FILTER_CHANGED,
        ChangeType.COMPOSITEPROVIDER_MAPPING_CHANGED,
    }


def _unknown_source_finding(change: ChangeEvent) -> ImpactFinding:
    return ImpactFinding(
        id=f"finding:{change.id}:unknown-source",
        change_id=change.id,
        impacted_object_id=change.object_id,
        impacted_object_type=change.object_type,
        severity=ImpactSeverity.UNKNOWN,
        confidence="missing_graph_node",
        reason=f"Changed object {change.object_id} was not found in the supplied graph.",
        evidence_node_ids=[],
        evidence_edge_ids=[],
        manual_verification=True,
    )
