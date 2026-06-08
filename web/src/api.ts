export type ConfigSource = 'env' | 'ui' | 'unset';
export type Direction = 'upstream' | 'downstream' | 'both';
export type DataflowDirection = 'upwards' | 'downwards' | 'both';
export type XrefDirection = 'upstream' | 'downstream';
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

export interface SnapshotSummary {
  id: string;
  created_at: string;
  mode: string;
  source: string;
  manifest_path: string | null;
  object_count: number;
  edge_count: number;
}

export interface SnapshotListResponse {
  snapshots: SnapshotSummary[];
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
    columns: Array<{ id: string; column_name: string; expression: string }>;
    fragments: Array<{ id: string; kind: string; text: string }>;
  };
  citations: string[];
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
  xrefDirection?: XrefDirection;
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
    xref_direction: options.xrefDirection,
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
  const payload = (await response.json()) as unknown;
  if (!response.ok) {
    const detail =
      typeof payload === 'object' && payload !== null && 'detail' in payload
        ? String((payload as { detail: unknown }).detail)
        : response.statusText;
    throw new Error(detail);
  }
  return payload as T;
}
