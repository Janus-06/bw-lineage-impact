from __future__ import annotations

import inspect
import os
from collections.abc import Iterator
from uuid import UUID

import httpx
import pytest

import bwli.client as client_module
from bwli.client import ECLIPSE_USER_AGENT, BwClient
from bwli.endpoints import (
    ACCEPT_HEADERS,
    build_adso_endpoint,
    build_dataflow_endpoint,
    build_hcpr_endpoint,
    build_repository_contents_endpoint,
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
    expected = {
        "fetch_search",
        "fetch_dataflow",
        "fetch_xref",
        "fetch_hcpr",
        "fetch_adso",
        "fetch_repository_contents",
    }
    assert expected.issubset(names)


def test_endpoint_builders_use_expected_read_only_paths() -> None:
    search = build_search_endpoint("ADSO", object_type="ADSO")
    dataflow = build_dataflow_endpoint(
        "ZSALES",
        object_type="HCPR",
        direction="both",
        levels=4,
    )
    xref = build_xref_endpoint("ZSALES", object_type="ADSO")
    hcpr = build_hcpr_endpoint("ZSALES")
    adso = build_adso_endpoint("ZSALES_ADSO")
    repository_root = build_repository_contents_endpoint()
    repository_child = build_repository_contents_endpoint("/InfoArea/ZSALES/")

    assert search.path == "/sap/bw/modeling/repo/is/bwsearch"
    assert search.params["searchTerm"] == "ADSO"
    assert search.params["objectType"] == "ADSO"
    assert search.params["searchInName"] == "true"
    assert search.params["searchInDescription"] == "true"
    assert search.params["createdOnFrom"] == "1970-01-01T00:00:00Z"
    assert search.params["createdOnTo"] == "2099-12-31T23:59:59Z"
    assert search.params["changedOnFrom"] == "1970-01-01T00:00:00Z"
    assert search.params["changedOnTo"] == "2099-12-31T23:59:59Z"
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
    assert xref.params == {"objectType": "ADSO", "objectName": "ZSALES"}
    assert "direction" not in xref.params
    assert xref.accept == ACCEPT_HEADERS["xref"]
    assert hcpr.path == "/sap/bw/modeling/hcpr/zsales/m"
    assert hcpr.accept == ACCEPT_HEADERS["hcpr"]
    assert adso.path == "/sap/bw/modeling/adso/zsales_adso/m"
    assert adso.accept == ACCEPT_HEADERS["adso"]
    assert repository_root.path == "/sap/bw/modeling/repo/infoproviderstructure"
    assert repository_root.params == {}
    assert repository_root.accept == ACCEPT_HEADERS["repository"]
    assert (
        repository_child.path
        == "/sap/bw/modeling/repo/infoproviderstructure/infoarea/zsales"
    )
    assert repository_child.params == {}
    assert repository_child.accept == ACCEPT_HEADERS["repository"]


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
        if request.url.path == "/sap/bw/modeling/repo/is/systeminfo":
            return httpx.Response(
                200,
                text="<systeminfo />",
                headers={"x-csrf-token": "csrf-token"},
            )
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
        assert client.fetch_repository_contents("InfoArea/ZSALES") == {"ok": True}
    finally:
        client.close()

    assert seen_methods == ["GET", "GET", "GET", "GET", "GET", "GET", "GET"]
    assert seen_paths == [
        "/sap/bw/modeling/repo/is/systeminfo",
        "/sap/bw/modeling/repo/is/bwsearch",
        "/sap/bw/modeling/dmod/8TRANSIENT",
        "/sap/bw/modeling/repo/is/xref",
        "/sap/bw/modeling/hcpr/zsales/m",
        "/sap/bw/modeling/adso/zsales_adso/m",
        "/sap/bw/modeling/repo/infoproviderstructure/infoarea/zsales",
    ]
    assert seen_accepts == [
        "application/xml",
        ACCEPT_HEADERS["search"],
        ACCEPT_HEADERS["dataflow"],
        "application/xml, application/atom+xml;type=feed",
        ACCEPT_HEADERS["hcpr"],
        ACCEPT_HEADERS["adso"],
        ACCEPT_HEADERS["repository"],
    ]
    assert seen_queries[2]["objecttype"] == "HCPR"
    assert seen_queries[2]["objectname"] == "ZSALES"
    assert seen_queries[2]["leveldownwards"] == "3"
    assert seen_queries[3]["objectType"] == "ADSO"
    assert seen_queries[3]["objectName"] == "ZSALES"
    assert "direction" not in seen_queries[3]


def test_bw_client_fetch_sends_eclipse_adt_headers() -> None:
    seen_headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers)
        if request.url.path == "/sap/bw/modeling/repo/is/systeminfo":
            return httpx.Response(
                200,
                text="<systeminfo />",
                headers={"x-csrf-token": "csrf-token"},
            )
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
    finally:
        client.close()

    assert len(seen_headers) == 2
    bootstrap_headers = seen_headers[0]
    headers = seen_headers[1]
    assert bootstrap_headers["x-csrf-token"] == "Fetch"
    assert bootstrap_headers["accept"] == "application/xml"
    assert headers["user-agent"] == ECLIPSE_USER_AGENT
    assert headers["x-sap-adt-profiling"] == "server-time"
    assert headers["x-sap-adt-sessiontype"] == "stateful"
    assert headers["bwmt-level"] == "50"
    assert headers["sap-client"] == "100"
    assert headers["sap-language"] == "EN"
    assert headers["x-csrf-token"] == "csrf-token"
    UUID(headers["sap-adt-request-id"])


