from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

REDACTED = "[REDACTED]"

_SECRET_VALUE_KEY_TOKENS = (
    "apikey",
    "authorization",
    "bearer",
    "credential",
    "credentials",
    "passwd",
    "password",
    "secret",
    "token",
    "user",
    "username",
)
_BW_CREDENTIAL_TOKENS = (
    "apikey",
    "authorization",
    "client",
    "credential",
    "key",
    "passwd",
    "password",
    "secret",
    "token",
    "user",
    "username",
)
_RAW_SNAPSHOT_TOKENS = ("rawsnapshot", "snapshotpayload", "snapshot", "manifest")

_SQL_STRING_LITERAL_PATTERN = r"(?:'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\")"
_SQL_FUNCTION_CALL_PATTERN = r"(?:[A-Za-z_][A-Za-z0-9_]*\s*\([^)]*\))"
_SQL_SCALAR_PATTERN = (
    rf"(?:{_SQL_FUNCTION_CALL_PATTERN}|\([^)]*\)|{_SQL_STRING_LITERAL_PATTERN}|[^\s,;)]+)"
)
_SQL_ANY_STRING_LITERAL_RE=re.compile(_SQL_STRING_LITERAL_PATTERN)
_SQL_PREDICATE_OPERATOR_PATTERN = (
    r"(?:"
    rf"(?:not\s+)?between\s+{_SQL_SCALAR_PATTERN}\s+and\s+{_SQL_SCALAR_PATTERN}|"
    rf"not\s+like\s+{_SQL_SCALAR_PATTERN}|"
    rf"like\s+{_SQL_SCALAR_PATTERN}|"
    r"not\s+in\s*\([^)]*\)|"
    r"in\s*\([^)]*\)|"
    rf"is\s+(?:not\s+)?distinct\s+from\s+{_SQL_SCALAR_PATTERN}|"
    r"is\s+not\s+null|"
    r"is\s+null|"
    rf"(?:=|<>|!=|<=|>=|<|>)\s*(?:any\s*)?{_SQL_SCALAR_PATTERN}"
    r")"
)
_SECRET_IDENTIFIER_PATTERN = (
    r"(?:(?:[`\"\[])?[A-Za-z_][A-Za-z0-9_$]*(?:[`\"\]])?\.)*"
    r"(?:[`\"\[])?\b(?:[A-Za-z0-9]*[_-])*[A-Za-z0-9]*"
    r"(?:api[_-]?key|authorization|password|passwd|secret|token|"
    r"credentials?|user(?:name|id)?)"
    r"[A-Za-z0-9]*(?:[_-][A-Za-z0-9]+)*\b(?:[`\"\]])?"
)
_ENV_IDENTIFIER_PATTERN = (
    r"(?:(?:[`\"\[])?[A-Za-z_][A-Za-z0-9_$]*(?:[`\"\]])?\.)*"
    r"(?:[`\"\[])?\b(?:mandt|client|bw[_-]?client|sap[_-]?client)\b(?:[`\"\]])?"
)
_SECRET_ASSIGNMENT_RE = re.compile(
    rf"(?i){_SECRET_IDENTIFIER_PATTERN}\s*[:=]\s*{_SQL_SCALAR_PATTERN}"
)
_SECRET_SQL_PREDICATE_RE = re.compile(
    rf"(?i){_SECRET_IDENTIFIER_PATTERN}\s*{_SQL_PREDICATE_OPERATOR_PATTERN}"
)
_SECRET_REVERSED_SQL_PREDICATE_RE = re.compile(
    rf"(?i){_SQL_SCALAR_PATTERN}\s*"
    rf"(?:=|<>|!=|<=|>=|<|>|is\s+(?:not\s+)?distinct\s+from)\s*"
    rf"{_SECRET_IDENTIFIER_PATTERN}"
)
_SECRET_REVERSED_IN_SQL_PREDICATE_RE = re.compile(
    rf"(?i){_SQL_SCALAR_PATTERN}\s+(?:not\s+)?in\s*\([^)]*{_SECRET_IDENTIFIER_PATTERN}[^)]*\)"
)
_SECRET_ALIAS_RE = re.compile(
    rf"(?i){_SQL_SCALAR_PATTERN}\s+as\s+{_SECRET_IDENTIFIER_PATTERN}"
)
_SECRET_FUNCTION_SQL_PREDICATE_RE = re.compile(
    rf"(?i)\b[A-Za-z_][A-Za-z0-9_]*\s*\([^)]*{_SECRET_IDENTIFIER_PATTERN}[^)]*\)+"
    rf"\s*{_SQL_PREDICATE_OPERATOR_PATTERN}"
)
_ENV_IDENTIFIER_ASSIGNMENT_RE = re.compile(
    rf"(?i){_ENV_IDENTIFIER_PATTERN}\s*[:=]\s*{_SQL_SCALAR_PATTERN}"
)
_ENV_SQL_PREDICATE_RE = re.compile(
    rf"(?i){_ENV_IDENTIFIER_PATTERN}\s*{_SQL_PREDICATE_OPERATOR_PATTERN}"
)
_ENV_REVERSED_SQL_PREDICATE_RE = re.compile(
    rf"(?i){_SQL_SCALAR_PATTERN}\s*"
    rf"(?:=|<>|!=|<=|>=|<|>|is\s+(?:not\s+)?distinct\s+from)\s*"
    rf"{_ENV_IDENTIFIER_PATTERN}"
)
_ENV_REVERSED_IN_SQL_PREDICATE_RE = re.compile(
    rf"(?i){_SQL_SCALAR_PATTERN}\s+(?:not\s+)?in\s*\([^)]*{_ENV_IDENTIFIER_PATTERN}[^)]*\)"
)
_ENV_ALIAS_RE = re.compile(rf"(?i){_SQL_SCALAR_PATTERN}\s+as\s+{_ENV_IDENTIFIER_PATTERN}")
_ENV_FUNCTION_SQL_PREDICATE_RE = re.compile(
    rf"(?i)\b[A-Za-z_][A-Za-z0-9_]*\s*\([^)]*{_ENV_IDENTIFIER_PATTERN}[^)]*\)+"
    rf"\s*{_SQL_PREDICATE_OPERATOR_PATTERN}"
)
_AUTH_HEADER_RE = re.compile(
    r"(?i)\bauthorization\s*[:=]\s*(?:(?:bearer|basic)\s+)?[A-Za-z0-9._~+/=-]+"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_URL_RE = re.compile(r"(?i)\b(?:[a-z][a-z0-9+.-]*://|www\.)[^\s<>\"'`]+")
_HOST_LABEL_PATTERN = r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)"
_FQDN_HOST_RE = re.compile(
    rf"(?<![A-Za-z0-9_:/-])(?:{_HOST_LABEL_PATTERN}\.)+[A-Za-z]{{2,63}}"
    r"(?::\d{1,5})?(?=[^A-Za-z0-9_-]|$)",
    re.I,
)
_INTERNAL_HOST_RE = re.compile(r"\b[A-Za-z0-9-]+\.(?:corp|internal|lan|local)\b", re.I)
_OPENAI_STYLE_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]+\b")
_SECRET_MARKER_RE = re.compile(rf"(?i){_SECRET_IDENTIFIER_PATTERN}")
_ENV_IDENTIFIER_MARKER_RE = re.compile(rf"(?i){_ENV_IDENTIFIER_PATTERN}")
_USAGE_TOKEN_KEYS = {
    "prompttokens",
    "completiontokens",
    "totaltokens",
    "cachedtokens",
    "reasoningtokens",
}
_TOKEN_CREDENTIAL_KEYS = {
    "token",
    "tokens",
    "accesstoken",
    "refreshtoken",
    "apitoken",
    "authtoken",
    "bearertoken",
    "idtoken",
}
_ENV_IDENTIFIER_KEY_TOKENS = {
    "bwclient",
    "client",
    "email",
    "host",
    "hostname",
    "ip",
    "ipaddr",
    "ipaddress",
    "mandt",
    "sapclient",
    "server",
}


class SanitizedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: Any
    redacted_paths: list[str] = Field(default_factory=list)
    omitted_paths: list[str] = Field(default_factory=list)


def sanitize_llm_evidence(value: Any) -> SanitizedPayload:
    """Return an LLM-safe copy of evidence with secrets redacted and raw/BW data omitted."""

    redacted_paths: list[str] = []
    omitted_paths: list[str] = []
    data = _sanitize_value(
        value,
        path="$",
        redacted_paths=redacted_paths,
        omitted_paths=omitted_paths,
    )
    return SanitizedPayload(data=data, redacted_paths=redacted_paths, omitted_paths=omitted_paths)


def sanitize_text(value: str) -> str:
    """Remove obvious inline secret markers and values from text sent to an LLM."""

    sanitized = _AUTH_HEADER_RE.sub(REDACTED, value)
    sanitized = _BEARER_RE.sub(REDACTED, sanitized)
    sanitized = _URL_RE.sub(REDACTED, sanitized)
    sanitized = _ENV_FUNCTION_SQL_PREDICATE_RE.sub(REDACTED, sanitized)
    sanitized = _ENV_REVERSED_IN_SQL_PREDICATE_RE.sub(REDACTED, sanitized)
    sanitized = _ENV_REVERSED_SQL_PREDICATE_RE.sub(REDACTED, sanitized)
    sanitized = _ENV_SQL_PREDICATE_RE.sub(REDACTED, sanitized)
    sanitized = _ENV_ALIAS_RE.sub(REDACTED, sanitized)
    sanitized = _ENV_IDENTIFIER_ASSIGNMENT_RE.sub(REDACTED, sanitized)
    sanitized = _SECRET_FUNCTION_SQL_PREDICATE_RE.sub(REDACTED, sanitized)
    sanitized = _SECRET_REVERSED_IN_SQL_PREDICATE_RE.sub(REDACTED, sanitized)
    sanitized = _SECRET_REVERSED_SQL_PREDICATE_RE.sub(REDACTED, sanitized)
    sanitized = _SECRET_SQL_PREDICATE_RE.sub(REDACTED, sanitized)
    sanitized = _SECRET_ALIAS_RE.sub(REDACTED, sanitized)
    sanitized = _SECRET_ASSIGNMENT_RE.sub(REDACTED, sanitized)
    sanitized = _EMAIL_RE.sub(REDACTED, sanitized)
    sanitized = _IPV4_RE.sub(REDACTED, sanitized)
    sanitized = _INTERNAL_HOST_RE.sub(REDACTED, sanitized)
    sanitized = _FQDN_HOST_RE.sub(REDACTED, sanitized)
    sanitized = _OPENAI_STYLE_KEY_RE.sub(REDACTED, sanitized)
    sanitized = _SQL_ANY_STRING_LITERAL_RE.sub(REDACTED, sanitized)
    sanitized = _SECRET_MARKER_RE.sub(REDACTED, sanitized)
    sanitized = _ENV_IDENTIFIER_MARKER_RE.sub(REDACTED, sanitized)
    return sanitized


