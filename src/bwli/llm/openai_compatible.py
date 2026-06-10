from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpcore
import httpx
from pydantic import BaseModel, ConfigDict, Field

from bwli.config import (
    LlmRuntimeConfig,
    _validate_local_llm_base_url,
    _validated_llm_connection_host,
)
from bwli.llm.sanitizer import sanitize_llm_evidence

TransportLike = httpx.BaseTransport | Callable[[httpx.Request], httpx.Response]


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str


class LlmChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage]
    citation_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)


class LlmAuditMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["openai-compatible"] = "openai-compatible"
    endpoint: str = "/chat/completions"
    runtime_endpoint_source: Literal["runtime"] = "runtime"
    runtime_model_source: Literal["runtime"] = "runtime"
    model: str
    prompt_sha256: str
    sanitized_input_sha256: str
    request_citation_ids: list[str] = Field(default_factory=list)
    citation_validation: Literal["not_validated", "passed"] = "not_validated"
    response_timestamp: str
    response_id: str | None = None
    usage: dict[str, Any] | None = None


class LlmCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str
    audit: LlmAuditMetadata


class OpenAICompatibleClient:
    """Minimal local/private OpenAI-compatible chat-completions client."""

    def __init__(
        self,
        *,
        runtime: LlmRuntimeConfig,
        transport: TransportLike | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._runtime = runtime
        self._transport = _EndpointGuardTransport(_coerce_transport(transport))
        self._timeout = timeout

    def chat(self, request: LlmChatRequest) -> LlmCompletion:
        _validate_local_llm_base_url(self._runtime.base_url)
        payload = {
            "model": self._runtime.model,
            "messages": [message.model_dump(mode="json") for message in request.messages],
            "stream": False,
        }
        with httpx.Client(
            base_url=_normalized_base_url(self._runtime.base_url),
            transport=self._transport,
            timeout=self._timeout,
            trust_env=False,
        ) as client:
            response = client.post(
                "chat/completions",
                headers={
                    "Authorization": f"Bearer {self._runtime.api_key.get_secret_value()}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        response.raise_for_status()
        body = response.json()
        content = _extract_content(body)
        return LlmCompletion(
            content=content,
            audit=LlmAuditMetadata(
                model=self._runtime.model,
                prompt_sha256=_sha256_json(payload["messages"]),
                sanitized_input_sha256=_sha256_text(_sanitized_input_text(request)),
                request_citation_ids=request.citation_ids,
                response_timestamp=_utc_timestamp(),
                response_id=_extract_response_id(body),
                usage=_safe_usage(body),
            ),
        )


def write_llm_audit_log(completion: LlmCompletion, audit_dir: Path) -> Path:
    """Persist a local-only LLM audit record without endpoint or API-key values."""

    audit_dir.mkdir(parents=True, exist_ok=True)
    timestamp = completion.audit.response_timestamp.replace(":", "").replace("+", "Z")
    path = audit_dir / f"llm-{timestamp}-{completion.audit.prompt_sha256[:12]}.json"
    path.write_text(
        json.dumps(completion.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return path


class _EndpointGuardTransport(httpx.BaseTransport):
    def __init__(self, inner: httpx.BaseTransport) -> None:
        self._inner = inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        _validate_local_llm_base_url(str(request.url))
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()


def _coerce_transport(transport: TransportLike | None) -> httpx.BaseTransport:
    if transport is None:
        return _GuardedHTTPTransport()
    if isinstance(transport, httpx.BaseTransport):
        return transport
    return httpx.MockTransport(transport)


class _GuardedHTTPTransport(httpx.HTTPTransport):
    def __init__(self) -> None:
        super().__init__(trust_env=False)
        self._pool._network_backend = _GuardedNetworkBackend()


class _GuardedNetworkBackend(httpcore.NetworkBackend):
    def __init__(self) -> None:
        self._inner = httpcore.SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        connection_host = _validated_llm_connection_host(host.lower(), port)
        return self._inner.connect_tcp(
            host=connection_host,
            port=port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.NetworkStream:
        return self._inner.connect_unix_socket(
            path=path,
            timeout=timeout,
            socket_options=socket_options,
        )

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)


def _normalized_base_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/"


def _extract_content(body: Any) -> str:
    if not isinstance(body, dict):
        raise ValueError("LLM response must be a JSON object")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("LLM response did not include choices")
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise ValueError("LLM response choice must be an object")
    message = first_choice.get("message")
    if not isinstance(message, dict):
        raise ValueError("LLM response choice did not include a message")
    content = message.get("content")
    if not isinstance(content, str):
        raise ValueError("LLM response message content must be a string")
    return content


def _extract_response_id(body: Any) -> str | None:
    if not isinstance(body, dict):
        return None
    response_id = body.get("id")
    return response_id if isinstance(response_id, str) else None


def _safe_usage(body: Any) -> dict[str, Any] | None:
    usage = _extract_usage(body)
    if usage is None:
        return None
    sanitized = sanitize_llm_evidence(usage)
    return sanitized.data if isinstance(sanitized.data, dict) else None


def _extract_usage(body: Any) -> dict[str, Any] | None:
    if not isinstance(body, dict):
        return None
    usage = body.get("usage")
    return usage if isinstance(usage, dict) else None


def _sanitized_input_text(request: LlmChatRequest) -> str:
    return "\n".join(message.content for message in request.messages)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat()
