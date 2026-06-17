from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from typing import Any

from bwli.graph import BwNode


class ChangeLevel(StrEnum):
    NONE = "NONE"
    COSMETIC = "COSMETIC"
    STRUCTURAL = "STRUCTURAL"


_COSMETIC_NODE_FIELDS = frozenset({"label", "name", "summary", "tags"})
_COSMETIC_METADATA_KEYS = frozenset({"description", "label", "name", "summary", "tags"})
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def object_fingerprint(node: BwNode) -> str:
    """Return a deterministic sha256 over a normalized stable node payload."""
    canonical = json.dumps(
        _fingerprint_payload(node),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def classify_node_change(before: BwNode, after: BwNode) -> ChangeLevel:
    """Classify a node-only change without mutating either input model."""
    if object_fingerprint(before) == object_fingerprint(after):
        return ChangeLevel.NONE
    if _structural_payload(before) != _structural_payload(after):
        return ChangeLevel.STRUCTURAL
    return ChangeLevel.COSMETIC


def _fingerprint_payload(node: BwNode) -> dict[str, Any]:
    return {
        "id": node.id,
        "type": node.type,
        "layer": node.layer.value if node.layer is not None else None,
        "name": node.name,
        "label": node.label,
        "summary": node.summary,
        "tags": sorted(node.tags),
        "complexity": node.complexity,
        "metadata": _normalize_metadata(node.metadata),
    }


def _structural_payload(node: BwNode) -> dict[str, Any]:
    metadata = _normalize_metadata(node.metadata)
    structural_metadata = {
        key: value
        for key, value in metadata.items()
        if _normalized_key(key) not in _COSMETIC_METADATA_KEYS
    }
    return {
        "id": node.id,
        "type": node.type,
        "layer": node.layer.value if node.layer is not None else None,
        "complexity": node.complexity,
        "metadata": structural_metadata,
    }


def _normalize_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _normalize_metadata(value[key])
            for key in sorted(value)
            if not _is_volatile_metadata_key(key)
        }
    if isinstance(value, list):
        return [_normalize_metadata(item) for item in value]
    return value


def _is_volatile_metadata_key(key: str) -> bool:
    normalized = _normalized_key(key)
    if not normalized:
        return False
    if normalized in {
        "etag",
        "guid",
        "uuid",
        "tsn",
        "requesttsn",
        "requesttimestamp",
        "runtimefreshness",
        "requestfreshness",
        "freshness",
    }:
        return True
    if "fingerprint" in normalized or "hash" in normalized or "sha256" in normalized:
        return True
    if "timestamp" in normalized:
        return True
    if "generatedid" in normalized or normalized.endswith("generatedid"):
        return True
    if normalized.endswith("tsn") or normalized.endswith("uuid") or normalized.endswith("guid"):
        return True
    return normalized.startswith(("created", "changed", "updated", "modified", "last")) and (
        normalized.endswith("at")
        or normalized.endswith("date")
        or normalized.endswith("time")
        or normalized.endswith("datetime")
    )


def _normalized_key(key: str) -> str:
    return _NON_ALNUM.sub("", key.lower())
