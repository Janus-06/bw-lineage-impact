from __future__ import annotations

import json
import re
from collections.abc import Sequence

from bwli.config import LlmRuntimeConfig
from bwli.llm.assistant_context import (
    AssistantContextKind,
    AssistantEvidenceContext,
    AssistantManualCheck,
    AssistantReviewRequest,
    AssistantReviewResponse,
    AssistantSafety,
    assistant_citation_ids,
    sanitize_assistant_request,
)
from bwli.llm.explainer import LlmCitationError, LlmEvidenceError, _validate_completion_safety
from bwli.llm.openai_compatible import (
    ChatMessage,
    LlmChatRequest,
    OpenAICompatibleClient,
    TransportLike,
)
from bwli.llm.sanitizer import REDACTED, sanitize_llm_evidence, sanitize_text

_CITATION_TOKEN_RE = re.compile(r"\[([^\[\]]+)\]")
_UNKNOWN_LINE_RE = re.compile(
    r"(?i)\b(unknown|not available|insufficient evidence|needs? manual verification|manual check)\b"
)


def review_assistant(
    request: AssistantReviewRequest,
    *,
    runtime: LlmRuntimeConfig | None = None,
    transport: TransportLike | None = None,
) -> AssistantReviewResponse:
    """Answer Ask BW / Review questions from supplied deterministic context only."""

    safe_request = sanitize_assistant_request(request)
    if runtime is None:
        return deterministic_assistant_fallback(
            safe_request,
            status="disabled",
            fallback_reason="LLM runtime is disabled or unconfigured.",
        )

    chat_request = build_assistant_review_request(safe_request)
    try:
        completion = OpenAICompatibleClient(runtime=runtime, transport=transport).chat(chat_request)
        _validate_completion_safety(completion)
        answer = sanitize_text(completion.content).strip()
        validate_assistant_answer_citations(answer, chat_request.citation_ids)
    except Exception as exc:
        reason = (
            str(exc)
            if isinstance(exc, LlmCitationError | LlmEvidenceError | ValueError)
            else type(exc).__name__
        )
        return deterministic_assistant_fallback(
            safe_request,
            status="fallback",
            fallback_reason=f"LLM answer failed assistant safety validation: {reason}",
        )

    citations = _used_citations(answer, chat_request.citation_ids)
    return AssistantReviewResponse(
        status="ok",
        answer=answer,
        citations=citations,
        unknowns=_default_unknowns(safe_request.context),
        confidence="medium" if citations else "low",
        manual_checks=derive_manual_checks(safe_request),
        safety=AssistantSafety(
            llm_used=True,
            citation_validation="passed",
        ),
    )


def build_assistant_review_request(request: AssistantReviewRequest) -> LlmChatRequest:
    """Build a citation-only OpenAI-compatible chat request from sanitized context."""

    safe_request = sanitize_assistant_request(request)
    citation_ids = assistant_citation_ids(safe_request.context)
    evidence_payload = {
        "snapshot_id": safe_request.snapshot_id,
        "object_id": safe_request.object_id,
        "preset": safe_request.preset,
        "prompt": safe_request.prompt,
        "contexts": [item.model_dump(mode="json") for item in safe_request.context],
        "allowed_citation_ids": citation_ids,
        "safety": {
            "read_only": True,
            "no_live_bw_calls": True,
            "no_bw_query_execution": True,
            "no_data_preview": True,
            "no_raw_snapshot_payload": True,
            "impact_severity_authority": "impact.py",
        },
    }
    sanitized = sanitize_llm_evidence(evidence_payload)
    evidence_json = json.dumps(sanitized.data, ensure_ascii=False, sort_keys=True)
    return LlmChatRequest(
        messages=[
            ChatMessage(
                role="system",
                content=(
                    "You are the Ask BW / Review assistant for a local-only SAP BW lineage "
                    "and impact tool. Use only the sanitized deterministic context supplied. "
                    "Do not ask for or infer raw data, credentials, live BW state, query results, "
                    "or data previews. impact.py is final authority for severity. Every non-empty "
                    "answer line must include one allowed square-bracket citation such as "
                    "[ctx:id], [node:id], [edge:id], or [impact:id], unless the line "
                    "explicitly says the item is unknown or needs manual verification."
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    "Task prompt: "
                    f"{safe_request.prompt or safe_request.preset or 'Review context'}\n"
                    f"Allowed citations: {', '.join(citation_ids) or 'none'}\n"
                    "Return a concise answer, then unknowns/manual checks. "
                    "Sanitized deterministic context JSON:\n"
                    f"{evidence_json}"
                ),
            ),
        ],
        citation_ids=citation_ids,
        metadata={
            "snapshot_id": safe_request.snapshot_id or "",
            "object_id": safe_request.object_id or "",
            "assistant": "review",
        },
    )


