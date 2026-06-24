from __future__ import annotations

import time
from collections.abc import Callable

from bwli.config import LlmRuntimeConfig
from bwli.impact_evidence import ImpactEvidencePack
from bwli.llm.agentic_enricher import AgenticEvidenceEnricher
from bwli.llm.agentic_policy import ALLOWED_ENRICHERS, PolicyGate
from bwli.llm.agentic_review import (
    AgenticReviewBudget,
    AgenticReviewBudgetUsage,
    AgenticReviewCard,
    AgenticReviewPlan,
    AgenticReviewRun,
    EvidenceGap,
    EvidenceRequestDecision,
    ManualCheck,
    ReviewHypothesis,
    ReviewTraceStep,
    _deterministic_cab_summary,
    build_review_plan_request,
    create_agentic_synthesis,
    deterministic_review_cards,
    parse_review_plan_content,
    run_hypothesis_review,
)
from bwli.llm.agentic_validator import validate_agentic_run
from bwli.llm.explainer import _validate_completion_safety
from bwli.llm.openai_compatible import (
    LlmAuditMetadata,
    LlmCompletion,
    OpenAICompatibleClient,
    TransportLike,
)

Clock = Callable[[], float]


class AgenticReviewAssistant:
    """Coordinate the bounded agentic review pipeline with fail-closed fallback."""

    def __init__(
        self,
        *,
        policy_gate: PolicyGate | None = None,
        enricher: AgenticEvidenceEnricher | None = None,
        transport: TransportLike | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._policy_gate = policy_gate or PolicyGate()
        self._enricher = enricher
        self._transport = transport
        self._clock = clock or time.monotonic

    def run(
        self,
        pack: ImpactEvidencePack,
        *,
        runtime: LlmRuntimeConfig | None,
        question: str | None = None,
        budget: AgenticReviewBudget | None = None,
    ) -> AgenticReviewRun:
        """Run planner, policy, enrichment, review, synthesis, and validation.

        Expected budget, validation, parsing, and LLM failures do not escape this method; they
        return a deterministic ``status="fallback"`` run with the trace and audit collected so far.
        """

        active_budget = budget or AgenticReviewBudget()
        start = self._clock()
        current_pack = pack
        plan = AgenticReviewPlan()
        hypotheses: list[ReviewHypothesis] = []
        evidence_gaps: list[EvidenceGap] = []
        manual_checks: list[ManualCheck] = []
        trace: list[ReviewTraceStep] = []
        audit_trail: list[LlmAuditMetadata] = []
        policy_decisions: list[EvidenceRequestDecision] = []
        usage = AgenticReviewBudgetUsage()

        def refresh_elapsed_ms() -> int:
            elapsed = max(0.0, self._clock() - start)
            usage.elapsed_ms = int(elapsed * 1000)
            return usage.elapsed_ms

        def deterministic_outputs(
            output_pack: ImpactEvidencePack,
        ) -> tuple[list[AgenticReviewCard], str]:
            return (
                deterministic_review_cards(output_pack, max_cards=active_budget.max_cards),
                _deterministic_cab_summary(output_pack, max_cards=active_budget.max_cards),
            )

        def fallback(reason: str) -> AgenticReviewRun:
            cards, cab_summary = deterministic_outputs(current_pack)
            usage.cards = len(cards)
            refresh_elapsed_ms()
            trace.append(
                ReviewTraceStep(
                    stage="fallback",
                    round=0,
                    summary=f"Fallback: {reason}",
                )
            )
            return AgenticReviewRun(
                snapshot_id=current_pack.snapshot_id,
                llm_enabled=True,
                llm_disabled=False,
                status="fallback",
                objective_question=question,
                objectives=plan.objectives,
                hypotheses=hypotheses,
                evidence_gaps=evidence_gaps,
                manual_checks=manual_checks,
                cards=cards,
                cab_summary=cab_summary,
                deterministic_pack=current_pack,
                trace=trace,
                budget=active_budget,
                budget_usage=usage,
                policy_decisions=policy_decisions,
                audit_trail=audit_trail,
            )

        if runtime is None:
            cards, cab_summary = deterministic_outputs(pack)
            usage.cards = len(cards)
            refresh_elapsed_ms()
            trace.append(
                ReviewTraceStep(
                    stage="runtime_disabled",
                    round=0,
                    summary=(
                        "LLM runtime disabled; returned deterministic cards and CAB summary "
                        "without any transport or network call."
                    ),
                    citation_validation="passed",
                )
            )
            return AgenticReviewRun(
                snapshot_id=pack.snapshot_id,
                llm_enabled=False,
                llm_disabled=True,
                status="disabled",
                objective_question=question,
                cards=cards,
                cab_summary=cab_summary,
                deterministic_pack=pack,
                trace=trace,
                budget=active_budget,
                budget_usage=usage,
                policy_decisions=[],
                audit_trail=[],
            )

        try:
            if active_budget.max_cards <= 0 and pack.impact.findings:
                return fallback(
                    "max_cards is 0 while deterministic impact findings exist; returned "
                    "deterministic zero-card fallback."
                )

            budget_stop = self._pre_planner_stop_reason(active_budget, usage, refresh_elapsed_ms)
            if budget_stop is not None:
                return fallback(budget_stop)

            planner_completion = self._call_planner(
                pack,
                runtime=runtime,
                question=question,
                usage=usage,
                trace=trace,
                audit_trail=audit_trail,
            )
            plan = planner_completion[0]
            budget_stop = self._latency_stop_reason(active_budget, refresh_elapsed_ms)
            if budget_stop is not None:
                return fallback(budget_stop)

            allowed_requests, policy_decisions = self._policy_gate.evaluate(
                plan,
                pack=current_pack,
                budget=active_budget,
            )
            usage.evidence_requests = len(allowed_requests)
            trace.append(
                ReviewTraceStep(
                    stage="policy",
                    round=0,
                    summary=(
                        f"Policy gate allowed {len(allowed_requests)} of "
                        f"{len(plan.evidence_requests)} evidence requests."
                    ),
                )
            )
            budget_stop = self._latency_stop_reason(active_budget, refresh_elapsed_ms)
            if budget_stop is not None:
                return fallback(budget_stop)

            if allowed_requests and self._enricher is not None:
                current_pack = self._enricher.run_enrichers(
                    allowed_requests,
                    prior_pack=current_pack,
                )
                usage.enrichers_executed = len(allowed_requests)
                trace.append(
                    ReviewTraceStep(
                        stage="evidence",
                        round=0,
                        summary=(
                            f"Executed {len(allowed_requests)} local parse-only evidence "
                            "enricher request(s)."
                        ),
                    )
                )
            elif allowed_requests:
                trace.append(
                    ReviewTraceStep(
                        stage="evidence",
                        round=0,
                        summary=(
                            "Evidence enrichment skipped safely because no enricher was supplied; "
                            "kept deterministic pack unchanged."
                        ),
                    )
                )
            else:
                trace.append(
                    ReviewTraceStep(
                        stage="evidence",
                        round=0,
                        summary="No policy-allowed evidence requests required enrichment.",
                    )
                )
            budget_stop = self._latency_stop_reason(active_budget, refresh_elapsed_ms)
            if budget_stop is not None:
                return fallback(budget_stop)

            review_stop = self._pre_reviewer_stop_reason(
                active_budget,
                usage,
                refresh_elapsed_ms,
            )
            if review_stop is not None:
                return fallback(review_stop)

            review_budget = active_budget.model_copy(
                update={"max_llm_calls": self._remaining_llm_calls(active_budget, usage)}
            )
            review_result = run_hypothesis_review(
                current_pack,
                plan,
                runtime=runtime,
                budget=review_budget,
                question=question,
                transport=self._transport,
            )
            usage.review_rounds = review_result.review_rounds
            usage.llm_calls += review_result.llm_calls
            hypotheses = review_result.hypotheses
            evidence_gaps = review_result.evidence_gaps
            manual_checks = review_result.manual_checks
            audit_trail.extend(review_result.audit_trail)
            trace.extend(review_result.trace)
            if review_result.status == "fallback":
                return fallback(
                    "Hypothesis review fallback: "
                    f"{review_result.fallback_reason or 'unknown reviewer failure'}"
                )
            budget_stop = self._latency_stop_reason(active_budget, refresh_elapsed_ms)
            if budget_stop is not None:
                return fallback(budget_stop)

            synthesis_stop = self._pre_synthesis_stop_reason(
                active_budget,
                usage,
                refresh_elapsed_ms,
            )
            if synthesis_stop is not None:
                return fallback(synthesis_stop)

            synthesis_budget = active_budget.model_copy(
                update={"max_llm_calls": self._remaining_llm_calls(active_budget, usage)}
            )
            synthesis_result = create_agentic_synthesis(
                current_pack,
                plan,
                hypotheses,
                evidence_gaps,
                manual_checks,
                runtime=runtime,
                budget=synthesis_budget,
                transport=self._transport,
            )
            usage.llm_calls += synthesis_result.llm_calls
            audit_trail.extend(synthesis_result.audit_trail)
            trace.extend(synthesis_result.trace)
            if synthesis_result.status == "fallback":
                return fallback(
                    "Synthesis fallback: "
                    f"{synthesis_result.fallback_reason or 'unknown synthesis failure'}"
                )
            budget_stop = self._latency_stop_reason(active_budget, refresh_elapsed_ms)
            if budget_stop is not None:
                return fallback(budget_stop)

            usage.cards = len(synthesis_result.cards)
            refresh_elapsed_ms()
            run = AgenticReviewRun(
                snapshot_id=current_pack.snapshot_id,
                llm_enabled=True,
                llm_disabled=False,
                status="completed",
                objective_question=question,
                objectives=plan.objectives,
                hypotheses=hypotheses,
                evidence_gaps=evidence_gaps,
                manual_checks=manual_checks,
                cards=synthesis_result.cards,
                cab_summary=synthesis_result.cab_summary,
                deterministic_pack=current_pack,
                trace=trace,
                budget=active_budget,
                budget_usage=usage,
                policy_decisions=policy_decisions,
                audit_trail=audit_trail,
            )
            try:
                return validate_agentic_run(run, pack=current_pack)
            except Exception as exc:
                return fallback(_exception_reason("Validator failed", exc))
        except Exception as exc:
            return fallback(_exception_reason("Agentic review orchestrator failed", exc))

    def _call_planner(
        self,
        pack: ImpactEvidencePack,
        *,
        runtime: LlmRuntimeConfig,
        question: str | None,
        usage: AgenticReviewBudgetUsage,
        trace: list[ReviewTraceStep],
        audit_trail: list[LlmAuditMetadata],
    ) -> tuple[AgenticReviewPlan, LlmAuditMetadata]:
        request = build_review_plan_request(pack, question=question)
        client = OpenAICompatibleClient(runtime=runtime, transport=self._transport)
        completion: LlmCompletion | None = None
        try:
            completion = client.chat(request)
            usage.llm_calls += 1
            usage.planner_rounds += 1
            _validate_completion_safety(completion)
            plan = parse_review_plan_content(
                completion.content,
                allowed_citation_ids=request.citation_ids,
                allowed_enrichers=ALLOWED_ENRICHERS,
            )
        except Exception as exc:
            if completion is None:
                trace.append(
                    ReviewTraceStep(
                        stage="planner",
                        round=1,
                        summary="Planner call failed before a completion was available.",
                        citation_validation="failed",
                    )
                )
            else:
                audit_trail.append(completion.audit)
                trace.append(
                    ReviewTraceStep(
                        stage="planner",
                        round=1,
                        summary="Planner completion failed safety, citation, or schema validation.",
                        llm_audit=completion.audit,
                        citation_validation="failed",
                    )
                )
            raise RuntimeError(_exception_reason("Planner validation failed", exc)) from exc

        planner_audit = completion.audit.model_copy(update={"citation_validation": "passed"})
        audit_trail.append(planner_audit)
        trace.append(
            ReviewTraceStep(
                stage="planner",
                round=1,
                summary=(
                    f"Planner produced {len(plan.objectives)} objectives and "
                    f"{len(plan.evidence_requests)} evidence requests."
                ),
                llm_audit=planner_audit,
                citation_validation="passed",
            )
        )
        return plan, planner_audit

    @staticmethod
    def _pre_planner_stop_reason(
        budget: AgenticReviewBudget,
        usage: AgenticReviewBudgetUsage,
        refresh_elapsed_ms: Callable[[], int],
    ) -> str | None:
        latency_stop = AgenticReviewAssistant._latency_stop_reason(budget, refresh_elapsed_ms)
        if latency_stop is not None:
            return latency_stop
        if budget.max_planner_rounds <= 0:
            return "Planner budget exhausted before first planner round."
        if AgenticReviewAssistant._remaining_llm_calls(budget, usage) <= 0:
            return "LLM call budget exhausted before planner."
        return None

    @staticmethod
    def _pre_reviewer_stop_reason(
        budget: AgenticReviewBudget,
        usage: AgenticReviewBudgetUsage,
        refresh_elapsed_ms: Callable[[], int],
    ) -> str | None:
        latency_stop = AgenticReviewAssistant._latency_stop_reason(budget, refresh_elapsed_ms)
        if latency_stop is not None:
            return latency_stop
        if budget.max_review_rounds <= 0:
            return "Review budget exhausted before hypothesis reviewer stage."
        if AgenticReviewAssistant._remaining_llm_calls(budget, usage) <= 0:
            return "LLM call budget exhausted before hypothesis reviewer stage."
        return None

    @staticmethod
    def _pre_synthesis_stop_reason(
        budget: AgenticReviewBudget,
        usage: AgenticReviewBudgetUsage,
        refresh_elapsed_ms: Callable[[], int],
    ) -> str | None:
        latency_stop = AgenticReviewAssistant._latency_stop_reason(budget, refresh_elapsed_ms)
        if latency_stop is not None:
            return latency_stop
        if AgenticReviewAssistant._remaining_llm_calls(budget, usage) <= 0:
            return "LLM call budget exhausted before synthesis stage."
        return None

    @staticmethod
    def _latency_stop_reason(
        budget: AgenticReviewBudget,
        refresh_elapsed_ms: Callable[[], int],
    ) -> str | None:
        elapsed_ms = refresh_elapsed_ms()
        if budget.max_latency_ms <= 0:
            return "Latency budget is 0 ms; stopped before the next stage."
        if elapsed_ms > budget.max_latency_ms:
            return (
                f"Latency budget exhausted after {elapsed_ms} ms "
                f"(limit {budget.max_latency_ms} ms)."
            )
        return None

    @staticmethod
    def _remaining_llm_calls(
        budget: AgenticReviewBudget,
        usage: AgenticReviewBudgetUsage,
    ) -> int:
        return max(0, budget.max_llm_calls - usage.llm_calls)


def _exception_reason(prefix: str, exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        return prefix
    return f"{prefix}: {message}"
