from __future__ import annotations

import json
from typing import cast

from bwli.config import LlmRuntimeConfig
from bwli.domain import summarize_impact_domain
from bwli.llm.explainer import _validate_completion_citations, _validate_completion_safety
from bwli.llm.openai_compatible import (
    ChatMessage,
    LlmChatRequest,
    LlmCompletion,
    OpenAICompatibleClient,
)
from bwli.llm.sanitizer import sanitize_llm_evidence
from bwli.llm.tour import (
    TourRequestContext,
    build_guided_tour_request_context,
    validate_tour_completion_content,
)

_MAX_AFFECTED_OBJECTS = 60
_MAX_LLM_EVIDENCE_TEXT_CHARS = 600


def build_impact_advice_request(impact_payload: dict[str, object]) -> LlmChatRequest:
    """Build a citation-only advisory prompt from deterministic impact output."""

    evidence, citations = _impact_evidence_payload(impact_payload)
    sanitized = sanitize_llm_evidence(evidence)
    bounded_data = _truncate_evidence_text(sanitized.data)
    evidence_json = json.dumps(bounded_data, ensure_ascii=False, indent=2, sort_keys=True)
    citation_text = ", ".join(citations) if citations else "none"
    return LlmChatRequest(
        messages=[
            ChatMessage(
                role="system",
                content=(
                    "You are an SAP BW lineage impact reviewer. Use only the provided "
                    "deterministic, sanitized impact evidence. Do not invent BW objects, "
                    "credentials, transports, writes, activations, or execution results. "
                    "Every non-empty answer line must cite at least one provided evidence ID "
                    "as a square-bracket token, for example [affected:1]."
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    "Boundary: advisory review notes only; no BW write, activation, transport, "
                    "SQL execution, or external data lookup.\n"
                    f"Citation IDs: {citation_text}\n"
                    "Task: produce concise review notes for a human BW analyst:\n"
                    "1. likely downstream business/technical impact;\n"
                    "2. likely false positives or over-reporting caveats;\n"
                    "3. recommended BW Modeling Tools/Eclipse verification checklist.\n"
                    "Sanitized deterministic impact evidence JSON:\n"
                    f"{evidence_json}"
                ),
            ),
        ],
        citation_ids=citations,
        metadata={"task": "impact_advice"},
    )


def create_impact_advice(
    impact_payload: dict[str, object],
    *,
    runtime: LlmRuntimeConfig,
) -> dict[str, object]:
    """Create advisory impact notes using only sanitized deterministic evidence."""

    chat_request = build_impact_advice_request(impact_payload)
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


def build_impact_tour_request(
    impact_payload: dict[str, object],
    *,
    include_korean_summary: bool = False,
) -> LlmChatRequest:
    """Build a citation-bound guided-tour prompt from deterministic impact evidence."""

    return _build_impact_tour_context(
        impact_payload,
        include_korean_summary=include_korean_summary,
    ).request


def validate_impact_tour_completion(
    content: str,
    impact_payload: dict[str, object],
    *,
    include_korean_summary: bool = False,
) -> dict[str, object]:
    """Validate LLM tour JSON against citations and present impact node/edge IDs."""

    context = _build_impact_tour_context(
        impact_payload,
        include_korean_summary=include_korean_summary,
    )
    return validate_tour_completion_content(
        content,
        citation_ids=context.request.citation_ids,
        allowed_node_ids=context.allowed_node_ids,
        allowed_edge_ids=context.allowed_edge_ids,
        include_korean_summary=include_korean_summary,
    )


def create_impact_tour(
    impact_payload: dict[str, object],
    *,
    runtime: LlmRuntimeConfig,
    include_korean_summary: bool = False,
) -> dict[str, object]:
    """Create and validate a guided impact tour from sanitized deterministic evidence."""

    context = _build_impact_tour_context(
        impact_payload,
        include_korean_summary=include_korean_summary,
    )
    completion = OpenAICompatibleClient(runtime=runtime).chat(context.request)
    _validate_completion_safety(completion)
    tour_result = validate_tour_completion_content(
        completion.content,
        citation_ids=context.request.citation_ids,
        allowed_node_ids=context.allowed_node_ids,
        allowed_edge_ids=context.allowed_edge_ids,
        include_korean_summary=include_korean_summary,
    )
    completion = _mark_citation_validation_passed(completion)
    return {
        "summary": tour_result["summary"],
        "tour": tour_result["tour"],
        "citations": tour_result["citations"],
        "llm_audit": completion.audit.model_dump(mode="json"),
        "domain_summary": context.domain_summary,
    }


