from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ACCEPT_HEADERS: dict[str, str] = {
    "search": "application/xml",
    "xref": "application/xml",
    "dataflow": "application/vnd.sap.bw.modeling.dmod-v1_0_0+xml",
    "hcpr": "application/vnd.sap.bw.modeling.hcpr-v1_15_0+xml",
    "adso": "application/vnd.sap.bw.modeling.adso-v1_5_0+xml",
}

DataflowDirection = Literal["upwards", "downwards", "both"]


@dataclass(frozen=True)
class Endpoint:
    path: str
    params: dict[str, Any]
    accept: str


def build_search_endpoint(search_term: str, *, object_type: str | None = None) -> Endpoint:
    params: dict[str, Any] = {"searchTerm": search_term}
    if object_type:
        params["objectType"] = object_type
    return Endpoint(
        path="/sap/bw/modeling/repo/is/bwsearch",
        params=params,
        accept=ACCEPT_HEADERS["search"],
    )


def build_dataflow_endpoint(
    object_name: str,
    *,
    object_type: str = "ADSO",
    source_system: str | None = None,
    direction: DataflowDirection = "downwards",
    levels: int = 3,
) -> Endpoint:
    type_upper = object_type.upper()
    object_name_for_dataflow = _dataflow_object_name(
        object_name,
        object_type=type_upper,
        source_system=source_system,
    )
    params: dict[str, Any] = {
        "objecttype": type_upper,
        "objectname": object_name_for_dataflow,
    }
    if direction in {"upwards", "both"}:
        params["levelupwards"] = levels
    if direction in {"downwards", "both"}:
        params["leveldownwards"] = levels
    return Endpoint(
        path="/sap/bw/modeling/dmod/8TRANSIENT",
        params=params,
        accept=ACCEPT_HEADERS["dataflow"],
    )


def build_xref_endpoint(object_name: str, *, direction: str = "downstream") -> Endpoint:
    return Endpoint(
        path="/sap/bw/modeling/repo/is/xref",
        params={"objectName": object_name, "direction": direction},
        accept=ACCEPT_HEADERS["xref"],
    )


def build_hcpr_endpoint(object_name: str) -> Endpoint:
    return Endpoint(
        path=f"/sap/bw/modeling/hcpr/{object_name.lower()}/m",
        params={},
        accept=ACCEPT_HEADERS["hcpr"],
    )


def build_adso_endpoint(object_name: str) -> Endpoint:
    return Endpoint(
        path=f"/sap/bw/modeling/adso/{object_name.lower()}/m",
        params={},
        accept=ACCEPT_HEADERS["adso"],
    )


def _dataflow_object_name(
    object_name: str,
    *,
    object_type: str,
    source_system: str | None,
) -> str:
    name_upper = object_name.upper()
    if object_type != "RSDS":
        return name_upper
    if not source_system:
        raise ValueError("RSDS dataflow requires source_system")
    return name_upper.ljust(30) + source_system.upper()
