from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict

from bwli.endpoints import DataflowDirection
from bwli.redact import redact_text
from bwli.snapshot import SnapshotManifest, SnapshotWriter

XrefDirection = Literal["upstream", "downstream"]


class BwReadClient(Protocol):
    def fetch_search(self, search_term: str, *, object_type: str | None = None) -> Any: ...

    def fetch_dataflow(
        self,
        object_name: str,
        *,
        object_type: str = "ADSO",
        source_system: str | None = None,
        direction: DataflowDirection = "downwards",
        levels: int = 3,
    ) -> Any: ...

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


class LiveCollectionResult(BaseModel):
    """Internal result of a live snapshot collection run."""

    model_config = ConfigDict(extra="forbid")

    manifest: SnapshotManifest
    operations: list[LiveOperationSummary]
    succeeded: int
    failed: int


class LiveCollectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = "live-read-only"
    read_only: bool = True
    manifest_path: str
    manifest: SnapshotManifest
    operations: list[LiveOperationSummary] = []
    succeeded: int = 0
    failed: int = 0


class LiveCollectionError(ValueError):
    """Raised when a live collection run has no successful read-only payloads."""


def run_live_smoke(
    *,
    client_factory: ClientFactory,
    search_term: str,
    object_name: str | None = None,
    xref_direction: XrefDirection = "downstream",
    dataflow_object_type: str = "ADSO",
    dataflow_source_system: str | None = None,
    dataflow_direction: DataflowDirection = "downwards",
    dataflow_levels: int = 3,
    secret_values: Sequence[str] = (),
    secret_urls: Sequence[str] = (),
) -> LiveSmokeResult:
    client = client_factory()
    try:
        operations = [
            _run_operation(
                name="bw_search",
                label="bw://bw_search",
                func=lambda: client.fetch_search(search_term),
                secret_values=secret_values,
                secret_urls=secret_urls,
            )
        ]
        if object_name:
            operations.append(
                _run_operation(
                    name="bw_get_dataflow",
                    label="bw://bw_get_dataflow",
                    func=lambda: client.fetch_dataflow(
                        object_name,
                        object_type=dataflow_object_type,
                        source_system=dataflow_source_system,
                        direction=dataflow_direction,
                        levels=dataflow_levels,
                    ),
                    secret_values=secret_values,
                    secret_urls=secret_urls,
                )
            )
            operations.append(
                _run_operation(
                    name="bw_xref",
                    label=f"bw://bw_xref/{xref_direction}",
                    func=lambda: client.fetch_xref(object_name, direction=xref_direction),
                    secret_values=secret_values,
                    secret_urls=secret_urls,
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
    dataflow_object_type: str = "ADSO",
    dataflow_source_system: str | None = None,
    dataflow_direction: DataflowDirection = "downwards",
    dataflow_levels: int = 3,
    secret_values: Sequence[str] = (),
    secret_urls: Sequence[str] = (),
) -> LiveCollectionResult:
    """Capture a live BW snapshot, surviving per-object fetch failures.

    Each fetch is wrapped: successful payloads are persisted, failed ones are
    recorded as redacted operation summaries. Raises only when nothing succeeded.
    """
    if not search_terms and not object_names:
        raise ValueError("at least one search term or object name is required for live collection")

    writer = SnapshotWriter(out_dir)
    payloads = []
    operations: list[LiveOperationSummary] = []
    client = client_factory()
    try:
        for index, term in enumerate(search_terms):
            payload_id = f"search-{index}-{_safe_fragment(term)}"
            label = f"bw://bw_search?term={quote(term, safe='')}"
            try:
                payload = client.fetch_search(term)
            except Exception as exc:
                operations.append(
                    _failure_summary(
                        name="bw_search",
                        label=label,
                        exc=exc,
                        secret_values=secret_values,
                        secret_urls=secret_urls,
                    )
                )
                continue
            payloads.append(
                writer.write_payload(
                    payload_id=payload_id,
                    kind="bw_search",
                    source=label,
                    payload=payload,
                )
            )
            operations.append(_success_summary("bw_search", label, payload))

        for index, object_name in enumerate(object_names):
            if include_dataflow:
                label = (
                    "bw://bw_get_dataflow?"
                    f"objectName={quote(object_name, safe='')}&"
                    f"objectType={quote(dataflow_object_type, safe='')}"
                )
                payload_id = f"dataflow-{index}-{_safe_fragment(object_name)}"
                try:
                    payload = client.fetch_dataflow(
                        object_name,
                        object_type=dataflow_object_type,
                        source_system=dataflow_source_system,
                        direction=dataflow_direction,
                        levels=dataflow_levels,
                    )
                except Exception as exc:
                    operations.append(
                        _failure_summary(
                            name="bw_get_dataflow",
                            label=label,
                            exc=exc,
                            secret_values=secret_values,
                            secret_urls=secret_urls,
                        )
                    )
                else:
                    payloads.append(
                        writer.write_payload(
                            payload_id=payload_id,
                            kind="bw_get_dataflow",
                            source=label,
                            payload=payload,
                        )
                    )
                    operations.append(
                        _success_summary("bw_get_dataflow", label, payload)
                    )
            if include_xref:
                label = (
                    f"bw://bw_xref/{xref_direction}?"
                    f"objectName={quote(object_name, safe='')}"
                )
                payload_id = f"xref-{index}-{_safe_fragment(object_name)}-{xref_direction}"
                try:
                    payload = client.fetch_xref(object_name, direction=xref_direction)
                except Exception as exc:
                    operations.append(
                        _failure_summary(
                            name="bw_xref",
                            label=label,
                            exc=exc,
                            secret_values=secret_values,
                            secret_urls=secret_urls,
                        )
                    )
                else:
                    payloads.append(
                        writer.write_payload(
                            payload_id=payload_id,
                            kind="bw_xref",
                            source=label,
                            payload=payload,
                        )
                    )
                    operations.append(_success_summary("bw_xref", label, payload))
    finally:
        client.close()

    failed = sum(1 for op in operations if not op.ok)
    succeeded = len(operations) - failed
    if succeeded == 0:
        preview = "; ".join(
            f"{op.name} {op.label}: {op.error or 'error'}"
            for op in operations[:3]
        )
        raise LiveCollectionError(
            "live snapshot capture failed: "
            f"no payloads collected ({failed} operation(s) failed)"
            f"; {preview}"
        )
    manifest = writer.write_manifest(mode="live-read-only", payloads=payloads)
    return LiveCollectionResult(
        manifest=manifest,
        operations=operations,
        succeeded=succeeded,
        failed=failed,
    )


def _success_summary(name: str, label: str, payload: Any) -> LiveOperationSummary:
    payload_kind, item_count = _payload_shape(payload)
    return LiveOperationSummary(
        name=name,
        label=label,
        ok=True,
        status="ok",
        payload_kind=payload_kind,
        item_count=item_count,
    )


def _failure_summary(
    *,
    name: str,
    label: str,
    exc: Exception,
    secret_values: Sequence[str],
    secret_urls: Sequence[str],
) -> LiveOperationSummary:
    message = str(exc) or type(exc).__name__
    return LiveOperationSummary(
        name=name,
        label=label,
        ok=False,
        status="error",
        error=redact_text(message, secret_values=secret_values, urls=secret_urls),
    )


def _run_operation(
    *,
    name: str,
    label: str,
    func: Callable[[], Any],
    secret_values: Sequence[str],
    secret_urls: Sequence[str] = (),
) -> LiveOperationSummary:
    try:
        payload = func()
    except Exception as exc:
        return _failure_summary(
            name=name,
            label=label,
            exc=exc,
            secret_values=secret_values,
            secret_urls=secret_urls,
        )
    return _success_summary(name, label, payload)


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
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    if not safe:
        return digest
    return f"{safe[:67]}-{digest}"


def _redact_text(value: str, *, secret_values: Sequence[str]) -> str:
    """Backwards-compatible shim. New code should call bwli.redact.redact_text."""
    return redact_text(value, secret_values=secret_values)
