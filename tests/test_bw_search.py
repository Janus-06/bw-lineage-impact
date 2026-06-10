from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bwli.server import create_app

SEARCH_XML = """
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <objectName>ZADSO_SALES</objectName>
    <objectType>ADSO</objectType>
    <description>Sales ADSO</description>
  </entry>
  <entry>
    <objectName>ZTRFN_MARGIN</objectName>
    <objectType>TRFN</objectType>
    <description>Margin Transformation</description>
  </entry>
</feed>
"""


class FakeBwClient:
    def __init__(self, *, search_payload: object | None = None) -> None:
        self.search_calls: list[tuple[str, str | None]] = []
        self.dataflow_calls: list[str] = []
        self.dataflow_requests: list[dict[str, object]] = []
        self.xref_calls: list[str] = []
        self.xref_requests: list[dict[str, object]] = []
        self.search_payload: object = (
            search_payload
            if search_payload is not None
            else {
                "objects": [
                    {
                        "objectName": "ZADSO_SALES",
                        "objectType": "ADSO",
                        "description": "Sales ADSO",
                    },
                    {
                        "objectName": "ZADSO_COST",
                        "objectType": "ADSO",
                        "description": "Cost ADSO",
                    },
                    {
                        "objectName": "ZTRFN_MARGIN",
                        "objectType": "TRFN",
                        "description": "Margin Transformation",
                    },
                ]
            }
        )

    def fetch_search(
        self,
        search_term: str,
        *,
        object_type: str | None = None,
    ) -> object:
        self.search_calls.append((search_term, object_type))
        return self.search_payload

    def fetch_dataflow(
        self,
        object_name: str,
        *,
        object_type: str = "ADSO",
        source_system: str | None = None,
        direction: str = "downwards",
        levels: int = 3,
    ) -> str:
        self.dataflow_calls.append(object_name)
        self.dataflow_requests.append(
            {
                "object_name": object_name,
                "object_type": object_type,
                "source_system": source_system,
                "direction": direction,
                "levels": levels,
            }
        )
        return f"""
        <dataflow>
          <node nodeID="1" objectName="{object_name}" objectType="{object_type}" />
        </dataflow>
        """

    def fetch_xref(
        self,
        object_name: str,
        *,
        object_type: str = "ADSO",
        source_system: str | None = None,
    ) -> dict[str, object]:
        self.xref_calls.append(object_name)
        self.xref_requests.append(
            {
                "object_name": object_name,
                "object_type": object_type,
                "source_system": source_system,
            }
        )
        return {"references": []}

    def close(self) -> None:
        return None


def _configured_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake: FakeBwClient,
    *,
    run_connection_test: bool = True,
) -> TestClient:
    monkeypatch.setenv("BWLI_HOME", str(tmp_path / "bwli-home"))
    client = TestClient(create_app(project_root=tmp_path, bw_client_factory=lambda _state: fake))
    configured = client.put(
        "/api/v1/runtime-config",
        json={
            "bw": {
                "url": "https://bw.example.invalid",
                "user": "fixture-user",
                "password": "fixture-secret-value",
                "client": "100",
                "language": "EN",
                "verify_ssl": True,
            }
        },
    )
    assert configured.status_code == 200, configured.text
    if run_connection_test:
        connection = client.post(
            "/api/v1/connection/test",
            json={"confirm_read_only": True, "search_term": "Z*"},
        )
        assert connection.status_code == 200, connection.text
        fake.search_calls.clear()
    return client


