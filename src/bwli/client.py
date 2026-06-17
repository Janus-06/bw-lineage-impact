from __future__ import annotations

import os
import time
from collections.abc import MutableMapping
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from bwli.endpoints import (
    DataflowDirection,
    Endpoint,
    build_adso_endpoint,
    build_composite_provider_endpoint,
    build_dataflow_endpoint,
    build_datasource_endpoint,
    build_dtp_endpoint,
    build_get_request_endpoint,
    build_hcpr_endpoint,
    build_list_requests_endpoint,
    build_process_chain_endpoint,
    build_process_variant_endpoint,
    build_query_endpoint,
    build_repository_contents_endpoint,
    build_search_endpoint,
    build_source_system_endpoint,
    build_xref_endpoint,
)

HttpxVerify = bool | str
CSRF_TOKEN_TTL_SECONDS = 240.0
_AUTH_RETRY_STATUSES = {401, 403}

ECLIPSE_USER_AGENT = (
    "Eclipse/4.38.0.v20251201-0920 "
    "(win32; x86_64; Java 21.0.9) ADT/3.56.0 (devedition)"
)
_ADT_PROFILING = "server-time"
_ADT_SESSION_TYPE = "stateful"
_BWMT_LEVEL = "50"


class BwClient:
    """Read-only SAP BW Modeling API client.

    The public surface intentionally exposes only fetch_* methods backed by HTTP GET.
    """

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        sap_client: str,
        language: str = "EN",
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
        verify: HttpxVerify = True,
        trust_env: bool = True,
    ) -> None:
        self._sap_client = sap_client
        self._language = language
        self._csrf_token: str | None = None
        self._csrf_token_fetched_at: float | None = None
        base_url = base_url.rstrip("/")
        if trust_env:
            _ensure_no_proxy_for_url(base_url)
        self._client = httpx.Client(
            base_url=base_url,
            auth=(username, password),
            headers={
                "User-Agent": ECLIPSE_USER_AGENT,
                "X-sap-adt-profiling": _ADT_PROFILING,
                "X-sap-adt-sessiontype": _ADT_SESSION_TYPE,
                "bwmt-level": _BWMT_LEVEL,
                "sap-client": sap_client,
                "sap-language": language,
            },
            transport=transport,
            timeout=timeout,
            verify=verify,
            trust_env=trust_env,
        )

    def fetch_search(self, search_term: str, *, object_type: str | None = None) -> Any:
        return self._fetch(build_search_endpoint(search_term, object_type=object_type))

    def fetch_dataflow(
        self,
        object_name: str,
        *,
        object_type: str = "ADSO",
        source_system: str | None = None,
        direction: DataflowDirection = "downwards",
        levels: int = 3,
    ) -> Any:
        return self._fetch(
            build_dataflow_endpoint(
                object_name,
                object_type=object_type,
                source_system=source_system,
                direction=direction,
                levels=levels,
            )
        )

    def fetch_xref(
        self,
        object_name: str,
        *,
        object_type: str = "ADSO",
        source_system: str | None = None,
    ) -> Any:
        return self._fetch(
            build_xref_endpoint(
                object_name,
                object_type=object_type,
                source_system=source_system,
            )
        )

    def fetch_hcpr(self, object_name: str) -> Any:
        return self._fetch(build_hcpr_endpoint(object_name))

    def fetch_adso(self, object_name: str) -> Any:
        return self._fetch(build_adso_endpoint(object_name))

    def fetch_repository_contents(self, path: str | None = None) -> Any:
        return self._fetch(build_repository_contents_endpoint(path))

    def fetch_process_chain(self, chain_name: str) -> Any:
        return self._fetch(build_process_chain_endpoint(chain_name))

    def fetch_process_variant(self, process_type: str, variant_name: str) -> Any:
        return self._fetch(build_process_variant_endpoint(process_type, variant_name))

    def fetch_dtp(self, dtp_name: str) -> Any:
        return self._fetch(build_dtp_endpoint(dtp_name))

    def fetch_datasource(self, datasource_name: str, source_system: str) -> Any:
        return self._fetch(build_datasource_endpoint(datasource_name, source_system))

    def fetch_source_system(self, source_system: str) -> Any:
        return self._fetch(build_source_system_endpoint(source_system))

    def fetch_query(self, query_name: str) -> Any:
        return self._fetch_with_fallback(
            [
                build_query_endpoint(query_name, active=True),
                build_query_endpoint(query_name, active=False),
            ],
            fallback_statuses={404},
        )

    def fetch_composite_provider(self, object_name: str) -> Any:
        return self._fetch(build_composite_provider_endpoint(object_name))

    def fetch_list_requests(
        self,
        target: str,
        *,
        target_type: str = "ADSO",
        top: int = 3,
        created_from: str | None = None,
    ) -> Any:
        return self._fetch(
            build_list_requests_endpoint(
                target,
                target_type=target_type,
                top=top,
                created_from=created_from,
            )
        )

    def fetch_request(self, request_tsn: str, *, storage: str = "AQ") -> Any:
        return self._fetch(build_get_request_endpoint(request_tsn, storage=storage))

    def close(self) -> None:
        self._client.close()

    def _fetch(self, endpoint: Endpoint) -> Any:
        return self._fetch_with_fallback([endpoint], fallback_statuses=set())

    def _fetch_with_fallback(
        self,
        endpoints: list[Endpoint],
        *,
        fallback_statuses: set[int],
    ) -> Any:
        self._ensure_session()
        response: httpx.Response | None = None
        for index, endpoint in enumerate(endpoints):
            response = self._request_read_with_auth_retry(endpoint)
            is_last = index == len(endpoints) - 1
            if not is_last and response.status_code in fallback_statuses:
                continue
            break
        if response is None:
            raise RuntimeError("no BW endpoint was requested")
        return self._response_payload(response)

    def _request_read_with_auth_retry(self, endpoint: Endpoint) -> httpx.Response:
        response = self._request_read(endpoint)
        if response.status_code not in _AUTH_RETRY_STATUSES:
            return response
        self._clear_session()
        self._ensure_session()
        return self._request_read(endpoint)

    def _response_payload(self, response: httpx.Response) -> Any:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "json" in content_type.lower():
            return response.json()
        try:
            return response.json()
        except ValueError:
            return response.text

    def _request_read(self, endpoint: Endpoint) -> httpx.Response:
        request_headers = {
            "Accept": endpoint.accept,
            "sap-adt-request-id": str(uuid4()),
        }
        request_headers.update(endpoint.headers)
        if self._csrf_token:
            request_headers["X-CSRF-Token"] = self._csrf_token
        response = self._client.request(
            "GET",
            endpoint.path,
            params=endpoint.params,
            headers=request_headers,
        )
        return response

    def _ensure_session(self) -> None:
        if not self._csrf_session_is_stale():
            return
        response = self._client.request(
            "GET",
            "/sap/bw/modeling/repo/is/systeminfo",
            headers={
                "Accept": "application/xml",
                "X-CSRF-Token": "Fetch",
                "sap-adt-request-id": str(uuid4()),
            },
        )
        response.raise_for_status()
        token = response.headers.get("x-csrf-token")
        if not token or token.lower() == "fetch":
            raise RuntimeError(
                "Failed to fetch CSRF token from BW systeminfo "
                f"(HTTP {response.status_code})."
            )
        self._csrf_token = token
        self._csrf_token_fetched_at = time.monotonic()

    def _csrf_session_is_stale(self) -> bool:
        if not self._csrf_token or self._csrf_token_fetched_at is None:
            return True
        return (time.monotonic() - self._csrf_token_fetched_at) > CSRF_TOKEN_TTL_SECONDS

    def _clear_session(self) -> None:
        self._csrf_token = None
        self._csrf_token_fetched_at = None


