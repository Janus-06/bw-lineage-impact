from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from bwli.server import create_app

FIXTURES = Path("tests/fixtures")


def test_health_reports_local_read_only_server() -> None:
    client = TestClient(create_app(project_root=Path.cwd()))

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "local_only": True,
        "read_only": True,
        "llm_enabled_by_default": False,
        "version": "0.1.0",
    }


def test_lineage_endpoint_renders_markdown_from_local_graph() -> None:
    client = TestClient(create_app(project_root=Path.cwd()))

    response = client.post(
        "/api/lineage",
        json={
            "graph_path": str(FIXTURES / "sample-graph.json"),
            "object_id": "SRC",
            "direction": "downstream",
            "max_depth": 3,
            "format": "md",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["format"] == "md"
    assert "# Lineage Report" in payload["content"]
    assert "`SRC`" in payload["content"]
    assert "`QRY`" in payload["content"]


def test_impact_endpoint_renders_json_from_local_files() -> None:
    client = TestClient(create_app(project_root=Path.cwd()))

    response = client.post(
        "/api/impact",
        json={
            "graph_path": str(FIXTURES / "sample-graph.json"),
            "changes_path": str(FIXTURES / "sample-changes.json"),
            "format": "json",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["format"] == "json"
    assert "chg-field-remove" in payload["content"]
    assert "finding:chg-field-remove:TR" in payload["content"]


def test_sql_view_endpoint_uses_local_sql_file_only() -> None:
    client = TestClient(create_app(project_root=Path.cwd()))

    response = client.post(
        "/api/sql-view",
        json={
            "view_id": "ZSQL_VIEW",
            "sql_file": str(FIXTURES / "native_sql_view.sql"),
            "format": "md",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["format"] == "md"
    assert "# Native SQL View Evidence" in payload["content"]
    assert "no SQL rewrite or DB change is applied" in payload["content"]


def test_field_lineage_endpoint_renders_from_local_xml() -> None:
    client = TestClient(create_app(project_root=Path.cwd()))

    response = client.post(
        "/api/field-lineage",
        json={
            "xml_file": str(FIXTURES / "sample-transformation.xml"),
            "transformation_id": "T1",
            "source_object": "SRC",
            "target_object": "TGT",
            "format": "md",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["format"] == "md"
    assert "# Field Lineage Evidence" in payload["content"]
    assert "`NETVAL` <= `AMOUNT`" in payload["content"]


def test_runtime_config_is_stored_in_memory_and_redacts_secrets(monkeypatch) -> None:
    monkeypatch.delenv("BW_URL", raising=False)
    monkeypatch.delenv("BW_USER", raising=False)
    monkeypatch.delenv("BW_PASSWORD", raising=False)
    client = TestClient(create_app(project_root=Path.cwd()))

    initial = client.get("/api/runtime-config")
    assert initial.status_code == 200
    assert initial.json()["bw"]["configured"] is False
    assert initial.json()["llm"]["enabled"] is False

    response = client.put(
        "/api/runtime-config",
        json={
            "bw": {
                "url": "https://bw.example.invalid",
                "user": "fixture-user",
                "password": "fixture-secret-value",
                "client": "100",
                "language": "EN",
                "verify_ssl": True,
            },
            "llm": {
                "enabled": True,
                "base_url": "http://127.0.0.1:11434/v1",
                "model": "fixture-local-model",
                "api_key": "fixture-llm-secret",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["storage"] == "process-memory"
    assert payload["bw"] == {
        "configured": True,
        "url": "https://bw.example.invalid",
        "user": "fixture-user",
        "password": "[REDACTED]",
        "client": "100",
        "language": "EN",
        "verify_ssl": True,
    }
    assert payload["llm"] == {
        "enabled": True,
        "configured": True,
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "fixture-local-model",
        "api_key": "[REDACTED]",
    }
    assert os.environ.get("BW_PASSWORD") is None
    assert "fixture-secret-value" not in response.text
    assert "fixture-llm-secret" not in response.text


def test_runtime_config_rejects_non_local_llm_endpoint() -> None:
    client = TestClient(create_app(project_root=Path.cwd()))

    response = client.put(
        "/api/runtime-config",
        json={
            "llm": {
                "enabled": True,
                "base_url": "https://api.openai.com/v1",
                "model": "remote-model",
                "api_key": "fixture-llm-secret",
            }
        },
    )

    assert response.status_code == 400
    assert "loopback/local host" in response.text
    assert "fixture-llm-secret" not in response.text


def test_runtime_config_validation_error_does_not_echo_submitted_secrets() -> None:
    client = TestClient(create_app(project_root=Path.cwd()))

    response = client.put(
        "/api/runtime-config",
        json={
            "bw": {
                "url": "https://bw.example.invalid",
                "user": "fixture-user",
                "password": {"leak": "do-not-render-bw-password"},
                "client": "100",
                "unexpected_secret": "do-not-render-extra-secret",
            },
            "llm": {
                "enabled": True,
                "base_url": "http://127.0.0.1:11434/v1",
                "model": "fixture-local-model",
                "api_key": {"leak": "do-not-render-llm-key"},
            },
        },
    )

    assert response.status_code == 422
    assert "do-not-render-bw-password" not in response.text
    assert "do-not-render-extra-secret" not in response.text
    assert "do-not-render-llm-key" not in response.text


def test_runtime_config_failed_put_is_atomic() -> None:
    client = TestClient(create_app(project_root=Path.cwd()))

    response = client.put(
        "/api/runtime-config",
        json={
            "bw": {
                "url": "https://bw.example.invalid",
                "user": "fixture-user",
                "password": "do-not-render-bw-password",
                "client": "100",
            },
            "llm": {
                "enabled": True,
                "base_url": "https://api.openai.com/v1",
                "model": "remote-model",
                "api_key": "do-not-render-llm-key",
            },
        },
    )

    assert response.status_code == 400
    assert "do-not-render-bw-password" not in response.text
    assert "do-not-render-llm-key" not in response.text
    state = client.get("/api/runtime-config").json()
    assert state["bw"]["configured"] is False
    assert state["llm"]["configured"] is False


def test_runtime_config_rejects_blank_bw_secret_without_prior_config() -> None:
    client = TestClient(create_app(project_root=Path.cwd()))

    response = client.put(
        "/api/runtime-config",
        json={
            "bw": {
                "url": "https://bw.example.invalid",
                "user": "fixture-user",
                "password": "",
                "client": "100",
            }
        },
    )

    assert response.status_code == 400
    assert "password" in response.text
    assert client.get("/api/runtime-config").json()["bw"]["configured"] is False


def test_runtime_config_reuses_existing_secrets_when_secret_fields_are_blank() -> None:
    client = TestClient(create_app(project_root=Path.cwd()))

    assert client.put(
        "/api/runtime-config",
        json={
            "bw": {
                "url": "https://bw.example.invalid",
                "user": "fixture-user",
                "password": "do-not-render-bw-password",
                "client": "100",
            },
            "llm": {
                "enabled": True,
                "base_url": "http://127.0.0.1:11434/v1",
                "model": "fixture-local-model",
                "api_key": "do-not-render-llm-key",
            },
        },
    ).status_code == 200

    response = client.put(
        "/api/runtime-config",
        json={
            "bw": {
                "url": "https://bw.example.invalid",
                "user": "fixture-user-2",
                "password": "",
                "client": "100",
            },
            "llm": {
                "enabled": True,
                "base_url": "http://127.0.0.1:11434/v1",
                "model": "fixture-local-model-2",
                "api_key": "",
            },
        },
    )

    assert response.status_code == 200
    assert "do-not-render-bw-password" not in response.text
    assert "do-not-render-llm-key" not in response.text
    payload = response.json()
    assert payload["bw"]["user"] == "fixture-user-2"
    assert payload["bw"]["password"] == "[REDACTED]"
    assert payload["llm"]["model"] == "fixture-local-model-2"
    assert payload["llm"]["api_key"] == "[REDACTED]"


def test_runtime_config_can_be_cleared() -> None:
    client = TestClient(create_app(project_root=Path.cwd()))
    assert client.put(
        "/api/runtime-config",
        json={
            "bw": {
                "url": "https://bw.example.invalid",
                "user": "u",
                "password": "p",
                "client": "100",
            }
        },
    ).status_code == 200

    response = client.delete("/api/runtime-config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["bw"]["configured"] is False
    assert payload["llm"]["configured"] is False
