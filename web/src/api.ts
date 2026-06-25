export type ConfigSource = 'env' | 'ui' | 'unset';
export type ConnectionStatus = 'unconfigured' | 'untested' | 'ok' | 'failed' | 'stale';
export type Direction = 'upstream' | 'downstream' | 'both';
export type DataflowDirection = 'upwards' | 'downwards' | 'both';
export type AppTab = 'lineage' | 'impact' | 'ask' | 'query' | 'sql' | 'glossary';
export type ChangeType =
  | 'field_removed'
  | 'field_type_changed'
  | 'infoobject_attribute_changed'
  | 'infoobject_type_changed'
  | 'routine_changed'
  | 'dtp_filter_changed'
  | 'compositeprovider_mapping_changed';
export type TourStatus = 'ok' | 'disabled' | string;

export interface HealthResponse {
  status: string;
  local_only: boolean;
  read_only: boolean;
  llm_enabled_by_default: boolean;
  version: string;
}

export interface RuntimeConfigResponse {
  storage: 'process-memory' | 'process-memory+project-env';
  connection_status: ConnectionStatus;
  bw: {
    source: ConfigSource;
    configured: boolean;
    url: string | null;
    user: string | null;
    password: string | null;
    client: string | null;
    language: string;
    verify_ssl: boolean;
    ca_bundle: string | null;
    trust_env: boolean;
  };
  llm: {
    source: ConfigSource;
    enabled: boolean;
    configured: boolean;
    base_url: string | null;
    model: string | null;
    api_key: string | null;
  };
}

export interface RuntimeConfigRequest {
  persist_to_env?: boolean;
  bw?: {
    url: string;
    user: string;
    password: string;
    client: string;
    language: string;
    verify_ssl: boolean;
    ca_bundle?: string;
    trust_env?: boolean;
  };
  llm?: {
    enabled: boolean;
    base_url?: string;
    model?: string;
    api_key?: string;
  };
}

export interface LiveOperationSummary {
  name: string;
  label: string;
  ok: boolean;
  status: 'ok' | 'error';
  payload_kind: string | null;
  item_count: number | null;
  error: string | null;
}

export interface LiveSmokeResult {
  mode: string;
  read_only: boolean;
  status: 'ok' | 'partial' | 'error';
  operations: LiveOperationSummary[];
}

export interface LiveCaptureSummary {
  mode: string;
  succeeded: number;
  failed: number;
  operations: LiveOperationSummary[];
}

export interface CaptureScopeItem {
  object_id: string;
  object_type: string;
  role: 'selected' | 'discovered';
  operation: string;
  status: 'selected' | 'ok' | 'error' | 'skipped';
  error: string | null;
  evidence_ids: string[];
  metadata: Record<string, unknown>;
}

export interface GlossaryTerm {
  id: string;
  term: string;
  normalized_term: string;
  source: string;
  candidate: boolean;
  lifecycle?: 'candidate' | 'confirmed' | 'rejected';
  occurrences?: number;
  object_id: string | null;
  object_type: string | null;
  field_name: string | null;
  evidence_ids: string[];
  metadata: Record<string, unknown>;
}

export interface RequestFreshnessEntry {
  request_tsn?: string | null;
  tsn?: string | null;
  status?: string | null;
  records?: number | null;
  timestamp?: string | null;
  [key: string]: unknown;
}

export interface RequestFreshnessResponse {
  target?: string | null;
  target_type?: string | null;
  latest?: RequestFreshnessEntry | null;
  requests?: RequestFreshnessEntry[];
  [key: string]: unknown;
}

export interface DomainSummary {
  node_count?: number;
  edge_count?: number;
  object_types?: string[];
  layer_counts?: Record<string, number>;
  [key: string]: unknown;
}

export interface GuidedTourStep {
  id: string;
  title: string;
  description: string;
  node_ids: string[];
  edge_ids: string[];
  [key: string]: unknown;
}

