from __future__ import annotations

import hashlib
from typing import Any

import pytest

from bwli.live import LiveCollectionError, collect_live_snapshot, run_live_smoke
from bwli.snapshot import SnapshotReader
from bwli.store import ingest_manifest


class RecordingLiveClient:
    def __init__(self) -> None:
        self.closed = False

    def fetch_search(self, search_term: str, *, object_type: str | None = None) -> dict[str, Any]:
        return {"term": search_term, "object_type": object_type}

    def fetch_dataflow(
        self,
        object_name: str,
        *,
        object_type: str = "ADSO",
        source_system: str | None = None,
        direction: str = "downwards",
        levels: int = 3,
    ) -> dict[str, Any]:
        return {
            "object": object_name,
            "object_type": object_type,
            "source_system": source_system,
            "direction": direction,
            "levels": levels,
        }

    def fetch_xref(
        self,
        object_name: str,
        *,
        object_type: str = "ADSO",
        source_system: str | None = None,
    ) -> dict[str, Any]:
        return {
            "object": object_name,
            "object_type": object_type,
            "source_system": source_system,
        }

    def fetch_process_chain(self, chain_name: str) -> dict[str, Any]:
        return {"chain": chain_name}

    def fetch_process_variant(self, process_type: str, variant_name: str) -> dict[str, Any]:
        return {"process_type": process_type, "variant": variant_name}

    def fetch_dtp(self, dtp_name: str) -> dict[str, Any]:
        return {"dtp": dtp_name}

    def fetch_datasource(self, datasource_name: str, source_system: str) -> dict[str, Any]:
        return {"datasource": datasource_name, "source_system": source_system}

    def fetch_source_system(self, source_system: str) -> dict[str, Any]:
        return {"source_system": source_system}

    def fetch_query(self, query_name: str) -> dict[str, Any]:
        return {"query": query_name}

    def fetch_composite_provider(self, composite_provider_name: str) -> dict[str, Any]:
        return {"composite_provider": composite_provider_name}

    def fetch_list_requests(
        self,
        target: str,
        *,
        target_type: str = "ADSO",
        top: int = 3,
        created_from: str | None = None,
    ) -> list[dict[str, Any]]:
        return []

    def fetch_request(self, request_tsn: str, *, storage: str = "AQ") -> dict[str, Any]:
        return {"requestTsn": request_tsn, "storage": storage}

    def close(self) -> None:
        self.closed = True


class FlakyLiveClient(RecordingLiveClient):
    def __init__(self, fail_for: set[str], leak_value: str | None = None) -> None:
        super().__init__()
        self._fail_for = fail_for
        self._leak_value = leak_value

    def fetch_dataflow(
        self,
        object_name: str,
        *,
        object_type: str = "ADSO",
        source_system: str | None = None,
        direction: str = "downwards",
        levels: int = 3,
    ) -> Any:
        if object_name in self._fail_for:
            leak = self._leak_value or ""
            raise RuntimeError(
                f"dataflow failed for {object_name} token={leak} url=https://bw.example.invalid/sap/bw"
            )
        return super().fetch_dataflow(
            object_name,
            object_type=object_type,
            source_system=source_system,
            direction=direction,
            levels=levels,
        )


class XmlDataflowClient(RecordingLiveClient):
    def __init__(self, xml: str) -> None:
        super().__init__()
        self._xml = xml

    def fetch_dataflow(
        self,
        object_name: str,
        *,
        object_type: str = "ADSO",
        source_system: str | None = None,
        direction: str = "downwards",
        levels: int = 3,
    ) -> str:
        return self._xml


class MetadataFlakyLiveClient(RecordingLiveClient):
    def __init__(self, leak_value: str) -> None:
        super().__init__()
        self._leak_value = leak_value

    def fetch_source_system(self, source_system: str) -> Any:
        raise RuntimeError(
            f"source system failed token={self._leak_value} url=https://bw.example.invalid/sap/bw"
        )


