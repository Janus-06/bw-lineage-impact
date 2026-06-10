from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from importlib import import_module
from pathlib import Path
from typing import Literal, Protocol, cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from bwli import __version__
from bwli.client import BwClient
from bwli.config import ConfigError, LlmRuntimeConfig, validate_local_llm_base_url
from bwli.dataflow import DataflowOutputFormat, render_dataflow
from bwli.endpoints import DataflowDirection
from bwli.field_lineage import (
    SqlParseResult,
    load_text,
    parse_native_sql_view,
    parse_transformation_mapping_xml,
    render_field_lineage,
    render_sql_view_evidence,
)
from bwli.graph import BwGraph, Direction
from bwli.impact import (
    ChangeEvent,
    ChangeSet,
    ChangeType,
    diff_graphs,
    load_changes,
    render_impact_report,
    render_snapshot_diff,
    run_impact_analysis,
)
from bwli.lineage import load_graph, render_lineage
from bwli.live import (
    BwReadClient,
    LiveCollectionResponse,
    LiveSmokeResult,
    collect_live_snapshot,
    run_live_smoke,
)
from bwli.redact import redact_text
from bwli.repository import (
    normalize_repository_path,
    parse_repository_contents_xml,
)
from bwli.store import (
    CaptureScopeRecord,
    CatalogEdgeRecord,
    CatalogObjectRecord,
    CatalogSnapshotRecord,
    CatalogStore,
    GlossaryTermRecord,
    catalog_path_for,
    ingest_fixture_payload,
    ingest_manifest,
)
from bwli.store.catalog import EdgeInput, ObjectInput
from bwli.store.secret_guard import SecretPersistenceError, assert_no_persisted_secrets
from bwli.traversal import BoundedLineageResult, bounded_lineage

LineageFormat = Literal["json", "mermaid", "md"]
ImpactFormat = Literal["json", "md"]
EvidenceFormat = Literal["json", "md"]
RuntimeConfigSource = Literal["env", "ui", "unset"]
ConnectionStatus = Literal["unconfigured", "untested", "ok", "failed", "stale"]


class HealthResponse(BaseModel):
    status: str = "ok"
    local_only: bool = True
    read_only: bool = True
    llm_enabled_by_default: bool = False
    version: str = __version__


class RenderedResponse(BaseModel):
    format: str
    content: str


class RuntimeBwConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    user: str
    password: str
    client: str
    language: str = "EN"
    verify_ssl: bool = True
    ca_bundle: str | None = None
    trust_env: bool = True


class RuntimeLlmConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None


class RuntimeConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bw: RuntimeBwConfigRequest | None = None
    llm: RuntimeLlmConfigRequest | None = None
    persist_to_env: bool = False


class RuntimeBwConfigState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: RuntimeConfigSource = "unset"
    configured: bool = False
    url: str | None = None
    user: str | None = None
    password: str | None = None
    client: str | None = None
    language: str = "EN"
    verify_ssl: bool = True
    ca_bundle: str | None = None
    trust_env: bool = True


class RuntimeLlmConfigState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: RuntimeConfigSource = "unset"
    enabled: bool = False
    configured: bool = False
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None


class RuntimeConfigState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    storage: str = "process-memory"
    connection_status: ConnectionStatus = "unconfigured"
    bw: RuntimeBwConfigState = Field(default_factory=RuntimeBwConfigState)
    llm: RuntimeLlmConfigState = Field(default_factory=RuntimeLlmConfigState)

    def redacted(self) -> RuntimeConfigResponse:
        return RuntimeConfigResponse(
            storage=self.storage,
            bw=RuntimeBwConfigPublic(
                configured=self.bw.configured,
                url=self.bw.url,
                user=self.bw.user,
                password="[REDACTED]" if self.bw.configured else None,
                client=self.bw.client,
                language=self.bw.language,
                verify_ssl=self.bw.verify_ssl,
                ca_bundle=self.bw.ca_bundle,
            ),
            llm=RuntimeLlmConfigPublic(
                enabled=self.llm.enabled,
                configured=self.llm.configured,
                base_url=self.llm.base_url,
                model=self.llm.model,
                api_key="[REDACTED]" if self.llm.configured else None,
            ),
        )

    def redacted_v1(self) -> V1RuntimeConfigResponse:
        return V1RuntimeConfigResponse(
            storage=self.storage,
            connection_status=self.connection_status,
            bw=V1RuntimeBwConfigPublic(
                source=self.bw.source,
                configured=self.bw.configured,
                url=self.bw.url,
                user=self.bw.user,
                password="[REDACTED]" if self.bw.configured else None,
                client=self.bw.client,
                language=self.bw.language,
                verify_ssl=self.bw.verify_ssl,
                ca_bundle=self.bw.ca_bundle,
                trust_env=self.bw.trust_env,
            ),
            llm=V1RuntimeLlmConfigPublic(
                source=self.llm.source,
                enabled=self.llm.enabled,
                configured=self.llm.configured,
                base_url=self.llm.base_url,
                model=self.llm.model,
                api_key="[REDACTED]" if self.llm.configured else None,
            ),
        )


class RuntimeBwConfigPublic(BaseModel):
    configured: bool
    url: str | None = None
    user: str | None = None
    password: str | None = None
    client: str | None = None
    language: str = "EN"
    verify_ssl: bool = True
    ca_bundle: str | None = None


class RuntimeLlmConfigPublic(BaseModel):
    enabled: bool
    configured: bool
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None


class RuntimeConfigResponse(BaseModel):
    storage: str
    bw: RuntimeBwConfigPublic
    llm: RuntimeLlmConfigPublic


class V1RuntimeBwConfigPublic(RuntimeBwConfigPublic):
    source: RuntimeConfigSource = "unset"
    trust_env: bool = True


class V1RuntimeLlmConfigPublic(RuntimeLlmConfigPublic):
    source: RuntimeConfigSource = "unset"


class V1RuntimeConfigResponse(BaseModel):
    storage: str
    connection_status: ConnectionStatus
    bw: V1RuntimeBwConfigPublic
    llm: V1RuntimeLlmConfigPublic


class LineageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph_path: str
    object_id: str
    direction: Direction = Direction.DOWNSTREAM
    max_depth: int = Field(default=3, ge=0, le=20)
    format: LineageFormat = "json"


class ImpactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph_path: str
    changes_path: str
    max_depth: int = Field(default=3, ge=0, le=20)
    format: ImpactFormat = "json"


class DiffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    before_path: str
    after_path: str


class SqlViewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    view_id: str
    sql_file: str
    format: EvidenceFormat = "json"


class FieldLineageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    xml_file: str
    transformation_id: str
    source_object: str
    target_object: str
    format: EvidenceFormat = "json"


class LiveSmokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_read_only: bool = False
    search_term: str = "*"
    object_name: str | None = None
    xref_direction: Literal["upstream", "downstream"] = "downstream"
    object_type: str = "ADSO"
    source_system: str | None = None
    dataflow_direction: DataflowDirection = "downwards"
    dataflow_levels: int = Field(default=3, ge=0, le=20)


class LiveCollectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_read_only: bool = False
    out_dir: str = ".tmp/live-snapshot"
    search_terms: list[str] = Field(default_factory=list)
    object_names: list[str] = Field(default_factory=list)
    include_dataflow: bool = True
    include_xref: bool = True
    xref_direction: Literal["upstream", "downstream"] = "downstream"
    object_type: str = "ADSO"
    source_system: str | None = None
    dataflow_direction: DataflowDirection = "downwards"
    dataflow_levels: int = Field(default=3, ge=0, le=20)


class LiveDataflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_read_only: bool = False
    object_name: str
    object_type: str = "ADSO"
    source_system: str | None = None
    direction: DataflowDirection = "downwards"
    levels: int = Field(default=3, ge=0, le=20)
    format: DataflowOutputFormat = "mermaid"


class V1ConnectionTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_read_only: bool = False
    search_term: str = "Z*"


class V1SnapshotCaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_read_only: bool = False
    fixture_path: str | None = None
    manifest_path: str | None = None
    search_terms: list[str] = Field(default_factory=list)
    object_names: list[str] = Field(default_factory=list)
    include_dataflow: bool = True
    include_xref: bool = True
    xref_direction: Literal["upstream", "downstream"] = "downstream"
    object_type: str = "ADSO"
    source_system: str | None = None
    dataflow_direction: DataflowDirection = "downwards"
    dataflow_levels: int = Field(default=3, ge=0, le=20)


