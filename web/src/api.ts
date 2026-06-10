export type ConfigSource = 'env' | 'ui' | 'unset';
export type ConnectionStatus = 'unconfigured' | 'untested' | 'ok' | 'failed' | 'stale';
export type Direction = 'upstream' | 'downstream' | 'both';
export type DataflowDirection = 'upwards' | 'downwards' | 'both';
export type AppTab = 'lineage' | 'impact' | 'sql';
export type ChangeType =
  | 'field_removed'
  | 'field_type_changed'
  | 'infoobject_attribute_changed'
  | 'infoobject_type_changed'
  | 'routine_changed'
  | 'dtp_filter_changed'
  | 'compositeprovider_mapping_changed';

export interface HealthResponse {
  status: string;
  local_only: boolean;
  read_only: boolean;
  llm_enabled_by_default: boolean;
  version: string;
}

export interface RuntimeConfigResponse {
  storage: 'process-memory';
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
  object_id: string | null;
  object_type: string | null;
  field_name: string | null;
  evidence_ids: string[];
  metadata: Record<string, unknown>;
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
  items: GlossaryTerm[];
}

export interface CatalogObject {
  id: string;
  name: string | null;
  type: string;
  label: string | null;
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

export async function getCaptureScope(snapshotId: string): Promise<CaptureScopeResponse> {
  return getJson<CaptureScopeResponse>(`/api/v1/snapshots/${encodeURIComponent(snapshotId)}/capture-scope`);
}

export async function getGlossary(snapshotId: string, query?: string): Promise<GlossaryResponse> {
  const params = new URLSearchParams();
  if (query?.trim()) params.set('query', query.trim());
  const suffix = params.toString() ? `?${params}` : '';
  return getJson<GlossaryResponse>(`/api/v1/snapshots/${encodeURIComponent(snapshotId)}/glossary${suffix}`);
}

export async function captureFixtureSnapshot(fixturePath: string): Promise<SnapshotSummary> {
  return postJson<SnapshotSummary>('/api/v1/snapshots/capture', { fixture_path: fixturePath });
}

export async function captureLiveSnapshot(options: {
  confirmReadOnly: boolean;
  objectNames: string[];
  searchTerms?: string[];
  objectType?: string;
  sourceSystem?: string;
  dataflowDirection?: DataflowDirection;
  dataflowLevels?: number;
}): Promise<SnapshotSummary> {
  return postJson<SnapshotSummary>('/api/v1/snapshots/capture', {
    confirm_read_only: options.confirmReadOnly,
    search_terms: options.searchTerms ?? [],
    object_names: options.objectNames,
    include_dataflow: true,
    include_xref: true,
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