def _sanitize_value(
    value: Any,
    *,
    path: str,
    redacted_paths: list[str],
    omitted_paths: list[str],
) -> Any:
    if isinstance(value, Mapping):
        sanitized_mapping: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            item_path = f"{path}.{key_text}"
            if _should_omit_key(key_text):
                omitted_paths.append(item_path)
                continue
            if _should_redact_key(key_text):
                sanitized_mapping[key_text] = REDACTED
                redacted_paths.append(item_path)
                continue
            sanitized_mapping[key_text] = _sanitize_value(
                item,
                path=item_path,
                redacted_paths=redacted_paths,
                omitted_paths=omitted_paths,
            )
        return sanitized_mapping

    if isinstance(value, str):
        sanitized = sanitize_text(value)
        if sanitized != value:
            redacted_paths.append(path)
        return sanitized

    if _is_non_string_sequence(value):
        return [
            _sanitize_value(
                item,
                path=f"{path}[{index}]",
                redacted_paths=redacted_paths,
                omitted_paths=omitted_paths,
            )
            for index, item in enumerate(value)
        ]

    return value


def _is_non_string_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray)


def _should_omit_key(key: str) -> bool:
    normalized = _normalize_key(key)
    if any(token in normalized for token in _RAW_SNAPSHOT_TOKENS):
        return True
    if normalized in {"bwcredentials", "bwcredential"}:
        return True
    return normalized.startswith("bw") and any(
        token in normalized for token in _BW_CREDENTIAL_TOKENS
    )


def _should_redact_key(key: str) -> bool:
    normalized = _normalize_key(key)
    if normalized in _USAGE_TOKEN_KEYS:
        return False
    if normalized in _ENV_IDENTIFIER_KEY_TOKENS or any(
        token in normalized for token in _ENV_IDENTIFIER_KEY_TOKENS if len(token) > 3
    ):
        return True
    if (
        normalized in _TOKEN_CREDENTIAL_KEYS
        or normalized.endswith("token")
        or normalized.startswith("token")
    ):
        return True
    return any(token in normalized for token in _SECRET_VALUE_KEY_TOKENS if token != "token")


def _normalize_key(key: str) -> str:
    return "".join(char for char in key.lower() if char.isalnum())