interface GuidedTourResponseBase {
  schema_version: string;
  status: TourStatus;
  advisory: boolean;
  config_required: boolean;
  message: string;
  summary: string;
  tour: GuidedTourStep[];
  citations: string[];
  domain_summary: DomainSummary;
  llm_audit?: Record<string, unknown>;
}

export interface RepositoryNode {
  id: string;
  parent_path: string;
  path: string;
  name: string;
  description: string;
  object_type: string;
  object_subtype: string | null;
  status: string | null;
  has_children: boolean;
  self_url: string | null;
  fiori_only: boolean;
  children_path: string | null;
  metadata: Record<string, unknown>;
}

export interface RepositoryResponse {
  path: string;
  source: 'live' | 'cache' | 'empty';
  count: number;
  items: RepositoryNode[];
  action_required: string | null;
}

export interface BwSearchItem {
  object_id: string;
  object_type: string;
  name: string | null;
  source: 'live';
}

export interface BwSearchResponse {
  mode: string;
  read_only: boolean;
  search_term: string;
  object_type: string | null;
  count: number;
  truncated: boolean;
  items: BwSearchItem[];
}

export interface SnapshotSummary {
  id: string;
  created_at: string;
  mode: string;
  source: string;
  manifest_path: string | null;
  object_count: number;
  edge_count: number;
  capture?: LiveCaptureSummary;
  capture_scope?: CaptureScopeItem[];
}

export interface SnapshotListResponse {
  snapshots: SnapshotSummary[];
}

export interface CaptureScopeResponse {
  snapshot_id: string;
  items: CaptureScopeItem[];
}

export interface GlossaryResponse {
  snapshot_id: string;
  query: string | null;
  count: number;
  counts?: GlossaryAggregateResponse | null;
  items: GlossaryTerm[];
}

export interface GlossaryAggregateResponse {
  total: number;
  candidate: number;
  confirmed: number;
  rejected: number;
  object_count: number;
  query?: string | null;
  semantics?: string[];
}

export interface ObjectField {
  name: string;
  type?: string;
  role?: string;
  description?: string;
  [key: string]: unknown;
}

export interface ObjectFieldsResponse {
  snapshot_id: string;
  object_id: string;
  count: number;
  fields: ObjectField[];
}

export interface QueryAnalysisResponse {
  snapshot_id: string;
  query_name: string;
  read_only: boolean;
  source: 'snapshot' | string;
  result: Record<string, unknown>;
}

export interface CatalogObject {
  id: string;
  name: string | null;
  type: string;
  label: string | null;
  summary?: string | null;
  tags?: string[];
  complexity?: number | null;
  layer?: string | null;
  metadata: Record<string, unknown>;
  evidence_ids: string[];
}

export interface CatalogObjectDetail extends CatalogObject {
  incoming_count: number;
  outgoing_count: number;
  glossary_terms?: GlossaryTerm[];
}

export interface ObjectListResponse {
  items: CatalogObject[];
  next_cursor: string | null;
  limit: number;
}

export interface LineageNode extends CatalogObject {
  summary?: string | null;
  tags?: string[];
  complexity?: number | null;
  layer?: string | null;
  evidence_ids: string[];
}

export interface LineageEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  confidence: string;
  metadata: Record<string, unknown>;
  evidence_ids: string[];
}

export interface LineageResponse {
  schema_version: string;
  snapshot_id: string;
  start_id: string;
  direction: Direction;
  depth: number;
  node_cap: number;
  edge_cap: number;
  nodes: LineageNode[];
  edges: LineageEdge[];
  levels: Record<string, number>;
  truncated: boolean;
  truncation: {
    node_cap_reached: boolean;
    edge_cap_reached: boolean;
    depth_limit_reached: boolean;
    omitted_neighbor_total: number;
  };
  omitted_neighbor_counts: Record<string, number>;
  cycles_detected: boolean;
  evidence_ids: string[];
}

export interface LineageAdviceResponse {
  schema_version: string;
  status: 'ok' | 'disabled';
  advisory: boolean;
  config_required: boolean;
  message: string;
  advice: string;
  citations: string[];
  llm_audit?: Record<string, unknown>;
  lineage: LineageResponse;
}

