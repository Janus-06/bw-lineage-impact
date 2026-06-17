from __future__ import annotations

from pathlib import Path

import pytest

from bwli.snapshot import SnapshotWriter
from bwli.store import CatalogStore, ingest_fixture_payload, ingest_manifest


def _skip_if_file_symlink_unavailable(tmp_path: Path) -> None:
    target = tmp_path / "symlink-probe-target.txt"
    link = tmp_path / "symlink-probe-link.txt"
    target.write_text("probe", encoding="utf-8")
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"file symlinks are unavailable in this environment: {exc}")
    finally:
        if link.is_symlink() or link.exists():
            link.unlink()
        if target.exists():
            target.unlink()


def _write_manifest_payload(
    tmp_path: Path,
    *,
    kind: str,
    payload_id: str,
    payload: object,
    source: str | None = None,
) -> Path:
    writer = SnapshotWriter(tmp_path)
    metadata = writer.write_payload(
        payload_id=payload_id,
        kind=kind,
        source=source or f"bw://{kind}",
        payload=payload,
    )
    writer.write_manifest(mode="live-read-only", payloads=[metadata])
    return tmp_path / "manifest.json"


def test_ingest_manifest_dispatches_xml_search_by_manifest_kind(tmp_path: Path) -> None:
    manifest_path = _write_manifest_payload(
        tmp_path,
        kind="bw_search",
        payload_id="search-xml",
        payload="""
        <searchResults>
          <object technicalName="ZQ_SALES" objectType="QUERY" description="Sales query"/>
        </searchResults>
        """,
    )

    _, catalog = ingest_manifest(manifest_path)

    assert [(item.id, item.type, item.name) for item in catalog.objects] == [
        ("ZQ_SALES", "QUERY", "Sales query")
    ]
    assert catalog.edges == []


@pytest.mark.parametrize(
    "payload",
    [
        {
            "results": [
                {
                    "id": "SHOULD_NOT_BECOME_SEARCH_OBJECT",
                    "sourceObject": "SRC_ADSO",
                    "targetObject": "TGT_QUERY",
                }
            ]
        },
        """
        <xref>
          <reference id="SHOULD_NOT_BECOME_SEARCH_OBJECT"
                     sourceObject="SRC_ADSO"
                     targetObject="TGT_QUERY" />
        </xref>
        """,
    ],
)
def test_ingest_manifest_dispatches_xref_by_manifest_kind(
    tmp_path: Path,
    payload: object,
) -> None:
    manifest_path = _write_manifest_payload(
        tmp_path,
        kind="bw_xref",
        payload_id="xref-payload",
        payload=payload,
    )

    _, catalog = ingest_manifest(manifest_path)

    assert [(edge.source, edge.target, edge.type) for edge in catalog.edges] == [
        ("SRC_ADSO", "TGT_QUERY", "xref")
    ]
    assert {item.id for item in catalog.objects} == {"SRC_ADSO", "TGT_QUERY"}


def test_ingest_manifest_reads_explicit_manifest_filename(tmp_path: Path) -> None:
    manifest_path = _write_manifest_payload(
        tmp_path,
        kind="bw_search",
        payload_id="search-selected",
        payload={"objects": [{"technicalName": "Z_SELECTED", "objectType": "QUERY"}]},
    )
    selected_path = tmp_path / "selected-manifest.json"
    manifest_path.replace(selected_path)

    _, catalog = ingest_manifest(selected_path)

    assert [item.id for item in catalog.objects] == ["Z_SELECTED"]


def test_ingest_manifest_links_atom_xref_entries_to_requested_object(tmp_path: Path) -> None:
    manifest_path = _write_manifest_payload(
        tmp_path,
        kind="bw_xref",
        payload_id="xref-0-ZSRC-acab96cdce9c-downstream",
        source="bw://bw_xref/downstream?objectName=ZSRC",
        payload="""
        <atom:feed xmlns:atom="http://www.w3.org/2005/Atom"
                   xmlns:bwModel="urn:sap:bw:model">
          <atom:entry>
            <atom:title>Target Query</atom:title>
            <bwModel:object objectName="ZQ_TARGET" objectType="QUERY" objectStatus="active" />
          </atom:entry>
        </atom:feed>
        """,
    )

    _, catalog = ingest_manifest(manifest_path)

    assert [(edge.source, edge.target, edge.type) for edge in catalog.edges] == [
        ("ZSRC", "ZQ_TARGET", "xref")
    ]
    objects = {item.id: item for item in catalog.objects}
    assert objects["ZQ_TARGET"].type == "QUERY"
    assert objects["ZQ_TARGET"].name == "Target Query"


