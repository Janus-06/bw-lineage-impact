from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from bwli.field_lineage import SqlConfidence, SqlParseResult
from bwli.impact import ImpactFinding, ImpactReport
from bwli.query_analysis import QueryAnalysisResult

JsonDict = dict[str, object]
ManualVerificationSource = Literal["impact", "query", "sql", "freshness"]


class QueryExposureEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_id: str
    description: str | None = None
    provider_object_ids: list[str] = Field(default_factory=list)
    variable_names: list[str] = Field(default_factory=list)
    calculated_key_figure_names: list[str] = Field(default_factory=list)
    restricted_key_figure_names: list[str] = Field(default_factory=list)
    field_names: list[str] = Field(default_factory=list)
    filter_count: int = 0
    layout_fields: list[str] = Field(default_factory=list)
    exposed_object_ids: list[str] = Field(default_factory=list)
    matched_finding_ids: list[str] = Field(default_factory=list)
    manual_check_notes: list[str] = Field(default_factory=list)
    metadata: JsonDict = Field(default_factory=dict)


class SqlReferenceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    view_id: str
    parser: str
    confidence: SqlConfidence
    referenced_object_ids: list[str] = Field(default_factory=list)
    referenced_column_names: list[str] = Field(default_factory=list)
    reference_edge_ids: list[str] = Field(default_factory=list)
    fragment_ids: list[str] = Field(default_factory=list)
    matched_finding_ids: list[str] = Field(default_factory=list)
    manual_check_notes: list[str] = Field(default_factory=list)


class FreshnessEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_id: str
    object_type: str | None = None
    request_count: int = 0
    latest_request_tsn: str | None = None
    latest_status: str | None = None
    latest_timestamp: str | None = None
    latest_records: int | float | None = None
    evidence_available: bool = False
    manual_check_notes: list[str] = Field(default_factory=list)


class ManualVerificationGap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: ManualVerificationSource
    reason: str
    object_id: str | None = None
    object_type: str | None = None
    finding_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class ImpactEvidencePack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    snapshot_id: str | None = None
    deterministic: bool = True
    read_only: bool = True
    execution_blocked: bool = True
    final_authority: Literal["impact.py"] = "impact.py"
    authority_note: str = (
        "Impact severity, confidence, affected objects, and impact manual flags are copied "
        "from bwli.impact only. Query XML and Native SQL evidence are parse-only context."
    )
    impact: ImpactReport
    query_evidence: list[QueryExposureEvidence] = Field(default_factory=list)
    sql_evidence: list[SqlReferenceEvidence] = Field(default_factory=list)
    freshness_evidence: list[FreshnessEvidence] = Field(default_factory=list)
    manual_verification_gaps: list[ManualVerificationGap] = Field(default_factory=list)
    coverage_summary: JsonDict = Field(default_factory=dict)


def build_impact_evidence_pack(
    impact: ImpactReport,
    *,
    snapshot_id: str | None = None,
    query_results: Sequence[QueryAnalysisResult] = (),
    sql_results: Sequence[SqlParseResult] = (),
    freshness_by_object_id: Mapping[str, Mapping[str, object]] | None = None,
) -> ImpactEvidencePack:
    """Compose deterministic impact findings with optional parse-only evidence.

    The pack intentionally does not call BW, execute SQL, or import LLM modules. Impact findings
    remain authoritative; additional evidence only records references and manual-check notes.
    """

    findings_by_object = _findings_by_object(impact.findings)
    query_evidence = [
        _query_exposure_evidence(result, findings_by_object=findings_by_object)
        for result in query_results
    ]
    sql_evidence = [
        _sql_reference_evidence(result, findings_by_object=findings_by_object)
        for result in sql_results
    ]
    freshness_evidence = _freshness_evidence(
        impact.findings,
        freshness_by_object_id=freshness_by_object_id or {},
    )
    manual_gaps = _manual_verification_gaps(
        impact.findings,
        query_evidence=query_evidence,
        sql_evidence=sql_evidence,
        freshness_evidence=freshness_evidence,
    )
    return ImpactEvidencePack(
        snapshot_id=snapshot_id,
        impact=impact,
        query_evidence=query_evidence,
        sql_evidence=sql_evidence,
        freshness_evidence=freshness_evidence,
        manual_verification_gaps=manual_gaps,
        coverage_summary={
            "impact_finding_count": len(impact.findings),
            "affected_object_count": len(
                {finding.impacted_object_id for finding in impact.findings}
            ),
            "query_evidence_count": len(query_evidence),
            "sql_evidence_count": len(sql_evidence),
            "freshness_evidence_count": len(freshness_evidence),
            "manual_gap_count": len(manual_gaps),
            "query_matched_finding_count": len(
                {finding_id for item in query_evidence for finding_id in item.matched_finding_ids}
            ),
            "sql_matched_finding_count": len(
                {finding_id for item in sql_evidence for finding_id in item.matched_finding_ids}
            ),
        },
    )