export interface LineageTourResponse extends GuidedTourResponseBase {
  lineage: LineageResponse;
}

export interface ImpactScenarioResponse {
  schema_version: string;
  snapshot_id: string;
  deterministic: boolean;
  advisory: boolean;
  scenario: {
    object_id: string;
    object_type: string;
    change_type: ChangeType;
    field: string | null;
    value_description: string | null;
    description: string | null;
    changes_path_required: boolean;
  };
  affected_objects: Array<{
    object_id: string;
    object_type: string;
    severity: 'HIGH' | 'MEDIUM' | 'LOW' | 'UNKNOWN';
    confidence: string;
    reason: string;
    evidence_ids: string[];
    evidence_node_ids: string[];
    evidence_edge_ids: string[];
    manual_verification: boolean;
    glossary_terms?: GlossaryTerm[];
  }>;
  lineage_bounds: {
    depth: number;
    node_cap: number;
    edge_cap: number;
    truncated: boolean;
    omitted_neighbor_counts: Record<string, number>;
    cycles_detected: boolean;
  };
}

export interface ImpactAdviceResponse {
  schema_version: string;
  status: 'ok' | 'disabled';
  advisory: boolean;
  config_required: boolean;
  message: string;
  advice: string;
  citations: string[];
  llm_audit?: Record<string, unknown>;
  impact: ImpactScenarioResponse;
}

export interface ImpactTourResponse extends GuidedTourResponseBase {
  impact: ImpactScenarioResponse;
}

export interface ImpactReportResponse {
  schema_version: string;
  changes: Array<{
    id: string;
    object_id: string;
    object_type: string;
    change_type: ChangeType;
    field: string | null;
    before: Record<string, unknown>;
    after: Record<string, unknown>;
    metadata: Record<string, unknown>;
  }>;
  findings: Array<{
    id: string;
    change_id: string;
    impacted_object_id: string;
    impacted_object_type: string;
    severity: 'HIGH' | 'MEDIUM' | 'LOW' | 'UNKNOWN';
    confidence: string;
    reason: string;
    evidence_node_ids: string[];
    evidence_edge_ids: string[];
    manual_verification: boolean;
  }>;
}

export interface QueryExposureEvidence {
  query_id: string;
  description: string | null;
  provider_object_ids: string[];
  variable_names: string[];
  calculated_key_figure_names: string[];
  restricted_key_figure_names: string[];
  field_names: string[];
  filter_count: number;
  layout_fields: string[];
  exposed_object_ids: string[];
  matched_finding_ids: string[];
  manual_check_notes: string[];
  metadata: Record<string, unknown>;
}

export interface SqlReferenceEvidence {
  view_id: string;
  parser: string;
  confidence: string;
  referenced_object_ids: string[];
  referenced_column_names: string[];
  reference_edge_ids: string[];
  fragment_ids: string[];
  matched_finding_ids: string[];
  manual_check_notes: string[];
}

export interface FreshnessEvidence {
  object_id: string;
  object_type: string | null;
  request_count: number;
  latest_request_tsn: string | null;
  latest_status: string | null;
  latest_timestamp: string | null;
  latest_records: number | null;
  evidence_available: boolean;
  manual_check_notes: string[];
}

export interface ManualVerificationGap {
  id: string;
  source: 'impact' | 'query' | 'sql' | 'freshness';
  reason: string;
  object_id: string | null;
  object_type: string | null;
  finding_id: string | null;
  evidence_ids: string[];
}

export interface ImpactReviewResponse {
  schema_version: string;
  snapshot_id: string | null;
  deterministic: boolean;
  read_only: boolean;
  execution_blocked: boolean;
  final_authority: 'impact.py';
  authority_note: string;
  impact: ImpactReportResponse;
  query_evidence: QueryExposureEvidence[];
  sql_evidence: SqlReferenceEvidence[];
  freshness_evidence: FreshnessEvidence[];
  manual_verification_gaps: ManualVerificationGap[];
  coverage_summary: Record<string, number>;
}

