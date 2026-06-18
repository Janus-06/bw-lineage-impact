export type DisplayLayer = 'Source' | 'Transform' | 'Model' | 'Semantic' | 'Runtime' | 'Unknown';
export type FreshnessState = 'fresh' | 'stale' | 'none' | 'unknown';
export type ChangeGrade = 'A' | 'B' | 'C' | 'D' | 'Review' | '—';

export const DISPLAY_LAYER_ORDER: DisplayLayer[] = [
  'Source',
  'Transform',
  'Model',
  'Semantic',
  'Runtime',
  'Unknown',
];

const LAYER_ORDER_INDEX = new Map(DISPLAY_LAYER_ORDER.map((layer, index) => [layer, index]));

const LAYER_ALIASES: Record<string, DisplayLayer> = {
  source: 'Source',
  datasource: 'Source',
  acquisition: 'Source',
  ingestion: 'Source',
  transform: 'Transform',
  transformation: 'Transform',
  transformations: 'Transform',
  staging: 'Model',
  model: 'Model',
  provider: 'Model',
  infoprovider: 'Model',
  semantic: 'Semantic',
  reporting: 'Semantic',
  report: 'Semantic',
  runtime: 'Runtime',
  orchestration: 'Runtime',
  process: 'Runtime',
  unknown: 'Unknown',
};

const TYPE_LAYER_ALIASES: Record<string, DisplayLayer> = {
  rsds: 'Source',
  datasource: 'Source',
  datasourcemetadata: 'Source',
  sourcesystem: 'Source',
  lsys: 'Source',
  infosource: 'Source',
  isource: 'Source',
  dtp: 'Transform',
  dtpa: 'Transform',
  dtpload: 'Transform',
  trcs: 'Transform',
  trfn: 'Transform',
  transformation: 'Transform',
  transformations: 'Transform',
  adso: 'Model',
  dso: 'Model',
  ods: 'Model',
  infoobject: 'Model',
  iobj: 'Model',
  cube: 'Model',
  infocube: 'Model',
  multiprovider: 'Model',
  mpro: 'Model',
  compositeprovider: 'Semantic',
  hcpr: 'Semantic',
  query: 'Semantic',
  alvl: 'Semantic',
  aggrlevel: 'Semantic',
  aggregationlevel: 'Semantic',
  aggr_level: 'Semantic',
  ckf: 'Semantic',
  rkf: 'Semantic',
  queryvariable: 'Semantic',
  query_variable: 'Semantic',
  localmember: 'Semantic',
  local_member: 'Semantic',
  bquery: 'Semantic',
  workbook: 'Semantic',
  nativeview: 'Semantic',
  nativesqlview: 'Semantic',
  processchain: 'Runtime',
  processchainstep: 'Runtime',
  rspc: 'Runtime',
  runtime: 'Runtime',
};

export interface LayerNodeLike {
  id?: string;
  layer?: unknown;
  type?: unknown;
  metadata?: unknown;
}

export interface DisplayLayerInfo {
  layer: DisplayLayer;
  label: DisplayLayer;
  order: number;
  source: 'node.layer' | 'node.type' | 'unknown';
}

export interface LayerGroup<T extends LayerNodeLike> {
  layer: DisplayLayer;
  order: number;
  nodes: T[];
}

export interface FreshnessDisplay {
  state: FreshnessState;
  label: string;
  timestamp?: string;
  status?: string;
  ageHours?: number;
}

export interface NormalizedTourStep {
  id: string;
  index: number;
  total: number;
  title: string;
  description: string;
  nodeIds: string[];
  edgeIds: string[];
  evidenceIds: string[];
  canPrevious: boolean;
  canNext: boolean;
}

export interface ObjectField {
  name: string;
  type?: string;
  role?: string;
  description?: string;
  [key: string]: unknown;
}

export interface UnknownBreakdown {
  metadata_missing: number;
  type_unmapped: number;
  parser_unsupported: number;
  freshness_unavailable: number;
  unknown: number;
}

export interface ImpactSummary {
  grade: ChangeGrade;
  gradeLabel: string;
  headline: string;
  affectedCount: number;
  severityCounts: Record<'HIGH' | 'MEDIUM' | 'LOW' | 'UNKNOWN', number>;
  evidenceCount: number;
  manualVerificationCount: number;
  truncated: boolean;
}

