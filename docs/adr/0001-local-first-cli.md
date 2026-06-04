# ADR 0001: Local-first CLI architecture

## Decision

The MVP is a local-first Python CLI. Each analyst/developer runs it in their own secured environment. A central hosted analyzer backend is out of scope for MVP.

## Rationale

BW metadata, object names, snapshots, reports, and LLM audit logs can be sensitive. Local execution keeps data inside the user's workstation or approved internal runtime.

## Consequences

- Primary distribution targets are `pipx`/`uvx`, wheel, and Docker.
- Configuration is supplied by runtime env vars, flags, config files, or secret references.
- Web UI and MCP server surfaces are postponed until read-only boundaries are proven.
