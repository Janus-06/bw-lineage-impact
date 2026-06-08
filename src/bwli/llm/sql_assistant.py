from __future__ import annotations

from bwli.config import LlmRuntimeConfig
from bwli.field_lineage import SqlParseResult
from bwli.llm.explainer import _validate_completion_citations, build_sql_explainer_request
from bwli.llm.openai_compatible import ChatMessage, LlmChatRequest, OpenAICompatibleClient
from bwli.llm.sanitizer import sanitize_text


def build_sql_draft_request(
    result: SqlParseResult,
    *,
    question: str,
    target_dialect: str,
) -> LlmChatRequest:
    """Build a draft-specific prompt from sanitized SQL evidence without explainer tasks."""

    evidence_request = build_sql_explainer_request(result)
    citations = evidence_request.citation_ids
    if not citations:
        raise ValueError("SQL draft requires parsed deterministic SQL evidence with citations")
    evidence_message = next(
        (message.content for message in evidence_request.messages if message.role == "user"),
        "",
    )
    marker = "Sanitized cited evidence JSON:\n"
    if marker not in evidence_message:
        raise ValueError("SQL draft requires sanitized deterministic evidence payload")
    evidence_preamble = evidence_message.split("Task:", maxsplit=1)[0].rstrip()
    evidence_json = evidence_message.split(marker, maxsplit=1)[1]
    return LlmChatRequest(
        messages=[
            ChatMessage(
                role="system",
                content=(
                    "Create advisory SQL draft text only. Never execute SQL. "
                    "Use only cited deterministic Native SQL View evidence. "
                    "Every non-empty line must cite at least one provided evidence ID."
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    f"{evidence_preamble}\n"
                    "Task: create an advisory SQL draft or pseudocode plus caveats. "
                    "Do not claim execution. Keep all claims tied to citation IDs.\n"
                    f"NL request: {sanitize_text(question)}\n"
                    f"Target dialect: {sanitize_text(target_dialect)}\n"
                    "Sanitized cited evidence JSON:\n"
                    f"{evidence_json}"
                ),
            ),
        ],
        citation_ids=citations,
        metadata={"target_dialect": sanitize_text(target_dialect)},
    )


def create_sql_draft(
    result: SqlParseResult,
    *,
    question: str,
    target_dialect: str,
    runtime: LlmRuntimeConfig,
) -> dict[str, object]:
    """Create an advisory SQL draft using only sanitized cited SQL evidence."""

    chat_request = build_sql_draft_request(
        result,
        question=question,
        target_dialect=target_dialect,
    )
    citations = chat_request.citation_ids
    completion = OpenAICompatibleClient(runtime=runtime).chat(chat_request)
    _validate_completion_citations(completion, citations)
    completion = completion.model_copy(
        update={
            "audit": completion.audit.model_copy(update={"citation_validation": "passed"})
        }
    )
    return {
        "draft_sql": completion.content,
        "citations": citations,
        "llm_audit": completion.audit.model_dump(mode="json"),
    }
