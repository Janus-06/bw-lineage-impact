from __future__ import annotations

import pytest

import bwli.config as config_module
from bwli.config import (
    AppConfig,
    BwConnectionConfig,
    ConfigError,
    DataGateConfig,
    LlmConfig,
    LlmRuntimeConfig,
    load_bw_cookie_file,
)


def test_bw_env_config_loads_reference_mcp_names(monkeypatch: pytest.MonkeyPatch) -> None:
    credential_value = "fakepass"
    monkeypatch.delenv("BW_COOKIE_FILE", raising=False)
    monkeypatch.setenv("BW_URL", "https://bw.example.invalid")
    monkeypatch.setenv("BW_USER", "fixture-user")
    monkeypatch.setenv("BW_PASSWORD", credential_value)
    monkeypatch.setenv("BW_CLIENT", "100")
    monkeypatch.setenv("BW_LANGUAGE", "KO")

    config = BwConnectionConfig.from_env()

    assert config.url == "https://bw.example.invalid"
    assert config.user == "fixture-user"
    assert config.password is not None
    assert config.password.get_secret_value() == credential_value
    assert config.client == "100"
    assert config.language == "KO"


def test_bw_env_config_accepts_optional_ca_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("BW_COOKIE_FILE", raising=False)
    ca_bundle = tmp_path / "corp-ca.pem"
    ca_bundle.write_text(
        "-----BEGIN CERTIFICATE-----\nfixture\n-----END CERTIFICATE-----\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BW_URL", "https://bw.example.invalid")
    monkeypatch.setenv("BW_USER", "fixture-user")
    monkeypatch.setenv("BW_PASSWORD", "fakepass")
    monkeypatch.setenv("BW_CLIENT", "100")
    monkeypatch.setenv("BW_CA_BUNDLE", str(ca_bundle))

    config = BwConnectionConfig.from_env()

    assert config.verify_ssl is True
    assert config.ca_bundle == str(ca_bundle)
    assert config.trust_env is True
    assert config.httpx_verify_arg() == str(ca_bundle)


def test_cookie_file_requires_safe_permissions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    cookie_file = tmp_path / "bw-cookies.txt"
    cookie_file.write_text(
        "SAP_SESSIONID=file-session; __VCAP_ID__=app-instance\n",
        encoding="utf-8",
    )
    cookie_file.chmod(0o644)
    monkeypatch.setenv("BW_URL", "https://bw.example.invalid")
    monkeypatch.setenv("BW_CLIENT", "100")
    monkeypatch.setenv("BW_COOKIE_FILE", str(cookie_file))
    monkeypatch.delenv("BW_USER", raising=False)
    monkeypatch.delenv("BW_PASSWORD", raising=False)

    with pytest.raises(ConfigError) as excinfo:
        BwConnectionConfig.from_env()

    assert "group/other accessible" in str(excinfo.value)
    assert str(cookie_file) not in str(excinfo.value)