export function inferDisplayLayer(node: LayerNodeLike): DisplayLayerInfo {
  const explicitLayer = displayLayerFromValue(node.layer, LAYER_ALIASES);
  if (explicitLayer) return layerInfo(explicitLayer, 'node.layer');

  const inferredLayer = displayLayerFromValue(node.type, TYPE_LAYER_ALIASES);
  if (inferredLayer) return layerInfo(inferredLayer, 'node.type');

  return layerInfo('Unknown', 'unknown');
}

export function compareDisplayLayers(left: DisplayLayer, right: DisplayLayer): number {
  return (LAYER_ORDER_INDEX.get(left) ?? 999) - (LAYER_ORDER_INDEX.get(right) ?? 999);
}

export function groupNodesByDisplayLayer<T extends LayerNodeLike>(nodes: T[]): LayerGroup<T>[] {
  const groups = new Map<DisplayLayer, T[]>();
  nodes.forEach((node) => {
    const layer = inferDisplayLayer(node).layer;
    groups.set(layer, [...(groups.get(layer) ?? []), node]);
  });

  return Array.from(groups.entries())
    .map(([layer, layerNodes]) => ({
      layer,
      order: LAYER_ORDER_INDEX.get(layer) ?? 999,
      nodes: [...layerNodes],
    }))
    .sort((left, right) => left.order - right.order);
}

export function freshnessFromMetadata(metadata: unknown): unknown {
  if (!isRecord(metadata)) return null;
  return metadata.request_freshness ?? null;
}

export function classifyFreshness(value: unknown, now: Date = new Date()): FreshnessDisplay {
  const freshness = unwrapFreshness(value);
  if (!isRecord(freshness)) return { state: 'unknown', label: 'Unknown' };

  const requests = Array.isArray(freshness.requests) ? freshness.requests : [];
  const latest = latestFreshnessRequest(freshness, requests);
  if (!latest && ('latest' in freshness || 'requests' in freshness)) {
    return { state: 'none', label: 'No requests' };
  }
  if (!latest) return { state: 'unknown', label: 'Unknown' };

  const timestamp = stringValue(latest.timestamp) ?? stringValue(latest.created_at) ?? stringValue(latest.createdAt);
  const status = stringValue(latest.status);
  if (!timestamp) return { state: 'unknown', label: 'Unknown', status };

  const ageMs = now.getTime() - new Date(timestamp).getTime();
  if (!Number.isFinite(ageMs)) return { state: 'unknown', label: 'Unknown', timestamp, status };

  const ageHours = roundTwo(Math.max(0, ageMs / 3_600_000));
  if (ageHours < 2) {
    return { state: 'fresh', label: 'Fresh < 2h', timestamp, status, ageHours };
  }

  return {
    state: 'stale',
    label: `Stale ${formatStaleAge(ageHours)}`,
    timestamp,
    status,
    ageHours,
  };
}

export function normalizeGuidedTourSteps(value: unknown): NormalizedTourStep[] {
  const { tour, citations } = tourPayload(value);
  const total = tour.length;
  return tour.map((item, index) => {
    const record = isRecord(item) ? item : {};
    const title = stringValue(record.title) || `Step ${index + 1}`;
    const description = stringValue(record.description) || stringValue(record.body) || '';
    const evidenceIds = uniqueStrings([
      ...citations,
      ...arrayStrings(record.evidence_ids),
      ...arrayStrings(record.evidenceIds),
      ...extractCitationTokens(title),
      ...extractCitationTokens(description),
    ]);

    return {
      id: stringValue(record.id) || `step-${index + 1}`,
      index: index + 1,
      total,
      title,
      description,
      nodeIds: uniqueStrings([...arrayStrings(record.node_ids), ...arrayStrings(record.nodeIds)]),
      edgeIds: uniqueStrings([...arrayStrings(record.edge_ids), ...arrayStrings(record.edgeIds)]),
      evidenceIds,
      canPrevious: index > 0,
      canNext: index < total - 1,
    };
  });
}

export function clampTourIndex(index: number, steps: NormalizedTourStep[]): number {
  if (steps.length === 0) return 0;
  if (!Number.isFinite(index)) return 0;
  return Math.max(0, Math.min(steps.length - 1, Math.trunc(index)));
}

export function objectFieldsFromMetadata(metadata: unknown): ObjectField[] {
  if (!isRecord(metadata)) return [];
  const direct = Array.isArray(metadata.fields) ? metadata.fields : null;
  const queryAnalysis = isRecord(metadata.query_analysis) ? metadata.query_analysis : null;
  const queryFields = queryAnalysis && Array.isArray(queryAnalysis.fields) ? queryAnalysis.fields : null;
  const sourceFields = direct ?? queryFields ?? [];
  return sourceFields
    .map((item) => fieldFromRecord(item))
    .filter((field): field is ObjectField => Boolean(field));
}

