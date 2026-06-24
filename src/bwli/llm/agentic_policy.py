from __future__ import annotations

import re
from typing import get_args

from bwli.impact_evidence import ImpactEvidencePack
from bwli.llm.agentic_review import (
    AgenticReviewBudget,
    AgenticReviewPlan,
    EnricherName,
    EvidenceRequest,
    EvidenceRequestDecision,
)

ALLOWED_ENRICHERS = frozenset(str(item) for item in get_args(EnricherName))
MUTATING_TOKENS = frozenset(
    {
        "activate",
        "create",
        "data",
        "delete",
        "drop",
        "execute",
        "insert",
        "preview",
        "row",
        "rows",
        "run",
        "transport",
        "update",
        "write",
    }
)
_UNBOUNDED_TARGET_TOKENS = frozenset(
    {"all", "allobjects", "any", "everything", "entire", "global", "scope", "unbounded", "whole"}
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")


class PolicyGate:
    """Deterministically allow only parse-only, in-scope evidence requests."""

    def evaluate(
        self,
        plan: AgenticReviewPlan,
        *,
        pack: ImpactEvidencePack,
        budget: AgenticReviewBudget,
    ) -> tuple[list[EvidenceRequest], list[EvidenceRequestDecision]]:
        scope = _pack_scope(pack)
        allowed_requests: list[EvidenceRequest] = []
        decisions: list[EvidenceRequestDecision] = []
        seen: set[tuple[str, str]] = set()

        for request in plan.evidence_requests:
            reasons: list[str] = []
            enricher = str(request.enricher)
            target = request.target.strip()
            duplicate_key = (enricher.casefold(), target.casefold())

            if duplicate_key in seen:
                reasons.append("Duplicate evidence request was skipped.")
            else:
                seen.add(duplicate_key)

            if enricher not in ALLOWED_ENRICHERS:
                reasons.append("Evidence enricher is not allowlisted for agentic review.")

            blocked_field = _blocked_policy_field(request)
            if blocked_field is not None:
                reasons.append(
                    "Request contains a mutating, execution, or data-preview token "
                    f"in {blocked_field}."
                )

            target_reason = _target_rejection_reason(target)
            if target_reason is not None:
                reasons.append(target_reason)
            elif target.casefold() not in scope:
                reasons.append("Request target is outside the deterministic impact evidence scope.")

            if not reasons and len(allowed_requests) >= budget.max_evidence_requests:
                reasons.append("Evidence request budget exhausted; request was not executed.")

            if reasons:
                decisions.append(
                    EvidenceRequestDecision(
                        request_id=request.id,
                        allowed=False,
                        reason=" ".join(reasons).strip(),
                    )
                )
                continue

            allowed_requests.append(request)
            decisions.append(
                EvidenceRequestDecision(
                    request_id=request.id,
                    allowed=True,
                    reason="Allowed parse-only evidence request within deterministic pack scope.",
                )
            )

        return allowed_requests, decisions


def _blocked_policy_field(request: EvidenceRequest) -> str | None:
    values = {
        "enricher": str(request.enricher),
        "target": request.target,
        "reason": request.reason,
        "citation_hint": request.citation_hint or "",
    }
    for field, value in values.items():
        if _contains_mutating_token(value):
            return field
    return None


def _contains_mutating_token(value: str) -> bool:
    tokens = set(_tokens(value))
    return bool(tokens & MUTATING_TOKENS)


def _target_rejection_reason(target: str) -> str | None:
    if not target:
        return "Request target must not be empty."
    if target in {"*", "%"} or "*" in target or "%" in target:
        return "Request target must not contain wildcard or broad-match characters."
    target_tokens = _tokens(target)
    normalized = "".join(target_tokens)
    if normalized in _UNBOUNDED_TARGET_TOKENS:
        return "Request target is broad or unbounded."
    broad_prefixes = {"all", "any", "everything", "global"}
    if target_tokens and target_tokens[0] in broad_prefixes and len(target_tokens) <= 2:
        return "Request target is broad or unbounded."
    return None


def _tokens(value: str) -> list[str]:
    return _TOKEN_RE.findall(value.casefold())


def _pack_scope(pack: ImpactEvidencePack) -> set[str]:
    values: set[str] = set()

    def add(value: str | None) -> None:
        if value and value.strip():
            values.add(value.strip().casefold())

    add(pack.snapshot_id)
    for change in pack.impact.changes:
        add(change.id)
        add(change.object_id)
        if change.field:
            add(change.field)
    for finding in pack.impact.findings:
        add(finding.id)
        add(finding.change_id)
        add(finding.impacted_object_id)
        for node_id in finding.evidence_node_ids:
            add(node_id)
        for edge_id in finding.evidence_edge_ids:
            add(edge_id)
    for query_item in pack.query_evidence:
        add(query_item.query_id)
        for object_id in query_item.provider_object_ids:
            add(object_id)
        for object_id in query_item.exposed_object_ids:
            add(object_id)
        for finding_id in query_item.matched_finding_ids:
            add(finding_id)
    for sql_item in pack.sql_evidence:
        add(sql_item.view_id)
        for object_id in sql_item.referenced_object_ids:
            add(object_id)
        for edge_id in sql_item.reference_edge_ids:
            add(edge_id)
        for fragment_id in sql_item.fragment_ids:
            add(fragment_id)
        for finding_id in sql_item.matched_finding_ids:
            add(finding_id)
    for freshness_item in pack.freshness_evidence:
        add(freshness_item.object_id)
    for gap in pack.manual_verification_gaps:
        add(gap.id)
        add(gap.object_id)
        add(gap.finding_id)
        for evidence_id in gap.evidence_ids:
            add(evidence_id)
    return values