class V1SnapshotListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshots: list[dict[str, object]]


class V1ObjectListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, object]]
    next_cursor: str | None = None
    limit: int


class V1RepositoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    source: Literal["live", "cache", "empty"]
    count: int
    items: list[dict[str, object]]
    action_required: str | None = None


class V1CaptureScopeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    items: list[dict[str, object]]


class V1GlossaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    query: str | None = None
    count: int
    items: list[dict[str, object]]


class V1LineageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_id: str
    direction: Direction = Direction.DOWNSTREAM
    depth: int = Field(default=1, ge=0, le=20)
    node_cap: int = Field(default=25, ge=1, le=500)
    edge_cap: int = Field(default=60, ge=0, le=1000)


class V1LineageExpandRequest(V1LineageRequest):
    expand_object_id: str | None = None


class V1ImpactScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_id: str
    change_type: ChangeType
    field: str | None = None
    value_description: str | None = None
    description: str | None = None
    depth: int = Field(default=3, ge=0, le=20)
    node_cap: int = Field(default=25, ge=1, le=500)
    edge_cap: int = Field(default=60, ge=0, le=1000)


class V1SqlExplainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    view_id: str
    sql_file: str | None = None
    sql_text: str | None = None
    format: EvidenceFormat = "json"


class V1SqlDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    target_dialect: str = "sap-hana-sql"
    view_id: str | None = None
    sql_file: str | None = None
    sql_text: str | None = None


BwClientFactory = Callable[[RuntimeBwConfigState], BwReadClient]


class SqlDraftFactory(Protocol):
    def __call__(
        self,
        result: SqlParseResult,
        *,
        question: str,
        target_dialect: str,
        runtime: LlmRuntimeConfig,
    ) -> dict[str, object]: ...


class ImpactAdviceFactory(Protocol):
    def __call__(
        self,
        impact_payload: dict[str, object],
        *,
        runtime: LlmRuntimeConfig,
    ) -> dict[str, object]: ...


class LineageAdviceFactory(Protocol):
    def __call__(
        self,
        lineage_payload: dict[str, object],
        *,
        runtime: LlmRuntimeConfig,
    ) -> dict[str, object]: ...


