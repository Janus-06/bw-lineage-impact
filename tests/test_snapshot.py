from __future__ import annotations

from bwli.snapshot import SnapshotReader, SnapshotWriter, write_fixture_snapshot


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
