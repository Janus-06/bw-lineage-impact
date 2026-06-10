from __future__ import annotations

import json
import os
import socket
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
    user: str
    password: SecretStr
    client: str
    language: str = "EN"
    verify_ssl: bool = True
    ca_bundle: str | None = None
    trust_env: bool = True

    @classmethod
    def from_env(cls) -> BwConnectionConfig:
        missing = [
            name
            for name in ("BW_URL", "BW_USER", "BW_PASSWORD", "BW_CLIENT")
            if not os.environ.get(name)
        ]
        if missing:
            joined = ", ".join(missing)
            raise ConfigError(f"missing required BW runtime environment variables: {joined}")
        return cls(
            url=os.environ["BW_URL"],
            user=os.environ["BW_USER"],
            password=SecretStr(os.environ["BW_PASSWORD"]),
            client=os.environ["BW_CLIENT"],
            language=os.environ.get("BW_LANGUAGE", "EN"),
            verify_ssl=_resolve_env_bool("BW_VERIFY_SSL", default=True),
            ca_bundle=_resolve_optional_env_path("BW_CA_BUNDLE"),
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


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bw: BwConfigRefs = Field(default_factory=BwConfigRefs)
    llm: LlmConfig = Field(default_factory=LlmConfig)

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
