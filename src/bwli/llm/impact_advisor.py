from __future__ import annotations

import json
from typing import cast

from bwli.config import LlmRuntimeConfig
from bwli.llm.explainer import _validate_completion_citations
from bwli.llm.openai_compatible import ChatMessage, LlmChatRequest, OpenAICompatibleClient
from bwli.llm.sanitizer import sanitize_llm_evidence

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