def test_bw_client_xref_uses_configured_sap_context_as_headers_not_query_params() -> None:
    seen_headers: list[httpx.Headers] = []
    seen_queries: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers)
        seen_queries.append(dict(request.url.params.multi_items()))
        if request.url.path == "/sap/bw/modeling/repo/is/systeminfo":
            return httpx.Response(
                200,
                text="<systeminfo />",
                headers={"x-csrf-token": "csrf-token"},
            )
        return httpx.Response(200, text="<feed />")

    client = BwClient(
        base_url="https://bw.example.invalid",
        username="fixture-user",
        password="[REDACTED]",
        sap_client="321",
        language="KO",
        transport=httpx.MockTransport(handler),
    )

    try:
        assert client.fetch_xref("ZSALES", object_type="ADSO") == "<feed />"
    finally:
        client.close()

    assert len(seen_headers) == 2
    bootstrap_headers = seen_headers[0]
    bootstrap_query = seen_queries[0]
    xref_headers = seen_headers[1]
    xref_query = seen_queries[1]
    assert bootstrap_headers["sap-client"] == "321"
    assert bootstrap_headers["sap-language"] == "KO"
    assert "sap-client" not in bootstrap_query
    assert "sap-language" not in bootstrap_query
    assert xref_headers["sap-client"] == "321"
    assert xref_headers["sap-language"] == "KO"
    assert xref_query["objectType"] == "ADSO"
    assert xref_query["objectName"] == "ZSALES"
    assert "sap-client" not in xref_query
    assert "sap-language" not in xref_query


