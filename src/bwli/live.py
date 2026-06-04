from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from bwli.snapshot import SnapshotManifest, SnapshotWriter

XrefDirection = Literal["upstream", "downstream"]


class BwReadClient(Protocol):
    def fetch_search(self, search_term: str, *, object_type: str | None = None) -> Any: ...

    def fetch_dataflow(self, object_name: str) -> Any: ...

    def fetch_xref(self, object_name: str, *, direction: str = "downstream") -> Any: ...

    def close(self) -> None: ...


ClientFactory = Callable[[], BwReadClient]


class LiveOperationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    ok: bool
    status: Literal["ok", "error"]
    payload_kind: str | None = None
    item_count: int | None = None
    error: str | None = None


class LiveSmokeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = "live-read-only"
    read_only: bool = True
    status: Literal["ok", "partial", "error"]
    operations: list[LiveOperationSummary]


class LiveCollectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = "live-read-only"
    read_only: bool = True
    manifest_path: str
    manifest: SnapshotManifest


def run_live_smoke(
    *,
    client_factory: ClientFactory,
    search_term: str,
    object_name: str | None = None,
    xref_direction: XrefDirection = "downstream",
    secret_values: Sequence[str] = (),
) -> LiveSmokeResult:
    client = client_factory()
    try:
        operations = [
            _run_operation(
                name="bw_search",
                label="bw://bw_search",
                func=lambda: client.fetch_search(search_term),
                secret_values=secret_values,
            )
        ]
        if object_name:
            operations.append(
                _run_operation(
                    name="bw_get_dataflow",
                    label="bw://bw_get_dataflow",
                    func=lambda: client.fetch_dataflow(object_name),
                    secret_values=secret_values,
                )
            )
            operations.append(
                _run_operation(
                    name="bw_xref",
                    label=f"bw://bw_xref/{xref_direction}",
                    func=lambda: client.fetch_xref(object_name, direction=xref_direction),
                    secret_values=secret_values,
                )
            )
    finally:
        client.close()

    ok_count = sum(1 for operation in operations if operation.ok)
    if ok_count == len(operations):
        status: Literal["ok", "partial", "error"] = "ok"
    elif ok_count > 0:
        status = "partial"
    else:
        status = "error"
    return LiveSmokeResult(status=status, operations=operations)


def collect_live_snapshot(
    *,
    out_dir: Path,
    client_factory: ClientFactory,
    search_terms: Sequence[str] = (),
    object_names: Sequence[str] = (),
    include_dataflow: bool = True,
    include_xref: bool = True,
    xref_direction: XrefDirection = "downstream",
) -> SnapshotManifest:
    if not search_terms and not object_names:
        raise ValueError("at least one search term or object name is required for live collection")

    writer = SnapshotWriter(out_dir)
    payloads = []
    client = client_factory()
    try:
        for term in search_terms:
            payloads.append(
                writer.write_payload(
                    payload_id=f"search-{_safe_fragment(term)}",
                    kind="bw_search",
                    source="bw://bw_search",
                    payload=client.fetch_search(term),
                )
            )
        for object_name in object_names:
            if include_dataflow:
                payloads.append(
                    writer.write_payload(
                        payload_id=f"dataflow-{_safe_fragment(object_name)}",
                        kind="bw_get_dataflow",
                        source="bw://bw_get_dataflow",
                        payload=client.fetch_dataflow(object_name),
                    )
                )
            if include_xref:
                payloads.append(
                    writer.write_payload(
                        payload_id=f"xref-{_safe_fragment(object_name)}-{xref_direction}",
                        kind="bw_xref",
                        source=f"bw://bw_xref/{xref_direction}",
                        payload=client.fetch_xref(object_name, direction=xref_direction),
                    )
                )
    finally:
        client.close()
    return writer.write_manifest(mode="live-read-only", payloads=payloads)


def _run_operation(
    *,
    name: str,
    label: str,
    func: Callable[[], Any],
    secret_values: Sequence[str],
) -> LiveOperationSummary:
    try:
        payload = func()
    except Exception as exc:
        return LiveOperationSummary(
            name=name,
            label=label,
            ok=False,
            status="error",
            error=_redact_text(str(exc) or type(exc).__name__, secret_values=secret_values),
        )
    payload_kind, item_count = _payload_shape(payload)
    return LiveOperationSummary(
        name=name,
        label=label,
        ok=True,
        status="ok",
        payload_kind=payload_kind,
        item_count=item_count,
    )


def _payload_shape(payload: Any) -> tuple[str, int | None]:
    if isinstance(payload, list):
        return "list", len(payload)
    if isinstance(payload, dict):
        for key in ("objects", "results", "items", "references", "nodes", "edges"):
            value = payload.get(key)
            if isinstance(value, list):
                return f"dict.{key}", len(value)
        return "dict", len(payload)
    if isinstance(payload, str):
        return "text", None
    return type(payload).__name__, None


def _safe_fragment(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)
    if safe:
        return safe[:80]
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _redact_text(value: str, *, secret_values: Sequence[str]) -> str:
    redacted = value
    for secret in secret_values:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    redacted = re.sub(
        r"(?i)(authorization|password|passwd|pwd|token|api[_-]?key)(\s*[:=]\s*)[^\s,;]+",
        r"\1\2[REDACTED]",
        redacted,
    )
    redacted = re.sub(r"(?i)Bearer\s+[^\s,;]+", "Bearer [REDACTED]", redacted)
    return redacted