class RequestFreshnessClient(RecordingLiveClient):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[Any, ...]] = []

    def fetch_list_requests(
        self,
        target: str,
        *,
        target_type: str = "ADSO",
        top: int = 3,
        created_from: str | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append(("list_requests", target, target_type, top, created_from))
        return [
            {
                "requestTsn": "TSN_NEW",
                "requestTsnExternal": "REQ_NEW",
                "storage": "AQ",
                "requestStatus": "G",
                "lastProcessStatus": "G",
                "records": 42,
                "lastTimeStamp": "2026-06-17T12:00:00Z",
            },
            {
                "requestTsn": "TSN_OLD",
                "requestTsnExternal": "REQ_OLD",
                "storage": "AQ",
                "requestStatus": "R",
                "records": 1,
                "lastTimeStamp": "2026-06-16T12:00:00Z",
            },
        ]

    def fetch_request(self, request_tsn: str, *, storage: str = "AQ") -> dict[str, Any]:
        self.calls.append(("get_request", request_tsn, storage))
        return {
            "requestTsn": request_tsn,
            "requestTsnExternal": "REQ_NEW",
            "storage": storage,
            "requestStatus": "G",
            "lastProcessStatus": "G",
            "records": 43,
            "lastTimeStamp": "2026-06-17T12:05:00Z",
            "lastAction": "LOAD",
        }


def test_collect_live_snapshot_uses_unique_payload_paths_for_colliding_labels(tmp_path) -> None:
    first = "Z" * 90 + "A"
    second = "Z" * 90 + "B"
    client = RecordingLiveClient()

    result = collect_live_snapshot(
        out_dir=tmp_path,
        client_factory=lambda: client,
        search_terms=[first, second],
    )

    paths = [payload.relative_path for payload in result.manifest.payloads]
    assert len(paths) == 2
    assert len(set(paths)) == 2
    for payload in result.manifest.payloads:
        encoded = (tmp_path / payload.relative_path).read_bytes()
        assert payload.sha256 == hashlib.sha256(encoded).hexdigest()
    assert client.closed is True
    assert result.succeeded == 2
    assert result.failed == 0


def test_collect_live_snapshot_partial_success_keeps_succeeded_payloads(tmp_path) -> None:
    client = FlakyLiveClient(fail_for={"ZBAD"}, leak_value="redaction-target-secret")

    result = collect_live_snapshot(
        out_dir=tmp_path,
        client_factory=lambda: client,
        object_names=["ZOK", "ZBAD"],
        include_xref=False,
        secret_values=["redaction-target-secret"],
        secret_urls=["https://bw.example.invalid/sap/bw"],
    )

    assert result.succeeded == 1
    assert result.failed == 1
    assert len(result.manifest.payloads) == 1
    assert result.manifest.payloads[0].source.startswith("bw://bw_get_dataflow?")
    assert "objectName=ZOK" in result.manifest.payloads[0].source
    failed_ops = [op for op in result.operations if not op.ok]
    assert len(failed_ops) == 1
    assert "redaction-target-secret" not in failed_ops[0].error
    assert "[REDACTED]" in failed_ops[0].error
    assert "bw.example.invalid" not in failed_ops[0].error


def test_request_freshness_attached_to_provider_node(tmp_path) -> None:
    client = RequestFreshnessClient()

    result = collect_live_snapshot(
        out_dir=tmp_path,
        client_factory=lambda: client,
        object_names=["ZADSO_SALES"],
        include_dataflow=False,
        include_xref=False,
        include_request_freshness=True,
        request_freshness_top=2,
    )

    assert client.closed is True
    assert client.calls == [
        ("list_requests", "ZADSO_SALES", "ADSO", 2, None),
        ("get_request", "TSN_NEW", "AQ"),
    ]
    assert result.succeeded == 2
    assert result.failed == 0
    assert [payload.kind for payload in result.manifest.payloads] == [
        "bw_list_requests",
        "bw_get_request",
    ]
    assert [op.name for op in result.operations] == [
        "bw_list_requests",
        "bw_get_request",
    ]

    reader = SnapshotReader(tmp_path)
    persisted = {payload.kind: reader.read_payload(payload) for payload in result.manifest.payloads}
    assert persisted["bw_list_requests"][0]["requestTsn"] == "TSN_NEW"
    assert persisted["bw_get_request"]["requestTsn"] == "TSN_NEW"
    assert "objectName=ZADSO_SALES" in result.manifest.payloads[0].source
    assert "requestTsn=TSN_NEW" in result.manifest.payloads[1].source

    _, catalog = ingest_manifest(tmp_path / "manifest.json")
    objects = {item.id: item for item in catalog.objects}
    freshness = objects["ZADSO_SALES"].metadata["request_freshness"]
    assert freshness["target"] == "ZADSO_SALES"
    assert freshness["target_type"] == "ADSO"
    assert freshness["latest"]["request_tsn"] == "TSN_NEW"
    assert freshness["latest"]["tsn"] == "REQ_NEW"
    assert freshness["latest"]["status"] == "G"
    assert freshness["latest"]["records"] == 43
    assert freshness["latest"]["timestamp"] == "2026-06-17T12:05:00Z"


def test_collect_live_snapshot_writes_xml_payload_as_xml_file(tmp_path) -> None:
    xml = '<?xml version="1.0"?><dmod:dataFlow><node nodeID="1"/></dmod:dataFlow>'
    client = XmlDataflowClient(xml)

    result = collect_live_snapshot(
        out_dir=tmp_path,
        client_factory=lambda: client,
        object_names=["ZADSO_DEMO"],
        include_xref=False,
    )

    assert result.succeeded == 1
    payload = result.manifest.payloads[0]
    assert payload.relative_path.endswith(".xml")
    stored = (tmp_path / payload.relative_path).read_text(encoding="utf-8")
    assert stored == xml
    reader = SnapshotReader(tmp_path)
    loaded = reader.read_payload(payload)
    assert isinstance(loaded, str)
    assert loaded == xml


def test_collect_live_snapshot_captures_explicit_metadata_reads_and_isolates_failures(
    tmp_path,
) -> None:
    client = MetadataFlakyLiveClient(leak_value="redaction-target-secret")

    result = collect_live_snapshot(
        out_dir=tmp_path,
        client_factory=lambda: client,
        process_chains=["ZCHAIN_SALES"],
        process_variants=[("ABAP", "ZVAR_SALES")],
        dtps=["ZDTP_SALES"],
        datasources=[("ZDS_SALES", "S4H")],
        source_systems=["S4H"],
        queries=["ZQ_SALES"],
        composite_providers=["ZCP_SALES"],
        secret_values=["redaction-target-secret"],
        secret_urls=["https://bw.example.invalid/sap/bw"],
    )

    assert client.closed is True
    assert result.succeeded == 6
    assert result.failed == 1
    assert {payload.kind for payload in result.manifest.payloads} == {
        "bw_get_process_chain",
        "bw_get_process_variant",
        "bw_get_dtp",
        "bw_get_datasource",
        "bw_get_query",
        "bw_get_composite_provider",
    }
    assert {op.name for op in result.operations if op.ok} == {
        "bw_get_process_chain",
        "bw_get_process_variant",
        "bw_get_dtp",
        "bw_get_datasource",
        "bw_get_query",
        "bw_get_composite_provider",
    }
    failed_ops = [op for op in result.operations if not op.ok]
    assert [op.name for op in failed_ops] == ["bw_get_source_system"]
    assert "redaction-target-secret" not in failed_ops[0].error
    assert "[REDACTED]" in failed_ops[0].error
    assert "bw.example.invalid" not in failed_ops[0].error


def test_collect_live_snapshot_raises_when_all_calls_fail(tmp_path) -> None:
    client = FlakyLiveClient(fail_for={"ZBAD"}, leak_value="redaction-target-secret")

    with pytest.raises(LiveCollectionError, match="no payloads collected"):
        collect_live_snapshot(
            out_dir=tmp_path,
            client_factory=lambda: client,
            object_names=["ZBAD"],
            include_xref=False,
            secret_values=["redaction-target-secret"],
        )


def test_run_live_smoke_covers_optional_read_only_metadata() -> None:
    class SmokeClient:
        closed = False
        calls: list[tuple[str, str]]

        def __init__(self) -> None:
            self.calls = []

        def fetch_search(
            self, search_term: str, *, object_type: str | None = None
        ) -> dict[str, object]:
            self.calls.append(("search", search_term))
            return {"objects": []}

        def fetch_dataflow(self, object_name: str, **_: object) -> str:
            self.calls.append(("dataflow", object_name))
            return "<dataflow />"

        def fetch_xref(self, object_name: str, **_: object) -> str:
            self.calls.append(("xref", object_name))
            return "<feed />"

        def fetch_query(self, query_name: str) -> str:
            self.calls.append(("query", query_name))
            return "<query />"

        def fetch_datasource(self, datasource_name: str, source_system: str) -> str:
            self.calls.append(("datasource", f"{datasource_name}/{source_system}"))
            return "<datasource />"

        def fetch_process_chain(self, chain_name: str) -> dict[str, object]:
            self.calls.append(("process_chain", chain_name))
            return {"oHeader": {"sProcessChainId": chain_name}}

        def close(self) -> None:
            self.closed = True

    client = SmokeClient()

    result = run_live_smoke(
        client_factory=lambda: client,
        search_term="Z*",
        object_name="ZADSO_SALES",
        query_name="ZQ_SALES",
        datasource=("ZDS_SALES", "S4H"),
        process_chain="ZCHAIN_SALES",
    )

    assert client.closed is True
    assert result.read_only is True
    assert [op.name for op in result.operations] == [
        "bw_search",
        "bw_get_dataflow",
        "bw_xref",
        "bw_get_query",
        "bw_get_datasource",
        "bw_get_process_chain",
    ]
    assert client.calls == [
        ("search", "Z*"),
        ("dataflow", "ZADSO_SALES"),
        ("xref", "ZADSO_SALES"),
        ("query", "ZQ_SALES"),
        ("datasource", "ZDS_SALES/S4H"),
        ("process_chain", "ZCHAIN_SALES"),
    ]
