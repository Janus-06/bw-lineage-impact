from __future__ import annotations

import json
from typing import cast

from bwli.config import LlmRuntimeConfig
from bwli.llm.explainer import _validate_completion_citations, _validate_completion_safety
from bwli.llm.openai_compatible import ChatMessage, LlmChatRequest, OpenAICompatibleClient
from bwli.llm.sanitizer import sanitize_llm_evidence

_MAX_GRAPH_NODES = 80
_MAX_GRAPH_EDGES = 120
_MAX_EVIDENCE_IDS_PER_ITEM = 8
_MAX_LLM_EVIDENCE_TEXT_CHARS = 600
_MAX_LLM_EVIDENCE_JSON_CHARS = 16_000


def build_lineage_advice_request(lineage_payload: dict[str, object]) -> LlmChatRequest:
    """Build a citation-only advisory prompt from deterministic bounded lineage output."""

    evidence, citations = _lineage_evidence_payload(lineage_payload)
    sanitized = sanitize_llm_evidence(evidence)
    bounded_data = _truncate_evidence_text(sanitized.data)
    bounded_data, citations = _cap_serialized_evidence(bounded_data, citations)
    evidence_json = json.dumps(bounded_data, ensure_ascii=False, indent=2, sort_keys=True)
    citation_text = ", ".join(citations) if citations else "none"
    return LlmChatRequest(
        messages=[
            ChatMessage(
                role="system",
                content=(
                    "You are an SAP BW lineage reviewer. Use only the provided deterministic, "
                    "sanitized bounded lineage evidence. Do not invent BW objects, credentials, "
                    "business owners, transports, activations, writes, or execution results. "
                    "Every non-empty answer line must cite at least one provided evidence ID "
                    "as a square-bracket token, for example [node:1] or [edge:1]."
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    "Boundary: advisory graph review notes only; no BW write, activation, "
                    "transport, SQL execution, or external data lookup.\n"
                    f"Citation IDs: {citation_text}\n"
                    "Task: produce concise Korean review notes for a human BW analyst while "
                    "keeping BW terms in English:\n"
                    "1. summarize the visible upstream/downstream Lineage structure;\n"
                    "2. call out central or high-risk objects and uncertain/truncated areas;\n"
                    "3. propose BW Modeling Tools/Eclipse verification questions/checklist.\n"
                    "Sanitized deterministic bounded lineage evidence JSON:\n"
                    f"{evidence_json}"
                ),
            ),
        ],
        citation_ids=citations,
        metadata={"task": "lineage_advice"},
    )


def create_lineage_advice(
    lineage_payload: dict[str, object],
    *,
    runtime: LlmRuntimeConfig,
) -> dict[str, object]:
    """Create advisory lineage notes using only sanitized deterministic graph evidence."""

    chat_request = build_lineage_advice_request(lineage_payload)
    completion = OpenAICompatibleClient(runtime=runtime).chat(chat_request)
    _validate_completion_safety(completion)
    _validate_completion_citations(completion, chat_request.citation_ids)
    completion = completion.model_copy(
        update={
            "audit": completion.audit.model_copy(update={"citation_validation": "passed"})
        }
    )
    return {
        "advice": completion.content,
        "citations": chat_request.citation_ids,
        "llm_audit": completion.audit.model_dump(mode="json"),
    }


def _lineage_evidence_payload(
    lineage_payload: dict[str, object],
) -> tuple[dict[str, object], list[str]]:
    nodes_raw = lineage_payload.get("nodes")
    edges_raw = lineage_payload.get("edges")
    levels_raw = lineage_payload.get("levels")
    if not isinstance(nodes_raw, list):
        raise ValueError("lineage advice requires deterministic nodes")
    if not isinstance(edges_raw, list):
        raise ValueError("lineage advice requires deterministic edges")

    nodes: list[dict[str, object]] = []
    edges: list[dict[str, object]] = []
    citations: list[str] = []

    included_node_ids: set[str] = set()
    for index, item in enumerate(nodes_raw[:_MAX_GRAPH_NODES], start=1):
        if not isinstance(item, dict):
            continue
        citation_id = f"node:{index}"
        citations.append(citation_id)
        node = _copy_keys(
            cast(dict[str, object], item),
            ["id", "name", "label", "type", "evidence_ids"],
        )
        _cap_item_evidence_ids(node)
        node_id = str(node.get("id", ""))
        if node_id:
            included_node_ids.add(node_id)
        if isinstance(levels_raw, dict) and node_id in levels_raw:
            node["level"] = levels_raw[node_id]
        node["citation_id"] = citation_id
        nodes.append(node)

    for index, item in enumerate(edges_raw[:_MAX_GRAPH_EDGES], start=1):
        if not isinstance(item, dict):
            continue
        edge_source = str(item.get("source", ""))
        edge_target = str(item.get("target", ""))
        if edge_source not in included_node_ids or edge_target not in included_node_ids:
            continue
        citation_id = f"edge:{index}"
        citations.append(citation_id)
        edge = _copy_keys(
            cast(dict[str, object], item),
            ["id", "source", "target", "type", "confidence", "evidence_ids"],
        )
        _cap_item_evidence_ids(edge)
        edge["citation_id"] = citation_id
        edges.append(edge)

    bounds = _copy_keys(
        lineage_payload,
        [
            "snapshot_id",
            "start_id",
            "direction",
            "depth",
            "node_cap",
            "edge_cap",
            "truncated",
            "cycles_detected",
        ],
    )
    truncation_raw = lineage_payload.get("truncation")
    if isinstance(truncation_raw, dict):
        bounds["truncation"] = _copy_keys(
            cast(dict[str, object], truncation_raw),
            [
                "node_cap_reached",
                "edge_cap_reached",
                "depth_limit_reached",
                "omitted_neighbor_total",
            ],
        )
    omitted_counts = lineage_payload.get("omitted_neighbor_counts")
    if isinstance(omitted_counts, dict):
        numeric_counts = [
            int(value)
            for value in omitted_counts.values()
            if isinstance(value, int | float) and not isinstance(value, bool)
        ]
        bounds["omitted_neighbor_object_count"] = len(omitted_counts)
        bounds["omitted_neighbor_total"] = sum(numeric_counts)

    evidence: dict[str, object] = {
        "lineage_bounds": bounds,
        "nodes": nodes,
        "edges": edges,
        "node_count": len(nodes_raw),
        "edge_count": len(edges_raw),
    }
    if len(nodes_raw) > len(nodes):
        evidence["node_truncation"] = {
            "truncated": True,
            "included": len(nodes),
            "total": len(nodes_raw),
        }
    if len(edges_raw) > len(edges):
        evidence["edge_truncation"] = {
            "truncated": True,
            "included": len(edges),
            "total": len(edges_raw),
        }
    return evidence, citations