def deterministic_assistant_fallback(
    request: AssistantReviewRequest,
    *,
    status: str = "disabled",
    fallback_reason: str | None = None,
) -> AssistantReviewResponse:
    safe_request = sanitize_assistant_request(request)
    contexts = safe_request.context
    citations = assistant_citation_ids(contexts)
    answer = _fallback_answer(safe_request, contexts)
    validate_assistant_answer_citations(answer, citations)
    return AssistantReviewResponse(
        status="fallback" if status == "fallback" else "disabled",
        answer=answer,
        citations=_used_citations(answer, citations) or citations[: min(len(citations), 12)],
        unknowns=_default_unknowns(contexts),
        confidence="medium" if contexts else "low",
        manual_checks=derive_manual_checks(safe_request),
        safety=AssistantSafety(
            llm_used=False,
            citation_validation="passed",
            fallback_reason=fallback_reason,
        ),
    )


def validate_assistant_answer_citations(answer: str, citation_ids: Sequence[str]) -> None:
    """Require every substantive answer line to cite allowed deterministic context."""

    allowed = set(citation_ids)
    if not allowed:
        uncited = [line for line in answer.splitlines() if line.strip() and not _unknown_line(line)]
        if uncited:
            raise LlmCitationError("assistant answer has no deterministic citations")
        return
    for line in answer.splitlines():
        stripped = line.strip()
        if not stripped or _unknown_line(stripped):
            continue
        cited = set(_CITATION_TOKEN_RE.findall(stripped)) - {REDACTED.strip("[]")}
        if not cited or not (cited & allowed) or not cited <= allowed:
            raise LlmCitationError("assistant answer line did not cite supported context")


def derive_manual_checks(request: AssistantReviewRequest) -> list[AssistantManualCheck]:
    contexts = request.context
    checks: list[AssistantManualCheck] = []
    impact_context_ids = [
        context.id
        for context in contexts
        if context.kind in {AssistantContextKind.IMPACT, AssistantContextKind.IMPACT_REVIEW}
    ]
    freshness_context_ids = [
        context.id for context in contexts if context.kind == AssistantContextKind.FRESHNESS
    ]
    explicit_manual_context_ids = [
        context.id for context in contexts if context.kind == AssistantContextKind.MANUAL_CHECK
    ]
    if impact_context_ids:
        checks.append(
            AssistantManualCheck(
                id="manual:bwmt-impact-review",
                title="Verify impact scope in BWMT before transport approval",
                tool="BWMT",
                steps_summary=(
                    "Open the changed BW object and impacted providers/queries in BWMT or "
                    "Eclipse, compare activation/transport scope, and keep impact.py severity "
                    "as the deterministic authority."
                ),
                related_context_ids=impact_context_ids[:12],
                citation_ids=_citations_for_context_ids(contexts, impact_context_ids),
            )
        )
    if freshness_context_ids:
        checks.append(
            AssistantManualCheck(
                id="manual:bwmt-request-freshness",
                title="Check request freshness and load status manually",
                tool="BWMT",
                steps_summary=(
                    "Use BWMT/Eclipse request monitor or the approved operational console to "
                    "confirm latest load status; this assistant does not call BW live or "
                    "preview data."
                ),
                related_context_ids=freshness_context_ids[:12],
                citation_ids=_citations_for_context_ids(contexts, freshness_context_ids),
            )
        )
    if explicit_manual_context_ids:
        checks.append(
            AssistantManualCheck(
                id="manual:eclipse-gap-review",
                title="Resolve explicit manual verification gaps",
                tool="Eclipse",
                steps_summary=(
                    "Review each deterministic manual gap in the SAP BW modeling tools; do not "
                    "treat assistant wording as activation, transport, or runtime evidence."
                ),
                related_context_ids=explicit_manual_context_ids[:12],
                citation_ids=_citations_for_context_ids(contexts, explicit_manual_context_ids),
            )
        )
    if not checks and _manual_prompt(request.prompt):
        checks.append(
            AssistantManualCheck(
                id="manual:baseline-review",
                title="Manual BW review required for unavailable runtime evidence",
                tool="manual",
                steps_summary=(
                    "Manually verify live BW state, activation, authorization, and runtime query "
                    "behavior outside this read-only assistant."
                ),
                related_context_ids=[context.id for context in contexts[:8]],
                citation_ids=assistant_citation_ids(contexts)[:20],
            )
        )
    return checks