def test_ingest_manifest_strips_safe_fragment_hash_from_legacy_xref_payload_id(
    tmp_path: Path,
) -> None:
    manifest_path = _write_manifest_payload(
        tmp_path,
        kind="bw_xref",
        payload_id="xref-0-ZSRC-acab96cdce9c-downstream",
        source="bw://bw_xref/downstream",
        payload="""
        <atom:feed xmlns:atom="http://www.w3.org/2005/Atom"
                   xmlns:bwModel="urn:sap:bw:model">
          <atom:entry>
            <atom:title>Target Query</atom:title>
            <bwModel:object objectName="ZQ_TARGET" objectType="QUERY" />
          </atom:entry>
        </atom:feed>
        """,
    )

    _, catalog = ingest_manifest(manifest_path)

    assert [(edge.source, edge.target, edge.type) for edge in catalog.edges] == [
        ("ZSRC", "ZQ_TARGET", "xref")
    ]


def test_ingest_manifest_links_upstream_atom_xref_to_requested_object(tmp_path: Path) -> None:
    manifest_path = _write_manifest_payload(
        tmp_path,
        kind="bw_xref",
        payload_id="xref-0-ZTARGET-upstream",
        source="bw://bw_xref/upstream",
        payload="""
        <atom:feed xmlns:atom="http://www.w3.org/2005/Atom"
                   xmlns:bwModel="urn:sap:bw:model">
          <atom:entry>
            <atom:title>Source ADSO</atom:title>
            <bwModel:object objectName="ZSRC" objectType="ADSO" objectStatus="active" />
          </atom:entry>
        </atom:feed>
        """,
    )

    _, catalog = ingest_manifest(manifest_path)

    assert [(edge.source, edge.target, edge.type) for edge in catalog.edges] == [
        ("ZSRC", "ZTARGET", "xref")
    ]
    objects = {item.id: item for item in catalog.objects}
    assert objects["ZSRC"].type == "ADSO"
    assert objects["ZSRC"].name == "Source ADSO"


def test_ingest_manifest_merges_duplicate_dataflow_object_names_before_storage(
    tmp_path: Path,
) -> None:
    manifest_path = _write_manifest_payload(
        tmp_path,
        kind="bw_get_dataflow",
        payload_id="dataflow-xml",
        payload="""
        <dataflow>
          <node nodeID="1" objectName="ZADSO_DUP" objectType="ADSO">
            <targetNode>#///2</targetNode>
          </node>
          <node nodeID="2" objectName="ZADSO_DUP" objectType="ADSO">
            <sourceNode>#///1</sourceNode>
          </node>
        </dataflow>
        """,
    )

    _, catalog = ingest_manifest(manifest_path)

    assert [item.id for item in catalog.objects] == ["ZADSO_DUP"]
    assert catalog.objects[0].evidence_ids == [
        "dataflow-xml:dataflow-node:1",
        "dataflow-xml:dataflow-node:2",
    ]

    store = CatalogStore(tmp_path / "catalog.sqlite")
    snapshot = store.create_snapshot(mode="test", source="fixture://duplicate-dataflow")
    store.replace_catalog(snapshot.id, objects=catalog.objects, edges=catalog.edges)
    stored = store.get_object(snapshot.id, "ZADSO_DUP")
    assert stored is not None
    assert stored.incoming_count == 1
    assert stored.outgoing_count == 1


def test_ingest_manifest_process_chain_json_adds_chain_variants_and_sequence(
    tmp_path: Path,
) -> None:
    manifest_path = _write_manifest_payload(
        tmp_path,
        kind="bw_get_process_chain",
        payload_id="process-chain-json",
        payload={
            "oHeader": {
                "sProcessChainId": "ZCHAIN_SALES",
                "sDescription": "Sales load chain",
            },
            "aNode": [
                {
                    "sProcessType": "DTP_LOAD",
                    "sProcessVariant": "ZDTP_SALES",
                    "sVariantDescription": "Load sales DTP",
                },
                {
                    "sProcessType": "ABAP",
                    "sProcessVariant": "ZABAP_STEP",
                    "sVariantDescription": "Finalize sales",
                },
            ],
            "aEdge": [{"iNodeIndexFrom": 0, "iNodeIndexTo": 1}],
        },
    )

    _, catalog = ingest_manifest(manifest_path)

    objects = {item.id: item for item in catalog.objects}
    assert objects["ZCHAIN_SALES"].type == "RSPC"
    assert objects["ZCHAIN_SALES"].name == "Sales load chain"
    assert objects["ZDTP_SALES"].type == "DTP_LOAD"
    assert objects["ZABAP_STEP"].type == "ABAP"
    assert {(edge.source, edge.target, edge.type) for edge in catalog.edges} == {
        ("ZCHAIN_SALES", "ZABAP_STEP", "contains"),
        ("ZCHAIN_SALES", "ZDTP_SALES", "contains"),
        ("ZDTP_SALES", "ZABAP_STEP", "sequence"),
    }


