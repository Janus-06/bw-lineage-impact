from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence

from bwli.graph import BwNode
from bwli.layers import assign_layer
from bwli.redact import redact_text

_MAX_SUMMARY_GROUPS = 12
_MAX_GROUP_CITATIONS = 8
_REDACTED = "[REDACTED]"
_AUTH_HEADER_RE = re.compile(
    r"(?i)\bauthorization\s*[:=]\s*(?:(?:bearer|basic)\s+)?[A-Za-z0-9._~+/=-]+"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_INTERNAL_HOST_RE = re.compile(r"\b[A-Za-z0-9-]+\.(?:corp|internal|lan|local)\b", re.I)
_OPENAI_STYLE_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]+\b")
_SECRET_MARKER_RE = re.compile(
    r"(?i)\b[A-Za-z0-9_-]*(?:api[_-]?key|authorization|password|passwd|secret|token|"
    r"credentials?|user(?:name|id)?)[A-Za-z0-9_-]*\b"
)
_ENV_MARKER_RE = re.compile(r"(?i)\b(?:mandt|client|bw[_-]?client|sap[_-]?client)\b")
_DOMAIN_METADATA_KEYS = (
    "infoarea",
    "info_area",
    "info_area_name",
    "infoarea_name",
    "info_area_id",
    "application_component",
    "business_domain",
    "domain",
)
_GENERIC_DOMAIN_TOKENS = {
    "adso",
    "advanced",
    "composite",
    "compositeprovider",
    "cube",
    "cycle",
    "data",
    "dtp",
    "flow",
    "guard",
    "hcpr",
    "infocube",
    "object",
    "provider",
    "query",
    "report",
    "source",
    "target",
    "test",
    "transformation",
    "trfn",
}


def summarize_lineage_domain(lineage_payload: Mapping[str, object]) -> dict[str, object]:
    """Summarize a visible lineage graph with deterministic, citation-tied counts."""

    nodes_raw = lineage_payload.get("nodes")
    edges_raw = lineage_payload.get("edges")
    nodes = (
        [item for item in nodes_raw if isinstance(item, Mapping)]
        if isinstance(nodes_raw, list)
        else []
    )
    edge_count = len(edges_raw) if isinstance(edges_raw, list) else 0
    return summarize_domain_nodes(nodes, edge_count=edge_count)


def summarize_impact_domain(impact_payload: Mapping[str, object]) -> dict[str, object]:
    """Summarize deterministic impact evidence without requiring a live graph."""

    nodes: list[Mapping[str, object]] = []
    scenario_raw = impact_payload.get("scenario")
    if isinstance(scenario_raw, Mapping):
        scenario = {
            "id": scenario_raw.get("object_id"),
            "type": scenario_raw.get("object_type"),
            "name": scenario_raw.get("object_id"),
            "citation_id": "scenario:change",
        }
        nodes.append(scenario)

    affected_raw = impact_payload.get("affected_objects")
    if isinstance(affected_raw, list):
        for index, item in enumerate(affected_raw, start=1):
            if not isinstance(item, Mapping):
                continue
            nodes.append(
                {
                    "id": item.get("object_id"),
                    "type": item.get("object_type"),
                    "name": item.get("object_id"),
                    "citation_id": item.get("citation_id") or f"affected:{index}",
                }
            )

    edge_ids: set[str] = set()
    if isinstance(affected_raw, list):
        for item in affected_raw:
            if not isinstance(item, Mapping):
                continue
            evidence_edge_ids = item.get("evidence_edge_ids")
            if isinstance(evidence_edge_ids, list):
                edge_ids.update(value for value in evidence_edge_ids if isinstance(value, str))
    return summarize_domain_nodes(nodes, edge_count=len(edge_ids))


