from __future__ import annotations

import hashlib
import json

from bwli.fingerprint import object_fingerprint
from bwli.graph import BwNode
from bwli.snapshot import (
    PayloadMetadata,
    SnapshotManifest,
    SnapshotReader,
    SnapshotWriter,
    write_fixture_snapshot,
)


def test_snapshot_writer_reader_round_trip(tmp_path) -> None:
    out_dir = tmp_path / "snapshot"
    writer = SnapshotWriter(out_dir)

    metadata = writer.write_payload(
        payload_id="fixture-search",
        kind="bwsearch",
        source="fixture://search.json",
        payload={"objects": [{"technicalName": "ZADSO"}]},
    )
    manifest = writer.write_manifest(mode="offline-fixture", payloads=[metadata])

    reader = SnapshotReader(out_dir)
    loaded_manifest = reader.read_manifest()
    loaded_payload = reader.read_payload(loaded_manifest.payloads[0])

    assert loaded_manifest == manifest
    assert loaded_payload == {"objects": [{"technicalName": "ZADSO"}]}
    assert loaded_manifest.payloads[0].source == "fixture://search.json"
    assert not loaded_manifest.payloads[0].relative_path.startswith("/")


def test_write_fixture_snapshot_uses_local_file_without_persisting_absolute_path(tmp_path) -> None:
    fixture = tmp_path / "sample.json"
    fixture.write_text('{"kind":"bwsearch","objects":[]}', encoding="utf-8")

    manifest = write_fixture_snapshot(fixture, tmp_path / "out")

    assert manifest.mode == "offline-fixture"
    assert len(manifest.payloads) == 1
    assert manifest.payloads[0].source == "fixture://sample.json"
    assert str(tmp_path) not in manifest.model_dump_json()


def test_snapshot_writer_stores_xml_payload_as_xml_file(tmp_path) -> None:
    writer = SnapshotWriter(tmp_path / "snap")
    xml = '<?xml version="1.0"?><dmod:dataFlow><node nodeID="1"/></dmod:dataFlow>'

    metadata = writer.write_payload(
        payload_id="dataflow-xyz",
        kind="bw_get_dataflow",
        source="bw://bw_get_dataflow",
        payload=xml,
    )

    assert metadata.relative_path.endswith(".xml")
    stored = (tmp_path / "snap" / metadata.relative_path).read_text(encoding="utf-8")
    assert stored == xml

    reader = SnapshotReader(tmp_path / "snap")
    assert reader.read_payload(metadata) == xml


def test_snapshot_reader_keeps_backward_compat_with_json_encoded_xml(tmp_path) -> None:
    """Snapshots predating the .xml split stored XML as a JSON-encoded string."""
    out_dir = tmp_path / "legacy"
    payload_dir = out_dir / "payloads"
    payload_dir.mkdir(parents=True)
    legacy_payload_path = payload_dir / "legacy-payload.json"
    legacy_xml = "<dmod:dataFlow><node nodeID=\"1\"/></dmod:dataFlow>"
    legacy_payload_path.write_bytes(json.dumps(legacy_xml).encode("utf-8"))
    encoded = legacy_payload_path.read_bytes()
    writer = SnapshotWriter(out_dir)
    metadata = type(
        writer.write_payload(
            payload_id="placeholder",
            kind="bw_get_dataflow",
            source="bw://bw_get_dataflow",
            payload={"placeholder": True},
        )
    )(
        payload_id="legacy-payload",
        kind="bw_get_dataflow",
        source="bw://bw_get_dataflow",
        relative_path="payloads/legacy-payload.json",
        sha256="0" * 64,
        size_bytes=len(encoded),
    )

    reader = SnapshotReader(out_dir)
    loaded = reader.read_payload(metadata)
    assert loaded == legacy_xml


def test_snapshot_writer_stores_object_fingerprints_for_graph_payload(tmp_path) -> None:
    writer = SnapshotWriter(tmp_path / "snap")
    graph_payload = {"nodes": [{"id": "A", "type": "ADSO"}], "edges": []}

    metadata = writer.write_payload(
        payload_id="graph",
        kind="graph",
        source="fixture://graph.json",
        payload=graph_payload,
    )
    manifest = writer.write_manifest(mode="offline-fixture", payloads=[metadata])

    stored = (tmp_path / "snap" / metadata.relative_path).read_bytes()
    expected_fingerprint = object_fingerprint(BwNode(id="A", type="ADSO"))

    assert metadata.sha256 == hashlib.sha256(stored).hexdigest()
    assert metadata.object_fingerprints == {"A": expected_fingerprint}
    assert manifest.payloads[0].object_fingerprints == {"A": expected_fingerprint}
    assert json.loads((tmp_path / "snap" / "manifest.json").read_text(encoding="utf-8"))[
        "payloads"
    ][0]["object_fingerprints"] == {"A": expected_fingerprint}


def test_payload_metadata_accepts_manifests_with_and_without_object_fingerprints() -> None:
    legacy_payload = {
        "payload_id": "graph",
        "kind": "graph",
        "source": "fixture://graph.json",
        "relative_path": "payloads/graph.json",
        "sha256": "a" * 64,
        "size_bytes": 42,
    }

    legacy = PayloadMetadata.model_validate(legacy_payload)
    enriched = PayloadMetadata.model_validate(
        {**legacy_payload, "object_fingerprints": {"A": "b" * 64}}
    )
    manifest = SnapshotManifest.model_validate(
        {"mode": "offline-fixture", "payloads": [legacy_payload]}
    )

    assert legacy.object_fingerprints is None
    assert enriched.object_fingerprints == {"A": "b" * 64}
    assert manifest.payloads[0].object_fingerprints is None
