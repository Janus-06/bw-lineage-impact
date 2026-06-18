from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from bwli.graph import TourStep
from bwli.llm.explainer import LlmCitationError, _line_has_citation
from bwli.llm.openai_compatible import ChatMessage, LlmChatRequest
from bwli.llm.sanitizer import sanitize_llm_evidence

_MAX_TOUR_EVIDENCE_TEXT_CHARS = 600


@dataclass(frozen=True)
class TourRequestContext:
    request: LlmChatRequest
    domain_summary: dict[str, object]
    allowed_node_ids: set[str]
    allowed_edge_ids: set[str]


def build_guided_tour_request_context(
    *,
    task: str,
    evidence_label: str,
    evidence: dict[str, object],
    citations: list[str],
    domain_summary: dict[str, object],
    allowed_node_ids: set[str],
    allowed_edge_ids: set[str],
    include_korean_summary: bool,
) -> TourRequestContext:
    """Build a strict JSON guided-tour prompt from sanitized deterministic evidence."""

    prompt_payload = {
        "domain_summary": domain_summary,
        "allowed_node_ids": sorted(allowed_node_ids),
        "allowed_edge_ids": sorted(allowed_edge_ids),
        evidence_label: evidence,
    }
    sanitized = sanitize_llm_evidence(prompt_payload)
    bounded_data = _truncate_evidence_text(sanitized.data)
    evidence_json = json.dumps(bounded_data, ensure_ascii=False, indent=2, sort_keys=True)
    citation_text = (
        ", ".join(f"[{citation_id}]" for citation_id in citations) if citations else "none"
    )
    summary_instruction = (
        "Include a concise Korean summary (한국어 요약) in the top-level summary field; "
        "each non-empty summary line must cite allowed evidence."
        if include_korean_summary
        else "Set the top-level summary field to an empty string; summary was not requested."
    )
    return TourRequestContext(
        request=LlmChatRequest(
            messages=[
                ChatMessage(
                    role="system",
                    content=(
                        "You are an SAP BW read-only guided-tour generator. Use only the "
                        "provided deterministic, sanitized evidence. Do not invent BW objects, "
                        "credentials, owners, hosts, transports, activations, writes, execution "
                        "results, or external lookup. Return strict JSON only. Every non-empty "
                        "summary or tour description line must cite at least one provided evidence "
                        "ID as an exact square-bracket token, for example [node:1]."
                    ),
                ),
                ChatMessage(
                    role="user",
                    content=(
                        "Boundary: read-only advisory tour only; no BW write, activation, "
                        "transport, SQL execution, or external data lookup.\n"
                        f"Citation IDs: {citation_text}\n"
                        f"{summary_instruction}\n"
                        "Return JSON object shape: {\"summary\": string, \"tour\": "
                        "[{\"id\": string, \"title\": string, \"description\": string, "
                        "\"node_ids\": string[], \"edge_ids\": string[]}], "
                        "\"citations\": string[]}.\n"
                        "TourStep node_ids and edge_ids must be copied exactly from "
                        "allowed_node_ids and allowed_edge_ids. If evidence is insufficient, "
                        "return an empty tour and empty summary unless a summary was explicitly "
                        "requested.\n"
                        f"Task: produce a compact {task} guided tour for a human BW analyst.\n"
                        "Sanitized deterministic bounded guided-tour evidence JSON:\n"
                        f"{evidence_json}"
                    ),
                ),
            ],
            citation_ids=citations,
            metadata={
                "task": task,
                "include_korean_summary": "true" if include_korean_summary else "false",
            },
        ),
        domain_summary=domain_summary,
        allowed_node_ids=allowed_node_ids,
        allowed_edge_ids=allowed_edge_ids,
    )


def validate_tour_completion_content(
    content: str,
    *,
    citation_ids: list[str],
    allowed_node_ids: set[str],
    allowed_edge_ids: set[str],
    include_korean_summary: bool,
) -> dict[str, object]:
    """Parse and fail-closed validate LLM guided-tour JSON output."""

    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("LLM guided tour response must be a JSON object")

    summary = payload.get("summary", "")
    if not isinstance(summary, str):
        raise ValueError("LLM guided tour summary must be a string")
    if summary.strip() and not include_korean_summary:
        raise ValueError("LLM guided tour summary was not requested")
    _validate_cited_text_lines(summary, citation_ids)

    reported_citations_raw = payload.get("citations", [])
    if not isinstance(reported_citations_raw, list) or not all(
        isinstance(item, str) for item in reported_citations_raw
    ):
        raise ValueError("LLM guided tour citations must be a string list")
    reported_citations = list(reported_citations_raw)
    fabricated = [citation for citation in reported_citations if citation not in citation_ids]
    if fabricated:
        raise LlmCitationError("LLM guided tour cited unknown deterministic evidence IDs")

    tour_raw = payload.get("tour", [])
    if not isinstance(tour_raw, list):
        raise ValueError("LLM guided tour tour field must be a list")

    tour: list[dict[str, object]] = []
    for item in tour_raw:
        step = TourStep.model_validate(item).model_dump(mode="json")
        _validate_step_references(
            step,
            allowed_node_ids=allowed_node_ids,
            allowed_edge_ids=allowed_edge_ids,
        )
        description = step.get("description")
        if isinstance(description, str):
            _validate_cited_text_lines(description, citation_ids)
        tour.append(step)

    return {
        "summary": summary.strip(),
        "tour": tour,
        "citations": reported_citations,
    }


def _validate_step_references(
    step: dict[str, object],
    *,
    allowed_node_ids: set[str],
    allowed_edge_ids: set[str],
) -> None:
    for node_id in _string_list(step.get("node_ids")):
        if node_id not in allowed_node_ids:
            raise ValueError(f"LLM guided tour step referenced unknown node: {node_id}")
    for edge_id in _string_list(step.get("edge_ids")):
        if edge_id not in allowed_edge_ids:
            raise ValueError(f"LLM guided tour step referenced unknown edge: {edge_id}")


def _validate_cited_text_lines(text: str, citation_ids: list[str]) -> None:
    if not text.strip():
        return
    for line in text.splitlines():
        if not line.strip():
            continue
        if not citation_ids or not _line_has_citation(line, citation_ids):
            raise LlmCitationError("LLM guided tour line did not cite deterministic evidence IDs")


def _string_list(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _truncate_evidence_text(value: object) -> object:
    if isinstance(value, str):
        if len(value) <= _MAX_TOUR_EVIDENCE_TEXT_CHARS:
            return value
        return f"{value[:_MAX_TOUR_EVIDENCE_TEXT_CHARS]}…(truncated)"
    if isinstance(value, dict):
        return {key: _truncate_evidence_text(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_truncate_evidence_text(item) for item in value]
    return value
