from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from bwli.field_lineage import SqlParseResult
from bwli.impact import ImpactReport
from bwli.impact_evidence import (
    FreshnessEvidence,
    ImpactEvidencePack,
    ManualVerificationGap,
    QueryExposureEvidence,
    SqlReferenceEvidence,
    _manual_verification_gaps,
    build_impact_evidence_pack,
)
from bwli.llm.agentic_review import EvidenceRequest
from bwli.query_analysis import QueryAnalysisResult


@dataclass(frozen=True)
class AgenticEvidenceSources:
    """Injected parse-only evidence providers for agentic review enrichment.

    The enricher intentionally depends only on these callables. Server adapters, snapshot-store
    lookups, parser fakes, or tests can provide them without this module importing server.py,
    BW clients, network transports, or SQL execution surfaces.
    """

    impact_report: Callable[[], ImpactReport] | None = None
    query_result: Callable[[EvidenceRequest], QueryAnalysisResult | None] | None = None
    sql_result: Callable[[EvidenceRequest], SqlParseResult | None] | None = None
    freshness: Callable[[EvidenceRequest], Mapping[str, Mapping[str, object]] | None] | None = None


class AgenticEvidenceEnricher:
    """Deterministically recompose impact evidence from allowlisted local evidence requests."""

    def __init__(self, sources: AgenticEvidenceSources) -> None:
        self._sources = sources

    def run_enrichers(
        self,
        allowed_requests: Sequence[EvidenceRequest],
        *,
        prior_pack: ImpactEvidencePack,
    ) -> ImpactEvidencePack:
        """Run allowed parse-only enrichers and return a recomposed evidence pack.

        Missing adapters and ``None`` adapter results are fail-safe: they add no evidence and do
        not fail the run. Unknown enricher names raise ``ValueError`` even though PolicyGate should
        prevent them before this executor is called.
        """

        query_results: list[QueryAnalysisResult] = []
        sql_results: list[SqlParseResult] = []
        freshness_by_object_id: dict[str, Mapping[str, object]] = {}
        refreshed_impact: ImpactReport | None = None

        for request in allowed_requests:
            enricher = str(request.enricher)
            if enricher == "reparse_query_xml":
                if self._sources.query_result is None:
                    continue
                query_result = self._sources.query_result(request)
                if query_result is not None:
                    query_results.append(query_result)
                continue

            if enricher == "reparse_native_sql_view":
                if self._sources.sql_result is None:
                    continue
                sql_result = self._sources.sql_result(request)
                if sql_result is not None:
                    sql_results.append(sql_result)
                continue

            if enricher == "lookup_request_freshness":
                if self._sources.freshness is None:
                    continue
                freshness = self._sources.freshness(request)
                if freshness is not None:
                    freshness_by_object_id.update(freshness)
                continue

            if enricher == "recompute_impact_pack":
                if self._sources.impact_report is not None:
                    refreshed_impact = self._sources.impact_report()
                continue

            raise ValueError(f"unknown agentic evidence enricher: {enricher}")

        if (
            refreshed_impact is None
            and not query_results
            and not sql_results
            and not freshness_by_object_id
        ):
            return prior_pack

        impact = refreshed_impact or prior_pack.impact
        new_pack = build_impact_evidence_pack(
            impact,
            snapshot_id=prior_pack.snapshot_id,
            query_results=query_results,
            sql_results=sql_results,
            freshness_by_object_id=freshness_by_object_id,
        )
        query_evidence = _merge_query_evidence(prior_pack.query_evidence, new_pack.query_evidence)
        sql_evidence = _merge_sql_evidence(prior_pack.sql_evidence, new_pack.sql_evidence)
        freshness_evidence = _merge_freshness_evidence(
            prior_pack.freshness_evidence,
            new_pack.freshness_evidence,
        )
        manual_gaps = _manual_verification_gaps(
            impact.findings,
            query_evidence=query_evidence,
            sql_evidence=sql_evidence,
            freshness_evidence=freshness_evidence,
        )
        return prior_pack.model_copy(
            update={
                "impact": impact,
                "query_evidence": query_evidence,
                "sql_evidence": sql_evidence,
                "freshness_evidence": freshness_evidence,
                "manual_verification_gaps": manual_gaps,
                "coverage_summary": _coverage_summary(
                    impact,
                    query_evidence=query_evidence,
                    sql_evidence=sql_evidence,
                    freshness_evidence=freshness_evidence,
                    manual_gaps=manual_gaps,
                ),
            }
        )


def _merge_query_evidence(
    prior: Sequence[QueryExposureEvidence],
    new: Sequence[QueryExposureEvidence],
) -> list[QueryExposureEvidence]:
    values = {item.query_id.casefold(): item for item in prior}
    order = [item.query_id.casefold() for item in prior]
    for item in new:
        key = item.query_id.casefold()
        if key not in values:
            order.append(key)
        values[key] = item
    return [values[key] for key in order]


def _merge_sql_evidence(
    prior: Sequence[SqlReferenceEvidence],
    new: Sequence[SqlReferenceEvidence],
) -> list[SqlReferenceEvidence]:
    values = {item.view_id.casefold(): item for item in prior}
    order = [item.view_id.casefold() for item in prior]
    for item in new:
        key = item.view_id.casefold()
        if key not in values:
            order.append(key)
        values[key] = item
    return [values[key] for key in order]


def _merge_freshness_evidence(
    prior: Sequence[FreshnessEvidence],
    new: Sequence[FreshnessEvidence],
) -> list[FreshnessEvidence]:
    values = {item.object_id.casefold(): item for item in prior}
    order = [item.object_id.casefold() for item in prior]
    for item in new:
        key = item.object_id.casefold()
        if key not in values:
            order.append(key)
        values[key] = item
    return [values[key] for key in order]


def _coverage_summary(
    impact: ImpactReport,
    *,
    query_evidence: Sequence[QueryExposureEvidence],
    sql_evidence: Sequence[SqlReferenceEvidence],
    freshness_evidence: Sequence[FreshnessEvidence],
    manual_gaps: Sequence[ManualVerificationGap],
) -> dict[str, object]:
    return {
        "impact_finding_count": len(impact.findings),
        "affected_object_count": len({finding.impacted_object_id for finding in impact.findings}),
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
    }
