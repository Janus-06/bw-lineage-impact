from __future__ import annotations

import json
import os
import re
import socket
import stat
from ipaddress import IPv4Address, IPv6Address, ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, SecretStr

IpAddress = IPv4Address | IPv6Address


class ConfigError(RuntimeError):
    """Raised when user-supplied runtime configuration is missing or invalid."""


class BwConnectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    user: str | None = None
    password: SecretStr | None = None
    client: str
    language: str = "EN"
    verify_ssl: bool = True
    ca_bundle: str | None = None
    cookie_file: str | None = None
    trust_env: bool = True

    @classmethod
    def from_env(cls) -> BwConnectionConfig:
        cookie_file = _resolve_optional_env_path("BW_COOKIE_FILE")
        required_names = ["BW_URL", "BW_CLIENT"]
        if cookie_file is None:
            required_names.extend(["BW_USER", "BW_PASSWORD"])
        missing = [
            name
            for name in required_names
            if not os.environ.get(name)
        ]
        if missing:
            joined = ", ".join(missing)
            raise ConfigError(f"missing required BW runtime environment variables: {joined}")
        if cookie_file is not None:
            try:
                load_bw_cookie_file(cookie_file)
            except ValueError as exc:
                raise ConfigError("BW_COOKIE_FILE does not contain valid cookies") from exc
        password = os.environ.get("BW_PASSWORD")
        return cls(
            url=os.environ["BW_URL"],
            user=os.environ.get("BW_USER") or None,
            password=SecretStr(password) if password else None,
            client=os.environ["BW_CLIENT"],
            language=os.environ.get("BW_LANGUAGE", "EN"),
            verify_ssl=_resolve_env_bool("BW_VERIFY_SSL", default=True),
            ca_bundle=_resolve_optional_env_path("BW_CA_BUNDLE"),
            cookie_file=cookie_file,
            trust_env=_resolve_env_bool("BW_TRUST_ENV", default=True),
        )

    def httpx_verify_arg(self) -> bool | str:
        if self.verify_ssl and self.ca_bundle:
            return self.ca_bundle
        return self.verify_ssl


class BwConfigRefs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url_ref: str = "env://BW_URL"
    user_ref: str = "env://BW_USER"
    password_ref: str = "env://BW_PASSWORD"
    client_ref: str = "env://BW_CLIENT"
    language_ref: str = "env://BW_LANGUAGE"
    cookie_file_ref: str = "env://BW_COOKIE_FILE"


class LlmRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str
    model: str
    api_key: SecretStr


class LlmConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    provider: str = "openai-compatible"
    base_url_ref: str = "env://BWLI_LLM_BASE_URL"
    model_ref: str = "env://BWLI_LLM_MODEL"
    api_key_ref: str = "env://BWLI_LLM_API_KEY"

    def resolve_runtime(self) -> LlmRuntimeConfig | None:
        if not self.enabled:
            return None
        if self.provider != "openai-compatible":
            raise ConfigError("MVP supports only OpenAI-compatible LLM endpoints")
        base_url = _resolve_env_ref(self.base_url_ref)
        _validate_local_llm_base_url(base_url)
        return LlmRuntimeConfig(
            base_url=base_url,
            model=_resolve_env_ref(self.model_ref),
            api_key=SecretStr(_resolve_env_ref(self.api_key_ref)),
        )


