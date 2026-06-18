from __future__ import annotations

import inspect
import os
from collections.abc import Iterator
from uuid import UUID

import httpx
import pytest

import bwli.client as client_module
import bwli.endpoints as endpoints_module
from bwli.client import ECLIPSE_USER_AGENT, BwClient
from bwli.endpoints import (
    ACCEPT_HEADERS,
    build_adso_endpoint,
    build_dataflow_endpoint,
    build_get_request_endpoint,
    build_hcpr_endpoint,
    build_list_requests_endpoint,
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
        "run",
        "push",
        "move",
        "unlock",
    }
    assert names.isdisjoint(forbidden)
    assert not {
        name for name in names if any(token in name.lower().split("_") for token in forbidden)
    }
    expected = {
        "fetch_search",
        "fetch_dataflow",
        "fetch_xref",
        "fetch_hcpr",
        "fetch_adso",
        "fetch_repository_contents",
        "fetch_process_chain",
        "fetch_process_variant",
        "fetch_dtp",
        "fetch_datasource",
        "fetch_source_system",
        "fetch_query",
        "fetch_composite_provider",
        "fetch_list_requests",
        "fetch_request",
    }
    assert expected.issubset(names)


def test_run_dtp_and_activate_request_absent_from_surface() -> None:
    names = set(public_method_names(BwClient))

    forbidden_terms = (
        "run_dtp",
        "activate_request",
        "activate",
        "transport",
        "push",
        "write",
        "post",
        "put",
        "patch",
        "delete",
    )

    assert not [name for name in names if any(term in name.lower() for term in forbidden_terms)]


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
    assert repository_child.path == "/sap/bw/modeling/repo/infoproviderstructure/infoarea/zsales"
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


def test_build_process_chain_endpoint_path_and_accept() -> None:
    endpoint = endpoints_module.build_process_chain_endpoint("ZCHAIN_SALES")

    assert endpoint.path == "/sap/bw/modeling/rspc/zchain_sales/m"
    assert endpoint.params == {}
    assert endpoint.accept == ACCEPT_HEADERS["process_chain"]
    assert endpoint.accept == "application/vnd.sap.bw4.modeling.processchain-v1_0_0+json"


def test_build_process_variant_endpoint_path_and_accept() -> None:
    endpoint = endpoints_module.build_process_variant_endpoint("ABAP", "ZVAR_SALES")

    assert endpoint.path == "/sap/bw4/v1/modeling/processtypes/abap/variants/zvar_sales/m"
    assert endpoint.params == {}
    assert endpoint.accept == ACCEPT_HEADERS["process_variant"]
    assert endpoint.accept == "application/json"


def test_build_dtp_endpoint_path_params_and_accept() -> None:
    endpoint = endpoints_module.build_dtp_endpoint("ZDTP_SALES")

    assert endpoint.path == "/sap/bw/modeling/dtpa/zdtp_sales/m"
    assert endpoint.params == {"forceCacheUpdate": "true"}
    assert endpoint.accept == ACCEPT_HEADERS["dtp"]
    assert endpoint.accept == "application/vnd.sap.bw.modeling.dtpa-v1_0_0+xml"


def test_build_datasource_endpoint_path_and_accept() -> None:
    endpoint = endpoints_module.build_datasource_endpoint("ZDS_SALES", "s4h")

    assert endpoint.path == "/sap/bw/modeling/rsds/ZDS_SALES/S4H/m"
    assert endpoint.params == {}
    assert endpoint.accept == ACCEPT_HEADERS["datasource"]
    assert (
        endpoint.accept == "application/vnd.sap.bw.modeling.rsds-v1_0_0+xml, "
        "application/vnd.sap.bw.modeling.rsds-v1_1_0+xml"
    )


def test_build_source_system_endpoint_path_and_accept() -> None:
    endpoint = endpoints_module.build_source_system_endpoint("S4H")

    assert endpoint.path == "/sap/bw/modeling/lsys/s4h/a"
    assert endpoint.params == {}
    assert endpoint.accept == ACCEPT_HEADERS["source_system"]
    assert (
        endpoint.accept == "application/vnd.sap.bw.modeling.lsys-v1_0_0+xml, "
        "application/vnd.sap.bw.modeling.lsys-v1_1_0+xml"
    )