export function firstAutoFieldName(fields: ObjectField[]): string {
  return fields.find((field) => field.name.trim())?.name ?? '';
}

export function nextImpactFieldName(
  current: string,
  fields: ObjectField[],
  options: { objectChanged?: boolean; manualFallback?: string } = {},
): string {
  const fieldNames = fields
    .map((field) => field.name.trim())
    .filter(Boolean);
  if (fieldNames.length === 0) {
    return options.objectChanged ? (options.manualFallback ?? 'AMOUNT') : current;
  }
  const currentName = current.trim();
  if (currentName && currentName !== 'AMOUNT' && fieldNames.includes(currentName)) {
    return current;
  }
  return fieldNames[0];
}

export function unknownBreakdown(nodes: LayerNodeLike[]): UnknownBreakdown {
  const breakdown: UnknownBreakdown = {
    metadata_missing: 0,
    type_unmapped: 0,
    parser_unsupported: 0,
    freshness_unavailable: 0,
    unknown: 0,
  };
  nodes.forEach((node) => {
    const reason = unknownReason(node);
    if (reason === 'METADATA_MISSING') breakdown.metadata_missing += 1;
    else if (reason === 'TYPE_UNMAPPED') breakdown.type_unmapped += 1;
    else if (reason === 'PARSER_UNSUPPORTED') breakdown.parser_unsupported += 1;
    else if (reason === 'FRESHNESS_UNAVAILABLE') breakdown.freshness_unavailable += 1;
    else if (inferDisplayLayer(node).layer === 'Unknown') breakdown.unknown += 1;
  });
  return breakdown;
}

export function deriveImpactSummary(value: unknown): ImpactSummary {
  if (!isRecord(value)) {
    return emptyImpactSummary('—', 'Run impact to grade the selected change.');
  }

  const affected = Array.isArray(value.affected_objects) ? value.affected_objects : [];
  const severityCounts = emptySeverityCounts();
  const evidenceIds = new Set<string>();
  let manualVerificationCount = 0;

  affected.forEach((item) => {
    if (!isRecord(item)) return;
    const severity = normalizeSeverity(item.severity);
    severityCounts[severity] += 1;
    arrayStrings(item.evidence_ids).forEach((id) => evidenceIds.add(id));
    if (item.manual_verification === true) manualVerificationCount += 1;
  });

  const truncated = Boolean(isRecord(value.lineage_bounds) && value.lineage_bounds.truncated === true);
  const affectedCount = affected.length;
  const highestSeverity = highestSeverityLabel(severityCounts);
  const grade = gradeForImpact(severityCounts, truncated, affectedCount);
  const gradeLabel = grade === '—' ? 'Not run' : `Grade ${grade}`;
  const headline = affectedCount > 0
    ? `${affectedCount} affected object${affectedCount === 1 ? '' : 's'} · highest ${highestSeverity}${truncated ? ' · bounded result truncated' : ''}`
    : truncated
      ? 'No affected objects in the bounded result, but traversal was truncated.'
      : 'No downstream impact found in the selected bounds.';

  return {
    grade,
    gradeLabel,
    headline,
    affectedCount,
    severityCounts,
    evidenceCount: evidenceIds.size,
    manualVerificationCount,
    truncated,
  };
}

function fieldFromRecord(value: unknown): ObjectField | null {
  if (!isRecord(value)) return null;
  const name = stringValue(value.name) ?? stringValue(value.technical_name) ?? stringValue(value.fieldName);
  if (!name) return null;
  const field: ObjectField = { name };
  const type = stringValue(value.type);
  const role = stringValue(value.role);
  const description = stringValue(value.description);
  if (type) field.type = type;
  if (role) field.role = role;
  if (description) field.description = description;
  Object.entries(value).forEach(([key, item]) => {
    if (!(key in field)) field[key] = item;
  });
  return field;
}

function unknownReason(node: LayerNodeLike): string | null {
  if (!isRecord(node.metadata)) return null;
  const reason = stringValue(node.metadata.unknown_reason);
  return reason ? reason.toUpperCase() : null;
}

function layerInfo(layer: DisplayLayer, source: DisplayLayerInfo['source']): DisplayLayerInfo {
  return {
    layer,
    label: layer,
    order: LAYER_ORDER_INDEX.get(layer) ?? 999,
    source,
  };
}

