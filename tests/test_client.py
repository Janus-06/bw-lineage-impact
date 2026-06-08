from __future__ import annotations

import inspect
from collections.abc import Iterator

import httpx

from bwli.client import BwClient
from bwli.endpoints import (
    ACCEPT_HEADERS,
    build_adso_endpoint,
    build_dataflow_endpoint,
    build_hcpr_endpoint,
    build_search_endpoint,
    build_xref_endpoint,
)


def public_method_names(cls: type[object]) -> Iterator[str]:
    for name, _member in inspect.getmembers(cls, predicate=callable):
        if not name.startswith("_"):
            yield name


def test_bw_client_public_surface_is_get_only_read_api() -> None:
    names = set(public_method_names(BwClient))

    forbidden = {
        "post",
        "put",
        "patch",
        "delete",
        "create",
        "update",
        "write",
        "activate",
        "transport",
    }
    assert names.isdisjoint(forbidden)
    expected = {"fetch_search", "fetch_dataflow", "fetch_xref", "fetch_hcpr", "fetch_adso"}
    assert expected.issubset(names)


def test_endpoint_builders_use_expected_read_only_paths() -> None:
    search = build_search_endpoint("ADSO", object_type="ADSO")
    dataflow = build_dataflow_endpoint(
        "ZSALES",
        object_type="HCPR",
        direction="both",
        levels=4,
    )
    xref = build_xref_endpoint("ZSALES", direction="downstream")
    hcpr = build_hcpr_endpoint("ZSALES")
    adso = build_adso_endpoint("ZSALES_ADSO")

    assert search.path == "/sap/bw/modeling/repo/is/bwsearch"
    assert search.params["searchTerm"] == "ADSO"
    assert search.params["objectType"] == "ADSO"
    assert search.accept == ACCEPT_HEADERS["search"]
    assert dataflow.path == "/sap/bw/modeling/dmod/8TRANSIENT"
    assert dataflow.params == {
        "objecttype": "HCPR",
        "objectname": "ZSALES",
        "levelupwards": 4,
        "leveldownwards": 4,
    }
    assert dataflow.accept == ACCEPT_HEADERS["dataflow"]
    assert xref.path == "/sap/bw/modeling/repo/is/xref"
    assert xref.params["direction"] == "downstream"
    assert xref.accept == ACCEPT_HEADERS["xref"]
    assert hcpr.path == "/sap/bw/modeling/hcpr/zsales/m"
    assert hcpr.accept == ACCEPT_HEADERS["hcpr"]
    assert adso.path == "/sap/bw/modeling/adso/zsales_adso/m"
    assert adso.accept == ACCEPT_HEADERS["adso"]


def test_dataflow_endpoint_supports_rsds_source_system_padding() -> None:
    endpoint = build_dataflow_endpoint(
        "ZDS_SALES",
        object_type="RSDS",
        source_system="S4H",
        direction="upwards",
        levels=2,
    )

    assert endpoint.params == {
        "objecttype": "RSDS",
        "objectname": "ZDS_SALES".ljust(30) + "S4H",
        "levelupwards": 2,
    }


def test_bw_client_fetch_uses_http_get_only() -> None:
    seen_methods: list[str] = []
    seen_paths: list[str] = []
    seen_accepts: list[str] = []
    seen_queries: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_methods.append(request.method)
        seen_paths.append(request.url.path)
        seen_accepts.append(request.headers["accept"])
        seen_queries.append(dict(request.url.params.multi_items()))
        return httpx.Response(200, json={"ok": True})

    client = BwClient(
        base_url="https://bw.example.invalid",
        username="fixture-user",
        password="[REDACTED]",
        sap_client="100",
        language="EN",
        transport=httpx.MockTransport(handler),
    )

    try:
        assert client.fetch_search("ADSO") == {"ok": True}
        assert client.fetch_dataflow(
            "ZSALES",
            object_type="HCPR",
            direction="downwards",
            levels=3,
        ) == {"ok": True}
        assert client.fetch_xref("ZSALES") == {"ok": True}
        assert client.fetch_hcpr("ZSALES") == {"ok": True}
        assert client.fetch_adso("ZSALES_ADSO") == {"ok": True}
    finally:
        client.close()

    assert seen_methods == ["GET", "GET", "GET", "GET", "GET"]
    assert seen_paths == [
        "/sap/bw/modeling/repo/is/bwsearch",
        "/sap/bw/modeling/dmod/8TRANSIENT",
        "/sap/bw/modeling/repo/is/xref",
        "/sap/bw/modeling/hcpr/zsales/m",
        "/sap/bw/modeling/adso/zsales_adso/m",
    ]
    assert seen_accepts == [
        ACCEPT_HEADERS["search"],
        ACCEPT_HEADERS["dataflow"],
        ACCEPT_HEADERS["xref"],
        ACCEPT_HEADERS["hcpr"],
        ACCEPT_HEADERS["adso"],
    ]
    assert seen_queries[1]["objecttype"] == "HCPR"
    assert seen_queries[1]["objectname"] == "ZSALES"
    assert seen_queries[1]["leveldownwards"] == "3"