export type AssistantContextKind =
  | 'object'
  | 'lineage'
  | 'impact'
  | 'impact_review'
  | 'freshness'
  | 'manual_check';
export type AssistantReviewStatus = 'ok' | 'disabled' | 'fallback';

export interface AssistantEvidenceContext {
  id: string;
  kind: AssistantContextKind;
  title: string;
  body: string;
  object_id?: string | null;
  object_type?: string | null;
  citation_id?: string | null;
  source_ids?: string[];
}

export interface AssistantManualCheck {
  id: string;
  title: string;
  tool: 'BWMT' | 'Eclipse' | 'HANA_Studio' | 'manual';
  steps_summary: string;
  related_context_ids: string[];
  citation_ids: string[];
}

export interface AssistantSafety {
  read_only: boolean;
  no_live_bw_calls: boolean;
  no_bw_query_execution: boolean;
  no_data_preview: boolean;
  no_raw_snapshot_payload: boolean;
  deterministic_authority: 'impact.py';
  llm_used: boolean;
  citation_validation: AgenticCitationValidationStatus;
  fallback_reason: string | null;
}

export interface AssistantReviewRequest {
  prompt: string;
  snapshot_id?: string | null;
  object_id?: string | null;
  preset?: string | null;
  context?: AssistantEvidenceContext[];
  max_context_items?: number;
}

export interface AssistantReviewResponse {
  status: AssistantReviewStatus;
  answer: string;
  citations: string[];
  unknowns: string[];
  confidence: 'high' | 'medium' | 'low' | 'unknown';
  manual_checks: AssistantManualCheck[];
  safety: AssistantSafety;
}

export type AgenticReviewStatus = 'completed' | 'disabled' | 'fallback' | 'failed';
export type AgenticCitationValidationStatus = 'not_validated' | 'passed' | 'failed';
export type AgenticReviewCardKind =
  | 'deterministic_finding'
  | 'llm_proposed_concern'
  | 'manual_verification_required';

export interface LlmAuditMetadata {
  provider?: 'openai-compatible' | string;
  endpoint?: string;
  runtime_endpoint_source?: 'runtime' | string;
  runtime_model_source?: 'runtime' | string;
  model?: string;
  prompt_sha256?: string;
  sanitized_input_sha256?: string;
  request_citation_ids?: string[];
  citation_validation?: AgenticCitationValidationStatus | string;
  response_timestamp?: string;
  response_id?: string | null;
  usage?: Record<string, unknown> | null;
  [key: string]: unknown;
}

export interface ReviewObjective {
  id: string;
  title: string;
  rationale: string;
  citation_ids: string[];
}

export interface ReviewHypothesis {
  id: string;
  statement: string;
  status: 'proposed' | 'supported' | 'refuted';
  severity_opinion: 'HIGH' | 'MEDIUM' | 'LOW' | 'UNKNOWN' | null;
  supports_finding_ids: string[];
  confidence_rationale: string;
  citation_ids: string[];
}

export interface EvidenceGap {
  id: string;
  description: string;
  missing_evidence: string;
  suggested_local_action:
    | 'reparse_query_xml'
    | 'reparse_native_sql_view'
    | 'lookup_request_freshness'
    | 'recompute_impact_pack'
    | null;
  related_object_id: string | null;
  citation_ids: string[];
}

export interface ManualCheck {
  id: string;
  title: string;
  tool: 'BWMT' | 'Eclipse' | 'HANA_Studio' | 'manual';
  steps_summary: string;
  priority: 'HIGH' | 'MEDIUM' | 'LOW' | 'UNKNOWN';
  related_finding_ids: string[];
  citation_ids: string[];
}

export interface AgenticReviewCard {
  id: string;
  kind: AgenticReviewCardKind;
  title: string;
  body: string;
  severity_label: 'HIGH' | 'MEDIUM' | 'LOW' | 'UNKNOWN' | null;
  review_priority: number;
  source_finding_id: string | null;
  citation_ids: string[];
}