def create_app(
    *,
    project_root: Path | None = None,
    static_dir: Path | None = None,
    bw_client_factory: BwClientFactory | None = None,
) -> FastAPI:
    """Create the local read-only API and optional static frontend server."""

    root = (project_root or Path.cwd()).resolve()
    runtime_env = _merged_runtime_env(root)
    app = FastAPI(
        title="BW Lineage Impact Local API",
        version=__version__,
        summary="Local-first read-only BW lineage and change-impact analyzer API.",
    )
    runtime_config = _initial_runtime_config(runtime_env)
    catalog_store = CatalogStore(catalog_path_for(root))

    @app.exception_handler(RequestValidationError)
    async def request_validation_exception_handler(
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"detail": _redact_validation_errors(exc)},
        )

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse()

    @app.get("/api/runtime-config", response_model=RuntimeConfigResponse)
    def get_runtime_config() -> RuntimeConfigResponse:
        return runtime_config.redacted()

    @app.put("/api/runtime-config", response_model=RuntimeConfigResponse)
    def put_runtime_config(request: RuntimeConfigRequest) -> RuntimeConfigResponse:
        try:
            _apply_runtime_request(runtime_config, request, root)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return runtime_config.redacted()

    @app.delete("/api/runtime-config", response_model=RuntimeConfigResponse)
    def clear_runtime_config() -> RuntimeConfigResponse:
        _clear_runtime_config(runtime_config)
        return runtime_config.redacted()

    @app.get("/api/v1/runtime-config", response_model=V1RuntimeConfigResponse)
    def get_runtime_config_v1() -> V1RuntimeConfigResponse:
        return runtime_config.redacted_v1()

    @app.put("/api/v1/runtime-config", response_model=V1RuntimeConfigResponse)
    def put_runtime_config_v1(request: RuntimeConfigRequest) -> V1RuntimeConfigResponse:
        try:
            _apply_runtime_request(runtime_config, request, root)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return runtime_config.redacted_v1()

    @app.delete("/api/v1/runtime-config", response_model=V1RuntimeConfigResponse)
    def clear_runtime_config_v1() -> V1RuntimeConfigResponse:
        # Re-read the project .env so the fallback reflects values persisted
        # by a later "설정 저장" instead of the snapshot taken at startup.
        _reset_runtime_config(runtime_config, _merged_runtime_env(root))
        return runtime_config.redacted_v1()

    @app.post("/api/v1/connection/test", response_model=LiveSmokeResult)
    def connection_test_v1(request: V1ConnectionTestRequest) -> LiveSmokeResult:
        state = _ensure_live_ready(runtime_config, request.confirm_read_only)
        try:
            result = run_live_smoke(
                client_factory=lambda: _build_runtime_bw_client(state, bw_client_factory),
                search_term=request.search_term,
                secret_values=_runtime_secret_values(state),
                secret_urls=_runtime_secret_urls(state),
            )
            runtime_config.connection_status = "failed" if result.status == "error" else "ok"
            return result
        except Exception as exc:
            runtime_config.connection_status = "failed"
            raise _live_http_error(exc, state=state) from exc

    @app.post("/api/v1/snapshots/capture")
    def capture_snapshot_v1(request: V1SnapshotCaptureRequest) -> dict[str, object]:
        live_state = runtime_config.bw if runtime_config.bw.configured else None
        try:
            snapshot, capture_meta, capture_scope = _capture_v1_snapshot(
                root=root,
                store=catalog_store,
                runtime_config=runtime_config,
                request=request,
                bw_client_factory=bw_client_factory,
            )
        except Exception as exc:
            raise _live_http_error(exc, state=live_state) from exc
        payload = snapshot.model_dump(mode="json")
        if capture_meta is not None:
            payload["capture"] = capture_meta
        payload["capture_scope"] = [entry.model_dump(mode="json") for entry in capture_scope]
        return payload

    @app.get("/api/v1/repository", response_model=V1RepositoryResponse)
    def repository_v1(
        path: str | None = None,
        refresh: bool = False,
        confirm_read_only: bool = False,
    ) -> V1RepositoryResponse:
        try:
            return _repository_payload(
                store=catalog_store,
                runtime_config=runtime_config,
                path=path,
                refresh=refresh,
                confirm_read_only=confirm_read_only,
                bw_client_factory=bw_client_factory,
            )
        except Exception as exc:
            raise _live_http_error(exc, state=runtime_config.bw) from exc

    @app.get("/api/v1/snapshots", response_model=V1SnapshotListResponse)
    def list_snapshots_v1() -> V1SnapshotListResponse:
        return V1SnapshotListResponse(
            snapshots=[
                snapshot.model_dump(mode="json") for snapshot in catalog_store.list_snapshots()
            ]
        )

    @app.get("/api/v1/snapshots/{snapshot_id}")
    def get_snapshot_v1(snapshot_id: str) -> dict[str, object]:
        snapshot = catalog_store.get_snapshot(snapshot_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail=f"snapshot not found: {snapshot_id}")
        payload = snapshot.model_dump(mode="json")
        payload["capture_scope"] = [
            entry.model_dump(mode="json") for entry in catalog_store.list_capture_scope(snapshot_id)
        ]
        return payload

    @app.get(
        "/api/v1/snapshots/{snapshot_id}/capture-scope",
        response_model=V1CaptureScopeResponse,
    )
    def get_snapshot_capture_scope_v1(snapshot_id: str) -> V1CaptureScopeResponse:
        if catalog_store.get_snapshot(snapshot_id) is None:
            raise HTTPException(status_code=404, detail=f"snapshot not found: {snapshot_id}")
        return V1CaptureScopeResponse(
            snapshot_id=snapshot_id,
            items=[
                entry.model_dump(mode="json")
                for entry in catalog_store.list_capture_scope(snapshot_id)
            ],
        )

    @app.get("/api/v1/snapshots/{snapshot_id}/glossary", response_model=V1GlossaryResponse)
    def glossary_v1(
        snapshot_id: str,
        query: str | None = None,
        limit: int = 50,
    ) -> V1GlossaryResponse:
        if catalog_store.get_snapshot(snapshot_id) is None:
            raise HTTPException(status_code=404, detail=f"snapshot not found: {snapshot_id}")
        terms = catalog_store.list_glossary_terms(snapshot_id, query=query, limit=limit)
        return V1GlossaryResponse(
            snapshot_id=snapshot_id,
            query=query,
            count=len(terms),
            items=[term.model_dump(mode="json") for term in terms],
        )

    @app.get("/api/v1/snapshots/{snapshot_id}/objects", response_model=V1ObjectListResponse)
    def list_snapshot_objects_v1(
        snapshot_id: str,
        q: str | None = None,
        type: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> V1ObjectListResponse:
        if catalog_store.get_snapshot(snapshot_id) is None:
            raise HTTPException(status_code=404, detail=f"snapshot not found: {snapshot_id}")
        try:
            offset = _parse_cursor(cursor)
        except ValueError as exc:
            raise _http_error(exc) from exc
        items, next_cursor = catalog_store.list_objects(
            snapshot_id,
            q=q,
            object_type=type,
            limit=limit,
            cursor=offset,
        )
        return V1ObjectListResponse(
            items=[item.model_dump(mode="json") for item in items],
            next_cursor=str(next_cursor) if next_cursor is not None else None,
            limit=max(1, min(limit, 100)),
        )

    @app.get("/api/v1/snapshots/{snapshot_id}/objects/{object_id:path}")
    def get_snapshot_object_v1(snapshot_id: str, object_id: str) -> dict[str, object]:
        item = catalog_store.get_object(snapshot_id, object_id)
        if item is None:
            raise HTTPException(status_code=404, detail=f"object not found: {object_id}")
        return item.model_dump(mode="json")

    @app.post(
        "/api/v1/snapshots/{snapshot_id}/lineage",
        response_model=BoundedLineageResult,
    )
    def lineage_v1(snapshot_id: str, request: V1LineageRequest) -> BoundedLineageResult:
        try:
            return _run_v1_lineage(catalog_store, snapshot_id, request)
        except Exception as exc:
            raise _http_error(exc) from exc

    @app.post("/api/v1/snapshots/{snapshot_id}/lineage/advice")
    def lineage_advice_v1(snapshot_id: str, request: V1LineageRequest) -> dict[str, object]:
        try:
            lineage_payload = _run_v1_lineage(catalog_store, snapshot_id, request).model_dump(
                mode="json"
            )
            return _lineage_advice_payload(
                runtime_config=runtime_config,
                lineage_payload=lineage_payload,
            )
        except Exception as exc:
            raise _http_error(exc) from exc

    @app.post(
        "/api/v1/snapshots/{snapshot_id}/lineage/expand",
        response_model=BoundedLineageResult,
    )
    def lineage_expand_v1(
        snapshot_id: str,
        request: V1LineageExpandRequest,
    ) -> BoundedLineageResult:
        start_id = request.expand_object_id or request.object_id
        try:
            graph = catalog_store.load_graph(snapshot_id)
            return bounded_lineage(
                graph,
                snapshot_id=snapshot_id,
                start_id=start_id,
                direction=request.direction,
                depth=request.depth,
                node_cap=request.node_cap,
                edge_cap=request.edge_cap,
            )
        except Exception as exc:
            raise _http_error(exc) from exc

    @app.post("/api/v1/snapshots/{snapshot_id}/impact/scenario")
    def impact_scenario_v1(
        snapshot_id: str,
        request: V1ImpactScenarioRequest,
    ) -> dict[str, object]:
        try:
            return _run_v1_impact_scenario(catalog_store, snapshot_id, request)
        except Exception as exc:
            raise _http_error(exc) from exc

    @app.post("/api/v1/snapshots/{snapshot_id}/impact/advice")
    def impact_advice_v1(
        snapshot_id: str,
        request: V1ImpactScenarioRequest,
    ) -> dict[str, object]:
        try:
            impact_payload = _run_v1_impact_scenario(catalog_store, snapshot_id, request)
            return _impact_advice_payload(
                runtime_config=runtime_config,
                impact_payload=impact_payload,
            )
        except Exception as exc:
            raise _http_error(exc) from exc

    @app.post("/api/v1/snapshots/{snapshot_id}/sql/explain")
    def sql_explain_v1(snapshot_id: str, request: V1SqlExplainRequest) -> dict[str, object]:
        try:
            _ensure_snapshot_exists(catalog_store, snapshot_id)
            result = _parse_v1_sql(root, request)
            return _sql_explain_payload(
                result,
                output_format=request.format,
                store=catalog_store,
                snapshot_id=snapshot_id,
            )
        except Exception as exc:
            raise _http_error(exc) from exc

    @app.post("/api/v1/snapshots/{snapshot_id}/sql/draft")
    def sql_draft_v1(snapshot_id: str, request: V1SqlDraftRequest) -> dict[str, object]:
        try:
            _ensure_snapshot_exists(catalog_store, snapshot_id)
            return _sql_draft_payload(
                root=root,
                store=catalog_store,
                snapshot_id=snapshot_id,
                runtime_config=runtime_config,
                request=request,
            )
        except Exception as exc:
            raise _http_error(exc) from exc

    @app.post("/api/lineage", response_model=RenderedResponse)
    def lineage(request: LineageRequest) -> RenderedResponse:
        try:
            graph = load_graph(_resolve_local_path(root, request.graph_path))
            result = graph.traverse(
                request.object_id,
                direction=request.direction,
                max_depth=request.max_depth,
            )
            content = render_lineage(result, output_format=request.format)
        except Exception as exc:
            raise _http_error(exc) from exc
        return RenderedResponse(format=request.format, content=content)

    @app.post("/api/impact", response_model=RenderedResponse)
    def impact(request: ImpactRequest) -> RenderedResponse:
        try:
            graph = load_graph(_resolve_local_path(root, request.graph_path))
            changes = load_changes(_resolve_local_path(root, request.changes_path))
            report = run_impact_analysis(graph, changes, max_depth=request.max_depth)
            content = render_impact_report(
                report,
                output_format=request.format,
            )
        except Exception as exc:
            raise _http_error(exc) from exc
        return RenderedResponse(format=request.format, content=content)

    @app.post("/api/diff", response_model=RenderedResponse)
    def diff(request: DiffRequest) -> RenderedResponse:
        try:
            result = diff_graphs(
                load_graph(_resolve_local_path(root, request.before_path)),
                load_graph(_resolve_local_path(root, request.after_path)),
            )
            content = render_snapshot_diff(result)
        except Exception as exc:
            raise _http_error(exc) from exc
        return RenderedResponse(format="md", content=content)

    @app.post("/api/sql-view", response_model=RenderedResponse)
    def sql_view(request: SqlViewRequest) -> RenderedResponse:
        try:
            result = parse_native_sql_view(
                load_text(_resolve_local_path(root, request.sql_file)),
                view_id=request.view_id,
            )
            content = render_sql_view_evidence(
                result,
                output_format=request.format,
            )
        except Exception as exc:
            raise _http_error(exc) from exc
        return RenderedResponse(format=request.format, content=content)

    @app.post("/api/field-lineage", response_model=RenderedResponse)
    def field_lineage(request: FieldLineageRequest) -> RenderedResponse:
        try:
            document = parse_transformation_mapping_xml(
                load_text(_resolve_local_path(root, request.xml_file)),
                transformation_id=request.transformation_id,
                source_object_id=request.source_object,
                target_object_id=request.target_object,
            )
            content = render_field_lineage(
                document,
                output_format=request.format,
            )
        except Exception as exc:
            raise _http_error(exc) from exc
        return RenderedResponse(format=request.format, content=content)

    @app.post("/api/live/smoke", response_model=LiveSmokeResult)
    def live_smoke(request: LiveSmokeRequest) -> LiveSmokeResult:
        state = _ensure_live_ready(runtime_config, request.confirm_read_only)
        try:
            return run_live_smoke(
                client_factory=lambda: _build_runtime_bw_client(state, bw_client_factory),
                search_term=request.search_term,
                object_name=request.object_name,
                dataflow_object_type=request.object_type,
                dataflow_source_system=request.source_system,
                dataflow_direction=request.dataflow_direction,
                dataflow_levels=request.dataflow_levels,
                secret_values=_runtime_secret_values(state),
                secret_urls=_runtime_secret_urls(state),
            )
        except Exception as exc:
            raise _live_http_error(exc, state=state) from exc

    @app.post("/api/collect/live", response_model=LiveCollectionResponse)
    def live_collect(request: LiveCollectRequest) -> LiveCollectionResponse:
        state = _ensure_live_ready(runtime_config, request.confirm_read_only)
        try:
            out_dir = _resolve_local_output_dir(root, request.out_dir)
            result = collect_live_snapshot(
                out_dir=out_dir,
                client_factory=lambda: _build_runtime_bw_client(state, bw_client_factory),
                search_terms=request.search_terms,
                object_names=request.object_names,
                include_dataflow=request.include_dataflow,
                include_xref=request.include_xref,
                dataflow_object_type=request.object_type,
                dataflow_source_system=request.source_system,
                dataflow_direction=request.dataflow_direction,
                dataflow_levels=request.dataflow_levels,
                secret_values=_runtime_secret_values(state),
                secret_urls=_runtime_secret_urls(state),
            )
        except Exception as exc:
            raise _live_http_error(exc, state=state) from exc
        return LiveCollectionResponse(
            manifest_path=str(out_dir / "manifest.json"),
            manifest=result.manifest,
            operations=result.operations,
            succeeded=result.succeeded,
            failed=result.failed,
        )

    @app.post("/api/live/dataflow", response_model=RenderedResponse)
    def live_dataflow(request: LiveDataflowRequest) -> RenderedResponse:
        state = _ensure_live_ready(runtime_config, request.confirm_read_only)
        client = _build_runtime_bw_client(state, bw_client_factory)
        try:
            payload = client.fetch_dataflow(
                request.object_name,
                object_type=request.object_type,
                source_system=request.source_system,
                direction=request.direction,
                levels=request.levels,
            )
            if not isinstance(payload, str):
                raise ValueError("live dataflow response is not XML text")
            content = render_dataflow(payload, output_format=request.format)
        except Exception as exc:
            raise _live_http_error(exc, state=state) from exc
        finally:
            client.close()
        return RenderedResponse(format=request.format, content=content)

    frontend_dir = _frontend_static_dir(root, static_dir)
    if frontend_dir is not None:
        assets_dir = frontend_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(frontend_dir / "index.html")

    return app


def create_default_app() -> FastAPI:
    project_root = Path(os.environ.get("BWLI_PROJECT_ROOT", ".")).resolve()
    static_dir_value = os.environ.get("BWLI_STATIC_DIR")
    static_dir = Path(static_dir_value) if static_dir_value else None
    return create_app(project_root=project_root, static_dir=static_dir)


def _initial_runtime_config(env: Mapping[str, str]) -> RuntimeConfigState:
    bw = _env_bw_state(env)
    return RuntimeConfigState(
        connection_status="untested" if bw.configured else "unconfigured",
        bw=bw,
        llm=_env_llm_state(env),
    )


def _clear_runtime_config(state: RuntimeConfigState) -> None:
    state.storage = "process-memory"
    state.connection_status = "unconfigured"
    state.bw = RuntimeBwConfigState()
    state.llm = RuntimeLlmConfigState()


def _reset_runtime_config(state: RuntimeConfigState, env: Mapping[str, str]) -> None:
    state.storage = "process-memory"
    state.bw = _env_bw_state(env)
    state.connection_status = "untested" if state.bw.configured else "unconfigured"
    state.llm = _env_llm_state(env)


def _env_bw_state(env: Mapping[str, str]) -> RuntimeBwConfigState:
    required = ("BW_URL", "BW_USER", "BW_CLIENT")
    if any(not _has_text(env.get(name)) for name in required):
        return RuntimeBwConfigState()
    if not _has_secret_text(env.get("BW_PASSWORD")):
        return RuntimeBwConfigState()
    return RuntimeBwConfigState(
        source="env",
        configured=True,
        url=env["BW_URL"].strip(),
        user=env["BW_USER"].strip(),
        password=env["BW_PASSWORD"],
        client=env["BW_CLIENT"].strip(),
        language=env.get("BW_LANGUAGE", "EN").strip() or "EN",
        verify_ssl=_env_bool(env, "BW_VERIFY_SSL", default=True),
        ca_bundle=_optional_env(env, "BW_CA_BUNDLE"),
        trust_env=_env_bool(env, "BW_TRUST_ENV", default=True),
    )


def _env_llm_state(env: Mapping[str, str]) -> RuntimeLlmConfigState:
    required = ("BWLI_LLM_BASE_URL", "BWLI_LLM_MODEL", "BWLI_LLM_API_KEY")
    if any(not _has_text(env.get(name)) for name in required[:-1]):
        return RuntimeLlmConfigState()
    if not _has_secret_text(env.get("BWLI_LLM_API_KEY")):
        return RuntimeLlmConfigState()
    base_url = env["BWLI_LLM_BASE_URL"].strip()
    try:
        validate_local_llm_base_url(base_url)
    except ConfigError:
        return RuntimeLlmConfigState()
    return RuntimeLlmConfigState(
        source="env",
        enabled=True,
        configured=True,
        base_url=base_url,
        model=env["BWLI_LLM_MODEL"].strip(),
        api_key=env["BWLI_LLM_API_KEY"],
    )


def _apply_runtime_config(state: RuntimeConfigState, request: RuntimeConfigRequest) -> None:
    previous_bw = state.bw
    previous_connection_status = state.connection_status
    new_bw = state.bw
    new_llm = state.llm
    bw_requested = request.bw is not None

    if request.bw is not None:
        new_bw = _build_bw_state(request.bw, previous=state.bw)

    if request.llm is not None:
        new_llm = _build_llm_state(request.llm, previous=state.llm)

    state.bw = new_bw
    state.llm = new_llm
    if bw_requested:
        if not new_bw.configured:
            state.connection_status = "unconfigured"
        elif (
            previous_connection_status == "ok"
            and previous_bw.configured
            and _bw_materially_changed(previous_bw, new_bw)
        ):
            state.connection_status = "stale"
        elif previous_connection_status == "ok" and not _bw_materially_changed(
            previous_bw, new_bw
        ):
            state.connection_status = "ok"
        else:
            state.connection_status = "untested"


def _apply_runtime_request(
    state: RuntimeConfigState,
    request: RuntimeConfigRequest,
    root: Path,
) -> None:
    next_state = state.model_copy(deep=True)
    _apply_runtime_config(next_state, request)
    if not request.persist_to_env:
        next_state.storage = "process-memory"
        _replace_runtime_config_state(state, next_state)
        return
    try:
        _persist_runtime_env(
            root,
            next_state,
            include_bw=request.bw is not None,
            include_llm=request.llm is not None,
        )
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail="failed to write runtime config to project .env",
        ) from exc
    next_state.storage = "process-memory+project-env"
    _replace_runtime_config_state(state, next_state)