def _fallback_answer(
    request: AssistantReviewRequest,
    contexts: Sequence[AssistantEvidenceContext],
) -> str:
    if not contexts:
        return (
            "Unknown / needs manual verification: no deterministic context was supplied "
            "to Ask BW / Review.\n"
            "Unknown / needs manual verification: live BW state, query results, data "
            "preview, and raw snapshot payloads are outside this read-only assistant."
        )
    lines: list[str] = []
    prompt = request.prompt or request.preset or "Ask BW / Review"
    lines.append(
        f"Deterministic fallback for “{prompt}” uses {len(contexts)} bounded context item(s). "
        f"[ctx:{contexts[0].id}]"
    )
    for context in contexts[:8]:
        lines.append(f"{context.title}: {_first_sentence(context.body)} [ctx:{context.id}]")
    impact_context = next(
        (
            item
            for item in contexts
            if item.kind in {AssistantContextKind.IMPACT, AssistantContextKind.IMPACT_REVIEW}
        ),
        None,
    )
    if impact_context is not None:
        lines.append(
            "Impact severity, confidence, and affected-object authority remain deterministic in "
            f"impact.py. [ctx:{impact_context.id}]"
        )
    lines.append(
        "Unknown / needs manual verification: live BW state, runtime query output, data "
        "preview, credentials, and transport approval are not available in this read-only "
        "assistant."
    )
    return "\n".join(lines)


def _default_unknowns(contexts: Sequence[AssistantEvidenceContext]) -> list[str]:
    unknowns = [
        "Live BW state is not available; assistant code performs no live BW calls.",
        "Runtime query results and data previews are not available.",
        "Credentials, raw snapshot payloads, and raw audit logs are not available to the answer.",
    ]
    if not contexts:
        unknowns.insert(0, "No deterministic context items were supplied.")
    return unknowns


def _first_sentence(value: str) -> str:
    text = " ".join(value.split())
    if len(text) <= 260:
        return text
    return f"{text[:260]}…"


def _used_citations(answer: str, allowed: Sequence[str]) -> list[str]:
    allowed_set = set(allowed)
    used: list[str] = []
    for citation in _CITATION_TOKEN_RE.findall(answer):
        if citation in allowed_set and citation not in used:
            used.append(citation)
    return used


def _unknown_line(line: str) -> bool:
    return bool(_UNKNOWN_LINE_RE.search(line))


def _citations_for_context_ids(
    contexts: Sequence[AssistantEvidenceContext],
    context_ids: Sequence[str],
) -> list[str]:
    wanted = set(context_ids)
    scoped = [context for context in contexts if context.id in wanted]
    return assistant_citation_ids(scoped)


def _manual_prompt(prompt: str) -> bool:
    return bool(re.search(r"(?i)\b(cab|review|manual|bwmt|eclipse|freshness|transport)\b", prompt))
