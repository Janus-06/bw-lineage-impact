# BW Lineage & Change Impact Analyzer — Planning Brief

Date: 2026-06-04 KST
Owner: JC님
Project path: `/Users/jclee/Projects/bw-lineage-impact`
Reference repo inspected locally: `/tmp/bw-modeling-mcp-inspect`
Reference GitHub: https://github.com/dnic-dev/bw-modeling-mcp

## Intent

Build a separate project inspired by `dnic-dev/bw-modeling-mcp`, focused on **read-only SAP BW/4HANA lineage and change impact analysis**.

The user wants to proceed with the three product directions from the prior analysis:

1. **Read-only BW Graph Collector**
   - Connect to SAP BW/4HANA Modeling REST API.
   - Collect BW structural graph data deterministically.
   - Store snapshots for repeatable analysis and diffing.

2. **Lineage Analyzer**
   - Given a BW object, trace upstream/downstream lineage.
   - Output object-level and, where feasible, field-level lineage.
   - Provide human-readable report plus machine-readable graph.

3. **Change Impact Analyzer**
   - Given a proposed change, estimate downstream impact.
   - Examples: ADSO field delete/type change, InfoObject change, Transformation routine change, DTP filter change, CompositeProvider mapping change.
   - Output impacted objects, severity, confidence, and manual verification gaps.

## Non-negotiable Constraints

- Start as **read-only**. No create/update/delete/activate/transport operations in MVP.
- Do not expose or persist SAP credentials in clear text.
- Make deployment easy for enterprise/internal users.
- Each user/environment must be able to run the analyzer **locally** for security reasons. Do not assume a central hosted analyzer backend.
- Snapshots, metadata, reports, and audit logs should stay on the local machine or an explicitly mounted internal volume.
- The program should be usable without an LLM for deterministic graph extraction and rule-based impact analysis.
- Analyze when LLM involvement is beneficial vs unnecessary/risky.
- Prefer deterministic outputs first; LLM should be optional, bounded, and auditable.
- LLM support should target a **local OpenAI-compatible API server** first, e.g. `http://127.0.0.1:<port>/v1`; cloud-hosted LLM providers are not the default path.
- LLM endpoint, model, and API key/token must be supplied by the user at runtime via interactive prompt, CLI flag, environment variable, or secret reference. Do not hardcode or persist real values.
- The BW/MCP-style environment variables from the reference tool (`BW_URL`, `BW_USER`, `BW_PASSWORD`, `BW_CLIENT`, optional `BW_LANGUAGE`) must also be user-supplied at runtime. The project may provide placeholders and validation, but not real values.
- Assume SAP BW/4HANA, not BW 7.5. Reference repo says BW 7.5 may hit HTTP 406/version negotiation issues.

## Confirmed Reference Repo Facts

From local inspection/build of `dnic-dev/bw-modeling-mcp`:

- TypeScript project, package `bw-modeling-mcp`, version `0.7.0`, MIT license.
- `npm ci && npm run build` succeeded locally.
- It exposes MCP tools over stdio using `@modelcontextprotocol/sdk`.
- It connects to live BW using environment variables:
  - `BW_URL`
  - `BW_USER`
  - `BW_PASSWORD`
  - `BW_CLIENT`
  - optional `BW_LANGUAGE`
- It uses Axios, XML parsing, SAP CSRF token/session cookies, and internal REST APIs used by Eclipse BWMT/BW cockpit.
- Relevant implementation files in reference repo:
  - `src/bw-client.ts` — BW HTTP client, CSRF/session handling.
  - `src/tools/dataflow.ts` — `bw_get_dataflow`, uses `/sap/bw/modeling/dmod/8TRANSIENT`.
  - `src/tools/search.ts` — `bw_search` and `bw_xref`, uses `/sap/bw/modeling/repo/is/bwsearch` and `/sap/bw/modeling/repo/is/xref`.
  - `src/tools/transformation.ts` — reads Transformation XML, source/target, field mappings, formulas, routine references.
  - `src/tools/dtp.ts` — reads DTP XML, source/target, transformation reference, filters/routines.
  - `src/tools/composite_provider.ts` — reads HCPR structure/mappings/joins.
  - `src/tools/query.ts` and `src/tools/cp_components.ts` — reads query/CKF/RKF/structures.
  - `src/tools/processchain.ts` — reads process chain steps/dependencies/variant detail.
  - `src/tools/roles.ts` — query-to-role assignment.