def test_bw_env_config_cookie_file_allows_missing_basic_auth(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    cookie_file = tmp_path / "bw-cookies.txt"
    cookie_file.write_text(
        ".example.invalid TRUE / TRUE 1893456000 SAP_SESSIONID file-session\n"
        ".example.invalid TRUE / TRUE 1893456000 __VCAP_ID__ app-instance\n",
        encoding="utf-8",
    )
    cookie_file.chmod(0o600)
    monkeypatch.setenv("BW_URL", "https://bw.example.invalid")
    monkeypatch.setenv("BW_CLIENT", "100")
    monkeypatch.setenv("BW_COOKIE_FILE", str(cookie_file))
    monkeypatch.delenv("BW_USER", raising=False)
    monkeypatch.delenv("BW_PASSWORD", raising=False)

    config = BwConnectionConfig.from_env()

    assert config.user is None
    assert config.password is None
    assert config.cookie_file == str(cookie_file)
    assert load_bw_cookie_file(cookie_file) == {
        "SAP_SESSIONID": "file-session",
        "__VCAP_ID__": "app-instance",
    }
    assert "file-session" not in str(config.model_dump())


def test_cookie_file_supports_raw_cookie_header(tmp_path) -> None:
    cookie_file = tmp_path / "raw-cookies.txt"
    cookie_file.write_text(
        "Cookie: SAP_SESSIONID=file-session; __VCAP_ID__=app-instance\n",
        encoding="utf-8",
    )
    cookie_file.chmod(0o600)

    assert load_bw_cookie_file(cookie_file) == {
        "SAP_SESSIONID": "file-session",
        "__VCAP_ID__": "app-instance",
    }


def test_data_gate_defaults_and_row_cap() -> None:
    disabled = DataGateConfig()

    assert disabled.enabled is False
    assert disabled.allow_llm_rows is False
    with pytest.raises(ConfigError):
        disabled.enforce_row_cap(10)

    enabled = DataGateConfig(enabled=True, max_rows=25)

    assert enabled.enforce_row_cap(None) == 25
    assert enabled.enforce_row_cap(5) == 5
    assert enabled.enforce_row_cap(500) == 25
    with pytest.raises(ConfigError):
        enabled.require_llm_rows_allowed()


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


def test_llm_enabled_accepts_remote_runtime_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BWLI_LLM_BASE_URL", "https://llm-gateway.example.invalid/v1")
    monkeypatch.setenv("BWLI_LLM_MODEL", "local-fixture-model")
    monkeypatch.setenv("BWLI_LLM_API_KEY", "dummy-key")
    monkeypatch.setattr(
        config_module,
        "_resolve_hostname_addresses",
        lambda _host, _port: ["93.184.216.34"],
    )

    runtime = LlmConfig(enabled=True).resolve_runtime()

    assert isinstance(runtime, LlmRuntimeConfig)
    assert runtime.base_url == "https://llm-gateway.example.invalid/v1"


def test_llm_enabled_rejects_link_local_metadata_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BWLI_LLM_BASE_URL", "http://169.254.169.254/v1")
    monkeypatch.setenv("BWLI_LLM_MODEL", "local-fixture-model")
    monkeypatch.setenv("BWLI_LLM_API_KEY", "dummy-key")

    with pytest.raises(ConfigError):
        LlmConfig(enabled=True).resolve_runtime()


def test_llm_enabled_rejects_hostname_resolving_to_link_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BWLI_LLM_BASE_URL", "http://metadata.example.invalid/v1")
    monkeypatch.setenv("BWLI_LLM_MODEL", "local-fixture-model")
    monkeypatch.setenv("BWLI_LLM_API_KEY", "dummy-key")
    monkeypatch.setattr(
        config_module,
        "_resolve_hostname_addresses",
        lambda _host, _port: ["169.254.169.254"],
        raising=False,
    )

    with pytest.raises(ConfigError):
        LlmConfig(enabled=True).resolve_runtime()


def test_llm_enabled_rejects_unresolved_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BWLI_LLM_BASE_URL", "https://llm-gateway.example.invalid/v1")
    monkeypatch.setenv("BWLI_LLM_MODEL", "local-fixture-model")
    monkeypatch.setenv("BWLI_LLM_API_KEY", "dummy-key")
    monkeypatch.setattr(
        config_module,
        "_resolve_hostname_addresses",
        lambda _host, _port: [],
    )

    with pytest.raises(ConfigError):
        LlmConfig(enabled=True).resolve_runtime()


def test_llm_enabled_rejects_localhost_resolving_to_link_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BWLI_LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("BWLI_LLM_MODEL", "local-fixture-model")
    monkeypatch.setenv("BWLI_LLM_API_KEY", "dummy-key")
    monkeypatch.setattr(
        config_module,
        "_resolve_hostname_addresses",
        lambda _host, _port: ["169.254.169.254"],
    )

    with pytest.raises(ConfigError):
        LlmConfig(enabled=True).resolve_runtime()


def test_llm_enabled_rejects_invalid_port_as_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BWLI_LLM_BASE_URL", "http://llm.example.invalid:notaport/v1")
    monkeypatch.setenv("BWLI_LLM_MODEL", "local-fixture-model")
    monkeypatch.setenv("BWLI_LLM_API_KEY", "dummy-key")

    with pytest.raises(ConfigError):
        LlmConfig(enabled=True).resolve_runtime()


def test_llm_enabled_accepts_private_non_loopback_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    for base_url in [
        "http://10.0.0.1/v1",
        "http://172.16.0.1/v1",
        "http://192.168.1.50/v1",
    ]:
        monkeypatch.setenv("BWLI_LLM_BASE_URL", base_url)
        monkeypatch.setenv("BWLI_LLM_MODEL", "local-fixture-model")
        monkeypatch.setenv("BWLI_LLM_API_KEY", "dummy-key")

        runtime = LlmConfig(enabled=True).resolve_runtime()

        assert isinstance(runtime, LlmRuntimeConfig)
        assert runtime.base_url == base_url


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
