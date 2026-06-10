from __future__ import annotations

from pathlib import Path

import pytest

from bwli.snapshot import SnapshotWriter
from bwli.store import CatalogStore, ingest_fixture_payload, ingest_manifest


def _skip_if_file_symlink_unavailable(tmp_path: Path) -> None:
    target = tmp_path / "symlink-probe-target.txt"
    link = tmp_path / "symlink-probe-link.txt"
    target.write_text("probe", encoding="utf-8")
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"file symlinks are unavailable in this environment: {exc}")
    finally:
        if link.is_symlink() or link.exists():
            link.unlink()
        if target.exists():
            target.unlink()


def _write_manifest_payload(
    tmp_path: Path,
    *,
    kind: str,
    payload_id: str,
    payload: object,
    source: str | None = None,
) -> Path:
    writer = SnapshotWriter(tmp_path)
    metadata = writer.write_payload(
        payload_id=payload_id,
        kind=kind,
        source=source or f"bw://{kind}",
        payload=payload,
    )
    writer.write_manifest(mode="live-read-only", payloads=[metadata])
    return tmp_path / "manifest.json"


def test_ingest_manifest_dispatches_xml_search_by_manifest_kind(tmp_path: Path) -> None:
    manifest_path = _write_manifest_payload(
        tmp_path,
        kind="bw_search",
        payload_id="search-xml",
        payload="""
        <searchResults>
          <object technicalName="ZQ_SALES" objectType="QUERY" description="Sales query"/>
        </searchResults>
        """,
    )

    _, catalog = ingest_manifest(manifest_path)

    assert [(item.id, item.type, item.name) for item in catalog.objects] == [
        ("ZQ_SALES", "QUERY", "Sales query")
    ]
    assert catalog.edges == []


@pytest.mark.parametrize(
    "payload",
    [
        {
            "results": [
                {
                    "id": "SHOULD_NOT_BECOME_SEARCH_OBJECT",
                    "sourceObject": "SRC_ADSO",
                    "targetObject": "TGT_QUERY",
                }
            ]
        },
        """
        <xref>
          <reference id="SHOULD_NOT_BECOME_SEARCH_OBJECT"
                     sourceObject="SRC_ADSO"
                     targetObject="TGT_QUERY" />
        </xref>
        """,
    ],
)
def test_ingest_manifest_dispatches_xref_by_manifest_kind(
    tmp_path: Path,
    payload: object,
) -> None:
    manifest_path = _write_manifest_payload(
        tmp_path,
        kind="bw_xref",
        payload_id="xref-payload",
        payload=payload,
    )

    _, catalog = ingest_manifest(manifest_path)

    assert [(edge.source, edge.target, edge.type) for edge in catalog.edges] == [
        ("SRC_ADSO", "TGT_QUERY", "xref")
    ]
    assert {item.id for item in catalog.objects} == {"SRC_ADSO", "TGT_QUERY"}


def test_ingest_manifest_reads_explicit_manifest_filename(tmp_path: Path) -> None:
    manifest_path = _write_manifest_payload(
        tmp_path,
        kind="bw_search",
        payload_id="search-selected",
        payload={"objects": [{"technicalName": "Z_SELECTED", "objectType": "QUERY"}]},
    )
    selected_path = tmp_path / "selected-manifest.json"
    manifest_path.replace(selected_path)

    _, catalog = ingest_manifest(selected_path)

    assert [item.id for item in catalog.objects] == ["Z_SELECTED"]


def test_ingest_manifest_links_atom_xref_entries_to_requested_object(tmp_path: Path) -> None:
    manifest_path = _write_manifest_payload(
        tmp_path,
        kind="bw_xref",
        payload_id="xref-0-ZSRC-acab96cdce9c-downstream",
        source="bw://bw_xref/downstream?objectName=ZSRC",
        payload="""
        <atom:feed xmlns:atom="http://www.w3.org/2005/Atom"
                   xmlns:bwModel="urn:sap:bw:model">
          <atom:entry>
            <atom:title>Target Query</atom:title>
            <bwModel:object objectName="ZQ_TARGET" objectType="QUERY" objectStatus="active" />
          </atom:entry>
        </atom:feed>
        """,
    )

    _, catalog = ingest_manifest(manifest_path)

    assert [(edge.source, edge.target, edge.type) for edge in catalog.edges] == [
        ("ZSRC", "ZQ_TARGET", "xref")
    ]
    objects = {item.id: item for item in catalog.objects}
    assert objects["ZQ_TARGET"].type == "QUERY"
    assert objects["ZQ_TARGET"].name == "Target Query"


def test_ingest_manifest_strips_safe_fragment_hash_from_legacy_xref_payload_id(
    tmp_path: Path,
) -> None:
    manifest_path = _write_manifest_payload(
        tmp_path,
        kind="bw_xref",
        payload_id="xref-0-ZSRC-acab96cdce9c-downstream",
        source="bw://bw_xref/downstream",
        payload="""
        <atom:feed xmlns:atom="http://www.w3.org/2005/Atom"
                   xmlns:bwModel="urn:sap:bw:model">
          <atom:entry>
            <atom:title>Target Query</atom:title>
            <bwModel:object objectName="ZQ_TARGET" objectType="QUERY" />
          </atom:entry>
        </atom:feed>
        """,
    )

    _, catalog = ingest_manifest(manifest_path)

    assert [(edge.source, edge.target, edge.type) for edge in catalog.edges] == [
        ("ZSRC", "ZQ_TARGET", "xref")
    ]


