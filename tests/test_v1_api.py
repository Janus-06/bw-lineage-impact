from __future__ import annotations

import stat
from pathlib import Path, PureWindowsPath

import pytest
from fastapi.testclient import TestClient

from bwli.config import LlmRuntimeConfig
from bwli.impact_evidence import ImpactEvidencePack
from bwli.llm.agentic_review import (
    AgenticReviewBudget,
    AgenticReviewBudgetUsage,
    AgenticReviewRun,
    ReviewTraceStep,
    deterministic_review_cards,
)
from bwli.llm.openai_compatible import LlmAuditMetadata, LlmCompletion
from bwli.server import _project_relative_path, create_app
from bwli.snapshot import SnapshotWriter
from bwli.store import CatalogStore, SecretPersistenceError

FIXTURES = Path("tests/fixtures")


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("BWLI_HOME", str(tmp_path / "bwli-home"))
    return TestClient(create_app(project_root=Path.cwd()))


def _capture_sample_graph(client: TestClient) -> str:
    response = client.post(
        "/api/v1/snapshots/capture",
        json={"fixture_path": str(FIXTURES / "sample-graph.json")},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["object_count"] == 5
    assert payload["edge_count"] == 5
    return str(payload["id"])


def test_v1_runtime_config_auto_seeds_env_and_clear_falls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BWLI_HOME", str(tmp_path / "bwli-home"))
    monkeypatch.delenv("BW_COOKIE_FILE", raising=False)
    monkeypatch.setenv("BW_URL", "https://bw.example.invalid")
    monkeypatch.setenv("BW_USER", "env-user")
    monkeypatch.setenv("BW_PASSWORD", "env-secret-value")
    monkeypatch.setenv("BW_CLIENT", "100")
    monkeypatch.setenv("BW_LANGUAGE", "KO")
    monkeypatch.setenv("BW_VERIFY_SSL", "false")
    monkeypatch.setenv("BW_TRUST_ENV", "false")
    client = TestClient(create_app(project_root=Path.cwd()))

    seeded = client.get("/api/v1/runtime-config")

    assert seeded.status_code == 200
    payload = seeded.json()
    assert payload["bw"]["configured"] is True
    assert payload["bw"]["source"] == "env"
    assert payload["bw"]["user"] == "env-user"
    assert payload["bw"]["password"] == "[REDACTED]"
    assert payload["bw"]["language"] == "KO"
    assert payload["bw"]["verify_ssl"] is False
    assert payload["bw"]["trust_env"] is False
    assert "env-secret-value" not in seeded.text

    overridden = client.put(
        "/api/v1/runtime-config",
        json={
            "bw": {
                "url": "https://ui.example.invalid",
                "user": "ui-user",
                "password": "ui-secret-value",
                "client": "200",
                "language": "EN",
                "verify_ssl": True,
            }
        },
    )
    assert overridden.status_code == 200
    assert overridden.json()["bw"]["source"] == "ui"
    assert overridden.json()["bw"]["user"] == "ui-user"
    assert "ui-secret-value" not in overridden.text

    cleared = client.delete("/api/v1/runtime-config")

    assert cleared.status_code == 200
    assert cleared.json()["bw"]["source"] == "env"
    assert cleared.json()["bw"]["user"] == "env-user"
    assert "env-secret-value" not in cleared.text


def test_v1_runtime_config_env_without_cookie_requires_bw_user(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("BWLI_HOME", str(tmp_path / "bwli-home"))
    monkeypatch.setenv("BW_URL", "https://bw.example.invalid")
    monkeypatch.setenv("BW_PASSWORD", "env-secret-value")
    monkeypatch.setenv("BW_CLIENT", "100")
    client = TestClient(create_app(project_root=tmp_path))

    response = client.get("/api/v1/runtime-config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["connection_status"] == "unconfigured"
    assert payload["bw"]["configured"] is False
    assert payload["bw"]["user"] is None
    assert payload["bw"]["password"] is None
    assert "env-secret-value" not in response.text


def test_v1_runtime_config_loads_project_dotenv_as_startup_seed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BWLI_HOME", str(tmp_path / "bwli-home"))
    for name in [
        "BW_URL",
        "BW_USER",
        "BW_PASSWORD",
        "BW_COOKIE_FILE",
        "BW_CLIENT",
        "BW_LANGUAGE",
        "BW_VERIFY_SSL",
        "BW_TRUST_ENV",
    ]:
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env"
    original_env = "\n".join(
        [
            'BW_URL="https://dotenv.example.invalid"',
            "BW_USER=dotenv-user",
            "BW_PASSWORD=dotenv-secret-value",
            "BW_CLIENT=300",
            "BW_LANGUAGE=KO",
            "BW_VERIFY_SSL=false",
            "BW_TRUST_ENV=false",
        ]
    )
    env_file.write_text(original_env + "\n", encoding="utf-8")

    client = TestClient(create_app(project_root=tmp_path))
    seeded = client.get("/api/v1/runtime-config")

    assert seeded.status_code == 200
    payload = seeded.json()
    assert payload["bw"]["configured"] is True
    assert payload["bw"]["source"] == "env"
    assert payload["bw"]["url"] == "https://dotenv.example.invalid"
    assert payload["bw"]["user"] == "dotenv-user"
    assert payload["bw"]["client"] == "300"
    assert payload["bw"]["language"] == "KO"
    assert payload["bw"]["verify_ssl"] is False
    assert payload["bw"]["trust_env"] is False
    assert payload["bw"]["password"] == "[REDACTED]"
    assert "dotenv-secret-value" not in seeded.text

    overridden = client.put(
        "/api/v1/runtime-config",
        json={
            "bw": {
                "url": "https://ui.example.invalid",
                "user": "ui-user",
                "password": "ui-secret-value",
                "client": "200",
                "language": "EN",
                "verify_ssl": True,
            }
        },
    )
    assert overridden.status_code == 200
    assert overridden.json()["bw"]["source"] == "ui"
    assert env_file.read_text(encoding="utf-8") == original_env + "\n"

    cleared = client.delete("/api/v1/runtime-config")

    assert cleared.status_code == 200
    assert cleared.json()["bw"]["source"] == "env"
    assert cleared.json()["bw"]["user"] == "dotenv-user"
    assert env_file.read_text(encoding="utf-8") == original_env + "\n"


_RUNTIME_ENV_KEYS = (
    "BW_URL",
    "BW_USER",
    "BW_PASSWORD",
    "BW_COOKIE_FILE",
    "BW_CLIENT",
    "BW_LANGUAGE",
    "BW_VERIFY_SSL",
    "BW_CA_BUNDLE",
    "BW_TRUST_ENV",
    "BWLI_LLM_BASE_URL",
    "BWLI_LLM_MODEL",
    "BWLI_LLM_API_KEY",
)


def _clear_runtime_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _RUNTIME_ENV_KEYS:
        monkeypatch.delenv(name, raising=False)


def test_v1_runtime_config_persist_to_env_writes_env_file_without_echoing_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("BWLI_HOME", str(tmp_path / "bwli-home"))
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# unrelated comment stays",
                "UNRELATED_TOOL_KEY=keep-me",
                "BW_URL=https://old.example.invalid",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    client = TestClient(create_app(project_root=tmp_path))

    response = client.put(
        "/api/v1/runtime-config",
        json={
            "persist_to_env": True,
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

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["storage"] == "process-memory+project-env"
    assert payload["bw"]["password"] == "[REDACTED]"
    assert payload["llm"]["api_key"] == "[REDACTED]"
    assert "fixture-secret-value" not in response.text
    assert "fixture-llm-secret" not in response.text

    content = env_file.read_text(encoding="utf-8")
    assert "# unrelated comment stays" in content
    assert "UNRELATED_TOOL_KEY=keep-me" in content
    assert "https://old.example.invalid" not in content
    assert 'BW_URL="https://bw.example.invalid"' in content
    assert 'BW_PASSWORD="fixture-secret-value"' in content
    assert 'BW_VERIFY_SSL="true"' in content
    assert 'BWLI_LLM_BASE_URL="http://127.0.0.1:11434/v1"' in content
    assert 'BWLI_LLM_API_KEY="fixture-llm-secret"' in content
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600

    memory_only = client.put(
        "/api/v1/runtime-config",
        json={
            "bw": {
                "url": "https://memory-only.example.invalid",
                "user": "memory-user",
                "password": "memory-secret-value",
                "client": "999",
                "language": "EN",
                "verify_ssl": True,
            }
        },
    )
    assert memory_only.status_code == 200, memory_only.text
    assert memory_only.json()["storage"] == "process-memory"
    assert memory_only.json()["bw"]["url"] == "https://memory-only.example.invalid"
    assert env_file.read_text(encoding="utf-8") == content

    reset = client.delete("/api/v1/runtime-config")
    assert reset.status_code == 200, reset.text
    assert reset.json()["bw"]["source"] == "env"
    assert reset.json()["bw"]["url"] == "https://bw.example.invalid"
    assert "fixture-secret-value" not in reset.text

    restarted = TestClient(create_app(project_root=tmp_path))
    seeded = restarted.get("/api/v1/runtime-config")
    assert seeded.status_code == 200
    seeded_payload = seeded.json()
    assert seeded_payload["storage"] == "process-memory"
    assert seeded_payload["bw"]["configured"] is True
    assert seeded_payload["bw"]["source"] == "env"
    assert seeded_payload["bw"]["url"] == "https://bw.example.invalid"
    assert seeded_payload["llm"]["configured"] is True
    assert "fixture-secret-value" not in seeded.text
    assert "fixture-llm-secret" not in seeded.text


@pytest.mark.parametrize("marker", ["[REDACTED]", "[REACTED]"])
def test_v1_runtime_config_persist_reuses_in_process_secret_for_placeholder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
) -> None:
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("BWLI_HOME", str(tmp_path / "bwli-home"))
    client = TestClient(create_app(project_root=tmp_path))
    bw_body = {
        "url": "https://bw.example.invalid",
        "user": "fixture-user",
        "password": "actual-secret-value",
        "client": "100",
        "language": "EN",
        "verify_ssl": True,
    }
    assert client.put("/api/v1/runtime-config", json={"bw": bw_body}).status_code == 200
    assert not (tmp_path / ".env").exists()

    response = client.put(
        "/api/v1/runtime-config",
        json={"persist_to_env": True, "bw": {**bw_body, "password": marker}},
    )

    assert response.status_code == 200, response.text
    assert response.json()["storage"] == "process-memory+project-env"
    assert "actual-secret-value" not in response.text
    content = (tmp_path / ".env").read_text(encoding="utf-8")
    assert 'BW_PASSWORD="actual-secret-value"' in content
    assert marker not in content


def test_v1_runtime_config_persist_rejects_placeholder_without_prior_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("BWLI_HOME", str(tmp_path / "bwli-home"))
    client = TestClient(create_app(project_root=tmp_path))

    response = client.put(
        "/api/v1/runtime-config",
        json={
            "persist_to_env": True,
            "bw": {
                "url": "https://bw.example.invalid",
                "user": "fixture-user",
                "password": "[REDACTED]",
                "client": "100",
            },
        },
    )

    assert response.status_code == 400
    assert "password" in response.text
    assert not (tmp_path / ".env").exists()


def test_v1_runtime_config_failed_env_persist_does_not_mutate_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("BWLI_HOME", str(tmp_path / "bwli-home"))
    client = TestClient(create_app(project_root=tmp_path))

    response = client.put(
        "/api/v1/runtime-config",
        json={
            "persist_to_env": True,
            "bw": {
                "url": "https://bw.example.invalid",
                "user": "fixture-user",
                "password": "bad\nsecret",
                "client": "100",
            },
        },
    )

    assert response.status_code == 400
    assert "line breaks" in response.text
    assert client.get("/api/v1/runtime-config").json()["bw"]["configured"] is False
    assert not (tmp_path / ".env").exists()


def test_v1_runtime_config_persist_round_trips_quoted_secret_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("BWLI_HOME", str(tmp_path / "bwli-home"))
    tricky_secret = 'pa#ss"word\\$value'
    client = TestClient(create_app(project_root=tmp_path))

    response = client.put(
        "/api/v1/runtime-config",
        json={
            "persist_to_env": True,
            "bw": {
                "url": "https://bw.example.invalid",
                "user": "fixture-user",
                "password": tricky_secret,
                "client": "100",
            },
        },
    )

    assert response.status_code == 200, response.text
    escaped_line = 'BW_PASSWORD="pa#ss\\"word\\\\$value"'
    assert escaped_line in (tmp_path / ".env").read_text(encoding="utf-8")

    # A restarted backend seeds the secret from .env; resubmitting only the
    # redaction marker and persisting again must rewrite the identical value,
    # proving the quote/unquote round trip is lossless.
    restarted = TestClient(create_app(project_root=tmp_path))
    resubmitted = restarted.put(
        "/api/v1/runtime-config",
        json={
            "persist_to_env": True,
            "bw": {
                "url": "https://bw.example.invalid",
                "user": "fixture-user",
                "password": "[REDACTED]",
                "client": "100",
            },
        },
    )
    assert resubmitted.status_code == 200, resubmitted.text
    assert escaped_line in (tmp_path / ".env").read_text(encoding="utf-8")
    assert tricky_secret not in resubmitted.text


def test_v1_runtime_config_persist_disabled_llm_removes_env_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_runtime_env(monkeypatch)
    monkeypatch.setenv("BWLI_HOME", str(tmp_path / "bwli-home"))
    client = TestClient(create_app(project_root=tmp_path))
    configured = client.put(
        "/api/v1/runtime-config",
        json={
            "persist_to_env": True,
            "llm": {
                "enabled": True,
                "base_url": "http://127.0.0.1:11434/v1",
                "model": "fixture-local-model",
                "api_key": "fixture-llm-secret",
            },
        },
    )
    assert configured.status_code == 200
    assert "BWLI_LLM_MODEL" in (tmp_path / ".env").read_text(encoding="utf-8")

    response = client.put(
        "/api/v1/runtime-config",
        json={"persist_to_env": True, "llm": {"enabled": False}},
    )

    assert response.status_code == 200
    content = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "BWLI_LLM_BASE_URL" not in content
    assert "BWLI_LLM_MODEL" not in content
    assert "BWLI_LLM_API_KEY" not in content


@pytest.mark.parametrize("marker", ["[REDACTED]", "[REACTED]"])
def test_v1_runtime_config_rejects_secret_placeholder_without_prior_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
) -> None:
    monkeypatch.setenv("BWLI_HOME", str(tmp_path / "bwli-home"))
    client = TestClient(create_app(project_root=tmp_path))

    response = client.put(
        "/api/v1/runtime-config",
        json={
            "bw": {
                "url": "https://bw.example.invalid",
                "user": "fixture-user",
                "password": marker,
                "client": "100",
                "language": "EN",
                "verify_ssl": True,
            }
        },
    )

    assert response.status_code == 400
    assert "password" in response.text
    assert client.get("/api/v1/runtime-config").json()["bw"]["configured"] is False


@pytest.mark.parametrize("marker", ["[REDACTED]", "[REACTED]"])
def test_v1_runtime_config_reuses_existing_secret_when_ui_resubmits_placeholder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
) -> None:
    monkeypatch.setenv("BWLI_HOME", str(tmp_path / "bwli-home"))
    captured_passwords: list[str | None] = []

    class FakeBwClient:
        def fetch_search(
            self,
            search_term: str,
            *,
            object_type: str | None = None,
        ) -> dict[str, object]:
            return {"term": search_term, "objects": []}

        def fetch_dataflow(
            self,
            object_name: str,
            *,
            object_type: str = "ADSO",
            source_system: str | None = None,
            direction: str = "downwards",
            levels: int = 3,
        ) -> str:
            return "<dmod:dataFlow />"

        def fetch_xref(
            self,
            object_name: str,
            *,
            object_type: str = "ADSO",
            source_system: str | None = None,
        ) -> dict[str, object]:
            return {"references": []}

        def close(self) -> None:
            return None

    def factory(state) -> FakeBwClient:
        captured_passwords.append(state.password)
        return FakeBwClient()

    client = TestClient(create_app(project_root=tmp_path, bw_client_factory=factory))
    configured = client.put(
        "/api/v1/runtime-config",
        json={
            "bw": {
                "url": "https://bw.example.invalid",
                "user": "fixture-user",
                "password": "actual-secret-value",
                "client": "100",
                "language": "EN",
                "verify_ssl": True,
            }
        },
    )
    assert configured.status_code == 200

    resubmitted = client.put(
        "/api/v1/runtime-config",
        json={
            "bw": {
                "url": "https://bw.example.invalid",
                "user": "fixture-user-renamed",
                "password": marker,
                "client": "100",
                "language": "EN",
                "verify_ssl": True,
            }
        },
    )
    assert resubmitted.status_code == 200, resubmitted.text
    assert resubmitted.json()["bw"]["user"] == "fixture-user-renamed"
    assert "actual-secret-value" not in resubmitted.text

    connection = client.post(
        "/api/v1/connection/test",
        json={"confirm_read_only": True, "search_term": "Z*"},
    )

    assert connection.status_code == 200
    assert captured_passwords == ["actual-secret-value"]


def test_v1_snapshot_capture_indexes_fixture_objects_and_edges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    snapshot_id = _capture_sample_graph(client)

    listed = client.get("/api/v1/snapshots")
    assert listed.status_code == 200
    assert listed.json()["snapshots"][0]["id"] == snapshot_id

    objects = client.get(
        f"/api/v1/snapshots/{snapshot_id}/objects",
        params={"q": "sales", "type": "QUERY", "limit": 10},
    )
    assert objects.status_code == 200
    object_payload = objects.json()
    assert object_payload["next_cursor"] is None
    assert [item["id"] for item in object_payload["items"]] == ["QRY"]
    assert object_payload["items"][0]["type"] == "QUERY"

    detail = client.get(f"/api/v1/snapshots/{snapshot_id}/objects/QRY")
    assert detail.status_code == 200
    assert detail.json()["incoming_count"] == 1
    assert detail.json()["outgoing_count"] == 1


def test_v1_snapshot_glossary_lists_metadata_seed_terms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    snapshot_id = _capture_sample_graph(client)

    response = client.get(
        f"/api/v1/snapshots/{snapshot_id}/glossary",
        params={"query": "sales"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["snapshot_id"] == snapshot_id
    assert payload["query"] == "sales"
    assert payload["count"] >= 2
    by_term = {item["term"]: item for item in payload["items"]}
    assert by_term["Sales Query"]["source"] == "metadata"
    assert by_term["Sales Query"]["candidate"] is True
    assert by_term["Sales Query"]["object_id"] == "QRY"
    assert by_term["Sales Query"]["object_type"] == "QUERY"
    assert by_term["Sales Query"]["field_name"] is None
    assert by_term["Sales Query"]["evidence_ids"]


def test_v1_manifest_capture_stores_project_relative_manifest_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BWLI_HOME", str(tmp_path / "bwli-home"))
    manifest_dir = tmp_path / "snapshots" / "imported"
    writer = SnapshotWriter(manifest_dir)
    metadata = writer.write_payload(
        payload_id="graph",
        kind="graph",
        source="fixture://inline-graph",
        payload={
            "nodes": [{"id": "SRC", "type": "ADSO"}, {"id": "TGT", "type": "QUERY"}],
            "edges": [{"id": "edge-1", "source": "SRC", "target": "TGT", "type": "feeds"}],
        },
    )
    writer.write_manifest(mode="offline-fixture", payloads=[metadata])
    client = TestClient(create_app(project_root=tmp_path))

    response = client.post(
        "/api/v1/snapshots/capture",
        json={"manifest_path": "snapshots/imported/manifest.json"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["manifest_path"] == "snapshots/imported/manifest.json"
    assert str(tmp_path) not in response.text

    listed = client.get("/api/v1/snapshots")
    assert listed.status_code == 200
    assert listed.json()["snapshots"][0]["manifest_path"] == "snapshots/imported/manifest.json"


class _WindowsResolvedPath:
    def __init__(self, value: str) -> None:
        self.value = PureWindowsPath(value)

    def resolve(self) -> PureWindowsPath:
        return self.value

    def __str__(self) -> str:
        return str(self.value)


def test_project_relative_path_uses_posix_separators_for_api_storage() -> None:
    relative_path = _project_relative_path(
        _WindowsResolvedPath("C:/repo"),  # type: ignore[arg-type]
        _WindowsResolvedPath("C:/repo/snapshots/imported/manifest.json"),  # type: ignore[arg-type]
    )

    assert relative_path == "snapshots/imported/manifest.json"


@pytest.mark.parametrize("cursor", ["abc", "-1"])
def test_v1_snapshot_objects_rejects_malformed_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cursor: str,
) -> None:
    client = _client(tmp_path, monkeypatch)
    snapshot_id = _capture_sample_graph(client)

    response = client.get(f"/api/v1/snapshots/{snapshot_id}/objects", params={"cursor": cursor})

    assert response.status_code == 400
    assert "cursor" in response.json()["detail"]


def test_freshness_endpoint_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "bwli-home"
    monkeypatch.setenv("BWLI_HOME", str(home))
    store = CatalogStore(home / "catalog.sqlite")
    snapshot = store.create_snapshot(mode="test", source="fixture://freshness")
    freshness = {
        "target": "ZADSO_SALES",
        "target_type": "ADSO",
        "latest": {
            "request_tsn": "TSN_NEW",
            "tsn": "REQ_NEW",
            "status": "G",
            "records": 43,
            "timestamp": "2026-06-17T12:05:00Z",
        },
    }
    bic_freshness = {
        "target": "/BIC/ZADSO",
        "target_type": "ADSO",
        "latest": {
            "request_tsn": "TSN_BIC",
            "tsn": "REQ_BIC",
            "status": "G",
            "records": 7,
            "timestamp": "2026-06-17T12:10:00Z",
        },
    }
    store.replace_catalog(
        snapshot.id,
        objects=[
            {
                "id": "ZADSO_SALES",
                "type": "ADSO",
                "metadata": {"request_freshness": freshness},
                "evidence_ids": ["request-detail:request-detail"],
            },
            {
                "id": "/BIC/ZADSO",
                "type": "ADSO",
                "metadata": {"request_freshness": bic_freshness},
                "evidence_ids": ["request-detail:bic-request-detail"],
            },
            {"id": "ZNO_FRESHNESS", "type": "ADSO"},
        ],
        edges=[],
    )
    client = TestClient(create_app(project_root=tmp_path))
    path = f"/api/v1/snapshots/{snapshot.id}/objects/ZADSO_SALES/freshness"

    response = client.get(path)

    assert response.status_code == 200, response.text
    assert response.json() == freshness

    bic_response = client.get(f"/api/v1/snapshots/{snapshot.id}/objects/%2FBIC%2FZADSO/freshness")
    assert bic_response.status_code == 200, bic_response.text
    assert bic_response.json() == bic_freshness

    for method in (client.post, client.put, client.patch, client.delete):
        mutating = method(path)
        assert mutating.status_code == 405

    missing_freshness = client.get(
        f"/api/v1/snapshots/{snapshot.id}/objects/ZNO_FRESHNESS/freshness"
    )
    assert missing_freshness.status_code == 404
    assert "request_freshness" in missing_freshness.text

    missing_object = client.get(f"/api/v1/snapshots/{snapshot.id}/objects/ZMISSING/freshness")
    assert missing_object.status_code == 404
    assert "object not found" in missing_object.text

    missing_snapshot = client.get("/api/v1/snapshots/snap-missing/objects/ZADSO_SALES/freshness")
    assert missing_snapshot.status_code == 404
    assert "snapshot not found" in missing_snapshot.text


def test_v1_bounded_lineage_reports_caps_omissions_and_cycles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    snapshot_id = _capture_sample_graph(client)

    capped = client.post(
        f"/api/v1/snapshots/{snapshot_id}/lineage",
        json={
            "object_id": "SRC",
            "direction": "downstream",
            "depth": 3,
            "node_cap": 3,
            "edge_cap": 2,
        },
    )
    assert capped.status_code == 200
    capped_payload = capped.json()
    assert capped_payload["truncated"] is True
    assert capped_payload["truncation"]["node_cap_reached"] is True
    assert len(capped_payload["nodes"]) <= 3
    assert len(capped_payload["edges"]) <= 2
    assert capped_payload["omitted_neighbor_counts"]
    assert "e1" in capped_payload["evidence_ids"]

    cycle = client.post(
        f"/api/v1/snapshots/{snapshot_id}/lineage",
        json={"object_id": "TGT", "direction": "downstream", "depth": 3, "node_cap": 25},
    )
    assert cycle.status_code == 200
    assert cycle.json()["cycles_detected"] is True


def test_v1_scenario_impact_is_deterministic_without_changes_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    snapshot_id = _capture_sample_graph(client)

    response = client.post(
        f"/api/v1/snapshots/{snapshot_id}/impact/scenario",
        json={
            "object_id": "SRC",
            "change_type": "field_removed",
            "field": "AMOUNT",
            "description": "컬럼 제거 영향 검토",
            "depth": 3,
            "node_cap": 25,
            "edge_cap": 60,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["advisory"] is False
    assert payload["deterministic"] is True
    assert payload["scenario"]["changes_path_required"] is False
    affected_ids = [item["object_id"] for item in payload["affected_objects"]]
    assert affected_ids == ["QRY", "TGT", "TR"]
    assert {item["severity"] for item in payload["affected_objects"]} == {"HIGH"}
    assert all(item["evidence_ids"] for item in payload["affected_objects"])
    query_item = next(item for item in payload["affected_objects"] if item["object_id"] == "QRY")
    assert query_item["glossary_terms"][0]["term"] == "Sales Query"


def test_v1_scenario_impact_applies_lineage_caps_before_analysis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    snapshot_id = _capture_sample_graph(client)

    response = client.post(
        f"/api/v1/snapshots/{snapshot_id}/impact/scenario",
        json={
            "object_id": "SRC",
            "change_type": "field_removed",
            "field": "AMOUNT",
            "depth": 3,
            "node_cap": 2,
            "edge_cap": 1,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["lineage_bounds"]["truncated"] is True
    assert [item["object_id"] for item in payload["affected_objects"]] == ["TR"]


def test_v1_impact_review_returns_deterministic_evidence_pack_without_llm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ["BWLI_LLM_BASE_URL", "BWLI_LLM_MODEL", "BWLI_LLM_API_KEY"]:
        monkeypatch.delenv(name, raising=False)
    client = _client(tmp_path, monkeypatch)
    snapshot_id = _capture_sample_graph(client)

    response = client.post(
        f"/api/v1/snapshots/{snapshot_id}/impact/review",
        json={
            "object_id": "SRC",
            "change_type": "field_removed",
            "field": "AMOUNT",
            "depth": 3,
            "node_cap": 25,
            "edge_cap": 60,
            "sql_views": [
                {
                    "view_id": "ZSQL_VIEW",
                    "sql_file": str(FIXTURES / "native_sql_view.sql"),
                }
            ],
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["deterministic"] is True
    assert payload["read_only"] is True
    assert payload["execution_blocked"] is True
    assert payload["final_authority"] == "impact.py"
    assert payload["impact"]["findings"][0]["confidence"] == "graph_rule"
    assert [item["query_id"] for item in payload["query_evidence"]] == ["QRY"]
    assert payload["query_evidence"][0]["metadata"]["unknown_reason"] == "PARSER_UNSUPPORTED"
    assert payload["sql_evidence"][0]["view_id"] == "ZSQL_VIEW"
    assert payload["sql_evidence"][0]["referenced_object_ids"] == [
        "zsales_fact",
        "zcustomer_dim",
    ]
    assert any(
        "without executing database SQL" in note
        for note in payload["sql_evidence"][0]["manual_check_notes"]
    )
    assert payload["coverage_summary"] == {
        "impact_finding_count": 3,
        "affected_object_count": 3,
        "query_evidence_count": 1,
        "sql_evidence_count": 1,
        "freshness_evidence_count": 0,
        "manual_gap_count": 2,
        "query_matched_finding_count": 1,
        "sql_matched_finding_count": 0,
    }
    assert {gap["source"] for gap in payload["manual_verification_gaps"]} == {"query", "sql"}
    assert "llm_audit" not in payload


def test_v1_impact_review_accepts_explicit_query_names_without_sql_views(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    snapshot_id = _capture_sample_graph(client)

    response = client.post(
        f"/api/v1/snapshots/{snapshot_id}/impact/review",
        json={
            "object_id": "SRC",
            "change_type": "field_removed",
            "field": "AMOUNT",
            "query_names": ["QRY"],
            "include_impacted_queries": False,
            "depth": 3,
            "node_cap": 25,
            "edge_cap": 60,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["deterministic"] is True
    assert payload["execution_blocked"] is True
    assert [item["query_id"] for item in payload["query_evidence"]] == ["QRY"]
    assert payload["sql_evidence"] == []
    assert payload["coverage_summary"]["query_evidence_count"] == 1
    assert payload["coverage_summary"]["sql_evidence_count"] == 0


def test_v1_impact_review_rejects_sql_evidence_without_parse_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    snapshot_id = _capture_sample_graph(client)

    response = client.post(
        f"/api/v1/snapshots/{snapshot_id}/impact/review",
        json={
            "object_id": "SRC",
            "change_type": "field_removed",
            "field": "AMOUNT",
            "sql_views": [{"view_id": "ZSQL_VIEW"}],
        },
    )

    assert response.status_code == 400
    assert "sql_text or sql_file is required" in response.text


def test_v1_agentic_review_disabled_returns_deterministic_cards_without_llm_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ["BWLI_LLM_BASE_URL", "BWLI_LLM_MODEL", "BWLI_LLM_API_KEY"]:
        monkeypatch.delenv(name, raising=False)
    client = _client(tmp_path, monkeypatch)
    snapshot_id = _capture_sample_graph(client)

    class ForbiddenAgenticClient:
        def __init__(self, **_: object) -> None:
            raise AssertionError("disabled agentic review must not construct an LLM client")

    monkeypatch.setattr(
        "bwli.llm.agentic_orchestrator.OpenAICompatibleClient",
        ForbiddenAgenticClient,
    )

    response = client.post(
        f"/api/v1/snapshots/{snapshot_id}/impact/review/agentic",
        json={
            "object_id": "SRC",
            "change_type": "field_removed",
            "field": "AMOUNT",
            "question": "Which deterministic findings need review?",
            "depth": 3,
            "node_cap": 25,
            "edge_cap": 60,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "disabled"
    assert payload["llm_disabled"] is True
    assert payload["llm_enabled"] is False
    assert payload["cards"]
    assert payload["cards"][0]["kind"] == "deterministic_finding"
    assert payload["deterministic_pack"]["snapshot_id"] == snapshot_id
    assert payload["deterministic_pack"]["impact"]["findings"]
    assert "fixture-api-key" not in response.text
    assert "fixture-secret-value" not in response.text


def test_v1_agentic_review_enabled_mock_returns_completed_run_secret_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    snapshot_id = _capture_sample_graph(client)
    configured = client.put(
        "/api/v1/runtime-config",
        json={
            "llm": {
                "enabled": True,
                "base_url": "http://127.0.0.1:11434/v1",
                "model": "local-fixture-model",
                "api_key": "fixture-api-key",
            }
        },
    )
    assert configured.status_code == 200, configured.text
    assert "fixture-api-key" not in configured.text

    calls: list[str] = []

    def fake_agentic_run(
        self: object,
        pack: ImpactEvidencePack,
        *,
        runtime: LlmRuntimeConfig,
        question: str | None = None,
        budget: AgenticReviewBudget | None = None,
    ) -> AgenticReviewRun:
        del self
        active_budget = budget or AgenticReviewBudget()
        assert runtime.api_key.get_secret_value() == "fixture-api-key"
        calls.append(runtime.model)
        cards = deterministic_review_cards(pack, max_cards=active_budget.max_cards)
        citation_ids = cards[0].citation_ids if cards else []
        citation = citation_ids[0] if citation_ids else ""
        return AgenticReviewRun(
            snapshot_id=pack.snapshot_id,
            llm_enabled=True,
            llm_disabled=False,
            status="completed",
            objective_question=question,
            cards=cards,
            cab_summary=f"Mock completed agentic review [{citation}]." if citation else "",
            deterministic_pack=pack,
            trace=[
                ReviewTraceStep(
                    stage="mock_agentic",
                    round=0,
                    summary="Mocked agentic assistant completed without network.",
                    citation_validation="passed",
                )
            ],
            budget=active_budget,
            budget_usage=AgenticReviewBudgetUsage(llm_calls=1, cards=len(cards)),
            audit_trail=[
                LlmAuditMetadata(
                    model=runtime.model,
                    prompt_sha256="a" * 64,
                    sanitized_input_sha256="b" * 64,
                    request_citation_ids=citation_ids,
                    citation_validation="passed",
                    response_timestamp="2026-06-08T00:00:00+00:00",
                )
            ],
        )

    monkeypatch.setattr("bwli.server.AgenticReviewAssistant.run", fake_agentic_run)

    response = client.post(
        f"/api/v1/snapshots/{snapshot_id}/impact/review/agentic",
        json={
            "object_id": "SRC",
            "change_type": "field_removed",
            "field": "AMOUNT",
            "question": "Mocked local review only",
            "objectives_hint": ["Prioritize CAB-ready cards"],
            "max_cards": 2,
            "max_llm_calls": 4,
            "depth": 3,
            "node_cap": 25,
            "edge_cap": 60,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert calls == ["local-fixture-model"]
    assert payload["status"] == "completed"
    assert payload["llm_disabled"] is False
    assert payload["llm_enabled"] is True
    assert payload["budget"]["max_cards"] == 2
    assert payload["budget"]["max_llm_calls"] == 4
    assert payload["budget_usage"]["llm_calls"] == 1
    assert payload["audit_trail"]
    assert "fixture-api-key" not in response.text


@pytest.mark.parametrize(
    "request_update",
    [
        {"question": "password=fixture-secret-value"},
        {"objectives_hint": ["Bearer fixture-secret-value"]},
    ],
)
def test_v1_agentic_review_secret_guard_rejects_prompt_secret_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request_update: dict[str, object],
) -> None:
    client = _client(tmp_path, monkeypatch)
    snapshot_id = _capture_sample_graph(client)
    body = {
        "object_id": "SRC",
        "change_type": "field_removed",
        "field": "AMOUNT",
        "depth": 3,
        "node_cap": 25,
        "edge_cap": 60,
        **request_update,
    }

    response = client.post(
        f"/api/v1/snapshots/{snapshot_id}/impact/review/agentic",
        json=body,
    )

    assert response.status_code == 400
    assert "refusing to persist secret-like text" in response.text
    assert "fixture-secret-value" not in response.text


def test_v1_sql_assistant_explains_view_and_disables_draft_without_local_llm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ["BWLI_LLM_BASE_URL", "BWLI_LLM_MODEL", "BWLI_LLM_API_KEY"]:
        monkeypatch.delenv(name, raising=False)
    client = _client(tmp_path, monkeypatch)
    snapshot_id = _capture_sample_graph(client)

    explain = client.post(
        f"/api/v1/snapshots/{snapshot_id}/sql/explain",
        json={
            "view_id": "ZSQL_VIEW",
            "sql_file": str(FIXTURES / "native_sql_view.sql"),
            "format": "json",
        },
    )
    assert explain.status_code == 200
    explain_payload = explain.json()
    assert explain_payload["advisory"] is True
    assert explain_payload["execution_blocked"] is True
    assert explain_payload["result"]["view"]["id"] == "ZSQL_VIEW"
    assert explain_payload["citations"]
    assert explain_payload["referenced_objects"]
    assert explain_payload["referenced_fields"]
    assert "glossary_terms" in explain_payload

    catalog = CatalogStore(tmp_path / "bwli-home" / "catalog.sqlite")
    with catalog._connect() as con:
        count = con.execute(
            "SELECT count(*) FROM analysis_runs WHERE snapshot_id = ? AND kind = ?",
            (snapshot_id, "sql_explain"),
        ).fetchone()[0]
    assert count == 1

    draft = client.post(
        f"/api/v1/snapshots/{snapshot_id}/sql/draft",
        json={"question": "월별 매출 합계를 보여줘", "target_dialect": "sap-hana-sql"},
    )
    assert draft.status_code == 200
    draft_payload = draft.json()
    assert draft_payload["status"] == "disabled"
    assert draft_payload["advisory"] is True
    assert draft_payload["execution_blocked"] is True
    assert draft_payload["config_required"] is True
    assert "execute" not in draft_payload.get("draft_sql", "").lower()


def test_v1_sql_draft_rejects_uncited_local_llm_draft_without_storing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BWLI_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("BWLI_LLM_MODEL", "local-fixture-model")
    monkeypatch.setenv("BWLI_LLM_API_KEY", "dummy-key")
    client = _client(tmp_path, monkeypatch)
    snapshot_id = _capture_sample_graph(client)

    class UncitedDraftClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def chat(self, _request: object) -> LlmCompletion:
            return LlmCompletion(
                content="SELECT * FROM zsales_fact",
                audit=LlmAuditMetadata(
                    model="local-fixture-model",
                    prompt_sha256="0" * 64,
                    sanitized_input_sha256="1" * 64,
                    request_citation_ids=[],
                    response_timestamp="2026-06-08T00:00:00+00:00",
                ),
            )

    monkeypatch.setattr(
        "bwli.llm.sql_assistant.OpenAICompatibleClient",
        UncitedDraftClient,
    )

    response = client.post(
        f"/api/v1/snapshots/{snapshot_id}/sql/draft",
        json={
            "question": "월별 매출 합계를 보여줘",
            "target_dialect": "sap-hana-sql",
            "sql_file": str(FIXTURES / "native_sql_view.sql"),
            "view_id": "ZSQL_VIEW",
        },
    )

    assert response.status_code == 400
    assert "cite" in response.json()["detail"].lower()

    catalog = CatalogStore(tmp_path / "bwli-home" / "catalog.sqlite")
    with catalog._connect() as con:
        count = con.execute("SELECT count(*) FROM sql_drafts").fetchone()[0]
    assert count == 0


def test_v1_sql_draft_rejects_empty_local_llm_draft_without_storing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BWLI_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("BWLI_LLM_MODEL", "local-fixture-model")
    monkeypatch.setenv("BWLI_LLM_API_KEY", "dummy-key")
    client = _client(tmp_path, monkeypatch)
    snapshot_id = _capture_sample_graph(client)

    class EmptyDraftClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def chat(self, _request: object) -> LlmCompletion:
            return LlmCompletion(
                content="   \n\t  ",
                audit=LlmAuditMetadata(
                    model="local-fixture-model",
                    prompt_sha256="0" * 64,
                    sanitized_input_sha256="1" * 64,
                    request_citation_ids=[],
                    response_timestamp="2026-06-08T00:00:00+00:00",
                ),
            )

    monkeypatch.setattr(
        "bwli.llm.sql_assistant.OpenAICompatibleClient",
        EmptyDraftClient,
    )

    response = client.post(
        f"/api/v1/snapshots/{snapshot_id}/sql/draft",
        json={
            "question": "월별 매출 합계를 보여줘",
            "target_dialect": "sap-hana-sql",
            "sql_file": str(FIXTURES / "native_sql_view.sql"),
            "view_id": "ZSQL_VIEW",
        },
    )

    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()

    catalog = CatalogStore(tmp_path / "bwli-home" / "catalog.sqlite")
    with catalog._connect() as con:
        count = con.execute("SELECT count(*) FROM sql_drafts").fetchone()[0]
    assert count == 0


def test_v1_impact_advice_returns_deterministic_impact_when_llm_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    snapshot_id = _capture_sample_graph(client)

    response = client.post(
        f"/api/v1/snapshots/{snapshot_id}/impact/advice",
        json={
            "object_id": "SRC",
            "change_type": "field_removed",
            "field": "CUSTOMER_ID",
            "depth": 3,
            "node_cap": 25,
            "edge_cap": 60,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "disabled"
    assert payload["config_required"] is True
    assert payload["advisory"] is True
    assert payload["advice"] == ""
    assert payload["impact"]["deterministic"] is True
    assert payload["impact"]["scenario"]["field"] == "CUSTOMER_ID"
    assert [item["object_id"] for item in payload["impact"]["affected_objects"]]


def test_v1_lineage_advice_returns_deterministic_lineage_when_llm_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ["BWLI_LLM_BASE_URL", "BWLI_LLM_MODEL", "BWLI_LLM_API_KEY"]:
        monkeypatch.delenv(name, raising=False)
    client = _client(tmp_path, monkeypatch)
    snapshot_id = _capture_sample_graph(client)

    response = client.post(
        f"/api/v1/snapshots/{snapshot_id}/lineage/advice",
        json={
            "object_id": "SRC",
            "direction": "downstream",
            "depth": 3,
            "node_cap": 25,
            "edge_cap": 60,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "disabled"
    assert payload["config_required"] is True
    assert payload["advisory"] is True
    assert payload["advice"] == ""
    assert payload["lineage"]["start_id"] == "SRC"
    assert [node["id"] for node in payload["lineage"]["nodes"]]
    assert [edge["id"] for edge in payload["lineage"]["edges"]]


def test_v1_lineage_tour_disabled_by_default_no_network_returns_domain_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ["BWLI_LLM_BASE_URL", "BWLI_LLM_MODEL", "BWLI_LLM_API_KEY"]:
        monkeypatch.delenv(name, raising=False)
    client = _client(tmp_path, monkeypatch)
    snapshot_id = _capture_sample_graph(client)

    class ForbiddenTourClient:
        def __init__(self, **_: object) -> None:
            raise AssertionError("disabled LLM tour must not construct a network client")

    monkeypatch.setattr(
        "bwli.llm.lineage_advisor.OpenAICompatibleClient",
        ForbiddenTourClient,
    )

    response = client.post(
        f"/api/v1/snapshots/{snapshot_id}/lineage/tour",
        json={
            "object_id": "SRC",
            "direction": "downstream",
            "depth": 3,
            "node_cap": 25,
            "edge_cap": 60,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "disabled"
    assert payload["config_required"] is True
    assert payload["summary"]
    assert payload["tour"]
    assert payload["citations"]
    assert all(step["node_ids"] for step in payload["tour"])
    assert all(step["description"] for step in payload["tour"])
    assert payload["lineage"]["start_id"] == "SRC"
    assert payload["domain_summary"]["node_count"] == len(payload["lineage"]["nodes"])
    assert payload["domain_summary"]["edge_count"] == len(payload["lineage"]["edges"])
    assert payload["domain_summary"]["object_types"]


def test_v1_lineage_advice_uses_local_llm_with_citation_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    snapshot_id = _capture_sample_graph(client)

    configured = client.put(
        "/api/v1/runtime-config",
        json={
            "llm": {
                "enabled": True,
                "base_url": "http://127.0.0.1:11434/v1",
                "model": "local-fixture-model",
                "api_key": "fixture-api-key",
            }
        },
    )
    assert configured.status_code == 200, configured.text

    class CitedLineageAdviceClient:
        def __init__(self, **_: object) -> None:
            pass

        def chat(self, request: object) -> LlmCompletion:
            citation_ids = request.citation_ids  # type: ignore[attr-defined]
            return LlmCompletion(
                content=(
                    f"Lineage 시작 객체를 먼저 확인하세요 [{citation_ids[0]}].\n"
                    f"첫 번째 edge는 BWMT에서 재확인하세요 [{citation_ids[-1]}]."
                ),
                audit=LlmAuditMetadata(
                    model="local-fixture-model",
                    prompt_sha256="lineage-prompt-sha",
                    sanitized_input_sha256="lineage-input-sha",
                    request_citation_ids=list(citation_ids),
                    response_timestamp="2026-06-08T00:00:00+00:00",
                ),
            )

    monkeypatch.setattr(
        "bwli.llm.lineage_advisor.OpenAICompatibleClient",
        CitedLineageAdviceClient,
    )

    response = client.post(
        f"/api/v1/snapshots/{snapshot_id}/lineage/advice",
        json={
            "object_id": "SRC",
            "direction": "downstream",
            "depth": 3,
            "node_cap": 25,
            "edge_cap": 60,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["config_required"] is False
    assert "fixture-api-key" not in response.text
    assert payload["llm_audit"]["citation_validation"] == "passed"
    assert "[node:1]" in payload["advice"]
    assert payload["citations"][0] == "node:1"
    assert payload["lineage"]["start_id"] == "SRC"


def test_v1_impact_advice_uses_local_llm_with_citation_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    snapshot_id = _capture_sample_graph(client)

    configured = client.put(
        "/api/v1/runtime-config",
        json={
            "llm": {
                "enabled": True,
                "base_url": "http://127.0.0.1:11434/v1",
                "model": "local-fixture-model",
                "api_key": "fixture-api-key",
            }
        },
    )
    assert configured.status_code == 200, configured.text

    class CitedImpactAdviceClient:
        def __init__(self, **_: object) -> None:
            pass

        def chat(self, request: object) -> LlmCompletion:
            citation_ids = request.citation_ids  # type: ignore[attr-defined]
            return LlmCompletion(
                content=(
                    f"Scenario should be reviewed in BWMT [{citation_ids[0]}].\n"
                    f"First affected object needs owner confirmation [{citation_ids[1]}]."
                ),
                audit=LlmAuditMetadata(
                    model="local-fixture-model",
                    prompt_sha256="impact-prompt-sha",
                    sanitized_input_sha256="impact-input-sha",
                    request_citation_ids=list(citation_ids),
                    response_timestamp="2026-06-08T00:00:00+00:00",
                ),
            )

    monkeypatch.setattr(
        "bwli.llm.impact_advisor.OpenAICompatibleClient",
        CitedImpactAdviceClient,
    )

    response = client.post(
        f"/api/v1/snapshots/{snapshot_id}/impact/advice",
        json={
            "object_id": "SRC",
            "change_type": "field_removed",
            "field": "CUSTOMER_ID",
            "description": "Check downstream sales flow",
            "depth": 3,
            "node_cap": 25,
            "edge_cap": 60,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["config_required"] is False
    assert "fixture-api-key" not in response.text
    assert payload["llm_audit"]["citation_validation"] == "passed"
    assert "[scenario:change]" in payload["advice"]
    assert payload["citations"][0] == "scenario:change"
    assert payload["impact"]["deterministic"] is True


def test_catalog_store_rejects_obvious_secret_bearing_metadata(tmp_path: Path) -> None:
    store = CatalogStore(tmp_path / "catalog.sqlite")
    snapshot = store.create_snapshot(mode="test", source="fixture://secret-guard")

    with pytest.raises(SecretPersistenceError):
        store.replace_catalog(
            snapshot.id,
            objects=[
                {
                    "id": "OBJ",
                    "name": "Object",
                    "type": "ADSO",
                    "metadata": {"password": "must-not-persist"},
                    "evidence_ids": [],
                }
            ],
            edges=[],
        )

    with pytest.raises(SecretPersistenceError):
        store.replace_catalog(
            snapshot.id,
            objects=[
                {
                    "id": "URL_OBJ",
                    "name": "URL Object",
                    "type": "ADSO",
                    "metadata": {"source": "https://user:secret@bw.example.invalid/sap"},
                    "evidence_ids": [],
                }
            ],
            edges=[],
        )

    assert store.list_objects(snapshot.id, q=None, object_type=None, limit=10, cursor=0)[0] == []


def test_v1_live_capture_named_objects_does_not_run_wildcard_search(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BWLI_HOME", str(tmp_path / "bwli-home"))
    calls: list[tuple[str, str]] = []

    class FakeBwClient:
        def fetch_search(
            self, search_term: str, *, object_type: str | None = None
        ) -> dict[str, object]:
            calls.append(("search", search_term))
            return {"objects": []}

        def fetch_dataflow(self, object_name: str, **_: object) -> str:
            calls.append(("dataflow", object_name))
            return """
            <dataflow>
              <node nodeID="1" objectName="ZADSO" objectType="ADSO" />
            </dataflow>
            """

        def fetch_xref(
            self,
            object_name: str,
            *,
            object_type: str = "ADSO",
            source_system: str | None = None,
        ) -> dict[str, object]:
            calls.append(("xref", object_name))
            return {"references": []}

        def close(self) -> None:
            return None

    fake = FakeBwClient()
    client = TestClient(create_app(project_root=tmp_path, bw_client_factory=lambda _state: fake))
    configured = client.put(
        "/api/v1/runtime-config",
        json={
            "bw": {
                "url": "https://bw.example.invalid",
                "user": "user",
                "password": "secret-value",
                "client": "100",
                "language": "EN",
                "verify_ssl": True,
            }
        },
    )
    assert configured.status_code == 200, configured.text
    assert configured.json()["connection_status"] == "untested"

    blocked = client.post(
        "/api/v1/snapshots/capture",
        json={"confirm_read_only": True, "object_names": ["ZADSO"]},
    )
    assert blocked.status_code == 400
    assert "Test connection" in blocked.text

    connection = client.post(
        "/api/v1/connection/test",
        json={"confirm_read_only": True, "search_term": "Z*"},
    )
    assert connection.status_code == 200, connection.text
    assert client.get("/api/v1/runtime-config").json()["connection_status"] == "ok"
    calls.clear()

    response = client.post(
        "/api/v1/snapshots/capture",
        json={"confirm_read_only": True, "object_names": ["ZADSO"]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["object_count"] == 1
    assert response.json()["capture_scope"][0]["object_id"] == "ZADSO"
    assert response.json()["capture_scope"][0]["role"] == "selected"
    assert calls == [("dataflow", "ZADSO"), ("xref", "ZADSO")]

    unchanged = client.put(
        "/api/v1/runtime-config",
        json={
            "bw": {
                "url": "https://bw.example.invalid",
                "user": "user",
                "password": "[REDACTED]",
                "client": "100",
                "language": "EN",
                "verify_ssl": True,
            }
        },
    )
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["connection_status"] == "ok"

    changed = client.put(
        "/api/v1/runtime-config",
        json={
            "bw": {
                "url": "https://bw.example.invalid",
                "user": "user",
                "password": "[REDACTED]",
                "client": "100",
                "language": "KO",
                "verify_ssl": True,
            }
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["connection_status"] == "stale"

    blocked_after_change = client.post(
        "/api/v1/snapshots/capture",
        json={"confirm_read_only": True, "object_names": ["ZADSO"]},
    )
    assert blocked_after_change.status_code == 400
    assert "Test connection" in blocked_after_change.text


def test_v1_live_capture_queries_fetches_query_xml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BWLI_HOME", str(tmp_path / "bwli-home"))
    calls: list[tuple[str, str]] = []

    class FakeBwClient:
        def fetch_search(
            self, search_term: str, *, object_type: str | None = None
        ) -> dict[str, object]:
            calls.append(("search", search_term))
            return {"objects": []}

        def fetch_dataflow(self, object_name: str, **_: object) -> str:
            raise AssertionError(f"query capture must not fetch dataflow for {object_name}")

        def fetch_xref(self, object_name: str, **_: object) -> dict[str, object]:
            raise AssertionError(f"query capture must not fetch xref for {object_name}")

        def fetch_query(self, query_name: str) -> str:
            calls.append(("query", query_name))
            return (
                "<Qry:queryResource xmlns:Qry='http://www.sap.com/bw/modeling/query' "
                "technicalName='ZQ_SALES_MARGIN' description='Sales Margin Query' />"
            )

        def close(self) -> None:
            return None

    fake = FakeBwClient()
    client = TestClient(create_app(project_root=tmp_path, bw_client_factory=lambda _state: fake))
    configured = client.put(
        "/api/v1/runtime-config",
        json={
            "bw": {
                "url": "https://bw.example.invalid",
                "user": "user",
                "password": "secret-value",
                "client": "100",
                "language": "EN",
                "verify_ssl": True,
            }
        },
    )
    assert configured.status_code == 200, configured.text
    ready = client.post(
        "/api/v1/connection/test",
        json={"confirm_read_only": True, "search_term": "Z*"},
    )
    assert ready.status_code == 200, ready.text
    calls.clear()

    response = client.post(
        "/api/v1/snapshots/capture",
        json={"confirm_read_only": True, "queries": ["ZQ_SALES_MARGIN"]},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["capture"]["operations"][0]["name"] == "bw_get_query"
    assert payload["capture_scope"][0]["object_id"] == "ZQ_SALES_MARGIN"
    assert payload["capture_scope"][0]["operation"] == "bw_get_query"
    assert calls == [("query", "ZQ_SALES_MARGIN")]


def test_v1_failed_fixture_capture_deletes_empty_snapshot_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BWLI_HOME", str(tmp_path / "bwli-home"))
    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    fixture_path = fixture_dir / "leaky.json"
    fixture_path.write_text(
        """
        {
          "nodes": [
            {"id": "LEAK", "type": "ADSO", "metadata": {"api_key": "supersecretvalue"}}
          ],
          "edges": []
        }
        """,
        encoding="utf-8",
    )
    client = TestClient(create_app(project_root=tmp_path))

    response = client.post(
        "/api/v1/snapshots/capture",
        json={"fixture_path": "fixtures/leaky.json"},
    )

    assert response.status_code == 400
    listed = client.get("/api/v1/snapshots")
    assert listed.status_code == 200
    assert listed.json()["snapshots"] == []


def test_object_fields_endpoint_returns_catalog_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    store = CatalogStore(tmp_path / "bwli-home" / "catalog.sqlite")
    snapshot = store.create_snapshot(mode="test", source="fixture://fields")
    store.replace_catalog(
        snapshot.id,
        objects=[
            {
                "id": "ZADSO_SALES",
                "type": "ADSO",
                "metadata": {"fields": [{"name": "NET_VALUE", "type": "CURR", "role": "data"}]},
                "evidence_ids": ["fields:e1"],
            }
        ],
        edges=[],
    )

    response = client.get(f"/api/v1/snapshots/{snapshot.id}/objects/ZADSO_SALES/fields")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["snapshot_id"] == snapshot.id
    assert payload["object_id"] == "ZADSO_SALES"
    assert payload["fields"] == [{"name": "NET_VALUE", "type": "CURR", "role": "data"}]


def test_object_fields_endpoint_empty_when_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    snapshot_id = _capture_sample_graph(client)

    response = client.get(f"/api/v1/snapshots/{snapshot_id}/objects/QRY/fields")

    assert response.status_code == 200, response.text
    assert response.json()["fields"] == []


def test_query_analyze_endpoint_snapshot_driven_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BWLI_HOME", str(tmp_path / "bwli-home"))
    client = TestClient(create_app(project_root=tmp_path))
    writer = SnapshotWriter(tmp_path / "query-snapshot")
    metadata = writer.write_payload(
        payload_id="query-rich",
        kind="bw_get_query",
        source="bw://bw_get_query?queryName=ZQ_SALES_MARGIN",
        payload=Path("tests/fixtures/query-analysis.xml").read_text(encoding="utf-8"),
    )
    writer.write_manifest(mode="live-read-only", payloads=[metadata])
    captured = client.post(
        "/api/v1/snapshots/capture",
        json={"manifest_path": str(tmp_path / "query-snapshot" / "manifest.json")},
    )
    assert captured.status_code == 200, captured.text
    snapshot_id = captured.json()["id"]

    response = client.get(
        f"/api/v1/snapshots/{snapshot_id}/query/analyze",
        params={"query_name": "ZQ_SALES_MARGIN"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["read_only"] is True
    assert payload["query_name"] == "ZQ_SALES_MARGIN"
    assert payload["result"]["variables"][0]["technical_name"] == "ZVAR_CALMONTH"
    assert payload["result"]["calculated_key_figures"][0]["technical_name"] == "ZCKF_MARGIN"
    assert payload["result"]["providers"][0]["object_id"] == "ZC_SALES"


def test_query_analyze_unknown_query_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    snapshot_id = _capture_sample_graph(client)

    response = client.get(
        f"/api/v1/snapshots/{snapshot_id}/query/analyze",
        params={"query_name": "ZQ_DOES_NOT_EXIST"},
    )

    assert response.status_code == 404


def test_impact_brief_deterministic_fields_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    snapshot_id = _capture_sample_graph(client)

    response = client.post(
        f"/api/v1/snapshots/{snapshot_id}/impact/tour",
        json={
            "object_id": "SRC",
            "change_type": "field_removed",
            "field": "CUSTOMER_ID",
            "depth": 3,
            "node_cap": 25,
            "edge_cap": 60,
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "disabled"
    assert payload["summary"]
    assert payload["tour"]
    assert all(step["description"] for step in payload["tour"])
    assert payload["impact"]["deterministic"] is True
    assert payload["citations"]


def test_glossary_aggregate_and_lifecycle_are_local_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    snapshot_id = _capture_sample_graph(client)

    aggregate = client.get("/api/v1/glossary/aggregate")
    assert aggregate.status_code == 200, aggregate.text
    assert aggregate.json()["total"] >= 1
    assert (tmp_path / "bwli-home" / "glossary.sqlite").exists()

    glossary = client.get(
        f"/api/v1/snapshots/{snapshot_id}/glossary", params={"query": "Sales Query"}
    )
    assert glossary.status_code == 200, glossary.text
    term_id = glossary.json()["items"][0]["id"]
    updated = client.post(f"/api/v1/glossary/{term_id}/lifecycle", json={"lifecycle": "confirmed"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["lifecycle"] == "confirmed"
    assert "read_only_bw" in updated.json()["semantics"]

    aggregate_after = client.get("/api/v1/glossary/aggregate", params={"query": "Sales Query"})
    assert aggregate_after.status_code == 200, aggregate_after.text
    assert aggregate_after.json()["confirmed"] >= 1
