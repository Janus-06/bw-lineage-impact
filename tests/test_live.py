from __future__ import annotations

import hashlib
from typing import Any

import pytest

from bwli.live import LiveCollectionError, collect_live_snapshot
from bwli.snapshot import SnapshotReader


class RecordingLiveClient:
    def __init__(self) -> None:
        self.closed = False

    def fetch_search(self, search_term: str, *, object_type: str | None = None) -> dict[str, Any]:
        return {"term": search_term, "object_type": object_type}

    def fetch_dataflow(
        self,
        object_name: str,
        *,
        object_type: str = "ADSO",
        source_system: str | None = None,
        direction: str = "downwards",
        levels: int = 3,
    ) -> dict[str, Any]:
        return {
            "object": object_name,
            "object_type": object_type,
            "source_system": source_system,
            "direction": direction,
            "levels": levels,
        }

    def fetch_xref(self, object_name: str, *, direction: str = "downstream") -> dict[str, Any]:
        return {"object": object_name, "direction": direction}

    def close(self) -> None:
        self.closed = True


class FlakyLiveClient(RecordingLiveClient):
    def __init__(self, fail_for: set[str], leak_value: str | None = None) -> None:
        super().__init__()
        self._fail_for = fail_for
        self._leak_value = leak_value

    def fetch_dataflow(
        self,
        object_name: str,
        *,
        object_type: str = "ADSO",
        source_system: str | None = None,
        direction: str = "downwards",
        levels: int = 3,
    ) -> Any:
        if object_name in self._fail_for:
            leak = self._leak_value or ""
            raise RuntimeError(
                f"dataflow failed for {object_name} token={leak} url=https://bw.example.invalid/sap/bw"
            )
        return super().fetch_dataflow(
            object_name,
            object_type=object_type,
            source_system=source_system,
            direction=direction,
            levels=levels,
        )


class XmlDataflowClient(RecordingLiveClient):
    def __init__(self, xml: str) -> None:
        super().__init__()
        self._xml = xml

    def fetch_dataflow(
        self,
        object_name: str,
        *,
        object_type: str = "ADSO",
        source_system: str | None = None,
        direction: str = "downwards",
        levels: int = 3,
    ) -> str:
        return self._xml


def test_collect_live_snapshot_uses_unique_payload_paths_for_colliding_labels(tmp_path) -> None:
    first = "Z" * 90 + "A"
    second = "Z" * 90 + "B"
    client = RecordingLiveClient()

    result = collect_live_snapshot(
        out_dir=tmp_path,
        client_factory=lambda: client,
        search_terms=[first, second],
    )

    paths = [payload.relative_path for payload in result.manifest.payloads]
    assert len(paths) == 2
    assert len(set(paths)) == 2
    for payload in result.manifest.payloads:
        encoded = (tmp_path / payload.relative_path).read_bytes()
        assert payload.sha256 == hashlib.sha256(encoded).hexdigest()
    assert client.closed is True
    assert result.succeeded == 2
    assert result.failed == 0


def test_collect_live_snapshot_partial_success_keeps_succeeded_payloads(tmp_path) -> None:
    client = FlakyLiveClient(fail_for={"ZBAD"}, leak_value="redaction-target-secret")

    result = collect_live_snapshot(
        out_dir=tmp_path,
        client_factory=lambda: client,
        object_names=["ZOK", "ZBAD"],
        include_xref=False,
        secret_values=["redaction-target-secret"],
        secret_urls=["https://bw.example.invalid/sap/bw"],
    )

    assert result.succeeded == 1
    assert result.failed == 1
    assert len(result.manifest.payloads) == 1
    assert result.manifest.payloads[0].source.startswith("bw://bw_get_dataflow?")
    assert "objectName=ZOK" in result.manifest.payloads[0].source
    failed_ops = [op for op in result.operations if not op.ok]
    assert len(failed_ops) == 1
    assert "redaction-target-secret" not in failed_ops[0].error
    assert "[REDACTED]" in failed_ops[0].error
    assert "bw.example.invalid" not in failed_ops[0].error


def test_collect_live_snapshot_writes_xml_payload_as_xml_file(tmp_path) -> None:
    xml = '<?xml version="1.0"?><dmod:dataFlow><node nodeID="1"/></dmod:dataFlow>'
    client = XmlDataflowClient(xml)

    result = collect_live_snapshot(
        out_dir=tmp_path,
        client_factory=lambda: client,
        object_names=["ZADSO_DEMO"],
        include_xref=False,
    )

    assert result.succeeded == 1
    payload = result.manifest.payloads[0]
    assert payload.relative_path.endswith(".xml")
    stored = (tmp_path / payload.relative_path).read_text(encoding="utf-8")
    assert stored == xml
    reader = SnapshotReader(tmp_path)
    loaded = reader.read_payload(payload)
    assert isinstance(loaded, str)
    assert loaded == xml


def test_collect_live_snapshot_raises_when_all_calls_fail(tmp_path) -> None:
    client = FlakyLiveClient(fail_for={"ZBAD"}, leak_value="redaction-target-secret")

    with pytest.raises(LiveCollectionError, match="no payloads collected"):
        collect_live_snapshot(
            out_dir=tmp_path,
            client_factory=lambda: client,
            object_names=["ZBAD"],
            include_xref=False,
            secret_values=["redaction-target-secret"],
        )