def test_ingest_manifest_process_variant_json_preserves_process_type(
    tmp_path: Path,
) -> None:
    manifest_path = _write_manifest_payload(
        tmp_path,
        kind="bw_get_process_variant",
        payload_id="process-variant-json",
        source="bw://bw_get_process_variant?processType=ABAP&variantName=ZVAR_SALES",
        payload={
            "bActive": True,
            "sVariantDescription": "Run sales finalizer",
            "oDetail": {"PROGRAM": [{"key": "ZSALES_FINALIZE"}]},
        },
    )

    _, catalog = ingest_manifest(manifest_path)

    assert [(item.id, item.type, item.name) for item in catalog.objects] == [
        ("ZVAR_SALES", "ABAP", "Run sales finalizer")
    ]
    assert catalog.objects[0].metadata["active"] is True


def test_ingest_manifest_dtp_xml_adds_dtp_source_target_and_transformation(
    tmp_path: Path,
) -> None:
    manifest_path = _write_manifest_payload(
        tmp_path,
        kind="bw_get_dtp",
        payload_id="dtp-xml",
        payload="""
        <dtpa:dataTransferProcess xmlns:dtpa="urn:sap:bw:dtpa"
            name="ZDTP_SALES"
            description="Load sales"
            sourceObjectName="ZDS_SALES"
            sourceObjectType="RSDS"
            sourceSystemName="S4H"
            targetObjectName="ZADSO_SALES"
            targetObjectType="ADSO"
            transformationName="ZTRFN_SALES" />
        """,
    )

    _, catalog = ingest_manifest(manifest_path)

    objects = {item.id: item for item in catalog.objects}
    assert objects["ZDTP_SALES"].type == "DTPA"
    assert objects["ZDS_SALES"].type == "RSDS"
    assert objects["ZDS_SALES"].metadata["source_system"] == "S4H"
    assert objects["ZADSO_SALES"].type == "ADSO"
    assert objects["ZTRFN_SALES"].type == "TRFN"
    edge_triples = {(edge.source, edge.target, edge.type) for edge in catalog.edges}
    assert ("ZDS_SALES", "ZDTP_SALES", "dataflow") in edge_triples
    assert ("ZDTP_SALES", "ZADSO_SALES", "dataflow") in edge_triples
    assert any("ZTRFN_SALES" in (edge.source, edge.target) for edge in catalog.edges)


def test_ingest_manifest_dtp_xml_reads_child_source_target_and_overview_transform(
    tmp_path: Path,
) -> None:
    manifest_path = _write_manifest_payload(
        tmp_path,
        kind="bw_get_dtp",
        payload_id="dtp-child-xml",
        payload="""
        <dtpa:dataTransferProcess xmlns:dtpa="urn:sap:bw:dtpa"
            name="ZDTP_SALES"
            description="Load sales">
          <dtpa:source type="RSDS" name="ZDS_SALES" sourceSystemName="S4H" />
          <dtpa:target type="ADSO" name="ZADSO_SALES" />
          <dtpa:overview>
            <dtpa:object name="ZTRFN_SALES" description="Sales transformation" />
          </dtpa:overview>
        </dtpa:dataTransferProcess>
        """,
    )

    _, catalog = ingest_manifest(manifest_path)

    objects = {item.id: item for item in catalog.objects}
    assert objects["ZDTP_SALES"].type == "DTPA"
    assert objects["ZDS_SALES"].type == "RSDS"
    assert objects["ZDS_SALES"].metadata["source_system"] == "S4H"
    assert objects["ZADSO_SALES"].type == "ADSO"
    assert objects["ZTRFN_SALES"].type == "TRFN"
    assert objects["ZTRFN_SALES"].name == "Sales transformation"
    assert {(edge.source, edge.target, edge.type) for edge in catalog.edges} == {
        ("ZDS_SALES", "ZDTP_SALES", "dataflow"),
        ("ZDTP_SALES", "ZADSO_SALES", "dataflow"),
        ("ZDTP_SALES", "ZTRFN_SALES", "uses_transformation"),
    }


