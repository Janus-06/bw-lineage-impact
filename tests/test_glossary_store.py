from __future__ import annotations

from pathlib import Path

from bwli.store import CatalogObjectRecord, CatalogStore
from bwli.store.glossary_store import GlossaryStore, glossary_path_for


def _catalog_with_terms(tmp_path: Path) -> tuple[CatalogStore, str]:
    catalog = CatalogStore(tmp_path / "catalog.sqlite")
    snapshot = catalog.create_snapshot(mode="test", source="fixture://glossary")
    catalog.replace_catalog(
        snapshot.id,
        objects=[
            CatalogObjectRecord(
                id="ZADSO_SALES",
                name="Sales ADSO",
                type="ADSO",
                metadata={
                    "fields": [{"name": "NET_VALUE", "description": "Net Value", "type": "CURR"}]
                },
                evidence_ids=["e1"],
            ),
            CatalogObjectRecord(
                id="ZQ_SALES", name="Sales Query", type="QUERY", evidence_ids=["e2"]
            ),
        ],
        edges=[],
    )
    return catalog, snapshot.id


def test_glossary_db_separate_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BWLI_HOME", str(tmp_path / "home"))

    assert glossary_path_for(tmp_path).name == "glossary.sqlite"
    assert glossary_path_for(tmp_path) != (tmp_path / "home" / "catalog.sqlite")


def test_backfill_idempotent(tmp_path: Path) -> None:
    catalog, snapshot_id = _catalog_with_terms(tmp_path)
    store = GlossaryStore(tmp_path / "glossary.sqlite")

    first = store.backfill_from_catalog(catalog, snapshot_id)
    second = store.backfill_from_catalog(catalog, snapshot_id)

    assert first == second
    aggregate = store.aggregate()
    assert aggregate["total"] == first["total"]
    assert aggregate["candidate"] == first["candidate"]
    assert aggregate["confirmed"] == 0


def test_aggregate_counts_dedupe(tmp_path: Path) -> None:
    catalog, snapshot_id = _catalog_with_terms(tmp_path)
    store = GlossaryStore(tmp_path / "glossary.sqlite")
    store.backfill_from_catalog(catalog, snapshot_id)
    store.backfill_from_catalog(catalog, snapshot_id)

    terms = store.list_terms(snapshot_id=snapshot_id, query="sales")
    assert terms
    aggregate = store.aggregate(query="sales")
    assert aggregate["total"] == len(terms)
    assert aggregate["object_count"] >= 2


def test_lifecycle_candidate_to_confirmed(tmp_path: Path) -> None:
    catalog, snapshot_id = _catalog_with_terms(tmp_path)
    store = GlossaryStore(tmp_path / "glossary.sqlite")
    store.backfill_from_catalog(catalog, snapshot_id)
    term = store.list_terms(snapshot_id=snapshot_id, query="Sales ADSO")[0]

    updated = store.set_lifecycle(term.id, "confirmed")

    assert updated.lifecycle == "confirmed"
    aggregate = store.aggregate(query="Sales ADSO")
    assert aggregate["candidate"] == 0
    assert aggregate["confirmed"] == 1