export interface ReviewTraceStep {
  stage: string;
  round: number;
  summary: string;
  llm_audit: LlmAuditMetadata | null;
  citation_validation: AgenticCitationValidationStatus;
}

export interface AgenticReviewBudget {
  max_planner_rounds: number;
  max_evidence_requests: number;
  max_review_rounds: number;
  max_llm_calls: number;
  max_cards: number;
  max_latency_ms: number;
}

export interface AgenticReviewBudgetUsage {
  planner_rounds: number;
  evidence_requests: number;
  enrichers_executed: number;
  review_rounds: number;
  llm_calls: number;
  cards: number;
  elapsed_ms: number;
}

export interface EvidenceRequestDecision {
  request_id: string;
  allowed: boolean;
  reason: string;
}

export interface AgenticReviewRun {
  schema_version: string;
  snapshot_id: string | null;
  llm_enabled: boolean;
  status: AgenticReviewStatus;
  objective_question: string | null;
  objectives: ReviewObjective[];
  hypotheses: ReviewHypothesis[];
  evidence_gaps: EvidenceGap[];
  manual_checks: ManualCheck[];
  cards: AgenticReviewCard[];
  cab_summary: string;
  deterministic_pack: ImpactReviewResponse;
  trace: ReviewTraceStep[];
  budget: AgenticReviewBudget;
  budget_usage: AgenticReviewBudgetUsage;
  policy_decisions: EvidenceRequestDecision[];
  audit_trail: LlmAuditMetadata[];
  llm_disabled: boolean;
}

export interface AgenticReviewRequest {
  object_id: string;
  change_type: ChangeType;
  field?: string | null;
  value_description?: string | null;
  description?: string | null;
  depth: number;
  node_cap: number;
  edge_cap: number;
  query_names?: string[];
  include_impacted_queries?: boolean;
  include_freshness?: boolean;
  sql_views?: Array<{ view_id: string; sql_file?: string; sql_text?: string }>;
  question?: string | null;
  objectives_hint?: string[];
  include_korean_summary?: boolean;
  max_planner_rounds?: number | null;
  max_evidence_requests?: number | null;
  max_review_rounds?: number | null;
  max_llm_calls?: number | null;
  max_cards?: number | null;
  max_latency_ms?: number | null;
}

export interface SqlExplainResponse {
  schema_version: string;
  advisory: boolean;
  execution_blocked: boolean;
  execution_disabled_warning: string;
  target: string;
  format: 'json' | 'md';
  content: string;
  result: {
    view: { id: string; object_type: string };
    confidence: string;
    parser: string;
    reference_edges: Array<{ id: string; source_object_id: string; target_object_id: string }>;
    columns: Array<{ id: string; column_name: string; expression: string; table_alias?: string | null }>;
    fragments: Array<{ id: string; kind: string; text: string }>;
  };
  citations: string[];
  referenced_objects: string[];
  referenced_fields: Array<{ id: string; table_alias: string | null; column_name: string; expression: string }>;
  glossary_terms: GlossaryTerm[];
}

export interface SqlDraftResponse {
  schema_version: string;
  status: 'ok' | 'disabled';
  advisory: boolean;
  execution_blocked: boolean;
  config_required: boolean;
  message?: string;
  target_dialect: string;
  draft_sql: string;
  citations: string[];
  llm_audit?: Record<string, unknown>;
}

export async function getHealth(): Promise<HealthResponse> {
  return getJson<HealthResponse>('/api/health');
}

export async function getRuntimeConfig(): Promise<RuntimeConfigResponse> {
  return getJson<RuntimeConfigResponse>('/api/v1/runtime-config');
}

export async function putRuntimeConfig(body: RuntimeConfigRequest): Promise<RuntimeConfigResponse> {
  return putJson<RuntimeConfigResponse>('/api/v1/runtime-config', body);
}

