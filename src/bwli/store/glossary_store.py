from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from bwli.store.catalog import CatalogStore
from bwli.store.secret_guard import assert_no_persisted_secrets

GlossaryLifecycle = Literal["candidate", "confirmed", "rejected"]
JsonDict = dict[str, object]


class GlossaryLifecycleRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    snapshot_id: str
    term: str
    normalized_term: str
    source: str = "metadata"
    lifecycle: GlossaryLifecycle = "candidate"
    object_id: str | None = None
    object_type: str | None = None
    field_name: str | None = None
    occurrences: int = 1
    evidence_ids: list[str] = Field(default_factory=list)
    metadata: JsonDict = Field(default_factory=dict)


def glossary_path_for(project_root: Path) -> Path:
    home = os.environ.get("BWLI_HOME")
    if home and home.strip():
        return Path(home).expanduser().resolve() / "glossary.sqlite"
    return project_root.resolve() / ".bwli" / "glossary.sqlite"


class GlossaryStore:
    """Local-only glossary lifecycle DB, separate from snapshot catalog storage."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def backfill_from_catalog(self, catalog: CatalogStore, snapshot_id: str) -> dict[str, int]:
        snapshot = catalog.get_snapshot(snapshot_id)
        if snapshot is None:
            raise KeyError(f"snapshot not found: {snapshot_id}")
        terms = catalog.list_glossary_terms(snapshot_id, query=None, limit=100)
        assert_no_persisted_secrets(
            {
                "snapshot_id": snapshot_id,
                "terms": [term.model_dump(mode="json") for term in terms],
            }
        )
        now = datetime.now(UTC).isoformat()
        with self._connect() as con:
            for term in terms:
                con.execute(
                    """
                    INSERT INTO glossary_terms(
                        term_id, snapshot_id, term, normalized_term, source, lifecycle,
                        object_id, object_type, field_name, occurrences, evidence_ids_json,
                        metadata_json, first_seen, last_seen
                    ) VALUES (?, ?, ?, ?, ?, 'candidate', ?, ?, ?, 1, ?, ?, ?, ?)
                    ON CONFLICT(term_id) DO UPDATE SET
                        snapshot_id = excluded.snapshot_id,
                        term = excluded.term,
                        normalized_term = excluded.normalized_term,
                        source = excluded.source,
                        object_id = excluded.object_id,
                        object_type = excluded.object_type,
                        field_name = excluded.field_name,
                        evidence_ids_json = excluded.evidence_ids_json,
                        metadata_json = excluded.metadata_json,
                        last_seen = excluded.last_seen
                    """,
                    (
                        term.id,
                        snapshot_id,
                        term.term,
                        term.normalized_term,
                        term.source,
                        term.object_id,
                        term.object_type,
                        term.field_name,
                        _json_dumps(term.evidence_ids),
                        _json_dumps(term.metadata),
                        now,
                        now,
                    ),
                )
        return self.aggregate(snapshot_id=snapshot_id)

    def list_terms(
        self,
        *,
        snapshot_id: str | None = None,
        query: str | None = None,
        limit: int = 100,
    ) -> list[GlossaryLifecycleRecord]:
        clauses, params = self._query_clauses(snapshot_id=snapshot_id, query=query)
        params.append(max(1, min(limit, 500)))
        with self._connect() as con:
            rows = con.execute(
                f"""
                SELECT term_id, snapshot_id, term, normalized_term, source, lifecycle,
                    object_id, object_type, field_name, occurrences, evidence_ids_json,
                    metadata_json
                FROM glossary_terms
                WHERE {" AND ".join(clauses)}
                ORDER BY lower(term), object_id, field_name, term_id
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [_record_from_row(row) for row in rows]

    def aggregate(
        self,
        *,
        snapshot_id: str | None = None,
        query: str | None = None,
    ) -> dict[str, int]:
        clauses, params = self._query_clauses(snapshot_id=snapshot_id, query=query)
        with self._connect() as con:
            rows = con.execute(
                f"""
                SELECT lifecycle, count(*) AS count
                FROM glossary_terms
                WHERE {" AND ".join(clauses)}
                GROUP BY lifecycle
                """,
                tuple(params),
            ).fetchall()
            object_count = int(
                con.execute(
                    f"""
                    SELECT count(DISTINCT object_id) AS count
                    FROM glossary_terms
                    WHERE {" AND ".join(clauses)} AND object_id IS NOT NULL
                    """,
                    tuple(params),
                ).fetchone()[0]
            )
        counts = {
            "total": 0,
            "candidate": 0,
            "confirmed": 0,
            "rejected": 0,
            "object_count": object_count,
        }
        for row in rows:
            lifecycle = _row_str(row, "lifecycle")
            count = int(row["count"])
            counts["total"] += count
            if lifecycle in {"candidate", "confirmed", "rejected"}:
                counts[lifecycle] = count
        return counts

    def set_lifecycle(self, term_id: str, lifecycle: str) -> GlossaryLifecycleRecord:
        if lifecycle not in {"candidate", "confirmed", "rejected"}:
            raise ValueError("lifecycle must be candidate, confirmed, or rejected")
        with self._connect() as con:
            row = con.execute(
                "SELECT term_id FROM glossary_terms WHERE term_id = ?",
                (term_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"glossary term not found: {term_id}")
            con.execute(
                "UPDATE glossary_terms SET lifecycle = ?, last_seen = ? WHERE term_id = ?",
                (lifecycle, datetime.now(UTC).isoformat(), term_id),
            )
            updated = con.execute(
                """
                SELECT term_id, snapshot_id, term, normalized_term, source, lifecycle,
                    object_id, object_type, field_name, occurrences, evidence_ids_json,
                    metadata_json
                FROM glossary_terms WHERE term_id = ?
                """,
                (term_id,),
            ).fetchone()
        if updated is None:
            raise KeyError(f"glossary term not found after update: {term_id}")
        return _record_from_row(updated)

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS glossary_terms (
                    term_id TEXT PRIMARY KEY,
                    snapshot_id TEXT NOT NULL,
                    term TEXT NOT NULL,
                    normalized_term TEXT NOT NULL,
                    source TEXT NOT NULL,
                    lifecycle TEXT NOT NULL DEFAULT 'candidate',
                    object_id TEXT,
                    object_type TEXT,
                    field_name TEXT,
                    occurrences INTEGER NOT NULL DEFAULT 1,
                    evidence_ids_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_local_glossary_query
                    ON glossary_terms(normalized_term);
                CREATE INDEX IF NOT EXISTS idx_local_glossary_snapshot
                    ON glossary_terms(snapshot_id);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    @staticmethod
    def _query_clauses(
        *,
        snapshot_id: str | None,
        query: str | None,
    ) -> tuple[list[str], list[object]]:
        clauses = ["1 = 1"]
        params: list[object] = []
        if snapshot_id:
            clauses.append("snapshot_id = ?")
            params.append(snapshot_id)
        if query and query.strip():
            pattern = f"%{_normalize_term(query)}%"
            raw_pattern = f"%{query.strip().lower()}%"
            clauses.append(
                "(normalized_term LIKE ? OR lower(coalesce(object_id, '')) LIKE ? "
                "OR lower(coalesce(field_name, '')) LIKE ?)"
            )
            params.extend([pattern, raw_pattern, raw_pattern])
        return clauses, params


def _record_from_row(row: sqlite3.Row) -> GlossaryLifecycleRecord:
    lifecycle = cast(GlossaryLifecycle, _row_str(row, "lifecycle"))
    return GlossaryLifecycleRecord(
        id=_row_str(row, "term_id"),
        snapshot_id=_row_str(row, "snapshot_id"),
        term=_row_str(row, "term"),
        normalized_term=_row_str(row, "normalized_term"),
        source=_row_str(row, "source"),
        lifecycle=lifecycle,
        object_id=_row_optional_str(row, "object_id"),
        object_type=_row_optional_str(row, "object_type"),
        field_name=_row_optional_str(row, "field_name"),
        occurrences=int(row["occurrences"]),
        evidence_ids=_json_str_list(_row_str(row, "evidence_ids_json")),
        metadata=_json_dict(_row_str(row, "metadata_json")),
    )


def _normalize_term(value: str) -> str:
    return " ".join(value.strip().lower().split())


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
