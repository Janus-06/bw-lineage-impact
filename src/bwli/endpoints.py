from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import quote

_QUERY_ACCEPT = (
    "application/vnd.sap.bw.modeling.query-v1_8_0+xml, "
    "application/vnd.sap.bw.modeling.query-v1_9_0+xml, "
    "application/vnd.sap.bw.modeling.query-v1_10_0+xml, "
    "application/vnd.sap.bw.modeling.query-v1_11_0+xml"
)

_HCPR_ACCEPT = ",".join(
    [
        "application/vnd.sap.bw.modeling.hcpr-v1_0_0+xml",
        "application/vnd.sap.bw.modeling.hcpr-v1_4_0+xml",
        "application/vnd.sap.bw.modeling.hcpr-v1_7_0+xml",
        "application/vnd.sap.bw.modeling.hcpr-v1_8_0+xml",
        "application/vnd.sap.bw.modeling.hcpr-v1_9_0+xml",
        "application/vnd.sap.bw.modeling.hcpr-v1_10_0+xml",
        "application/vnd.sap.bw.modeling.hcpr-v1_11_0+xml",
        "application/vnd.sap.bw.modeling.hcpr-v1_12_0+xml",
        "application/vnd.sap.bw.modeling.hcpr-v1_13_0+xml",
        "application/vnd.sap.bw.modeling.hcpr-v1_14_0+xml",
        "application/vnd.sap.bw.modeling.hcpr-v1_15_0+xml",
        "application/vnd.sap.bw.modeling.hcpr-v9_99_9+xml",
    ]
)

ACCEPT_HEADERS: dict[str, str] = {
    "search": "application/atom+xml;type=feed",
    "xref": "application/xml, application/atom+xml;type=feed",
    "dataflow": "application/vnd.sap.bw.modeling.dmod-v1_0_0+xml",
    "hcpr": _HCPR_ACCEPT,
    "adso": "application/vnd.sap.bw.modeling.adso-v1_5_0+xml",
    "repository": "application/atom+xml",
    "process_chain": "application/vnd.sap.bw4.modeling.processchain-v1_0_0+json",
    "process_variant": "application/json",
    "dtp": "application/vnd.sap.bw.modeling.dtpa-v1_0_0+xml",
    "datasource": (
        "application/vnd.sap.bw.modeling.rsds-v1_0_0+xml, "
        "application/vnd.sap.bw.modeling.rsds-v1_1_0+xml"
    ),
    "source_system": (
        "application/vnd.sap.bw.modeling.lsys-v1_0_0+xml, "
        "application/vnd.sap.bw.modeling.lsys-v1_1_0+xml"
    ),
    "query": _QUERY_ACCEPT,
    "request_monitor": "*/*",
}

DataflowDirection = Literal["upwards", "downwards", "both"]
_WIDE_DATE_FROM = "1970-01-01T00:00:00Z"
_WIDE_DATE_TO = "2099-12-31T23:59:59Z"
REQUEST_MONITOR_TOP_DEFAULT = 3
REQUEST_MONITOR_TOP_CAP = 20
_REQUEST_MONITOR_STORAGE = "AQ,AX,AT"
_REQUEST_MONITOR_STATUS = "N,GG,GR,YG,RR,YR,RG,U,Y,X"
_REQUEST_MONITOR_HEADERS = {"Content-Type": "application/json"}


@dataclass(frozen=True)
class Endpoint:
    path: str
    params: dict[str, Any]
    accept: str
    headers: dict[str, str] = field(default_factory=dict)


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


def build_process_chain_endpoint(chain_name: str) -> Endpoint:
    return Endpoint(
        path=f"/sap/bw/modeling/rspc/{_url_lower(chain_name)}/m",
        params={},
        accept=ACCEPT_HEADERS["process_chain"],
    )


def build_process_variant_endpoint(process_type: str, variant_name: str) -> Endpoint:
    return Endpoint(
        path=(
            "/sap/bw4/v1/modeling/processtypes/"
            f"{_url_lower(process_type)}/variants/{_url_lower(variant_name)}/m"
        ),
        params={},
        accept=ACCEPT_HEADERS["process_variant"],
    )


def build_dtp_endpoint(dtp_name: str) -> Endpoint:
    return Endpoint(
        path=f"/sap/bw/modeling/dtpa/{_url_lower(dtp_name)}/m",
        params={"forceCacheUpdate": "true"},
        accept=ACCEPT_HEADERS["dtp"],
    )


def build_datasource_endpoint(datasource_name: str, source_system: str) -> Endpoint:
    return Endpoint(
        path=(
            "/sap/bw/modeling/rsds/"
            f"{_url_preserve_case(datasource_name)}/{_url_upper(source_system)}/m"
        ),
        params={},
        accept=ACCEPT_HEADERS["datasource"],
    )


def build_source_system_endpoint(source_system: str) -> Endpoint:
    return Endpoint(
        path=f"/sap/bw/modeling/lsys/{_url_lower(source_system)}/a",
        params={},
        accept=ACCEPT_HEADERS["source_system"],
    )


def build_query_endpoint(query_name: str, *, active: bool = True) -> Endpoint:
    suffix = "a" if active else "m"
    return Endpoint(
        path=f"/sap/bw/modeling/query/{_url_lower(query_name)}/{suffix}",
        params={},
        accept=ACCEPT_HEADERS["query"],
    )


def build_list_requests_endpoint(
    target: str,
    *,
    target_type: str = "ADSO",
    top: int = REQUEST_MONITOR_TOP_DEFAULT,
    created_from: str | None = None,
) -> Endpoint:
    safe_top = _request_monitor_top(top)
    params: dict[str, Any] = {
        "tlogo": target_type.lower(),
        "datatarget": target.lower(),
        "storage": _REQUEST_MONITOR_STORAGE,
        "top": safe_top,
        "status": _REQUEST_MONITOR_STATUS,
    }
    if created_from:
        params["createdfrom"] = created_from
    else:
        params["latestrequests"] = safe_top
    return Endpoint(
        path="/sap/bc/http/sap/bw4/v1/manage/requests",
        params=params,
        accept=ACCEPT_HEADERS["request_monitor"],
        headers=dict(_REQUEST_MONITOR_HEADERS),
    )


def build_get_request_endpoint(request_tsn: str, storage: str = "AQ") -> Endpoint:
    return Endpoint(
        path=(
            "/sap/bc/http/sap/bw4/v1/manage/requests/"
            f"{quote(request_tsn, safe='')}/{_url_lower(storage)}"
        ),
        params={},
        accept=ACCEPT_HEADERS["request_monitor"],
        headers=dict(_REQUEST_MONITOR_HEADERS),
    )


def build_composite_provider_endpoint(object_name: str) -> Endpoint:
    return build_hcpr_endpoint(object_name)


def _request_monitor_top(value: int) -> int:
    if value <= 0:
        return REQUEST_MONITOR_TOP_DEFAULT
    return min(value, REQUEST_MONITOR_TOP_CAP)


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


def _url_lower(value: str) -> str:
    return quote(value.lower(), safe="")


def _url_upper(value: str) -> str:
    return quote(value.upper(), safe="")


def _url_preserve_case(value: str) -> str:
    return quote(value, safe="")