- Important API endpoints from `ARCHITECTURE.md`:
  - `/sap/bw/modeling/dmod/8TRANSIENT` for transient dataflow graph.
  - `/sap/bw/modeling/repo/is/xref` for where-used/cross-reference.
  - `/sap/bw/modeling/repo/is/bwsearch` for search.
  - `/sap/bw/modeling/trfn/{name}/m` for Transformation.
  - `/sap/bw/modeling/dtpa/{name}/m` for DTP.
  - `/sap/bw/modeling/hcpr/{name}` for CompositeProvider.
  - `/sap/bw/modeling/query/{compid}/{objvers}` for BW Query.
  - `/sap/bw/modeling/rspc/{name}/m` and `/sap/bw4/v1/modeling/processtypes/{type}/variants/{name}/m` for process chain detail.
- Known limitation: `bw_get_dataflow` notes routine-based lookups are not reflected; structural BW dependencies only.
- The reference repo has write/delete tools. This project should not copy those into MVP runtime surface.

## Desired Product Shape

### Interfaces

Plan for multiple interfaces, but prioritize easy deployment:

- CLI first, e.g. `bwli collect`, `bwli lineage`, `bwli impact`, `bwli report`.
- Optional local web UI later for graph exploration.
- Optional MCP server later, but not required for MVP if CLI is easier to deploy.
- JSON output should be stable for automation.

### Deployment

Analyze and recommend deployment approach:

- Local-first execution model: each analyst/developer runs the analyzer on their own secured workstation or approved internal runtime.
- No central hosted analyzer service in MVP.
- Python CLI package via `pipx`/`uvx` or wheel.
- Docker image for enterprise users who do not want local Python setup.
- Single binary option if feasible, or explain why not.
- Config via env vars + config file + secret references.
- Offline/air-gapped considerations.

### Data Model

Plan a graph snapshot model:

- Nodes: BW objects with `type`, `name`, `description`, `status`, `version`, `source_system`, metadata.
- Edges: structural flow, where-used, transformation source/target, DTP source/target, process chain step dependency, query/provider, role publish.
- Field lineage: source field -> transformation rule -> target field, including confidence.
- Change impact: impacted nodes/edges/fields with severity and confidence.

### LLM Involvement Analysis

Classify tasks into:

- **No LLM needed / should be deterministic**:
  - API calls
  - graph extraction
  - parsing XML/JSON
  - impact rules for known object/field changes
  - graph diffing
  - validation checks

- **LLM optional / useful**:
  - plain-language explanation of transformation logic
  - summarizing long lineage reports
  - translating ABAP/AMDP routine snippets into business explanation
  - explaining **Native SQL View** logic when the object is not a graphical view
  - suggesting SQL readability/performance optimization candidates for native SQL views, clearly marked as advisory and requiring developer review
  - suggesting manual verification steps for ambiguous dynamic dependencies
  - generating executive/business impact summaries

- **LLM risky / should be gated**:
  - deciding actual transport/change execution
  - making high-confidence claims about dynamic routine dependencies without source evidence
  - accessing or storing credentials
  - modifying BW objects

Design an architecture where the LLM receives only sanitized graph/routine snippets, returns explanations with citations to node/edge IDs, and never performs BW API calls directly in MVP.

## Expected Planning Output from Claude

Please produce a practical implementation plan in Korean or English. Include:

1. Recommended architecture and stack.
2. Repository layout with exact files/directories.
3. Deployment strategy and tradeoffs.
4. LLM involvement decision matrix and implementation pattern.
5. MVP milestone plan with bite-sized tasks.
6. Test strategy, including fixture-based tests without live BW and optional live smoke tests.
7. Security/credential handling boundaries.
8. First implementation slice that can be built immediately.
9. Risks/unknowns and how to validate them.
10. Non-goals and approval gates.

Planning only. Do not modify project files. Do not run write operations. Inspect the reference repo as needed.