@pytest.mark.parametrize(
    ("call_name", "expected_path"),
    [
        ("fetch_search", "/sap/bw/modeling/repo/is/bwsearch"),
        ("fetch_dataflow", "/sap/bw/modeling/dmod/8TRANSIENT"),
        ("fetch_xref", "/sap/bw/modeling/repo/is/xref"),
    ],
)
def test_bw_client_bootstraps_session_before_first_search_dataflow_xref(
    call_name: str,
    expected_path: str,
) -> None:
    seen_paths: list[str] = []
    seen_tokens: list[str | None] = []
    seen_cookies: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        seen_tokens.append(request.headers.get("x-csrf-token"))
        seen_cookies.append(request.headers.get("cookie"))
        if request.url.path == "/sap/bw/modeling/repo/is/systeminfo":
            return httpx.Response(
                200,
                text="<systeminfo />",
                headers={
                    "x-csrf-token": "bootstrap-token",
                    "set-cookie": "SAP_SESSIONID=bootstrap-session; Path=/; HttpOnly",
                },
            )
        return httpx.Response(200, json={"ok": True})

    client = BwClient(
        base_url="https://bw.example.invalid",
        username="fixture-user",
        password="[REDACTED]",
        sap_client="100",
        language="EN",
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )

    try:
        if call_name == "fetch_search":
            assert client.fetch_search("Z*") == {"ok": True}
        elif call_name == "fetch_dataflow":
            assert client.fetch_dataflow("ZADSO") == {"ok": True}
        else:
            assert client.fetch_xref("ZADSO") == {"ok": True}
    finally:
        client.close()

    assert seen_paths == ["/sap/bw/modeling/repo/is/systeminfo", expected_path]
    assert seen_tokens == ["Fetch", "bootstrap-token"]
    assert seen_cookies[1] == "SAP_SESSIONID=bootstrap-session"


def test_bw_client_reuses_fresh_csrf_token_before_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1_000.0
    monkeypatch.setattr(client_module.time, "monotonic", lambda: now)
    seen_paths: list[str] = []
    seen_tokens: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        seen_tokens.append(request.headers.get("x-csrf-token"))
        if request.url.path == "/sap/bw/modeling/repo/is/systeminfo":
            return httpx.Response(
                200,
                text="<systeminfo />",
                headers={"x-csrf-token": "fresh-token"},
            )
        return httpx.Response(200, json={"ok": True})

    client = BwClient(
        base_url="https://bw.example.invalid",
        username="fixture-user",
        password="[REDACTED]",
        sap_client="100",
        language="EN",
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )

    try:
        assert client.fetch_search("Z*") == {"ok": True}
        now = 1_239.0
        assert client.fetch_dataflow("ZADSO") == {"ok": True}
    finally:
        client.close()

    assert seen_paths == [
        "/sap/bw/modeling/repo/is/systeminfo",
        "/sap/bw/modeling/repo/is/bwsearch",
        "/sap/bw/modeling/dmod/8TRANSIENT",
    ]
    assert seen_tokens == ["Fetch", "fresh-token", "fresh-token"]


def test_bw_client_refetches_csrf_token_after_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 2_000.0
    monkeypatch.setattr(client_module.time, "monotonic", lambda: now)
    seen_paths: list[str] = []
    seen_tokens: list[str | None] = []
    bootstrap_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal bootstrap_count
        seen_paths.append(request.url.path)
        seen_tokens.append(request.headers.get("x-csrf-token"))
        if request.url.path == "/sap/bw/modeling/repo/is/systeminfo":
            bootstrap_count += 1
            return httpx.Response(
                200,
                text="<systeminfo />",
                headers={"x-csrf-token": f"token-{bootstrap_count}"},
            )
        return httpx.Response(200, json={"ok": True})

    client = BwClient(
        base_url="https://bw.example.invalid",
        username="fixture-user",
        password="[REDACTED]",
        sap_client="100",
        language="EN",
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )

    try:
        assert client.fetch_search("Z*") == {"ok": True}
        now = 2_241.0
        assert client.fetch_xref("ZADSO") == {"ok": True}
    finally:
        client.close()

    assert seen_paths == [
        "/sap/bw/modeling/repo/is/systeminfo",
        "/sap/bw/modeling/repo/is/bwsearch",
        "/sap/bw/modeling/repo/is/systeminfo",
        "/sap/bw/modeling/repo/is/xref",
    ]
    assert seen_tokens == ["Fetch", "token-1", "Fetch", "token-2"]


