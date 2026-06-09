# ADR 0003: Local LLM explainer policy

## Decision

LLM support is optional, disabled by default, and limited to a user-supplied local OpenAI-compatible endpoint.

## Allowed use

Implemented advisory surfaces:

- Explain Native SQL View logic when deterministic SQL evidence is available.
- Draft SQL review candidates from sanitized SQL evidence; SQL execution stays blocked.
- Draft Impact review notes from deterministic affected-object evidence.
- Draft Lineage graph notes from deterministic bounded graph evidence, including level/truncation/cycle context.

Additional candidate surfaces, if implemented later, must follow the same guardrails:

- Object summary cards for selected ADSO/HCPR/TRFN/QUERY objects.
- Manual verification checklist or test-plan suggestions for BW Modeling Tools/Eclipse.
- Migration/remediation notes for risky or truncated dependencies.
- Snapshot/capture result summaries that describe counts and gaps, not secrets or raw payloads.

## Required guardrails

- Deterministic extraction, Lineage, Impact, and SQL parsing remain authoritative.
- LLM calls use only sanitized/capped evidence JSON and citation IDs.
- The endpoint must be local OpenAI-compatible, e.g. `http://127.0.0.1:<port>/v1`.
- If LLM config is missing or disabled, API responses return deterministic payloads with `status: disabled` and do not perform network I/O.
- Prompt and response validation must reject leaked credentials/internal URLs and require citations for non-empty advice.

## Forbidden use

- Calling BW APIs.
- Loading credentials.
- Making final severity/confidence decisions.
- Automatically rewriting or applying SQL/BW object changes.
- Sending raw snapshots or secrets to an LLM.
