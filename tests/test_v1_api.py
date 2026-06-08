from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bwli.llm.openai_compatible import LlmAuditMetadata, LlmCompletion
from bwli.server import create_app
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
            self, object_name: str, *, direction: str = "downstream"
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

    response = client.post(
        "/api/v1/snapshots/capture",
        json={"confirm_read_only": True, "object_names": ["ZADSO"]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["object_count"] == 1
    assert calls == [("dataflow", "ZADSO"), ("xref", "ZADSO")]


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
