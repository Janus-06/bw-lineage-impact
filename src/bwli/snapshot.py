from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class PayloadMetadata(BaseModel):
    payload_id: str
    kind: str
    source: str
    relative_path: str
    sha256: str
    size_bytes: int


class SnapshotManifest(BaseModel):
    schema_version: str = "1.0"
    mode: str
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    payloads: list[PayloadMetadata]


class SnapshotWriter:
    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.payload_dir = out_dir / "payloads"
        self.payload_dir.mkdir(parents=True, exist_ok=True)

    def write_payload(
        self,
        *,
        payload_id: str,
        kind: str,
        source: str,
        payload: Any,
    ) -> PayloadMetadata:
        safe_id = _safe_payload_id(payload_id)
        if isinstance(payload, str):
            extension = "xml"
            encoded = payload.encode("utf-8")
        else:
            extension = "json"
            encoded = json.dumps(
                payload, ensure_ascii=False, indent=2, sort_keys=True
            ).encode("utf-8")
        relative_path = f"payloads/{safe_id}.{extension}"
        path = self.out_dir / relative_path
        path.write_bytes(encoded)
        return PayloadMetadata(
            payload_id=safe_id,
            kind=kind,
            source=source,
            relative_path=relative_path,
            sha256=hashlib.sha256(encoded).hexdigest(),
            size_bytes=len(encoded),
        )

    def write_manifest(self, *, mode: str, payloads: list[PayloadMetadata]) -> SnapshotManifest:
        manifest = SnapshotManifest(mode=mode, payloads=payloads)
        path = self.out_dir / "manifest.json"
        path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        return manifest


class SnapshotReader:
    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir

    def read_manifest(self) -> SnapshotManifest:
        raw = json.loads((self.out_dir / "manifest.json").read_text(encoding="utf-8"))
        return SnapshotManifest.model_validate(raw)

    def read_payload(self, metadata: PayloadMetadata) -> Any:
        payload_path = _resolve_snapshot_relative_path(self.out_dir, metadata.relative_path)
        suffix = payload_path.suffix.lower()
        if suffix == ".xml":
            return payload_path.read_text(encoding="utf-8")
        return json.loads(payload_path.read_text(encoding="utf-8"))


def write_fixture_snapshot(fixture_path: Path, out_dir: Path) -> SnapshotManifest:
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    kind = (
        str(payload.get("kind", fixture_path.stem))
        if isinstance(payload, dict)
        else fixture_path.stem
    )
    writer = SnapshotWriter(out_dir)
    metadata = writer.write_payload(
        payload_id=fixture_path.stem,
        kind=kind,
        source=f"fixture://{fixture_path.name}",
        payload=payload,
    )
    return writer.write_manifest(mode="offline-fixture", payloads=[metadata])


def _safe_payload_id(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)
    return safe or "payload"


def _resolve_snapshot_relative_path(out_dir: Path, relative_path: str) -> Path:
    _ensure_relative(relative_path)
    base = out_dir.resolve()
    resolved = (base / relative_path).resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"unsafe snapshot relative path: {relative_path}") from exc
    return resolved


def _ensure_relative(relative_path: str) -> None:
    path = Path(relative_path)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe snapshot relative path: {relative_path}")