def _findings_by_object(findings: Sequence[ImpactFinding]) -> dict[str, list[ImpactFinding]]:
    indexed: dict[str, list[ImpactFinding]] = {}
    for finding in findings:
        indexed.setdefault(_key(finding.impacted_object_id), []).append(finding)
    return indexed


def _query_exposure_evidence(
    result: QueryAnalysisResult,
    *,
    findings_by_object: Mapping[str, Sequence[ImpactFinding]],
) -> QueryExposureEvidence:
    provider_object_ids = _unique(provider.object_id for provider in result.providers)
    variable_names = _unique(variable.technical_name for variable in result.variables)
    calculated_key_figure_names = _unique(
        figure.technical_name for figure in result.calculated_key_figures
    )
    restricted_key_figure_names = _unique(
        figure.technical_name for figure in result.restricted_key_figures
    )
    field_names = _query_field_names(result)
    layout_fields = _layout_fields(result.layout)
    exposed_object_ids = _unique([result.query_id, *provider_object_ids])
    matched_finding_ids = _matching_finding_ids(exposed_object_ids, findings_by_object)
    manual_check_notes: list[str] = []
    if matched_finding_ids:
        manual_check_notes.append(
            "Query XML evidence matched impact scope; review variables, filters, key figures, "
            "and provider links without changing impact severity."
        )
    if (
        result.variables
        or result.filters
        or result.calculated_key_figures
        or result.restricted_key_figures
    ):
        manual_check_notes.append(
            "Query semantics can constrain exposure; deterministic review records the evidence "
            "but does not execute the BW query or infer final impact."
        )
    unknown_reason = result.metadata.get("unknown_reason")
    if isinstance(unknown_reason, str):
        manual_check_notes.append(f"Query XML parser fallback: {unknown_reason}.")
    return QueryExposureEvidence(
        query_id=result.query_id,
        description=result.description,
        provider_object_ids=provider_object_ids,
        variable_names=variable_names,
        calculated_key_figure_names=calculated_key_figure_names,
        restricted_key_figure_names=restricted_key_figure_names,
        field_names=field_names,
        filter_count=len(result.filters),
        layout_fields=layout_fields,
        exposed_object_ids=exposed_object_ids,
        matched_finding_ids=matched_finding_ids,
        manual_check_notes=manual_check_notes,
        metadata={
            key: value
            for key, value in {
                "source": result.metadata.get("source"),
                "unknown_reason": result.metadata.get("unknown_reason"),
            }.items()
            if value
        },
    )


def _sql_reference_evidence(
    result: SqlParseResult,
    *,
    findings_by_object: Mapping[str, Sequence[ImpactFinding]],
) -> SqlReferenceEvidence:
    referenced_object_ids = _unique(edge.source_object_id for edge in result.reference_edges)
    referenced_column_names = _unique(column.column_name for column in result.columns)
    reference_edge_ids = _unique(edge.id for edge in result.reference_edges)
    fragment_ids = _unique(fragment.id for fragment in result.fragments)
    matched_finding_ids = _matching_finding_ids(
        [result.view.id, *referenced_object_ids],
        findings_by_object,
    )
    manual_check_notes: list[str] = [
        "Native SQL evidence is parse-only; review referenced objects and columns manually "
        "without executing database SQL or changing impact severity."
    ]
    if result.confidence == SqlConfidence.SQL_UNKNOWN:
        manual_check_notes.append(
            "SQL parser could not classify references; raw SQL is retained only as evidence."
        )
    elif matched_finding_ids:
        manual_check_notes.append(
            "Parsed SQL references intersect impact scope; impact.py remains authoritative."
        )
    return SqlReferenceEvidence(
        view_id=result.view.id,
        parser=result.parser,
        confidence=result.confidence,
        referenced_object_ids=referenced_object_ids,
        referenced_column_names=referenced_column_names,
        reference_edge_ids=reference_edge_ids,
        fragment_ids=fragment_ids,
        matched_finding_ids=matched_finding_ids,
        manual_check_notes=manual_check_notes,
    )