def _impact_evidence_payload(
    impact_payload: dict[str, object],
) -> tuple[dict[str, object], list[str]]:
    scenario_raw = impact_payload.get("scenario")
    affected_raw = impact_payload.get("affected_objects")
    bounds_raw = impact_payload.get("lineage_bounds")
    if not isinstance(scenario_raw, dict):
        raise ValueError("impact advice requires a deterministic scenario object")
    if not isinstance(affected_raw, list):
        raise ValueError("impact advice requires deterministic affected_objects")

    citations = ["scenario:change"]
    scenario = _copy_keys(
        cast(dict[str, object], scenario_raw),
        ["object_id", "object_type", "change_type", "field", "value_description", "description"],
    )
    scenario["citation_id"] = "scenario:change"

    affected_objects: list[dict[str, object]] = []
    for index, item in enumerate(affected_raw[:_MAX_AFFECTED_OBJECTS], start=1):
        if not isinstance(item, dict):
            continue
        citation_id = f"affected:{index}"
        citations.append(citation_id)
        affected = _copy_keys(
            cast(dict[str, object], item),
            [
                "object_id",
                "object_type",
                "severity",
                "confidence",
                "reason",
                "manual_verification",
                "evidence_ids",
                "evidence_node_ids",
                "evidence_edge_ids",
            ],
        )
        affected["citation_id"] = citation_id
        affected_objects.append(affected)

    lineage_bounds = (
        _copy_keys(
            cast(dict[str, object], bounds_raw),
            [
                "depth",
                "node_cap",
                "edge_cap",
                "truncated",
                "cycles_detected",
            ],
        )
        if isinstance(bounds_raw, dict)
        else {}
    )
    if isinstance(bounds_raw, dict):
        omitted_counts = bounds_raw.get("omitted_neighbor_counts")
        if isinstance(omitted_counts, dict):
            numeric_counts = [
                int(value)
                for value in omitted_counts.values()
                if isinstance(value, int | float) and not isinstance(value, bool)
            ]
            lineage_bounds["omitted_neighbor_object_count"] = len(omitted_counts)
            lineage_bounds["omitted_neighbor_total"] = sum(numeric_counts)
    evidence: dict[str, object] = {
        "scenario": scenario,
        "affected_objects": affected_objects,
        "affected_object_count": len(affected_raw),
        "lineage_bounds": lineage_bounds,
    }
    if len(affected_raw) > len(affected_objects):
        evidence["affected_object_truncation"] = {
            "truncated": True,
            "included": len(affected_objects),
            "total": len(affected_raw),
        }
    return evidence, citations


def _build_impact_tour_context(
    impact_payload: dict[str, object],
    *,
    include_korean_summary: bool,
) -> TourRequestContext:
    evidence, citations = _impact_evidence_payload(impact_payload)
    sanitized = sanitize_llm_evidence(evidence)
    safe_evidence = (
        cast(dict[str, object], sanitized.data)
        if isinstance(sanitized.data, dict)
        else {}
    )
    domain_summary = summarize_impact_domain(safe_evidence)
    bounded_evidence = _truncate_evidence_text(safe_evidence)
    allowed_node_ids = _impact_node_ids(bounded_evidence)
    allowed_edge_ids = _impact_edge_ids(bounded_evidence)
    return build_guided_tour_request_context(
        task="impact_tour",
        evidence_label="impact_evidence",
        evidence=bounded_evidence if isinstance(bounded_evidence, dict) else {},
        citations=citations,
        domain_summary=domain_summary,
        allowed_node_ids=allowed_node_ids,
        allowed_edge_ids=allowed_edge_ids,
        include_korean_summary=include_korean_summary,
    )


def _impact_node_ids(evidence: object) -> set[str]:
    if not isinstance(evidence, dict):
        return set()
    values: set[str] = set()
    scenario = evidence.get("scenario")
    if isinstance(scenario, dict):
        object_id = scenario.get("object_id")
        if isinstance(object_id, str):
            values.add(object_id)
    affected = evidence.get("affected_objects")
    if isinstance(affected, list):
        for item in affected:
            if not isinstance(item, dict):
                continue
            object_id = item.get("object_id")
            if isinstance(object_id, str):
                values.add(object_id)
    return values


def _impact_edge_ids(evidence: object) -> set[str]:
    if not isinstance(evidence, dict):
        return set()
    values: set[str] = set()
    affected = evidence.get("affected_objects")
    if isinstance(affected, list):
        for item in affected:
            if not isinstance(item, dict):
                continue
            edge_ids = item.get("evidence_edge_ids")
            if isinstance(edge_ids, list):
                values.update(edge_id for edge_id in edge_ids if isinstance(edge_id, str))
    return values


def _mark_citation_validation_passed(completion: LlmCompletion) -> LlmCompletion:
    return completion.model_copy(
        update={
            "audit": completion.audit.model_copy(update={"citation_validation": "passed"})
        }
    )


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
