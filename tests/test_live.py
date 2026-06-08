from __future__ import annotations

import hashlib
from typing import Any

from bwli.live import collect_live_snapshot


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


def test_collect_live_snapshot_uses_unique_payload_paths_for_colliding_labels(tmp_path) -> None:
    first = "Z" * 90 + "A"
    second = "Z" * 90 + "B"
    client = RecordingLiveClient()

    manifest = collect_live_snapshot(
        out_dir=tmp_path,
        client_factory=lambda: client,
        search_terms=[first, second],
    )

    paths = [payload.relative_path for payload in manifest.payloads]
    assert len(paths) == 2
    assert len(set(paths)) == 2
    for payload in manifest.payloads:
        encoded = (tmp_path / payload.relative_path).read_bytes()
        assert payload.sha256 == hashlib.sha256(encoded).hexdigest()
    assert client.closed is True
