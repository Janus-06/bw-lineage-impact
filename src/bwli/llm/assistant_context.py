from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from bwli.impact_evidence import ImpactEvidencePack
from bwli.llm.sanitizer import REDACTED, sanitize_llm_evidence, sanitize_text

AssistantStatus = Literal["ok", "disabled", "fallback"]
AssistantConfidence = Literal["high", "medium", "low", "unknown"]
AssistantManualTool = Literal["BWMT", "Eclipse", "HANA_Studio", "manual"]
CitationValidationStatus = Literal["not_validated", "passed", "failed"]

_MAX_CONTEXT_ITEMS = 20
_MAX_CONTEXT_BODY_CHARS = 900
_MAX_CONTEXT_TITLE_CHARS = 180
_MAX_PROMPT_CHARS = 2_000
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_./:-]+")
_RAW_PAYLOAD_TEXT_RE = re.compile(
    r"(?i)\b(?:raw[_\s-]*(?:snapshot|payload)(?:[_\s-]*payload)?|snapshot[_\s-]*payload)"
    r"\b\s*[:=]?\s*\S*"
)


class AssistantContextKind(StrEnum):
    OBJECT = "object"
    LINEAGE = "lineage"
    IMPACT = "impact"
    IMPACT_REVIEW = "impact_review"
    FRESHNESS = "freshness"
    MANUAL_CHECK = "manual_check"


class AssistantEvidenceContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=140)
    kind: AssistantContextKind
    title: str = Field(min_length=1, max_length=240)
    body: str = Field(min_length=1, max_length=1_400)
    object_id: str | None = Field(default=None, max_length=240)
    object_type: str | None = Field(default=None, max_length=120)
    citation_id: str | None = Field(default=None, max_length=180)
    source_ids: list[str] = Field(default_factory=list, max_length=40)


class AssistantReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(default="", max_length=3_000)
    snapshot_id: str | None = Field(default=None, max_length=160)
    object_id: str | None = Field(default=None, max_length=240)
    preset: str | None = Field(default=None, max_length=120)
    context: list[AssistantEvidenceContext] = Field(
        default_factory=list,
        max_length=_MAX_CONTEXT_ITEMS,
    )
    max_context_items: int = Field(default=12, ge=1, le=_MAX_CONTEXT_ITEMS)


class AssistantManualCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=140)
    title: str = Field(min_length=1, max_length=240)
    tool: AssistantManualTool
    steps_summary: str = Field(min_length=1, max_length=700)
    related_context_ids: list[str] = Field(default_factory=list, max_length=20)
    citation_ids: list[str] = Field(default_factory=list, max_length=40)


class AssistantSafety(BaseModel):
    model_config = ConfigDict(extra="forbid")

    read_only: bool = True
    no_live_bw_calls: bool = True
    no_bw_query_execution: bool = True
    no_data_preview: bool = True
    no_raw_snapshot_payload: bool = True
    deterministic_authority: Literal["impact.py"] = "impact.py"
    llm_used: bool = False
    citation_validation: CitationValidationStatus = "not_validated"
    fallback_reason: str | None = None


class AssistantReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: AssistantStatus
    answer: str
    citations: list[str] = Field(default_factory=list, max_length=120)
    unknowns: list[str] = Field(default_factory=list, max_length=20)
    confidence: AssistantConfidence
    manual_checks: list[AssistantManualCheck] = Field(default_factory=list, max_length=20)
    safety: AssistantSafety = Field(default_factory=AssistantSafety)


def sanitize_assistant_request(request: AssistantReviewRequest) -> AssistantReviewRequest:
    """Return a bounded, sanitizer-clean request safe for deterministic or LLM review."""

    max_items = min(request.max_context_items, _MAX_CONTEXT_ITEMS)
    sanitized_context = [
        sanitize_assistant_context(item) for item in request.context[:max_items]
    ]
    return request.model_copy(
        update={
            "prompt": _truncate_text(_sanitize_context_text(request.prompt), _MAX_PROMPT_CHARS),
            "snapshot_id": _optional_sanitized_text(request.snapshot_id, 160),
            "object_id": _optional_sanitized_text(request.object_id, 240),
            "preset": _optional_sanitized_text(request.preset, 120),
            "context": sanitized_context,
            "max_context_items": max_items,
        }
    )