def _replace_runtime_config_state(
    state: RuntimeConfigState,
    next_state: RuntimeConfigState,
) -> None:
    state.storage = next_state.storage
    state.connection_status = next_state.connection_status
    state.bw = next_state.bw
    state.llm = next_state.llm


def _persist_runtime_env(
    root: Path,
    state: RuntimeConfigState,
    *,
    include_bw: bool,
    include_llm: bool,
) -> None:
    """Write the requested runtime config sections to the project-root .env.

    Secrets are persisted from the applied in-process state, so redaction
    markers submitted by the UI never reach disk. A ``None`` update removes
    the key from the file; unrelated keys and comments are preserved.
    """

    updates: dict[str, str | None] = {}
    if include_bw and state.bw.configured:
        updates.update(
            {
                "BW_URL": state.bw.url or "",
                "BW_USER": state.bw.user or "",
                "BW_PASSWORD": state.bw.password or "",
                "BW_CLIENT": state.bw.client or "",
                "BW_LANGUAGE": state.bw.language,
                "BW_VERIFY_SSL": "true" if state.bw.verify_ssl else "false",
                "BW_CA_BUNDLE": state.bw.ca_bundle,
                "BW_TRUST_ENV": "true" if state.bw.trust_env else "false",
            }
        )
    if include_llm:
        if state.llm.configured:
            updates.update(
                {
                    "BWLI_LLM_BASE_URL": state.llm.base_url or "",
                    "BWLI_LLM_MODEL": state.llm.model or "",
                    "BWLI_LLM_API_KEY": state.llm.api_key or "",
                }
            )
        else:
            updates.update(
                {
                    "BWLI_LLM_BASE_URL": None,
                    "BWLI_LLM_MODEL": None,
                    "BWLI_LLM_API_KEY": None,
                }
            )
    if not updates:
        return
    for key, value in updates.items():
        if value is not None and ("\n" in value or "\r" in value):
            raise ConfigError(f"{key} cannot contain line breaks when persisting to .env")
    _update_env_file(root / ".env", updates)


