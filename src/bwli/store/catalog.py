from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree as ET

from pydantic import BaseModel, ConfigDict, Field

from bwli.dataflow import parse_dataflow_xml
from bwli.graph import BwEdge, BwGraph, BwNode
from bwli.repository import RepositoryNodeRecord, normalize_repository_path
from bwli.snapshot import SnapshotManifest, SnapshotReader
from bwli.store.secret_guard import assert_no_persisted_secrets

JsonDict = dict[str, object]
ObjectInput = dict[str, object]
EdgeInput = dict[str, object]


class CatalogSnapshotRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    created_at: str
    mode: str
    source: str
    manifest_path: str | None = None
    object_count: int = 0
    edge_count: int = 0


class CatalogObjectRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str | None = None
    type: str = "UNKNOWN"
    label: str | None = None
    metadata: JsonDict = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)


class CatalogObjectDetail(CatalogObjectRecord):
    incoming_count: int = 0
    outgoing_count: int = 0
    glossary_terms: list[dict[str, object]] = Field(default_factory=list)


class CatalogEdgeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    target: str
    type: str = "depends_on"
    confidence: str = "unknown"
    metadata: JsonDict = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)


class CaptureScopeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_id: str
    object_type: str = "UNKNOWN"
    role: Literal["selected", "discovered"] = "discovered"
    operation: str = "catalog"
    status: Literal["selected", "ok", "error", "skipped"] = "ok"
    error: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    metadata: JsonDict = Field(default_factory=dict)


class GlossaryTermRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    term: str
    normalized_term: str
    source: str = "metadata"
    candidate: bool = True
    object_id: str | None = None
    object_type: str | None = None
    field_name: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    metadata: JsonDict = Field(default_factory=dict)


class IngestedCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objects: list[CatalogObjectRecord]
    edges: list[CatalogEdgeRecord]
    evidence_ids: list[str] = Field(default_factory=list)


class CatalogStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def create_snapshot(
        self,
        *,
        mode: str,
        source: str,
        manifest_path: str | None = None,
        snapshot_id: str | None = None,
    ) -> CatalogSnapshotRecord:
        created_at = datetime.now(UTC).isoformat()
        resolved_id = snapshot_id or _snapshot_id(source=source, created_at=created_at)
        assert_no_persisted_secrets(
            {"id": resolved_id, "mode": mode, "source": source, "manifest_path": manifest_path}
        )
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO snapshots(
                    id, created_at, mode, source, manifest_path, object_count, edge_count
                )
                VALUES (?, ?, ?, ?, ?, 0, 0)
                """,
                (resolved_id, created_at, mode, source, manifest_path),
            )
        return CatalogSnapshotRecord(
            id=resolved_id,
            created_at=created_at,
            mode=mode,
            source=source,
            manifest_path=manifest_path,
        )

    def replace_catalog(
        self,
        snapshot_id: str,
        *,
        objects: Sequence[ObjectInput | CatalogObjectRecord],
        edges: Sequence[EdgeInput | CatalogEdgeRecord],
    ) -> CatalogSnapshotRecord:
        object_records = [_coerce_object_record(item) for item in objects]
        edge_records = [_coerce_edge_record(item) for item in edges]
        assert_no_persisted_secrets(
            {
                "snapshot_id": snapshot_id,
                "objects": [item.model_dump(mode="json") for item in object_records],
                "edges": [item.model_dump(mode="json") for item in edge_records],
            }
        )
        with self._connect() as con:
            if self._get_snapshot(con, snapshot_id) is None:
                raise KeyError(f"snapshot not found: {snapshot_id}")
            con.execute("DELETE FROM objects WHERE snapshot_id = ?", (snapshot_id,))
            con.execute("DELETE FROM edges WHERE snapshot_id = ?", (snapshot_id,))
            con.executemany(
                """
                INSERT INTO objects(
                    snapshot_id, object_id, name, object_type, label, metadata_json,
                    evidence_ids_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        snapshot_id,
                        item.id,
                        item.name,
                        item.type,
                        item.label,
                        _json_dumps(item.metadata),
                        _json_dumps(item.evidence_ids),
                    )
                    for item in object_records
                ],
            )
            con.executemany(
                """
                INSERT INTO edges(
                    snapshot_id, edge_id, source_id, target_id, edge_type, confidence,
                    metadata_json, evidence_ids_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        snapshot_id,
                        item.id,
                        item.source,
                        item.target,
                        item.type,
                        item.confidence,
                        _json_dumps(item.metadata),
                        _json_dumps(item.evidence_ids),
                    )
                    for item in edge_records
                ],
            )
            con.execute(
                "UPDATE snapshots SET object_count = ?, edge_count = ? WHERE id = ?",
                (len(object_records), len(edge_records), snapshot_id),
            )
            _replace_metadata_glossary_terms(con, snapshot_id, object_records)
        snapshot = self.get_snapshot(snapshot_id)
        if snapshot is None:
            raise KeyError(f"snapshot not found after write: {snapshot_id}")
        return snapshot

    def list_snapshots(self) -> list[CatalogSnapshotRecord]:
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT id, created_at, mode, source, manifest_path, object_count, edge_count
                FROM snapshots
                ORDER BY created_at DESC, id DESC
                """
            ).fetchall()
        return [_snapshot_from_row(row) for row in rows]

    def get_snapshot(self, snapshot_id: str) -> CatalogSnapshotRecord | None:
        with self._connect() as con:
            row = self._get_snapshot(con, snapshot_id)
        return _snapshot_from_row(row) if row is not None else None

    def delete_snapshot(self, snapshot_id: str) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM snapshots WHERE id = ?", (snapshot_id,))

    def replace_repository_nodes(
        self,
        *,
        parent_path: str | None,
        nodes: Sequence[RepositoryNodeRecord],
    ) -> list[RepositoryNodeRecord]:
        normalized_parent = normalize_repository_path(parent_path)
        cached_at = datetime.now(UTC).isoformat()
        assert_no_persisted_secrets(
            {
                "parent_path": normalized_parent,
                "nodes": [node.model_dump(mode="json") for node in nodes],
            }
        )
        with self._connect() as con:
            con.execute("DELETE FROM repository_nodes WHERE parent_path = ?", (normalized_parent,))
            con.executemany(
                """
                INSERT INTO repository_nodes(
                    parent_path, node_id, path, name, description, object_type,
                    object_subtype, status, has_children, self_url, fiori_only,
                    children_path, metadata_json, cached_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        normalized_parent,
                        item.id,
                        item.path,
                        item.name,
                        item.description,
                        item.object_type,
                        item.object_subtype,
                        item.status,
                        1 if item.has_children else 0,
                        item.self_url,
                        1 if item.fiori_only else 0,
                        item.children_path,
                        _json_dumps(item.metadata),
                        cached_at,
                    )
                    for item in nodes
                ],
            )
        return self.list_repository_nodes(parent_path=normalized_parent)

    def list_repository_nodes(self, *, parent_path: str | None) -> list[RepositoryNodeRecord]:
        normalized_parent = normalize_repository_path(parent_path)
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT parent_path, node_id, path, name, description, object_type,
                    object_subtype, status, has_children, self_url, fiori_only,
                    children_path, metadata_json
                FROM repository_nodes
                WHERE parent_path = ?
                ORDER BY has_children DESC, object_type, name, path
                """,
                (normalized_parent,),
            ).fetchall()
        return [_repository_node_from_row(row) for row in rows]

    def replace_capture_scope(
        self,
        snapshot_id: str,
        entries: Sequence[CaptureScopeRecord],
    ) -> list[CaptureScopeRecord]:
        assert_no_persisted_secrets(
            {
                "snapshot_id": snapshot_id,
                "capture_scope": [entry.model_dump(mode="json") for entry in entries],
            }
        )
        with self._connect() as con:
            if self._get_snapshot(con, snapshot_id) is None:
                raise KeyError(f"snapshot not found: {snapshot_id}")
            con.execute("DELETE FROM capture_scope WHERE snapshot_id = ?", (snapshot_id,))
            con.executemany(
                """
                INSERT INTO capture_scope(
                    snapshot_id, object_id, object_type, role, operation, status, error,
                    evidence_ids_json, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        snapshot_id,
                        entry.object_id,
                        entry.object_type,
                        entry.role,
                        entry.operation,
                        entry.status,
                        entry.error,
                        _json_dumps(entry.evidence_ids),
                        _json_dumps(entry.metadata),
                    )
                    for entry in entries
                ],
            )
        return self.list_capture_scope(snapshot_id)

    def list_capture_scope(self, snapshot_id: str) -> list[CaptureScopeRecord]:
        with self._connect() as con:
            rows = con.execute(
                """
                SELECT object_id, object_type, role, operation, status, error,
                    evidence_ids_json, metadata_json
                FROM capture_scope
                WHERE snapshot_id = ?
                ORDER BY role DESC, object_id, operation
                """,
                (snapshot_id,),
            ).fetchall()
        return [_capture_scope_from_row(row) for row in rows]

    def list_objects(
        self,
        snapshot_id: str,
        *,
        q: str | None,
        object_type: str | None,
        limit: int,
        cursor: int,
    ) -> tuple[list[CatalogObjectRecord], int | None]:
        safe_limit = max(1, min(limit, 100))
        clauses = ["snapshot_id = ?"]
        params: list[object] = [snapshot_id]
        if q:
            clauses.append(
                "(lower(object_id) LIKE ? OR lower(coalesce(name, '')) LIKE ? "
                "OR lower(coalesce(label, '')) LIKE ?)"
            )
            pattern = f"%{q.lower()}%"
            params.extend([pattern, pattern, pattern])
        if object_type:
            clauses.append("upper(object_type) = upper(?)")
            params.append(object_type)
        where = " AND ".join(clauses)
        params.extend([safe_limit + 1, max(0, cursor)])
        with self._connect() as con:
            rows = con.execute(
                f"""
                SELECT object_id, name, object_type, label, metadata_json, evidence_ids_json
                FROM objects
                WHERE {where}
                ORDER BY object_id
                LIMIT ? OFFSET ?
                """,
                tuple(params),
            ).fetchall()
        records = [_object_from_row(row) for row in rows[:safe_limit]]
        next_cursor = cursor + safe_limit if len(rows) > safe_limit else None
        return records, next_cursor

    def get_object(self, snapshot_id: str, object_id: str) -> CatalogObjectDetail | None:
        with self._connect() as con:
            row = con.execute(
                """
                SELECT object_id, name, object_type, label, metadata_json, evidence_ids_json
                FROM objects
                WHERE snapshot_id = ? AND object_id = ?
                """,
                (snapshot_id, object_id),
            ).fetchone()
            if row is None:
                return None
            incoming = int(
                con.execute(
                    "SELECT count(*) FROM edges WHERE snapshot_id = ? AND target_id = ?",
                    (snapshot_id, object_id),
                ).fetchone()[0]
            )
            outgoing = int(
                con.execute(
                    "SELECT count(*) FROM edges WHERE snapshot_id = ? AND source_id = ?",
                    (snapshot_id, object_id),
                ).fetchone()[0]
            )
        base = _object_from_row(row)
        return CatalogObjectDetail(
            **base.model_dump(mode="json"),
            incoming_count=incoming,
            outgoing_count=outgoing,
            glossary_terms=[
                item.model_dump(mode="json")
                for item in self.list_glossary_terms(
                    snapshot_id,
                    query=None,
                    object_id=object_id,
                    limit=12,
                )
            ],
        )

    def list_glossary_terms(
        self,
        snapshot_id: str,
        *,
        query: str | None = None,
        object_id: str | None = None,
        limit: int = 50,
    ) -> list[GlossaryTermRecord]:
        safe_limit = max(1, min(limit, 100))
        clauses = ["snapshot_id = ?"]
        params: list[object] = [snapshot_id]
        if query and query.strip():
            pattern = f"%{_normalize_term(query)}%"
            clauses.append("(normalized_term LIKE ? OR lower(coalesce(object_id, '')) LIKE ?)")
            params.extend([pattern, f"%{query.strip().lower()}%"])
        if object_id:
            clauses.append("object_id = ?")
            params.append(object_id)
        params.append(safe_limit)
        with self._connect() as con:
            rows = con.execute(
                f"""
                SELECT term_id, term, normalized_term, source, candidate, object_id,
                    object_type, field_name, evidence_ids_json, metadata_json
                FROM glossary_terms
                WHERE {" AND ".join(clauses)}
                ORDER BY source, lower(term), object_id, field_name
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [_glossary_term_from_row(row) for row in rows]

    def load_graph(self, snapshot_id: str) -> BwGraph:
        if self.get_snapshot(snapshot_id) is None:
            raise KeyError(f"snapshot not found: {snapshot_id}")
        with self._connect() as con:
            object_rows = con.execute(
                """
                SELECT object_id, name, object_type, label, metadata_json, evidence_ids_json
                FROM objects WHERE snapshot_id = ? ORDER BY object_id
                """,
                (snapshot_id,),
            ).fetchall()
            edge_rows = con.execute(
                """
                SELECT edge_id, source_id, target_id, edge_type, confidence, metadata_json,
                    evidence_ids_json
                FROM edges WHERE snapshot_id = ? ORDER BY edge_id
                """,
                (snapshot_id,),
            ).fetchall()
        nodes = [_bw_node_from_record(_object_from_row(row)) for row in object_rows]
        edges = [_bw_edge_from_record(_edge_from_row(row)) for row in edge_rows]
        return BwGraph(nodes=nodes, edges=edges)

    def record_sql_draft(
        self,
        snapshot_id: str,
        *,
        question: str,
        target_dialect: str,
        draft_sql: str,
        citation_ids: list[str],
    ) -> None:
        assert_no_persisted_secrets(
            {
                "snapshot_id": snapshot_id,
                "question": question,
                "target_dialect": target_dialect,
                "draft_sql": draft_sql,
                "citation_ids": citation_ids,
            }
        )
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO sql_drafts(
                    snapshot_id, created_at, question, target_dialect, draft_sql,
                    citation_ids_json
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    datetime.now(UTC).isoformat(),
                    question,
                    target_dialect,
                    draft_sql,
                    _json_dumps(citation_ids),
                ),
            )

    def record_sql_analysis(
        self,
        snapshot_id: str,
        *,
        view_id: str,
        reference_object_ids: Sequence[str],
        column_names: Sequence[str],
        citation_ids: Sequence[str],
    ) -> None:
        metadata: JsonDict = {
            "view_id": view_id,
            "reference_object_ids": sorted(set(reference_object_ids)),
            "column_names": sorted(set(column_names)),
            "citation_ids": list(citation_ids),
        }
        assert_no_persisted_secrets({"snapshot_id": snapshot_id, "sql_analysis": metadata})
        now = datetime.now(UTC).isoformat()
        with self._connect() as con:
            if self._get_snapshot(con, snapshot_id) is None:
                raise KeyError(f"snapshot not found: {snapshot_id}")
            con.execute(
                """
                INSERT INTO analysis_runs(snapshot_id, created_at, kind, metadata_json)
                VALUES (?, ?, ?, ?)
                """,
                (snapshot_id, now, "sql_explain", _json_dumps(metadata)),
            )
            _insert_glossary_terms(
                con,
                snapshot_id,
                _sql_glossary_terms(
                    view_id=view_id,
                    reference_object_ids=reference_object_ids,
                    column_names=column_names,
                    citation_ids=citation_ids,
                ),
            )

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    source TEXT NOT NULL,
                    manifest_path TEXT,
                    object_count INTEGER NOT NULL DEFAULT 0,
                    edge_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS objects (
                    snapshot_id TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    name TEXT,
                    object_type TEXT NOT NULL,
                    label TEXT,
                    metadata_json TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    PRIMARY KEY (snapshot_id, object_id),
                    FOREIGN KEY(snapshot_id) REFERENCES snapshots(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_objects_type ON objects(snapshot_id, object_type);
                CREATE TABLE IF NOT EXISTS edges (
                    snapshot_id TEXT NOT NULL,
                    edge_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    edge_type TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    PRIMARY KEY (snapshot_id, edge_id),
                    FOREIGN KEY(snapshot_id) REFERENCES snapshots(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(snapshot_id, source_id);
                CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(snapshot_id, target_id);
                CREATE TABLE IF NOT EXISTS repository_nodes (
                    parent_path TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    object_type TEXT NOT NULL,
                    object_subtype TEXT,
                    status TEXT,
                    has_children INTEGER NOT NULL,
                    self_url TEXT,
                    fiori_only INTEGER NOT NULL,
                    children_path TEXT,
                    metadata_json TEXT NOT NULL,
                    cached_at TEXT NOT NULL,
                    PRIMARY KEY(parent_path, node_id)
                );
                CREATE INDEX IF NOT EXISTS idx_repository_nodes_parent
                    ON repository_nodes(parent_path);
                CREATE TABLE IF NOT EXISTS capture_scope (
                    snapshot_id TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    object_type TEXT NOT NULL,
                    role TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    evidence_ids_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    PRIMARY KEY(snapshot_id, object_id, role, operation),
                    FOREIGN KEY(snapshot_id) REFERENCES snapshots(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS glossary_terms (
                    snapshot_id TEXT NOT NULL,
                    term_id TEXT NOT NULL,
                    term TEXT NOT NULL,
                    normalized_term TEXT NOT NULL,
                    source TEXT NOT NULL,
                    candidate INTEGER NOT NULL,
                    object_id TEXT,
                    object_type TEXT,
                    field_name TEXT,
                    evidence_ids_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    PRIMARY KEY(snapshot_id, term_id),
                    FOREIGN KEY(snapshot_id) REFERENCES snapshots(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_glossary_terms_query
                    ON glossary_terms(snapshot_id, normalized_term);
                CREATE INDEX IF NOT EXISTS idx_glossary_terms_object
                    ON glossary_terms(snapshot_id, object_id);
                CREATE TABLE IF NOT EXISTS evidence (
                    snapshot_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    source TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    PRIMARY KEY (snapshot_id, evidence_id),
                    FOREIGN KEY(snapshot_id) REFERENCES snapshots(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS analysis_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    FOREIGN KEY(snapshot_id) REFERENCES snapshots(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS sql_drafts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    question TEXT NOT NULL,
                    target_dialect TEXT NOT NULL,
                    draft_sql TEXT NOT NULL,
                    citation_ids_json TEXT NOT NULL,
                    FOREIGN KEY(snapshot_id) REFERENCES snapshots(id) ON DELETE CASCADE
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        return con

    @staticmethod
    def _get_snapshot(con: sqlite3.Connection, snapshot_id: str) -> sqlite3.Row | None:
        row = con.execute(
            """
            SELECT id, created_at, mode, source, manifest_path, object_count, edge_count
            FROM snapshots WHERE id = ?
            """,
            (snapshot_id,),
        ).fetchone()
        return cast(sqlite3.Row | None, row)


def catalog_path_for(project_root: Path) -> Path:
    home = os.environ.get("BWLI_HOME")
    if home and home.strip():
        return Path(home).expanduser().resolve() / "catalog.sqlite"
    return project_root.resolve() / ".bwli" / "catalog.sqlite"


def ingest_fixture_payload(payload: object, *, source: str) -> IngestedCatalog:
    return _ingest_payload(
        payload,
        payload_id=_safe_id(Path(source).stem or "fixture"),
        source=source,
    )


def ingest_manifest(manifest_path: Path) -> tuple[SnapshotManifest, IngestedCatalog]:
    manifest_file = manifest_path / "manifest.json" if manifest_path.is_dir() else manifest_path
    reader = SnapshotReader(manifest_file.parent)
    if manifest_file.name == "manifest.json":
        manifest = reader.read_manifest()
    else:
        manifest = SnapshotManifest.model_validate_json(manifest_file.read_text(encoding="utf-8"))
    combined = _MutableCatalog()
    for metadata in manifest.payloads:
        payload = reader.read_payload(metadata)
        ingested = _ingest_payload(
            payload,
            payload_id=metadata.payload_id,
            source=metadata.source,
            kind=metadata.kind,
        )
        combined.merge(ingested)
    return manifest, combined.to_catalog()


def parse_search_results(payload: object, *, source: str) -> list[CatalogObjectRecord]:
    """Parse a live bw_search payload (JSON dict/list or XML text) into object records.

    Reuses the deterministic snapshot-ingest parsing so live search results and
    captured search payloads stay consistent. Unlike persisted catalog listings,
    live search responses preserve the BW/result payload order so the picker does
    not reshuffle what the user just searched for.
    """
    records = _ingest_payload(
        payload,
        payload_id="bw-search",
        source=source,
        kind="bw_search",
    ).objects
    return sorted(records, key=_search_result_order)


def _search_result_order(record: CatalogObjectRecord) -> int:
    for evidence_id in record.evidence_ids:
        marker = ":search:"
        if marker not in evidence_id:
            continue
        suffix = evidence_id.rsplit(marker, 1)[-1]
        if suffix.isdigit():
            return int(suffix)
    return 0


def _ingest_payload(
    payload: object,
    *,
    payload_id: str,
    source: str,
    kind: str | None = None,
) -> IngestedCatalog:
    kind_value = _payload_kind(payload, explicit_kind=kind)
    if isinstance(payload, dict) and isinstance(payload.get("nodes"), list) and isinstance(
        payload.get("edges"), list
    ):
        return _ingest_graph_payload(payload, payload_id=payload_id)
    if kind_value in {"bw_list_requests", "list_requests", "request_list"}:
        if isinstance(payload, dict | list):
            return _ingest_request_list_payload(
                payload,
                payload_id=payload_id,
                source=source,
            )
        return IngestedCatalog(objects=[], edges=[], evidence_ids=[])
    if kind_value in {"bw_get_request", "get_request", "request_detail"}:
        if isinstance(payload, dict):
            return _ingest_request_detail_payload(
                payload,
                payload_id=payload_id,
                source=source,
            )
        return IngestedCatalog(objects=[], edges=[], evidence_ids=[])
    if kind_value in {"bw_get_process_chain", "process_chain", "processchain"}:
        if isinstance(payload, dict):
            return _ingest_process_chain_payload(payload, payload_id=payload_id, source=source)
        return IngestedCatalog(objects=[], edges=[], evidence_ids=[])
    if kind_value in {"bw_get_process_variant", "process_variant", "processvariant"}:
        if isinstance(payload, dict):
            return _ingest_process_variant_payload(payload, payload_id=payload_id, source=source)
        return IngestedCatalog(objects=[], edges=[], evidence_ids=[])
    if kind_value in {"bw_get_dtp", "dtp", "dtpa"}:
        if isinstance(payload, str):
            return _ingest_dtp_xml(payload, payload_id=payload_id)
        return IngestedCatalog(objects=[], edges=[], evidence_ids=[])
    if kind_value in {"bw_get_datasource", "datasource", "rsds"}:
        if isinstance(payload, str):
            return _ingest_datasource_xml(payload, payload_id=payload_id)
        return IngestedCatalog(objects=[], edges=[], evidence_ids=[])
    if kind_value in {"bw_get_source_system", "source_system", "sourcesystem", "lsys"}:
        if isinstance(payload, str):
            return _ingest_source_system_xml(payload, payload_id=payload_id)
        return IngestedCatalog(objects=[], edges=[], evidence_ids=[])
    if kind_value in {"bw_get_query", "query"}:
        if isinstance(payload, str):
            return _ingest_query_xml(payload, payload_id=payload_id, source=source)
        return IngestedCatalog(objects=[], edges=[], evidence_ids=[])
    if kind_value in {"bw_get_composite_provider", "composite_provider", "hcpr"}:
        if isinstance(payload, str):
            return _ingest_composite_provider_xml(payload, payload_id=payload_id)
        return IngestedCatalog(objects=[], edges=[], evidence_ids=[])
    if kind_value in {"bw_search", "bwsearch", "search"}:
        if isinstance(payload, dict | list):
            return _ingest_search_payload(payload, payload_id=payload_id, source=source)
        if isinstance(payload, str):
            return _ingest_search_xml(payload, payload_id=payload_id, source=source)
        return IngestedCatalog(objects=[], edges=[], evidence_ids=[])
    if kind_value in {"bw_xref", "xref"}:
        if isinstance(payload, dict):
            return _ingest_xref_payload(payload, payload_id=payload_id)
        if isinstance(payload, str):
            return _ingest_xref_xml(payload, payload_id=payload_id, source=source)
        return IngestedCatalog(objects=[], edges=[], evidence_ids=[])
    if kind_value in {"bw_get_dataflow", "bw_dataflow", "dataflow", "get_dataflow"}:
        if isinstance(payload, str):
            return _ingest_dataflow_xml(payload, payload_id=payload_id)
        return IngestedCatalog(objects=[], edges=[], evidence_ids=[])
    if isinstance(payload, str) and "<node" in payload:
        return _ingest_dataflow_xml(payload, payload_id=payload_id)
    if isinstance(payload, dict):
        if kind_value in {"bw_search", "bwsearch", "search"} or any(
            isinstance(payload.get(key), list) for key in ("objects", "results", "items")
        ):
            return _ingest_search_payload(payload, payload_id=payload_id, source=source)
        if kind_value in {"bw_xref", "xref"} or isinstance(payload.get("references"), list):
            return _ingest_xref_payload(payload, payload_id=payload_id)
    return IngestedCatalog(objects=[], edges=[], evidence_ids=[])


def _ingest_graph_payload(payload: dict[str, object], *, payload_id: str) -> IngestedCatalog:
    graph = BwGraph.from_payload(payload)
    objects = [
        CatalogObjectRecord(
            id=node.id,
            name=node.name,
            type=node.type,
            label=node.label,
            metadata=_safe_metadata(node.metadata),
            evidence_ids=[f"{payload_id}:node:{node.id}"],
        )
        for node in graph.nodes
    ]
    edges = [
        CatalogEdgeRecord(
            id=edge.id,
            source=edge.source,
            target=edge.target,
            type=edge.type,
            confidence=edge.confidence,
            metadata=_safe_metadata(edge.metadata),
            evidence_ids=[f"{payload_id}:edge:{edge.id}"],
        )
        for edge in graph.edges
    ]
    return IngestedCatalog(
        objects=objects,
        edges=edges,
        evidence_ids=[
            *[evidence_id for obj in objects for evidence_id in obj.evidence_ids],
            *[evidence_id for edge in edges for evidence_id in edge.evidence_ids],
        ],
    )


def _ingest_dataflow_xml(xml: str, *, payload_id: str) -> IngestedCatalog:
    graph = parse_dataflow_xml(xml)
    by_id = {node.id: node for node in graph.nodes}
    mutable = _MutableCatalog()
    for node in graph.nodes:
        mutable.add_object(
            CatalogObjectRecord(
                id=node.object_name or f"dataflow-node-{node.id}",
                name=node.object_description or node.object_name,
                type=node.object_type or "UNKNOWN",
                metadata={
                    "object_status": node.object_status,
                    "persistent": node.persistent,
                    "exists": node.exists,
                },
                evidence_ids=[f"{payload_id}:dataflow-node:{node.id}"],
            )
        )
    for source_id, target_id in graph.edges:
        source_node = by_id[source_id]
        target_node = by_id[target_id]
        source_object = source_node.object_name or f"dataflow-node-{source_id}"
        target_object = target_node.object_name or f"dataflow-node-{target_id}"
        edge_id = f"{payload_id}:dataflow:{source_object}->{target_object}"
        mutable.add_edge(
            CatalogEdgeRecord(
                id=edge_id,
                source=source_object,
                target=target_object,
                type="dataflow",
                confidence="direct",
                evidence_ids=[edge_id],
            )
        )
    return mutable.to_catalog()


def _ingest_process_chain_payload(
    payload: dict[str, object],
    *,
    payload_id: str,
    source: str,
) -> IngestedCatalog:
    header = payload.get("oHeader")
    header_fields = header if isinstance(header, dict) else {}
    chain_id = _first_text(
        header_fields,
        "sProcessChainId",
        "processChainId",
        "chainName",
        "name",
    ) or _source_query_value(source, "chainName")
    if chain_id is None:
        return IngestedCatalog(objects=[], edges=[], evidence_ids=[])
    chain_name = _first_text(header_fields, "sDescription", "description", "label")
    chain_evidence_id = f"{payload_id}:process-chain"
    mutable = _MutableCatalog()
    mutable.add_object(
        CatalogObjectRecord(
            id=chain_id,
            name=chain_name,
            type="RSPC",
            metadata={
                "object_status": _first_text(header_fields, "sObjectStatus", "objectStatus"),
                "object_version": _first_text(header_fields, "sObjectVersion", "objectVersion"),
            },
            evidence_ids=[chain_evidence_id],
        )
    )

    raw_nodes = payload.get("aNode")
    nodes = (
        [item for item in raw_nodes if isinstance(item, dict)]
        if isinstance(raw_nodes, list)
        else []
    )
    node_ids: list[str] = []
    for index, node in enumerate(nodes):
        variant_id = _first_text(
            node,
            "sProcessVariant",
            "processVariant",
            "variantName",
            "name",
        ) or f"{chain_id}:step:{index}"
        process_type = _first_text(
            node,
            "sProcessType",
            "processType",
            "type",
        ) or "PROCESS"
        evidence_id = f"{payload_id}:process-chain-node:{index}"
        node_ids.append(variant_id)
        mutable.add_object(
            CatalogObjectRecord(
                id=variant_id,
                name=_first_text(
                    node,
                    "sVariantDescription",
                    "sTypeDescription",
                    "description",
                    "label",
                ),
                type=process_type,
                metadata={
                    "chain_id": chain_id,
                    "step_index": index,
                    "status": _first_text(node, "sStatus", "status"),
                },
                evidence_ids=[evidence_id],
            )
        )
        contains_id = f"{payload_id}:process-chain-contains:{chain_id}->{variant_id}"
        mutable.add_edge(
            CatalogEdgeRecord(
                id=contains_id,
                source=chain_id,
                target=variant_id,
                type="contains",
                confidence="direct",
                metadata={"step_index": index},
                evidence_ids=[contains_id],
            )
        )

    raw_edges = payload.get("aEdge")
    edges = (
        [item for item in raw_edges if isinstance(item, dict)]
        if isinstance(raw_edges, list)
        else []
    )
    for edge in edges:
        source_index = _int_value(edge.get("iNodeIndexFrom"))
        target_index = _int_value(edge.get("iNodeIndexTo"))
        if source_index is None or target_index is None:
            continue
        if not (0 <= source_index < len(node_ids) and 0 <= target_index < len(node_ids)):
            continue
        source_id = node_ids[source_index]
        target_id = node_ids[target_index]
        edge_id = f"{payload_id}:process-chain-sequence:{source_index}->{target_index}"
        mutable.add_edge(
            CatalogEdgeRecord(
                id=edge_id,
                source=source_id,
                target=target_id,
                type="sequence",
                confidence="direct",
                metadata={
                    "chain_id": chain_id,
                    "status": _first_text(edge, "sStatus", "status"),
                    "strength": _first_text(edge, "sStrength", "strength"),
                },
                evidence_ids=[edge_id],
            )
        )
    return mutable.to_catalog()


def _ingest_process_variant_payload(
    payload: dict[str, object],
    *,
    payload_id: str,
    source: str,
) -> IngestedCatalog:
    variant_name = (
        _source_query_value(source, "variantName")
        or _first_text(payload, "sProcessVariant", "variantName", "name")
    )
    if variant_name is None:
        return IngestedCatalog(objects=[], edges=[], evidence_ids=[])
    process_type = (
        _source_query_value(source, "processType")
        or _first_text(payload, "sProcessType", "processType", "type")
        or "PROCESS"
    )
    evidence_id = f"{payload_id}:process-variant"
    metadata: JsonDict = {}
    active = payload.get("bActive")
    if isinstance(active, bool):
        metadata["active"] = active
    return IngestedCatalog(
        objects=[
            CatalogObjectRecord(
                id=variant_name,
                name=_first_text(payload, "sVariantDescription", "description", "label"),
                type=process_type,
                metadata=metadata,
                evidence_ids=[evidence_id],
            )
        ],
        edges=[],
        evidence_ids=[evidence_id],
    )


def _ingest_dtp_xml(xml: str, *, payload_id: str) -> IngestedCatalog:
    root = _xml_root(xml)
    if root is None:
        return IngestedCatalog(objects=[], edges=[], evidence_ids=[])
    fields = _xml_fields(root)
    source_fields = _first_xml_child_fields(root, "source")
    target_fields = _first_xml_child_fields(root, "target")
    overview_object_fields = _dtp_overview_object_fields(root)
    dtp_id = _first_text(fields, "name", "technicalName", "dtpName", "objectName")
    if dtp_id is None:
        return IngestedCatalog(objects=[], edges=[], evidence_ids=[])

    mutable = _MutableCatalog()
    dtp_evidence_id = f"{payload_id}:dtp"
    mutable.add_object(
        CatalogObjectRecord(
            id=dtp_id,
            name=_first_text(fields, "description", "label"),
            type="DTPA",
            metadata={
                "object_status": _first_text(fields, "objectStatus", "status"),
                "source_system": _first_text(fields, "sourceSystemName", "sourceSystem"),
            },
            evidence_ids=[dtp_evidence_id],
        )
    )

    source_id = _first_text(
        fields,
        "sourceObjectName",
        "sourceName",
        "source",
        "sourceObject",
    ) or _first_text(source_fields, "technicalName", "name", "objectName")
    source_type = _first_text(
        fields,
        "sourceObjectType",
        "sourceType",
        "sourceTlogo",
    ) or _first_text(source_fields, "objectType", "type", "tlogo") or "UNKNOWN"
    source_system = _first_text(fields, "sourceSystemName", "sourceSystem") or _first_text(
        source_fields, "sourceSystemName", "sourceSystem"
    )
    if source_id is not None:
        mutable.add_object(
            CatalogObjectRecord(
                id=source_id,
                type=source_type,
                metadata={"source_system": source_system},
                evidence_ids=[dtp_evidence_id],
            )
        )
        _add_edge(
            mutable,
            payload_id=payload_id,
            source=source_id,
            target=dtp_id,
            edge_type="dataflow",
            suffix="dtp-source",
        )

    target_id = _first_text(
        fields,
        "targetObjectName",
        "targetName",
        "target",
        "targetObject",
    ) or _first_text(target_fields, "technicalName", "name", "objectName")
    target_type = _first_text(
        fields,
        "targetObjectType",
        "targetType",
        "targetTlogo",
    ) or _first_text(target_fields, "objectType", "type", "tlogo") or "UNKNOWN"
    if target_id is not None:
        mutable.add_object(
            CatalogObjectRecord(
                id=target_id,
                type=target_type,
                evidence_ids=[dtp_evidence_id],
            )
        )
        _add_edge(
            mutable,
            payload_id=payload_id,
            source=dtp_id,
            target=target_id,
            edge_type="dataflow",
            suffix="dtp-target",
        )

    transformation_id = _first_text(
        fields,
        "transformationName",
        "transformation",
        "transformationObjectName",
    ) or _first_text(overview_object_fields, "technicalName", "name", "objectName")
    if transformation_id is not None:
        mutable.add_object(
            CatalogObjectRecord(
                id=transformation_id,
                name=_first_text(overview_object_fields, "description", "label"),
                type="TRFN",
                evidence_ids=[dtp_evidence_id],
            )
        )
        _add_edge(
            mutable,
            payload_id=payload_id,
            source=dtp_id,
            target=transformation_id,
            edge_type="uses_transformation",
            suffix="dtp-transformation",
        )
    return mutable.to_catalog()


def _ingest_datasource_xml(xml: str, *, payload_id: str) -> IngestedCatalog:
    root = _xml_root(xml)
    if root is None:
        return IngestedCatalog(objects=[], edges=[], evidence_ids=[])
    fields = _xml_fields(root)
    datasource_id = _first_text(fields, "name", "technicalName", "dataSourceName")
    if datasource_id is None:
        return IngestedCatalog(objects=[], edges=[], evidence_ids=[])
    field_metadata = _datasource_fields(root)
    evidence_id = f"{payload_id}:datasource"
    metadata: JsonDict = {
        "source_system": _first_text(fields, "sourceSystemName", "sourceSystem"),
        "datasource_type": _first_text(fields, "type", "dataSourceType"),
    }
    if field_metadata:
        metadata["fields"] = field_metadata
    return IngestedCatalog(
        objects=[
            CatalogObjectRecord(
                id=datasource_id,
                name=_first_text(fields, "description", "label"),
                type="RSDS",
                metadata=metadata,
                evidence_ids=[evidence_id],
            )
        ],
        edges=[],
        evidence_ids=[evidence_id],
    )


def _ingest_source_system_xml(xml: str, *, payload_id: str) -> IngestedCatalog:
    root = _xml_root(xml)
    if root is None:
        return IngestedCatalog(objects=[], edges=[], evidence_ids=[])
    fields = _xml_fields(root)
    source_system_id = _first_text(fields, "name", "technicalName", "sourceSystemName")
    if source_system_id is None:
        return IngestedCatalog(objects=[], edges=[], evidence_ids=[])
    evidence_id = f"{payload_id}:source-system"
    return IngestedCatalog(
        objects=[
            CatalogObjectRecord(
                id=source_system_id,
                name=_first_text(fields, "description", "label"),
                type="LSYS",
                metadata={"source_system_type": _first_text(fields, "type", "context")},
                evidence_ids=[evidence_id],
            )
        ],
        edges=[],
        evidence_ids=[evidence_id],
    )


def _ingest_query_xml(xml: str, *, payload_id: str, source: str) -> IngestedCatalog:
    root = _xml_root(xml)
    if root is None:
        return IngestedCatalog(objects=[], edges=[], evidence_ids=[])
    fields = _xml_fields(root)
    query_id = (
        _first_text(fields, "technicalName", "name", "queryName")
        or _source_query_value(source, "queryName")
    )
    if query_id is None:
        return IngestedCatalog(objects=[], edges=[], evidence_ids=[])
    evidence_id = f"{payload_id}:query"
    mutable = _MutableCatalog()
    mutable.add_object(
        CatalogObjectRecord(
            id=query_id,
            name=_first_text(fields, "description", "label", "text"),
            type="QUERY",
            evidence_ids=[evidence_id],
        )
    )
    for index, (provider_id, provider_type) in enumerate(
        _related_providers_from_links(root, current_object_id=query_id)
    ):
        provider_evidence_id = f"{payload_id}:query-provider:{index}"
        mutable.add_object(
            CatalogObjectRecord(
                id=provider_id,
                type=provider_type,
                evidence_ids=[provider_evidence_id],
            )
        )
        mutable.add_edge(
            CatalogEdgeRecord(
                id=provider_evidence_id,
                source=provider_id,
                target=query_id,
                type="provides",
                confidence="direct",
                evidence_ids=[provider_evidence_id],
            )
        )
    return mutable.to_catalog()


def _ingest_composite_provider_xml(xml: str, *, payload_id: str) -> IngestedCatalog:
    root = _xml_root(xml)
    if root is None:
        return IngestedCatalog(objects=[], edges=[], evidence_ids=[])
    fields = _xml_fields(root)
    hcpr_id = _first_text(fields, "technicalName", "name", "compositeProviderName")
    if hcpr_id is None:
        return IngestedCatalog(objects=[], edges=[], evidence_ids=[])
    evidence_id = f"{payload_id}:hcpr"
    mutable = _MutableCatalog()
    mutable.add_object(
        CatalogObjectRecord(
            id=hcpr_id,
            name=_first_text(fields, "description", "label"),
            type="HCPR",
            evidence_ids=[evidence_id],
        )
    )
    for index, provider in enumerate(_composite_input_providers(root)):
        provider_id = _first_text(provider, "technicalName", "name", "objectName")
        if provider_id is None:
            continue
        provider_type = _first_text(provider, "objectType", "type") or "UNKNOWN"
        provider_evidence_id = f"{payload_id}:hcpr-input:{index}"
        mutable.add_object(
            CatalogObjectRecord(
                id=provider_id,
                type=provider_type,
                evidence_ids=[provider_evidence_id],
            )
        )
        mutable.add_edge(
            CatalogEdgeRecord(
                id=provider_evidence_id,
                source=provider_id,
                target=hcpr_id,
                type="composite_input",
                confidence="direct",
                evidence_ids=[provider_evidence_id],
            )
        )
    return mutable.to_catalog()


def _ingest_request_list_payload(
    payload: dict[str, object] | list[object],
    *,
    payload_id: str,
    source: str,
) -> IngestedCatalog:
    raw_items = _request_payload_items(payload)
    requests = _sorted_request_records(
        [_request_record(item) for item in raw_items if isinstance(item, dict)]
    )
    target = _request_target(source, payload=payload, requests=requests)
    if target is None:
        return IngestedCatalog(objects=[], edges=[], evidence_ids=[])
    target_type = _request_target_type(source, payload=payload, requests=requests)
    evidence_id = f"{payload_id}:request-list"
    return IngestedCatalog(
        objects=[
            CatalogObjectRecord(
                id=target,
                type=target_type,
                metadata={
                    "request_freshness": _request_freshness_metadata(
                        target=target,
                        target_type=target_type,
                        requests=requests,
                    )
                },
                evidence_ids=[evidence_id],
            )
        ],
        edges=[],
        evidence_ids=[evidence_id],
    )


def _ingest_request_detail_payload(
    payload: dict[str, object],
    *,
    payload_id: str,
    source: str,
) -> IngestedCatalog:
    detail = _request_detail_payload(payload)
    request = _request_record(
        detail,
        request_tsn=_source_query_value(source, "requestTsn"),
        storage=_source_query_value(source, "storage"),
    )
    requests = _sorted_request_records([request])
    target = _request_target(source, payload=payload, requests=requests)
    if target is None:
        return IngestedCatalog(objects=[], edges=[], evidence_ids=[])
    target_type = _request_target_type(source, payload=payload, requests=requests)
    evidence_id = f"{payload_id}:request-detail"
    return IngestedCatalog(
        objects=[
            CatalogObjectRecord(
                id=target,
                type=target_type,
                metadata={
                    "request_freshness": _request_freshness_metadata(
                        target=target,
                        target_type=target_type,
                        requests=requests,
                    )
                },
                evidence_ids=[evidence_id],
            )
        ],
        edges=[],
        evidence_ids=[evidence_id],
    )


def _ingest_search_payload(
    payload: dict[str, object] | list[object],
    *,
    payload_id: str,
    source: str,
) -> IngestedCatalog:
    items: Sequence[object]
    if isinstance(payload, dict):
        items = _payload_items(payload, ("objects", "results", "items"))
    else:
        items = payload
    mutable = _MutableCatalog()
    for index, raw_item in enumerate(items, start=1):
        if not isinstance(raw_item, dict):
            continue
        item: dict[str, object] = dict(raw_item)
        object_id = _first_text(
            item,
            "id",
            "technicalName",
            "technical_name",
            "objectName",
            "object_name",
            "name",
        )
        if object_id is None:
            continue
        object_type = _first_text(item, "objectType", "object_type", "type") or "UNKNOWN"
        name = _first_text(item, "description", "label", "name")
        evidence_id = f"{payload_id}:search:{index}"
        mutable.add_object(
            CatalogObjectRecord(
                id=object_id,
                name=name,
                type=object_type,
                metadata={"source": source},
                evidence_ids=[evidence_id],
            )
        )
    return mutable.to_catalog()


def _ingest_search_xml(xml: str, *, payload_id: str, source: str) -> IngestedCatalog:
    objects: list[CatalogObjectRecord] = []
    seen: set[str] = set()
    for index, item in enumerate(_xml_items(xml), start=1):
        object_id = _first_text(
            item,
            "id",
            "technicalName",
            "technical_name",
            "objectName",
            "object_name",
            "name",
        )
        if object_id is None or object_id in seen:
            continue
        seen.add(object_id)
        object_type = _first_text(item, "objectType", "object_type", "type") or "UNKNOWN"
        name = _first_text(item, "description", "label", "name")
        evidence_id = f"{payload_id}:search:{index}"
        objects.append(
            CatalogObjectRecord(
                id=object_id,
                name=name,
                type=object_type,
                metadata={"source": source},
                evidence_ids=[evidence_id],
            )
        )
    return IngestedCatalog(
        objects=objects,
        edges=[],
        evidence_ids=[evidence_id for obj in objects for evidence_id in obj.evidence_ids],
    )


def _ingest_xref_payload(payload: dict[str, object], *, payload_id: str) -> IngestedCatalog:
    refs = _payload_items(payload, ("references", "results", "items"))
    mutable = _MutableCatalog()
    for index, ref in enumerate(refs, start=1):
        source = _first_text(ref, "from", "source", "sourceObject", "source_object", "source_id")
        target = _first_text(ref, "to", "target", "targetObject", "target_object", "target_id")
        if source is None or target is None:
            continue
        evidence_id = f"{payload_id}:xref:{index}"
        mutable.add_object(
            CatalogObjectRecord(id=source, type="UNKNOWN", evidence_ids=[evidence_id])
        )
        mutable.add_object(
            CatalogObjectRecord(id=target, type="UNKNOWN", evidence_ids=[evidence_id])
        )
        mutable.add_edge(
            CatalogEdgeRecord(
                id=evidence_id,
                source=source,
                target=target,
                type="xref",
                confidence="direct",
                evidence_ids=[evidence_id],
            )
        )
    return mutable.to_catalog()


def _ingest_xref_xml(xml: str, *, payload_id: str, source: str) -> IngestedCatalog:
    mutable = _MutableCatalog()
    requested_object, direction = _xref_context(payload_id=payload_id, source=source)
    for index, ref in enumerate(_xref_xml_items(xml), start=1):
        source_id = _first_text(ref, "from", "source", "sourceObject", "source_object", "source_id")
        target_id = _first_text(ref, "to", "target", "targetObject", "target_object", "target_id")
        source_type = "UNKNOWN"
        target_type = "UNKNOWN"
        source_name: str | None = None
        target_name: str | None = None
        if source_id is None or target_id is None:
            related_id = _first_text(
                ref,
                "objectName",
                "object_name",
                "technicalName",
                "technical_name",
                "id",
            )
            if related_id is not None and requested_object is not None:
                related_type = _first_text(ref, "objectType", "object_type", "type") or "UNKNOWN"
                related_name = _first_text(ref, "title", "description", "label", "name")
                if direction == "upstream":
                    source_id = related_id
                    target_id = requested_object
                    source_type = related_type
                    source_name = related_name
                else:
                    source_id = requested_object
                    target_id = related_id
                    target_type = related_type
                    target_name = related_name
        if source_id is None or target_id is None:
            continue
        evidence_id = f"{payload_id}:xref:{index}"
        mutable.add_object(
            CatalogObjectRecord(
                id=source_id,
                name=source_name,
                type=source_type,
                evidence_ids=[evidence_id],
            )
        )
        mutable.add_object(
            CatalogObjectRecord(
                id=target_id,
                name=target_name,
                type=target_type,
                evidence_ids=[evidence_id],
            )
        )
        mutable.add_edge(
            CatalogEdgeRecord(
                id=evidence_id,
                source=source_id,
                target=target_id,
                type="xref",
                confidence="direct",
                evidence_ids=[evidence_id],
            )
        )
    return mutable.to_catalog()


def _xref_context(*, payload_id: str, source: str) -> tuple[str | None, str]:
    direction = "downstream"
    parsed_source = urlparse(source)
    source_parts = parsed_source.path.rstrip("/").split("/")
    if source_parts and source_parts[-1] in {"upstream", "downstream"}:
        direction = source_parts[-1]

    query_object = parse_qs(parsed_source.query).get("objectName")
    requested_object = query_object[0] if query_object else None

    payload_parts = payload_id.split("-")
    if requested_object is None and len(payload_parts) >= 4 and payload_parts[0] == "xref":
        requested_object = _strip_safe_fragment_hash("-".join(payload_parts[2:-1])) or None
    if len(payload_parts) >= 4 and payload_parts[-1] in {"upstream", "downstream"}:
        direction = payload_parts[-1]
    return requested_object, direction


def _strip_safe_fragment_hash(value: str) -> str:
    stem, separator, suffix = value.rpartition("-")
    has_hash_suffix = len(suffix) == 12 and all(
        char in "0123456789abcdef" for char in suffix.lower()
    )
    if separator and has_hash_suffix:
        return stem
    return value


def _xref_xml_items(xml: str) -> list[dict[str, object]]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []

    refs: list[dict[str, object]] = []
    for entry in root.iter():
        if _local_xml_name(entry.tag) != "entry":
            continue
        entry_fields = _xml_fields(entry)
        object_fields = []
        for child in entry.iter():
            if child is entry:
                continue
            fields = _xml_fields(child)
            if _first_text(fields, "objectName", "object_name") is not None:
                object_fields.append(fields)
        if object_fields:
            refs.extend({**entry_fields, **fields} for fields in object_fields)
        elif entry_fields:
            refs.append(entry_fields)
    return refs or [_xml_fields(element) for element in root.iter() if _xml_fields(element)]


def _request_payload_items(payload: dict[str, object] | list[object]) -> list[dict[str, object]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    for key in ("requests", "results", "items", "objects"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _request_detail_payload(payload: dict[str, object]) -> dict[str, object]:
    header = payload.get("header")
    if isinstance(header, dict):
        return {key: value for key, value in header.items() if isinstance(key, str)}
    return payload


def _request_record(
    item: dict[str, object],
    *,
    request_tsn: str | None = None,
    storage: str | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {}
    resolved_request_tsn = request_tsn or _first_text(
        item,
        "requestTsn",
        "request_tsn",
        "request",
        "requestId",
    )
    external_tsn = _first_text(item, "requestTsnExternal", "request_tsn_external", "tsn")
    resolved_storage = storage or _first_text(item, "storage")
    status = _first_text(item, "requestStatus", "request_status", "status")
    last_process_status = _first_text(
        item,
        "lastProcessStatus",
        "last_process_status",
        "processStatus",
    )
    last_action = _first_text(item, "lastAction", "last_action", "action")
    records = _number_value(item.get("records")) or _number_value(item.get("recordCount"))
    timestamp = _first_text(
        item,
        "lastTimeStamp",
        "lastTimestamp",
        "timestamp",
        "createdAt",
        "requestStart",
        "requestFinish",
    )
    for key, value in (
        ("request_tsn", resolved_request_tsn),
        ("tsn", external_tsn),
        ("storage", resolved_storage),
        ("status", status),
        ("last_process_status", last_process_status),
        ("last_action", last_action),
        ("records", records),
        ("timestamp", timestamp),
    ):
        if value is not None:
            record[key] = value
    return record


def _request_freshness_metadata(
    *,
    target: str,
    target_type: str,
    requests: Sequence[dict[str, object]],
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "target": target,
        "target_type": target_type,
        "requests": list(requests),
    }
    if requests:
        metadata["latest"] = dict(requests[0])
    return metadata


def _request_target(
    source: str,
    *,
    payload: dict[str, object] | list[object],
    requests: Sequence[dict[str, object]],
) -> str | None:
    target = (
        _source_query_value(source, "objectName")
        or _source_query_value(source, "target")
        or _source_query_value(source, "datatarget")
    )
    if target is not None:
        return target
    if isinstance(payload, dict):
        target = _first_text(
            payload,
            "dataTarget",
            "datatarget",
            "target",
            "objectName",
            "technicalName",
        )
        if target is not None:
            return target
    for request in requests:
        target_value = request.get("target")
        if isinstance(target_value, str) and target_value.strip():
            return target_value.strip()
    return None


def _request_target_type(
    source: str,
    *,
    payload: dict[str, object] | list[object],
    requests: Sequence[dict[str, object]],
) -> str:
    target_type = (
        _source_query_value(source, "objectType")
        or _source_query_value(source, "targetType")
        or _source_query_value(source, "tlogo")
    )
    if target_type is not None:
        return target_type.upper()
    if isinstance(payload, dict):
        target_type = _first_text(payload, "objectType", "targetType", "tlogo", "type")
        if target_type is not None:
            return target_type.upper()
    for request in requests:
        value = request.get("target_type")
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return "UNKNOWN"


def _sorted_request_records(
    requests: Sequence[dict[str, object]],
) -> list[dict[str, object]]:
    return sorted(
        [request for request in requests if request],
        key=_request_sort_key,
        reverse=True,
    )


def _request_sort_key(request: dict[str, object]) -> tuple[bool, str, str]:
    timestamp = request.get("timestamp")
    request_tsn = request.get("request_tsn") or request.get("tsn")
    return (
        isinstance(timestamp, str) and bool(timestamp),
        timestamp if isinstance(timestamp, str) else "",
        request_tsn if isinstance(request_tsn, str) else "",
    )


def _number_value(value: object) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return value
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        try:
            if "." in text:
                return float(text)
            return int(text)
        except ValueError:
            return None
    return None


def _merge_metadata(
    current: dict[str, object],
    incoming: dict[str, object],
) -> dict[str, object]:
    metadata: dict[str, object] = {**incoming, **current}
    current_freshness = current.get("request_freshness")
    incoming_freshness = incoming.get("request_freshness")
    if isinstance(current_freshness, dict) and isinstance(incoming_freshness, dict):
        metadata["request_freshness"] = _merge_request_freshness(
            current_freshness,
            incoming_freshness,
        )
    return metadata


def _merge_request_freshness(
    current: dict[object, object],
    incoming: dict[object, object],
) -> dict[str, object]:
    target = _dict_text(current, "target") or _dict_text(incoming, "target") or ""
    target_type = _dict_text(current, "target_type") or _dict_text(incoming, "target_type")
    requests = _merge_request_records(
        [
            *_freshness_requests(current),
            *_freshness_requests(incoming),
            *_freshness_latest(current),
            *_freshness_latest(incoming),
        ]
    )
    metadata = _request_freshness_metadata(
        target=target,
        target_type=target_type or "UNKNOWN",
        requests=requests,
    )
    return metadata


def _freshness_requests(value: dict[object, object]) -> list[dict[str, object]]:
    requests = value.get("requests")
    if not isinstance(requests, list):
        return []
    return [item for item in requests if isinstance(item, dict)]


def _freshness_latest(value: dict[object, object]) -> list[dict[str, object]]:
    latest = value.get("latest")
    return [latest] if isinstance(latest, dict) else []


def _merge_request_records(
    requests: Sequence[dict[str, object]],
) -> list[dict[str, object]]:
    by_key: dict[str, dict[str, object]] = {}
    for index, request in enumerate(requests):
        key = _request_record_key(request, fallback=str(index))
        by_key[key] = {**by_key.get(key, {}), **request}
    return _sorted_request_records(list(by_key.values()))


def _request_record_key(request: dict[str, object], *, fallback: str) -> str:
    for key in ("request_tsn", "tsn"):
        value = request.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _dict_text(value: dict[object, object], key: str) -> str | None:
    item = value.get(key)
    return item.strip() if isinstance(item, str) and item.strip() else None


class _MutableCatalog:
    def __init__(self) -> None:
        self.objects: dict[str, CatalogObjectRecord] = {}
        self.edges: dict[str, CatalogEdgeRecord] = {}

    def merge(self, catalog: IngestedCatalog) -> None:
        for obj in catalog.objects:
            self.add_object(obj)
        for edge in catalog.edges:
            self.add_edge(edge)

    def add_object(self, obj: CatalogObjectRecord) -> None:
        current = self.objects.get(obj.id)
        if current is None:
            self.objects[obj.id] = obj
            return
        evidence_ids = sorted({*current.evidence_ids, *obj.evidence_ids})
        self.objects[obj.id] = CatalogObjectRecord(
            id=current.id,
            name=current.name or obj.name,
            type=current.type if current.type != "UNKNOWN" else obj.type,
            label=current.label or obj.label,
            metadata=_merge_metadata(current.metadata, obj.metadata),
            evidence_ids=evidence_ids,
        )

    def add_edge(self, edge: CatalogEdgeRecord) -> None:
        self.edges.setdefault(edge.id, edge)

    def to_catalog(self) -> IngestedCatalog:
        objects = sorted(self.objects.values(), key=lambda item: item.id)
        edges = sorted(self.edges.values(), key=lambda item: item.id)
        evidence_ids = sorted(
            {
                *[evidence_id for obj in objects for evidence_id in obj.evidence_ids],
                *[evidence_id for edge in edges for evidence_id in edge.evidence_ids],
            }
        )
        return IngestedCatalog(objects=objects, edges=edges, evidence_ids=evidence_ids)


def _coerce_object_record(value: ObjectInput | CatalogObjectRecord) -> CatalogObjectRecord:
    if isinstance(value, CatalogObjectRecord):
        return value
    return CatalogObjectRecord.model_validate(value)


def _coerce_edge_record(value: EdgeInput | CatalogEdgeRecord) -> CatalogEdgeRecord:
    if isinstance(value, CatalogEdgeRecord):
        return value
    return CatalogEdgeRecord.model_validate(value)


def _bw_node_from_record(record: CatalogObjectRecord) -> BwNode:
    metadata = {**record.metadata, "evidence_ids": record.evidence_ids}
    return BwNode(
        id=record.id,
        name=record.name,
        type=record.type,
        label=record.label,
        metadata=metadata,
    )


def _bw_edge_from_record(record: CatalogEdgeRecord) -> BwEdge:
    metadata = {**record.metadata, "evidence_ids": record.evidence_ids}
    return BwEdge(
        id=record.id,
        source=record.source,
        target=record.target,
        type=record.type,
        confidence=record.confidence,
        metadata=metadata,
    )


def _snapshot_from_row(row: sqlite3.Row) -> CatalogSnapshotRecord:
    return CatalogSnapshotRecord(
        id=_row_str(row, "id"),
        created_at=_row_str(row, "created_at"),
        mode=_row_str(row, "mode"),
        source=_row_str(row, "source"),
        manifest_path=_row_optional_str(row, "manifest_path"),
        object_count=_row_int(row, "object_count"),
        edge_count=_row_int(row, "edge_count"),
    )


def _object_from_row(row: sqlite3.Row) -> CatalogObjectRecord:
    return CatalogObjectRecord(
        id=_row_str(row, "object_id"),
        name=_row_optional_str(row, "name"),
        type=_row_str(row, "object_type"),
        label=_row_optional_str(row, "label"),
        metadata=_json_dict(_row_str(row, "metadata_json")),
        evidence_ids=_json_str_list(_row_str(row, "evidence_ids_json")),
    )


def _edge_from_row(row: sqlite3.Row) -> CatalogEdgeRecord:
    return CatalogEdgeRecord(
        id=_row_str(row, "edge_id"),
        source=_row_str(row, "source_id"),
        target=_row_str(row, "target_id"),
        type=_row_str(row, "edge_type"),
        confidence=_row_str(row, "confidence"),
        metadata=_json_dict(_row_str(row, "metadata_json")),
        evidence_ids=_json_str_list(_row_str(row, "evidence_ids_json")),
    )


def _repository_node_from_row(row: sqlite3.Row) -> RepositoryNodeRecord:
    return RepositoryNodeRecord(
        id=_row_str(row, "node_id"),
        parent_path=_row_str(row, "parent_path"),
        path=_row_str(row, "path"),
        name=_row_str(row, "name"),
        description=_row_str(row, "description"),
        object_type=_row_str(row, "object_type"),
        object_subtype=_row_optional_str(row, "object_subtype"),
        status=_row_optional_str(row, "status"),
        has_children=bool(_row_int(row, "has_children")),
        self_url=_row_optional_str(row, "self_url"),
        fiori_only=bool(_row_int(row, "fiori_only")),
        children_path=_row_optional_str(row, "children_path"),
        metadata=_json_dict(_row_str(row, "metadata_json")),
    )


def _capture_scope_from_row(row: sqlite3.Row) -> CaptureScopeRecord:
    return CaptureScopeRecord(
        object_id=_row_str(row, "object_id"),
        object_type=_row_str(row, "object_type"),
        role=cast(Literal["selected", "discovered"], _row_str(row, "role")),
        operation=_row_str(row, "operation"),
        status=cast(Literal["selected", "ok", "error", "skipped"], _row_str(row, "status")),
        error=_row_optional_str(row, "error"),
        evidence_ids=_json_str_list(_row_str(row, "evidence_ids_json")),
        metadata=_json_dict(_row_str(row, "metadata_json")),
    )


def _glossary_term_from_row(row: sqlite3.Row) -> GlossaryTermRecord:
    return GlossaryTermRecord(
        id=_row_str(row, "term_id"),
        term=_row_str(row, "term"),
        normalized_term=_row_str(row, "normalized_term"),
        source=_row_str(row, "source"),
        candidate=bool(_row_int(row, "candidate")),
        object_id=_row_optional_str(row, "object_id"),
        object_type=_row_optional_str(row, "object_type"),
        field_name=_row_optional_str(row, "field_name"),
        evidence_ids=_json_str_list(_row_str(row, "evidence_ids_json")),
        metadata=_json_dict(_row_str(row, "metadata_json")),
    )


def _row_str(row: sqlite3.Row, key: str) -> str:
    value = row[key]
    if not isinstance(value, str):
        raise TypeError(f"expected string column {key}")
    return value


def _row_optional_str(row: sqlite3.Row, key: str) -> str | None:
    value = row[key]
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"expected optional string column {key}")
    return value


def _row_int(row: sqlite3.Row, key: str) -> int:
    value = row[key]
    if not isinstance(value, int):
        raise TypeError(f"expected integer column {key}")
    return value


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_dict(value: str) -> JsonDict:
    loaded = json.loads(value)
    return loaded if isinstance(loaded, dict) else {}


def _json_str_list(value: str) -> list[str]:
    loaded = json.loads(value)
    if not isinstance(loaded, list):
        return []
    return [item for item in loaded if isinstance(item, str)]


def _replace_metadata_glossary_terms(
    con: sqlite3.Connection,
    snapshot_id: str,
    objects: Sequence[CatalogObjectRecord],
) -> None:
    con.execute(
        "DELETE FROM glossary_terms WHERE snapshot_id = ? AND source = ?",
        (snapshot_id, "metadata"),
    )
    _insert_glossary_terms(con, snapshot_id, _metadata_glossary_terms(objects))


def _insert_glossary_terms(
    con: sqlite3.Connection,
    snapshot_id: str,
    terms: Sequence[GlossaryTermRecord],
) -> None:
    if not terms:
        return
    con.executemany(
        """
        INSERT OR REPLACE INTO glossary_terms(
            snapshot_id, term_id, term, normalized_term, source, candidate, object_id,
            object_type, field_name, evidence_ids_json, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                snapshot_id,
                term.id,
                term.term,
                term.normalized_term,
                term.source,
                1 if term.candidate else 0,
                term.object_id,
                term.object_type,
                term.field_name,
                _json_dumps(term.evidence_ids),
                _json_dumps(term.metadata),
            )
            for term in terms
        ],
    )


def _metadata_glossary_terms(
    objects: Sequence[CatalogObjectRecord],
) -> list[GlossaryTermRecord]:
    terms: dict[str, GlossaryTermRecord] = {}
    for obj in objects:
        candidates = [
            (obj.id, "technical_id"),
            (obj.name, "name"),
            (obj.label, "label"),
        ]
        description = _text(obj.metadata.get("description"))
        if description:
            candidates.append((description, "description"))
        for raw_term, source_field in candidates:
            term = _text(raw_term)
            if term is None:
                continue
            record = _glossary_record(
                term=term,
                source="metadata",
                object_id=obj.id,
                object_type=obj.type,
                field_name=None,
                evidence_ids=obj.evidence_ids,
                metadata={"source_field": source_field},
            )
            terms.setdefault(record.id, record)
    return sorted(terms.values(), key=lambda item: (item.source, item.term.lower(), item.id))


def _sql_glossary_terms(
    *,
    view_id: str,
    reference_object_ids: Sequence[str],
    column_names: Sequence[str],
    citation_ids: Sequence[str],
) -> list[GlossaryTermRecord]:
    terms: dict[str, GlossaryTermRecord] = {}
    for object_id in reference_object_ids:
        term = _text(object_id)
        if term is None:
            continue
        record = _glossary_record(
            term=term,
            source="sql_evidence",
            object_id=term,
            object_type="SQL_REFERENCE",
            field_name=None,
            evidence_ids=list(citation_ids),
            metadata={"view_id": view_id},
        )
        terms.setdefault(record.id, record)
    for column_name in column_names:
        term = _text(column_name)
        if term is None:
            continue
        record = _glossary_record(
            term=term,
            source="sql_evidence",
            object_id=view_id,
            object_type="NATIVE_SQL_VIEW",
            field_name=term,
            evidence_ids=list(citation_ids),
            metadata={"view_id": view_id},
        )
        terms.setdefault(record.id, record)
    return sorted(terms.values(), key=lambda item: (item.source, item.term.lower(), item.id))


def _glossary_record(
    *,
    term: str,
    source: str,
    object_id: str | None,
    object_type: str | None,
    field_name: str | None,
    evidence_ids: Sequence[str],
    metadata: JsonDict,
) -> GlossaryTermRecord:
    normalized = _normalize_term(term)
    digest = hashlib.sha256(
        "\n".join([source, normalized, object_id or "", field_name or ""]).encode("utf-8")
    ).hexdigest()[:16]
    return GlossaryTermRecord(
        id=f"glossary:{source}:{digest}",
        term=term,
        normalized_term=normalized,
        source=source,
        candidate=True,
        object_id=object_id,
        object_type=object_type,
        field_name=field_name,
        evidence_ids=sorted(set(evidence_ids)),
        metadata=metadata,
    )


def _normalize_term(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _snapshot_id(*, source: str, created_at: str) -> str:
    digest = hashlib.sha256(f"{source}\n{created_at}".encode()).hexdigest()[:12]
    timestamp = created_at.replace(":", "").replace("+", "Z").replace(".", "-")
    return f"snap-{timestamp}-{digest}"


def _safe_id(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)
    return safe or "payload"


def _safe_metadata(value: dict[str, Any]) -> JsonDict:
    safe: JsonDict = {}
    for key, item in value.items():
        if isinstance(item, str | int | float | bool) or item is None:
            safe[key] = item
    return safe


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _payload_kind(payload: object, *, explicit_kind: str | None) -> str:
    kind = _text(explicit_kind)
    if kind is None and isinstance(payload, dict):
        kind = _text(payload.get("kind")) or _text(payload.get("type"))
    return kind.lower().replace("-", "_") if kind is not None else ""


def _first_text(item: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        value = _text(item.get(key))
        if value is not None:
            return value
    return None


def _payload_items(payload: dict[str, object], keys: tuple[str, ...]) -> list[dict[str, object]]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _source_query_value(source: str, key: str) -> str | None:
    wanted = key.lower()
    for raw_key, values in parse_qs(urlparse(source).query).items():
        if raw_key.lower() == wanted and values:
            return values[0]
    return None


def _int_value(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _add_edge(
    mutable: _MutableCatalog,
    *,
    payload_id: str,
    source: str,
    target: str,
    edge_type: str,
    suffix: str,
) -> None:
    edge_id = f"{payload_id}:{suffix}:{source}->{target}"
    mutable.add_edge(
        CatalogEdgeRecord(
            id=edge_id,
            source=source,
            target=target,
            type=edge_type,
            confidence="direct",
            evidence_ids=[edge_id],
        )
    )


def _xml_root(xml: str) -> ET.Element[str] | None:
    try:
        return ET.fromstring(xml)
    except ET.ParseError:
        return None


def _datasource_fields(root: ET.Element[str]) -> list[dict[str, object]]:
    fields: list[dict[str, object]] = []
    for element in root.iter():
        if element is root or _local_xml_name(element.tag) != "field":
            continue
        item = _xml_fields(element)
        name = _first_text(item, "name", "technicalName", "fieldName")
        if name is None:
            continue
        record: dict[str, object] = {"name": name}
        for source_key, target_key in (
            ("description", "description"),
            ("type", "type"),
            ("length", "length"),
        ):
            value = _first_text(item, source_key)
            if value is not None:
                record[target_key] = value
        fields.append(record)
    return fields


def _first_xml_child_fields(root: ET.Element[str], *local_names: str) -> dict[str, object]:
    allowed_names = set(local_names)
    for element in root.iter():
        if element is root or _local_xml_name(element.tag) not in allowed_names:
            continue
        fields = _xml_fields(element)
        if fields:
            return fields
    return {}


def _dtp_overview_object_fields(root: ET.Element[str]) -> dict[str, object]:
    for overview in root.iter():
        if _local_xml_name(overview.tag) != "overview":
            continue
        for element in list(overview):
            if _local_xml_name(element.tag) != "object":
                continue
            fields = _xml_fields(element)
            object_name = _first_text(fields, "technicalName", "name", "objectName")
            if object_name is None:
                continue
            object_type = _first_text(fields, "objectType", "type", "tlogo")
            if object_type is None or object_type.upper() in {"TRFN", "TRANSFORMATION"}:
                return fields
    return {}


def _related_providers_from_links(
    root: ET.Element[str],
    *,
    current_object_id: str | None = None,
) -> list[tuple[str, str]]:
    providers: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    current_object_key = current_object_id.upper() if current_object_id is not None else None
    for element in root.iter():
        if _local_xml_name(element.tag) != "link":
            continue
        rel = _text(element.attrib.get("rel"))
        if rel is None or rel.lower() != "related":
            continue
        href = _text(element.attrib.get("href"))
        if href is None:
            continue
        provider = _provider_from_href(href)
        if provider is None or provider in seen:
            continue
        provider_id, _provider_type = provider
        if current_object_key is not None and provider_id.upper() == current_object_key:
            continue
        seen.add(provider)
        providers.append(provider)
    return providers


def _provider_from_href(href: str) -> tuple[str, str] | None:
    parts = [part for part in urlparse(href).path.split("/") if part]
    type_by_segment = {
        "hcpr": "HCPR",
        "adso": "ADSO",
        "rsds": "RSDS",
        "query": "QUERY",
    }
    lower_parts = [part.lower() for part in parts]
    for index, segment in enumerate(lower_parts):
        provider_type = type_by_segment.get(segment)
        if provider_type is None or index + 1 >= len(parts):
            continue
        return parts[index + 1].upper(), provider_type
    return None


def _composite_input_providers(root: ET.Element[str]) -> list[dict[str, object]]:
    providers: list[dict[str, object]] = []
    for element in root.iter():
        if element is root:
            continue
        if _local_xml_name(element.tag) not in {"inputProvider", "provider", "input"}:
            continue
        fields = _xml_fields(element)
        fields = _normalize_composite_provider_fields(fields)
        if _first_text(fields, "technicalName", "name", "objectName") is not None:
            providers.append(fields)
    return providers


def _normalize_composite_provider_fields(fields: dict[str, object]) -> dict[str, object]:
    provider_id = _first_text(fields, "technicalName", "objectName", "name")
    provider_type = _first_text(fields, "objectType", "type")
    alias_id, alias_type = _provider_from_input_alias(_first_text(fields, "alias"))
    normalized = dict(fields)
    if provider_id is None and alias_id is not None:
        normalized["name"] = alias_id
    if provider_type is None and alias_type is not None:
        normalized["type"] = alias_type
    return normalized


def _provider_from_input_alias(alias: str | None) -> tuple[str | None, str | None]:
    if alias is None:
        return None, None
    prefix, separator, suffix = alias.strip().rpartition(".")
    if not separator:
        alias_id = alias.strip().upper()
        return alias_id or None, None
    provider_id = prefix.strip().upper() or None
    provider_type = _provider_type_from_alias_suffix(suffix.strip())
    return provider_id, provider_type


def _provider_type_from_alias_suffix(suffix: str) -> str | None:
    normalized = suffix.upper()
    type_by_suffix = {
        "AD": "ADSO",
        "ADSO": "ADSO",
        "CP": "HCPR",
        "HCPR": "HCPR",
        "QUERY": "QUERY",
        "QRY": "QUERY",
        "RSDS": "RSDS",
    }
    return type_by_suffix.get(normalized)


def _xml_items(xml: str) -> list[dict[str, object]]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    return [item for item in (_xml_fields(element) for element in root.iter()) if item]


def _xml_fields(element: ET.Element[str]) -> dict[str, object]:
    fields: dict[str, object] = {}
    for key, value in element.attrib.items():
        if value.strip():
            fields[_local_xml_name(key)] = value.strip()
    for child in list(element):
        text = "".join(child.itertext()).strip()
        if text:
            fields[_local_xml_name(child.tag)] = text
    return fields


def _local_xml_name(name: str) -> str:
    if "}" in name:
        return name.rsplit("}", maxsplit=1)[1]
    return name