export async function clearRuntimeConfig(): Promise<RuntimeConfigResponse> {
  const response = await fetch('/api/v1/runtime-config', { method: 'DELETE' });
  return parseJsonResponse<RuntimeConfigResponse>(response);
}

export async function listSnapshots(): Promise<SnapshotListResponse> {
  return getJson<SnapshotListResponse>('/api/v1/snapshots');
}

export async function getRepository(options: {
  path?: string;
  refresh?: boolean;
  confirmReadOnly?: boolean;
} = {}): Promise<RepositoryResponse> {
  const query = new URLSearchParams();
  if (options.path) query.set('path', options.path);
  if (options.refresh) query.set('refresh', 'true');
  if (options.confirmReadOnly) query.set('confirm_read_only', 'true');
  const suffix = query.toString() ? `?${query}` : '';
  return getJson<RepositoryResponse>(`/api/v1/repository${suffix}`);
}

export async function searchBwObjects(options: {
  confirmReadOnly: boolean;
  searchTerm: string;
  objectType?: string;
  limit?: number;
}): Promise<BwSearchResponse> {
  return postJson<BwSearchResponse>('/api/v1/bw/search', {
    confirm_read_only: options.confirmReadOnly,
    search_term: options.searchTerm,
    object_type: options.objectType || undefined,
    limit: options.limit ?? 20,
  });
}

export async function refreshSnapshotFromBw(snapshotId: string): Promise<SnapshotSummary> {
  return postJson<SnapshotSummary>(`/api/v1/snapshots/${encodeURIComponent(snapshotId)}/refresh`, {
    confirm_read_only: true,
  });
}

export async function getCaptureScope(snapshotId: string): Promise<CaptureScopeResponse> {
  return getJson<CaptureScopeResponse>(`/api/v1/snapshots/${encodeURIComponent(snapshotId)}/capture-scope`);
}

export async function getGlossary(snapshotId: string, query?: string): Promise<GlossaryResponse> {
  const params = new URLSearchParams();
  if (query?.trim()) params.set('query', query.trim());
  const suffix = params.toString() ? `?${params}` : '';
  return getJson<GlossaryResponse>(`/api/v1/snapshots/${encodeURIComponent(snapshotId)}/glossary${suffix}`);
}

export async function getGlossaryAggregate(query?: string): Promise<GlossaryAggregateResponse> {
  const params = new URLSearchParams();
  if (query?.trim()) params.set('query', query.trim());
  const suffix = params.toString() ? `?${params}` : '';
  return getJson<GlossaryAggregateResponse>(`/api/v1/glossary/aggregate${suffix}`);
}

export async function postGlossaryLifecycle(termId: string, lifecycle: 'candidate' | 'confirmed' | 'rejected'): Promise<GlossaryTerm> {
  return postJson<GlossaryTerm>(`/api/v1/glossary/${encodeURIComponent(termId)}/lifecycle`, { lifecycle });
}

export async function captureFixtureSnapshot(fixturePath: string): Promise<SnapshotSummary> {
  return postJson<SnapshotSummary>('/api/v1/snapshots/capture', { fixture_path: fixturePath });
}

export async function captureLiveSnapshot(options: {
  confirmReadOnly: boolean;
  objectNames: string[];
  searchTerms?: string[];
  queries?: string[];
  objectType?: string;
  sourceSystem?: string;
  dataflowDirection?: DataflowDirection;
  dataflowLevels?: number;
  includeRequestFreshness?: boolean;
  requestFreshnessTop?: number;
}): Promise<SnapshotSummary> {
  return postJson<SnapshotSummary>('/api/v1/snapshots/capture', {
    confirm_read_only: options.confirmReadOnly,
    search_terms: options.searchTerms ?? [],
    object_names: options.objectNames,
    queries: options.queries ?? [],
    include_dataflow: true,
    include_xref: true,
    include_request_freshness: options.includeRequestFreshness ?? false,
    request_freshness_top: options.requestFreshnessTop,
    object_type: options.objectType,
    source_system: options.sourceSystem,
    dataflow_direction: options.dataflowDirection,
    dataflow_levels: options.dataflowLevels,
  });
}