def test_build_query_endpoint_path_and_versioned_accept() -> None:
    active = endpoints_module.build_query_endpoint("ZQ_SALES")
    inactive = endpoints_module.build_query_endpoint("ZQ_SALES", active=False)

    assert active.path == "/sap/bw/modeling/query/zq_sales/a"
    assert inactive.path == "/sap/bw/modeling/query/zq_sales/m"
    assert active.params == {}
    assert inactive.params == {}
    assert active.accept == ACCEPT_HEADERS["query"]
    assert active.accept == inactive.accept
    for version in ("v1_8_0", "v1_9_0", "v1_10_0", "v1_11_0"):
        assert f"application/vnd.sap.bw.modeling.query-{version}+xml" in active.accept


def test_build_composite_provider_endpoint_aliases_hcpr() -> None:
    endpoint = endpoints_module.build_composite_provider_endpoint("ZCP_SALES")
    hcpr = build_hcpr_endpoint("ZCP_SALES")

    assert endpoint == hcpr
    assert endpoint.path == "/sap/bw/modeling/hcpr/zcp_sales/m"
    assert endpoint.accept == ACCEPT_HEADERS["hcpr"]
    for version in ("v1_0_0", "v1_10_0", "v1_15_0", "v9_99_9"):
        assert f"application/vnd.sap.bw.modeling.hcpr-{version}+xml" in endpoint.accept


def test_endpoints_get_only_for_runtime_request_monitor() -> None:
    capped = build_list_requests_endpoint("ZADSO_SALES", target_type="ADSO", top=999)
    defaulted = build_list_requests_endpoint("ZADSO_SALES", target_type="ADSO", top=0)
    bounded = build_list_requests_endpoint(
        "ZADSO_SALES",
        target_type="HCPR",
        top=5,
        created_from="2026-01-01T00:00:00Z",
    )
    request = build_get_request_endpoint("REQ TSN", storage="AX")

    assert capped.path == "/sap/bc/http/sap/bw4/v1/manage/requests"
    assert capped.params["tlogo"] == "adso"
    assert capped.params["datatarget"] == "zadso_sales"
    assert capped.params["storage"] == "AQ,AX,AT"
    assert capped.params["latestrequests"] == 20
    assert capped.params["top"] == 20
    assert capped.params["status"] == "N,GG,GR,YG,RR,YR,RG,U,Y,X"
    assert capped.accept == "*/*"
    assert capped.headers == {"Content-Type": "application/json"}

    assert defaulted.params["latestrequests"] == 3
    assert defaulted.params["top"] == 3

    assert bounded.params["tlogo"] == "hcpr"
    assert bounded.params["createdfrom"] == "2026-01-01T00:00:00Z"
    assert "latestrequests" not in bounded.params
    assert bounded.params["top"] == 5

    assert request.path == "/sap/bc/http/sap/bw4/v1/manage/requests/REQ%20TSN/ax"
    assert request.params == {}
    assert request.accept == "*/*"
    assert request.headers == {"Content-Type": "application/json"}


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
        assert client.fetch_process_chain("ZCHAIN_SALES") == {"ok": True}
        assert client.fetch_process_variant("ABAP", "ZVAR_SALES") == {"ok": True}
        assert client.fetch_dtp("ZDTP_SALES") == {"ok": True}
        assert client.fetch_datasource("ZDS_SALES", "S4H") == {"ok": True}
        assert client.fetch_source_system("S4H") == {"ok": True}
        assert client.fetch_query("ZQ_SALES") == {"ok": True}
        assert client.fetch_composite_provider("ZCP_SALES") == {"ok": True}
    finally:
        client.close()

    assert seen_methods == ["GET"] * 14
    assert seen_paths == [
        "/sap/bw/modeling/repo/is/systeminfo",
        "/sap/bw/modeling/repo/is/bwsearch",
        "/sap/bw/modeling/dmod/8TRANSIENT",
        "/sap/bw/modeling/repo/is/xref",
        "/sap/bw/modeling/hcpr/zsales/m",
        "/sap/bw/modeling/adso/zsales_adso/m",
        "/sap/bw/modeling/repo/infoproviderstructure/infoarea/zsales",
        "/sap/bw/modeling/rspc/zchain_sales/m",
        "/sap/bw4/v1/modeling/processtypes/abap/variants/zvar_sales/m",
        "/sap/bw/modeling/dtpa/zdtp_sales/m",
        "/sap/bw/modeling/rsds/ZDS_SALES/S4H/m",
        "/sap/bw/modeling/lsys/s4h/a",
        "/sap/bw/modeling/query/zq_sales/a",
        "/sap/bw/modeling/hcpr/zcp_sales/m",
    ]
    assert seen_accepts == [
        "application/xml",
        ACCEPT_HEADERS["search"],
        ACCEPT_HEADERS["dataflow"],
        "application/xml, application/atom+xml;type=feed",
        ACCEPT_HEADERS["hcpr"],
        ACCEPT_HEADERS["adso"],
        ACCEPT_HEADERS["repository"],
        ACCEPT_HEADERS["process_chain"],
        ACCEPT_HEADERS["process_variant"],
        ACCEPT_HEADERS["dtp"],
        ACCEPT_HEADERS["datasource"],
        ACCEPT_HEADERS["source_system"],
        ACCEPT_HEADERS["query"],
        ACCEPT_HEADERS["hcpr"],
    ]
    assert seen_queries[2]["objecttype"] == "HCPR"
    assert seen_queries[2]["objectname"] == "ZSALES"
    assert seen_queries[2]["leveldownwards"] == "3"
    assert seen_queries[3]["objectType"] == "ADSO"
    assert seen_queries[3]["objectName"] == "ZSALES"
    assert "direction" not in seen_queries[3]
    assert seen_queries[9] == {"forceCacheUpdate": "true"}