def test_ingest_manifest_links_upstream_atom_xref_to_requested_object(tmp_path: Path) -> None:
    manifest_path = _write_manifest_payload(
        tmp_path,
        kind="bw_xref",
        payload_id="xref-0-ZTARGET-upstream",
        source="bw://bw_xref/upstream",
        payload="""
        <atom:feed xmlns:atom="http://www.w3.org/2005/Atom"
                   xmlns:bwModel="urn:sap:bw:model">
          <atom:entry>
            <atom:title>Source ADSO</atom:title>
            <bwModel:object objectName="ZSRC" objectType="ADSO" objectStatus="active" />
          </atom:entry>
        </atom:feed>
        """,
    )

    _, catalog = ingest_manifest(manifest_path)

    assert [(edge.source, edge.target, edge.type) for edge in catalog.edges] == [
        ("ZSRC", "ZTARGET", "xref")
    ]
    objects = {item.id: item for item in catalog.objects}
    assert objects["ZSRC"].type == "ADSO"
    assert objects["ZSRC"].name == "Source ADSO"


def test_ingest_manifest_merges_duplicate_dataflow_object_names_before_storage(
    tmp_path: Path,
) -> None:
    manifest_path = _write_manifest_payload(
        tmp_path,
        kind="bw_get_dataflow",
        payload_id="dataflow-xml",
        payload="""
        <dataflow>
          <node nodeID="1" objectName="ZADSO_DUP" objectType="ADSO">
            <targetNode>#///2</targetNode>
          </node>
          <node nodeID="2" objectName="ZADSO_DUP" objectType="ADSO">
            <sourceNode>#///1</sourceNode>
          </node>
        </dataflow>
        """,
    )

    _, catalog = ingest_manifest(manifest_path)

    assert [item.id for item in catalog.objects] == ["ZADSO_DUP"]
    assert catalog.objects[0].evidence_ids == [
        "dataflow-xml:dataflow-node:1",
        "dataflow-xml:dataflow-node:2",
    ]

    store = CatalogStore(tmp_path / "catalog.sqlite")
    snapshot = store.create_snapshot(mode="test", source="fixture://duplicate-dataflow")
    store.replace_catalog(snapshot.id, objects=catalog.objects, edges=catalog.edges)
    stored = store.get_object(snapshot.id, "ZADSO_DUP")
    assert stored is not None
    assert stored.incoming_count == 1
    assert stored.outgoing_count == 1


def test_ingest_manifest_indexes_top_level_search_array_payload(tmp_path: Path) -> None:
    manifest_path = _write_manifest_payload(
        tmp_path,
        kind="bw_search",
        payload_id="search-array",
        payload=[
            {"technicalName": "ZQ_ARRAY", "objectType": "QUERY", "description": "Array query"}
        ],
    )

    _, catalog = ingest_manifest(manifest_path)

    assert [item.id for item in catalog.objects] == ["ZQ_ARRAY"]
    assert catalog.objects[0].type == "QUERY"


def test_catalog_search_matches_visible_label_when_name_is_missing(tmp_path: Path) -> None:
    catalog = ingest_fixture_payload(
        {"nodes": [{"id": "A", "label": "Sales Label", "type": "QUERY"}], "edges": []},
        source="fixture://label-search.json",
    )
    store = CatalogStore(tmp_path / "catalog.sqlite")
    snapshot = store.create_snapshot(mode="test", source="fixture://label-search")
    store.replace_catalog(snapshot.id, objects=catalog.objects, edges=catalog.edges)

    records, next_cursor = store.list_objects(
        snapshot.id,
        q="sales label",
        object_type=None,
        limit=10,
        cursor=0,
    )

    assert next_cursor is None
    assert [record.id for record in records] == ["A"]


def test_ingest_manifest_rejects_symlink_payload_outside_snapshot_root(tmp_path: Path) -> None:
    _skip_if_file_symlink_unavailable(tmp_path)

    writer = SnapshotWriter(tmp_path / "snapshot")
    metadata = writer.write_payload(
        payload_id="safe",
        kind="fixture",
        source="fixture://safe.json",
        payload={"nodes": [{"id": "SAFE", "type": "ADSO"}], "edges": []},
    )
    writer.write_manifest(mode="offline-fixture", payloads=[metadata])
    outside_payload = tmp_path / "outside.json"
    outside_payload.write_text(
        '{"nodes": [{"id": "OUTSIDE", "type": "ADSO"}], "edges": []}',
        encoding="utf-8",
    )
    payload_path = tmp_path / "snapshot" / metadata.relative_path
    payload_path.unlink()
    payload_path.symlink_to(outside_payload)

    with pytest.raises(ValueError, match="unsafe snapshot relative path"):
        ingest_manifest(tmp_path / "snapshot" / "manifest.json")


def test_ingest_manifest_merges_duplicate_json_search_objects_before_storage(
    tmp_path: Path,
) -> None:
    manifest_path = _write_manifest_payload(
        tmp_path,
        kind="bw_search",
        payload_id="search-json",
        payload={
            "objects": [
                {"technicalName": "ZADSO_DUP", "objectType": "ADSO", "description": "First"},
                {"technicalName": "ZADSO_DUP", "objectType": "ADSO", "description": "Second"},
            ]
        },
    )

    _, catalog = ingest_manifest(manifest_path)

    assert [item.id for item in catalog.objects] == ["ZADSO_DUP"]
    assert catalog.objects[0].name == "First"
    assert catalog.objects[0].evidence_ids == ["search-json:search:1", "search-json:search:2"]

    store = CatalogStore(tmp_path / "catalog.sqlite")
    snapshot = store.create_snapshot(mode="test", source="fixture://duplicate-search-json")
    store.replace_catalog(snapshot.id, objects=catalog.objects, edges=catalog.edges)
    assert store.get_object(snapshot.id, "ZADSO_DUP") is not None
