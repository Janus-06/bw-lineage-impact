from __future__ import annotations

import re
from collections.abc import Sequence
from urllib.parse import urlparse

_BW_HOST_PLACEHOLDER = "[BW_HOST]"
_BW_URL_PLACEHOLDER = "[BW_URL]"
_SECRET_PLACEHOLDER = "[REDACTED]"

_SECRET_KEY_PATTERN = re.compile(
    r"(?i)(authorization|password|passwd|pwd|token|api[_-]?key)(\s*[:=]\s*)[^\s,;]+",
)
_URL_USERINFO_PATTERN = re.compile(r"(?i)(https?://)[^\s/@]+@")
_BEARER_PATTERN = re.compile(r"(?i)Bearer\s+[^\s,;]+")
_SAP_QUERY_PATTERN = re.compile(
    r"(?i)(sap[_-]?client|mandt|sap[_-]?language)(\s*[:=]\s*)[^\s,;&]+",
)


def redact_text(
    value: str,
    *,
    secret_values: Sequence[str] = (),
    urls: Sequence[str] = (),
) -> str:
    """Scrub secrets, BW URL/host fragments, and credential-shaped tokens from text.

    Used for user-visible error messages: ensures BW host, query parameters, and
    declared secret values never appear verbatim in HTTP responses or logs.
    """
    if not value:
        return value
    redacted = value
    for secret in secret_values:
        if secret:
            redacted = redacted.replace(secret, _SECRET_PLACEHOLDER)
    for url in urls:
        if not url:
            continue
        redacted = redacted.replace(url, _BW_URL_PLACEHOLDER)
        host = _extract_host(url)
        if host:
            redacted = redacted.replace(host, _BW_HOST_PLACEHOLDER)
    redacted = _URL_USERINFO_PATTERN.sub(rf"\1{_SECRET_PLACEHOLDER}@", redacted)
    redacted = _BEARER_PATTERN.sub(f"Bearer {_SECRET_PLACEHOLDER}", redacted)
    redacted = _SECRET_KEY_PATTERN.sub(rf"\1\2{_SECRET_PLACEHOLDER}", redacted)
    redacted = _SAP_QUERY_PATTERN.sub(rf"\1\2{_SECRET_PLACEHOLDER}", redacted)
    return redacted


def _extract_host(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.netloc:
        return parsed.netloc
    if parsed.path and "/" not in parsed.path:
        return parsed.path
    return None
