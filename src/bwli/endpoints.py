from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ACCEPT_HEADERS: dict[str, str] = {
    "search": "application/atom+xml;type=feed",
    "xref": "application/xml, application/atom+xml;type=feed",
    "dataflow": "application/vnd.sap.bw.modeling.dmod-v1_0_0+xml",
    "hcpr": "application/vnd.sap.bw.modeling.hcpr-v1_15_0+xml",
    "adso": "application/vnd.sap.bw.modeling.adso-v1_5_0+xml",
    "repository": "application/atom+xml",
}

DataflowDirection = Literal["upwards", "downwards", "both"]
_WIDE_DATE_FROM = "1970-01-01T00:00:00Z"
_WIDE_DATE_TO = "2099-12-31T23:59:59Z"


@dataclass(frozen=True)
class Endpoint:
    path: str
    params: dict[str, Any]
    accept: str


def build_search_endpoint(search_term: str, *, object_type: str | None = None) -> Endpoint:
    params: dict[str, Any] = {
        "searchTerm": search_term,
        "searchInName": "true",
        "searchInDescription": "true",
        "objectType": object_type.upper() if object_type else "",
        "createdOnFrom": _WIDE_DATE_FROM,
        "createdOnTo": _WIDE_DATE_TO,
        "changedOnFrom": _WIDE_DATE_FROM,
        "changedOnTo": _WIDE_DATE_TO,
    }
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


def build_xref_endpoint(
    object_name: str,
    *,
    object_type: str = "ADSO",
    source_system: str | None = None,
) -> Endpoint:
    type_upper = object_type.upper()
    return Endpoint(
        path="/sap/bw/modeling/repo/is/xref",
        params={
            "objectType": type_upper,
            "objectName": _dataflow_object_name(
                object_name,
                object_type=type_upper,
                source_system=source_system,
            ),
        },
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


def build_repository_contents_endpoint(path: str | None = None) -> Endpoint:
    normalized = _repository_path(path)
    endpoint_path = "/sap/bw/modeling/repo/infoproviderstructure"
    if normalized:
        endpoint_path = f"{endpoint_path}/{normalized}"
    return Endpoint(
        path=endpoint_path,
        params={},
        accept=ACCEPT_HEADERS["repository"],
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
        raise ValueError("RSDS dataflow/xref requires source_system")
    return name_upper.ljust(30) + source_system.upper()


def _repository_path(path: str | None) -> str:
    value = (path or "").strip().lower().strip("/")
    return value