def sanitize_assistant_context(context: AssistantEvidenceContext) -> AssistantEvidenceContext:
    """Sanitize and bound one frontend/backend deterministic context item."""

    sanitized_payload = sanitize_llm_evidence(context.model_dump(mode="json"))
    payload = sanitized_payload.data if isinstance(sanitized_payload.data, Mapping) else {}
    raw_id = _text(payload.get("id")) or context.id
    raw_title = _text(payload.get("title")) or context.title
    raw_body = _text(payload.get("body")) or context.body
    raw_object_id = _text(payload.get("object_id"))
    raw_object_type = _text(payload.get("object_type"))
    raw_citation_id = _text(payload.get("citation_id"))
    raw_source_ids = payload.get("source_ids")
    source_ids = (
        [_safe_identifier(str(item)) for item in raw_source_ids if isinstance(item, str)]
        if isinstance(raw_source_ids, list)
        else []
    )
    return AssistantEvidenceContext(
        id=_safe_identifier(raw_id),
        kind=context.kind,
        title=_non_empty(
            _truncate_text(_sanitize_context_text(raw_title), _MAX_CONTEXT_TITLE_CHARS)
        ),
        body=_non_empty(
            _truncate_text(_sanitize_context_text(raw_body), _MAX_CONTEXT_BODY_CHARS)
        ),
        object_id=_optional_safe_identifier(raw_object_id),
        object_type=_optional_sanitized_text(raw_object_type, 120),
        citation_id=_optional_safe_identifier(raw_citation_id),
        source_ids=_unique_texts(source_ids)[:40],
    )


def assistant_citation_ids(contexts: Sequence[AssistantEvidenceContext]) -> list[str]:
    citation_ids: list[str] = []
    for context in contexts:
        _append_unique(citation_ids, f"ctx:{context.id}")
        if context.citation_id:
            _append_unique(citation_ids, context.citation_id)
        for source_id in context.source_ids:
            _append_unique(citation_ids, source_id)
    return citation_ids


