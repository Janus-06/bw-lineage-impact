from __future__ import annotations

import json
import os
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class ConfigError(RuntimeError):
    """Raised when user-supplied runtime configuration is missing or invalid."""


class BwConnectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    user: str
    password: SecretStr
    client: str
    language: str = "EN"

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
        )


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
            raise ConfigError("MVP supports only local OpenAI-compatible LLM endpoints")
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


def validate_local_llm_base_url(base_url: str) -> None:
    _validate_local_llm_base_url(base_url)


def _validate_local_llm_base_url(base_url: str) -> None:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigError("LLM base URL must be an http(s) OpenAI-compatible endpoint")

    host = parsed.hostname.lower()
    if host in {"localhost", "host.docker.internal"}:
        return

    try:
        address = ip_address(host)
    except ValueError as exc:
        raise ConfigError("LLM base URL must point to a loopback/local host endpoint") from exc

    if address.is_loopback:
        return

    raise ConfigError("LLM base URL must point to a loopback/local host endpoint")


def redact_config_for_log(value: Any) -> str:
    """Return a safe representation for diagnostics without revealing secrets."""
    if isinstance(value, SecretStr):
        return "[REDACTED]"
    return str(value)