def test_ingest_manifest_datasource_xml_preserves_rsds_and_fields(
    tmp_path: Path,
) -> None:
    manifest_path = _write_manifest_payload(
        tmp_path,
        kind="bw_get_datasource",
        payload_id="datasource-xml",
        payload="""
        <rsds:dataSource xmlns:rsds="urn:sap:bw:rsds"
            name="ZDS_SALES"
            description="Sales datasource"
            sourceSystemName="S4H"
            type="TRANSACTION_DATA">
          <rsds:field name="CUSTOMER" description="Customer" type="CHAR" length="10" />
          <rsds:field name="AMOUNT" description="Amount" type="DEC" length="17" />
        </rsds:dataSource>
        """,
    )

    _, catalog = ingest_manifest(manifest_path)

    assert [(item.id, item.type, item.name) for item in catalog.objects] == [
        ("ZDS_SALES", "RSDS", "Sales datasource")
    ]
    metadata = catalog.objects[0].metadata
    assert metadata["source_system"] == "S4H"
    assert metadata["datasource_type"] == "TRANSACTION_DATA"
    assert metadata["fields"] == [
        {"name": "CUSTOMER", "description": "Customer", "type": "CHAR", "length": "10"},
        {"name": "AMOUNT", "description": "Amount", "type": "DEC", "length": "17"},
    ]


def test_ingest_manifest_source_system_xml_preserves_lsys(
    tmp_path: Path,
) -> None:
    manifest_path = _write_manifest_payload(
        tmp_path,
        kind="bw_get_source_system",
        payload_id="source-system-xml",
        payload="""
        <lsys:sourceSystem xmlns:lsys="urn:sap:bw:lsys"
            name="S4H"
            description="S/4HANA source"
            type="ODP" />
        """,
    )

    _, catalog = ingest_manifest(manifest_path)

    assert [(item.id, item.type, item.name) for item in catalog.objects] == [
        ("S4H", "LSYS", "S/4HANA source")
    ]
    assert catalog.objects[0].metadata["source_system_type"] == "ODP"


def test_ingest_manifest_query_xml_adds_provider_from_related_link(
    tmp_path: Path,
) -> None:
    manifest_path = _write_manifest_payload(
        tmp_path,
        kind="bw_get_query",
        payload_id="query-xml",
        payload="""
        <Qry:queryResource xmlns:Qry="urn:sap:bw:query"
            xmlns:atom="http://www.w3.org/2005/Atom"
            technicalName="ZQ_SALES"
            description="Sales query">
          <atom:link rel="related" href="/sap/bw/modeling/hcpr/zcp_sales/m" />
        </Qry:queryResource>
        """,
    )

    _, catalog = ingest_manifest(manifest_path)

    objects = {item.id: item for item in catalog.objects}
    assert objects["ZQ_SALES"].type == "QUERY"
    assert objects["ZQ_SALES"].name == "Sales query"
    assert objects["ZCP_SALES"].type == "HCPR"
    assert [(edge.source, edge.target, edge.type) for edge in catalog.edges] == [
        ("ZCP_SALES", "ZQ_SALES", "provides")
    ]


def test_ingest_manifest_query_xml_ignores_self_link_provider_edges(
    tmp_path: Path,
) -> None:
    manifest_path = _write_manifest_payload(
        tmp_path,
        kind="bw_get_query",
        payload_id="query-self-and-related-links",
        payload="""
        <Qry:queryResource xmlns:Qry="urn:sap:bw:query"
            xmlns:atom="http://www.w3.org/2005/Atom"
            technicalName="ZQ_SALES"
            description="Sales query">
          <atom:link rel="self" href="/sap/bw/modeling/query/ZQ_SALES/a" />
          <atom:link rel="alternate" href="/sap/bw/modeling/query/ZQ_SALES/m" />
          <atom:link rel="related" href="/sap/bw/modeling/hcpr/ZCP_SALES/m" />
        </Qry:queryResource>
        """,
    )

    _, catalog = ingest_manifest(manifest_path)

    objects = {item.id: item for item in catalog.objects}
    assert set(objects) == {"ZQ_SALES", "ZCP_SALES"}
    assert objects["ZQ_SALES"].type == "QUERY"
    assert objects["ZCP_SALES"].type == "HCPR"
    assert [(edge.source, edge.target, edge.type) for edge in catalog.edges] == [
        ("ZCP_SALES", "ZQ_SALES", "provides")
    ]