def contexts_from_impact_pack(
    pack: ImpactEvidencePack | Mapping[str, object],
    *,
    max_items: int = 12,
) -> list[AssistantEvidenceContext]:
    """Derive assistant-safe contexts from an existing deterministic impact evidence pack."""

    payload = pack.model_dump(mode="json") if isinstance(pack, ImpactEvidencePack) else dict(pack)
    contexts: list[AssistantEvidenceContext] = []
    impact = _mapping(payload.get("impact"))
    findings = _mapping_list(impact.get("findings") if impact else None)
    for index, finding in enumerate(findings, start=1):
        finding_id = _text(finding.get("id")) or f"finding-{index}"
        object_id = _text(finding.get("impacted_object_id")) or "unknown-object"
        object_type = _text(finding.get("impacted_object_type")) or "UNKNOWN"
        severity = _text(finding.get("severity")) or "UNKNOWN"
        reason = _text(finding.get("reason")) or "Deterministic impact finding."
        confidence = _text(finding.get("confidence")) or "unknown"
        manual = bool(finding.get("manual_verification"))
        evidence_ids = [
            *_string_list(finding.get("evidence_node_ids")),
            *_string_list(finding.get("evidence_edge_ids")),
        ]
        contexts.append(
            AssistantEvidenceContext(
                id=f"impact:{_safe_identifier(finding_id)}",
                kind=AssistantContextKind.IMPACT,
                title=f"{severity} impact · {object_id}",
                body=(
                    f"Deterministic impact.py finding for {object_type} {object_id}: "
                    f"{reason} Confidence={confidence}. Manual verification={manual}."
                ),
                object_id=object_id,
                object_type=object_type,
                citation_id=f"impact:{_safe_identifier(finding_id)}",
                source_ids=_unique_texts(evidence_ids),
            )
        )
    for query_item in _mapping_list(payload.get("query_evidence")):
        query_id = _text(query_item.get("query_id")) or "query"
        provider_text = ", ".join(_string_list(query_item.get("provider_object_ids"))) or "none"
        variable_text = ", ".join(_string_list(query_item.get("variable_names"))) or "none"
        query_notes = " ".join(_string_list(query_item.get("manual_check_notes"))) or "none"
        contexts.append(
            AssistantEvidenceContext(
                id=f"query:{_safe_identifier(query_id)}",
                kind=AssistantContextKind.IMPACT_REVIEW,
                title=f"Query evidence · {query_id}",
                body=(
                    f"Query exposure evidence lists providers "
                    f"{provider_text}, "
                    f"variables {variable_text}, "
                    f"filters={query_item.get('filter_count') or 0}. "
                    f"Manual notes: {query_notes}."
                ),
                object_id=query_id,
                object_type="QUERY",
            )
        )
    for sql_item in _mapping_list(payload.get("sql_evidence")):
        view_id = _text(sql_item.get("view_id")) or "sql-view"
        source_ids = [
            *_string_list(sql_item.get("reference_edge_ids")),
            *_string_list(sql_item.get("fragment_ids")),
        ]
        referenced_objects = (
            ", ".join(_string_list(sql_item.get("referenced_object_ids"))) or "none"
        )
        referenced_columns = (
            ", ".join(_string_list(sql_item.get("referenced_column_names"))[:12]) or "none"
        )
        sql_notes = " ".join(_string_list(sql_item.get("manual_check_notes"))) or "none"
        contexts.append(
            AssistantEvidenceContext(
                id=f"sql:{_safe_identifier(view_id)}",
                kind=AssistantContextKind.IMPACT_REVIEW,
                title=f"SQL reference evidence · {view_id}",
                body=(
                    f"Native SQL parse-only evidence references objects "
                    f"{referenced_objects} and columns {referenced_columns}. "
                    f"Manual notes: {sql_notes}."
                ),
                object_id=view_id,
                object_type="NATIVE_SQL_VIEW",
                source_ids=_unique_texts(source_ids),
            )
        )
    for freshness_item in _mapping_list(payload.get("freshness_evidence")):
        object_id = _text(freshness_item.get("object_id")) or "freshness-object"
        freshness_available = bool(freshness_item.get("evidence_available"))
        latest_status = _text(freshness_item.get("latest_status")) or "unknown"
        latest_timestamp = _text(freshness_item.get("latest_timestamp")) or "unknown"
        freshness_notes = (
            " ".join(_string_list(freshness_item.get("manual_check_notes"))) or "none"
        )
        contexts.append(
            AssistantEvidenceContext(
                id=f"freshness:{_safe_identifier(object_id)}",
                kind=AssistantContextKind.FRESHNESS,
                title=f"Freshness evidence · {object_id}",
                body=(
                    f"Request freshness evidence_available={freshness_available}; "
                    f"request_count={freshness_item.get('request_count') or 0}; "
                    f"latest_status={latest_status}; "
                    f"latest_timestamp={latest_timestamp}. "
                    f"Manual notes: {freshness_notes}."
                ),
                object_id=object_id,
                object_type=_text(freshness_item.get("object_type")),
            )
        )
    for gap in _mapping_list(payload.get("manual_verification_gaps")):
        gap_id = _text(gap.get("id")) or "manual-gap"
        contexts.append(
            AssistantEvidenceContext(
                id=f"manual:{_safe_identifier(gap_id)}",
                kind=AssistantContextKind.MANUAL_CHECK,
                title=f"Manual verification gap · {_text(gap.get('source')) or 'manual'}",
                body=_text(gap.get("reason")) or "Manual-only verification is required.",
                object_id=_text(gap.get("object_id")),
                object_type=_text(gap.get("object_type")),
                citation_id=(
                    f"impact:{_safe_identifier(str(gap.get('finding_id')))}"
                    if _text(gap.get("finding_id"))
                    else None
                ),
                source_ids=_string_list(gap.get("evidence_ids")),
            )
        )
    return [sanitize_assistant_context(item) for item in contexts[:max_items]]


def _truncate_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return f"{value[:max_chars]}…[truncated]"


def _optional_sanitized_text(value: str | None, max_chars: int) -> str | None:
    if value is None:
        return None
    text = _truncate_text(_sanitize_context_text(value.strip()), max_chars)
    return text or None


def _optional_safe_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    safe = _safe_identifier(value)
    return safe or None


def _safe_identifier(value: str) -> str:
    sanitized = _sanitize_context_text(value).replace(REDACTED, "REDACTED")
    safe = _SAFE_ID_RE.sub("-", sanitized.strip()).strip("-")
    return safe[:140] or "redacted"


def _sanitize_context_text(value: str) -> str:
    return _RAW_PAYLOAD_TEXT_RE.sub(REDACTED, sanitize_text(value))


def _non_empty(value: str) -> str:
    return value.strip() or "Sanitized evidence was redacted."


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _mapping(value: object) -> Mapping[str, object] | None:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else None


def _mapping_list(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        return []
    return [cast(Mapping[str, object], item) for item in value if isinstance(item, Mapping)]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _unique_texts(values: Iterable[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
        text = value.strip()
        if text and text not in unique:
            unique.append(text)
    return unique


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)
