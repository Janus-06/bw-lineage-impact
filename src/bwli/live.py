from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict

from bwli.endpoints import REQUEST_MONITOR_TOP_CAP, REQUEST_MONITOR_TOP_DEFAULT, DataflowDirection
from bwli.redact import redact_text
from bwli.snapshot import PayloadMetadata, SnapshotManifest, SnapshotWriter


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

    def fetch_xref(
        self,
        object_name: str,
        *,
        object_type: str = "ADSO",
        source_system: str | None = None,
    ) -> Any: ...

    def fetch_repository_contents(self, path: str | None = None) -> Any: ...

    def fetch_process_chain(self, chain_name: str) -> Any: ...

    def fetch_process_variant(self, process_type: str, variant_name: str) -> Any: ...

    def fetch_dtp(self, dtp_name: str) -> Any: ...

    def fetch_datasource(self, datasource_name: str, source_system: str) -> Any: ...

    def fetch_source_system(self, source_system: str) -> Any: ...

    def fetch_query(self, query_name: str) -> Any: ...

    def fetch_composite_provider(self, composite_provider_name: str) -> Any: ...

    def fetch_list_requests(
        self,
        target: str,
        *,
        target_type: str = "ADSO",
        top: int = REQUEST_MONITOR_TOP_DEFAULT,
        created_from: str | None = None,
    ) -> Any: ...

    def fetch_request(self, request_tsn: str, *, storage: str = "AQ") -> Any: ...

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
    dataflow_object_type: str = "ADSO",
    dataflow_source_system: str | None = None,
    dataflow_direction: DataflowDirection = "downwards",
    dataflow_levels: int = 3,
    query_name: str | None = None,
    datasource: tuple[str, str] | None = None,
    process_chain: str | None = None,
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
            xref_label = _xref_label(
                object_name,
                object_type=dataflow_object_type,
                source_system=dataflow_source_system,
            )
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
                    label=xref_label,
                    func=lambda: client.fetch_xref(
                        object_name,
                        object_type=dataflow_object_type,
                        source_system=dataflow_source_system,
                    ),
                    secret_values=secret_values,
                    secret_urls=secret_urls,
                )
            )
        if query_name:
            operations.append(
                _run_operation(
                    name="bw_get_query",
                    label=f"bw://bw_get_query?queryName={quote(query_name, safe='')}",
                    func=lambda: client.fetch_query(query_name),
                    secret_values=secret_values,
                    secret_urls=secret_urls,
                )
            )
        if datasource is not None:
            datasource_name, source_system = datasource
            operations.append(
                _run_operation(
                    name="bw_get_datasource",
                    label=(
                        "bw://bw_get_datasource?"
                        f"datasourceName={quote(datasource_name, safe='')}&"
                        f"sourceSystem={quote(source_system, safe='')}"
                    ),
                    func=lambda: client.fetch_datasource(datasource_name, source_system),
                    secret_values=secret_values,
                    secret_urls=secret_urls,
                )
            )
        if process_chain:
            operations.append(
                _run_operation(
                    name="bw_get_process_chain",
                    label=f"bw://bw_get_process_chain?chainName={quote(process_chain, safe='')}",
                    func=lambda: client.fetch_process_chain(process_chain),
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
    process_chains: Sequence[str] = (),
    process_variants: Sequence[tuple[str, str]] = (),
    dtps: Sequence[str] = (),
    datasources: Sequence[tuple[str, str]] = (),
    source_systems: Sequence[str] = (),
    queries: Sequence[str] = (),
    composite_providers: Sequence[str] = (),
    include_dataflow: bool = True,
    include_xref: bool = True,
    include_request_freshness: bool = False,
    request_freshness_top: int = REQUEST_MONITOR_TOP_DEFAULT,
    request_freshness_created_from: str | None = None,
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
    if not any(
        (
            search_terms,
            object_names,
            process_chains,
            process_variants,
            dtps,
            datasources,
            source_systems,
            queries,
            composite_providers,
        )
    ):
        raise ValueError(
            "at least one search term, object name, or metadata name is required "
            "for live collection"
        )

    writer = SnapshotWriter(out_dir)
    payloads: list[PayloadMetadata] = []
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
                label = _xref_label(
                    object_name,
                    object_type=dataflow_object_type,
                    source_system=dataflow_source_system,
                )
                payload_id = (
                    f"xref-{index}-{_safe_fragment(dataflow_object_type)}-"
                    f"{_safe_fragment(object_name)}"
                )
                try:
                    payload = client.fetch_xref(
                        object_name,
                        object_type=dataflow_object_type,
                        source_system=dataflow_source_system,
                    )
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

            if include_request_freshness:
                _capture_request_freshness(
                    writer=writer,
                    payloads=payloads,
                    operations=operations,
                    index=index,
                    object_name=object_name,
                    object_type=dataflow_object_type,
                    top=request_freshness_top,
                    created_from=request_freshness_created_from,
                    client=client,
                    secret_values=secret_values,
                    secret_urls=secret_urls,
                )

        for index, chain_name in enumerate(process_chains):
            _capture_live_payload(
                writer=writer,
                payloads=payloads,
                operations=operations,
                payload_id=f"process-chain-{index}-{_safe_fragment(chain_name)}",
                kind="bw_get_process_chain",
                label=(
                    "bw://bw_get_process_chain?"
                    f"chainName={quote(chain_name, safe='')}"
                ),
                func=_zero_arg_call(client.fetch_process_chain, chain_name),
                secret_values=secret_values,
                secret_urls=secret_urls,
            )

        for index, (process_type, variant_name) in enumerate(process_variants):
            _capture_live_payload(
                writer=writer,
                payloads=payloads,
                operations=operations,
                payload_id=(
                    f"process-variant-{index}-{_safe_fragment(process_type)}-"
                    f"{_safe_fragment(variant_name)}"
                ),
                kind="bw_get_process_variant",
                label=(
                    "bw://bw_get_process_variant?"
                    f"processType={quote(process_type, safe='')}&"
                    f"variantName={quote(variant_name, safe='')}"
                ),
                func=_zero_arg_call(
                    client.fetch_process_variant,
                    process_type,
                    variant_name,
                ),
                secret_values=secret_values,
                secret_urls=secret_urls,
            )

        for index, dtp_name in enumerate(dtps):
            _capture_live_payload(
                writer=writer,
                payloads=payloads,
                operations=operations,
                payload_id=f"dtp-{index}-{_safe_fragment(dtp_name)}",
                kind="bw_get_dtp",
                label=f"bw://bw_get_dtp?dtpName={quote(dtp_name, safe='')}",
                func=_zero_arg_call(client.fetch_dtp, dtp_name),
                secret_values=secret_values,
                secret_urls=secret_urls,
            )

        for index, (datasource_name, source_system) in enumerate(datasources):
            _capture_live_payload(
                writer=writer,
                payloads=payloads,
                operations=operations,
                payload_id=(
                    f"datasource-{index}-{_safe_fragment(datasource_name)}-"
                    f"{_safe_fragment(source_system)}"
                ),
                kind="bw_get_datasource",
                label=(
                    "bw://bw_get_datasource?"
                    f"datasourceName={quote(datasource_name, safe='')}&"
                    f"sourceSystem={quote(source_system, safe='')}"
                ),
                func=_zero_arg_call(
                    client.fetch_datasource,
                    datasource_name,
                    source_system,
                ),
                secret_values=secret_values,
                secret_urls=secret_urls,
            )

        for index, source_system in enumerate(source_systems):
            _capture_live_payload(
                writer=writer,
                payloads=payloads,
                operations=operations,
                payload_id=f"source-system-{index}-{_safe_fragment(source_system)}",
                kind="bw_get_source_system",
                label=(
                    "bw://bw_get_source_system?"
                    f"sourceSystem={quote(source_system, safe='')}"
                ),
                func=_zero_arg_call(client.fetch_source_system, source_system),
                secret_values=secret_values,
                secret_urls=secret_urls,
            )

        for index, query_name in enumerate(queries):
            _capture_live_payload(
                writer=writer,
                payloads=payloads,
                operations=operations,
                payload_id=f"query-{index}-{_safe_fragment(query_name)}",
                kind="bw_get_query",
                label=f"bw://bw_get_query?queryName={quote(query_name, safe='')}",
                func=_zero_arg_call(client.fetch_query, query_name),
                secret_values=secret_values,
                secret_urls=secret_urls,
            )

        for index, composite_provider_name in enumerate(composite_providers):
            _capture_live_payload(
                writer=writer,
                payloads=payloads,
                operations=operations,
                payload_id=(
                    "composite-provider-"
                    f"{index}-{_safe_fragment(composite_provider_name)}"
                ),
                kind="bw_get_composite_provider",
                label=(
                    "bw://bw_get_composite_provider?"
                    f"name={quote(composite_provider_name, safe='')}"
                ),
                func=_zero_arg_call(
                    client.fetch_composite_provider,
                    composite_provider_name,
                ),
                secret_values=secret_values,
                secret_urls=secret_urls,
            )
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


def _zero_arg_call(func: Callable[..., Any], *args: object) -> Callable[[], Any]:
    def call() -> Any:
        return func(*args)

    return call


def _capture_live_payload(
    *,
    writer: SnapshotWriter,
    payloads: list[PayloadMetadata],
    operations: list[LiveOperationSummary],
    payload_id: str,
    kind: str,
    label: str,
    func: Callable[[], Any],
    secret_values: Sequence[str],
    secret_urls: Sequence[str],
) -> None:
    try:
        payload = func()
    except Exception as exc:
        operations.append(
            _failure_summary(
                name=kind,
                label=label,
                exc=exc,
                secret_values=secret_values,
                secret_urls=secret_urls,
            )
        )
        return
    payloads.append(
        writer.write_payload(
            payload_id=payload_id,
            kind=kind,
            source=label,
            payload=payload,
        )
    )
    operations.append(_success_summary(kind, label, payload))


def _capture_request_freshness(
    *,
    writer: SnapshotWriter,
    payloads: list[PayloadMetadata],
    operations: list[LiveOperationSummary],
    index: int,
    object_name: str,
    object_type: str,
    top: int,
    created_from: str | None,
    client: BwReadClient,
    secret_values: Sequence[str],
    secret_urls: Sequence[str],
) -> None:
    safe_top = _request_freshness_top(top)
    list_label = _request_list_label(
        object_name,
        object_type=object_type,
        top=safe_top,
        created_from=created_from,
    )
    list_payload_id = f"requests-{index}-{_safe_fragment(object_name)}"
    try:
        list_payload = client.fetch_list_requests(
            object_name,
            target_type=object_type,
            top=safe_top,
            created_from=created_from,
        )
    except Exception as exc:
        operations.append(
            _failure_summary(
                name="bw_list_requests",
                label=list_label,
                exc=exc,
                secret_values=secret_values,
                secret_urls=secret_urls,
            )
        )
        return
    payloads.append(
        writer.write_payload(
            payload_id=list_payload_id,
            kind="bw_list_requests",
            source=list_label,
            payload=list_payload,
        )
    )
    operations.append(_success_summary("bw_list_requests", list_label, list_payload))

    latest = _latest_request_pointer(list_payload)
    if latest is None:
        return
    request_tsn, storage = latest
    detail_label = _request_detail_label(
        object_name,
        object_type=object_type,
        request_tsn=request_tsn,
        storage=storage,
    )
    detail_payload_id = (
        f"request-{index}-{_safe_fragment(object_name)}-"
        f"{_safe_fragment(request_tsn)}"
    )
    try:
        detail_payload = client.fetch_request(request_tsn, storage=storage)
    except Exception as exc:
        operations.append(
            _failure_summary(
                name="bw_get_request",
                label=detail_label,
                exc=exc,
                secret_values=secret_values,
                secret_urls=secret_urls,
            )
        )
        return
    payloads.append(
        writer.write_payload(
            payload_id=detail_payload_id,
            kind="bw_get_request",
            source=detail_label,
            payload=detail_payload,
        )
    )
    operations.append(_success_summary("bw_get_request", detail_label, detail_payload))


def _request_freshness_top(value: int) -> int:
    if value <= 0:
        return REQUEST_MONITOR_TOP_DEFAULT
    return min(value, REQUEST_MONITOR_TOP_CAP)


def _request_list_label(
    object_name: str,
    *,
    object_type: str,
    top: int,
    created_from: str | None,
) -> str:
    query = (
        f"objectName={quote(object_name, safe='')}&"
        f"objectType={quote(object_type, safe='')}&"
        f"top={top}"
    )
    if created_from:
        query += f"&createdFrom={quote(created_from, safe='')}"
    return f"bw://bw_list_requests?{query}"


def _request_detail_label(
    object_name: str,
    *,
    object_type: str,
    request_tsn: str,
    storage: str,
) -> str:
    query = (
        f"objectName={quote(object_name, safe='')}&"
        f"objectType={quote(object_type, safe='')}&"
        f"requestTsn={quote(request_tsn, safe='')}&"
        f"storage={quote(storage, safe='')}"
    )
    return f"bw://bw_get_request?{query}"


def _latest_request_pointer(payload: Any) -> tuple[str, str] | None:
    candidates: list[tuple[tuple[bool, str, str, int], str, str]] = []
    for index, item in enumerate(_request_payload_items(payload)):
        request_tsn = _text_value(
            item,
            "requestTsn",
            "request_tsn",
            "request",
            "requestId",
        )
        if request_tsn is None:
            continue
        timestamp = _text_value(
            item,
            "lastTimeStamp",
            "lastTimestamp",
            "timestamp",
            "createdAt",
            "requestStart",
            "requestFinish",
        ) or ""
        external = _text_value(item, "requestTsnExternal", "request_tsn_external", "tsn") or ""
        storage = _text_value(item, "storage") or "AQ"
        candidates.append(
            (
                (bool(timestamp), timestamp, external or request_tsn, -index),
                request_tsn,
                storage,
            )
        )
    if not candidates:
        return None
    _, request_tsn, storage = max(candidates, key=lambda item: item[0])
    return request_tsn, storage


def _request_payload_items(payload: Any) -> list[dict[str, object]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("requests", "results", "items", "objects"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [payload]
    return []


def _text_value(item: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _xref_label(
    object_name: str,
    *,
    object_type: str,
    source_system: str | None = None,
) -> str:
    """Mirror bw-modeling-mcp xref shape: objectType/objectName only, no direction."""

    query = (
        f"objectType={quote(object_type.upper(), safe='')}&"
        f"objectName={quote(object_name.upper(), safe='')}"
    )
    if object_type.upper() == "RSDS" and source_system:
        query += f"&sourceSystem={quote(source_system.upper(), safe='')}"
    return f"bw://bw_xref?{query}"


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