def test_ingest_manifest_composite_provider_xml_adds_input_provider_edges(
    tmp_path: Path,
) -> None:
    manifest_path = _write_manifest_payload(
        tmp_path,
        kind="bw_get_composite_provider",
        payload_id="hcpr-xml",
        payload="""
        <Composite:compositeView xmlns:Composite="urn:sap:bw:hcpr"
            technicalName="ZCP_SALES"
            description="Sales CompositeProvider">
          <Composite:inputProvider name="ZADSO_SALES" type="ADSO" />
          <Composite:inputProvider technicalName="ZCP_BASE" objectType="HCPR" />
        </Composite:compositeView>
        """,
    )

    _, catalog = ingest_manifest(manifest_path)

    objects = {item.id: item for item in catalog.objects}
    assert objects["ZCP_SALES"].type == "HCPR"
    assert objects["ZCP_SALES"].name == "Sales CompositeProvider"
    assert objects["ZADSO_SALES"].type == "ADSO"
    assert objects["ZCP_BASE"].type == "HCPR"
    assert {(edge.source, edge.target, edge.type) for edge in catalog.edges} == {
        ("ZADSO_SALES", "ZCP_SALES", "composite_input"),
        ("ZCP_BASE", "ZCP_SALES", "composite_input"),
    }


def test_ingest_manifest_composite_provider_xml_reads_view_node_input_alias(
    tmp_path: Path,
) -> None:
    manifest_path = _write_manifest_payload(
        tmp_path,
        kind="bw_get_composite_provider",
        payload_id="hcpr-view-node-input-xml",
        payload="""
        <Composite:compositeView xmlns:Composite="urn:sap:bw:hcpr"
            technicalName="ZCP_SALES"
            description="Sales CompositeProvider">
          <Composite:viewNode name="Projection_1">
            <Composite:input name="ZADSO_SALES" alias="ZADSO_SALES.ADSO" />
          </Composite:viewNode>
        </Composite:compositeView>
        """,
    )

    _, catalog = ingest_manifest(manifest_path)

    objects = {item.id: item for item in catalog.objects}
    assert objects["ZCP_SALES"].type == "HCPR"
    assert objects["ZADSO_SALES"].type == "ADSO"
    assert [(edge.source, edge.target, edge.type) for edge in catalog.edges] == [
        ("ZADSO_SALES", "ZCP_SALES", "composite_input")
    ]


def test_request_list_detail_ingest_attaches_request_freshness_deterministically(
    tmp_path: Path,
) -> None:
    writer = SnapshotWriter(tmp_path)
    graph = writer.write_payload(
        payload_id="graph",
        kind="graph",
        source="fixture://graph",
        payload={
            "nodes": [{"id": "ZADSO_SALES", "type": "ADSO", "name": "Sales ADSO"}],
            "edges": [],
        },
    )
    request_list = writer.write_payload(
        payload_id="requests",
        kind="bw_list_requests",
        source="bw://bw_list_requests?objectName=ZADSO_SALES&objectType=ADSO&top=2",
        payload=[
            {
                "requestTsn": "TSN_OLD",
                "requestTsnExternal": "REQ_OLD",
                "storage": "AQ",
                "requestStatus": "R",
                "lastProcessStatus": "R",
                "records": 1,
                "lastTimeStamp": "2026-06-16T12:00:00Z",
            },
            {
                "requestTsn": "TSN_NEW",
                "requestTsnExternal": "REQ_NEW",
                "storage": "AQ",
                "requestStatus": "G",
                "lastProcessStatus": "G",
                "records": 42,
                "lastTimeStamp": "2026-06-17T12:00:00Z",
            },
        ],
    )
    request_detail = writer.write_payload(
        payload_id="request-detail",
        kind="bw_get_request",
        source=(
            "bw://bw_get_request?objectName=ZADSO_SALES&objectType=ADSO"
            "&requestTsn=TSN_NEW&storage=AQ"
        ),
        payload={
            "requestTsn": "TSN_NEW",
            "requestTsnExternal": "REQ_NEW",
            "storage": "AQ",
            "requestStatus": "G",
            "lastProcessStatus": "G",
            "records": 43,
            "lastTimeStamp": "2026-06-17T12:05:00Z",
            "lastAction": "LOAD",
        },
    )
    writer.write_manifest(
        mode="live-read-only",
        payloads=[graph, request_list, request_detail],
    )

    _, catalog = ingest_manifest(tmp_path / "manifest.json")

    objects = {item.id: item for item in catalog.objects}
    provider = objects["ZADSO_SALES"]
    assert provider.type == "ADSO"
    assert provider.name == "Sales ADSO"
    freshness = provider.metadata["request_freshness"]
    assert freshness["target"] == "ZADSO_SALES"
    assert freshness["target_type"] == "ADSO"
    assert freshness["latest"] == {
        "request_tsn": "TSN_NEW",
        "tsn": "REQ_NEW",
        "storage": "AQ",
        "status": "G",
        "last_process_status": "G",
        "last_action": "LOAD",
        "records": 43,
        "timestamp": "2026-06-17T12:05:00Z",
    }
    assert [item["request_tsn"] for item in freshness["requests"]] == [
        "TSN_NEW",
        "TSN_OLD",
    ]
    assert "requests:request-list" in provider.evidence_ids
    assert "request-detail:request-detail" in provider.evidence_ids