class DataGateConfig(BaseModel):
    """Explicit gate for future data-bearing preview/query access."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    max_rows: int = Field(default=100, ge=1, le=1000)
    allow_llm_rows: bool = False

    def require_enabled(self) -> None:
        if not self.enabled:
            raise ConfigError("data-bearing access requires explicit enablement")

    def enforce_row_cap(self, requested_rows: int | None = None) -> int:
        self.require_enabled()
        if requested_rows is None:
            return self.max_rows
        if requested_rows < 1:
            raise ConfigError("requested data row count must be positive")
        return min(requested_rows, self.max_rows)

    def require_llm_rows_allowed(self) -> None:
        self.require_enabled()
        if not self.allow_llm_rows:
            raise ConfigError("LLM use for data rows requires explicit enablement")


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bw: BwConfigRefs = Field(default_factory=BwConfigRefs)
    llm: LlmConfig = Field(default_factory=LlmConfig)
    data_gate: DataGateConfig = Field(default_factory=DataGateConfig)

    @classmethod
    def from_file(cls, path: Path) -> AppConfig:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ConfigError("config file must contain a JSON object")
        return cls.model_validate(raw)


def _resolve_env_ref(ref: str) -> str:
    prefix = "env://"
    if not ref.startswith(prefix):
        raise ConfigError(f"unsupported secret reference scheme: {ref}")
    name = ref.removeprefix(prefix)
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"missing runtime value for {ref}")
    return value


def _resolve_env_bool(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _resolve_optional_env_path(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return None
    return value.strip()


_COOKIE_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


def load_bw_cookie_file(cookie_file: str | Path) -> dict[str, str]:
    """Load a validated BW cookie file into an in-memory cookie map."""

    path = validate_bw_cookie_file_path(cookie_file)
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError("BW_COOKIE_FILE could not be read") from exc
    return parse_bw_cookie_text(content)


def validate_bw_cookie_file_path(cookie_file: str | Path) -> Path:
    """Validate BW_COOKIE_FILE path safety without exposing the path in errors."""

    path = Path(cookie_file)
    try:
        file_stat = path.stat()
    except OSError as exc:
        raise ConfigError("BW_COOKIE_FILE must point to an existing regular file") from exc
    if not stat.S_ISREG(file_stat.st_mode):
        raise ConfigError("BW_COOKIE_FILE must point to an existing regular file")
    if file_stat.st_mode & 0o077:
        raise ConfigError("BW_COOKIE_FILE must not be group/other accessible")
    return path


def parse_bw_cookie_text(value: str) -> dict[str, str]:
    """Parse raw Cookie headers or Netscape cookie jar content."""

    cookies: dict[str, str] = {}
    data_lines = list(_cookie_data_lines(value))
    if not data_lines:
        raise ValueError("cookie file is empty")
    for line in data_lines:
        if _looks_like_netscape_cookie_line(line):
            name, cookie_value = _parse_netscape_cookie_line(line)
            _store_cookie(cookies, name, cookie_value)
            continue
        for name, cookie_value in _parse_raw_cookie_header_line(line).items():
            _store_cookie(cookies, name, cookie_value)
    if not cookies:
        raise ValueError("cookie file does not contain cookies")
    return cookies


def _cookie_data_lines(value: str) -> list[str]:
    lines: list[str] = []
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#") and not line.startswith("#HttpOnly_"):
            continue
        lines.append(line)
    return lines


def _looks_like_netscape_cookie_line(line: str) -> bool:
    fields = re.split(r"\s+", line, maxsplit=6)
    if len(fields) != 7:
        return False
    _domain, include_subdomains, path, secure, expires, _name, _value = fields
    return (
        include_subdomains.upper() in {"TRUE", "FALSE"}
        and path.startswith("/")
        and secure.upper() in {"TRUE", "FALSE"}
        and expires.isdigit()
    )


def _parse_netscape_cookie_line(line: str) -> tuple[str, str]:
    fields = re.split(r"\s+", line, maxsplit=6)
    if len(fields) != 7:
        raise ValueError("invalid Netscape cookie line")
    _domain, _flag, _path, _secure, _expires, name, value = fields
    _validate_cookie_pair(name, value)
    return name, value


def _parse_raw_cookie_header_line(line: str) -> dict[str, str]:
    raw = line
    if raw.lower().startswith("cookie:"):
        raw = raw.split(":", 1)[1].strip()
    cookies: dict[str, str] = {}
    for part in raw.split(";"):
        item = part.strip()
        if not item:
            continue
        name, separator, value = item.partition("=")
        if not separator:
            raise ValueError("invalid raw cookie syntax")
        _store_cookie(cookies, name.strip(), value.strip())
    if not cookies:
        raise ValueError("raw cookie header is empty")
    return cookies


def _store_cookie(cookies: dict[str, str], name: str, value: str) -> None:
    _validate_cookie_pair(name, value)
    cookies[name] = value


def _validate_cookie_pair(name: str, value: str) -> None:
    if not name or not _COOKIE_NAME_RE.fullmatch(name):
        raise ValueError("invalid cookie name")
    if not value or any(char in value for char in "\r\n;"):
        raise ValueError("invalid cookie value")


def validate_local_llm_base_url(base_url: str) -> None:
    _validate_local_llm_base_url(base_url)


def _validate_local_llm_base_url(base_url: str) -> None:
    parsed = urlparse(base_url)
    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ConfigError("LLM base URL must be an http(s) OpenAI-compatible endpoint") from exc
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise ConfigError("LLM base URL must be an http(s) OpenAI-compatible endpoint")

    host = hostname.lower()
    _validated_llm_connection_host(host, port)


def _validated_llm_connection_host(host: str, port: int | None) -> str:
    """Return a validated numeric connect host for an LLM endpoint hostname."""

    try:
        address = ip_address(host)
    except ValueError:
        resolved_addresses = _validate_resolved_llm_addresses(host, port)
        return resolved_addresses[0]

    _reject_disallowed_llm_address(address)

    if address.is_loopback or address.is_private or address.is_global:
        return host

    raise ConfigError("LLM base URL must be a reachable http(s) OpenAI-compatible endpoint")


def _reject_disallowed_llm_address(address: IpAddress) -> None:
    if address.is_link_local:
        raise ConfigError("LLM base URL must not point to a link-local metadata endpoint")


def _validate_resolved_llm_addresses(host: str, port: int | None) -> list[str]:
    resolved_addresses = _resolve_hostname_addresses(host, port)
    if not resolved_addresses:
        raise ConfigError("LLM base URL hostname must resolve before use")

    for resolved_address in resolved_addresses:
        try:
            address = ip_address(resolved_address)
        except ValueError as exc:
            raise ConfigError("LLM base URL hostname resolved to an invalid address") from exc
        _reject_disallowed_llm_address(address)
        if address.is_loopback or address.is_private or address.is_global:
            continue
        raise ConfigError("LLM base URL must resolve to a reachable endpoint")
    return resolved_addresses


def _resolve_hostname_addresses(host: str, port: int | None) -> list[str]:
    """Resolve hostname addresses for runtime metadata endpoint blocking."""

    try:
        infos = socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
    except OSError:
        return []

    addresses: list[str] = []
    seen: set[str] = set()
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        address = sockaddr[0]
        if not isinstance(address, str):
            continue
        if address in seen:
            continue
        seen.add(address)
        addresses.append(address)
    return addresses


def redact_config_for_log(value: Any) -> str:
    """Return a safe representation for diagnostics without revealing secrets."""
    if isinstance(value, SecretStr):
        return "[REDACTED]"
    return str(value)
