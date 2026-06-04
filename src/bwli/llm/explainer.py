from __future__ import annotations

import hashlib
import json
import re
from typing import cast

from bwli.config import LlmConfig
from bwli.field_lineage import SqlConfidence, SqlParseResult
from bwli.llm.openai_compatible import (
    ChatMessage,
    LlmChatRequest,
    LlmCompletion,
    OpenAICompatibleClient,
    TransportLike,
)
from bwli.llm.sanitizer import REDACTED, sanitize_llm_evidence, sanitize_text

_CITATION_TOKEN_RE = re.compile(r"\[([^\[\]]+)\]")
_MAX_LLM_EVIDENCE_ITEMS = 80
_MAX_LLM_EVIDENCE_TEXT_CHARS = 600


class LlmCitationError(ValueError):
    """Raised when an LLM completion does not cite deterministic evidence."""


class LlmEvidenceError(ValueError):
    """Raised when evidence is not safe or deterministic enough for LLM use."""


def build_sql_explainer_request(result: SqlParseResult) -> LlmChatRequest:
    """Build a sanitized, citation-only prompt for Native SQL View explanation."""

    _ensure_parsed_sql_evidence(result)
    citation_map = {
        citation_id: _safe_citation_id(citation_id) for citation_id in _citation_ids(result)
    }
    evidence = cast(
        dict[str, object],
        _rewrite_evidence_citation_ids(_sql_result_evidence(result), citation_map),
    )
    bounded_evidence, citation_ids = _bound_evidence_for_prompt(evidence)
    sanitized = sanitize_llm_evidence(bounded_evidence)
    evidence_json = json.dumps(sanitized.data, ensure_ascii=False, indent=2, sort_keys=True)
    citation_text = ", ".join(citation_ids) if citation_ids else "none"
    view_id = sanitize_text(result.view.id)
    return LlmChatRequest(
        messages=[
            ChatMessage(
                role="system",
                content=(
                    "You explain SAP BW Native SQL View deterministic evidence. "
                    "Use only the provided sanitized evidence and citation IDs. "
                    "Every non-empty answer line must cite at least one evidence ID "
                    "as a square-bracket token, for example [sqlfrag:where:1]."
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    f"Native SQL View: {view_id}\n"
                    "Boundary: advisory only; no SQL rewrite; no DB object change; "
                    "no BW write, activation, or transport.\n"
                    f"Citation IDs: {citation_text}\n"
                    "Task: explain the view logic and list manual readability or performance "
                    "checks, each tied to citations.\n"
                    "Sanitized cited evidence JSON:\n"
                    f"{evidence_json}"
                ),
            ),
        ],
        citation_ids=citation_ids,
        metadata={"view_id": view_id, "parser": result.parser},
    )


def explain_sql_with_llm(
    result: SqlParseResult,
    config: LlmConfig,
    *,
    transport: TransportLike | None = None,
) -> LlmCompletion | None:
    """Explain SQL only when the user has explicitly enabled runtime LLM config."""

    runtime = config.resolve_runtime()
    if runtime is None:
        return None
    request = build_sql_explainer_request(result)
    client = OpenAICompatibleClient(runtime=runtime, transport=transport)
    completion = client.chat(request)
    _validate_completion_citations(completion, request.citation_ids)
    return completion.model_copy(
        update={
            "audit": completion.audit.model_copy(update={"citation_validation": "passed"})
        }
    )


def _validate_completion_citations(completion: LlmCompletion, citation_ids: list[str]) -> None:
    if not citation_ids:
        return
    uncited_lines = [
        line
        for line in completion.content.splitlines()
        if line.strip() and not _line_has_citation(line, citation_ids)
    ]
    if uncited_lines:
        raise LlmCitationError("LLM completion line did not cite deterministic evidence IDs")


def _line_has_citation(line: str, citation_ids: list[str]) -> bool:
    allowed = set(citation_ids)
    cited = set(_CITATION_TOKEN_RE.findall(line)) - {REDACTED.strip("[]")}
    return bool(cited & allowed) and cited <= allowed