def test_ingest_manifest_indexes_top_level_search_array_payload(tmp_path: Path) -> None:
    manifest_path = _write_manifest_payload(
        tmp_path,
        kind="bw_search",
        payload_id="search-array",
        payload=[
            {"technicalName": "ZQ_ARRAY", "objectType": "QUERY", "description": "Array query"}
        ],
    )

    _, catalog = ingest_manifest(manifest_path)

    assert [item.id for item in catalog.objects] == ["ZQ_ARRAY"]
    assert catalog.objects[0].type == "QUERY"


def test_catalog_search_matches_visible_label_when_name_is_missing(tmp_path: Path) -> None:
    catalog = ingest_fixture_payload(
        {"nodes": [{"id": "A", "label": "Sales Label", "type": "QUERY"}], "edges": []},
        source="fixture://label-search.json",
    )
    store = CatalogStore(tmp_path / "catalog.sqlite")
    snapshot = store.create_snapshot(mode="test", source="fixture://label-search")
    store.replace_catalog(snapshot.id, objects=catalog.objects, edges=catalog.edges)

    records, next_cursor = store.list_objects(
        snapshot.id,
        q="sales label",
        object_type=None,
        limit=10,
        cursor=0,
    )

    assert next_cursor is None
    assert [record.id for record in records] == ["A"]


def test_ingest_manifest_rejects_symlink_payload_outside_snapshot_root(tmp_path: Path) -> None:
    _skip_if_file_symlink_unavailable(tmp_path)

    writer = SnapshotWriter(tmp_path / "snapshot")
    metadata = writer.write_payload(
        payload_id="safe",
        kind="fixture",
        source="fixture://safe.json",
        payload={"nodes": [{"id": "SAFE", "type": "ADSO"}], "edges": []},
    )
    writer.write_manifest(mode="offline-fixture", payloads=[metadata])
    outside_payload = tmp_path / "outside.json"
    outside_payload.write_text(
        '{"nodes": [{"id": "OUTSIDE", "type": "ADSO"}], "edges": []}',
        encoding="utf-8",
    )
    payload_path = tmp_path / "snapshot" / metadata.relative_path
    payload_path.unlink()
    payload_path.symlink_to(outside_payload)

    with pytest.raises(ValueError, match="unsafe snapshot relative path"):
        ingest_manifest(tmp_path / "snapshot" / "manifest.json")


def test_ingest_manifest_merges_duplicate_json_search_objects_before_storage(
    tmp_path: Path,
) -> None:
    manifest_path = _write_manifest_payload(
        tmp_path,
        kind="bw_search",
        payload_id="search-json",
        payload={
            "objects": [
                {"technicalName": "ZADSO_DUP", "objectType": "ADSO", "description": "First"},
                {"technicalName": "ZADSO_DUP", "objectType": "ADSO", "description": "Second"},
            ]
        },
    )

    _, catalog = ingest_manifest(manifest_path)

    assert [item.id for item in catalog.objects] == ["ZADSO_DUP"]
    assert catalog.objects[0].name == "First"
    assert catalog.objects[0].evidence_ids == ["search-json:search:1", "search-json:search:2"]

    store = CatalogStore(tmp_path / "catalog.sqlite")
    snapshot = store.create_snapshot(mode="test", source="fixture://duplicate-search-json")
    store.replace_catalog(snapshot.id, objects=catalog.objects, edges=catalog.edges)
    assert store.get_object(snapshot.id, "ZADSO_DUP") is not None
