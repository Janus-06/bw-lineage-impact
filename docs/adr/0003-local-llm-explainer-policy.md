# ADR 0003: Local LLM explainer policy

## Decision

LLM support is optional, disabled by default, and limited to a user-supplied local OpenAI-compatible endpoint.

## Allowed use

- Explain ABAP/AMDP routines from sanitized snippets.
- Explain Native SQL View logic when deterministic SQL evidence is available.
- Suggest advisory SQL readability/performance optimization candidates.
- Summarize reports and produce manual verification checklists.

## Forbidden use

- Calling BW APIs.
- Loading credentials.
- Making final severity/confidence decisions.
- Automatically rewriting or applying SQL/BW object changes.
- Sending raw snapshots or secrets to an LLM.
