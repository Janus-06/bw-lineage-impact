from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from bwli.repository import parse_repository_contents_xml
from bwli.server import create_app
from bwli.store import CatalogStore

REPOSITORY_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<atom:feed
  xmlns:atom="http://www.w3.org/2005/Atom"
  xmlns:bwModel="http://www.sap.com/bw/modeling">
  <atom:entry>
    <atom:title>Sales providers</atom:title>
    <atom:link
      rel="self"
      href="/sap/bw/modeling/repo/infoproviderstructure/sales"
      type="application/atom+xml" />
    <atom:link
      rel="http://www.sap.com/bw/modeling/relations:children"
      href="/sap/bw/modeling/repo/infoproviderstructure/sales" />
    <bwModel:object
      objectName="SALES"
      objectType="INFOAREA"
      objectStatus="ACTIVE" />
  </atom:entry>
  <atom:entry>
    <atom:title>Sales CompositeProvider</atom:title>
    <atom:link
      rel="self"
      href="/sap/bw/modeling/hcpr/zsales/m"
      type="application/vnd.sap.bw.modeling.hcpr-v1_15_0+xml" />
    <bwModel:object
      objectName="ZHCPR_SALES"
      objectType="HCPR"
      objectSubtype="COMPOSITE"
      objectStatus="ACTIVE" />
  </atom:entry>
  <atom:entry>
    <atom:title>Process chain</atom:title>
    <atom:link
      rel="self"
      href="#BWProcessChain?chainId=ZCHAIN"
      type="application/vnd.sap-bw-modeling.url" />
    <bwModel:object objectName="ZCHAIN" objectType="RSPC" />
  </atom:entry>
</atom:feed>
"""


def test_parse_repository_contents_atom_xml_is_deterministic() -> None:
    nodes = parse_repository_contents_xml(REPOSITORY_XML, parent_path="/")

    assert [node.name for node in nodes] == ["SALES", "ZHCPR_SALES", "ZCHAIN"]
    assert nodes[0].description == "Sales providers"
    assert nodes[0].object_type == "INFOAREA"
    assert nodes[0].status == "ACTIVE"
    assert nodes[0].has_children is True
    assert nodes[0].children_path == "sales"
    assert nodes[0].path == "sales"
    assert nodes[1].object_subtype == "COMPOSITE"
    assert nodes[1].has_children is False
    assert nodes[1].self_url == "/sap/bw/modeling/hcpr/zsales/m"
    assert nodes[2].fiori_only is True
    assert nodes[2].metadata["chain_id"] == "ZCHAIN"


def test_repository_store_cache_is_separate_from_lineage_edges(tmp_path: Path) -> None:
    store = CatalogStore(tmp_path / "catalog.sqlite")
    snapshot = store.create_snapshot(mode="test", source="fixture://graph")
    store.replace_catalog(
        snapshot.id,
        objects=[{"id": "A", "type": "ADSO"}],
        edges=[{"id": "edge-1", "source": "A", "target": "A"}],
    )
    store.replace_repository_nodes(
        parent_path="/",
        nodes=parse_repository_contents_xml(REPOSITORY_XML, parent_path="/"),
    )

    cached = store.list_repository_nodes(parent_path="/")
    graph = store.load_graph(snapshot.id)

    assert [node.name for node in cached] == ["SALES", "ZHCPR_SALES", "ZCHAIN"]
    assert [edge.id for edge in graph.edges] == ["edge-1"]


def test_v1_repository_refresh_fetches_live_atom_and_caches(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("BWLI_HOME", str(tmp_path / "bwli-home"))
    calls: list[tuple[str, str | None]] = []

    class FakeBwClient:
        def fetch_search(
            self,
            search_term: str,
            *,
            object_type: str | None = None,
        ) -> dict[str, object]:
            calls.append(("search", search_term))
            return {"objects": []}

        def fetch_dataflow(self, object_name: str, **_: object) -> str:
            calls.append(("dataflow", object_name))
            return "<dataflow />"

        def fetch_xref(self, object_name: str, **_: object) -> dict[str, object]:
            calls.append(("xref", object_name))
            return {"references": []}

        def fetch_repository_contents(self, path: str | None = None) -> str:
            calls.append(("repository", path))
            return REPOSITORY_XML

        def close(self) -> None:
            return None

    client = TestClient(
        create_app(project_root=tmp_path, bw_client_factory=lambda _state: FakeBwClient())
    )
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
    assert configured.status_code == 200
    ready = client.post(
        "/api/v1/connection/test",
        json={"confirm_read_only": True, "search_term": "Z*"},
    )
    assert ready.status_code == 200

    refreshed = client.get(
        "/api/v1/repository",
        params={"refresh": "true", "confirm_read_only": "true", "path": "/"},
    )

    assert refreshed.status_code == 200, refreshed.text
    assert "secret-value" not in refreshed.text
    payload = refreshed.json()
    assert payload["source"] == "live"
    assert payload["path"] == "/"
    assert payload["count"] == 3
    assert payload["items"][0]["name"] == "SALES"
    assert calls[-1] == ("repository", "/")

    cached = client.get("/api/v1/repository", params={"path": "/"})

    assert cached.status_code == 200
    assert cached.json()["source"] == "cache"
    assert cached.json()["items"][1]["name"] == "ZHCPR_SALES"


def test_v1_repository_refresh_requires_config_test_and_read_only_without_leaking(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("BWLI_HOME", str(tmp_path / "bwli-home"))
    client = TestClient(create_app(project_root=tmp_path))

    response = client.get("/api/v1/repository", params={"refresh": "true"})

    assert response.status_code == 400
    assert "configure" in response.json()["detail"].lower()
    assert "secret" not in response.text.lower()

