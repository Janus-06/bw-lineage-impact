from __future__ import annotations

import pytest

from bwli.config import AppConfig, BwConnectionConfig, ConfigError, LlmConfig, LlmRuntimeConfig


def test_bw_env_config_loads_reference_mcp_names(monkeypatch: pytest.MonkeyPatch) -> None:
    credential_value = "fakepass"
    monkeypatch.setenv("BW_URL", "https://bw.example.invalid")
    monkeypatch.setenv("BW_USER", "fixture-user")
    monkeypatch.setenv("BW_PASSWORD", credential_value)
    monkeypatch.setenv("BW_CLIENT", "100")
    monkeypatch.setenv("BW_LANGUAGE", "KO")

    config = BwConnectionConfig.from_env()

    assert config.url == "https://bw.example.invalid"
    assert config.user == "fixture-user"
    assert config.password.get_secret_value() == credential_value
    assert config.client == "100"
    assert config.language == "KO"


def test_bw_env_config_requires_runtime_values(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ["BW_URL", "BW_USER", "BW_PASSWORD", "BW_CLIENT", "BW_LANGUAGE"]:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ConfigError):
        BwConnectionConfig.from_env()


def test_llm_defaults_are_disabled_and_reference_local_openai_compatible_env() -> None:
    config = LlmConfig()

    assert config.enabled is False
    assert config.provider == "openai-compatible"
    assert config.base_url_ref == "env://BWLI_LLM_BASE_URL"
    assert config.model_ref == "env://BWLI_LLM_MODEL"
    assert config.api_key_ref == "env://BWLI_LLM_API_KEY"
    assert config.resolve_runtime() is None


def test_llm_enabled_requires_user_supplied_runtime_refs(monkeypatch: pytest.MonkeyPatch) -> None:
    credential_value = "dummy-key"
    monkeypatch.setenv("BWLI_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("BWLI_LLM_MODEL", "local-fixture-model")
    monkeypatch.setenv("BWLI_LLM_API_KEY", credential_value)

    runtime = LlmConfig(enabled=True).resolve_runtime()

    assert isinstance(runtime, LlmRuntimeConfig)
    assert runtime.base_url == "http://127.0.0.1:11434/v1"
    assert runtime.model == "local-fixture-model"
    assert runtime.api_key.get_secret_value() == credential_value


def test_llm_enabled_rejects_missing_runtime_refs(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ["BWLI_LLM_BASE_URL", "BWLI_LLM_MODEL", "BWLI_LLM_API_KEY"]:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ConfigError):
        LlmConfig(enabled=True).resolve_runtime()


def test_llm_enabled_rejects_public_runtime_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BWLI_LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("BWLI_LLM_MODEL", "local-fixture-model")
    monkeypatch.setenv("BWLI_LLM_API_KEY", "dummy-key")

    with pytest.raises(ConfigError):
        LlmConfig(enabled=True).resolve_runtime()


def test_llm_enabled_rejects_link_local_metadata_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BWLI_LLM_BASE_URL", "http://169.254.169.254/v1")
    monkeypatch.setenv("BWLI_LLM_MODEL", "local-fixture-model")
    monkeypatch.setenv("BWLI_LLM_API_KEY", "dummy-key")

    with pytest.raises(ConfigError):
        LlmConfig(enabled=True).resolve_runtime()


def test_llm_enabled_rejects_private_non_loopback_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    for base_url in [
        "http://10.0.0.1/v1",
        "http://172.16.0.1/v1",
        "http://192.168.1.50/v1",
    ]:
        monkeypatch.setenv("BWLI_LLM_BASE_URL", base_url)
        monkeypatch.setenv("BWLI_LLM_MODEL", "local-fixture-model")
        monkeypatch.setenv("BWLI_LLM_API_KEY", "dummy-key")

        with pytest.raises(ConfigError):
            LlmConfig(enabled=True).resolve_runtime()


def test_app_config_rejects_plaintext_secret_fields(tmp_path) -> None:
    config_path = tmp_path / "bwli-config.json"
    config_path.write_text(
        '{"llm":{"enabled":false,"api_key":"do-not-accept"}}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        AppConfig.from_file(config_path)


def test_app_config_can_load_config_refs_without_resolving_secrets(tmp_path) -> None:
    config_path = tmp_path / "bwli-config.json"
    config_path.write_text(
        '{"bw":{"url_ref":"env://BW_URL"},"llm":{"enabled":false}}\n',
        encoding="utf-8",
    )

    config = AppConfig.from_file(config_path)

    assert config.bw.url_ref == "env://BW_URL"
    assert config.llm.enabled is False