@pytest.mark.parametrize("status_code", [401, 403])
def test_bw_client_refetches_and_retries_get_once_after_auth_failure(
    status_code: int,
) -> None:
    seen_paths: list[str] = []
    seen_tokens: list[str | None] = []
    bootstrap_count = 0
    dataflow_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal bootstrap_count, dataflow_attempts
        seen_paths.append(request.url.path)
        seen_tokens.append(request.headers.get("x-csrf-token"))
        if request.url.path == "/sap/bw/modeling/repo/is/systeminfo":
            bootstrap_count += 1
            return httpx.Response(
                200,
                text="<systeminfo />",
                headers={"x-csrf-token": f"token-{bootstrap_count}"},
            )
        dataflow_attempts += 1
        if dataflow_attempts == 1:
            return httpx.Response(status_code, text="expired session")
        return httpx.Response(200, json={"ok": True})

    client = BwClient(
        base_url="https://bw.example.invalid",
        username="fixture-user",
        password="[REDACTED]",
        sap_client="100",
        language="EN",
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )

    try:
        assert client.fetch_dataflow("ZADSO") == {"ok": True}
    finally:
        client.close()

    assert seen_paths == [
        "/sap/bw/modeling/repo/is/systeminfo",
        "/sap/bw/modeling/dmod/8TRANSIENT",
        "/sap/bw/modeling/repo/is/systeminfo",
        "/sap/bw/modeling/dmod/8TRANSIENT",
    ]
    assert seen_tokens == ["Fetch", "token-1", "Fetch", "token-2"]
    assert bootstrap_count == 2
    assert dataflow_attempts == 2


@pytest.mark.parametrize("status_code", [401, 403])
def test_bw_client_repeated_auth_failure_raises_without_retry_loop(
    status_code: int,
) -> None:
    seen_paths: list[str] = []
    bootstrap_count = 0
    search_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal bootstrap_count, search_attempts
        seen_paths.append(request.url.path)
        if request.url.path == "/sap/bw/modeling/repo/is/systeminfo":
            bootstrap_count += 1
            return httpx.Response(
                200,
                text="<systeminfo />",
                headers={"x-csrf-token": f"token-{bootstrap_count}"},
            )
        search_attempts += 1
        return httpx.Response(status_code, text="still expired")

    client = BwClient(
        base_url="https://bw.example.invalid",
        username="fixture-user",
        password="[REDACTED]",
        sap_client="100",
        language="EN",
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )

    try:
        with pytest.raises(httpx.HTTPStatusError):
            client.fetch_search("Z*")
    finally:
        client.close()

    assert seen_paths == [
        "/sap/bw/modeling/repo/is/systeminfo",
        "/sap/bw/modeling/repo/is/bwsearch",
        "/sap/bw/modeling/repo/is/systeminfo",
        "/sap/bw/modeling/repo/is/bwsearch",
    ]
    assert bootstrap_count == 2
    assert search_attempts == 2


def test_bw_client_auto_adds_bw_host_to_no_proxy_when_trusting_environment(
    monkeypatch,
) -> None:
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")

    client = BwClient(
        base_url="https://bw.example.invalid:443/sap/bw/modeling",
        username="fixture-user",
        password="[REDACTED]",
        sap_client="100",
        language="EN",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"ok": True})),
        trust_env=True,
    )

    try:
        assert os.environ["NO_PROXY"] == "localhost,127.0.0.1,bw.example.invalid"
    finally:
        client.close()


def test_bw_client_leaves_no_proxy_alone_when_environment_is_not_trusted(
    monkeypatch,
) -> None:
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")

    client = BwClient(
        base_url="https://bw.example.invalid:443/sap/bw/modeling",
        username="fixture-user",
        password="[REDACTED]",
        sap_client="100",
        language="EN",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"ok": True})),
        trust_env=False,
    )

    try:
        assert os.environ["NO_PROXY"] == "localhost,127.0.0.1"
    finally:
        client.close()