def test_v1_bw_search_returns_parsed_candidates_and_passes_object_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeBwClient()
    client = _configured_client(tmp_path, monkeypatch, fake)

    response = client.post(
        "/api/v1/bw/search",
        json={"confirm_read_only": True, "search_term": "ZADSO_", "object_type": "ADSO"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["read_only"] is True
    assert payload["search_term"] == "ZADSO_"
    assert payload["object_type"] == "ADSO"
    assert payload["truncated"] is False
    assert payload["count"] == 3
    assert [item["object_id"] for item in payload["items"]] == [
        "ZADSO_SALES",
        "ZADSO_COST",
        "ZTRFN_MARGIN",
    ]
    assert payload["items"][0]["object_type"] == "ADSO"
    assert payload["items"][0]["name"] == "Sales ADSO"
    assert payload["items"][0]["source"] == "live"
    assert fake.search_calls == [("ZADSO_", "ADSO")]


def test_v1_bw_search_parses_xml_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeBwClient(search_payload=SEARCH_XML)
    client = _configured_client(tmp_path, monkeypatch, fake)

    response = client.post(
        "/api/v1/bw/search",
        json={"confirm_read_only": True, "search_term": "Z"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert [item["object_id"] for item in payload["items"]] == [
        "ZADSO_SALES",
        "ZTRFN_MARGIN",
    ]
    assert payload["items"][1]["object_type"] == "TRFN"


def test_v1_bw_search_enforces_result_limit_and_reports_truncation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeBwClient()
    client = _configured_client(tmp_path, monkeypatch, fake)

    response = client.post(
        "/api/v1/bw/search",
        json={"confirm_read_only": True, "search_term": "Z", "limit": 2},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["count"] == 2
    assert payload["truncated"] is True
    assert len(payload["items"]) == 2

    too_large = client.post(
        "/api/v1/bw/search",
        json={"confirm_read_only": True, "search_term": "Z", "limit": 500},
    )
    assert too_large.status_code == 422


@pytest.mark.parametrize(
    "term",
    ["*", " * ", "**", "%", "*%", "% *", "% * %", "* % *", " * % ", "  "],
)
def test_v1_bw_search_rejects_broad_wildcard_terms_server_side(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    term: str,
) -> None:
    fake = FakeBwClient()
    client = _configured_client(tmp_path, monkeypatch, fake)

    response = client.post(
        "/api/v1/bw/search",
        json={"confirm_read_only": True, "search_term": term},
    )

    assert response.status_code == 400
    assert fake.search_calls == []


def test_v1_bw_search_requires_read_only_confirmation_and_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BWLI_HOME", str(tmp_path / "bwli-home"))
    unconfigured = TestClient(
        create_app(project_root=tmp_path, bw_client_factory=lambda _state: FakeBwClient())
    )
    response = unconfigured.post(
        "/api/v1/bw/search",
        json={"confirm_read_only": True, "search_term": "ZADSO_"},
    )
    assert response.status_code == 400
    assert "not configured" in response.text

    fake = FakeBwClient()
    client = _configured_client(tmp_path, monkeypatch, fake)
    missing_confirm = client.post(
        "/api/v1/bw/search",
        json={"search_term": "ZADSO_"},
    )
    assert missing_confirm.status_code == 400
    assert "confirm_read_only" in missing_confirm.text
    assert fake.search_calls == []


def test_v1_bw_search_requires_successful_connection_test(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeBwClient()
    client = _configured_client(tmp_path, monkeypatch, fake, run_connection_test=False)

    response = client.post(
        "/api/v1/bw/search",
        json={"confirm_read_only": True, "search_term": "ZADSO_"},
    )

    assert response.status_code == 400
    assert "Test connection" in response.text
    assert fake.search_calls == []


def test_v1_bw_search_redacts_secrets_from_live_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingBwClient(FakeBwClient):
        def fetch_search(
            self,
            search_term: str,
            *,
            object_type: str | None = None,
        ) -> object:
            if self.search_calls:
                raise ValueError(
                    "connect to https://bw.example.invalid failed "
                    "with password fixture-secret-value"
                )
            return super().fetch_search(search_term, object_type=object_type)

    fake = FailingBwClient()
    client = _configured_client(tmp_path, monkeypatch, fake)
    fake.search_calls.append(("primed", None))

    response = client.post(
        "/api/v1/bw/search",
        json={"confirm_read_only": True, "search_term": "ZADSO_"},
    )

    assert response.status_code == 400
    assert "fixture-secret-value" not in response.text
    assert "bw.example.invalid" not in response.text


def test_v1_live_capture_rejects_broad_terms_and_too_many_objects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeBwClient()
    client = _configured_client(tmp_path, monkeypatch, fake)

    broad = client.post(
        "/api/v1/snapshots/capture",
        json={"confirm_read_only": True, "search_terms": ["*"], "object_names": []},
    )
    assert broad.status_code == 400
    assert "broad wildcard" in broad.text

    broad_name = client.post(
        "/api/v1/snapshots/capture",
        json={"confirm_read_only": True, "object_names": ["% * %"]},
    )
    assert broad_name.status_code == 400
    assert "broad wildcard object names" in broad_name.text

    too_many = client.post(
        "/api/v1/snapshots/capture",
        json={
            "confirm_read_only": True,
            "object_names": [f"ZADSO_{index:02d}" for index in range(21)],
        },
    )
    assert too_many.status_code == 400
    assert "limited to 20" in too_many.text
    assert fake.dataflow_calls == []
    assert fake.xref_calls == []

    too_many_terms = client.post(
        "/api/v1/snapshots/capture",
        json={
            "confirm_read_only": True,
            "search_terms": [f"ZADSO_{index:02d}" for index in range(21)],
        },
    )
    assert too_many_terms.status_code == 400
    assert "limited to 20" in too_many_terms.text
    assert fake.search_calls == []


def test_v1_live_capture_deduplicates_terms_and_objects_before_live_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeBwClient()
    client = _configured_client(tmp_path, monkeypatch, fake)

    captured = client.post(
        "/api/v1/snapshots/capture",
        json={
            "confirm_read_only": True,
            "search_terms": [" ZADSO_ ", "ZADSO_", ""],
            "object_names": [" ZADSO_SALES ", "ZADSO_SALES", ""],
        },
    )

    assert captured.status_code == 200, captured.text
    assert fake.search_calls == [("ZADSO_", None)]
    assert fake.dataflow_calls == ["ZADSO_SALES"]
    assert fake.xref_calls == ["ZADSO_SALES"]


def test_v1_snapshot_refresh_recaptures_selected_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeBwClient()
    client = _configured_client(tmp_path, monkeypatch, fake)

    captured = client.post(
        "/api/v1/snapshots/capture",
        json={
            "confirm_read_only": True,
            "object_names": ["ZRSDS_SALES"],
            "object_type": "RSDS",
            "source_system": "BW1",
            "dataflow_direction": "upwards",
            "dataflow_levels": 2,
        },
    )
    assert captured.status_code == 200, captured.text
    original_id = captured.json()["id"]
    fake.dataflow_calls.clear()
    fake.dataflow_requests.clear()
    fake.xref_calls.clear()
    fake.xref_requests.clear()

    refreshed = client.post(
        f"/api/v1/snapshots/{original_id}/refresh",
        json={"confirm_read_only": True},
    )

    assert refreshed.status_code == 200, refreshed.text
    payload = refreshed.json()
    assert payload["id"] != original_id
    assert payload["refreshed_from"] == original_id
    assert fake.dataflow_calls == ["ZRSDS_SALES"]
    assert fake.dataflow_requests == [
        {
            "object_name": "ZRSDS_SALES",
            "object_type": "RSDS",
            "source_system": "BW1",
            "direction": "upwards",
            "levels": 2,
        }
    ]
    assert fake.xref_calls == ["ZRSDS_SALES"]
    assert fake.xref_requests == [
        {"object_name": "ZRSDS_SALES", "object_type": "RSDS", "source_system": "BW1"}
    ]
    assert fake.search_calls == []
    selected = [item for item in payload["capture_scope"] if item["role"] == "selected"]
    assert {item["object_id"] for item in selected} == {"ZRSDS_SALES"}

    listed = client.get("/api/v1/snapshots")
    assert listed.status_code == 200
    assert listed.json()["snapshots"][0]["id"] == payload["id"]


def test_v1_snapshot_refresh_requires_selected_scope_and_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeBwClient()
    client = _configured_client(tmp_path, monkeypatch, fake)

    fixture_dir = tmp_path / "fixtures"
    fixture_dir.mkdir()
    fixture_path = fixture_dir / "graph.json"
    fixture_path.write_text(
        '{"nodes": [{"id": "SRC", "type": "ADSO"}], "edges": []}',
        encoding="utf-8",
    )
    fixture_capture = client.post(
        "/api/v1/snapshots/capture",
        json={"fixture_path": "fixtures/graph.json"},
    )
    assert fixture_capture.status_code == 200, fixture_capture.text
    fixture_id = fixture_capture.json()["id"]

    no_scope = client.post(
        f"/api/v1/snapshots/{fixture_id}/refresh",
        json={"confirm_read_only": True},
    )
    assert no_scope.status_code == 400
    assert "selected capture scope" in no_scope.text

    missing = client.post(
        f"/api/v1/snapshots/{fixture_id}-missing/refresh",
        json={"confirm_read_only": True},
    )
    assert missing.status_code == 404

    live_capture = client.post(
        "/api/v1/snapshots/capture",
        json={"confirm_read_only": True, "object_names": ["ZADSO_SALES"]},
    )
    assert live_capture.status_code == 200, live_capture.text
    live_id = live_capture.json()["id"]

    unconfirmed = client.post(f"/api/v1/snapshots/{live_id}/refresh", json={})
    assert unconfirmed.status_code == 400
    assert "confirm_read_only" in unconfirmed.text
