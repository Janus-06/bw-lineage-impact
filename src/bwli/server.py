from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from bwli import __version__
from bwli.config import ConfigError, validate_local_llm_base_url
from bwli.field_lineage import (
    load_text,
    parse_native_sql_view,
    parse_transformation_mapping_xml,
    render_field_lineage,
    render_sql_view_evidence,
)
from bwli.graph import Direction
from bwli.impact import (
    diff_graphs,
    load_changes,
    render_impact_report,
    render_snapshot_diff,
    run_impact_analysis,
)
from bwli.lineage import load_graph, render_lineage

LineageFormat = Literal["json", "mermaid", "md"]
ImpactFormat = Literal["json", "md"]
EvidenceFormat = Literal["json", "md"]


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


class RuntimeBwConfigState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configured: bool = False
    url: str | None = None
    user: str | None = None
    password: str | None = None
    client: str | None = None
    language: str = "EN"
    verify_ssl: bool = True


class RuntimeLlmConfigState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    configured: bool = False
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None


class RuntimeConfigState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    storage: str = "process-memory"
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
            ),
            llm=RuntimeLlmConfigPublic(
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


def create_app(
    *,
    project_root: Path | None = None,
    static_dir: Path | None = None,
) -> FastAPI:
    """Create the local read-only API and optional static frontend server."""

    root = (project_root or Path.cwd()).resolve()
    app = FastAPI(
        title="BW Lineage Impact Local API",
        version=__version__,
        summary="Local-first read-only BW lineage and change-impact analyzer API.",
    )
    runtime_config = RuntimeConfigState()

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
            _apply_runtime_config(runtime_config, request)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return runtime_config.redacted()

    @app.delete("/api/runtime-config", response_model=RuntimeConfigResponse)
    def clear_runtime_config() -> RuntimeConfigResponse:
        runtime_config.bw = RuntimeBwConfigState()
        runtime_config.llm = RuntimeLlmConfigState()
        return runtime_config.redacted()

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


def _apply_runtime_config(state: RuntimeConfigState, request: RuntimeConfigRequest) -> None:
    new_bw = state.bw
    new_llm = state.llm

    if request.bw is not None:
        new_bw = _build_bw_state(request.bw, previous=state.bw)

    if request.llm is not None:
        new_llm = _build_llm_state(request.llm, previous=state.llm)

    state.bw = new_bw
    state.llm = new_llm


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
    return RuntimeBwConfigState(
        configured=True,
        url=request.url.strip(),
        user=request.user.strip(),
        password=password,
        client=request.client.strip(),
        language=request.language.strip(),
        verify_ssl=request.verify_ssl,
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
        enabled=True,
        configured=True,
        base_url=base_url,
        model=request.model.strip(),
        api_key=api_key,
    )


def _has_text(value: str | None) -> bool:
    return value is not None and bool(value.strip())


def _coalesce_secret(candidate: str | None, previous: str | None) -> str | None:
    if _has_text(candidate):
        assert candidate is not None
        return candidate
    if _has_text(previous):
        assert previous is not None
        return previous
    return None


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
    resolved = path if path.is_absolute() else root / path
    resolved = resolved.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"local file not found: {user_path}")
    if not resolved.is_file():
        raise ValueError(f"local path is not a file: {user_path}")
    return resolved


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
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=type(exc).__name__)
