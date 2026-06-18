from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bwli.server import create_app

FIXTURES = Path("tests/fixtures")


class FakeLiveBwClient:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.closed = False

    def fetch_search(self, search_term: str, *, object_type: str | None = None) -> dict[str, Any]:
        self.calls.append(("search", search_term, object_type))
        return {"objects": [{"name": "ZCUBE"}, {"name": "ZQUERY"}]}

    def fetch_dataflow(
        self,
        object_name: str,
        *,
        object_type: str = "ADSO",
        source_system: str | None = None,
        direction: str = "downwards",
        levels: int = 3,
    ) -> str:
        self.calls.append(("dataflow", object_name, object_type, source_system, direction, levels))
        return """
<dmod:dataFlow>
  <node nodeID=\"1\" objectName=\"ZADSO_SRC\" objectType=\"ADSO\" objectDescription=\"Source\">
    <targetNode>#///2</targetNode>
  </node>
  <node nodeID=\"2\" objectName=\"ZHCPR_MAIN\" objectType=\"HCPR\" objectDescription=\"Provider\">
    <sourceNode>#///1</sourceNode>
  </node>
</dmod:dataFlow>
""".strip()

    def fetch_xref(
        self,
        object_name: str,
        *,
        object_type: str = "ADSO",
        source_system: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(("xref", object_name, object_type, source_system))
        return {"references": [{"from": object_name, "to": "ZQUERY"}]}

    def close(self) -> None:
        self.closed = True


class FailingDataflowBwClient(FakeLiveBwClient):
    def __init__(self, leaked_value: str) -> None:
        super().__init__()
        self._leaked_value = leaked_value

    def fetch_dataflow(
        self,
        object_name: str,
        *,
        object_type: str = "ADSO",
        source_system: str | None = None,
        direction: str = "downwards",
        levels: int = 3,
    ) -> str:
        self.calls.append(("dataflow", object_name, object_type, source_system, direction, levels))
        raise RuntimeError(f"authorization token={self._leaked_value} failed")


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


def test_sql_view_endpoint_rejects_absolute_path_outside_project_root(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.sql"
    outside.write_text("SELECT * FROM sensitive_table", encoding="utf-8")
    client = TestClient(create_app(project_root=root))

    response = client.post(
        "/api/sql-view",
        json={"view_id": "ZSQL_VIEW", "sql_file": str(outside), "format": "json"},
    )

    assert response.status_code == 400
    assert "project root" in response.text
    assert "sensitive_table" not in response.text


def test_sql_view_endpoint_rejects_parent_directory_traversal(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.sql"
    outside.write_text("SELECT * FROM escaped_table", encoding="utf-8")
    client = TestClient(create_app(project_root=root))

    response = client.post(
        "/api/sql-view",
        json={"view_id": "ZSQL_VIEW", "sql_file": "../outside.sql", "format": "json"},
    )

    assert response.status_code == 400
    assert "project root" in response.text
    assert "escaped_table" not in response.text


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


def test_runtime_config_is_stored_in_memory_and_redacts_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        "ca_bundle": None,
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


def test_runtime_config_accepts_remote_llm_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(create_app(project_root=Path.cwd()))
    monkeypatch.setattr(
        "bwli.config._resolve_hostname_addresses",
        lambda _host, _port: ["93.184.216.34"],
    )

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

    assert response.status_code == 200
    payload = response.json()
    assert payload["llm"]["enabled"] is True
    assert payload["llm"]["configured"] is True
    assert payload["llm"]["base_url"] == "https://api.openai.com/v1"
    assert payload["llm"]["api_key"] == "[REDACTED]"
    assert "fixture-llm-secret" not in response.text


def test_runtime_config_rejects_invalid_llm_port_without_echoing_secret() -> None:
    client = TestClient(create_app(project_root=Path.cwd()))

    response = client.put(
        "/api/runtime-config",
        json={
            "llm": {
                "enabled": True,
                "base_url": "http://llm.example.invalid:notaport/v1",
                "model": "remote-model",
                "api_key": "do-not-render-llm-key",
            }
        },
    )

    assert response.status_code == 400
    assert "do-not-render-llm-key" not in response.text


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
                "base_url": "http://169.254.169.254/v1",
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


def test_legacy_runtime_config_clear_ignores_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BW_COOKIE_FILE", raising=False)
    monkeypatch.setenv("BW_URL", "https://bw.example.invalid")
    monkeypatch.setenv("BW_USER", "env-user")
    monkeypatch.setenv("BW_PASSWORD", "env-password")
    monkeypatch.setenv("BW_CLIENT", "100")
    client = TestClient(create_app(project_root=Path.cwd()))
    assert client.get("/api/runtime-config").json()["bw"]["configured"] is True

    response = client.delete("/api/runtime-config")

    assert response.status_code == 200
    assert response.json()["bw"]["configured"] is False


def _put_runtime_bw_config(client: TestClient, *, password: str = "fixture-secret-value") -> None:
    response = client.put(
        "/api/runtime-config",
        json={
            "bw": {
                "url": "https://bw.example.invalid",
                "user": "fixture-user",
                "password": password,
                "client": "100",
                "language": "EN",
                "verify_ssl": True,
            }
        },
    )
    assert response.status_code == 200


def test_live_smoke_rejects_missing_runtime_config() -> None:
    fake = FakeLiveBwClient()
    client = TestClient(create_app(project_root=Path.cwd(), bw_client_factory=lambda _state: fake))

    response = client.post("/api/live/smoke", json={"confirm_read_only": True, "search_term": "Z"})

    assert response.status_code == 400
    assert "BW runtime config" in response.text
    assert fake.calls == []


def test_live_smoke_requires_explicit_read_only_confirmation() -> None:
    fake = FakeLiveBwClient()
    client = TestClient(create_app(project_root=Path.cwd(), bw_client_factory=lambda _state: fake))
    _put_runtime_bw_config(client)

    response = client.post("/api/live/smoke", json={"search_term": "Z", "object_name": "ZCUBE"})

    assert response.status_code == 400
    assert "confirm_read_only" in response.text
    assert fake.calls == []


def test_live_smoke_uses_runtime_config_and_returns_safe_operation_summaries() -> None:
    fake = FakeLiveBwClient()
    client = TestClient(create_app(project_root=Path.cwd(), bw_client_factory=lambda _state: fake))
    _put_runtime_bw_config(client)

    response = client.post(
        "/api/live/smoke",
        json={
            "confirm_read_only": True,
            "search_term": "Z",
            "object_name": "ZCUBE",
            "xref_direction": "upstream",
        },
    )

    assert response.status_code == 200
    assert "fixture-user" not in response.text
    payload = response.json()
    assert payload["mode"] == "live-read-only"
    assert payload["read_only"] is True
    assert payload["status"] == "ok"
    assert [operation["name"] for operation in payload["operations"]] == [
        "bw_search",
        "bw_get_dataflow",
        "bw_xref",
    ]
    assert payload["operations"][0]["item_count"] == 2
    assert payload["operations"][2]["label"] == "bw://bw_xref?objectType=ADSO&objectName=ZCUBE"
    assert fake.calls == [
        ("search", "Z", None),
        ("dataflow", "ZCUBE", "ADSO", None, "downwards", 3),
        ("xref", "ZCUBE", "ADSO", None),
    ]
    assert fake.closed is True


def test_live_smoke_redacts_runtime_secret_from_partial_error() -> None:
    leaked_value = "redaction-target-value"
    fake = FailingDataflowBwClient(leaked_value)
    client = TestClient(create_app(project_root=Path.cwd(), bw_client_factory=lambda _state: fake))
    _put_runtime_bw_config(client, password=leaked_value)

    response = client.post(
        "/api/live/smoke",
        json={
            "confirm_read_only": True,
            "search_term": "Z",
            "object_name": "ZCUBE",
        },
    )

    assert response.status_code == 200
    assert leaked_value not in response.text
    payload = response.json()
    assert payload["status"] == "partial"
    dataflow_error = payload["operations"][1]["error"]
    assert dataflow_error == "authorization token=[REDACTED] failed"
    assert fake.closed is True


def test_live_collect_writes_local_snapshot_manifest_without_secrets(tmp_path: Path) -> None:
    fake = FakeLiveBwClient()
    client = TestClient(create_app(project_root=tmp_path, bw_client_factory=lambda _state: fake))
    _put_runtime_bw_config(client)

    response = client.post(
        "/api/collect/live",
        json={
            "confirm_read_only": True,
            "out_dir": "snapshots/live",
            "search_terms": ["Z"],
            "object_names": ["ZCUBE"],
            "include_dataflow": True,
            "include_xref": True,
            "xref_direction": "downstream",
        },
    )

    assert response.status_code == 200
    assert "fixture-user" not in response.text
    payload = response.json()
    assert payload["mode"] == "live-read-only"
    assert payload["read_only"] is True
    assert payload["manifest"]["mode"] == "live-read-only"
    assert len(payload["manifest"]["payloads"]) == 3
    manifest_path = tmp_path / "snapshots/live/manifest.json"
    assert manifest_path.exists()
    manifest_text = manifest_path.read_text(encoding="utf-8")
    assert "fixture-user" not in manifest_text
    assert "bw://bw_search" in manifest_text
    assert "bw://bw_xref?objectType=ADSO&objectName=ZCUBE" in manifest_text
    assert "bw://bw_xref/downstream" not in manifest_text
    assert fake.calls == [
        ("search", "Z", None),
        ("dataflow", "ZCUBE", "ADSO", None, "downwards", 3),
        ("xref", "ZCUBE", "ADSO", None),
    ]


def test_live_dataflow_endpoint_renders_mermaid_without_secrets() -> None:
    fake = FakeLiveBwClient()
    client = TestClient(create_app(project_root=Path.cwd(), bw_client_factory=lambda _state: fake))
    _put_runtime_bw_config(client)

    response = client.post(
        "/api/live/dataflow",
        json={
            "confirm_read_only": True,
            "object_name": "ZHCPR_MAIN",
            "object_type": "HCPR",
            "direction": "both",
            "levels": 2,
            "format": "mermaid",
        },
    )

    assert response.status_code == 200
    assert "fixture-user" not in response.text
    payload = response.json()
    assert payload["format"] == "mermaid"
    assert payload["content"].startswith("flowchart LR")
    assert "N1 --> N2" in payload["content"]
    assert fake.calls == [("dataflow", "ZHCPR_MAIN", "HCPR", None, "both", 2)]


def test_live_collect_rejects_output_path_escape(tmp_path: Path) -> None:
    fake = FakeLiveBwClient()
    client = TestClient(create_app(project_root=tmp_path, bw_client_factory=lambda _state: fake))
    _put_runtime_bw_config(client)

    response = client.post(
        "/api/collect/live",
        json={
            "confirm_read_only": True,
            "out_dir": "../outside",
            "search_terms": ["Z"],
        },
    )

    assert response.status_code == 400
    assert "outside project root" in response.text
    assert fake.calls == []


def test_connection_test_endpoint_returns_live_smoke_result() -> None:
    fake = FakeLiveBwClient()
    client = TestClient(create_app(project_root=Path.cwd(), bw_client_factory=lambda _state: fake))
    _put_runtime_bw_config(client)

    response = client.post(
        "/api/v1/connection/test",
        json={"confirm_read_only": True, "search_term": "Z"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert [op["name"] for op in payload["operations"]] == ["bw_search"]
    assert "fixture-user" not in response.text
    assert client.get("/api/v1/runtime-config").json()["connection_status"] == "ok"


class HostLeakingSearchClient(FakeLiveBwClient):
    def __init__(self, host_url: str) -> None:
        super().__init__()
        self._host_url = host_url

    def fetch_search(self, search_term: str, *, object_type: str | None = None) -> dict[str, Any]:
        raise RuntimeError(
            f"HTTP 401 from {self._host_url}/sap/bw/modeling?"
            "sap-client=100 password=mock-leaked-bw-password"
        )


def test_connection_test_redacts_bw_host_and_secret_in_error_detail() -> None:
    leaking = HostLeakingSearchClient(host_url="https://bw.example.invalid")
    client = TestClient(
        create_app(project_root=Path.cwd(), bw_client_factory=lambda _state: leaking)
    )
    _put_runtime_bw_config(client, password="mock-leaked-bw-password")

    response = client.put(
        "/api/v1/runtime-config",
        json={
            "bw": {
                "url": "https://bw.example.invalid",
                "user": "fixture-user",
                "password": "mock-leaked-bw-password",
                "client": "100",
                "language": "EN",
                "verify_ssl": True,
            }
        },
    )
    assert response.status_code == 200

    response = client.post(
        "/api/v1/connection/test",
        json={"confirm_read_only": True, "search_term": "Z"},
    )

    assert response.status_code == 200
    assert "mock-leaked-bw-password" not in response.text
    assert "bw.example.invalid" not in response.text
    assert "sap-client=100" in response.text
    payload = response.json()
    search_op = next(op for op in payload["operations"] if op["name"] == "bw_search")
    assert search_op["ok"] is False
    assert "[BW_HOST]" in search_op["error"] or "[BW_URL]" in search_op["error"]
    assert "[REDACTED]" in search_op["error"]
    assert "sap-client=100" in search_op["error"]
    assert client.get("/api/v1/runtime-config").json()["connection_status"] == "failed"


def test_capture_snapshot_partial_success_preserves_succeeded_payloads(tmp_path: Path) -> None:
    class PartialFailureClient(FakeLiveBwClient):
        def fetch_dataflow(
            self,
            object_name: str,
            *,
            object_type: str = "ADSO",
            source_system: str | None = None,
            direction: str = "downwards",
            levels: int = 3,
        ) -> str:
            if object_name == "ZBAD":
                raise RuntimeError(
                    "401 unauthorized https://bw.example.invalid/sap/bw "
                    "token=mock-leaked-bw-password"
                )
            return super().fetch_dataflow(
                object_name,
                object_type=object_type,
                source_system=source_system,
                direction=direction,
                levels=levels,
            )

    partial = PartialFailureClient()
    client = TestClient(
        create_app(project_root=tmp_path, bw_client_factory=lambda _state: partial)
    )
    _put_runtime_bw_config(client, password="mock-leaked-bw-password")
    ready = client.post(
        "/api/v1/connection/test",
        json={"confirm_read_only": True, "search_term": "Z"},
    )
    assert ready.status_code == 200, ready.text

    response = client.post(
        "/api/v1/snapshots/capture",
        json={
            "confirm_read_only": True,
            "object_names": ["ZOK", "ZBAD"],
            "include_dataflow": True,
            "include_xref": False,
        },
    )

    assert response.status_code == 200
    assert "mock-leaked-bw-password" not in response.text
    assert "bw.example.invalid" not in response.text
    payload = response.json()
    capture = payload["capture"]
    assert capture["mode"] == "live-read-only"
    assert capture["succeeded"] == 1
    assert capture["failed"] == 1
    op_status = {op["name"] + ":" + op["label"]: op for op in capture["operations"]}
    failing = [op for op in capture["operations"] if op["ok"] is False]
    assert len(failing) == 1
    assert "mock-leaked-bw-password" not in failing[0]["error"]
    assert "bw.example.invalid" not in failing[0]["error"]
    assert capture["operations"]
    assert op_status  # spot-check shape
    scope = payload["capture_scope"]
    selected_scope = {
        (entry["object_id"], entry["operation"]): entry
        for entry in scope
        if entry["role"] == "selected"
    }
    assert selected_scope[("ZOK", "bw_get_dataflow")]["status"] == "ok"
    assert selected_scope[("ZBAD", "bw_get_dataflow")]["status"] == "error"
    assert "mock-leaked-bw-password" not in selected_scope[("ZBAD", "bw_get_dataflow")]["error"]
    assert "bw.example.invalid" not in selected_scope[("ZBAD", "bw_get_dataflow")]["error"]

    scope_response = client.get(f"/api/v1/snapshots/{payload['id']}/capture-scope")
    assert scope_response.status_code == 200
    assert scope_response.json()["items"] == scope


def test_capture_snapshot_returns_error_when_every_live_call_fails(tmp_path: Path) -> None:
    class FailingClient(FakeLiveBwClient):
        def fetch_dataflow(
            self,
            object_name: str,
            *,
            object_type: str = "ADSO",
            source_system: str | None = None,
            direction: str = "downwards",
            levels: int = 3,
        ) -> str:
            raise RuntimeError("HTTP 500 from https://bw.example.invalid/sap/bw")

    failing = FailingClient()
    client = TestClient(
        create_app(project_root=tmp_path, bw_client_factory=lambda _state: failing)
    )
    _put_runtime_bw_config(client, password="mock-leaked-bw-password")
    ready = client.post(
        "/api/v1/connection/test",
        json={"confirm_read_only": True, "search_term": "Z"},
    )
    assert ready.status_code == 200, ready.text

    response = client.post(
        "/api/v1/snapshots/capture",
        json={
            "confirm_read_only": True,
            "object_names": ["ZBAD"],
            "include_dataflow": True,
            "include_xref": False,
        },
    )

    assert response.status_code == 400
    assert "no payloads collected" in response.text
    assert "bw_get_dataflow" in response.text
    assert "mock-leaked-bw-password" not in response.text
    assert "bw.example.invalid" not in response.text