export async function postConnectionTest(searchTerm: string = 'Z*'): Promise<LiveSmokeResult> {
  return postJson<LiveSmokeResult>('/api/v1/connection/test', {
    confirm_read_only: true,
    search_term: searchTerm || 'Z*',
  });
}

export async function listObjects(
  snapshotId: string,
  params: { q?: string; type?: string; limit?: number; cursor?: string | null },
): Promise<ObjectListResponse> {
  const query = new URLSearchParams();
  if (params.q) query.set('q', params.q);
  if (params.type) query.set('type', params.type);
  if (params.limit) query.set('limit', String(params.limit));
  if (params.cursor) query.set('cursor', params.cursor);
  return getJson<ObjectListResponse>(`/api/v1/snapshots/${encodeURIComponent(snapshotId)}/objects?${query}`);
}

export async function getObject(
  snapshotId: string,
  objectId: string,
): Promise<CatalogObjectDetail> {
  return getJson<CatalogObjectDetail>(
    `/api/v1/snapshots/${encodeURIComponent(snapshotId)}/objects/${encodeURIComponent(objectId)}`,
  );
}

export async function getObjectFreshness(
  snapshotId: string,
  objectId: string,
): Promise<RequestFreshnessResponse> {
  return getJson<RequestFreshnessResponse>(
    `/api/v1/snapshots/${encodeURIComponent(snapshotId)}/objects/${encodeURIComponent(objectId)}/freshness`,
  );
}

export async function getObjectFields(
  snapshotId: string,
  objectId: string,
): Promise<ObjectFieldsResponse> {
  return getJson<ObjectFieldsResponse>(
    `/api/v1/snapshots/${encodeURIComponent(snapshotId)}/objects/${encodeURIComponent(objectId)}/fields`,
  );
}

export async function getQueryAnalysis(
  snapshotId: string,
  queryName: string,
): Promise<QueryAnalysisResponse> {
  const params = new URLSearchParams({ query_name: queryName });
  return getJson<QueryAnalysisResponse>(
    `/api/v1/snapshots/${encodeURIComponent(snapshotId)}/query/analyze?${params}`,
  );
}

export async function postLineage(
  snapshotId: string,
  body: {
    object_id: string;
    direction: Direction;
    depth: number;
    node_cap: number;
    edge_cap: number;
  },
): Promise<LineageResponse> {
  return postJson<LineageResponse>(`/api/v1/snapshots/${encodeURIComponent(snapshotId)}/lineage`, body);
}

export async function postLineageAdvice(
  snapshotId: string,
  body: {
    object_id: string;
    direction: Direction;
    depth: number;
    node_cap: number;
    edge_cap: number;
  },
): Promise<LineageAdviceResponse> {
  return postJson<LineageAdviceResponse>(
    `/api/v1/snapshots/${encodeURIComponent(snapshotId)}/lineage/advice`,
    body,
  );
}

export async function postLineageTour(
  snapshotId: string,
  body: {
    object_id: string;
    direction: Direction;
    depth: number;
    node_cap: number;
    edge_cap: number;
    include_korean_summary?: boolean;
  },
): Promise<LineageTourResponse> {
  return postJson<LineageTourResponse>(
    `/api/v1/snapshots/${encodeURIComponent(snapshotId)}/lineage/tour`,
    body,
  );
}

export async function postImpactScenario(
  snapshotId: string,
  body: {
    object_id: string;
    change_type: ChangeType;
    field?: string | null;
    value_description?: string | null;
    description?: string | null;
    depth: number;
    node_cap: number;
    edge_cap: number;
  },
): Promise<ImpactScenarioResponse> {
  return postJson<ImpactScenarioResponse>(
    `/api/v1/snapshots/${encodeURIComponent(snapshotId)}/impact/scenario`,
    body,
  );
}

