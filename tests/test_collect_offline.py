from __future__ import annotations

from bwli.cli import app
from bwli.snapshot import SnapshotReader


def test_collect_fixture_writes_snapshot_manifest_and_payload(tmp_path, capsys) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        '{"kind":"bwsearch","objects":[{"technicalName":"ZADSO"}]}',
        encoding="utf-8",
    )
    out_dir = tmp_path / "snapshot"

    assert app(["collect", "--fixture", str(fixture), "--out", str(out_dir)]) == 0

    captured = capsys.readouterr()
    assert "manifest.json" in captured.out

    reader = SnapshotReader(out_dir)
    manifest = reader.read_manifest()
    payload = reader.read_payload(manifest.payloads[0])

    assert manifest.mode == "offline-fixture"
    assert payload == {"kind": "bwsearch", "objects": [{"technicalName": "ZADSO"}]}


def test_collect_live_is_gated_and_does_not_contact_bw(monkeypatch, capsys) -> None:
    monkeypatch.delenv("BWLI_LIVE", raising=False)

    assert app(["collect", "--live"]) == 2

    captured = capsys.readouterr()
    assert "BWLI_LIVE=1" in captured.err
    assert "no BW calls" in captured.err
