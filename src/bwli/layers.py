from __future__ import annotations

from bwli.graph import BwGraph, BwLayer, BwNode

# BW semantics: LSYS/SOURCE_SYSTEM are source nodes; RSDS/DATASOURCE are acquisition nodes.
_LAYER_BY_NORMALIZED_TYPE: dict[str, BwLayer] = {
    "LSYS": BwLayer.SOURCE,
    "SOURCESYSTEM": BwLayer.SOURCE,
    "RSDS": BwLayer.ACQUISITION,
    "DATASOURCE": BwLayer.ACQUISITION,
    "ADSO": BwLayer.PROVIDER,
    "ADSOO": BwLayer.PROVIDER,
    "CUBE": BwLayer.PROVIDER,
    "INFOCUBE": BwLayer.PROVIDER,
    "HCPR": BwLayer.PROVIDER,
    "COMPOSITEPROVIDER": BwLayer.PROVIDER,
    "TRFN": BwLayer.TRANSFORMATION,
    "TRANSFORMATION": BwLayer.TRANSFORMATION,
    "DTP": BwLayer.TRANSFORMATION,
    "DTPA": BwLayer.TRANSFORMATION,
    "DTPLOAD": BwLayer.TRANSFORMATION,
    "TRCS": BwLayer.TRANSFORMATION,
    "QUERY": BwLayer.REPORTING,
    "ALVL": BwLayer.REPORTING,
    "AGGRLEVEL": BwLayer.REPORTING,
    "AGGREGATIONLEVEL": BwLayer.REPORTING,
    "VARIABLE": BwLayer.REPORTING,
    "QUERYVARIABLE": BwLayer.REPORTING,
    "QUERY_VARIABLE": BwLayer.REPORTING,
    "LOCALMEMBER": BwLayer.REPORTING,
    "LOCAL_MEMBER": BwLayer.REPORTING,
    "CKF": BwLayer.REPORTING,
    "RKF": BwLayer.REPORTING,
    "STRUCTURE": BwLayer.REPORTING,
    "REPORT": BwLayer.REPORTING,
    "RSPC": BwLayer.RUNTIME,
    "PROCESSCHAIN": BwLayer.RUNTIME,
    "REQUEST": BwLayer.RUNTIME,
    "INFOSOURCE": BwLayer.ACQUISITION,
    "ISOURCE": BwLayer.ACQUISITION,
}


def assign_layer(node: BwNode) -> BwLayer | None:
    """Infer the graph display layer for a BW node from its object type."""

    return _LAYER_BY_NORMALIZED_TYPE.get(_normalize_object_type(node.type))


def assign_layers(graph: BwGraph) -> BwGraph:
    """Return a copy of the graph with inferred node layers populated."""

    nodes: list[BwNode] = []
    for node in graph.nodes:
        inferred_layer = assign_layer(node)
        assigned_layer = inferred_layer if inferred_layer is not None else node.layer
        nodes.append(node.model_copy(deep=True, update={"layer": assigned_layer}))
    return graph.model_copy(deep=True, update={"nodes": nodes})


def _normalize_object_type(object_type: str) -> str:
    return "".join(char for char in object_type.upper() if char.isalnum())