function displayLayerFromValue(value: unknown, aliases: Record<string, DisplayLayer>): DisplayLayer | null {
  const normalized = normalizeToken(value);
  if (!normalized) return null;
  return aliases[normalized] ?? null;
}

function normalizeToken(value: unknown): string {
  if (typeof value !== 'string' && typeof value !== 'number') return '';
  return String(value).trim().toLowerCase().replace(/[^a-z0-9]+/g, '');
}

function unwrapFreshness(value: unknown): unknown {
  if (!isRecord(value)) return value;
  if ('request_freshness' in value && !('latest' in value) && !('requests' in value)) {
    return value.request_freshness;
  }
  return value;
}

function latestFreshnessRequest(
  freshness: Record<string, unknown>,
  requests: unknown[],
): Record<string, unknown> | null {
  if (isRecord(freshness.latest)) return freshness.latest;
  if (isRecord(freshness.latest_request)) return freshness.latest_request;
  const firstRequest = requests.find(isRecord);
  if (firstRequest) return firstRequest;
  if ('timestamp' in freshness || 'status' in freshness) return freshness;
  return null;
}

function tourPayload(value: unknown): { tour: unknown[]; citations: string[] } {
  if (Array.isArray(value)) return { tour: value, citations: [] };
  if (!isRecord(value)) return { tour: [], citations: [] };
  return {
    tour: Array.isArray(value.tour) ? value.tour : [],
    citations: arrayStrings(value.citations),
  };
}

function extractCitationTokens(value: string): string[] {
  const result: string[] = [];
  for (const match of value.matchAll(/\[([^\]\s][^\]]*?)\]/g)) {
    const token = match[1]?.trim();
    if (token) result.push(token);
  }
  return result;
}

function arrayStrings(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map(stringValue).filter((item): item is string => Boolean(item));
}

function uniqueStrings(values: string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  values.forEach((value) => {
    const trimmed = value.trim();
    if (!trimmed || seen.has(trimmed)) return;
    seen.add(trimmed);
    result.push(trimmed);
  });
  return result;
}

function stringValue(value: unknown): string | undefined {
  if (typeof value === 'string') return value.trim() || undefined;
  if (typeof value === 'number') return String(value);
  return undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function roundTwo(value: number): number {
  return Math.round(value * 100) / 100;
}

function formatStaleAge(ageHours: number): string {
  if (ageHours >= 24) return `${Math.max(1, Math.round(ageHours / 24))}d`;
  return `${Math.max(2, Math.ceil(ageHours))}h`;
}

function emptySeverityCounts(): Record<'HIGH' | 'MEDIUM' | 'LOW' | 'UNKNOWN', number> {
  return { HIGH: 0, MEDIUM: 0, LOW: 0, UNKNOWN: 0 };
}

function emptyImpactSummary(grade: ChangeGrade, headline: string): ImpactSummary {
  return {
    grade,
    gradeLabel: grade === '—' ? 'Not run' : `Grade ${grade}`,
    headline,
    affectedCount: 0,
    severityCounts: emptySeverityCounts(),
    evidenceCount: 0,
    manualVerificationCount: 0,
    truncated: false,
  };
}

function normalizeSeverity(value: unknown): 'HIGH' | 'MEDIUM' | 'LOW' | 'UNKNOWN' {
  const text = stringValue(value)?.toUpperCase();
  if (text === 'HIGH' || text === 'MEDIUM' || text === 'LOW' || text === 'UNKNOWN') return text;
  return 'UNKNOWN';
}

function highestSeverityLabel(counts: Record<'HIGH' | 'MEDIUM' | 'LOW' | 'UNKNOWN', number>): 'HIGH' | 'MEDIUM' | 'LOW' | 'UNKNOWN' {
  if (counts.HIGH > 0) return 'HIGH';
  if (counts.MEDIUM > 0) return 'MEDIUM';
  if (counts.LOW > 0) return 'LOW';
  return 'UNKNOWN';
}

function gradeForImpact(
  counts: Record<'HIGH' | 'MEDIUM' | 'LOW' | 'UNKNOWN', number>,
  truncated: boolean,
  affectedCount: number,
): ChangeGrade {
  if (counts.HIGH > 0) return 'A';
  if (counts.MEDIUM > 0 || truncated) return 'B';
  if (counts.LOW > 0) return 'C';
  if (counts.UNKNOWN > 0 || affectedCount > 0) return 'Review';
  return 'D';
}
