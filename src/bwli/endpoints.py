from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Endpoint:
    path: str
    params: dict[str, Any]


def build_search_endpoint(search_term: str, *, object_type: str | None = None) -> Endpoint:
    params: dict[str, Any] = {"searchTerm": search_term}
    if object_type:
        params["objectType"] = object_type
    return Endpoint(path="/sap/bw/modeling/repo/is/bwsearch", params=params)


def build_dataflow_endpoint(object_name: str) -> Endpoint:
    return Endpoint(
        path="/sap/bw/modeling/dmod/8TRANSIENT",
        params={"objectName": object_name},
    )


def build_xref_endpoint(object_name: str, *, direction: str = "downstream") -> Endpoint:
    return Endpoint(
        path="/sap/bw/modeling/repo/is/xref",
        params={"objectName": object_name, "direction": direction},
    )