def test_fetch_list_requests_get_only_with_top_cap() -> None:
    seen_methods: list[str] = []
    seen_paths: list[str] = []
    seen_queries: list[dict[str, str]] = []
    seen_headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_methods.append(request.method)
        seen_paths.append(request.url.path)
        seen_queries.append(dict(request.url.params.multi_items()))
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
        trust_env=False,
    )

    try:
        assert client.fetch_list_requests("ZADSO_SALES", top=999) == {"ok": True}
        assert client.fetch_request("REQ_TSN", storage="AX") == {"ok": True}
    finally:
        client.close()

    assert seen_methods == ["GET", "GET", "GET"]
    assert seen_paths == [
        "/sap/bw/modeling/repo/is/systeminfo",
        "/sap/bc/http/sap/bw4/v1/manage/requests",
        "/sap/bc/http/sap/bw4/v1/manage/requests/REQ_TSN/ax",
    ]
    list_query = seen_queries[1]
    assert list_query["tlogo"] == "adso"
    assert list_query["datatarget"] == "zadso_sales"
    assert list_query["storage"] == "AQ,AX,AT"
    assert list_query["latestrequests"] == "20"
    assert list_query["top"] == "20"
    assert list_query["status"] == "N,GG,GR,YG,RR,YR,RG,U,Y,X"
    assert seen_headers[1]["accept"] == "*/*"
    assert seen_headers[1]["content-type"] == "application/json"
    assert seen_headers[2]["accept"] == "*/*"
    assert seen_headers[2]["content-type"] == "application/json"


def test_bw_client_fetch_query_falls_back_to_inactive_metadata_on_404() -> None:
    seen_paths: list[str] = []
    seen_methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        seen_methods.append(request.method)
        if request.url.path == "/sap/bw/modeling/repo/is/systeminfo":
            return httpx.Response(
                200,
                text="<systeminfo />",
                headers={"x-csrf-token": "csrf-token"},
            )
        if request.url.path.endswith("/a"):
            return httpx.Response(404, text="active query not found")
        return httpx.Response(
            200,
            text='<Qry:queryResource technicalName="ZQ_SALES" />',
            headers={"content-type": ACCEPT_HEADERS["query"]},
        )

    client = BwClient(
        base_url="https://bw.example.invalid",
        username="fixture-user",
        password="[REDACTED]",
        sap_client="100",
        language="EN",
        transport=httpx.MockTransport(handler),
    )

    try:
        assert client.fetch_query("ZQ_SALES") == '<Qry:queryResource technicalName="ZQ_SALES" />'
    finally:
        client.close()

    assert seen_methods == ["GET", "GET", "GET"]
    assert seen_paths == [
        "/sap/bw/modeling/repo/is/systeminfo",
        "/sap/bw/modeling/query/zq_sales/a",
        "/sap/bw/modeling/query/zq_sales/m",
    ]


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