def _cap_item_evidence_ids(item: dict[str, object]) -> None:
    evidence_ids = item.get("evidence_ids")
    if not isinstance(evidence_ids, list):
        return
    string_ids = [str(value) for value in evidence_ids if isinstance(value, str)]
    item["evidence_ids"] = string_ids[:_MAX_EVIDENCE_IDS_PER_ITEM]
    if len(string_ids) > _MAX_EVIDENCE_IDS_PER_ITEM:
        item["evidence_ids_truncated"] = True


def _cap_serialized_evidence(
    value: object,
    citations: list[str],
) -> tuple[object, list[str]]:
    if _serialized_evidence_length(value) <= _MAX_LLM_EVIDENCE_JSON_CHARS:
        return value, citations
    if not isinstance(value, dict):
        return value, citations

    capped: dict[str, object] = dict(value)
    capped["evidence_json_truncation"] = {
        "truncated": True,
        "max_chars": _MAX_LLM_EVIDENCE_JSON_CHARS,
    }
    while _serialized_evidence_length(capped) > _MAX_LLM_EVIDENCE_JSON_CHARS:
        edges = capped.get("edges")
        nodes = capped.get("nodes")
        if isinstance(edges, list) and edges:
            edges.pop()
        elif isinstance(nodes, list) and nodes:
            removed = nodes.pop()
            if isinstance(removed, dict):
                removed_id = removed.get("id")
                if isinstance(removed_id, str) and isinstance(edges, list):
                    capped["edges"] = [
                        edge
                        for edge in edges
                        if not (
                            isinstance(edge, dict)
                            and (
                                edge.get("source") == removed_id
                                or edge.get("target") == removed_id
                            )
                        )
                    ]
        else:
            break
        truncation = capped.get("evidence_json_truncation")
        if isinstance(truncation, dict):
            truncation["included_nodes"] = _list_length(capped.get("nodes"))
            truncation["included_edges"] = _list_length(capped.get("edges"))

    if _serialized_evidence_length(capped) > _MAX_LLM_EVIDENCE_JSON_CHARS:
        capped["nodes"] = []
        capped["edges"] = []
        capped["evidence_json_truncation"] = {
            "truncated": True,
            "max_chars": _MAX_LLM_EVIDENCE_JSON_CHARS,
            "included_nodes": 0,
            "included_edges": 0,
        }

    remaining_citations = _collect_citation_ids(capped)
    return capped, [citation for citation in citations if citation in remaining_citations]


def _list_length(value: object) -> int:
    return len(value) if isinstance(value, list) else 0


def _serialized_evidence_length(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _collect_citation_ids(value: object) -> set[str]:
    citations: set[str] = set()
    if isinstance(value, dict):
        citation_id = value.get("citation_id")
        if isinstance(citation_id, str):
            citations.add(citation_id)
        for item in value.values():
            citations.update(_collect_citation_ids(item))
    elif isinstance(value, list):
        for item in value:
            citations.update(_collect_citation_ids(item))
    return citations


def _copy_keys(source: dict[str, object], keys: list[str]) -> dict[str, object]:
    return {key: source[key] for key in keys if key in source and source[key] is not None}


def _truncate_evidence_text(value: object) -> object:
    if isinstance(value, str):
        if len(value) <= _MAX_LLM_EVIDENCE_TEXT_CHARS:
            return value
        return f"{value[:_MAX_LLM_EVIDENCE_TEXT_CHARS]}…(truncated)"
    if isinstance(value, dict):
        return {key: _truncate_evidence_text(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_truncate_evidence_text(item) for item in value]
    return value