def summarize_domain_nodes(
    nodes: Sequence[Mapping[str, object]],
    *,
    edge_count: int,
) -> dict[str, object]:
    """Return compact domain/layer/type counts from already-bounded visible nodes."""

    object_type_counts: Counter[str] = Counter()
    layer_counts: Counter[str] = Counter()
    layer_citations: dict[str, list[str]] = defaultdict(list)
    type_citations: dict[str, list[str]] = defaultdict(list)
    group_counts: Counter[str] = Counter()
    group_citations: dict[str, list[str]] = defaultdict(list)
    all_citations: list[str] = []

    for index, node in enumerate(nodes, start=1):
        citation_id = _citation_id(node, index)
        if citation_id:
            all_citations.append(citation_id)

        object_type = _safe_text(node.get("type")) or "UNKNOWN"
        object_type_counts[object_type] += 1
        _append_citation(type_citations[object_type], citation_id)

        layer = _node_layer(node)
        layer_counts[layer] += 1
        _append_citation(layer_citations[layer], citation_id)

        domain_group = _domain_group(node, fallback_type=object_type)
        group_counts[domain_group] += 1
        _append_citation(group_citations[domain_group], citation_id)

    summary: dict[str, object] = {
        "node_count": len(nodes),
        "edge_count": edge_count,
        "layers": [
            {
                "layer": layer,
                "count": count,
                "citation_ids": layer_citations[layer][:_MAX_GROUP_CITATIONS],
            }
            for layer, count in _ordered_counts(layer_counts)
        ],
        "object_types": [
            {
                "type": object_type,
                "count": count,
                "citation_ids": type_citations[object_type][:_MAX_GROUP_CITATIONS],
            }
            for object_type, count in _ordered_counts(object_type_counts)
        ],
        "domain_groups": [
            {
                "name": group,
                "count": count,
                "citation_ids": group_citations[group][:_MAX_GROUP_CITATIONS],
            }
            for group, count in _ordered_counts(group_counts)[:_MAX_SUMMARY_GROUPS]
        ],
        "citation_ids": _unique_preserving_order(all_citations),
    }
    sanitized = _sanitize_domain_value(summary)
    return sanitized if isinstance(sanitized, dict) else summary


def _citation_id(node: Mapping[str, object], index: int) -> str:
    value = node.get("citation_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return f"node:{index}"


def _node_layer(node: Mapping[str, object]) -> str:
    value = node.get("layer")
    if isinstance(value, str) and value.strip():
        return _sanitize_domain_text(value.strip().lower())
    object_type = _safe_text(node.get("type")) or "UNKNOWN"
    node_id = _safe_text(node.get("id")) or f"domain-node-{object_type}"
    try:
        inferred = assign_layer(BwNode(id=node_id, type=object_type))
    except ValueError:
        inferred = None
    return inferred.value if inferred is not None else "unknown"


def _domain_group(node: Mapping[str, object], *, fallback_type: str) -> str:
    metadata = node.get("metadata")
    if isinstance(metadata, Mapping):
        for key in _DOMAIN_METADATA_KEYS:
            value = _lookup_case_insensitive(metadata, key)
            text = _safe_text(value)
            if text:
                return text

    for key in ("name", "label", "id"):
        text = _safe_text(node.get(key))
        if not text:
            continue
        token = _domain_token(text)
        if token:
            return token
    return f"type:{fallback_type}"


def _lookup_case_insensitive(mapping: Mapping[object, object], target: str) -> object | None:
    normalized_target = _normalize_key(target)
    for key, value in mapping.items():
        if _normalize_key(str(key)) == normalized_target:
            return value
    return None


def _domain_token(value: str) -> str | None:
    segments = [segment for segment in re.split(r"[^A-Za-z0-9]+", value) if segment]
    for segment in segments:
        normalized = segment.lower()
        if len(segment) < 2 or normalized in _GENERIC_DOMAIN_TOKENS:
            continue
        if segment.isdigit():
            continue
        return _sanitize_domain_text(segment)
    return None


def _safe_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = _sanitize_domain_text(value.strip())
    return text or None


def _sanitize_domain_value(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _sanitize_domain_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_domain_value(item) for item in value]
    if isinstance(value, str):
        return _sanitize_domain_text(value)
    return value


def _sanitize_domain_text(value: str) -> str:
    redacted = redact_text(value)
    redacted = _AUTH_HEADER_RE.sub(_REDACTED, redacted)
    redacted = _BEARER_RE.sub(_REDACTED, redacted)
    redacted = _EMAIL_RE.sub(_REDACTED, redacted)
    redacted = _IPV4_RE.sub(_REDACTED, redacted)
    redacted = _INTERNAL_HOST_RE.sub(_REDACTED, redacted)
    redacted = _OPENAI_STYLE_KEY_RE.sub(_REDACTED, redacted)
    redacted = _SECRET_MARKER_RE.sub(_REDACTED, redacted)
    return _ENV_MARKER_RE.sub(_REDACTED, redacted)


def _append_citation(values: list[str], citation_id: str) -> None:
    if citation_id and citation_id not in values:
        values.append(citation_id)


def _ordered_counts(counter: Counter[str]) -> list[tuple[str, int]]:
    return sorted(counter.items(), key=lambda item: (-item[1], item[0]))


def _unique_preserving_order(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _normalize_key(value: str) -> str:
    return "".join(char for char in value.lower() if char.isalnum())
