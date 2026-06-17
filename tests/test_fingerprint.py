from __future__ import annotations

from bwli.fingerprint import ChangeLevel, classify_node_change, object_fingerprint
from bwli.graph import BwLayer, BwNode


def test_object_fingerprint_stable_for_same_payload() -> None:
    node = BwNode(
        id="ADSO:SALES",
        name="Sales ADSO",
        type="ADSO",
        label="Sales Provider",
        summary="Curated sales facts",
        tags=["sales", "provider"],
        complexity=3,
        layer=BwLayer.PROVIDER,
        metadata={"b": 2, "a": {"z": "last", "y": ["first", "second"]}},
    )
    reordered = BwNode(
        id="ADSO:SALES",
        name="Sales ADSO",
        type="ADSO",
        label="Sales Provider",
        summary="Curated sales facts",
        tags=["sales", "provider"],
        complexity=3,
        layer=BwLayer.PROVIDER,
        metadata={"a": {"y": ["first", "second"], "z": "last"}, "b": 2},
    )

    digest = object_fingerprint(node)

    assert digest == object_fingerprint(reordered)
    assert len(digest) == 64


def test_object_fingerprint_ignores_volatile_metadata_keys() -> None:
    base = BwNode(
        id="ADSO:SALES",
        type="ADSO",
        layer=BwLayer.PROVIDER,
        metadata={
            "owner": "finance",
            "last_changed_at": "2026-06-17T10:00:00Z",
            "request_freshness": {"request_tsn": "123", "timestamp": "2026-06-17T10:01:00Z"},
            "fingerprint": "old",
            "generated_id": "run-1",
        },
    )
    same_stable_payload = BwNode(
        id="ADSO:SALES",
        type="ADSO",
        layer=BwLayer.PROVIDER,
        metadata={
            "owner": "finance",
            "last_changed_at": "2026-06-18T10:00:00Z",
            "request_freshness": {"request_tsn": "999", "timestamp": "2026-06-18T10:01:00Z"},
            "fingerprint": "new",
            "generated_id": "run-2",
        },
    )
    structural_change = same_stable_payload.model_copy(
        update={"metadata": {"owner": "sales"}}
    )

    assert object_fingerprint(base) == object_fingerprint(same_stable_payload)
    assert object_fingerprint(base) != object_fingerprint(structural_change)


def test_classify_node_change_cosmetic_vs_structural() -> None:
    before = BwNode(
        id="ADSO:SALES",
        name="Sales ADSO",
        type="ADSO",
        label="Sales Provider",
        summary="Old summary",
        tags=["sales"],
        layer=BwLayer.PROVIDER,
        metadata={"owner": "finance", "description": "Old description"},
    )
    volatile_only = before.model_copy(
        update={
            "metadata": {
                "owner": "finance",
                "description": "Old description",
                "last_changed_at": "2026-06-18T10:00:00Z",
            }
        }
    )
    cosmetic = before.model_copy(
        update={
            "name": "Sales ADSO v2",
            "label": "Sales Provider v2",
            "summary": "New summary",
            "tags": ["sales", "certified"],
            "metadata": {"owner": "finance", "description": "New description"},
        }
    )
    structural_type = before.model_copy(update={"type": "QUERY"})
    structural_metadata = before.model_copy(
        update={"metadata": {"owner": "sales", "description": "Old description"}}
    )

    assert classify_node_change(before, volatile_only) == ChangeLevel.NONE
    assert classify_node_change(before, cosmetic) == ChangeLevel.COSMETIC
    assert classify_node_change(before, structural_type) == ChangeLevel.STRUCTURAL
    assert classify_node_change(before, structural_metadata) == ChangeLevel.STRUCTURAL