def _bound_evidence_for_prompt(evidence: dict[str, object]) -> tuple[dict[str, object], list[str]]:
    bounded: dict[str, object] = {}
    included_citation_ids: list[str] = []
    omitted_counts: dict[str, int] = {}
    remaining = _MAX_LLM_EVIDENCE_ITEMS

    for key, value in evidence.items():
        if key in {"reference_edges", "fragments", "columns"} and isinstance(value, list):
            take_count = max(0, min(len(value), remaining))
            items = [_truncate_evidence_text(item) for item in value[:take_count]]
            bounded[key] = items
            remaining -= take_count
            if len(value) > take_count:
                omitted_counts[key] = len(value) - take_count
            for item in items:
                if isinstance(item, dict):
                    citation_id = item.get("citation_id")
                    if isinstance(citation_id, str) and citation_id not in included_citation_ids:
                        included_citation_ids.append(citation_id)
            continue
        bounded[key] = _truncate_evidence_text(value)

    if omitted_counts:
        bounded["evidence_truncation"] = {
            "truncated": True,
            "max_citation_items": _MAX_LLM_EVIDENCE_ITEMS,
            "omitted_counts": omitted_counts,
        }

    return bounded, included_citation_ids


def _truncate_evidence_text(value: object) -> object:
    if isinstance(value, str):
        if len(value) <= _MAX_LLM_EVIDENCE_TEXT_CHARS:
            return value
        return f"{value[:_MAX_LLM_EVIDENCE_TEXT_CHARS]}…[truncated]"
    if isinstance(value, dict):
        return {key: _truncate_evidence_text(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_truncate_evidence_text(item) for item in value]
    return value


def _safe_citation_id(citation_id: str) -> str:
    sanitized = sanitize_text(citation_id)
    cleaned = sanitized.replace(REDACTED, "REDACTED").replace("[", "").replace("]", "")
    if cleaned == citation_id:
        return cleaned
    digest = hashlib.sha256(citation_id.encode("utf-8")).hexdigest()[:8]
    return f"{cleaned}:h{digest}"


def _rewrite_evidence_citation_ids(
    value: object, citation_map: dict[str, str]
) -> object:
    if isinstance(value, dict):
        rewritten: dict[object, object] = {}
        for key, item in value.items():
            if key == "citation_id" and isinstance(item, str):
                rewritten[key] = citation_map.get(item, _safe_citation_id(item))
            elif key == "evidence_fragment_ids" and isinstance(item, list):
                rewritten[key] = [
                    citation_map.get(fragment_id, _safe_citation_id(fragment_id))
                    if isinstance(fragment_id, str)
                    else fragment_id
                    for fragment_id in item
                ]
            else:
                rewritten[key] = _rewrite_evidence_citation_ids(item, citation_map)
        return rewritten
    if isinstance(value, list):
        return [_rewrite_evidence_citation_ids(item, citation_map) for item in value]
    return value


def _ensure_parsed_sql_evidence(result: SqlParseResult) -> None:
    if result.confidence == SqlConfidence.SQL_UNKNOWN or any(
        fragment.kind == "raw_sql" for fragment in result.fragments
    ):
        raise LlmEvidenceError("LLM explanation requires parsed deterministic SQL evidence")


def _sql_result_evidence(result: SqlParseResult) -> dict[str, object]:
    return {
        "view": {
            "id": result.view.id,
            "object_type": result.view.object_type,
            "confidence": result.confidence.value,
            "parser": result.parser,
        },
        "reference_edges": [
            {
                "citation_id": edge.id,
                "source_object_id": edge.source_object_id,
                "target_object_id": edge.target_object_id,
                "type": edge.type,
                "confidence": edge.confidence.value,
                "evidence_fragment_ids": edge.evidence_fragment_ids,
            }
            for edge in result.reference_edges
        ],
        "fragments": [
            {"citation_id": fragment.id, "kind": fragment.kind, "text": fragment.text}
            for fragment in result.fragments
        ],
        "columns": [
            {
                "citation_id": column.id,
                "table_alias": column.table_alias,
                "column_name": column.column_name,
                "expression": column.expression,
            }
            for column in result.columns
        ],
    }


def _citation_ids(result: SqlParseResult) -> list[str]:
    return [
        *[edge.id for edge in result.reference_edges],
        *[fragment.id for fragment in result.fragments],
        *[column.id for column in result.columns],
    ]
