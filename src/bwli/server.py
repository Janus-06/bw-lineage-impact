from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from bwli import __version__
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

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse()

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
