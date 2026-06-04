from __future__ import annotations

import inspect
from collections.abc import Iterator

import httpx

from bwli.client import BwClient
from bwli.endpoints import build_dataflow_endpoint, build_search_endpoint, build_xref_endpoint


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
    assert {"fetch_search", "fetch_dataflow", "fetch_xref"}.issubset(names)


def test_endpoint_builders_use_expected_read_only_paths() -> None:
    search = build_search_endpoint("ADSO", object_type="ADSO")
    dataflow = build_dataflow_endpoint("ZSALES")
    xref = build_xref_endpoint("ZSALES", direction="downstream")

    assert search.path == "/sap/bw/modeling/repo/is/bwsearch"
    assert search.params["searchTerm"] == "ADSO"
    assert search.params["objectType"] == "ADSO"
    assert dataflow.path == "/sap/bw/modeling/dmod/8TRANSIENT"
    assert dataflow.params["objectName"] == "ZSALES"
    assert xref.path == "/sap/bw/modeling/repo/is/xref"
    assert xref.params["direction"] == "downstream"


def test_bw_client_fetch_uses_http_get_only() -> None:
    seen_methods: list[str] = []
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_methods.append(request.method)
        seen_paths.append(request.url.path)
        return httpx.Response(200, json={"ok": True})

    credential_value = "fakepass"
    client = BwClient(
        base_url="https://bw.example.invalid",
        username="fixture-user",
        password=credential_value,
        sap_client="100",
        language="EN",
        transport=httpx.MockTransport(handler),
    )

    try:
        assert client.fetch_search("ADSO") == {"ok": True}
        assert client.fetch_dataflow("ZSALES") == {"ok": True}
        assert client.fetch_xref("ZSALES") == {"ok": True}
    finally:
        client.close()

    assert seen_methods == ["GET", "GET", "GET"]
    assert seen_paths == [
        "/sap/bw/modeling/repo/is/bwsearch",
        "/sap/bw/modeling/dmod/8TRANSIENT",
        "/sap/bw/modeling/repo/is/xref",
    ]
