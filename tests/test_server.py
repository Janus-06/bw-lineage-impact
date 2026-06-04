from __future__ import annotations

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