def _ensure_no_proxy_for_url(
    base_url: str,
    environ: MutableMapping[str, str] | None = None,
) -> None:
    """Append the BW host to process NO_PROXY so env proxies bypass SAP BW."""

    parsed = urlparse(base_url)
    host = parsed.hostname
    if not host:
        return
    env = environ if environ is not None else os.environ
    source_value = env.get("NO_PROXY") or env.get("no_proxy") or ""
    updated = _append_no_proxy_host(source_value, host)
    env["NO_PROXY"] = updated
    if "no_proxy" in env:
        env["no_proxy"] = _append_no_proxy_host(env.get("no_proxy", ""), host)


def _append_no_proxy_host(value: str, host: str) -> str:
    entries = [entry.strip() for entry in value.split(",") if entry.strip()]
    if _no_proxy_covers_host(entries, host):
        return ",".join(entries)
    return ",".join([*entries, host])


def _no_proxy_covers_host(entries: list[str], host: str) -> bool:
    normalized_host = host.strip("[]").lower()
    for entry in entries:
        token = entry.strip().lower()
        if token == "*":
            return True
        if not token:
            continue
        token_without_port = token
        if ":" in token and ":" not in normalized_host:
            token_without_port = token.split(":", 1)[0]
        token_host = token_without_port.lstrip("*.")
        if normalized_host == token_host:
            return True
        if token_without_port.startswith((".", "*.")) and normalized_host.endswith(
            f".{token_host}"
        ):
            return True
    return False
