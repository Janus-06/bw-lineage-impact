from __future__ import annotations

from typing import Any

import httpx

from bwli.endpoints import (
    DataflowDirection,
    Endpoint,
    build_adso_endpoint,
    build_dataflow_endpoint,
    build_hcpr_endpoint,
    build_search_endpoint,
    build_xref_endpoint,
)

HttpxVerify = bool | str


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
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            auth=(username, password),
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

    def fetch_xref(self, object_name: str, *, direction: str = "downstream") -> Any:
        return self._fetch(build_xref_endpoint(object_name, direction=direction))

    def fetch_hcpr(self, object_name: str) -> Any:
        return self._fetch(build_hcpr_endpoint(object_name))

    def fetch_adso(self, object_name: str) -> Any:
        return self._fetch(build_adso_endpoint(object_name))

    def close(self) -> None:
        self._client.close()

    def _fetch(self, endpoint: Endpoint) -> Any:
        params = dict(endpoint.params)
        params.setdefault("sap-client", self._sap_client)
        params.setdefault("sap-language", self._language)
        response = self._client.request(
            "GET",
            endpoint.path,
            params=params,
            headers={"Accept": endpoint.accept},
        )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "json" in content_type.lower():
            return response.json()
        try:
            return response.json()
        except ValueError:
            return response.text