def _update_env_file(env_file: Path, updates: Mapping[str, str | None]) -> None:
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.is_file() else []
    pending = dict(updates)
    output: list[str] = []
    for raw_line in lines:
        key = _env_line_key(raw_line)
        if key is None or key not in updates:
            output.append(raw_line)
            continue
        if key in pending:
            value = pending.pop(key)
            if value is not None:
                output.append(f"{key}={_quote_env_value(value)}")
        # later duplicates of a managed key are dropped so one line wins
    additions = [(key, value) for key, value in pending.items() if value is not None]
    if not env_file.is_file() and not additions:
        return
    if additions:
        if output and output[-1].strip():
            output.append("")
        output.extend(f"{key}={_quote_env_value(value)}" for key, value in additions)
    rendered = "\n".join(output) + "\n"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{env_file.name}.",
        suffix=".tmp",
        dir=env_file.parent,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(rendered)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, env_file)
    except Exception:
        with suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise
    os.chmod(env_file, 0o600)


def _env_line_key(raw_line: str) -> str | None:
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    if line.startswith("export "):
        line = line.removeprefix("export ").strip()
    key, _, _ = line.partition("=")
    return key.strip() or None


def _quote_env_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _bw_materially_changed(
    previous: RuntimeBwConfigState,
    new: RuntimeBwConfigState,
) -> bool:
    return (
        previous.url,
        previous.user,
        previous.password,
        previous.client,
        previous.language,
        previous.verify_ssl,
        previous.ca_bundle,
        previous.trust_env,
    ) != (
        new.url,
        new.user,
        new.password,
        new.client,
        new.language,
        new.verify_ssl,
        new.ca_bundle,
        new.trust_env,
    )


def _build_bw_state(
    request: RuntimeBwConfigRequest,
    *,
    previous: RuntimeBwConfigState,
) -> RuntimeBwConfigState:
    missing = [
        name
        for name, value in {
            "url": request.url,
            "user": request.user,
            "client": request.client,
            "language": request.language,
        }.items()
        if not _has_text(value)
    ]
    previous_password = previous.password if previous.configured else None
    password = _coalesce_secret(request.password, previous_password)
    if password is None:
        missing.append("password")
    if missing:
        raise ConfigError(f"missing BW runtime config fields: {', '.join(missing)}")
    assert password is not None
    ca_bundle = (
        request.ca_bundle.strip()
        if request.ca_bundle and request.ca_bundle.strip()
        else None
    )
    return RuntimeBwConfigState(
        source="ui",
        configured=True,
        url=request.url.strip(),
        user=request.user.strip(),
        password=password,
        client=request.client.strip(),
        language=request.language.strip(),
        verify_ssl=request.verify_ssl,
        ca_bundle=ca_bundle,
        trust_env=request.trust_env,
    )


def _build_llm_state(
    request: RuntimeLlmConfigRequest,
    *,
    previous: RuntimeLlmConfigState,
) -> RuntimeLlmConfigState:
    if not request.enabled:
        return RuntimeLlmConfigState(enabled=False, configured=False)

    missing = [
        name
        for name, value in {
            "base_url": request.base_url,
            "model": request.model,
        }.items()
        if not _has_text(value)
    ]
    api_key = _coalesce_secret(request.api_key, previous.api_key if previous.configured else None)
    if api_key is None:
        missing.append("api_key")
    if missing:
        raise ConfigError(f"missing LLM runtime config fields: {', '.join(missing)}")
    assert request.base_url is not None
    assert request.model is not None
    assert api_key is not None
    base_url = request.base_url.strip()
    validate_local_llm_base_url(base_url)
    return RuntimeLlmConfigState(
        source="ui",
        enabled=True,
        configured=True,
        base_url=base_url,
        model=request.model.strip(),
        api_key=api_key,
    )


def _has_text(value: str | None) -> bool:
    return value is not None and bool(value.strip())


def _has_secret_text(value: str | None) -> bool:
    return _has_text(value) and not _is_redacted_marker(value)


def _coalesce_secret(candidate: str | None, previous: str | None) -> str | None:
    if _has_secret_text(candidate):
        assert candidate is not None
        return candidate.strip()
    if _has_secret_text(previous):
        assert previous is not None
        return previous
    return None