def _freshness_evidence(
    findings: Sequence[ImpactFinding],
    *,
    freshness_by_object_id: Mapping[str, Mapping[str, object]],
) -> list[FreshnessEvidence]:
    if not freshness_by_object_id:
        return []
    finding_types = {
        finding.impacted_object_id: finding.impacted_object_type for finding in findings
    }
    evidence: list[FreshnessEvidence] = []
    for object_id in sorted(freshness_by_object_id):
        payload = freshness_by_object_id[object_id]
        requests = _mapping_list(payload.get("requests"))
        latest = _mapping_value(payload.get("latest")) or (requests[0] if requests else {})
        evidence_available = bool(requests or latest)
        evidence.append(
            FreshnessEvidence(
                object_id=object_id,
                object_type=_text(payload.get("target_type")) or finding_types.get(object_id),
                request_count=len(requests),
                latest_request_tsn=_text(latest.get("request_tsn") or latest.get("tsn")),
                latest_status=_text(latest.get("status") or latest.get("last_process_status")),
                latest_timestamp=_text(latest.get("timestamp")),
                latest_records=_number(latest.get("records")),
                evidence_available=evidence_available,
                manual_check_notes=(
                    ["Request freshness evidence is present; validate stale/failed loads manually."]
                    if evidence_available
                    else ["Request freshness evidence is unavailable for this impacted object."]
                ),
            )
        )
    return evidence


def _manual_verification_gaps(
    findings: Sequence[ImpactFinding],
    *,
    query_evidence: Sequence[QueryExposureEvidence],
    sql_evidence: Sequence[SqlReferenceEvidence],
    freshness_evidence: Sequence[FreshnessEvidence],
) -> list[ManualVerificationGap]:
    gaps: list[ManualVerificationGap] = []
    for finding in findings:
        if finding.manual_verification:
            gaps.append(
                ManualVerificationGap(
                    id=f"impact:{finding.id}",
                    source="impact",
                    finding_id=finding.id,
                    object_id=finding.impacted_object_id,
                    object_type=finding.impacted_object_type,
                    reason="Impact rule marked this finding for manual verification.",
                    evidence_ids=_unique([*finding.evidence_node_ids, *finding.evidence_edge_ids]),
                )
            )
    for query_item in query_evidence:
        if query_item.manual_check_notes:
            gaps.append(
                ManualVerificationGap(
                    id=f"query:{query_item.query_id}",
                    source="query",
                    object_id=query_item.query_id,
                    object_type="QUERY",
                    finding_id=(
                        query_item.matched_finding_ids[0]
                        if query_item.matched_finding_ids
                        else None
                    ),
                    reason="; ".join(query_item.manual_check_notes),
                )
            )
    for sql_item in sql_evidence:
        if sql_item.manual_check_notes:
            gaps.append(
                ManualVerificationGap(
                    id=f"sql:{sql_item.view_id}",
                    source="sql",
                    object_id=sql_item.view_id,
                    object_type="NATIVE_SQL_VIEW",
                    finding_id=(
                        sql_item.matched_finding_ids[0] if sql_item.matched_finding_ids else None
                    ),
                    reason="; ".join(sql_item.manual_check_notes),
                    evidence_ids=sql_item.reference_edge_ids,
                )
            )
    for freshness_item in freshness_evidence:
        if freshness_item.manual_check_notes and freshness_item.evidence_available:
            gaps.append(
                ManualVerificationGap(
                    id=f"freshness:{freshness_item.object_id}",
                    source="freshness",
                    object_id=freshness_item.object_id,
                    object_type=freshness_item.object_type,
                    reason="; ".join(freshness_item.manual_check_notes),
                )
            )
    return gaps


def _matching_finding_ids(
    object_ids: Sequence[str],
    findings_by_object: Mapping[str, Sequence[ImpactFinding]],
) -> list[str]:
    finding_ids: list[str] = []
    for object_id in object_ids:
        for finding in findings_by_object.get(_key(object_id), []):
            finding_ids.append(finding.id)
    return _unique(finding_ids)


def _query_field_names(result: QueryAnalysisResult) -> list[str]:
    names: list[str] = []
    for field in result.fields:
        for key in ("name", "technical_name", "info_object", "fieldName"):
            value = field.get(key)
            if isinstance(value, str) and value.strip():
                names.append(value.strip())
                break
    return _unique(names)


def _layout_fields(layout: Mapping[str, object]) -> list[str]:
    fields: list[str] = []
    for value in layout.values():
        if isinstance(value, str):
            fields.append(value)
        elif isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
            fields.extend(item for item in value if isinstance(item, str))
    return _unique(fields)


def _mapping_list(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        return []
    return [cast(Mapping[str, object], item) for item in value if isinstance(item, Mapping)]


def _mapping_value(value: object) -> Mapping[str, object] | None:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else None


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _number(value: object) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return value
    return None


def _key(value: str) -> str:
    return value.casefold()


def _unique(values: Iterable[object]) -> list[str]:
    seen: set[str] = set()
    unique_values: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        unique_values.append(text)
    return unique_values