@pytest.mark.parametrize(
    "base_url",
    [
        "https://url-user@bw.example.invalid",
        "https://url-user:url-password@bw.example.invalid",
        "https://:url-password@bw.example.invalid",
    ],
)
def test_cookie_auth_rejects_url_userinfo_without_requesting(base_url: str) -> None:
    seen_headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers)
        return httpx.Response(200, json={"ok": True})

    with pytest.raises(ValueError, match="embedded credentials in cookie mode") as exc_info:
        BwClient(
            base_url=base_url,
            username=None,
            password=None,
            sap_client="100",
            language="EN",
            initial_cookies={"SAP_SESSIONID": "file-session"},
            transport=httpx.MockTransport(handler),
            trust_env=False,
        )

    assert seen_headers == []
    message = str(exc_info.value)
    assert "url-user" not in message
    assert "url-password" not in message
    assert "bw.example.invalid" not in message


def test_cookie_auth_get_only_no_csrf_when_frozen() -> None:
    seen_methods: list[str] = []
    seen_paths: list[str] = []
    seen_headers: list[httpx.Headers] = []
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        seen_methods.append(request.method)
        seen_paths.append(request.url.path)
        seen_headers.append(request.headers)
        assert request.url.path != "/sap/bw/modeling/repo/is/systeminfo"
        if attempts == 1:
            return httpx.Response(
                200,
                json={"ok": True},
                headers=[
                    ("set-cookie", "SAP_SESSIONID=server-session; Path=/; HttpOnly"),
                    ("set-cookie", "NEW_COOKIE=new-value; Path=/; HttpOnly"),
                ],
            )
        return httpx.Response(200, json={"ok": True})

    client = BwClient(
        base_url="https://bw.example.invalid",
        username=None,
        password=None,
        sap_client="100",
        language="EN",
        initial_cookies={
            "SAP_SESSIONID": "file-session",
            "__VCAP_ID__": "app-instance",
        },
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )

    try:
        assert client.fetch_search("ADSO") == {"ok": True}
        assert client.fetch_search("ADSO") == {"ok": True}
    finally:
        client.close()

    assert seen_methods == ["GET", "GET"]
    assert seen_paths == [
        "/sap/bw/modeling/repo/is/bwsearch",
        "/sap/bw/modeling/repo/is/bwsearch",
    ]
    for headers in seen_headers:
        assert "authorization" not in headers
        assert "sap-client" not in headers
        assert "x-sap-adt-sessiontype" not in headers
        assert "x-csrf-token" not in headers
        assert headers["sap-language"] == "EN"
        assert headers["bwmt-level"] == "50"
    assert seen_headers[0]["cookie"] == "SAP_SESSIONID=file-session; __VCAP_ID__=app-instance"
    assert "SAP_SESSIONID=file-session" in seen_headers[1]["cookie"]
    assert "SAP_SESSIONID=server-session" not in seen_headers[1]["cookie"]
    assert "__VCAP_ID__=app-instance" in seen_headers[1]["cookie"]
    assert "NEW_COOKIE=new-value" in seen_headers[1]["cookie"]


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


def test_query_accept_prefers_discovered_media_type_then_static_range() -> None:
    accept = endpoints_module.negotiate_accept(
        "query",
        discovered="application/vnd.sap.bw.modeling.query-v1_12_0+xml",
    )

    assert accept.startswith("application/vnd.sap.bw.modeling.query-v1_12_0+xml, ")
    assert "application/vnd.sap.bw.modeling.query-v1_8_0+xml" in accept
    assert accept.count("application/vnd.sap.bw.modeling.query-v1_12_0+xml") == 1


def test_bw_client_fetch_query_negotiates_on_406_415_then_404() -> None:
    seen_paths: list[str] = []
    seen_methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        seen_methods.append(request.method)
        if request.url.path == "/sap/bw/modeling/repo/is/systeminfo":
            return httpx.Response(
                200,
                text="<systeminfo />",
                headers={"x-csrf-token": "csrf-token"},
            )
        if request.url.path.endswith("/a"):
            return httpx.Response(406, text="media type rejected")
        return httpx.Response(
            200,
            text='<Qry:queryResource technicalName="ZQ_SALES" />',
            headers={"content-type": ACCEPT_HEADERS["query"]},
        )

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
        assert client.fetch_query("ZQ_SALES") == '<Qry:queryResource technicalName="ZQ_SALES" />'
    finally:
        client.close()

    assert seen_methods == ["GET", "GET", "GET"]
    assert seen_paths == [
        "/sap/bw/modeling/repo/is/systeminfo",
        "/sap/bw/modeling/query/zq_sales/a",
        "/sap/bw/modeling/query/zq_sales/m",
    ]