def _is_redacted_marker(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().upper() in {"[REDACTED]", "[REACTED]"}


def _merged_runtime_env(root: Path) -> dict[str, str]:
    env = _load_project_env_file(root)
    env.update(os.environ)
    return env


def _load_project_env_file(root: Path) -> dict[str, str]:
    env_file = root / ".env"
    if not env_file.exists() or not env_file.is_file():
        return {}
    return _parse_env_file(env_file)


def _parse_env_file(env_file: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not _is_supported_env_key(key):
            continue
        values[key] = _parse_env_value(raw_value)
    return values


def _is_supported_env_key(key: str) -> bool:
    return key.startswith("BW_") or key.startswith("BWLI_LLM_") or key in {"NO_PROXY", "no_proxy"}


def _parse_env_value(raw_value: str) -> str:
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        inner = value[1:-1]
        return _unescape_double_quoted(inner) if value[0] == '"' else inner
    return value


def _unescape_double_quoted(inner: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(inner):
        char = inner[index]
        if char == "\\" and index + 1 < len(inner) and inner[index + 1] in {'"', "\\"}:
            result.append(inner[index + 1])
            index += 2
        else:
            result.append(char)
            index += 1
    return "".join(result)


def _env_bool(env: Mapping[str, str], name: str, *, default: bool) -> bool:
    value = env.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _optional_env(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def _redact_validation_errors(exc: RequestValidationError) -> list[dict[str, object]]:
    return [
        {
            "type": error.get("type", "validation_error"),
            "loc": list(error.get("loc", [])),
            "msg": error.get("msg", "Invalid request"),
        }
        for error in exc.errors()
    ]


def _resolve_local_path(root: Path, user_path: str) -> Path:
    path = Path(user_path)
    root_resolved = root.resolve()
    resolved = path if path.is_absolute() else root_resolved / path
    resolved = resolved.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"local path must stay under project root: {user_path}") from exc
    if not resolved.exists():
        raise FileNotFoundError(f"local file not found: {user_path}")
    if not resolved.is_file():
        raise ValueError(f"local path is not a file: {user_path}")
    return resolved


def _ensure_live_ready(state: RuntimeConfigState, confirm_read_only: bool) -> RuntimeBwConfigState:
    if not state.bw.configured:
        raise HTTPException(status_code=400, detail="BW runtime config is not configured")
    if not confirm_read_only:
        raise HTTPException(
            status_code=400,
            detail="confirm_read_only=true is required before live BW calls",
        )
    return state.bw


def _repository_payload(
    *,
    store: CatalogStore,
    runtime_config: RuntimeConfigState,
    path: str | None,
    refresh: bool,
    confirm_read_only: bool,
    bw_client_factory: BwClientFactory | None,
) -> V1RepositoryResponse:
    normalized_path = normalize_repository_path(path)
    if refresh:
        state = _ensure_live_ready(runtime_config, confirm_read_only)
        if runtime_config.connection_status != "ok":
            raise ValueError("run a successful Test connection before repository refresh")
        client = _build_runtime_bw_client(state, bw_client_factory)
        try:
            payload = client.fetch_repository_contents(normalized_path)
        finally:
            client.close()
        if not isinstance(payload, str):
            raise ValueError("repository refresh response is not XML text")
        nodes = parse_repository_contents_xml(payload, parent_path=normalized_path)
        cached = store.replace_repository_nodes(parent_path=normalized_path, nodes=nodes)
        return V1RepositoryResponse(
            path=normalized_path,
            source="live",
            count=len(cached),
            items=[node.model_dump(mode="json") for node in cached],
        )

    cached = store.list_repository_nodes(parent_path=normalized_path)
    return V1RepositoryResponse(
        path=normalized_path,
        source="cache" if cached else "empty",
        count=len(cached),
        items=[node.model_dump(mode="json") for node in cached],
        action_required=(
            None
            if cached
            else "refresh=true with confirm_read_only=true after Test connection"
        ),
    )


def _build_runtime_bw_client(
    state: RuntimeBwConfigState,
    factory: BwClientFactory | None,
) -> BwReadClient:
    if factory is not None:
        return factory(state)
    if not state.url or not state.user or not state.password or not state.client:
        raise ConfigError("BW runtime config is incomplete")
    return BwClient(
        base_url=state.url,
        username=state.user,
        password=state.password,
        sap_client=state.client,
        language=state.language,
        verify=state.ca_bundle if state.verify_ssl and state.ca_bundle else state.verify_ssl,
        trust_env=state.trust_env,
    )


def _runtime_secret_values(state: RuntimeBwConfigState) -> list[str]:
    return [state.password] if state.password else []


def _runtime_secret_urls(state: RuntimeBwConfigState) -> list[str]:
    return [state.url] if state.url else []


def _resolve_local_output_dir(root: Path, user_path: str) -> Path:
    path = Path(user_path)
    resolved = path if path.is_absolute() else root / path
    resolved = resolved.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("live output path is outside project root") from exc
    return resolved


def _parse_cursor(cursor: str | None) -> int:
    if cursor is None or not cursor.strip():
        return 0
    try:
        value = int(cursor)
    except ValueError as exc:
        raise ValueError("cursor must be an integer offset") from exc
    if value < 0:
        raise ValueError("cursor must be >= 0")
    return value


def _capture_v1_snapshot(
    *,
    root: Path,
    store: CatalogStore,
    runtime_config: RuntimeConfigState,
    request: V1SnapshotCaptureRequest,
    bw_client_factory: BwClientFactory | None,
) -> tuple[CatalogSnapshotRecord, dict[str, object] | None, list[CaptureScopeRecord]]:
    if request.fixture_path:
        fixture_path = _resolve_local_path(root, request.fixture_path)
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        ingested = ingest_fixture_payload(payload, source=f"fixture://{fixture_path.name}")
        snapshot = store.create_snapshot(
            mode="offline-fixture",
            source=f"fixture://{fixture_path.name}",
        )
        record = _replace_catalog_or_delete_snapshot(
            store,
            snapshot.id,
            objects=ingested.objects,
            edges=ingested.edges,
        )
        scope = _replace_scope_or_delete_snapshot(
            store,
            snapshot.id,
            _discovered_scope_entries(ingested.objects),
        )
        return (record, None, scope)

    if request.manifest_path:
        manifest_path = _resolve_local_manifest_path(root, request.manifest_path)
        manifest, ingested = ingest_manifest(manifest_path)
        snapshot = store.create_snapshot(
            mode=manifest.mode,
            source=f"manifest://{manifest_path.name}",
            manifest_path=_project_relative_path(root, manifest_path),
        )
        record = _replace_catalog_or_delete_snapshot(
            store,
            snapshot.id,
            objects=ingested.objects,
            edges=ingested.edges,
        )
        scope = _replace_scope_or_delete_snapshot(
            store,
            snapshot.id,
            _discovered_scope_entries(ingested.objects),
        )
        return (record, None, scope)

    state = _ensure_live_ready(runtime_config, request.confirm_read_only)
    if runtime_config.connection_status != "ok":
        raise ValueError("run a successful Test connection before live capture")
    out_dir = _resolve_local_output_dir(root, _live_snapshot_output_dir())
    if not request.search_terms and not request.object_names:
        raise ValueError("live capture requires at least one search term or object name")
    result = collect_live_snapshot(
        out_dir=out_dir,
        client_factory=lambda: _build_runtime_bw_client(state, bw_client_factory),
        search_terms=request.search_terms,
        object_names=request.object_names,
        include_dataflow=request.include_dataflow,
        include_xref=request.include_xref,
        dataflow_object_type=request.object_type,
        dataflow_source_system=request.source_system,
        dataflow_direction=request.dataflow_direction,
        dataflow_levels=request.dataflow_levels,
        secret_values=_runtime_secret_values(state),
        secret_urls=_runtime_secret_urls(state),
    )
    _, ingested = ingest_manifest(out_dir / "manifest.json")
    snapshot = store.create_snapshot(
        mode=result.manifest.mode,
        source="live-read-only",
        manifest_path=_project_relative_path(root, out_dir / "manifest.json"),
    )
    record = _replace_catalog_or_delete_snapshot(
        store,
        snapshot.id,
        objects=ingested.objects,
        edges=ingested.edges,
    )
    capture_meta: dict[str, object] = {
        "mode": result.manifest.mode,
        "succeeded": result.succeeded,
        "failed": result.failed,
        "operations": [op.model_dump(mode="json") for op in result.operations],
    }
    scope_entries = [
        *_selected_scope_entries(
            request.object_names,
            object_type=request.object_type,
            include_dataflow=request.include_dataflow,
            include_xref=request.include_xref,
            operations=result.operations,
        ),
        *_discovered_scope_entries(ingested.objects),
    ]
    capture_scope = _replace_scope_or_delete_snapshot(store, snapshot.id, scope_entries)
    return record, capture_meta, capture_scope


def _replace_catalog_or_delete_snapshot(
    store: CatalogStore,
    snapshot_id: str,
    *,
    objects: Sequence[ObjectInput | CatalogObjectRecord],
    edges: Sequence[EdgeInput | CatalogEdgeRecord],
) -> CatalogSnapshotRecord:
    try:
        return store.replace_catalog(snapshot_id, objects=objects, edges=edges)
    except Exception:
        store.delete_snapshot(snapshot_id)
        raise


def _replace_scope_or_delete_snapshot(
    store: CatalogStore,
    snapshot_id: str,
    entries: Sequence[CaptureScopeRecord],
) -> list[CaptureScopeRecord]:
    try:
        return store.replace_capture_scope(snapshot_id, entries)
    except Exception:
        store.delete_snapshot(snapshot_id)
        raise


def _selected_scope_entries(
    object_names: Sequence[str],
    *,
    object_type: str,
    include_dataflow: bool,
    include_xref: bool,
    operations: Sequence[object],
) -> list[CaptureScopeRecord]:
    entries: list[CaptureScopeRecord] = []
    for object_name in _unique_texts(object_names):
        if include_dataflow:
            entries.append(
                _scope_entry_from_operations(
                    object_name,
                    object_type=object_type,
                    operation="bw_get_dataflow",
                    operations=operations,
                )
            )
        if include_xref:
            entries.append(
                _scope_entry_from_operations(
                    object_name,
                    object_type=object_type,
                    operation="bw_xref",
                    operations=operations,
                )
            )
    return entries


def _scope_entry_from_operations(
    object_name: str,
    *,
    object_type: str,
    operation: str,
    operations: Sequence[object],
) -> CaptureScopeRecord:
    matching = [
        op
        for op in operations
        if getattr(op, "name", "") == operation
        and f"objectName={object_name}" in getattr(op, "label", "")
    ]
    if not matching:
        return CaptureScopeRecord(
            object_id=object_name,
            object_type=object_type,
            role="selected",
            operation=operation,
            status="skipped",
        )
    op = matching[0]
    ok = bool(getattr(op, "ok", False))
    return CaptureScopeRecord(
        object_id=object_name,
        object_type=object_type,
        role="selected",
        operation=operation,
        status="ok" if ok else "error",
        error=None if ok else _persistable_error(getattr(op, "error", None)),
        metadata={
            key: value
            for key, value in {
                "label": getattr(op, "label", None),
                "payload_kind": getattr(op, "payload_kind", None),
                "item_count": getattr(op, "item_count", None),
            }.items()
            if value is not None
        },
    )


def _persistable_error(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text.strip():
        return None
    # User-visible live operation errors may contain already-redacted fragments
    # such as ``token=[REDACTED]``. The local catalog guard intentionally rejects
    # credential-shaped text, so collapse the credential key/value syntax before
    # persisting capture-scope metadata. The full redacted operation text still
    # remains in the immediate HTTP response.
    text = re.sub(
        r"(?i)(authorization|password|passwd|pwd|secret|token|api[_-]?key|credential)\s*[:=]\s*\[REDACTED\]",
        "[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)Bearer\s+\[REDACTED\]", "Bearer [REDACTED]", text)
    return text


def _discovered_scope_entries(
    objects: Sequence[CatalogObjectRecord],
) -> list[CaptureScopeRecord]:
    return [
        CaptureScopeRecord(
            object_id=item.id,
            object_type=item.type,
            role="discovered",
            operation="catalog_ingest",
            status="ok",
            evidence_ids=item.evidence_ids,
        )
        for item in objects
    ]


def _unique_texts(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = value.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _project_relative_path(root: Path, path: Path) -> str:
    root_resolved = root.resolve()
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(root_resolved))
    except ValueError as exc:
        raise ValueError(f"local snapshot path must stay under project root: {path}") from exc


def _resolve_local_manifest_path(root: Path, user_path: str) -> Path:
    path = Path(user_path)
    resolved = path if path.is_absolute() else root / path
    resolved = resolved.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"local manifest path must stay under project root: {user_path}") from exc
    manifest = resolved / "manifest.json" if resolved.is_dir() else resolved
    if not manifest.exists() or not manifest.is_file():
        raise FileNotFoundError(f"local manifest not found: {user_path}")
    return manifest


def _live_snapshot_output_dir() -> str:
    timestamp = datetime_safe_fragment()
    return f".bwli/snapshots/{timestamp}"


def datetime_safe_fragment() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace(":", "").replace("+", "Z").replace(".", "-")


def _ensure_snapshot_exists(store: CatalogStore, snapshot_id: str) -> None:
    if store.get_snapshot(snapshot_id) is None:
        raise FileNotFoundError(f"snapshot not found: {snapshot_id}")


def _run_v1_lineage(
    store: CatalogStore,
    snapshot_id: str,
    request: V1LineageRequest,
) -> BoundedLineageResult:
    assert_no_persisted_secrets(request.model_dump(mode="json"))
    graph = store.load_graph(snapshot_id)
    return bounded_lineage(
        graph,
        snapshot_id=snapshot_id,
        start_id=request.object_id,
        direction=request.direction,
        depth=request.depth,
        node_cap=request.node_cap,
        edge_cap=request.edge_cap,
    )


def _lineage_advice_payload(
    *,
    runtime_config: RuntimeConfigState,
    lineage_payload: dict[str, object],
) -> dict[str, object]:
    if not runtime_config.llm.enabled or not runtime_config.llm.configured:
        return {
            "schema_version": "1.0",
            "status": "disabled",
            "advisory": True,
            "config_required": True,
            "message": (
                "로컬 OpenAI-compatible LLM endpoint를 설정하면 Lineage advisory notes를 "
                "생성할 수 있습니다."
            ),
            "advice": "",
            "citations": [],
            "lineage": lineage_payload,
        }
    if (
        not runtime_config.llm.base_url
        or not runtime_config.llm.model
        or not runtime_config.llm.api_key
    ):
        raise ConfigError("LLM runtime config is incomplete")
    advice_result = _create_llm_lineage_advice(
        lineage_payload,
        runtime=LlmRuntimeConfig(
            base_url=runtime_config.llm.base_url,
            model=runtime_config.llm.model,
            api_key=SecretStr(runtime_config.llm.api_key),
        ),
    )
    advice = str(advice_result["advice"]).strip()
    if not advice:
        raise ValueError("LLM returned empty lineage advice")
    assert_no_persisted_secrets({"llm_advice": advice})
    raw_citations = advice_result.get("citations", [])
    citations = (
        [item for item in raw_citations if isinstance(item, str)]
        if isinstance(raw_citations, list)
        else []
    )
    return {
        "schema_version": "1.0",
        "status": "ok",
        "advisory": True,
        "config_required": False,
        "message": "Deterministic snapshot evidence 기반 Lineage advisory review를 생성했습니다.",
        "advice": advice,
        "citations": citations,
        "llm_audit": advice_result["llm_audit"],
        "lineage": lineage_payload,
    }


def _run_v1_impact_scenario(
    store: CatalogStore,
    snapshot_id: str,
    request: V1ImpactScenarioRequest,
) -> dict[str, object]:
    assert_no_persisted_secrets(request.model_dump(mode="json"))
    graph = store.load_graph(snapshot_id)
    nodes_by_id = graph.node_map()
    if request.object_id not in nodes_by_id:
        raise ValueError(f"start node not found: {request.object_id}")
    bounded = bounded_lineage(
        graph,
        snapshot_id=snapshot_id,
        start_id=request.object_id,
        direction=Direction.DOWNSTREAM,
        depth=request.depth,
        node_cap=request.node_cap,
        edge_cap=request.edge_cap,
    )
    source_node = nodes_by_id[request.object_id]
    change = ChangeEvent(
        id=f"scenario:{request.object_id}:{request.change_type.value}",
        object_id=request.object_id,
        object_type=source_node.type,
        change_type=request.change_type,
        field=request.field,
        metadata={
            key: value
            for key, value in {
                "value_description": request.value_description,
                "description": request.description,
            }.items()
            if value
        },
    )
    capped_graph = BwGraph.model_validate(
        {
            "nodes": [
                node.model_dump(mode="json", exclude={"evidence_ids"}) for node in bounded.nodes
            ],
            "edges": [
                edge.model_dump(mode="json", exclude={"evidence_ids"}) for edge in bounded.edges
            ],
        }
    )
    report = run_impact_analysis(
        capped_graph,
        ChangeSet(changes=[change]),
        max_depth=request.depth,
    )
    allowed_node_ids = {node.id for node in bounded.nodes}
    severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "UNKNOWN": 3}
    affected = []
    for finding in report.findings:
        if finding.impacted_object_id not in allowed_node_ids:
            continue
        evidence_ids = sorted({*finding.evidence_node_ids, *finding.evidence_edge_ids})
        glossary_terms = _glossary_terms_for_object(store, snapshot_id, finding.impacted_object_id)
        affected.append(
            {
                "object_id": finding.impacted_object_id,
                "object_type": finding.impacted_object_type,
                "severity": finding.severity.value,
                "confidence": finding.confidence,
                "reason": finding.reason,
                "evidence_ids": evidence_ids,
                "evidence_node_ids": finding.evidence_node_ids,
                "evidence_edge_ids": finding.evidence_edge_ids,
                "manual_verification": finding.manual_verification,
                "glossary_terms": [term.model_dump(mode="json") for term in glossary_terms],
            }
        )
    affected.sort(key=lambda item: (severity_order[str(item["severity"])], str(item["object_id"])))
    return {
        "schema_version": "1.0",
        "snapshot_id": snapshot_id,
        "deterministic": True,
        "advisory": False,
        "scenario": {
            "object_id": request.object_id,
            "object_type": source_node.type,
            "change_type": request.change_type.value,
            "field": request.field,
            "value_description": request.value_description,
            "description": request.description,
            "changes_path_required": False,
        },
        "affected_objects": affected,
        "lineage_bounds": {
            "depth": request.depth,
            "node_cap": request.node_cap,
            "edge_cap": request.edge_cap,
            "truncated": bounded.truncated,
            "truncation": bounded.truncation.model_dump(mode="json"),
            "omitted_neighbor_counts": bounded.omitted_neighbor_counts,
            "cycles_detected": bounded.cycles_detected,
        },
    }


def _impact_advice_payload(
    *,
    runtime_config: RuntimeConfigState,
    impact_payload: dict[str, object],
) -> dict[str, object]:
    if not runtime_config.llm.enabled or not runtime_config.llm.configured:
        return {
            "schema_version": "1.0",
            "status": "disabled",
            "advisory": True,
            "config_required": True,
            "message": (
                "로컬 OpenAI-compatible LLM endpoint를 설정하면 Impact advisory notes를 "
                "생성할 수 있습니다."
            ),
            "advice": "",
            "citations": [],
            "impact": impact_payload,
        }
    if (
        not runtime_config.llm.base_url
        or not runtime_config.llm.model
        or not runtime_config.llm.api_key
    ):
        raise ConfigError("LLM runtime config is incomplete")
    advice_result = _create_llm_impact_advice(
        impact_payload,
        runtime=LlmRuntimeConfig(
            base_url=runtime_config.llm.base_url,
            model=runtime_config.llm.model,
            api_key=SecretStr(runtime_config.llm.api_key),
        ),
    )
    advice = str(advice_result["advice"]).strip()
    if not advice:
        raise ValueError("LLM returned empty impact advice")
    assert_no_persisted_secrets({"llm_advice": advice})
    raw_citations = advice_result.get("citations", [])
    citations = (
        [item for item in raw_citations if isinstance(item, str)]
        if isinstance(raw_citations, list)
        else []
    )
    return {
        "schema_version": "1.0",
        "status": "ok",
        "advisory": True,
        "config_required": False,
        "message": "Deterministic snapshot evidence 기반 Impact advisory review를 생성했습니다.",
        "advice": advice,
        "citations": citations,
        "llm_audit": advice_result["llm_audit"],
        "impact": impact_payload,
    }


def _parse_v1_sql(root: Path, request: V1SqlExplainRequest | V1SqlDraftRequest) -> SqlParseResult:
    if request.sql_text and request.sql_file:
        raise ValueError("provide sql_text or sql_file, not both")
    if request.sql_text:
        sql_text = request.sql_text
    elif request.sql_file:
        sql_text = load_text(_resolve_local_path(root, request.sql_file))
    else:
        raise ValueError("sql_text or sql_file is required")
    view_id = request.view_id or "ADVISORY_SQL_VIEW"
    return parse_native_sql_view(sql_text, view_id=view_id)


def _sql_explain_payload(
    result: SqlParseResult,
    *,
    output_format: EvidenceFormat,
    store: CatalogStore,
    snapshot_id: str,
) -> dict[str, object]:
    rendered = render_sql_view_evidence(result, output_format=output_format)
    citations = _sql_citations(result)
    referenced_objects = _sql_referenced_objects(result)
    referenced_fields = _sql_referenced_fields(result)
    store.record_sql_analysis(
        snapshot_id,
        view_id=result.view.id,
        reference_object_ids=referenced_objects,
        column_names=[str(item["column_name"]) for item in referenced_fields],
        citation_ids=citations,
    )
    return {
        "schema_version": "1.0",
        "advisory": True,
        "execution_blocked": True,
        "execution_disabled_warning": (
            "SQL Analysis는 parse/reference extraction만 수행하며 "
            "database execution은 비활성입니다."
        ),
        "target": "native_sql_view",
        "format": output_format,
        "content": rendered,
        "result": result.model_dump(mode="json"),
        "citations": citations,
        "referenced_objects": referenced_objects,
        "referenced_fields": referenced_fields,
        "glossary_terms": [
            term.model_dump(mode="json")
            for term in store.list_glossary_terms(snapshot_id, query=None, limit=50)
            if term.source == "sql_evidence"
        ],
    }


def _sql_referenced_objects(result: SqlParseResult) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for edge in result.reference_edges:
        object_id = edge.source_object_id.strip()
        if not object_id or object_id in seen:
            continue
        seen.add(object_id)
        values.append(object_id)
    return values


def _sql_referenced_fields(result: SqlParseResult) -> list[dict[str, object]]:
    fields: list[dict[str, object]] = []
    for column in result.columns:
        fields.append(
            {
                "id": column.id,
                "table_alias": column.table_alias,
                "column_name": column.column_name,
                "expression": column.expression,
            }
        )
    return fields


def _glossary_terms_for_object(
    store: CatalogStore,
    snapshot_id: str,
    object_id: str,
) -> list[GlossaryTermRecord]:
    terms = store.list_glossary_terms(snapshot_id, query=None, object_id=object_id, limit=12)
    priority = {"name": 0, "label": 1, "description": 2, "technical_id": 3}
    return sorted(
        terms,
        key=lambda term: (
            priority.get(str(term.metadata.get("source_field", "")), 9),
            term.term.lower(),
            term.id,
        ),
    )


def _sql_draft_payload(
    *,
    root: Path,
    store: CatalogStore,
    snapshot_id: str,
    runtime_config: RuntimeConfigState,
    request: V1SqlDraftRequest,
) -> dict[str, object]:
    assert_no_persisted_secrets(request.model_dump(mode="json"))
    if not runtime_config.llm.enabled or not runtime_config.llm.configured:
        return {
            "schema_version": "1.0",
            "status": "disabled",
            "advisory": True,
            "execution_blocked": True,
            "config_required": True,
            "message": (
                "로컬 OpenAI-compatible LLM endpoint를 설정하면 advisory SQL draft를 "
                "생성할 수 있습니다."
            ),
            "target_dialect": request.target_dialect,
            "draft_sql": "",
            "citations": [],
        }
    if (
        not runtime_config.llm.base_url
        or not runtime_config.llm.model
        or not runtime_config.llm.api_key
    ):
        raise ConfigError("LLM runtime config is incomplete")
    result = _parse_v1_sql(root, request)
    draft_result = _create_llm_sql_draft(
        result,
        question=request.question,
        target_dialect=request.target_dialect,
        runtime=LlmRuntimeConfig(
            base_url=runtime_config.llm.base_url,
            model=runtime_config.llm.model,
            api_key=SecretStr(runtime_config.llm.api_key),
        ),
    )
    draft = str(draft_result["draft_sql"]).strip()
    if not draft:
        raise ValueError("LLM returned an empty SQL draft")
    raw_citations = draft_result.get("citations", [])
    citations = (
        [item for item in raw_citations if isinstance(item, str)]
        if isinstance(raw_citations, list)
        else []
    )
    assert_no_persisted_secrets({"draft_sql": draft})
    store.record_sql_draft(
        snapshot_id,
        question=request.question,
        target_dialect=request.target_dialect,
        draft_sql=draft,
        citation_ids=citations,
    )
    return {
        "schema_version": "1.0",
        "status": "ok",
        "advisory": True,
        "execution_blocked": True,
        "config_required": False,
        "target_dialect": request.target_dialect,
        "draft_sql": draft,
        "citations": citations,
        "llm_audit": draft_result["llm_audit"],
    }


def _sql_citations(result: SqlParseResult) -> list[str]:
    return [
        *[edge.id for edge in result.reference_edges],
        *[fragment.id for fragment in result.fragments],
        *[column.id for column in result.columns],
    ]


def _create_llm_sql_draft(
    result: SqlParseResult,
    *,
    question: str,
    target_dialect: str,
    runtime: LlmRuntimeConfig,
) -> dict[str, object]:
    module = import_module("bwli.llm.sql_assistant")
    create_sql_draft = cast(SqlDraftFactory, module.create_sql_draft)
    return create_sql_draft(
        result,
        question=question,
        target_dialect=target_dialect,
        runtime=runtime,
    )


def _create_llm_impact_advice(
    impact_payload: dict[str, object],
    *,
    runtime: LlmRuntimeConfig,
) -> dict[str, object]:
    module = import_module("bwli.llm.impact_advisor")
    create_impact_advice = cast(ImpactAdviceFactory, module.create_impact_advice)
    return create_impact_advice(impact_payload, runtime=runtime)


def _create_llm_lineage_advice(
    lineage_payload: dict[str, object],
    *,
    runtime: LlmRuntimeConfig,
) -> dict[str, object]:
    module = import_module("bwli.llm.lineage_advisor")
    create_lineage_advice = cast(LineageAdviceFactory, module.create_lineage_advice)
    return create_lineage_advice(lineage_payload, runtime=runtime)


def _frontend_static_dir(root: Path, static_dir: Path | None) -> Path | None:
    candidates = []
    if static_dir is not None:
        candidates.append(static_dir if static_dir.is_absolute() else root / static_dir)
    candidates.append(root / "web" / "dist")
    for candidate in candidates:
        index = candidate / "index.html"
        if index.exists() and index.is_file():
            return candidate.resolve()
    return None


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ConfigError | SecretPersistenceError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=type(exc).__name__)


def _live_http_error(
    exc: Exception,
    *,
    state: RuntimeBwConfigState | None,
) -> HTTPException:
    """Run _http_error then scrub BW password/URL/host from user-visible detail."""
    error = _http_error(exc)
    if isinstance(error.detail, str) and state is not None:
        scrubbed = redact_text(
            error.detail,
            secret_values=_runtime_secret_values(state),
            urls=_runtime_secret_urls(state),
        )
        if scrubbed != error.detail:
            error = HTTPException(status_code=error.status_code, detail=scrubbed)
    return error
