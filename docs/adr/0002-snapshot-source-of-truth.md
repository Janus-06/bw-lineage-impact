# ADR 0002: Snapshot as source of truth

## Decision

Collectors write local snapshot manifests and payloads. Analysis reads snapshots rather than repeatedly calling BW endpoints.

## Rationale

Snapshots make lineage, impact, diffing, reports, and tests repeatable. They also let teams inspect exactly what evidence supported a finding.

## Consequences

- Snapshot files must not persist credentials or absolute private paths.
- Fixture snapshots support offline tests and demos.
- Live collection remains explicitly gated.