export async function postImpactAdvice(
  snapshotId: string,
  body: {
    object_id: string;
    change_type: ChangeType;
    field?: string | null;
    value_description?: string | null;
    description?: string | null;
    depth: number;
    node_cap: number;
    edge_cap: number;
  },
): Promise<ImpactAdviceResponse> {
  return postJson<ImpactAdviceResponse>(
    `/api/v1/snapshots/${encodeURIComponent(snapshotId)}/impact/advice`,
    body,
  );
}

export async function postImpactTour(
  snapshotId: string,
  body: {
    object_id: string;
    change_type: ChangeType;
    field?: string | null;
    value_description?: string | null;
    description?: string | null;
    depth: number;
    node_cap: number;
    edge_cap: number;
    include_korean_summary?: boolean;
  },
): Promise<ImpactTourResponse> {
  return postJson<ImpactTourResponse>(
    `/api/v1/snapshots/${encodeURIComponent(snapshotId)}/impact/tour`,
    body,
  );
}

export async function postImpactReview(
  snapshotId: string,
  body: {
    object_id: string;
    change_type: ChangeType;
    field?: string | null;
    value_description?: string | null;
    description?: string | null;
    depth: number;
    node_cap: number;
    edge_cap: number;
    query_names?: string[];
    include_impacted_queries?: boolean;
    include_freshness?: boolean;
    sql_views?: Array<{ view_id: string; sql_file?: string; sql_text?: string }>;
  },
): Promise<ImpactReviewResponse> {
  return postJson<ImpactReviewResponse>(
    `/api/v1/snapshots/${encodeURIComponent(snapshotId)}/impact/review`,
    body,
  );
}

export async function postAgenticReview(
  snapshotId: string,
  body: AgenticReviewRequest,
): Promise<AgenticReviewRun> {
  return postJson<AgenticReviewRun>(
    `/api/v1/snapshots/${encodeURIComponent(snapshotId)}/impact/review/agentic`,
    body,
  );
}

export async function postAssistantReview(
  snapshotId: string,
  body: AssistantReviewRequest,
): Promise<AssistantReviewResponse> {
  return postJson<AssistantReviewResponse>(
    `/api/v1/snapshots/${encodeURIComponent(snapshotId)}/assistant/review`,
    body,
  );
}

export async function explainSql(
  snapshotId: string,
  body: { view_id: string; sql_file?: string; sql_text?: string; format: 'json' | 'md' },
): Promise<SqlExplainResponse> {
  return postJson<SqlExplainResponse>(
    `/api/v1/snapshots/${encodeURIComponent(snapshotId)}/sql/explain`,
    body,
  );
}

export async function draftSql(
  snapshotId: string,
  body: {
    question: string;
    target_dialect: string;
    view_id?: string;
    sql_file?: string;
    sql_text?: string;
  },
): Promise<SqlDraftResponse> {
  return postJson<SqlDraftResponse>(
    `/api/v1/snapshots/${encodeURIComponent(snapshotId)}/sql/draft`,
    body,
  );
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  return parseJsonResponse<T>(response);
}

async function putJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return parseJsonResponse<T>(response);
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return parseJsonResponse<T>(response);
}

async function parseJsonResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail: string | null = null;
    try {
      detail = detailText((await response.json()) as unknown);
    } catch {
      detail = null;
    }
    throw new Error(detail || `${response.status} ${response.statusText}`.trim());
  }
  return (await response.json()) as T;
}

function detailText(payload: unknown): string | null {
  if (typeof payload !== 'object' || payload === null || !('detail' in payload)) return null;
  const detail = (payload as { detail: unknown }).detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const lines = detail.map((item) => {
      if (typeof item === 'object' && item !== null && 'msg' in item) {
        const entry = item as { msg: unknown; loc?: unknown };
        const loc = Array.isArray(entry.loc) ? entry.loc.filter((part) => part !== 'body').join('.') : '';
        return loc ? `${loc}: ${String(entry.msg)}` : String(entry.msg);
      }
      return String(item);
    });
    return lines.join(' · ') || null;
  }
  return JSON.stringify(detail);
}
