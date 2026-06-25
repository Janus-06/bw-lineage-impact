import { useEffect, useMemo, useRef, useState } from 'react';
import {
  captureFixtureSnapshot,
  captureLiveSnapshot,
  clearRuntimeConfig,
  draftSql,
  explainSql,
  getCaptureScope,
  getGlossary,
  getGlossaryAggregate,
  getObject,
  getObjectFields,
  getObjectFreshness,
  getQueryAnalysis,
  getRepository,
  getRuntimeConfig,
  listObjects,
  listSnapshots,
  postAssistantReview,
  postConnectionTest,
  postAgenticReview,
  postImpactAdvice,
  postImpactReview,
  postImpactScenario,
  postImpactTour,
  postLineage,
  postLineageAdvice,
  postLineageTour,
  postGlossaryLifecycle,
  putRuntimeConfig,
  refreshSnapshotFromBw,
  searchBwObjects,
  type AppTab,
  type AssistantEvidenceContext,
  type AssistantReviewResponse,
  type BwSearchItem,
  type CaptureScopeItem,
  type CatalogObject,
  type CatalogObjectDetail,
  type ChangeType,
  type ConnectionStatus,
  type DataflowDirection,
  type Direction,
  type GlossaryAggregateResponse,
  type GlossaryTerm,
  type AgenticReviewRun,
  type ImpactAdviceResponse,
  type ImpactReviewResponse,
  type ImpactScenarioResponse,
  type ImpactTourResponse,
  type LineageAdviceResponse,
  type LineageResponse,
  type LineageTourResponse,
  type QueryAnalysisResponse,
  type LiveCaptureSummary,
  type LiveSmokeResult,
  type RequestFreshnessResponse,
  type RepositoryNode,
  type RuntimeConfigResponse,
  type SnapshotSummary,
  type SqlDraftResponse,
  type SqlExplainResponse,
} from './api';
import {
  DISPLAY_LAYER_ORDER,
  clampTourIndex,
  classifyFreshness,
  compareDisplayLayers,
  deriveImpactSummary,
  freshnessFromMetadata,
  groupNodesByDisplayLayer,
  inferDisplayLayer,
  nextImpactFieldName,
  normalizeGuidedTourSteps,
  objectFieldsFromMetadata,
  type DisplayLayer,
  type FreshnessDisplay,
  type NormalizedTourStep,
  type ObjectField,
} from './sliceG';

const fixtureGraphPath = 'tests/fixtures/sample-graph.json';
const fixtureSqlPath = 'tests/fixtures/native_sql_view.sql';
const GLOSSARY_VISIBLE: boolean = false;
const IMPACT_UNIFIED: boolean = true;
const bwObjectTypes = [
  'ADSO',
  'HCPR',
  'RSDS',
  'DSO',
  'IOBJ',
  'MPRO',
  'CPRO',
  'BCT',
  'TRFN',
  'QUERY',
  'NATIVE_SQL_VIEW',
];
const typeFilters = ['', ...bwObjectTypes];
const changeTypes: ChangeType[] = [
  'field_removed',
  'field_type_changed',
  'infoobject_attribute_changed',
  'infoobject_type_changed',
  'routine_changed',
  'dtp_filter_changed',
  'compositeprovider_mapping_changed',
];
const fieldOrientedChangeTypes: ChangeType[] = [
  'field_removed',
  'field_type_changed',
  'infoobject_attribute_changed',
  'infoobject_type_changed',
];
type ImpactScenarioId = 'field-change' | 'transformation-logic' | 'dtp-chain' | 'provider-query' | 'freshness-risk';
interface ImpactScenarioCard {
  id: ImpactScenarioId;
  title: string;
  description: string;
  changeType: ChangeType;
  changeTypes: ChangeType[];
  fieldOriented: boolean;
  defaultDescription: string;
  hint: string;
}
const impactScenarioCards: ImpactScenarioCard[] = [
  {
    id: 'field-change',
    title: 'ADSO / InfoObject field change',
    description: 'Field removal, type change, or InfoObject attribute/type metadata change.',
    changeType: 'field_removed',
    changeTypes: fieldOrientedChangeTypes,
    fieldOriented: true,
    defaultDescription: 'ADSO / InfoObject field change impact review',
    hint: 'Auto-selects a field from metadata when available; manual entry remains available for sparse snapshots.',
  },
  {
    id: 'transformation-logic',
    title: 'Transformation logic change',
    description: 'Routine, formula, or mapping logic changed in the transformation layer.',
    changeType: 'routine_changed',
    changeTypes: ['routine_changed'],
    fieldOriented: false,
    defaultDescription: 'Transformation logic change impact review',
    hint: 'No field input is required; describe the changed routine or formula if known.',
  },
  {
    id: 'dtp-chain',
    title: 'DTP / Process Chain change',
    description: 'DTP filter, extraction scope, or process-chain orchestration changed.',
    changeType: 'dtp_filter_changed',
    changeTypes: ['dtp_filter_changed'],
    fieldOriented: false,
    defaultDescription: 'DTP / Process Chain change impact review',
    hint: 'Uses existing DTP filter change semantics; verify process-chain scheduling manually.',
  },
  {
    id: 'provider-query',
    title: 'CompositeProvider / Query change',
    description: 'CompositeProvider mapping or semantic query exposure changed.',
    changeType: 'compositeprovider_mapping_changed',
    changeTypes: ['compositeprovider_mapping_changed'],
    fieldOriented: false,
    defaultDescription: 'CompositeProvider / Query change impact review',
    hint: 'Focuses on semantic exposure and query evidence; field input is not forced.',
  },
  {
    id: 'freshness-risk',
    title: 'Recent load / freshness risk',
    description: 'Review recent request freshness or load timing risk without adding a backend enum.',
    changeType: 'dtp_filter_changed',
    changeTypes: ['dtp_filter_changed'],
    fieldOriented: false,
    defaultDescription: 'Recent load / freshness risk review',
    hint: 'Explanatory scenario only: reuses the existing DTP-safe change type and surfaces freshness/manual checks.',
  },
];
const impactScenarioDefaultDescriptions = new Set(impactScenarioCards.map((card) => card.defaultDescription));
const askReviewPresets = [
  {
    label: 'CAB risk review',
    prompt: 'Review the selected BW change for CAB risk. Cite deterministic impact findings and call out manual BWMT checks only.',
  },
  {
    label: 'Query exposure',
    prompt: 'Explain which BW queries may be exposed by this change. Use only query evidence and evidence IDs; do not infer runtime data.',
  },
  {
    label: 'Freshness gaps',
    prompt: 'Summarize stale, missing, or unknown request freshness evidence for the selected object and downstream impact.',
  },
  {
    label: 'Evidence summary',
    prompt: 'Produce an evidence-bound answer for the selected object using lineage, impact, and citation IDs. Separate facts from follow-up review items.',
  },
];

function parseObjectNamesText(value: string): string[] {
  return value
    .split(/[\n,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function joinObjectNames(values: string[]): string {
  return Array.from(new Set(values)).join(', ');
}

function isFieldOrientedChangeType(value: ChangeType): boolean {
  return fieldOrientedChangeTypes.includes(value);
}

function isImpactScenarioDefaultDescription(value: string): boolean {
  const description = value.trim();
  return description === '컬럼/로직 변경 영향 검토' || impactScenarioDefaultDescriptions.has(description);
}

function isBroadLiveSearchTerm(term: string): boolean {
  return term.trim().replace(/[*%]/g, '').trim() === '';
}

function buildAssistantContexts(options: {
  selectedObject: CatalogObject | null;
  lineage: LineageResponse | null;
  impactReview: ImpactReviewResponse | null;
}): AssistantEvidenceContext[] {
  const contexts: AssistantEvidenceContext[] = [];
  const { selectedObject, lineage, impactReview } = options;
  if (selectedObject) {
    contexts.push({
      id: `object:${selectedObject.id}`,
      kind: 'object',
      title: `${selectedObject.type} · ${selectedObject.id}`,
      body: `${selectedObject.label || selectedObject.name || selectedObject.id}. Snapshot metadata only; no raw snapshot payload or data preview is sent.`,
      object_id: selectedObject.id,
      object_type: selectedObject.type,
      citation_id: `node:${selectedObject.id}`,
      source_ids: selectedObject.evidence_ids,
    });
  }
  if (lineage) {
    contexts.push({
      id: `lineage:${lineage.start_id}`,
      kind: 'lineage',
      title: `Lineage context · ${lineage.start_id}`,
      body: `${lineage.direction} lineage contains ${lineage.nodes.length} node(s), ${lineage.edges.length} edge(s), depth ${lineage.depth}, truncated=${String(lineage.truncated)}.`,
      object_id: lineage.start_id,
      citation_id: `node:${lineage.start_id}`,
      source_ids: lineage.evidence_ids.slice(0, 30),
    });
  }
  if (impactReview) {
    impactReview.impact.findings.slice(0, 8).forEach((finding) => {
      contexts.push({
        id: `impact:${finding.id}`,
        kind: 'impact',
        title: `${finding.severity} impact · ${finding.impacted_object_id}`,
        body: `${finding.reason} Confidence=${finding.confidence}. Manual verification=${String(finding.manual_verification)}. Severity is deterministic from impact.py.`,
        object_id: finding.impacted_object_id,
        object_type: finding.impacted_object_type,
        citation_id: `impact:${finding.id}`,
        source_ids: [...finding.evidence_node_ids, ...finding.evidence_edge_ids],
      });
    });
    impactReview.query_evidence.slice(0, 3).forEach((query) => {
      contexts.push({
        id: `query:${query.query_id}`,
        kind: 'impact_review',
        title: `Query evidence · ${query.query_id}`,
        body: `Providers=${query.provider_object_ids.join(', ') || 'none'}; variables=${query.variable_names.join(', ') || 'none'}; filters=${query.filter_count}; manual notes=${query.manual_check_notes.join(' ') || 'none'}.`,
        object_id: query.query_id,
        object_type: 'QUERY',
      });
    });
    impactReview.sql_evidence.slice(0, 3).forEach((sql) => {
      contexts.push({
        id: `sql:${sql.view_id}`,
        kind: 'impact_review',
        title: `SQL reference evidence · ${sql.view_id}`,
        body: `Parse-only SQL references ${sql.referenced_object_ids.join(', ') || 'no objects'} and ${sql.referenced_column_names.slice(0, 12).join(', ') || 'no columns'}; parser=${sql.parser}; confidence=${sql.confidence}.`,
        object_id: sql.view_id,
        object_type: 'NATIVE_SQL_VIEW',
        source_ids: [...sql.reference_edge_ids, ...sql.fragment_ids],
      });
    });
    impactReview.freshness_evidence.slice(0, 4).forEach((freshness) => {
      contexts.push({
        id: `freshness:${freshness.object_id}`,
        kind: 'freshness',
        title: `Freshness evidence · ${freshness.object_id}`,
        body: `Request count=${freshness.request_count}; latest status=${freshness.latest_status ?? 'unknown'}; timestamp=${freshness.latest_timestamp ?? 'unknown'}; evidence_available=${String(freshness.evidence_available)}.`,
        object_id: freshness.object_id,
        object_type: freshness.object_type,
      });
    });
    impactReview.manual_verification_gaps.slice(0, 6).forEach((gap) => {
      contexts.push({
        id: `manual:${gap.id}`,
        kind: 'manual_check',
        title: `Manual verification · ${gap.source}`,
        body: gap.reason,
        object_id: gap.object_id,
        object_type: gap.object_type,
        citation_id: gap.finding_id ? `impact:${gap.finding_id}` : null,
        source_ids: gap.evidence_ids,
      });
    });
  }
  return contexts.slice(0, 12);
}

interface SetupForm {
  url: string;
  user: string;
  password: string;
  client: string;
  language: string;
  verifySsl: boolean;
  caBundle: string;
  trustEnv: boolean;
  llmEnabled: boolean;
  llmBaseUrl: string;
  llmModel: string;
  llmApiKey: string;
}

interface ObjectFreshnessState {
  snapshotId: string;
  objectId: string;
  value: RequestFreshnessResponse | null;
}

interface ObjectDetailState {
  snapshotId: string;
  objectId: string;
  value: CatalogObjectDetail;
}

interface ReloadSnapshotsOptions {
  preserveAnalysisSelection?: boolean;
  isStale?: () => boolean;
}

export default function App() {
  const [runtime, setRuntime] = useState<RuntimeConfigResponse | null>(null);
  const [snapshots, setSnapshots] = useState<SnapshotSummary[]>([]);
  const [selectedSnapshotId, setSelectedSnapshotIdState] = useState('');
  const [objects, setObjects] = useState<CatalogObject[]>([]);
  const [objectsSnapshotId, setObjectsSnapshotId] = useState('');
  const [objectNextCursor, setObjectNextCursor] = useState<string | null>(null);
  const [selectedObjectId, setSelectedObjectIdState] = useState('');
  const [allowHiddenSelection, setAllowHiddenSelection] = useState(false);
  const [objectDetail, setObjectDetail] = useState<ObjectDetailState | null>(null);
  const [objectFreshness, setObjectFreshness] = useState<ObjectFreshnessState | null>(null);
  const [activeTab, setActiveTab] = useState<AppTab>('lineage');
  const [catalogQuery, setCatalogQuery] = useState('');
  const [objectType, setObjectType] = useState('');
  const [direction, setDirection] = useState<Direction>('downstream');
  const [depth, setDepth] = useState(1);
  const [nodeCap, setNodeCap] = useState(25);
  const [edgeCap, setEdgeCap] = useState(60);
  const [changeType, setChangeType] = useState<ChangeType>('field_removed');
  const [impactScenarioId, setImpactScenarioId] = useState<ImpactScenarioId>('field-change');
  const [fieldName, setFieldName] = useState('AMOUNT');
  const [objectFields, setObjectFields] = useState<ObjectField[]>([]);
  const [queryName, setQueryNameState] = useState('');
  const [queryAnalysis, setQueryAnalysis] = useState<QueryAnalysisResponse | null>(null);
  const [scenarioDescription, setScenarioDescription] = useState('컬럼/로직 변경 영향 검토');
  const [impactDepth, setImpactDepth] = useState(3);
  const [sqlViewId, setSqlViewId] = useState('ZSQL_VIEW');
  const [sqlFile, setSqlFile] = useState(fixtureSqlPath);
  const [sqlText, setSqlText] = useState('');
  const [includeSqlEvidence, setIncludeSqlEvidence] = useState(false);
  const [sqlQuestion, setSqlQuestion] = useState('이 뷰의 주요 소스와 집계 로직을 설명하는 조회 초안');
  const [lineage, setLineage] = useState<LineageResponse | null>(null);
  const [lineageAdvice, setLineageAdvice] = useState<LineageAdviceResponse | null>(null);
  const [lineageTour, setLineageTour] = useState<LineageTourResponse | null>(null);
  const [lineageTourStepIndex, setLineageTourStepIndex] = useState(0);
  const [impact, setImpact] = useState<ImpactScenarioResponse | null>(null);
  const [impactReview, setImpactReview] = useState<ImpactReviewResponse | null>(null);
  const [assistantReview, setAssistantReview] = useState<AssistantReviewResponse | null>(null);
  const [assistantPreset, setAssistantPreset] = useState<string | null>(null);
  const [agenticReview, setAgenticReview] = useState<AgenticReviewRun | null>(null);
  const [agenticQuestion, setAgenticQuestion] = useState('');
  const [impactAdvice, setImpactAdvice] = useState<ImpactAdviceResponse | null>(null);
  const [impactTour, setImpactTour] = useState<ImpactTourResponse | null>(null);
  const [impactTourStepIndex, setImpactTourStepIndex] = useState(0);
  const [sqlExplain, setSqlExplain] = useState<SqlExplainResponse | null>(null);
  const [sqlDraft, setSqlDraft] = useState<SqlDraftResponse | null>(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const [topStatusScrolled, setTopStatusScrolled] = useState(false);
  const [setupForm, setSetupForm] = useState<SetupForm>({
    url: '',
    user: '',
    password: '',
    client: '100',
    language: 'EN',
    verifySsl: true,
    caBundle: '',
    trustEnv: true,
    llmEnabled: false,
    llmBaseUrl: 'http://127.0.0.1:11434/v1',
    llmModel: 'local-model',
    llmApiKey: '',
  });
  const [bwSetupTouched, setBwSetupTouched] = useState(false);

  const [liveObjectNames, setLiveObjectNames] = useState('');
  const [liveObjectTargetTypes, setLiveObjectTargetTypes] = useState<Record<string, string>>({});
  const [liveObjectType, setLiveObjectType] = useState<string>('ADSO');
  const [liveSourceSystem, setLiveSourceSystem] = useState('');
  const [liveSearchTerms, setLiveSearchTerms] = useState('');
  const [liveDataflowDirection, setLiveDataflowDirection] = useState<DataflowDirection>('downwards');
  const [liveDataflowLevels, setLiveDataflowLevels] = useState(3);
  const [connectionTest, setConnectionTest] = useState<LiveSmokeResult | null>(null);
  const [connectionTestSearchTerm, setConnectionTestSearchTerm] = useState('Z*');
  const [connectionTestOk, setConnectionTestOk] = useState(false);
  const [repositoryPath, setRepositoryPath] = useState('/');
  const [repositoryNodes, setRepositoryNodes] = useState<RepositoryNode[]>([]);
  const [repositorySource, setRepositorySource] = useState<'live' | 'cache' | 'empty'>('empty');
  const [repositoryActionRequired, setRepositoryActionRequired] = useState<string | null>(null);
  const [captureScope, setCaptureScope] = useState<CaptureScopeItem[]>([]);
  const [glossaryTerms, setGlossaryTerms] = useState<GlossaryTerm[]>([]);
  const [glossaryAggregate, setGlossaryAggregate] = useState<GlossaryAggregateResponse | null>(null);
  const [glossaryQuery, setGlossaryQuery] = useState('');
  const [bwSearchTerm, setBwSearchTerm] = useState('');
  const [bwSearchType, setBwSearchType] = useState('');
  const [bwSearchResults, setBwSearchResults] = useState<BwSearchItem[]>([]);
  const [bwSearchTruncated, setBwSearchTruncated] = useState(false);
  const selectionRef = useRef({ snapshotId: '', objectId: '' });
  const analysisRequestRef = useRef(0);
  const queryAnalysisRequestRef = useRef(0);
  const objectListRequestRef = useRef(0);
  const snapshotContextRequestRef = useRef(0);
  const glossarySearchRequestRef = useRef(0);
  const fieldSelectionRef = useRef({ snapshotId: '', objectId: '' });
  const queryNameRef = useRef('');

  const selectedSnapshot = snapshots.find((snapshot) => snapshot.id === selectedSnapshotId) ?? null;
  const currentObjects = useMemo(
    () => (objectsSnapshotId === selectedSnapshotId ? objects : []),
    [objects, objectsSnapshotId, selectedSnapshotId],
  );
  const currentObjectNextCursor = objectsSnapshotId === selectedSnapshotId ? objectNextCursor : null;
  const selectedObjectFromCurrentObjects = currentObjects.find((item) => item.id === selectedObjectId) ?? null;
  const selectedObjectDetail =
    objectDetail?.snapshotId === selectedSnapshotId
    && objectDetail.objectId === selectedObjectId
    && objectDetail.value.id === selectedObjectId
      ? objectDetail.value
      : null;
  const selectedObject = selectedObjectFromCurrentObjects
    ?? (allowHiddenSelection && selectedObjectDetail ? selectedObjectDetail : null);
  const runtimeMissing = runtime ? !runtime.bw.configured : true;
  const connectionReady = runtime?.connection_status === 'ok' || connectionTestOk;
  const liveObjectNameTokens = useMemo(() => parseObjectNamesText(liveObjectNames), [liveObjectNames]);
  const liveSearchTermTokens = useMemo(() => parseObjectNamesText(liveSearchTerms), [liveSearchTerms]);
  const liveCaptureTargetReady = liveObjectNameTokens.length > 0 || liveSearchTermTokens.length > 0;
  const snapshotPickObjects = useMemo(() => currentObjects.slice(0, 16), [currentObjects]);
  const bwSavedForTesting = Boolean(runtime?.bw.configured && !bwSetupTouched);
  const bwTestedForCapture = Boolean(connectionReady && !bwSetupTouched);
  const refreshableAnalysisBasis = Boolean(
    selectedSnapshot?.mode === 'live-read-only' && captureScope.some((item) => item.role === 'selected'),
  );
  const selectedObjectGlossary = useMemo(
    () => selectedObjectDetail?.glossary_terms ?? glossaryTerms.filter((term) => term.object_id === selectedObjectId).slice(0, 12),
    [glossaryTerms, selectedObjectDetail, selectedObjectId],
  );
  const selectedFreshness = useMemo<RequestFreshnessResponse | null>(() => {
    const selectedObjectFreshness = objectFreshness?.snapshotId === selectedSnapshotId && objectFreshness.objectId === selectedObjectId
      ? objectFreshness.value
      : null;
    const value = selectedObjectFreshness
      ?? freshnessFromMetadata(selectedObjectDetail?.metadata)
      ?? freshnessFromMetadata(selectedObjectFromCurrentObjects?.metadata);
    return value && typeof value === 'object' ? value as RequestFreshnessResponse : null;
  }, [objectFreshness, selectedObjectDetail, selectedObjectFromCurrentObjects, selectedObjectId, selectedSnapshotId]);

  function resetImpactFieldSelection() {
    setObjectFields([]);
    setFieldName('AMOUNT');
  }

  function setSelectedSnapshotId(snapshotId: string) {
    if (snapshotId !== selectedSnapshotId) {
      resetImpactFieldSelection();
      invalidateQueryAnalysisRequests();
      setAgenticReview(null);
      setAssistantReview(null);
    }
    setSelectedSnapshotIdState(snapshotId);
  }

  function setSelectedObjectId(objectId: string) {
    if (objectId !== selectedObjectId) {
      resetImpactFieldSelection();
      invalidateQueryAnalysisRequests();
      setAgenticReview(null);
      setAssistantReview(null);
    }
    setSelectedObjectIdState(objectId);
  }

  function applyQueryName(value: string) {
    queryNameRef.current = value;
    setQueryNameState(value);
  }

  function setQueryNameFromInput(value: string) {
    queryNameRef.current = value;
    invalidateQueryAnalysisRequests();
    setQueryNameState(value);
  }

  function applyImpactFieldsForSelection(snapshotId: string, objectId: string, fields: ObjectField[]) {
    setObjectFields(fields);
    const objectChanged = fieldSelectionRef.current.snapshotId !== snapshotId
      || fieldSelectionRef.current.objectId !== objectId;
    fieldSelectionRef.current = { snapshotId, objectId };
    setFieldName((current) => nextImpactFieldName(current, fields, { objectChanged }));
  }

  useEffect(() => {
    void refreshAll();
  }, []);

  useEffect(() => {
    const onScroll = () => setTopStatusScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  useEffect(() => {
    selectionRef.current = { snapshotId: selectedSnapshotId, objectId: selectedObjectId };
  }, [selectedSnapshotId, selectedObjectId]);

  useEffect(() => {
    queryNameRef.current = queryName;
  }, [queryName]);

  useEffect(() => {
    if (!selectedSnapshotId && snapshots.length > 0) {
      const nextSnapshotId = snapshots[0].id;
      selectionRef.current = { snapshotId: nextSnapshotId, objectId: selectionRef.current.objectId };
      markObjectsStaleForSnapshot(nextSnapshotId);
      markSnapshotContextStale();
      setSelectedSnapshotId(nextSnapshotId);
    }
  }, [selectedSnapshotId, snapshots]);

  useEffect(() => {
    if (selectedSnapshotId) {
      void refreshSnapshotContext(selectedSnapshotId);
    } else {
      markObjectsStaleForSnapshot('');
      setSelectedObjectId('');
      markSnapshotContextStale();
    }
  }, [selectedSnapshotId]);

  useEffect(() => {
    if (!selectedSnapshotId) return;
    const timer = window.setTimeout(() => void refreshObjects(selectedSnapshotId), 200);
    return () => window.clearTimeout(timer);
  }, [selectedSnapshotId, catalogQuery, objectType]);

  useEffect(() => {
    if (currentObjects.length === 0) return;
    if (!selectedObjectId) {
      setSelectedObjectId(currentObjects[0].id);
      setAllowHiddenSelection(false);
    }
    if (selectedObjectId && !currentObjects.some((item) => item.id === selectedObjectId) && !allowHiddenSelection) {
      setSelectedObjectId(currentObjects[0]?.id ?? '');
      setLineage(null);
      setLineageAdvice(null);
      setLineageTour(null);
      setLineageTourStepIndex(0);
      setImpact(null);
      setImpactReview(null);
      setAgenticReview(null);
      setAssistantReview(null);
      setImpactAdvice(null);
      setImpactTour(null);
      setImpactTourStepIndex(0);
      setObjectFreshness(null);
    }
  }, [allowHiddenSelection, currentObjects, selectedObjectId]);

  useEffect(() => {
    let cancelled = false;
    if (selectedSnapshotId && selectedObjectId) {
      setObjectDetail(null);
      setObjectFreshness(null);
      void loadObjectDetail(selectedSnapshotId, selectedObjectId, () => cancelled);
    } else {
      setObjectDetail(null);
      setObjectFreshness(null);
    }
    return () => { cancelled = true; };
  }, [selectedSnapshotId, selectedObjectId]);

  useEffect(() => {
    if (!selectedSnapshotId || !selectedObjectId) {
      setObjectFields([]);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const response = await getObjectFields(selectedSnapshotId, selectedObjectId);
        if (cancelled) return;
        const fields = response.fields.length > 0
          ? response.fields
          : objectFieldsFromMetadata(selectedObjectDetail?.metadata ?? selectedObjectFromCurrentObjects?.metadata);
        applyImpactFieldsForSelection(selectedSnapshotId, selectedObjectId, fields);
      } catch (_err) {
        if (!cancelled) {
          const fields = objectFieldsFromMetadata(selectedObjectDetail?.metadata ?? selectedObjectFromCurrentObjects?.metadata);
          applyImpactFieldsForSelection(selectedSnapshotId, selectedObjectId, fields);
        }
      }
    })();
    return () => { cancelled = true; };
  }, [selectedSnapshotId, selectedObjectId, selectedObjectDetail, selectedObjectFromCurrentObjects]);

  const latestSnapshotLabel = selectedSnapshot
    ? compactDate(selectedSnapshot.created_at)
    : '분석 기준 없음';

  const graphStats = useMemo(() => {
    if (!lineage) return 'Lineage 미실행';
    const capText = lineage.truncated ? `일부 생략 ${lineage.truncation.omitted_neighbor_total}` : '전체 표시';
    return `${lineage.nodes.length} nodes · ${lineage.edges.length} edges · ${capText}`;
  }, [lineage]);

  function nextAnalysisRequestId(): number {
    analysisRequestRef.current += 1;
    return analysisRequestRef.current;
  }

  function invalidateAnalysisRequests() {
    analysisRequestRef.current += 1;
    setBusy((current) => (
      ['lineage', 'lineage-advice', 'lineage-tour', 'impact', 'impact-advice', 'impact-tour', 'impact-agentic', 'live-analyze', 'refresh-bw'].includes(current)
        ? ''
        : current
    ));
  }

  function isCurrentAnalysisRequest(requestId: number, snapshotId: string, objectId: string): boolean {
    return analysisRequestRef.current === requestId
      && selectionRef.current.snapshotId === snapshotId
      && selectionRef.current.objectId === objectId;
  }

  function isCurrentQueryAnalysisRequest(requestId: number, snapshotId: string, queryNameForRequest: string): boolean {
    const currentQueryName = queryNameRef.current.trim() || selectionRef.current.objectId;
    return queryAnalysisRequestRef.current === requestId
      && selectionRef.current.snapshotId === snapshotId
      && currentQueryName === queryNameForRequest;
  }

  function nextQueryAnalysisRequestId(): number {
    queryAnalysisRequestRef.current += 1;
    return queryAnalysisRequestRef.current;
  }

  function invalidateQueryAnalysisRequests() {
    queryAnalysisRequestRef.current += 1;
    setBusy((current) => current === 'query-analysis' ? '' : current);
  }

  function nextObjectListRequestId(): number {
    objectListRequestRef.current += 1;
    return objectListRequestRef.current;
  }

  function isCurrentObjectListRequest(requestId: number, snapshotId: string): boolean {
    return objectListRequestRef.current === requestId
      && selectionRef.current.snapshotId === snapshotId;
  }

  function markObjectsStaleForSnapshot(snapshotId: string) {
    nextObjectListRequestId();
    setObjectsSnapshotId(snapshotId);
    setObjects([]);
    setObjectNextCursor(null);
    setBusy((current) => current === 'catalog' ? '' : current);
  }

  function nextSnapshotContextRequestId(): number {
    snapshotContextRequestRef.current += 1;
    return snapshotContextRequestRef.current;
  }

  function isCurrentSnapshotContextRequest(requestId: number, snapshotId: string): boolean {
    return snapshotContextRequestRef.current === requestId
      && selectionRef.current.snapshotId === snapshotId;
  }

  function nextGlossarySearchRequestId(): number {
    glossarySearchRequestRef.current += 1;
    return glossarySearchRequestRef.current;
  }

  function isCurrentGlossarySearchRequest(requestId: number, snapshotId: string): boolean {
    return glossarySearchRequestRef.current === requestId
      && selectionRef.current.snapshotId === snapshotId;
  }

  function markSnapshotContextStale() {
    snapshotContextRequestRef.current += 1;
    glossarySearchRequestRef.current += 1;
    setCaptureScope([]);
    setGlossaryTerms([]);
    setGlossaryAggregate(null);
    setBusy((current) => current === 'glossary' ? '' : current);
  }

  function clearAnalysisState() {
    invalidateAnalysisRequests();
    invalidateQueryAnalysisRequests();
    setLineage(null);
    setLineageAdvice(null);
    setLineageTour(null);
    setLineageTourStepIndex(0);
    setImpact(null);
    setImpactReview(null);
    setAgenticReview(null);
      setAssistantReview(null);
    setImpactAdvice(null);
    setImpactTour(null);
    setImpactTourStepIndex(0);
    setSqlExplain(null);
    setSqlDraft(null);
    setObjectDetail(null);
    setObjectFreshness(null);
    setObjectFields([]);
    setQueryAnalysis(null);
  }

  function clearRenderedAnalysisStateForRefresh() {
    // Guarded refresh reruns keep the current request token alive until the rerun is started.
    // Clear only rendered snapshot/object-scoped state here; do not touch the shared analysis request token.
    invalidateQueryAnalysisRequests();
    setLineage(null);
    setLineageAdvice(null);
    setLineageTour(null);
    setLineageTourStepIndex(0);
    setImpact(null);
    setImpactReview(null);
    setAgenticReview(null);
      setAssistantReview(null);
    setImpactAdvice(null);
    setImpactTour(null);
    setImpactTourStepIndex(0);
    setObjectDetail(null);
    setObjectFreshness(null);
    setObjectFields([]);
    setQueryAnalysis(null);
    setCaptureScope([]);
    setGlossaryTerms([]);
  }

  function chooseSnapshot(snapshotId: string) {
    selectionRef.current = { snapshotId, objectId: '' };
    markObjectsStaleForSnapshot(snapshotId);
    markSnapshotContextStale();
    setSelectedSnapshotId(snapshotId);
    setSelectedObjectId('');
    setAllowHiddenSelection(false);
    clearAnalysisState();
  }

  function parseLiveObjectNames(): string[] {
    const names = parseObjectNamesText(liveObjectNames);
    return names.length > 0 ? names : parseObjectNamesText(selectedObjectId);
  }

  function pruneLiveObjectTargetTypes(names: string[]) {
    setLiveObjectTargetTypes((current) => Object.fromEntries(
      Object.entries(current).filter(([objectId]) => names.includes(objectId)),
    ));
  }

  function addLiveObjectName(objectId: string, objectType?: string | null) {
    const objectName = objectId.trim();
    if (!objectName) return;
    if (isBroadLiveSearchTerm(objectName)) {
      setError('단독 * / % 는 분석 대상 object name으로 허용하지 않습니다. 정확한 BW object name을 사용하세요.');
      return;
    }
    setLiveObjectNames((current) => joinObjectNames([...parseObjectNamesText(current), objectName]));
    const normalizedType = objectType?.trim();
    if (normalizedType && normalizedType !== 'UNKNOWN') {
      setLiveObjectTargetTypes((current) => ({ ...current, [objectName]: normalizedType }));
    }
  }

  function removeLiveObjectName(objectId: string) {
    setLiveObjectNames((current) => joinObjectNames(parseObjectNamesText(current).filter((item) => item !== objectId)));
    setLiveObjectTargetTypes((current) => {
      const next = { ...current };
      delete next[objectId];
      return next;
    });
  }

  function clearLiveObjectNames() {
    setLiveObjectNames('');
    setLiveObjectTargetTypes({});
  }

  function liveObjectChipLabel(name: string): string {
    const type = liveObjectTargetTypes[name];
    return type ? `${type}: ${name}` : name;
  }

  function liveCaptureObjectTypeFor(objectNames: string[], explicitType?: string): string | undefined {
    const defaultType = explicitType?.trim() || liveObjectType.trim();
    const types = Array.from(new Set(
      objectNames
        .map((name) => {
          if (explicitType?.trim()) return explicitType.trim();
          const trackedType = liveObjectTargetTypes[name]?.trim();
          if (trackedType) return trackedType;
          if (selectedObject?.id === name && selectedObject.type && selectedObject.type !== 'UNKNOWN') {
            return selectedObject.type;
          }
          return defaultType;
        })
        .filter(Boolean),
    ));
    if (types.length > 1) {
      throw new Error('서로 다른 BW object type이 섞여 있습니다. type별로 나눠서 가져오거나 대상 목록을 정리하세요.');
    }
    return types[0] || defaultType || undefined;
  }

  async function refreshAll() {
    setBusy('status');
    try {
      const [runtimeResponse, snapshotResponse, repositoryResponse] = await Promise.all([
        getRuntimeConfig(),
        listSnapshots(),
        getRepository({ path: repositoryPath }),
      ]);
      setRuntime(runtimeResponse);
      setConnectionTestOk(runtimeResponse.connection_status === 'ok');
      if (runtimeResponse.connection_status !== 'ok') {
        setConnectionTest(null);
      }
      setSetupForm((current) => ({
        ...current,
        url: current.url || runtimeResponse.bw.url || '',
        user: current.user || runtimeResponse.bw.user || '',
        client: runtimeResponse.bw.client || current.client,
        language: runtimeResponse.bw.language || current.language,
        verifySsl: runtimeResponse.bw.verify_ssl,
        caBundle: current.caBundle || runtimeResponse.bw.ca_bundle || '',
        trustEnv: runtimeResponse.bw.trust_env,
        llmEnabled: runtimeResponse.llm.enabled,
        llmBaseUrl: runtimeResponse.llm.base_url || current.llmBaseUrl,
        llmModel: runtimeResponse.llm.model || current.llmModel,
      }));
      setSnapshots(snapshotResponse.snapshots);
      setRepositoryPath(repositoryResponse.path);
      setRepositoryNodes(repositoryResponse.items);
      setRepositorySource(repositoryResponse.source);
      setRepositoryActionRequired(repositoryResponse.action_required);
      setError('');
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy('');
    }
  }

  async function refreshObjects(snapshotId: string, cursor?: string | null) {
    const requestId = nextObjectListRequestId();
    setBusy('catalog');
    try {
      const response = await listObjects(snapshotId, {
        q: catalogQuery.trim() || undefined,
        type: objectType || undefined,
        limit: 80,
        cursor,
      });
      if (!isCurrentObjectListRequest(requestId, snapshotId)) return;
      setObjects((current) => (cursor && objectsSnapshotId === snapshotId ? [...current, ...response.items] : response.items));
      setObjectsSnapshotId(snapshotId);
      setObjectNextCursor(response.next_cursor);
      setError('');
    } catch (err) {
      if (isCurrentObjectListRequest(requestId, snapshotId)) {
        setError(errorText(err));
      }
    } finally {
      if (objectListRequestRef.current === requestId) {
        setBusy((current) => current === 'catalog' ? '' : current);
      }
    }
  }

  async function refreshSnapshotContext(snapshotId: string) {
    const contextRequestId = nextSnapshotContextRequestId();
    const glossaryRequestId = nextGlossarySearchRequestId();
    try {
      const [scopeResponse, glossaryResponse, aggregateResponse] = await Promise.all([
        getCaptureScope(snapshotId),
        GLOSSARY_VISIBLE ? getGlossary(snapshotId) : Promise.resolve(null),
        GLOSSARY_VISIBLE ? getGlossaryAggregate() : Promise.resolve(null),
      ]);
      if (isCurrentSnapshotContextRequest(contextRequestId, snapshotId)) {
        setCaptureScope(scopeResponse.items);
      }
      if (GLOSSARY_VISIBLE && glossaryResponse && isCurrentGlossarySearchRequest(glossaryRequestId, snapshotId)) {
        setGlossaryTerms(glossaryResponse.items);
        setGlossaryAggregate(glossaryResponse.counts ?? aggregateResponse);
      }
    } catch (err) {
      const contextStillCurrent = isCurrentSnapshotContextRequest(contextRequestId, snapshotId);
      const glossaryStillCurrent = isCurrentGlossarySearchRequest(glossaryRequestId, snapshotId);
      if (contextStillCurrent) {
        setCaptureScope([]);
      }
      if (GLOSSARY_VISIBLE && glossaryStillCurrent) {
        setGlossaryTerms([]);
      }
      if (contextStillCurrent && (!GLOSSARY_VISIBLE || glossaryStillCurrent)) {
        setError(errorText(err));
      }
    }
  }

  async function loadRepository(path: string, refresh = false) {
    setBusy(refresh ? 'repository-refresh' : 'repository');
    try {
      const response = await getRepository({
        path,
        refresh,
        confirmReadOnly: refresh,
      });
      setRepositoryPath(response.path);
      setRepositoryNodes(response.items);
      setRepositorySource(response.source);
      setRepositoryActionRequired(response.action_required);
      setError('');
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy('');
    }
  }

  function selectRepositoryNode(node: RepositoryNode) {
    addLiveObjectName(node.name, node.object_type);
    if (node.object_type && node.object_type !== 'UNKNOWN') {
      setLiveObjectType(node.object_type);
    }
    if (node.has_children) {
      void loadRepository(node.children_path || node.path);
    }
  }

  async function loadObjectDetail(snapshotId: string, objectId: string, isStale = () => false) {
    try {
      const detail = await getObject(snapshotId, objectId);
      if (isStale()) return;
      setObjectDetail({ snapshotId, objectId, value: detail });
      applyImpactFieldsForSelection(snapshotId, objectId, objectFieldsFromMetadata(detail.metadata));
      const metadataFreshness = freshnessFromMetadata(detail.metadata);
      setObjectFreshness({
        snapshotId,
        objectId,
        value: metadataFreshness && typeof metadataFreshness === 'object' ? metadataFreshness as RequestFreshnessResponse : null,
      });
      try {
        const freshness = await getObjectFreshness(snapshotId, objectId);
        if (!isStale()) {
          setObjectFreshness({ snapshotId, objectId, value: freshness });
        }
      } catch (err) {
        if (!isFreshnessMissingError(err)) {
          // Freshness is supplemental Slice G context; object details remain usable.
          console.warn('freshness lookup failed; continuing without supplemental request evidence');
        }
      }
    } catch (err) {
      if (isStale()) return;
      setObjectDetail(null);
      setObjectFreshness(null);
      setError(errorText(err));
    }
  }

  async function saveSetup() {
    setBusy('setup');
    try {
      const bwConfigRequested =
        bwSetupTouched ||
        Boolean(runtime?.bw.configured) ||
        Boolean(setupForm.url.trim() || setupForm.user.trim() || setupForm.password.trim());
      const llmFieldsProvided =
        setupForm.llmEnabled || Boolean(setupForm.llmBaseUrl.trim() || setupForm.llmModel.trim() || setupForm.llmApiKey.trim());
      const next = await putRuntimeConfig({
        persist_to_env: true,
        bw: bwConfigRequested
          ? {
              url: setupForm.url.trim() || runtime?.bw.url || '',
              user: setupForm.user.trim() || runtime?.bw.user || '',
              password: setupForm.password,
              client: setupForm.client.trim() || runtime?.bw.client || '100',
              language: setupForm.language.trim() || runtime?.bw.language || 'EN',
              verify_ssl: setupForm.verifySsl,
              ca_bundle: setupForm.caBundle.trim() || runtime?.bw.ca_bundle || undefined,
              trust_env: setupForm.trustEnv,
            }
          : undefined,
        llm: llmFieldsProvided
          ? {
              enabled: setupForm.llmEnabled,
              base_url: setupForm.llmBaseUrl.trim() || undefined,
              model: setupForm.llmModel.trim() || undefined,
              api_key: setupForm.llmApiKey.trim() || undefined,
            }
          : undefined,
      });
      setRuntime(next);
      setSetupForm((current) => ({ ...current, password: '', llmApiKey: '' }));
      setBwSetupTouched(false);
      setConnectionTest(null);
      setConnectionTestOk(next.connection_status === 'ok');
      setError('');
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy('');
    }
  }

  async function clearSetup() {
    setBusy('setup');
    try {
      const next = await clearRuntimeConfig();
      setRuntime(next);
      setSetupForm((current) => ({ ...current, password: '', llmApiKey: '' }));
      setBwSetupTouched(false);
      setConnectionTest(null);
      setConnectionTestOk(next.connection_status === 'ok');
      setError('');
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy('');
    }
  }

  async function captureFixture() {
    setBusy('snapshot');
    try {
      const snapshot = await captureFixtureSnapshot(fixtureGraphPath);
      await reloadSnapshots(snapshot.id, snapshot);
      setError('');
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy('');
    }
  }

  async function captureLiveWithTargets(options: {
    objectNames: string[];
    searchTerms?: string[];
    objectType?: string;
    queries?: string[];
  }): Promise<SnapshotSummary> {
    const objectType = liveCaptureObjectTypeFor(options.objectNames, options.objectType);
    const queries = options.queries ?? (objectType === 'QUERY' ? options.objectNames : []);
    const objectNames = objectType === 'QUERY' ? [] : options.objectNames;
    return captureLiveSnapshot({
      confirmReadOnly: true,
      objectNames,
      searchTerms: options.searchTerms && options.searchTerms.length > 0 ? options.searchTerms : undefined,
      queries,
      objectType,
      sourceSystem: liveSourceSystem.trim() || undefined,
      dataflowDirection: liveDataflowDirection,
      dataflowLevels: liveDataflowLevels,
      includeRequestFreshness: true,
      requestFreshnessTop: 3,
    });
  }

  async function captureLive() {
    const objectNames = parseLiveObjectNames();
    const searchTerms = parseObjectNamesText(liveSearchTerms);
    if (objectNames.length === 0 && searchTerms.length === 0) {
      setError('BW에서 가져오려면 분석 대상 object 또는 좁은 search term이 최소 1개 필요합니다.');
      return;
    }
    if (searchTerms.some(isBroadLiveSearchTerm) || objectNames.some(isBroadLiveSearchTerm)) {
      setError('단독 * / % 는 BW에서 가져오기 대상/검색어로 허용하지 않습니다. 좁은 prefix 또는 정확한 object name을 사용하세요.');
      return;
    }
    setBusy('snapshot');
    try {
      const snapshot = await captureLiveWithTargets({ objectNames, searchTerms });
      await reloadSnapshots(snapshot.id, snapshot);
      setError('');
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy('');
    }
  }

  async function findBwObjects() {
    const term = bwSearchTerm.trim();
    if (!connectionReady || bwSetupTouched) {
      setError('먼저 Settings에서 BW 연결 테스트를 성공시켜야 BW에서 찾기를 사용할 수 있습니다.');
      return;
    }
    if (!term) {
      setError('BW에서 찾을 이름/설명/prefix를 입력하세요.');
      return;
    }
    if (isBroadLiveSearchTerm(term)) {
      setError('단독 * / % 검색은 허용하지 않습니다. 예: ZADSO_, SALES, MARGIN처럼 좁혀서 검색하세요.');
      return;
    }
    setBusy('bw-search');
    try {
      const response = await searchBwObjects({
        confirmReadOnly: true,
        searchTerm: term,
        objectType: bwSearchType || undefined,
        limit: 20,
      });
      setBwSearchResults(response.items);
      setBwSearchTruncated(response.truncated);
      setError('');
    } catch (err) {
      setBwSearchResults([]);
      setBwSearchTruncated(false);
      setError(errorText(err));
    } finally {
      setBusy('');
    }
  }

  async function fetchAndAnalyzeObject(item: BwSearchItem) {
    if (!connectionReady || bwSetupTouched) {
      setError('먼저 Settings에서 BW 연결 테스트를 성공시켜야 BW에서 가져오기를 사용할 수 있습니다.');
      return;
    }
    const captureRequestId = nextAnalysisRequestId();
    setActiveTab('lineage');
    setAllowHiddenSelection(true);
    setSelectedObjectId(item.object_id);
    setLineage(null);
    setLineageAdvice(null);
    setLineageTour(null);
    setLineageTourStepIndex(0);
    setImpact(null);
    setImpactReview(null);
    setAgenticReview(null);
      setAssistantReview(null);
    setImpactAdvice(null);
    setImpactTour(null);
    setImpactTourStepIndex(0);
    setObjectDetail(null);
    setObjectFreshness(null);
    setBusy('live-analyze');
    try {
      const snapshot = await captureLiveWithTargets({
        objectNames: [item.object_id],
        objectType: item.object_type,
      });
      if (analysisRequestRef.current !== captureRequestId) return;
      const captureSnapshotId = await reloadSnapshots(snapshot.id, snapshot, {
        preserveAnalysisSelection: true,
        isStale: () => analysisRequestRef.current !== captureRequestId,
      });
      if (!captureSnapshotId || analysisRequestRef.current !== captureRequestId) return;
      const lineageRequestId = nextAnalysisRequestId();
      selectionRef.current = { snapshotId: captureSnapshotId, objectId: item.object_id };
      setActiveTab('lineage');
      setAllowHiddenSelection(true);
      setSelectedObjectId(item.object_id);
      const response = await postLineage(captureSnapshotId, {
        object_id: item.object_id,
        direction,
        depth,
        node_cap: nodeCap,
        edge_cap: edgeCap,
      });
      if (!isCurrentAnalysisRequest(lineageRequestId, captureSnapshotId, item.object_id)) return;
      setLineage(response);
      setLineageAdvice(null);
      setLineageTour(null);
      setLineageTourStepIndex(0);
      setImpact(null);
      setImpactReview(null);
      setAgenticReview(null);
      setAssistantReview(null);
      setImpactAdvice(null);
      setImpactTour(null);
      setImpactTourStepIndex(0);
      setError('');
    } catch (err) {
      if (analysisRequestRef.current === captureRequestId || selectionRef.current.objectId === item.object_id) {
        setError(errorText(err));
      }
    } finally {
      setBusy((current) => current === 'live-analyze' ? '' : current);
    }
  }

  async function refreshAnalysisBasis() {
    if (!selectedSnapshotId) return;
    const snapshotToRefresh = selectedSnapshotId;
    const objectToRerun = selectedObjectId;
    const tabToRerun = activeTab;
    const impactBody = objectToRerun ? { ...impactRequestBody(), object_id: objectToRerun } : null;
    let activeRequestId = nextAnalysisRequestId();
    setBusy('refresh-bw');
    try {
      const snapshot = await refreshSnapshotFromBw(snapshotToRefresh);
      if (analysisRequestRef.current !== activeRequestId) return;
      const refreshedSnapshotId = await reloadSnapshots(snapshot.id, snapshot, {
        preserveAnalysisSelection: true,
        isStale: () => analysisRequestRef.current !== activeRequestId,
      });
      if (!refreshedSnapshotId || analysisRequestRef.current !== activeRequestId) return;
      clearRenderedAnalysisStateForRefresh();
      const rerunRequestId = nextAnalysisRequestId();
      activeRequestId = rerunRequestId;
      if (objectToRerun) {
        selectionRef.current = { snapshotId: refreshedSnapshotId, objectId: objectToRerun };
        setAllowHiddenSelection(true);
        setSelectedObjectId(objectToRerun);
      }
      if (tabToRerun === 'lineage' && objectToRerun) {
        const response = await postLineage(refreshedSnapshotId, {
          object_id: objectToRerun,
          direction,
          depth,
          node_cap: nodeCap,
          edge_cap: edgeCap,
        });
        if (!isCurrentAnalysisRequest(rerunRequestId, refreshedSnapshotId, objectToRerun)) return;
        setLineage(response);
        setLineageAdvice(null);
        setLineageTour(null);
        setLineageTourStepIndex(0);
      } else if (tabToRerun === 'impact' && objectToRerun && impactBody) {
        const response = await postImpactScenario(refreshedSnapshotId, impactBody);
        if (!isCurrentAnalysisRequest(rerunRequestId, refreshedSnapshotId, objectToRerun)) return;
        setImpact(response);
        setImpactAdvice(null);
        setImpactTour(null);
        setImpactTourStepIndex(0);
      } else if (GLOSSARY_VISIBLE && tabToRerun === 'glossary') {
        const response = await getGlossary(refreshedSnapshotId, glossaryQuery.trim() || undefined);
        if (analysisRequestRef.current !== rerunRequestId) return;
        setGlossaryTerms(response.items);
      }
      setError('');
    } catch (err) {
      if (analysisRequestRef.current === activeRequestId) {
        setError(errorText(err));
      }
    } finally {
      setBusy((current) => current === 'refresh-bw' ? '' : current);
    }
  }

  async function searchGlossary() {
    if (!selectedSnapshotId) return;
    const requestSnapshotId = selectedSnapshotId;
    const requestId = nextGlossarySearchRequestId();
    setBusy('glossary');
    try {
      const response = await getGlossary(requestSnapshotId, glossaryQuery.trim() || undefined);
      if (!isCurrentGlossarySearchRequest(requestId, requestSnapshotId)) return;
      setGlossaryTerms(response.items);
      setGlossaryAggregate(response.counts ?? null);
      setError('');
    } catch (err) {
      if (isCurrentGlossarySearchRequest(requestId, requestSnapshotId)) {
        setGlossaryTerms([]);
        setError(errorText(err));
      }
    } finally {
      if (glossarySearchRequestRef.current === requestId) {
        setBusy((current) => current === 'glossary' ? '' : current);
      }
    }
  }

  async function runConnectionTest() {
    if (!runtime?.bw.configured) {
      setError('Test connection 전 BW 설정을 저장하거나 .env로 로드해야 합니다.');
      return;
    }
    if (bwSetupTouched) {
      setError('변경한 BW 설정을 먼저 저장한 뒤 Test connection을 실행하세요.');
      return;
    }
    setBusy('connection-test');
    try {
      const result = await postConnectionTest(connectionTestSearchTerm.trim() || 'Z*');
      const nextRuntime = await getRuntimeConfig();
      setConnectionTest(result);
      setRuntime(nextRuntime);
      setConnectionTestOk(nextRuntime.connection_status === 'ok');
      setError('');
    } catch (err) {
      setConnectionTest(null);
      setConnectionTestOk(false);
      setError(errorText(err));
    } finally {
      setBusy('');
    }
  }

  async function reloadSnapshots(
    preferredId?: string,
    capturedSnapshot?: SnapshotSummary,
    options: ReloadSnapshotsOptions = {},
  ): Promise<string | null> {
    const snapshotResponse = await listSnapshots();
    if (options.isStale?.()) return null;

    const nextSnapshots = mergeSnapshotCapture(snapshotResponse.snapshots, capturedSnapshot);
    const nextSnapshotId = preferredId ?? nextSnapshots[0]?.id ?? '';
    setSnapshots(nextSnapshots);
    if (options.preserveAnalysisSelection) {
      selectionRef.current = { snapshotId: nextSnapshotId, objectId: selectionRef.current.objectId };
      markObjectsStaleForSnapshot(nextSnapshotId);
      markSnapshotContextStale();
      setSelectedSnapshotId(nextSnapshotId);
    } else {
      chooseSnapshot(nextSnapshotId);
    }
    return nextSnapshotId;
  }

  async function runLineage(startId = selectedObjectId) {
    if (!selectedSnapshotId || !startId) return;
    const requestSnapshotId = selectedSnapshotId;
    const requestObjectId = startId;
    const requestId = nextAnalysisRequestId();
    setBusy('lineage');
    try {
      const response = await postLineage(requestSnapshotId, {
        object_id: requestObjectId,
        direction,
        depth,
        node_cap: nodeCap,
        edge_cap: edgeCap,
      });
      if (!isCurrentAnalysisRequest(requestId, requestSnapshotId, requestObjectId)) return;
      setLineage(response);
      setLineageAdvice(null);
      setLineageTour(null);
      setLineageTourStepIndex(0);
      setSelectedObjectId(requestObjectId);
      setError('');
    } catch (err) {
      if (isCurrentAnalysisRequest(requestId, requestSnapshotId, requestObjectId)) {
        setError(errorText(err));
      }
    } finally {
      if (isCurrentAnalysisRequest(requestId, requestSnapshotId, requestObjectId)) {
        setBusy('');
      }
    }
  }

  async function runLineageAdvice() {
    if (!selectedSnapshotId || !selectedObjectId) return;
    const requestSnapshotId = selectedSnapshotId;
    const requestObjectId = selectedObjectId;
    const requestId = nextAnalysisRequestId();
    setBusy('lineage-advice');
    try {
      const response = await postLineageAdvice(requestSnapshotId, {
        object_id: requestObjectId,
        direction,
        depth,
        node_cap: nodeCap,
        edge_cap: edgeCap,
      });
      if (!isCurrentAnalysisRequest(requestId, requestSnapshotId, requestObjectId)) return;
      setLineage(response.lineage);
      setLineageAdvice(response);
      setLineageTour(null);
      setLineageTourStepIndex(0);
      setError('');
    } catch (err) {
      if (isCurrentAnalysisRequest(requestId, requestSnapshotId, requestObjectId)) {
        setError(errorText(err));
      }
    } finally {
      if (isCurrentAnalysisRequest(requestId, requestSnapshotId, requestObjectId)) {
        setBusy('');
      }
    }
  }

  async function runLineageTour() {
    if (!selectedSnapshotId || !selectedObjectId) return;
    const requestSnapshotId = selectedSnapshotId;
    const requestObjectId = selectedObjectId;
    const requestId = nextAnalysisRequestId();
    setBusy('lineage-tour');
    try {
      const response = await postLineageTour(requestSnapshotId, {
        object_id: requestObjectId,
        direction,
        depth,
        node_cap: nodeCap,
        edge_cap: edgeCap,
        include_korean_summary: true,
      });
      if (!isCurrentAnalysisRequest(requestId, requestSnapshotId, requestObjectId)) return;
      setLineage(response.lineage);
      setLineageTour(response);
      setLineageTourStepIndex(0);
      setError('');
    } catch (err) {
      if (isCurrentAnalysisRequest(requestId, requestSnapshotId, requestObjectId)) {
        setError(errorText(err));
      }
    } finally {
      if (isCurrentAnalysisRequest(requestId, requestSnapshotId, requestObjectId)) {
        setBusy('');
      }
    }
  }

  function impactRequestBody() {
    const field = isFieldOrientedChangeType(changeType) ? fieldName.trim() || null : null;
    return {
      object_id: selectedObjectId,
      change_type: changeType,
      field,
      description: scenarioDescription.trim() || null,
      depth: Math.max(impactDepth, 1),
      node_cap: nodeCap,
      edge_cap: edgeCap,
    };
  }

  function impactReviewRequestBody(objectId: string) {
    const sqlViews = includeSqlEvidence && sqlViewId.trim() && (sqlText.trim() || sqlFile.trim())
      ? [
          {
            view_id: sqlViewId.trim(),
            ...(sqlText.trim() ? { sql_text: sqlText } : { sql_file: sqlFile.trim() }),
          },
        ]
      : [];
    return {
      ...impactRequestBody(),
      object_id: objectId,
      query_names: parseObjectNamesText(queryName),
      include_impacted_queries: true,
      include_freshness: true,
      sql_views: sqlViews,
    };
  }

  async function runImpact() {
    if (!selectedSnapshotId || !selectedObjectId) return;
    const requestSnapshotId = selectedSnapshotId;
    const requestObjectId = selectedObjectId;
    const requestId = nextAnalysisRequestId();
    setBusy('impact');
    setAgenticReview(null);
      setAssistantReview(null);
    try {
      const scenarioBody = { ...impactRequestBody(), object_id: requestObjectId };
      const [scenarioResponse, reviewResponse] = await Promise.all([
        postImpactScenario(requestSnapshotId, scenarioBody),
        postImpactReview(requestSnapshotId, impactReviewRequestBody(requestObjectId)),
      ]);
      if (!isCurrentAnalysisRequest(requestId, requestSnapshotId, requestObjectId)) return;
      setImpact(scenarioResponse);
      setImpactReview(reviewResponse);
      setImpactAdvice(null);
      setImpactTour(null);
      setImpactTourStepIndex(0);
      setError('');
    } catch (err) {
      if (isCurrentAnalysisRequest(requestId, requestSnapshotId, requestObjectId)) {
        setError(errorText(err));
      }
    } finally {
      if (isCurrentAnalysisRequest(requestId, requestSnapshotId, requestObjectId)) {
        setBusy('');
      }
    }
  }

  async function runAgenticReview() {
    if (!selectedSnapshotId || !selectedObjectId) return;
    const requestSnapshotId = selectedSnapshotId;
    const requestObjectId = selectedObjectId;
    const requestId = nextAnalysisRequestId();
    setBusy('impact-agentic');
    setAgenticReview(null);
    setAssistantReview(null);
    try {
      const deterministicPack = impactReview
        ?? await postImpactReview(requestSnapshotId, impactReviewRequestBody(requestObjectId));
      if (!isCurrentAnalysisRequest(requestId, requestSnapshotId, requestObjectId)) return;
      const response = await postAssistantReview(requestSnapshotId, {
        prompt: agenticQuestion.trim() || 'Review selected BW lineage and impact evidence.',
        object_id: requestObjectId,
        preset: assistantPreset,
        context: buildAssistantContexts({
          selectedObject,
          lineage,
          impactReview: deterministicPack,
        }),
        max_context_items: 12,
      });
      if (!isCurrentAnalysisRequest(requestId, requestSnapshotId, requestObjectId)) return;
      setAssistantReview(response);
      setImpactReview(deterministicPack);
      setError('');
    } catch (err) {
      if (isCurrentAnalysisRequest(requestId, requestSnapshotId, requestObjectId)) {
        setError(errorText(err));
      }
    } finally {
      if (isCurrentAnalysisRequest(requestId, requestSnapshotId, requestObjectId)) {
        setBusy('');
      }
    }
  }

  async function runImpactAdvice() {
    if (!selectedSnapshotId || !selectedObjectId) return;
    const requestSnapshotId = selectedSnapshotId;
    const requestObjectId = selectedObjectId;
    const requestId = nextAnalysisRequestId();
    setBusy('impact-advice');
    try {
      const response = await postImpactAdvice(requestSnapshotId, { ...impactRequestBody(), object_id: requestObjectId });
      if (!isCurrentAnalysisRequest(requestId, requestSnapshotId, requestObjectId)) return;
      setImpact(response.impact);
      setImpactAdvice(response);
      setImpactTour(null);
      setImpactTourStepIndex(0);
      setError('');
    } catch (err) {
      if (isCurrentAnalysisRequest(requestId, requestSnapshotId, requestObjectId)) {
        setError(errorText(err));
      }
    } finally {
      if (isCurrentAnalysisRequest(requestId, requestSnapshotId, requestObjectId)) {
        setBusy('');
      }
    }
  }

  async function runImpactTour() {
    if (!selectedSnapshotId || !selectedObjectId) return;
    const requestSnapshotId = selectedSnapshotId;
    const requestObjectId = selectedObjectId;
    const requestId = nextAnalysisRequestId();
    setBusy('impact-tour');
    try {
      const response = await postImpactTour(requestSnapshotId, {
        ...impactRequestBody(),
        object_id: requestObjectId,
        include_korean_summary: true,
      });
      if (!isCurrentAnalysisRequest(requestId, requestSnapshotId, requestObjectId)) return;
      setImpact(response.impact);
      setImpactTour(response);
      setImpactTourStepIndex(0);
      setError('');
    } catch (err) {
      if (isCurrentAnalysisRequest(requestId, requestSnapshotId, requestObjectId)) {
        setError(errorText(err));
      }
    } finally {
      if (isCurrentAnalysisRequest(requestId, requestSnapshotId, requestObjectId)) {
        setBusy('');
      }
    }
  }

  async function runQueryAnalysis() {
    if (!selectedSnapshotId) return;
    const name = queryName.trim() || selectedObjectId;
    if (!name) {
      setError('Query name 또는 selected query object가 필요합니다.');
      return;
    }
    const requestSnapshotId = selectedSnapshotId;
    const requestQueryName = name;
    const requestId = nextQueryAnalysisRequestId();
    setBusy('query-analysis');
    try {
      const response = await getQueryAnalysis(requestSnapshotId, requestQueryName);
      if (!isCurrentQueryAnalysisRequest(requestId, requestSnapshotId, requestQueryName)) return;
      setQueryAnalysis(response);
      applyQueryName(response.query_name);
      setError('');
    } catch (err) {
      if (isCurrentQueryAnalysisRequest(requestId, requestSnapshotId, requestQueryName)) {
        setQueryAnalysis(null);
        setError(errorText(err));
      }
    } finally {
      if (isCurrentQueryAnalysisRequest(requestId, requestSnapshotId, requestQueryName)) {
        setBusy((current) => current === 'query-analysis' ? '' : current);
      }
    }
  }

  async function confirmGlossaryTerm(termId: string, lifecycle: 'candidate' | 'confirmed' | 'rejected') {
    setBusy('glossary');
    try {
      const updated = await postGlossaryLifecycle(termId, lifecycle);
      setGlossaryTerms((current) => current.map((term) => term.id === termId ? { ...term, ...updated } : term));
      setGlossaryAggregate(await getGlossaryAggregate(glossaryQuery.trim() || undefined));
      setError('');
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy('');
    }
  }

  async function runSqlExplain() {
    if (!selectedSnapshotId) return;
    setBusy('sql-explain');
    try {
      setSqlExplain(
        await explainSql(selectedSnapshotId, {
          view_id: sqlViewId,
          ...(sqlText.trim() ? { sql_text: sqlText } : { sql_file: sqlFile }),
          format: 'json',
        }),
      );
      setError('');
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy('');
    }
  }

  async function runSqlDraft() {
    if (!selectedSnapshotId) return;
    setBusy('sql-draft');
    try {
      setSqlDraft(
        await draftSql(selectedSnapshotId, {
          question: sqlQuestion,
          target_dialect: 'sap-hana-sql',
          view_id: sqlViewId,
          ...(sqlText.trim() ? { sql_text: sqlText } : { sql_file: sqlFile }),
        }),
      );
      setError('');
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy('');
    }
  }

  return (
    <div className="appShell">
      <header className={`topStatus ${topStatusScrolled ? 'scrolled' : ''}`}>
        <div className="brandBlock">
          <div>
            <strong>BW Lineage / Impact / Ask BW Review</strong>
            <span>Enterprise metadata workbench · local-first · evidence-bound LLM</span>
          </div>
        </div>
        <div className="statusStrip">
          <StatusPill label="BW" value={bwStatus(runtime)} tone={runtime?.connection_status === 'ok' ? 'ok' : runtime?.bw.configured ? 'warn' : 'warn'} />
          <StatusPill label="Basis" value={latestSnapshotLabel} tone={selectedSnapshot ? 'info' : 'warn'} />
          <StatusPill label="LLM" value={runtime?.llm.configured ? 'local 설정됨' : 'disabled'} tone="neutral" />
          <StatusPill label="Safety" value="read-only metadata · no rows" tone="ok" />
        </div>
        <button className="ghostButton" onClick={() => setDiagnosticsOpen((value) => !value)}>
          Settings
        </button>
      </header>

      {error ? (
        <div className="errorBar" role="alert">
          <span>{error}</span>
          <button className="errorDismiss" onClick={() => setError('')} aria-label="오류 메시지 닫기">×</button>
        </div>
      ) : null}

      {runtimeMissing && !diagnosticsOpen ? (
        <div className="setupPrompt">
          BW 연결 설정이 없어 BW에서 찾기/가져오기가 비활성입니다. <button onClick={() => setDiagnosticsOpen(true)}>Settings 열기</button>
        </div>
      ) : null}

      {diagnosticsOpen ? (
        <div className="settingsOverlay" onClick={() => setDiagnosticsOpen(false)}>
          <aside className="settingsDrawer" role="dialog" aria-modal="true" aria-label="Settings" onClick={(event) => event.stopPropagation()}>
            <div className="drawerHeader">
              <div>
                <span className="eyebrow">Settings</span>
                <h2>실행 설정</h2>
                <p>설정 저장 시 프로젝트 로컬 .env 파일에 저장됩니다 (Git 제외). Secrets는 UI/API에 다시 표시되지 않습니다. BW에서 찾기/가져오기는 GET-only metadata만 사용합니다.</p>
              </div>
              <button className="iconButton" onClick={() => setDiagnosticsOpen(false)} aria-label="Settings 닫기">×</button>
            </div>

            <div className="setupStepper" aria-label="BW setup steps">
              <SetupStep
                index={1}
                title="BW 연결 정보 저장"
                status={bwSavedForTesting ? '완료' : bwSetupTouched ? '저장 필요' : '대기'}
                done={bwSavedForTesting}
              />
              <SetupStep
                index={2}
                title="연결 테스트 실행"
                status={bwTestedForCapture ? '완료' : bwSavedForTesting ? '실행 필요' : '저장 후'}
                done={bwTestedForCapture}
              />
              <SetupStep
                index={3}
                title="BW에서 찾고 분석 기준 가져오기"
                status={bwTestedForCapture && liveCaptureTargetReady ? '준비됨' : '대기'}
                done={bwTestedForCapture && liveCaptureTargetReady}
              />
            </div>

            <section className="drawerSection primarySetupSection">
              <h3>1. BW 연결 정보 저장</h3>
              <p>{runtime?.bw.source === 'env' ? '.env/environment에서 로드됨' : '필수값을 저장하세요.'}</p>
              <div className="setupGrid">
                <label className="fieldLabel requiredField">
                  BW_URL <span aria-hidden="true">*</span>
                  <input
                    aria-label="required BW_URL"
                    placeholder="https://bw.example.invalid"
                    required
                    value={setupForm.url}
                    onChange={(event) => { setBwSetupTouched(true); setSetupForm({ ...setupForm, url: event.target.value }); }}
                  />
                </label>
                <label className="fieldLabel requiredField">
                  BW_USER <span aria-hidden="true">*</span>
                  <input
                    aria-label="required BW_USER"
                    placeholder="사용자 ID"
                    required
                    value={setupForm.user}
                    onChange={(event) => { setBwSetupTouched(true); setSetupForm({ ...setupForm, user: event.target.value }); }}
                  />
                </label>
                <label className="fieldLabel requiredField">
                  BW_PASSWORD <span aria-hidden="true">*</span>
                  <input
                    aria-label="required BW_PASSWORD"
                    placeholder={runtime?.bw.configured ? '저장됨 — 변경 시에만 입력' : '로컬 .env에 저장됩니다'}
                    type="password"
                    required={!runtime?.bw.configured}
                    value={setupForm.password}
                    onChange={(event) => { setBwSetupTouched(true); setSetupForm({ ...setupForm, password: event.target.value }); }}
                  />
                </label>
                <label className="fieldLabel requiredField">
                  BW_CLIENT <span aria-hidden="true">*</span>
                  <input
                    aria-label="required BW_CLIENT"
                    placeholder="100"
                    required
                    value={setupForm.client}
                    onChange={(event) => { setBwSetupTouched(true); setSetupForm({ ...setupForm, client: event.target.value }); }}
                  />
                </label>
                <label className="fieldLabel">
                  BW_LANGUAGE
                  <input
                    placeholder="EN"
                    value={setupForm.language}
                    onChange={(event) => { setBwSetupTouched(true); setSetupForm({ ...setupForm, language: event.target.value }); }}
                  />
                </label>
                <label className="fieldLabel">
                  BW_CA_BUNDLE (선택)
                  <input
                    placeholder="/path/to/ca.pem"
                    value={setupForm.caBundle}
                    onChange={(event) => { setBwSetupTouched(true); setSetupForm({ ...setupForm, caBundle: event.target.value }); }}
                  />
                </label>
                <label className="checkField">
                  <input
                    type="checkbox"
                    checked={setupForm.verifySsl}
                    onChange={(event) => { setBwSetupTouched(true); setSetupForm({ ...setupForm, verifySsl: event.target.checked }); }}
                  />
                  TLS/SSL 검증
                </label>
                <label className="checkField">
                  <input
                    type="checkbox"
                    checked={setupForm.trustEnv}
                    onChange={(event) => { setBwSetupTouched(true); setSetupForm({ ...setupForm, trustEnv: event.target.checked }); }}
                  />
                  Proxy env 신뢰
                </label>
                <p className="setupHint fullSpan">설정은 프로젝트 .env에 저장되어 재시작 후에도 유지됩니다. 저장 후 연결 테스트를 실행하세요. Secrets는 표시하지 않습니다.</p>
                <button className="primaryButton" onClick={saveSetup} disabled={busy === 'setup'}>설정 저장</button>
                <button className="secondaryButton" onClick={clearSetup} disabled={busy === 'setup'}>초기화 / env fallback</button>
              </div>
            </section>

            <section className="drawerSection">
              <h3>2. 연결 테스트 실행</h3>
              <p>저장된 BW 설정으로 인증/TLS/프록시를 확인합니다.</p>
              {!bwSavedForTesting || bwSetupTouched ? (
                <p className="dependencyHint">먼저 1단계 BW 설정을 저장해야 연결 테스트를 실행할 수 있습니다.</p>
              ) : null}
              <div className="liveOptionsGrid">
                <label>
                  검색 패턴 (search_term)
                  <input
                    placeholder="예: Z* 또는 ZADSO_"
                    value={connectionTestSearchTerm}
                    onChange={(event) => setConnectionTestSearchTerm(event.target.value)}
                  />
                </label>
              </div>
              <div className="captureRow">
                <button
                  className="secondaryButton"
                  onClick={runConnectionTest}
                  disabled={!runtime?.bw.configured || bwSetupTouched || busy === 'connection-test'}
                >
                  연결 테스트 실행
                </button>
                <span className={`connectionBadge ${connectionTestBadge(connectionTest, runtime?.connection_status)}`}>
                  {connectionTestLabel(connectionTest, runtime)}
                </span>
              </div>
              {connectionTest ? (
                <ul className="connectionOps" aria-label="connection test operations">
                  {connectionTest.operations.map((op) => (
                    <li key={op.label} className={op.ok ? 'opOk' : 'opError'}>
                      <strong>{op.name}</strong>
                      <small>{op.label}</small>
                      {op.ok ? (
                        <span>{op.payload_kind ?? 'ok'}{op.item_count != null ? ` · ${op.item_count}` : ''}</span>
                      ) : (
                        <code>{op.error ?? 'error'}</code>
                      )}
                    </li>
                  ))}
                </ul>
              ) : null}
            </section>

            <section className="drawerSection">
              <h3>3. 분석 대상 가져오기</h3>
              <p>테스트 성공 후 선택한 object의 관련 메타데이터만 BW에서 가져옵니다. 사용자는 스냅샷을 먼저 알 필요 없이 찾고 분석하면 됩니다.</p>
              {!bwTestedForCapture ? (
                <p className="dependencyHint">먼저 2단계 연결 테스트가 성공해야 BW에서 가져오기가 활성화됩니다.</p>
              ) : null}
              <label className="fieldLabel">
                분석 대상 object names
                <textarea
                  className="liveObjectInput"
                  placeholder="ZADSO_SALES, ZTRFN_MARGIN — dataflow/xref edge 수집 대상"
                  value={liveObjectNames}
                  onChange={(event) => {
                    const next = event.target.value;
                    setLiveObjectNames(next);
                    pruneLiveObjectTargetTypes(parseObjectNamesText(next));
                  }}
                />
              </label>
              <div className="liveObjectTools">
                <button className="secondaryButton" onClick={() => addLiveObjectName(selectedObjectId, selectedObject?.type)} disabled={!selectedObjectId}>
                  선택 객체 추가
                </button>
                <button className="secondaryButton" onClick={clearLiveObjectNames} disabled={liveObjectNameTokens.length === 0}>
                  선택 비우기
                </button>
              </div>
              {liveObjectNameTokens.length > 0 ? (
                <div className="selectedLiveObjects" aria-label="selected live capture objects">
                  {liveObjectNameTokens.map((name) => (
                    <button key={name} className="selectedObjectChip" onClick={() => removeLiveObjectName(name)} title="Live capture 대상에서 제거">
                      {liveObjectChipLabel(name)} ×
                    </button>
                  ))}
                </div>
              ) : (
                <p className="livePickerHint">객체를 선택하거나 직접 입력하세요.</p>
              )}
              {snapshotPickObjects.length > 0 ? (
                <div className="snapshotPickList" aria-label="snapshot object quick picker">
                  {snapshotPickObjects.map((item) => (
                    <button key={item.id} className="snapshotPickButton" onClick={() => addLiveObjectName(item.id, item.type)}>
                      <span>{item.type}</span>{item.id}
                    </button>
                  ))}
                </div>
              ) : null}
              <label className="fieldLabel">
                Search terms (선택)
                <input
                  className="liveSearchInput"
                  placeholder="예: ZADSO_, ZTRFN_ — 좁은 prefix만 권장 (broad * 자동 실행 금지)"
                  value={liveSearchTerms}
                  onChange={(event) => setLiveSearchTerms(event.target.value)}
                />
                <small className="livePickerHint">쉼표/줄바꿈으로 구분합니다.</small>
              </label>
              <div className="liveOptionsGrid">
                <label>
                  Object type
                  <select
                    value={liveObjectType}
                    onChange={(event) => setLiveObjectType(event.target.value)}
                  >
                    {bwObjectTypes.map((type) => (
                      <option key={type} value={type}>{type}</option>
                    ))}
                  </select>
                </label>
                <label>
                  Source system
                  <input placeholder="optional for RSDS" value={liveSourceSystem} onChange={(event) => setLiveSourceSystem(event.target.value)} />
                </label>
                <label>
                  Dataflow
                  <select
                    value={liveDataflowDirection}
                    onChange={(event) => setLiveDataflowDirection(event.target.value as DataflowDirection)}
                  >
                    <option value="downwards">Downwards</option>
                    <option value="upwards">Upwards</option>
                    <option value="both">Both</option>
                  </select>
                </label>

                <label>
                  Levels
                  <input
                    type="number"
                    min={0}
                    max={10}
                    value={liveDataflowLevels}
                    onChange={(event) => setLiveDataflowLevels(Number(event.target.value) || 0)}
                  />
                </label>
              </div>
              <p className="policyNote fullSpan">BW에서 가져오기는 서버에서 GET-only metadata 요청으로 고정됩니다.</p>
              <div className="captureRow">
                <button className="secondaryButton" onClick={captureFixture} disabled={busy === 'snapshot'}>
                  Fixture capture
                </button>
                <button
                  className="primaryButton"
                  onClick={captureLive}
                  disabled={
                    !runtime?.bw.configured
                    || bwSetupTouched
                    || !connectionReady
                    || !liveCaptureTargetReady
                    || busy === 'snapshot'
                  }
                  title={liveCaptureButtonTitle(connectionReady, liveCaptureTargetReady)}
                >
                  BW에서 가져오기
                </button>
              </div>
              {!connectionReady || bwSetupTouched || !liveCaptureTargetReady ? (
                <p className="livePickerHint">
                  {bwSetupTouched
                    ? '변경한 BW 설정을 저장하고 Test connection을 다시 실행해야 BW에서 가져오기가 활성화됩니다.'
                    : !connectionReady
                      ? 'BW에서 가져오기는 현재 세션에서 연결 테스트가 성공한 뒤에만 활성화됩니다.'
                      : 'BW에서 가져오려면 분석 대상 object 또는 좁은 search term이 최소 1개 필요합니다.'}
                </p>
              ) : null}
              <CaptureOutcomeCard snapshot={selectedSnapshot} />
            </section>

            <details className="drawerSection advancedSection">
              <summary>Advanced · LLM / diagnostics</summary>
              <div className="setupGrid advancedGrid">
                <label className="checkField fullSpan">
                  <input
                    type="checkbox"
                    checked={setupForm.llmEnabled}
                    onChange={(event) => setSetupForm({ ...setupForm, llmEnabled: event.target.checked })}
                  />
                  로컬 OpenAI-compatible LLM advisory 활성화
                </label>
                <label className="fieldLabel">
                  BWLI_LLM_BASE_URL
                  <input
                    placeholder="http://127.0.0.1:11434/v1"
                    value={setupForm.llmBaseUrl}
                    onChange={(event) => setSetupForm({ ...setupForm, llmBaseUrl: event.target.value })}
                  />
                </label>
                <label className="fieldLabel">
                  BWLI_LLM_MODEL
                  <input value={setupForm.llmModel} onChange={(event) => setSetupForm({ ...setupForm, llmModel: event.target.value })} />
                </label>
                <label className="fieldLabel fullSpan">
                  BWLI_LLM_API_KEY
                  <input
                    placeholder={runtime?.llm.configured ? '설정됨 — 변경 시에만 입력' : '선택'}
                    type="password"
                    value={setupForm.llmApiKey}
                    onChange={(event) => setSetupForm({ ...setupForm, llmApiKey: event.target.value })}
                  />
                </label>
                <p className="setupHint fullSpan">LLM: {llmStatus(runtime)} · advisory only</p>
                <button className="secondaryButton" onClick={saveSetup} disabled={busy === 'setup'}>고급 설정 저장</button>
              </div>
            </details>
          </aside>
        </div>
      ) : null}

      <main className="appFrame">
        <aside className="catalogPane">
          <div className="paneHeader">
            <div>
              <span className="eyebrow">Find and analyze</span>
              <h2>BW Objects</h2>
            </div>
            <button className="iconButton" onClick={() => void refreshAll()} disabled={busy === 'status'}>↻</button>
          </div>

          <RepositoryPicker
            path={repositoryPath}
            nodes={repositoryNodes}
            source={repositorySource}
            actionRequired={repositoryActionRequired}
            connectionReady={connectionReady && !bwSetupTouched}
            busy={busy}
            onOpenPath={(path) => void loadRepository(path)}
            onRefresh={() => void loadRepository(repositoryPath, true)}
            onSelect={selectRepositoryNode}
          />

          <section className="catalogActionCard bwSearchCard" aria-label="Find in BW">
            <div className="basketHeader">
              <strong>Find in BW</strong>
              <span>{bwSearchResults.length}{bwSearchTruncated ? '+' : ''} results</span>
            </div>
            <input
              className="catalogSearch"
              placeholder="이름/설명/prefix 검색: ZADSO_, SALES, MARGIN"
              value={bwSearchTerm}
              onChange={(event) => setBwSearchTerm(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') void findBwObjects();
              }}
            />
            <select value={bwSearchType} onChange={(event) => setBwSearchType(event.target.value)}>
              {typeFilters.map((filter) => (
                <option key={filter || 'all'} value={filter}>{filter || '전체 타입'}</option>
              ))}
            </select>
            <button
              className="primaryButton wide"
              onClick={() => void findBwObjects()}
              disabled={!connectionReady || bwSetupTouched || busy === 'bw-search'}
            >
              BW에서 찾기
            </button>
            {bwSearchResults.length > 0 ? (
              <div className="bwSearchResults" aria-label="BW live search results">
                {bwSearchResults.map((item) => (
                  <article key={`${item.object_type}-${item.object_id}`} className="bwSearchResult">
                    <button
                      className="searchResultMain"
                      onClick={() => {
                        addLiveObjectName(item.object_id, item.object_type);
                        if (item.object_type && item.object_type !== 'UNKNOWN') setLiveObjectType(item.object_type);
                      }}
                      title="분석 대상에 추가"
                    >
                      <span>{item.object_type}</span>
                      <strong>{item.object_id}</strong>
                      <small>{item.name || '설명 없음'}</small>
                    </button>
                    <button
                      className="miniPrimaryButton"
                      onClick={() => void fetchAndAnalyzeObject(item)}
                      disabled={busy === 'live-analyze'}
                      title="BW에서 메타데이터를 가져오고 Lineage를 바로 실행"
                    >
                      가져와 분석
                    </button>
                  </article>
                ))}
              </div>
            ) : (
              <small>정확한 object name을 몰라도 BW 검색으로 후보를 찾을 수 있습니다.</small>
            )}
          </section>

          <label className="fieldLabel analysisBasisField">
            Analysis basis
            <div className="basisRow">
              <select value={selectedSnapshotId} onChange={(event) => chooseSnapshot(event.target.value)}>
                <option value="">분석 기준 없음</option>
                {snapshots.map((snapshot) => (
                  <option key={snapshot.id} value={snapshot.id}>
                    {compactDate(snapshot.created_at)} · {snapshot.object_count} objects
                  </option>
                ))}
              </select>
              <button
                className="secondaryButton"
                onClick={() => void refreshAnalysisBasis()}
                disabled={!selectedSnapshotId || !connectionReady || bwSetupTouched || busy === 'refresh-bw'}
                title="현재 분석 기준의 선택 scope를 BW에서 다시 가져옵니다."
              >
                BW 새로고침
              </button>
            </div>
          </label>

          <div className="catalogActionCard pickerBasket">
            <div className="basketHeader">
              <strong>Analysis targets</strong>
              <span>{liveObjectNameTokens.length} selected</span>
            </div>
            <button className="secondaryButton wide" onClick={() => addLiveObjectName(selectedObjectId, selectedObject?.type)} disabled={!selectedObjectId}>
              Add selected to targets
            </button>
            {liveObjectNameTokens.length > 0 ? (
              <div className="selectedLiveObjects frontBasket" aria-label="selected live capture objects">
                {liveObjectNameTokens.map((name) => (
                  <button key={name} className="selectedObjectChip" onClick={() => removeLiveObjectName(name)} title="Capture 대상에서 제거">
                    {liveObjectChipLabel(name)} ×
                  </button>
                ))}
              </div>
            ) : (
              <small>No analysis target yet</small>
            )}
            <div className="captureRow">
              <button className="secondaryButton" onClick={captureFixture} disabled={busy === 'snapshot'}>Fixture demo</button>
              <button
                className="primaryButton"
                onClick={captureLive}
                disabled={!connectionReady || bwSetupTouched || !liveCaptureTargetReady || busy === 'snapshot'}
                title={liveCaptureButtonTitle(connectionReady, liveCaptureTargetReady)}
              >
                Fetch from BW
              </button>
            </div>
            <button className="ghostButton wide" onClick={() => setDiagnosticsOpen(true)}>Settings</button>
          </div>
          <CaptureOutcomeCard snapshot={selectedSnapshot} compact />

          {captureScope.length > 0 ? (
            <div className="scopeMini" aria-label="snapshot capture scope">
              <strong>Scope</strong>
              <span>{captureScope.filter((item) => item.role === 'selected').length} selected · {captureScope.filter((item) => item.role === 'discovered').length} discovered</span>
            </div>
          ) : null}

          {GLOSSARY_VISIBLE ? <TermsOverview terms={glossaryTerms} onOpen={() => setActiveTab('glossary')} /> : null}

          <input
            className="catalogSearch"
            placeholder="object 검색"
            value={catalogQuery}
            onChange={(event) => {
              setAllowHiddenSelection(false);
              setCatalogQuery(event.target.value);
            }}
          />
          <div className="filterChips">
            {typeFilters.map((filter) => (
              <button
                key={filter || 'all'}
                className={filter === objectType ? 'chip active' : 'chip'}
                onClick={() => {
                  setAllowHiddenSelection(false);
                  setObjectType(filter);
                }}
              >
                {filter || '전체'}
              </button>
            ))}
          </div>

          <div className="objectList" aria-busy={busy === 'catalog'}>
            {currentObjects.length === 0 ? (
              <div className="emptyState">Snapshot을 capture하세요.</div>
            ) : (
              currentObjects.map((item) => (
                <button
                  key={item.id}
                  className={item.id === selectedObjectId ? 'objectItem active' : 'objectItem'}
                  title={`${item.type} · ${item.id}${item.name ? ` · ${item.name}` : ''}`}
                  onClick={() => {
                    invalidateAnalysisRequests();
                    setAllowHiddenSelection(false);
                    setSelectedObjectId(item.id);
                    setLineage(null);
                    setLineageAdvice(null);
                    setLineageTour(null);
                    setLineageTourStepIndex(0);
                    setImpact(null);
                setImpactReview(null);
                setAgenticReview(null);
      setAssistantReview(null);
                    setImpactAdvice(null);
                    setImpactTour(null);
                    setImpactTourStepIndex(0);
                    setObjectFreshness(null);
                  }}
                >
                  <span className="objectType">{item.type}</span>
                  <strong>{item.id}</strong>
                  <small>{item.name || item.label || '—'}</small>
                  {freshnessFromMetadata(item.metadata) ? (
                    <FreshnessBadge display={classifyFreshness(freshnessFromMetadata(item.metadata))} compact />
                  ) : null}
                </button>
              ))
            )}
          </div>
            {currentObjectNextCursor ? (
              <button
                className="secondaryButton fullWidth"
                disabled={busy === 'catalog'}
                onClick={() => void refreshObjects(selectedSnapshotId, currentObjectNextCursor)}
              >
                objects 더 보기
              </button>
            ) : null}
        </aside>

        <section className="workspacePane">
          <nav className="tabBar">
            <TabButton id="lineage" active={activeTab} onClick={setActiveTab} label="Lineage" />
            <TabButton id="impact" active={activeTab} onClick={setActiveTab} label="Impact" />
            <TabButton id="ask" active={activeTab} onClick={setActiveTab} label="Ask BW / Review" />
            {!IMPACT_UNIFIED ? <TabButton id="query" active={activeTab} onClick={setActiveTab} label="Query Analysis" /> : null}
            {!IMPACT_UNIFIED ? <TabButton id="sql" active={activeTab} onClick={setActiveTab} label="SQL Analysis" /> : null}
            {GLOSSARY_VISIBLE ? <TabButton id="glossary" active={activeTab} onClick={setActiveTab} label="Glossary" /> : null}
          </nav>

          <WorkspaceContextBar
            activeTab={activeTab}
            selectedObject={selectedObject}
            selectedSnapshot={selectedSnapshot}
            lineage={lineage}
            impactReview={impactReview}
          />

          {activeTab === 'lineage' ? (
            <LineageTab
              selectedObject={selectedObject}
              objectDetail={selectedObjectDetail}
              objectFreshness={selectedFreshness}
              lineage={lineage}
              lineageAdvice={lineageAdvice}
              lineageTour={lineageTour}
              objectGlossary={selectedObjectGlossary}
              graphStats={graphStats}
              direction={direction}
              setDirection={setDirection}
              depth={depth}
              setDepth={setDepth}
              nodeCap={nodeCap}
              setNodeCap={setNodeCap}
              edgeCap={edgeCap}
              setEdgeCap={setEdgeCap}
              onRun={() => void runLineage()}
              onAdvice={() => void runLineageAdvice()}
              onTour={() => void runLineageTour()}
              onOpenAsk={() => setActiveTab('ask')}
              tourStepIndex={lineageTourStepIndex}
              setTourStepIndex={setLineageTourStepIndex}
              onSelect={(id) => {
                invalidateAnalysisRequests();
                setAllowHiddenSelection(true);
                setSelectedObjectId(id);
                setLineageAdvice(null);
                setLineageTour(null);
                setLineageTourStepIndex(0);
                setImpact(null);
                setImpactReview(null);
                setAgenticReview(null);
      setAssistantReview(null);
                setImpactAdvice(null);
                setImpactTour(null);
              }}
              onExpand={(id) => {
                invalidateAnalysisRequests();
                setAllowHiddenSelection(true);
                setLineageAdvice(null);
                setLineageTour(null);
                setLineageTourStepIndex(0);
                setImpact(null);
                setImpactReview(null);
                setAgenticReview(null);
      setAssistantReview(null);
                setImpactAdvice(null);
                setImpactTour(null);
                void runLineage(id);
              }}
              busy={busy === 'lineage'}
              adviceBusy={busy === 'lineage-advice'}
              tourBusy={busy === 'lineage-tour'}
            />
          ) : null}

          {activeTab === 'impact' ? (
            <ImpactTab
              selectedObject={selectedObject}
              scenarioId={impactScenarioId}
              setScenarioId={setImpactScenarioId}
              changeType={changeType}
              setChangeType={setChangeType}
              fieldName={fieldName}
              setFieldName={setFieldName}
              objectFields={objectFields}
              description={scenarioDescription}
              setDescription={setScenarioDescription}
              impactDepth={impactDepth}
              setImpactDepth={setImpactDepth}
              onRun={() => void runImpact()}
              onAdvice={() => void runImpactAdvice()}
              impact={impact}
              impactReview={impactReview}
              impactAdvice={impactAdvice}
              impactTour={impactTour}
              objectFreshness={selectedFreshness}
              queryName={queryName}
              setQueryName={setQueryNameFromInput}
              includeSqlEvidence={includeSqlEvidence}
              setIncludeSqlEvidence={setIncludeSqlEvidence}
              sqlViewId={sqlViewId}
              setSqlViewId={setSqlViewId}
              sqlFile={sqlFile}
              setSqlFile={setSqlFile}
              sqlText={sqlText}
              setSqlText={setSqlText}
              busy={busy === 'impact'}
              agenticBusy={busy === 'impact-agentic'}
              adviceBusy={busy === 'impact-advice'}
              tourBusy={busy === 'impact-tour'}
              onTour={() => void runImpactTour()}
              tourStepIndex={impactTourStepIndex}
              setTourStepIndex={setImpactTourStepIndex}
              onOpenAsk={() => setActiveTab('ask')}
            />
          ) : null}

          {activeTab === 'ask' ? (
            <AskReviewTab
              selectedObject={selectedObject}
              lineage={lineage}
              impact={impact}
              impactReview={impactReview}
              assistantReview={assistantReview}
              agenticReview={agenticReview}
              agenticQuestion={agenticQuestion}
              setAgenticQuestion={(value) => {
                setAgenticQuestion(value);
                setAssistantPreset(null);
              }}
              setAssistantPreset={setAssistantPreset}
              onAgenticReview={() => void runAgenticReview()}
              onOpenLineage={() => setActiveTab('lineage')}
              onOpenImpact={() => setActiveTab('impact')}
              busy={busy === 'impact-agentic'}
              disabled={!selectedObject || busy === 'impact' || busy === 'impact-advice' || busy === 'impact-tour'}
            />
          ) : null}

          {!IMPACT_UNIFIED && activeTab === 'query' ? (
            <QueryTab
              selectedSnapshot={selectedSnapshot}
              selectedObject={selectedObject}
              queryName={queryName}
              setQueryName={setQueryNameFromInput}
              queryAnalysis={queryAnalysis}
              onRun={() => void runQueryAnalysis()}
              busy={busy === 'query-analysis'}
            />
          ) : null}

          {!IMPACT_UNIFIED && activeTab === 'sql' ? (
            <SqlTab
              runtime={runtime}
              viewId={sqlViewId}
              setViewId={setSqlViewId}
              sqlFile={sqlFile}
              setSqlFile={setSqlFile}
              sqlText={sqlText}
              setSqlText={setSqlText}
              question={sqlQuestion}
              setQuestion={setSqlQuestion}
              explain={sqlExplain}
              draft={sqlDraft}
              onExplain={() => void runSqlExplain()}
              onDraft={() => void runSqlDraft()}
              busy={busy}
            />
          ) : null}

          {GLOSSARY_VISIBLE && activeTab === 'glossary' ? (
            <GlossaryTab
              selectedSnapshot={selectedSnapshot}
              query={glossaryQuery}
              setQuery={setGlossaryQuery}
              terms={glossaryTerms}
              aggregate={glossaryAggregate}
              onSearch={() => void searchGlossary()}
              onLifecycle={(termId, lifecycle) => void confirmGlossaryTerm(termId, lifecycle)}
              onSelectObject={(objectId) => {
                invalidateAnalysisRequests();
                setAllowHiddenSelection(true);
                setSelectedObjectId(objectId);
                setObjectDetail(null);
                setLineage(null);
                setLineageAdvice(null);
                setLineageTour(null);
                setLineageTourStepIndex(0);
                setImpact(null);
                setImpactReview(null);
                setAgenticReview(null);
      setAssistantReview(null);
                setImpactAdvice(null);
                setImpactTour(null);
                setImpactTourStepIndex(0);
                setObjectFreshness(null);
                setActiveTab('lineage');
              }}
              onAddTarget={addLiveObjectName}
              busy={busy === 'glossary'}
            />
          ) : null}
        </section>
      </main>
    </div>
  );
}

function WorkspaceContextBar(props: {
  activeTab: AppTab;
  selectedObject: CatalogObject | null;
  selectedSnapshot: SnapshotSummary | null;
  lineage: LineageResponse | null;
  impactReview: ImpactReviewResponse | null;
}) {
  const activeTask = props.activeTab === 'lineage'
    ? 'Lineage'
    : props.activeTab === 'impact'
      ? 'Impact'
      : props.activeTab === 'ask'
        ? 'Ask BW / Review'
        : props.activeTab;
  const objectLabel = props.selectedObject
    ? `${props.selectedObject.type} · ${props.selectedObject.id}`
    : 'No object selected';
  const evidenceLabel = props.impactReview
    ? `${props.impactReview.impact.findings.length} findings · ${props.impactReview.manual_verification_gaps.length} manual checks`
    : props.lineage
      ? `${props.lineage.nodes.length} lineage nodes · ${props.lineage.evidence_ids.length} evidence IDs`
      : 'Run Lineage or Impact to build evidence';

  return (
    <div className="workspaceContextBar" aria-label="selected object and safety context">
      <div>
        <span className="eyebrow">Selected object</span>
        <strong>{objectLabel}</strong>
      </div>
      <div>
        <span className="eyebrow">Task</span>
        <strong>{activeTask}</strong>
      </div>
      <div>
        <span className="eyebrow">Evidence</span>
        <strong>{evidenceLabel}</strong>
      </div>
      <div>
        <span className="eyebrow">Safety</span>
        <strong>Read-only metadata · no BW query execution · no data preview · local-first · evidence-bound LLM</strong>
      </div>
      <div>
        <span className="eyebrow">Basis</span>
        <strong>{props.selectedSnapshot ? compactDate(props.selectedSnapshot.created_at) : 'none'}</strong>
      </div>
    </div>
  );
}

interface AssistantPresetLink {
  label: string;
  description: string;
  onClick: () => void;
  disabled?: boolean;
}

function AssistantPresetLinks(props: { title: string; presets: AssistantPresetLink[] }) {
  return (
    <div className="assistantPresetBlock" aria-label={props.title}>
      <span className="eyebrow">{props.title}</span>
      <div className="assistantPresetGrid">
        {props.presets.map((preset) => (
          <button
            key={preset.label}
            type="button"
            className="assistantPresetButton"
            onClick={preset.onClick}
            disabled={preset.disabled}
          >
            <strong>{preset.label}</strong>
            <small>{preset.description}</small>
          </button>
        ))}
      </div>
    </div>
  );
}

function AskReviewTab(props: {
  selectedObject: CatalogObject | null;
  lineage: LineageResponse | null;
  impact: ImpactScenarioResponse | null;
  impactReview: ImpactReviewResponse | null;
  assistantReview: AssistantReviewResponse | null;
  agenticReview: AgenticReviewRun | null;
  agenticQuestion: string;
  setAgenticQuestion: (value: string) => void;
  setAssistantPreset: (value: string | null) => void;
  onAgenticReview: () => void;
  onOpenLineage: () => void;
  onOpenImpact: () => void;
  busy: boolean;
  disabled: boolean;
}) {
  const impactSummary = deriveImpactSummary(props.impact);
  const reviewDisabled = props.disabled || !props.selectedObject;
  return (
    <div className="askReviewLayout">
      <section className="controlCard askLauncherCard">
        <span className="eyebrow">Ask BW / Review</span>
        <h1>Ask over lineage and impact evidence</h1>
        <p className="tabPurpose">
          Assistant answers are bounded to local metadata, deterministic impact evidence, and citation IDs through the unified assistant review endpoint.
        </p>
        <div className="scenarioObject">선택 object: <strong>{props.selectedObject?.id ?? '없음'}</strong></div>
        <div className="assistantReadinessGrid">
          <Metric label="Lineage evidence" value={props.lineage ? `${props.lineage.nodes.length} nodes` : 'not run'} />
          <Metric label="Impact grade" value={impactSummary.gradeLabel} />
          <Metric label="Review pack" value={props.impactReview ? `${props.impactReview.impact.findings.length} findings` : 'not run'} />
        </div>
        <AssistantPresetLinks
          title="Preset questions"
          presets={askReviewPresets.map((preset) => ({
            label: preset.label,
            description: 'Fill review objective',
            onClick: () => {
              props.setAgenticQuestion(preset.prompt);
              props.setAssistantPreset(preset.label);
            },
            disabled: !props.selectedObject,
          }))}
        />
        <div className="assistantLinkRow" aria-label="evidence setup links">
          <button type="button" className="ghostButton" onClick={props.onOpenLineage}>Open Lineage</button>
          <button type="button" className="ghostButton" onClick={props.onOpenImpact}>Open Impact</button>
        </div>
        <p className="policyNote">Read-only metadata · no BW query execution · no data preview · local-first · evidence-bound LLM.</p>
      </section>
      <section className="resultCard assistantResultCard">
        <AgenticReviewWorkspace
          assistantReview={props.assistantReview}
          review={props.agenticReview}
          deterministicPack={props.impactReview}
          question={props.agenticQuestion}
          setQuestion={props.setAgenticQuestion}
          onRun={props.onAgenticReview}
          busy={props.busy}
          disabled={reviewDisabled}
        />
      </section>
    </div>
  );
}

function LineageTab(props: {
  selectedObject: CatalogObject | null;
  objectDetail: CatalogObjectDetail | null;
  objectFreshness: RequestFreshnessResponse | null;
  lineage: LineageResponse | null;
  lineageAdvice: LineageAdviceResponse | null;
  lineageTour: LineageTourResponse | null;
  objectGlossary: GlossaryTerm[];
  graphStats: string;
  direction: Direction;
  setDirection: (value: Direction) => void;
  depth: number;
  setDepth: (value: number) => void;
  nodeCap: number;
  setNodeCap: (value: number) => void;
  edgeCap: number;
  setEdgeCap: (value: number) => void;
  onRun: () => void;
  onAdvice: () => void;
  onTour: () => void;
  onOpenAsk: () => void;
  tourStepIndex: number;
  setTourStepIndex: (value: number) => void;
  onSelect: (id: string) => void;
  onExpand: (id: string) => void;
  busy: boolean;
  adviceBusy: boolean;
  tourBusy: boolean;
}) {
  const layerGroups = props.lineage ? groupNodesByDisplayLayer(props.lineage.nodes) : [];
  const activeLayerGroups = DISPLAY_LAYER_ORDER
    .filter((layer) => layer !== 'Unknown' || layerGroups.some((group) => group.layer === 'Unknown'))
    .map((layer) => ({
      layer,
      count: layerGroups.find((group) => group.layer === layer)?.nodes.length ?? 0,
    }));
  const freshnessSummary = props.lineage ? summarizeFreshness(props.lineage.nodes, props.objectDetail?.id ?? null, props.objectFreshness) : null;
  const selectedLayer = inferDisplayLayer(props.objectDetail ?? props.selectedObject ?? {}).label;
  const selectedFreshnessDisplay = classifyFreshness(props.objectFreshness ?? freshnessFromMetadata(props.objectDetail?.metadata) ?? freshnessFromMetadata(props.selectedObject?.metadata));
  const tourSteps = normalizeGuidedTourSteps(props.lineageTour);
  const currentTourIndex = clampTourIndex(props.tourStepIndex, tourSteps);
  const directionOptions: Array<{ value: Direction; label: string; hint: string }> = [
    { value: 'upstream', label: 'Upstream', hint: '소스/상위 의존성' },
    { value: 'downstream', label: 'Downstream', hint: '소비/하위 영향' },
    { value: 'both', label: 'Both', hint: '양방향 흐름' },
  ];
  const evidenceHealth: Array<{ label: string; value: string; tone: 'ok' | 'warn' | 'info' | 'neutral' }> = [
    lineageHealthItem('Dataflow', props.selectedObject, props.lineage),
    whereUsedHealthItem('Where-used', props.selectedObject, props.lineage, props.direction),
    objectDetailHealthItem('Object detail', props.selectedObject, props.objectDetail),
    freshnessHealthItem('Freshness', selectedFreshnessDisplay, props.objectFreshness),
  ];

  return (
    <div className="workspaceGrid sliceGWorkspaceGrid">
      <section className="lineageSummaryStrip" aria-label="Slice G lineage status summary">
        <div className="summaryHero">
          <span className="eyebrow">Slice G Workbench</span>
          <strong>{props.selectedObject?.id ?? 'Select a BW object'}</strong>
          <small>Read-only lineage lanes · metadata and citations only · no data rows</small>
        </div>
        <div className="laneMiniStrip" aria-label="layer lane counts">
          {activeLayerGroups.map((group) => (
            <span key={group.layer} className={group.count > 0 ? 'laneMini active' : 'laneMini'}>
              <b>{group.layer}</b><em>{group.count}</em>
            </span>
          ))}
        </div>
        <div className="freshnessMiniStrip" aria-label="freshness status counts">
          {freshnessSummary ? (
            <>
              <FreshnessCount label="Fresh" count={freshnessSummary.fresh} state="fresh" />
              <FreshnessCount label="Stale" count={freshnessSummary.stale} state="stale" />
              <FreshnessCount label="No req" count={freshnessSummary.none} state="none" />
              <FreshnessCount label="Unknown" count={freshnessSummary.unknown} state="unknown" />
            </>
          ) : (
            <span className="mutedSmall">Run lineage to populate lane/freshness context.</span>
          )}
        </div>
      </section>

      <section className="controlCard">
        <div className="sectionTitle">
          <span className="eyebrow">Lineage</span>
          <h1>Trace Lineage / 흐름 보기</h1>
          <p className="tabPurpose">Source → Transform → Model → Semantic → Runtime lane flow. Read-only metadata only; no data rows or BW query execution.</p>
          <p>{props.graphStats}</p>
        </div>

        <div className="lineageObjectContext" aria-label="selected object context">
          <span>Selected object</span>
          <strong>{props.selectedObject?.id ?? '객체 선택 필요'}</strong>
          <small>{props.selectedObject ? `${props.selectedObject.type} · ${selectedLayer}` : 'Catalog에서 BW object를 선택하세요.'}</small>
        </div>

        <div className="directionChipGroup" role="group" aria-label="Lineage direction">
          {directionOptions.map((option) => (
            <button
              key={option.value}
              type="button"
              className={props.direction === option.value ? 'directionChip active' : 'directionChip'}
              aria-pressed={props.direction === option.value}
              onClick={() => props.setDirection(option.value)}
            >
              <strong>{option.label}</strong>
              <small>{option.hint}</small>
            </button>
          ))}
        </div>

        <button className="primaryButton wide" onClick={props.onRun} disabled={!props.selectedObject || props.busy || props.adviceBusy || props.tourBusy}>
          {props.busy ? '흐름 확인 중…' : '흐름 보기'}
        </button>

        <section className="evidenceHealthSummary" aria-label="Lineage evidence health summary">
          <div className="panel-title">
            <span className="eyebrow">Evidence health</span>
            <small>항상 보이는 근거 상태</small>
          </div>
          <div className="evidenceHealthGrid">
            {evidenceHealth.map((item) => (
              <span key={item.label} className={`evidenceHealthBadge ${item.tone}`}>
                <b>{item.label}</b>
                <em>{item.value}</em>
              </span>
            ))}
          </div>
        </section>

        <details className="lineageAdvancedControls">
          <summary>Advanced limits</summary>
          <div className="compactForm three lineageAdvancedGrid">
            <NumberField label="Depth" value={props.depth} min={0} max={20} onChange={props.setDepth} />
            <NumberField label="Node cap" value={props.nodeCap} min={1} max={500} onChange={props.setNodeCap} />
            <NumberField label="Edge cap" value={props.edgeCap} min={0} max={1000} onChange={props.setEdgeCap} />
          </div>
        </details>

        <AssistantPresetLinks
          title="Assistant presets"
          presets={[
            {
              label: props.tourBusy ? 'Building evidence panel…' : 'Evidence panel',
              description: 'Evidence Walkthrough preset · citation-bound lane narrative',
              onClick: props.onTour,
              disabled: !props.selectedObject || props.busy || props.adviceBusy || props.tourBusy,
            },
            {
              label: props.adviceBusy ? 'Summarizing…' : 'Business summary',
              description: 'Evidence IDs only',
              onClick: props.onAdvice,
              disabled: !props.selectedObject || props.busy || props.adviceBusy || props.tourBusy,
            },
            {
              label: 'Ask BW / Review',
              description: 'Open assistant surface',
              onClick: props.onOpenAsk,
              disabled: !props.selectedObject,
            },
          ]}
        />
        {props.lineage ? (
          <div className="metaGrid">
            <Metric label="Truncated" value={props.lineage.truncated ? 'Yes' : 'No'} />
            <Metric label="Cycles" value={props.lineage.cycles_detected ? 'Detected' : 'None'} />
            <Metric label="Evidence" value={String(props.lineage.evidence_ids.length)} />
          </div>
        ) : null}
      </section>
      <section className="graphCard sliceGraphCard">
        <LineageGraph
          lineage={props.lineage}
          onSelect={props.onSelect}
          selectedId={props.objectDetail?.id ?? null}
          selectedFreshness={props.objectFreshness}
        />
      </section>
      <aside className="detailsDrawer sliceGDrawer">
        <GuidedTourPanel
          title="Evidence panel"
          response={props.lineageTour}
          steps={tourSteps}
          currentIndex={currentTourIndex}
          onStepIndex={props.setTourStepIndex}
          onRun={props.onTour}
          busy={props.tourBusy}
        />
        <span className="eyebrow">Details</span>
        <h2>{props.objectDetail?.id ?? '선택된 node 없음'}</h2>
        {props.objectDetail ? (
          <>
            <p>{props.objectDetail.summary || props.objectDetail.name || props.objectDetail.label || '설명 없음'}</p>
            <div className="detailRows">
              <span>Type</span><strong>{props.objectDetail.type}</strong>
              <span>Layer</span><strong>{selectedLayer}</strong>
              <span>Freshness</span><strong><FreshnessBadge display={selectedFreshnessDisplay} /></strong>
              <span>Incoming</span><strong>{props.objectDetail.incoming_count}</strong>
              <span>Outgoing</span><strong>{props.objectDetail.outgoing_count}</strong>
              <span>Evidence</span><strong>{props.objectDetail.evidence_ids.length}</strong>
            </div>
            {props.objectDetail.tags && props.objectDetail.tags.length > 0 ? (
              <div className="tagList" aria-label="object tags">
                {props.objectDetail.tags.slice(0, 8).map((tag) => <span key={tag}>{tag}</span>)}
              </div>
            ) : null}
            {GLOSSARY_VISIBLE ? <GlossaryList terms={props.objectGlossary} title="Glossary" emptyText="Glossary 용어 없음" /> : null}
            <button className="secondaryButton wide" onClick={() => props.onExpand(props.objectDetail!.id)}>
              Expand from node
            </button>
          </>
        ) : <p>카탈로그 또는 graph node를 선택하세요.</p>}
        {props.lineageAdvice ? (
          <div className={`llmAdviceBox ${props.lineageAdvice.status}`}>
            <h3>Business Summary</h3>
            <p>{props.lineageAdvice.message}</p>
            {props.lineageAdvice.advice ? <pre>{props.lineageAdvice.advice}</pre> : null}
            <small>Citations: {props.lineageAdvice.citations.join(', ') || 'none'}</small>
          </div>
        ) : null}
        <SafetyCopy />
      </aside>
    </div>
  );
}

const impactSeverityOrder = ['HIGH', 'MEDIUM', 'LOW', 'UNKNOWN'] as const;

function ImpactTab(props: {
  selectedObject: CatalogObject | null;
  scenarioId: ImpactScenarioId;
  setScenarioId: (value: ImpactScenarioId) => void;
  changeType: ChangeType;
  setChangeType: (value: ChangeType) => void;
  fieldName: string;
  setFieldName: (value: string) => void;
  objectFields: ObjectField[];
  description: string;
  setDescription: (value: string) => void;
  impactDepth: number;
  setImpactDepth: (value: number) => void;
  onRun: () => void;
  onAdvice: () => void;
  onTour: () => void;
  impact: ImpactScenarioResponse | null;
  impactReview: ImpactReviewResponse | null;
  impactAdvice: ImpactAdviceResponse | null;
  impactTour: ImpactTourResponse | null;
  objectFreshness: RequestFreshnessResponse | null;
  queryName: string;
  setQueryName: (value: string) => void;
  includeSqlEvidence: boolean;
  setIncludeSqlEvidence: (value: boolean) => void;
  sqlViewId: string;
  setSqlViewId: (value: string) => void;
  sqlFile: string;
  setSqlFile: (value: string) => void;
  sqlText: string;
  setSqlText: (value: string) => void;
  busy: boolean;
  agenticBusy: boolean;
  adviceBusy: boolean;
  tourBusy: boolean;
  onOpenAsk: () => void;
  tourStepIndex: number;
  setTourStepIndex: (value: number) => void;
}) {
  const impactSummary = deriveImpactSummary(props.impact);
  const freshnessDisplay = classifyFreshness(props.objectFreshness ?? freshnessFromMetadata(props.selectedObject?.metadata));
  const tourSteps = normalizeGuidedTourSteps(props.impactTour);
  const currentTourIndex = clampTourIndex(props.tourStepIndex, tourSteps);
  const activeScenarioById = impactScenarioCards.find((card) => card.id === props.scenarioId) ?? impactScenarioCards[0];
  const activeScenario = activeScenarioById.changeTypes.includes(props.changeType)
    ? activeScenarioById
    : impactScenarioCards.find((card) => card.changeTypes.includes(props.changeType)) ?? activeScenarioById;
  const affectedGroups = impactSeverityOrder.map((severity) => ({
    severity,
    items: props.impact?.affected_objects.filter((item) => item.severity === severity) ?? [],
  }));
  const assistantDisabled = !props.selectedObject || props.busy || props.agenticBusy || props.adviceBusy || props.tourBusy;

  function selectScenario(card: ImpactScenarioCard) {
    props.setScenarioId(card.id);
    props.setChangeType(card.changeType);
    if (!props.description.trim() || isImpactScenarioDefaultDescription(props.description)) {
      props.setDescription(card.defaultDescription);
    }
  }

  function selectFieldChangeType(value: ChangeType) {
    props.setScenarioId('field-change');
    props.setChangeType(value);
  }

  return (
    <div className="impactLayout sliceGImpactLayout">
      <section className="controlCard impactScenarioWorkspace">
        <span className="eyebrow">Impact</span>
        <h1>Scenario-first impact workspace</h1>
        <p className="tabPurpose">Pick the change scenario first; deterministic grade, affected objects, evidence, and manual checks render on the right.</p>
        <div className="scenarioObject">선택 object: <strong>{props.selectedObject?.id ?? '없음'}</strong></div>
        <div className="impactSafetyCopy" aria-label="Impact safety boundaries">
          Read-only metadata · no BW query execution · no data preview · parse-only SQL
        </div>

        <div className="impactScenarioCards" aria-label="Impact scenario cards">
          {impactScenarioCards.map((card) => (
            <button
              key={card.id}
              type="button"
              className={card.id === activeScenario.id ? 'impactScenarioCard active' : 'impactScenarioCard'}
              onClick={() => selectScenario(card)}
            >
              <strong>{card.title}</strong>
              <span>{card.description}</span>
              <small>{card.changeType}</small>
            </button>
          ))}
        </div>

        {activeScenario.fieldOriented ? (
          <div className="fieldScenarioControls" aria-label="field-oriented scenario controls">
            <label>Field / 필드
              {props.objectFields.length > 0 ? (
                <select value={props.fieldName} onChange={(event) => props.setFieldName(event.target.value)}>
                  {props.objectFields.map((field) => (
                    <option key={field.name} value={field.name}>{field.name}{field.role ? ` · ${field.role}` : ''}</option>
                  ))}
                </select>
              ) : (
                <input value={props.fieldName} onChange={(event) => props.setFieldName(event.target.value)} placeholder="Manual field name" />
              )}
            </label>
            <label>Field change detail
              <select value={props.changeType} onChange={(event) => selectFieldChangeType(event.target.value as ChangeType)}>
                {fieldOrientedChangeTypes.map((item) => <option key={item} value={item}>{item}</option>)}
              </select>
            </label>
            <p className="scenarioHint">{activeScenario.hint}</p>
          </div>
        ) : (
          <div className="scenarioReadOnlyHint" aria-label="field selector not required">
            <strong>Field input not required</strong>
            <span>{activeScenario.hint}</span>
          </div>
        )}

        <label>Scenario notes / 설명
          <textarea value={props.description} onChange={(event) => props.setDescription(event.target.value)} rows={4} />
        </label>

        <details className="advancedSection evidenceScopeAdvanced">
          <summary>Evidence scope (Advanced)</summary>
          <p className="mutedSmall">Bounded traversal plus optional Query/SQL evidence. Query evidence is metadata-only; SQL evidence is parse-only and never executed.</p>
          <NumberField label="Impact depth" value={props.impactDepth} min={1} max={20} onChange={props.setImpactDepth} />
          <label>Query evidence names
            <input
              value={props.queryName}
              placeholder="비워두면 impacted QUERY 자동 포함"
              onChange={(event) => props.setQueryName(event.target.value)}
            />
          </label>
          <label className="checkField compactCheck">
            <input
              type="checkbox"
              checked={props.includeSqlEvidence}
              onChange={(event) => props.setIncludeSqlEvidence(event.target.checked)}
            />
            SQL / Native SQL reference evidence 포함
          </label>
          <label>SQL view ID
            <input value={props.sqlViewId} onChange={(event) => props.setSqlViewId(event.target.value)} disabled={!props.includeSqlEvidence} />
          </label>
          <label>SQL text
            <textarea
              rows={4}
              value={props.sqlText}
              placeholder="SQL text가 있으면 file보다 우선합니다. DB SQL 실행 없음."
              disabled={!props.includeSqlEvidence}
              onChange={(event) => props.setSqlText(event.target.value)}
            />
          </label>
          <label>SQL file
            <input value={props.sqlFile} onChange={(event) => props.setSqlFile(event.target.value)} disabled={!props.includeSqlEvidence} />
          </label>
          <p className="policyNote">Read-only metadata · No BW query execution · No data preview · Parse-only SQL</p>
        </details>

        <button className="primaryButton wide" onClick={props.onRun} disabled={!props.selectedObject || props.busy || props.agenticBusy || props.adviceBusy || props.tourBusy}>
          {props.busy ? 'Assessing Impact…' : '영향 보기 / Assess Impact'}
        </button>
      </section>

      <section className="resultCard impactWorkbenchPanel">
        <div className="impactSummaryPanel" aria-label="change grade impact summary">
          <div className={`gradeBadge grade-${impactSummary.grade.toString().toLowerCase()}`}>
            <span>Risk grade</span>
            <strong>{impactSummary.gradeLabel}</strong>
          </div>
          <div className="impactHeadline">
            <strong>{impactSummary.headline}</strong>
            <small>Freshness context: <FreshnessBadge display={freshnessDisplay} /></small>
          </div>
          <div className="impactMetricGrid">
            <Metric label="Affected" value={String(impactSummary.affectedCount)} />
            <Metric label="Evidence" value={String(impactSummary.evidenceCount)} />
            <Metric label="Manual checks" value={String(impactSummary.manualVerificationCount)} />
            <Metric label="Bounds" value={impactSummary.truncated ? 'Truncated' : 'Complete'} />
          </div>
          <div className="severityBars" aria-label="severity distribution">
            {impactSeverityOrder.map((severity) => (
              <div key={severity} className={`severityBar severity-${severity.toLowerCase()}`}>
                <span>{severity}</span>
                <b>{impactSummary.severityCounts[severity]}</b>
              </div>
            ))}
          </div>
        </div>

        <section className="affectedSeverityGroups" aria-label="affected objects by severity">
          <span className="eyebrow">Affected objects by severity</span>
          {props.impact ? (
            <div className="severityGroupList">
              {affectedGroups.map((group) => (
                <section key={group.severity} className={`severityGroup severity-group-${group.severity.toLowerCase()}`}>
                  <div className="severityGroupHeader">
                    <strong>{group.severity}</strong>
                    <span>{group.items.length} object{group.items.length === 1 ? '' : 's'}</span>
                  </div>
                  {group.items.length > 0 ? (
                    <div className="severityList">
                      {group.items.map((item) => (
                        <article key={`${group.severity}-${item.object_id}`} className={`severityItem ${item.severity.toLowerCase()}`}>
                          <div>
                            <strong>{item.object_id}</strong>
                            <span><b className="objectTypeLabel">{item.object_type}</b> · {item.confidence}</span>
                          </div>
                          <b>{item.severity}</b>
                          <p>{item.reason}</p>
                          {GLOSSARY_VISIBLE && item.glossary_terms && item.glossary_terms.length > 0 ? (
                            <div className="inlineTerms">
                              {item.glossary_terms.slice(0, 4).map((term) => <span key={term.id} title={term.evidence_ids.join(', ')}>{term.term}</span>)}
                            </div>
                          ) : null}
                          <small>Evidence IDs: {item.evidence_ids.join(', ') || '—'}</small>
                        </article>
                      ))}
                    </div>
                  ) : (
                    <p className="severityGroupEmpty">No {group.severity.toLowerCase()} affected objects in this bounded result.</p>
                  )}
                </section>
              ))}
              {props.impact.affected_objects.length === 0 ? <div className="emptyState">이 범위 내 영향 없음 (깊이 기준 확인 필요)</div> : null}
            </div>
          ) : <div className="emptyState">Scenario cards populate deterministic severity groups after 영향 보기 / Assess Impact.</div>}
        </section>

        <ImpactEvidenceCards review={props.impactReview} />
        <ManualVerificationChecklist review={props.impactReview} impact={props.impact} />
        <AssistantPresetLinks
          title="Assistant presets"
          presets={[
            {
              label: props.tourBusy ? 'Generating brief…' : 'Impact brief',
              description: 'Evidence cards + citations',
              onClick: props.onTour,
              disabled: assistantDisabled,
            },
            {
              label: props.adviceBusy ? 'Summarizing…' : 'Business summary',
              description: 'Evidence-bound wording',
              onClick: props.onAdvice,
              disabled: assistantDisabled,
            },
            {
              label: 'Ask BW / Review',
              description: 'Open review assistant link',
              onClick: props.onOpenAsk,
              disabled: !props.selectedObject,
            },
          ]}
        />
        <AuthorityCallout review={props.impactReview} />

        <GuidedTourPanel
          title="Impact Brief"
          response={props.impactTour}
          steps={tourSteps}
          currentIndex={currentTourIndex}
          onStepIndex={props.setTourStepIndex}
          onRun={props.onTour}
          busy={props.tourBusy}
        />

        {props.impactAdvice ? (
          <div className={`llmAdviceBox ${props.impactAdvice.status}`}>
            <h3>Business Summary</h3>
            <p>{props.impactAdvice.message}</p>
            {props.impactAdvice.advice ? <pre>{props.impactAdvice.advice}</pre> : null}
            <small>Citations: {props.impactAdvice.citations.join(', ') || 'none'}</small>
          </div>
        ) : null}
      </section>
    </div>
  );
}

function AgenticReviewWorkspace(props: {
  assistantReview: AssistantReviewResponse | null;
  review: AgenticReviewRun | null;
  deterministicPack: ImpactReviewResponse | null;
  question: string;
  setQuestion: (value: string) => void;
  onRun: () => void;
  busy: boolean;
  disabled: boolean;
}) {
  const assistant = props.assistantReview;
  const review = props.review;
  const deterministicPack = review?.deterministic_pack ?? props.deterministicPack;
  const cards = [...(review?.cards ?? [])].sort((left, right) => left.review_priority - right.review_priority);
  const objectives = review?.objectives ?? [];
  const hypotheses = review?.hypotheses ?? [];
  const gaps = review?.evidence_gaps ?? [];
  const manualChecks = review?.manual_checks ?? [];
  const assistantManualChecks = assistant?.manual_checks ?? [];
  const deterministicFindings = deterministicPack?.impact.findings ?? [];
  const coverageEntries = Object.entries(deterministicPack?.coverage_summary ?? {});
  const hasAnswer = Boolean(assistant?.answer) || cards.length > 0 || Boolean(review?.cab_summary) || deterministicFindings.length > 0;

  return (
    <section className="agenticWorkspace" aria-label="Ask BW / Review citation-bound answer area">
      <div className="agenticWorkspaceHeader">
        <div>
          <span className="eyebrow">Ask BW / Review</span>
          <h2>Citation-bound answer area</h2>
          <p>Answers stay bound to deterministic lineage/impact evidence, citation IDs, and manual review gaps. Raw chat, chain-of-thought, audit logs, and snapshot payloads are not rendered.</p>
        </div>
        <div className="agenticBoundaryCopy" aria-label="assistant copy boundaries">
          <span>Read-only metadata</span>
          <span>No BW query execution</span>
          <span>No data preview</span>
          <span>Local-first · evidence-bound LLM</span>
        </div>
      </div>

      <article className="agenticSectionCard assistantPromptCard">
        <div className="agenticSectionTitle">
          <span className="eyebrow">Review objective</span>
          <h3>Ask from selected evidence</h3>
        </div>
        <label>Ask / review objective
          <textarea
            value={props.question}
            rows={4}
            placeholder="Use a preset or ask for CAB risk, query exposure, freshness gaps, or manual BWMT checks."
            onChange={(event) => props.setQuestion(event.target.value)}
          />
        </label>
        <button className="primaryButton wide" onClick={props.onRun} disabled={props.disabled || props.busy}>
          {props.busy ? 'Running citation-bound review…' : 'Run citation-bound review'}
        </button>
        <p className="policyNote">Uses the unified assistant review endpoint with selected deterministic context only; no live BW calls, query execution, data preview, or raw snapshot payload.</p>
      </article>

      {assistant?.status === 'disabled' ? (
        <div className="agenticBanner disabled">LLM disabled — deterministic assistant fallback</div>
      ) : null}
      {assistant?.status === 'fallback' ? (
        <div className="agenticBanner fallback">LLM validation fallback — deterministic assistant answer</div>
      ) : null}
      {review?.status === 'disabled' ? (
        <div className="agenticBanner disabled">LLM disabled — deterministic findings only</div>
      ) : null}
      {review?.status === 'fallback' ? (
        <div className="agenticBanner fallback">Autonomous review failed validation — showing deterministic findings</div>
      ) : null}

      <article className="assistantAnswerArea" aria-label="citation-bound answer area">
        <div className="agenticSectionTitle">
          <span className="eyebrow">Answer</span>
          <h3>Facts, review cards, and citations</h3>
        </div>
        {assistant ? (
          <div className="assistantCabBlock unifiedAssistantAnswer" aria-label="unified assistant review answer">
            <div className="budgetGrid">
              <Metric label="assistant status" value={assistant.status} />
              <Metric label="confidence" value={assistant.confidence} />
              <Metric label="llm used" value={String(assistant.safety.llm_used)} />
              <Metric label="validator" value={assistant.safety.citation_validation} />
            </div>
            <pre className="agenticCabSummary">{assistant.answer}</pre>
            <CitationChipList citationIds={assistant.citations} />
          </div>
        ) : null}
        {review?.cab_summary ? (
          <div className="assistantCabBlock">
            <pre className="agenticCabSummary">{review.cab_summary}</pre>
            <button className="secondaryButton" onClick={() => void copyCabSummary(review.cab_summary)}>
              Copy CAB summary
            </button>
          </div>
        ) : null}
        {cards.length > 0 ? (
          <div className="agenticCardList">
            {cards.map((card) => (
              <article key={card.id} className="agenticCard">
                <div className="agenticCardHeader">
                  <span className={`provenanceBadge ${card.kind}`}>{provenanceLabel(card.kind)}</span>
                  <span className="reviewPriority">Priority {card.review_priority}</span>
                </div>
                <h4>{card.title}</h4>
                <p>{card.body}</p>
                <div className="agenticCardMeta">
                  <span>Severity: {card.severity_label ?? 'n/a'}</span>
                  <span>Source finding: {card.source_finding_id ?? 'n/a'}</span>
                </div>
                <CitationChipList citationIds={card.citation_ids} />
              </article>
            ))}
          </div>
        ) : null}
        {cards.length === 0 && deterministicFindings.length > 0 ? (
          <div className="agenticCardList">
            {deterministicFindings.slice(0, 6).map((finding) => (
              <article key={finding.id} className="agenticCard deterministicFallbackCard">
                <div className="agenticCardHeader">
                  <span className="provenanceBadge deterministic_finding">Deterministic finding</span>
                  <span className="reviewPriority">{finding.severity}</span>
                </div>
                <h4>{finding.impacted_object_id}</h4>
                <p>{finding.reason}</p>
                <div className="agenticCardMeta">
                  <span>Confidence: {finding.confidence}</span>
                  <span>Manual verification: {String(finding.manual_verification)}</span>
                </div>
                <CitationChipList citationIds={[...finding.evidence_node_ids, ...finding.evidence_edge_ids]} />
              </article>
            ))}
          </div>
        ) : null}
        {!hasAnswer ? (
          <div className="emptyState">Choose a preset or write a review objective. The assistant answer will stay citation-bound and will not execute BW queries.</div>
        ) : null}
      </article>

      <div className="agenticGrid">
        <article className="agenticSectionCard missing-evidence-gaps">
          <div className="agenticSectionTitle">
            <span className="eyebrow">Missing evidence / gaps</span>
            <h3>Evidence still needed</h3>
          </div>
          {gaps.length > 0 ? (
            <div className="agenticList">
              {gaps.map((gap) => (
                <div key={gap.id} className="agenticMiniItem">
                  <strong>{gap.description}</strong>
                  <p>{gap.missing_evidence}</p>
                  {gap.suggested_local_action ? <small>suggested_local_action: {gap.suggested_local_action}</small> : null}
                  {gap.related_object_id ? <small>related_object_id: {gap.related_object_id}</small> : null}
                  <CitationChipList citationIds={gap.citation_ids} />
                </div>
              ))}
            </div>
          ) : assistant?.unknowns.length ? (
            <div className="agenticList">
              {assistant.unknowns.map((unknown) => (
                <div key={unknown} className="agenticMiniItem">
                  <strong>Unknown / needs manual verification</strong>
                  <p>{unknown}</p>
                </div>
              ))}
            </div>
          ) : (
            <div className="emptyState">No assistant evidence gaps yet. Deterministic manual gaps remain in Impact evidence cards.</div>
          )}
        </article>

        <article className="agenticSectionCard manual-bwmt-checklist">
          <div className="agenticSectionTitle">
            <span className="eyebrow">Manual BWMT checklist</span>
            <h3>Human-only verification steps</h3>
          </div>
          {manualChecks.length > 0 ? (
            <div className="agenticList">
              {manualChecks.map((check) => (
                <div key={check.id} className="agenticMiniItem">
                  <strong>{check.title}</strong>
                  <small>{check.tool} · priority {check.priority}</small>
                  <p>{check.steps_summary}</p>
                  {check.related_finding_ids.length > 0 ? <small>Findings: {check.related_finding_ids.join(', ')}</small> : null}
                  <CitationChipList citationIds={check.citation_ids} />
                </div>
              ))}
            </div>
          ) : assistantManualChecks.length > 0 ? (
            <div className="agenticList">
              {assistantManualChecks.map((check) => (
                <div key={check.id} className="agenticMiniItem">
                  <strong>{check.title}</strong>
                  <small>{check.tool}</small>
                  <p>{check.steps_summary}</p>
                  {check.related_context_ids.length > 0 ? <small>Contexts: {check.related_context_ids.join(', ')}</small> : null}
                  <CitationChipList citationIds={check.citation_ids} />
                </div>
              ))}
            </div>
          ) : (
            <div className="emptyState">No manual BWMT checklist items returned.</div>
          )}
        </article>
      </div>

      <details className="advancedSection agenticAdvancedEvidence">
        <summary>Advanced · citation map and validator status</summary>
        <div className="agenticGrid compact">
          <article className="agenticSectionCard evidence-map">
            <div className="agenticSectionTitle">
              <span className="eyebrow">Evidence map</span>
              <h3>Card and hypothesis citations</h3>
            </div>
            <div className="budgetGrid coverageGrid">
              {coverageEntries.length > 0 ? coverageEntries.map(([key, value]) => (
                <Metric key={key} label={key.replace(/_/g, ' ')} value={String(value)} />
              )) : <Metric label="Coverage" value="0" />}
            </div>
            <div className="agenticCitationMap">
              <strong>Card citations</strong>
              {cards.length > 0 ? cards.map((card) => (
                <div key={card.id} className="agenticMiniItem">
                  <span>{card.title}</span>
                  <CitationChipList citationIds={card.citation_ids} />
                </div>
              )) : <p className="mutedSmall">No card citations yet.</p>}
              <strong>Hypothesis citations</strong>
              {hypotheses.length > 0 ? hypotheses.map((hypothesis) => (
                <div key={hypothesis.id} className="agenticMiniItem">
                  <span>{hypothesis.statement}</span>
                  <small>{hypothesis.status} · severity opinion {hypothesis.severity_opinion ?? 'n/a'}</small>
                  <p>{hypothesis.confidence_rationale}</p>
                  <CitationChipList citationIds={hypothesis.citation_ids} />
                </div>
              )) : <p className="mutedSmall">No hypotheses yet.</p>}
            </div>
          </article>
          <article className="agenticSectionCard validator-status">
            <div className="agenticSectionTitle">
              <span className="eyebrow">Safety + validation</span>
              <h3>Status without audit log details</h3>
            </div>
            <div className="budgetGrid">
              <Metric label="status" value={review?.status ?? 'not run'} />
              <Metric label="assistant" value={assistant?.status ?? 'not run'} />
              <Metric label="llm enabled" value={review ? String(review.llm_enabled) : 'n/a'} />
              <Metric label="llm disabled" value={review ? String(review.llm_disabled) : 'n/a'} />
              <Metric label="final authority" value={deterministicPack?.final_authority ?? 'impact.py'} />
            </div>
            <p className="mutedSmall">Validator details are summarized here without rendering raw audit logs, snapshot payloads, credentials, cookies, or secrets.</p>
          </article>
        </div>
      </details>
    </section>
  );
}

function ImpactEvidenceCards(props: { review: ImpactReviewResponse | null }) {
  const review = props.review;
  const queryEvidence = review?.query_evidence ?? [];
  const sqlEvidence = review?.sql_evidence ?? [];
  const freshnessEvidence = review?.freshness_evidence ?? [];
  const variableCount = queryEvidence.reduce((total, item) => total + item.variable_names.length, 0);
  const keyFigureCount = queryEvidence.reduce(
    (total, item) => total + item.calculated_key_figure_names.length + item.restricted_key_figure_names.length,
    0,
  );
  const referencedObjectCount = new Set(sqlEvidence.flatMap((item) => item.referenced_object_ids)).size;
  const referencedColumnCount = new Set(sqlEvidence.flatMap((item) => item.referenced_column_names)).size;
  const freshnessManualCount = freshnessEvidence.reduce((total, item) => total + item.manual_check_notes.length, 0);

  return (
    <div className="evidenceStack impactEvidenceStack" aria-label="Impact evidence source cards">
      <article className="evidenceCard query-evidence">
        <div className="evidenceCardHeader">
          <div>
            <span className="eyebrow">Query exposure evidence</span>
            <h3>BW Query evidence inside Impact Review</h3>
          </div>
          <span className="evidenceStatus">No BW query execution · No data preview</span>
        </div>
        <div className="metaGrid evidenceMetricGrid">
          <Metric label="Queries" value={String(queryEvidence.length)} />
          <Metric label="Matched findings" value={coverageValue(review, 'query_matched_finding_count')} />
          <Metric label="Variables" value={String(variableCount)} />
          <Metric label="CKF/RKF" value={String(keyFigureCount)} />
        </div>
        {queryEvidence.length > 0 ? (
          <div className="evidenceRows">
            {queryEvidence.map((item) => (
              <div key={item.query_id} className="evidenceRow">
                <strong>{item.query_id}</strong>
                <small>{item.description || 'Snapshot parser evidence'}</small>
                <EvidenceChipList label="Providers" items={item.provider_object_ids} />
                <EvidenceChipList label="Variables" items={item.variable_names} empty="no variables" />
                <EvidenceChipList label="Key figures" items={[...item.calculated_key_figure_names, ...item.restricted_key_figure_names]} empty="no CKF/RKF" />
                {item.manual_check_notes.length > 0 ? <p>{item.manual_check_notes.join(' ')}</p> : null}
                <details className="advancedSection evidenceDrawer">
                  <summary>Advanced · Query Analysis parser output</summary>
                  <pre>{JSON.stringify(item, null, 2)}</pre>
                </details>
              </div>
            ))}
          </div>
        ) : (
          <div className="emptyState">Impact 실행 후 impacted QUERY 또는 explicit query name이 있으면 Query evidence가 여기에 표시됩니다.</div>
        )}
      </article>

      <article className="evidenceCard sql-evidence">
        <div className="evidenceCardHeader">
          <div>
            <span className="eyebrow">SQL / Native SQL reference evidence</span>
            <h3>SQL reference evidence inside Impact Review</h3>
          </div>
          <span className="evidenceStatus">Parse only · DB execution disabled</span>
        </div>
        <div className="metaGrid evidenceMetricGrid">
          <Metric label="Views" value={String(sqlEvidence.length)} />
          <Metric label="Matched findings" value={coverageValue(review, 'sql_matched_finding_count')} />
          <Metric label="Objects" value={String(referencedObjectCount)} />
          <Metric label="Columns" value={String(referencedColumnCount)} />
        </div>
        {sqlEvidence.length > 0 ? (
          <div className="evidenceRows">
            {sqlEvidence.map((item) => (
              <div key={item.view_id} className="evidenceRow">
                <strong>{item.view_id}</strong>
                <small>{item.parser} · confidence {item.confidence}</small>
                <EvidenceChipList label="Referenced objects" items={item.referenced_object_ids} />
                <EvidenceChipList label="Referenced columns" items={item.referenced_column_names.slice(0, 12)} empty="no parsed columns" />
                {item.manual_check_notes.length > 0 ? <p>{item.manual_check_notes.join(' ')}</p> : null}
                <details className="advancedSection evidenceDrawer">
                  <summary>Advanced · SQL Analysis parser output</summary>
                  <pre>{JSON.stringify(item, null, 2)}</pre>
                </details>
              </div>
            ))}
          </div>
        ) : (
          <div className="emptyState">SQL evidence는 좌측 advanced source에서 명시적으로 켠 경우에만 parse-only로 포함됩니다.</div>
        )}
      </article>

      <article className="evidenceCard freshness-evidence">
        <div className="evidenceCardHeader">
          <div>
            <span className="eyebrow">Freshness evidence</span>
            <h3>Recent load / request metadata evidence</h3>
          </div>
          <span className="evidenceStatus">Read-only metadata · No data preview</span>
        </div>
        <div className="metaGrid evidenceMetricGrid">
          <Metric label="Objects" value={String(freshnessEvidence.length)} />
          <Metric label="Manual notes" value={String(freshnessManualCount)} />
          <Metric label="Included" value={review ? String(Boolean(review.coverage_summary.freshness_evidence_count)) : 'false'} />
          <Metric label="Execution" value="blocked" />
        </div>
        {freshnessEvidence.length > 0 ? (
          <div className="evidenceRows">
            {freshnessEvidence.map((item) => (
              <div key={item.object_id} className="evidenceRow">
                <strong>{item.object_id}</strong>
                <small>{item.object_type ?? 'UNKNOWN'} · requests {item.request_count} · status {item.latest_status ?? 'n/a'}</small>
                <EvidenceChipList label="Latest TSN" items={item.latest_request_tsn ? [item.latest_request_tsn] : []} empty="no request TSN" />
                {item.latest_timestamp ? <p>Latest timestamp: {item.latest_timestamp}</p> : null}
                {item.manual_check_notes.length > 0 ? <p>{item.manual_check_notes.join(' ')}</p> : null}
              </div>
            ))}
          </div>
        ) : (
          <div className="emptyState">Impact Review includes freshness as read-only request metadata when available; no load or data preview is executed.</div>
        )}
      </article>
    </div>
  );
}

function ManualVerificationChecklist(props: { review: ImpactReviewResponse | null; impact: ImpactScenarioResponse | null }) {
  const gaps = props.review?.manual_verification_gaps ?? [];
  const manualFindings = props.review?.impact.findings.filter((finding) => finding.manual_verification)
    ?? props.impact?.affected_objects
      .filter((item) => item.manual_verification)
      .map((item) => ({
        id: item.object_id,
        impacted_object_id: item.object_id,
        impacted_object_type: item.object_type,
        severity: item.severity,
        reason: item.reason,
        evidence_node_ids: item.evidence_node_ids,
        evidence_edge_ids: item.evidence_edge_ids,
      }))
    ?? [];

  return (
    <article className="evidenceCard manual-checklist-card" aria-label="Manual verification checklist">
      <div className="evidenceCardHeader">
        <div>
          <span className="eyebrow">Manual-check checklist</span>
          <h3>Verification gaps and human-only follow-up</h3>
        </div>
        <span className="evidenceStatus">{gaps.length} deterministic gaps</span>
      </div>
      {gaps.length > 0 ? (
        <ul className="manualGapList">
          {gaps.map((gap) => (
            <li key={gap.id}>
              <strong>{gap.source.toUpperCase()}{gap.object_id ? ` · ${gap.object_id}` : ''}</strong>
              <span>{gap.reason}</span>
              {gap.evidence_ids.length > 0 ? <small>Evidence IDs: {gap.evidence_ids.join(', ')}</small> : null}
            </li>
          ))}
        </ul>
      ) : manualFindings.length > 0 ? (
        <ul className="manualGapList">
          {manualFindings.map((finding) => (
            <li key={finding.id}>
              <strong>{finding.severity} · {finding.impacted_object_type} · {finding.impacted_object_id}</strong>
              <span>{finding.reason}</span>
              <small>Manual BWMT check required. Evidence IDs: {[...finding.evidence_node_ids, ...finding.evidence_edge_ids].join(', ') || '—'}</small>
            </li>
          ))}
        </ul>
      ) : (
        <div className="emptyState">
          No deterministic manual gaps yet. Before transport approval, manually verify BWMT activation, DTP/process-chain load freshness, and query exposure outside this read-only metadata workspace.
        </div>
      )}
      <p className="policyNote">Human-only checks remain outside the app: read-only metadata, no BW query execution, no data preview, parse-only SQL.</p>
    </article>
  );
}

function AuthorityCallout(props: { review: ImpactReviewResponse | null }) {
  return (
    <aside className="authorityCallout">
      <strong>Deterministic authority boundary</strong>
      <p>
        impact.py remains the final authority for severity, confidence, affected objects, and manual verification.
      </p>
      <small>
        {props.review
          ? `${props.review.final_authority} · deterministic=${String(props.review.deterministic)} · read_only=${String(props.review.read_only)} · execution_blocked=${String(props.review.execution_blocked)}`
          : 'Impact Review evidence pack 대기 중 · LLM 권위 없음 · execution disabled'}
      </small>
    </aside>
  );
}

function EvidenceChipList(props: { label: string; items: string[]; empty?: string }) {
  return (
    <div className="evidenceChips">
      <span>{props.label}</span>
      {props.items.length > 0
        ? props.items.map((item) => <code key={item}>{item}</code>)
        : <em>{props.empty ?? '—'}</em>}
    </div>
  );
}

function CitationChipList(props: { citationIds: string[] }) {
  return (
    <div className="citationChipList" aria-label="citation ids">
      {props.citationIds.length > 0
        ? props.citationIds.map((id) => <code key={id} className="citationChip">{id}</code>)
        : <em>No citations</em>}
    </div>
  );
}

function provenanceLabel(kind: AgenticReviewRun['cards'][number]['kind']): string {
  if (kind === 'deterministic_finding') return 'Deterministic finding';
  if (kind === 'llm_proposed_concern') return 'LLM proposed concern';
  return 'Manual verification required';
}

async function copyCabSummary(text: string): Promise<void> {
  if (!text.trim() || !navigator.clipboard?.writeText) return;
  try {
    await navigator.clipboard.writeText(text);
  } catch (_err) {
    // Clipboard is best-effort in local builds and can be unavailable in tests or HTTP contexts.
  }
}

function coverageValue(review: ImpactReviewResponse | null, key: string): string {
  const value = review?.coverage_summary[key];
  return typeof value === 'number' && Number.isFinite(value) ? String(value) : '0';
}


function QueryTab(props: {
  selectedSnapshot: SnapshotSummary | null;
  selectedObject: CatalogObject | null;
  queryName: string;
  setQueryName: (value: string) => void;
  queryAnalysis: QueryAnalysisResponse | null;
  onRun: () => void;
  busy: boolean;
}) {
  const result = props.queryAnalysis?.result ?? null;
  const variables = Array.isArray(result?.variables) ? result.variables : [];
  const calculated = Array.isArray(result?.calculated_key_figures) ? result.calculated_key_figures : [];
  const restricted = Array.isArray(result?.restricted_key_figures) ? result.restricted_key_figures : [];
  const providers = Array.isArray(result?.providers) ? result.providers : [];
  const fields = Array.isArray(result?.fields) ? result.fields : [];
  return (
    <div className="queryLayout">
      <section className="controlCard">
        <span className="eyebrow">Query Analysis</span>
        <h1>Query XML analysis</h1>
        <p className="tabPurpose">Snapshot-driven, read-only parser output. No live query execution, preview, or data rows.</p>
        <div className="scenarioObject">분석 기준: <strong>{props.selectedSnapshot ? compactDate(props.selectedSnapshot.created_at) : '없음'}</strong></div>
        <label>Query technical name
          <input
            value={props.queryName}
            placeholder={props.selectedObject?.type === 'QUERY' ? props.selectedObject.id : 'ZQ_SALES_MARGIN'}
            onChange={(event) => props.setQueryName(event.target.value)}
            onKeyDown={(event) => { if (event.key === 'Enter') props.onRun(); }}
          />
        </label>
        <button className="primaryButton wide" onClick={props.onRun} disabled={!props.selectedSnapshot || props.busy}>
          Query Analysis 실행
        </button>
        <p className="policyNote">Live capture가 없으면 snapshot fixture/catalog에 저장된 query XML 분석 결과만 표시합니다.</p>
      </section>
      <section className="resultCard queryResultCard">
        <span className="eyebrow">Result</span>
        {props.queryAnalysis ? (
          <>
            <div className="metaGrid">
              <Metric label="Read-only" value={props.queryAnalysis.read_only ? 'Yes' : 'No'} />
              <Metric label="Variables" value={String(variables.length)} />
              <Metric label="CKF" value={String(calculated.length)} />
              <Metric label="RKF" value={String(restricted.length)} />
              <Metric label="Providers" value={String(providers.length)} />
              <Metric label="Fields" value={String(fields.length)} />
            </div>
            <pre className="jsonPreview">{JSON.stringify(props.queryAnalysis.result, null, 2)}</pre>
          </>
        ) : (
          <div className="emptyState">Query name을 입력하거나 QUERY object를 선택한 뒤 분석을 실행하세요.</div>
        )}
      </section>
    </div>
  );
}

function SqlTab(props: {
  runtime: RuntimeConfigResponse | null;
  viewId: string;
  setViewId: (value: string) => void;
  sqlFile: string;
  setSqlFile: (value: string) => void;
  sqlText: string;
  setSqlText: (value: string) => void;
  question: string;
  setQuestion: (value: string) => void;
  explain: SqlExplainResponse | null;
  draft: SqlDraftResponse | null;
  onExplain: () => void;
  onDraft: () => void;
  busy: string;
}) {
  return (
    <div className="sqlLayout">
      <section className="controlCard">
        <span className="eyebrow">SQL Analysis</span>
        <h1>Reference extraction</h1>
        <p className="tabPurpose">Native SQL View가 참조하는 객체·컬럼을 증거로 추출합니다. DB 실행은 하지 않습니다.</p>
        <p className="warningText">Parse only · DB execution disabled</p>
        <label>View ID
          <input value={props.viewId} onChange={(event) => props.setViewId(event.target.value)} />
        </label>
        <label>SQL text (붙여넣기)
          <textarea
            rows={6}
            placeholder="Native SQL View 정의를 붙여넣으세요. 입력하면 SQL file 대신 분석합니다."
            value={props.sqlText}
            onChange={(event) => props.setSqlText(event.target.value)}
          />
        </label>
        <label>SQL file (project 내 경로 · SQL text 비어있을 때 사용)
          <input value={props.sqlFile} onChange={(event) => props.setSqlFile(event.target.value)} />
        </label>
        <button className="primaryButton wide" onClick={props.onExplain} disabled={props.busy === 'sql-explain'}>
          SQL references 분석
        </button>
        <details className="advancedSection sqlAdvanced">
          <summary>Advanced · LLM draft</summary>
          <label>Prompt
            <textarea value={props.question} onChange={(event) => props.setQuestion(event.target.value)} rows={4} />
          </label>
          <button className="secondaryButton wide" onClick={props.onDraft} disabled={props.busy === 'sql-draft'}>
            Draft 생성
          </button>
          <p className="mutedSmall">
            LLM: {props.runtime?.llm.configured ? `${props.runtime.llm.source} · ${props.runtime.llm.model}` : 'disabled'}
          </p>
        </details>
      </section>
      <section className="resultCard">
        <span className="eyebrow">Evidence</span>
        {props.explain ? (
          <div className="sqlEvidence">
            <h2>{props.explain.result.view.id}</h2>
            <p>{props.explain.execution_disabled_warning}</p>
            <div className="metaGrid">
              <Metric label="Parser" value={props.explain.result.parser} />
              <Metric label="Objects" value={String(props.explain.referenced_objects.length)} />
              <Metric label="Fields" value={String(props.explain.referenced_fields.length)} />
            </div>
            <div className="sqlAnalysisGrid">
              <div>
                <h3>Referenced objects</h3>
                <ul className="plainList">
                  {props.explain.referenced_objects.map((objectId) => <li key={objectId}><code>{objectId}</code></li>)}
                  {props.explain.referenced_objects.length === 0 ? <li>—</li> : null}
                </ul>
              </div>
              <div>
                <h3>Referenced fields</h3>
                <ul className="plainList">
                  {props.explain.referenced_fields.slice(0, 18).map((field) => (
                    <li key={field.id}><code>{field.table_alias ? `${field.table_alias}.` : ''}{field.column_name}</code></li>
                  ))}
                  {props.explain.referenced_fields.length === 0 ? <li>—</li> : null}
                </ul>
              </div>
            </div>
            {GLOSSARY_VISIBLE ? <GlossaryList terms={props.explain.glossary_terms} title="Glossary" emptyText="Glossary 용어 없음" /> : null}
            <pre>{JSON.stringify(props.explain.result.reference_edges, null, 2)}</pre>
          </div>
        ) : <div className="emptyState">Native SQL View 정의를 붙여넣으면 참조 객체·컬럼을 결정적으로 추출합니다. DB 실행은 하지 않습니다.</div>}
        {props.draft ? (
          <div className="draftBox">
            <h3>Draft 상태: {props.draft.status}</h3>
            <pre>{props.draft.draft_sql || props.draft.message}</pre>
            <small>Citations: {props.draft.citations.join(', ') || 'none'}</small>
          </div>
        ) : null}
      </section>
    </div>
  );
}

function GlossaryTab(props: {
  selectedSnapshot: SnapshotSummary | null;
  query: string;
  setQuery: (value: string) => void;
  terms: GlossaryTerm[];
  aggregate: GlossaryAggregateResponse | null;
  onSearch: () => void;
  onLifecycle: (termId: string, lifecycle: 'candidate' | 'confirmed' | 'rejected') => void;
  onSelectObject: (objectId: string) => void;
  onAddTarget: (objectId: string) => void;
  busy: boolean;
}) {
  const [sourceFilter, setSourceFilter] = useState('');
  const [candidateFilter, setCandidateFilter] = useState<'all' | 'candidate' | 'confirmed'>('all');
  const sources = useMemo(
    () => Array.from(new Set(props.terms.map((term) => term.source).filter(Boolean))).sort(),
    [props.terms],
  );
  const filteredTerms = useMemo(
    () => props.terms.filter((term) => {
      if (sourceFilter && term.source !== sourceFilter) return false;
      if (candidateFilter === 'candidate' && !term.candidate) return false;
      if (candidateFilter === 'confirmed' && term.candidate) return false;
      return true;
    }),
    [candidateFilter, props.terms, sourceFilter],
  );
  const canSearch = Boolean(props.selectedSnapshot);
  return (
    <div className="glossaryLayout">
      <section className="controlCard">
        <span className="eyebrow">Glossary</span>
        <h1>업무 용어 검색</h1>
        <p className="tabPurpose">기술 ID와 업무 용어를 연결합니다. 객체명을 몰라도 용어로 찾고 분석 대상에 추가할 수 있습니다.</p>
        <div className="scenarioObject">
          분석 기준: <strong>{props.selectedSnapshot ? compactDate(props.selectedSnapshot.created_at) : '없음'}</strong>
        </div>
        <label>Search term
          <input
            value={props.query}
            placeholder="매출, AMOUNT, margin, source table..."
            disabled={!canSearch}
            onChange={(event) => props.setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') props.onSearch();
            }}
          />
        </label>
        <button className="primaryButton wide" onClick={props.onSearch} disabled={!canSearch || props.busy}>
          Glossary 검색
        </button>
        <div className="compactForm">
          <label>Source filter
            <select value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)}>
              <option value="">All sources</option>
              {sources.map((source) => <option key={source} value={source}>{source}</option>)}
            </select>
          </label>
          <label>Candidate filter
            <select value={candidateFilter} onChange={(event) => setCandidateFilter(event.target.value as 'all' | 'candidate' | 'confirmed')}>
              <option value="all">All terms</option>
              <option value="candidate">Candidates only</option>
              <option value="confirmed">Confirmed only</option>
            </select>
          </label>
        </div>
        <div className="metaGrid glossaryMetrics">
          <Metric label="Terms" value={String(props.aggregate?.total ?? props.terms.length)} />
          <Metric label="Confirmed" value={String(props.aggregate?.confirmed ?? props.terms.filter((term) => term.lifecycle === 'confirmed').length)} />
          <Metric label="Visible" value={String(filteredTerms.length)} />
          <Metric label="Sources" value={String(sources.length)} />
        </div>
      </section>
      <section className="resultCard">
        <span className="eyebrow">Term map</span>
        {canSearch ? (
          filteredTerms.length > 0 ? (
            <div className="glossaryResults">
              {filteredTerms.map((term) => {
                const qualifier = glossaryQualifier(term);
                return (
                  <article key={term.id} className={`glossaryResult ${term.candidate ? 'candidate' : 'confirmed'}`}>
                    <div className="glossaryResultMain">
                      <span>{term.source}{term.candidate ? ' · candidate' : ''}</span>
                      <strong>{term.term}</strong>
                      <small>
                        {term.object_id ?? 'object 연결 없음'}{term.field_name ? ` · ${term.field_name}` : ''}{qualifier ? ` · ${qualifier}` : ''}
                      </small>
                    </div>
                    {term.object_id ? (
                      <div className="glossaryActions">
                        <button className="secondaryButton" onClick={() => props.onSelectObject(term.object_id!)}>
                          Lineage에서 보기
                        </button>
                        <button className="miniPrimaryButton" onClick={() => props.onAddTarget(term.object_id!)}>
                          대상 추가
                        </button>
                      </div>
                    ) : null}
                    <div className="glossaryActions lifecycleActions" aria-label="local glossary lifecycle">
                      <button className="ghostButton" onClick={() => props.onLifecycle(term.id, 'confirmed')}>Confirm</button>
                      <button className="ghostButton" onClick={() => props.onLifecycle(term.id, 'rejected')}>Reject</button>
                    </div>
                    <small>Evidence IDs: {term.evidence_ids.join(', ') || '—'}</small>
                  </article>
                );
              })}
            </div>
          ) : (
            <div className="emptyState">검색 결과가 없습니다. 다른 업무 용어 또는 object/field 일부를 입력하세요.</div>
          )
        ) : (
          <div className="emptyState">먼저 Fixture demo 또는 BW에서 가져오기로 분석 기준을 만든 뒤 Glossary를 검색하세요.</div>
        )}
      </section>
    </div>
  );
}

function RepositoryPicker(props: {
  path: string;
  nodes: RepositoryNode[];
  source: 'live' | 'cache' | 'empty';
  actionRequired: string | null;
  connectionReady: boolean;
  busy: string;
  onOpenPath: (path: string) => void;
  onRefresh: () => void;
  onSelect: (node: RepositoryNode) => void;
}) {
  const canGoUp = props.path !== '/';
  const parentPath = canGoUp ? props.path.split('/').slice(0, -1).join('/') || '/' : '/';
  return (
    <section className="repositoryPicker" aria-label="BW repository object picker">
      <div className="repoHeader">
        <div>
          <strong>Repository</strong>
          <span>{props.source} · {props.path}</span>
        </div>
        <button className="iconButton" onClick={props.onRefresh} disabled={!props.connectionReady || props.busy === 'repository-refresh'} title="Live repository refresh">
          ⟳
        </button>
      </div>
      <div className="repoPathRow">
        <button className="ghostButton" onClick={() => props.onOpenPath(parentPath)} disabled={!canGoUp}>Up</button>
        <button className="ghostButton" onClick={() => props.onOpenPath('/')}>Root</button>
      </div>
      {props.nodes.length > 0 ? (
        <div className="repoNodeList">
          {props.nodes.slice(0, 40).map((node) => (
            <button key={node.id} className="repoNode" onClick={() => props.onSelect(node)} title={node.description || node.name}>
              <span className="repoType">{node.object_type}</span>
              <strong>{node.name}</strong>
              <small>{node.description || node.path}</small>
              {node.has_children ? <b>›</b> : null}
            </button>
          ))}
        </div>
      ) : (
        <p className="repoEmpty">{repositoryActionText(props.actionRequired)}</p>
      )}
    </section>
  );
}

function repositoryActionText(actionRequired: string | null): string {
  if (!actionRequired) return 'Repository cache 없음';
  if (actionRequired.includes('confirm_read_only')) return 'Test connection 후 repository refresh를 실행하세요.';
  return actionRequired;
}

function GlossaryList(props: { terms: GlossaryTerm[]; title: string; emptyText: string }) {
  return (
    <div className="glossaryBox">
      <h3>{props.title}</h3>
      {props.terms.length > 0 ? (
        <div className="glossaryTerms">
          {props.terms.slice(0, 10).map((term) => {
            const qualifier = glossaryQualifier(term);
            return (
              <span key={term.id} title={term.evidence_ids.join(', ')}>
                {term.term}{qualifier ? <small>{qualifier}</small> : null}
              </span>
            );
          })}
        </div>
      ) : <p>{props.emptyText}</p>}
    </div>
  );
}

function glossaryQualifier(term: GlossaryTerm): string {
  const candidates = [term.field_name, term.object_type, term.source];
  const normalizedTerm = term.term.trim().toLowerCase();
  const value = candidates.find((candidate) => candidate && candidate.trim().toLowerCase() !== normalizedTerm);
  return value ?? '';
}

function TermsOverview(props: { terms: GlossaryTerm[]; onOpen: () => void }) {
  const visibleTerms = props.terms.slice(0, 8);
  return (
    <section className="termsOverview" aria-label="snapshot glossary terms">
      <div className="termsOverviewHeader">
        <strong>Glossary</strong>
        <button className="ghostButton" onClick={props.onOpen}>열기</button>
        <span>{props.terms.length}</span>
      </div>
      {visibleTerms.length > 0 ? (
        <div className="termsOverviewList">
          {visibleTerms.map((term) => (
            <span key={term.id} title={`${term.term} · ${term.source}`}>
              {term.term}
            </span>
          ))}
        </div>
      ) : (
        <small>분석 기준을 가져오면 metadata/SQL에서 추출한 Glossary 용어가 표시됩니다.</small>
      )}
    </section>
  );
}

function GuidedTourPanel(props: {
  title: string;
  response: LineageTourResponse | ImpactTourResponse | null;
  steps: NormalizedTourStep[];
  currentIndex: number;
  onStepIndex: (value: number) => void;
  onRun: () => void;
  busy: boolean;
}) {
  const current = props.steps[props.currentIndex] ?? null;
  const isDisabled = props.response?.status === 'disabled';
  return (
    <section className="guidedTourCard" aria-label={props.title}>
      <div className="guidedTourTitle">
        <div>
          <span className="eyebrow">{props.title}</span>
          <h3>{current ? `Step ${current.index}/${current.total} · ${current.title}` : 'No tour loaded'}</h3>
        </div>
        {current?.evidenceIds[0] ? <span className="evidencePill">{current.evidenceIds[0]}</span> : null}
      </div>
      {props.response?.summary ? <p className="tourSummary">{props.response.summary}</p> : null}
      {current ? (
        <>
          <p>{current.description || props.response?.message || 'Evidence panel step has no description.'}</p>
          <div className="tourEvidenceList" aria-label="evidence panel evidence ids">
            {current.evidenceIds.length > 0 ? current.evidenceIds.slice(0, 6).map((id) => <code key={id}>{id}</code>) : <span>No evidence IDs returned</span>}
          </div>
          <div className="tourControls">
            <button className="secondaryButton" onClick={() => props.onStepIndex(props.currentIndex - 1)} disabled={!current.canPrevious}>Previous</button>
            <button className="primaryButton" onClick={() => props.onStepIndex(props.currentIndex + 1)} disabled={!current.canNext}>Next</button>
          </div>
        </>
      ) : (
        <>
          <p>{isDisabled ? props.response?.message : 'Generate citation-bound evidence panel content for the selected bounded analysis.'}</p>
          {props.response?.citations.length ? <small>Citations: {props.response.citations.join(', ')}</small> : null}
          <button className="secondaryButton wide" onClick={props.onRun} disabled={props.busy}>{props.busy ? 'Generating…' : 'Generate evidence panel'}</button>
        </>
      )}
    </section>
  );
}

function SafetyCopy() {
  return (
    <section className="safetyCopy" aria-label="read-only safety boundaries">
      <strong>Safety copy · read-only local-first</strong>
      <ul>
        <li>Read-only metadata analysis only; local-first snapshots remain the evidence basis.</li>
        <li>No SAP write, run, activate, or transport actions.</li>
        <li>No BW query execution and no data preview.</li>
        <li>No data rows or data-preview UI; metadata, request timestamps, and citations only.</li>
        <li>LLM answers are evidence-bound and never become final authority.</li>
        <li>No secrets, credentials, hostnames, API keys, cookies, or raw connection details are displayed here.</li>
      </ul>
    </section>
  );
}

function FreshnessBadge(props: { display: FreshnessDisplay; compact?: boolean }) {
  return (
    <span className={`freshnessBadge ${props.display.state} ${props.compact ? 'compact' : ''}`} title={props.display.timestamp ?? props.display.label}>
      <span aria-hidden="true" />{props.display.label}
    </span>
  );
}

function FreshnessCount(props: { label: string; count: number; state: FreshnessDisplay['state'] }) {
  return <span className={`freshnessCount ${props.state}`}><b>{props.label}</b><em>{props.count}</em></span>;
}

function lineageHealthItem(
  label: 'Dataflow',
  selectedObject: CatalogObject | null,
  lineage: LineageResponse | null,
): { label: string; value: string; tone: 'ok' | 'warn' | 'info' | 'neutral' } {
  if (!selectedObject) return { label, value: 'Select object', tone: 'neutral' };
  if (!lineage) return { label, value: 'Not run', tone: 'info' };
  if (lineage.edges.length > 0) return { label, value: `${lineage.edges.length} edges`, tone: lineage.truncated ? 'warn' : 'ok' };
  return { label, value: lineage.nodes.length > 0 ? 'Node only' : 'No evidence', tone: 'warn' };
}

function whereUsedHealthItem(
  label: 'Where-used',
  selectedObject: CatalogObject | null,
  lineage: LineageResponse | null,
  direction: Direction,
): { label: string; value: string; tone: 'ok' | 'warn' | 'info' | 'neutral' } {
  if (!selectedObject) return { label, value: 'Select object', tone: 'neutral' };
  if (!lineage) return { label, value: 'Not run', tone: 'info' };
  const evidenceDirection = lineage.direction ?? direction;
  if (evidenceDirection === 'downstream') return { label, value: 'Run Upstream/Both', tone: 'info' };
  if (lineage.edges.length > 0) return { label, value: `${lineage.edges.length} refs`, tone: lineage.truncated ? 'warn' : 'ok' };
  return { label, value: 'No refs', tone: 'warn' };
}

function objectDetailHealthItem(
  label: 'Object detail',
  selectedObject: CatalogObject | null,
  objectDetail: CatalogObjectDetail | null,
): { label: string; value: string; tone: 'ok' | 'warn' | 'info' | 'neutral' } {
  if (!selectedObject) return { label, value: 'Select object', tone: 'neutral' };
  if (!objectDetail) return { label, value: 'Loading', tone: 'info' };
  if (objectDetail.evidence_ids.length > 0) return { label, value: `${objectDetail.evidence_ids.length} ids`, tone: 'ok' };
  return { label, value: 'Metadata only', tone: 'warn' };
}

function freshnessHealthItem(
  label: 'Freshness',
  display: FreshnessDisplay,
  objectFreshness: RequestFreshnessResponse | null,
): { label: string; value: string; tone: 'ok' | 'warn' | 'info' | 'neutral' } {
  const toneByState: Record<FreshnessDisplay['state'], 'ok' | 'warn' | 'info' | 'neutral'> = {
    fresh: 'ok',
    stale: 'warn',
    none: 'info',
    unknown: objectFreshness ? 'info' : 'neutral',
  };
  return { label, value: display.label, tone: toneByState[display.state] };
}

function summarizeFreshness(
  nodes: LineageResponse['nodes'],
  selectedId: string | null,
  selectedFreshness: RequestFreshnessResponse | null,
): Record<FreshnessDisplay['state'], number> {
  return nodes.reduce<Record<FreshnessDisplay['state'], number>>(
    (counts, node) => {
      const state = freshnessForLineageNode(node, selectedId, selectedFreshness).state;
      counts[state] += 1;
      return counts;
    },
    { fresh: 0, stale: 0, none: 0, unknown: 0 },
  );
}

function freshnessForLineageNode(
  node: LineageResponse['nodes'][number],
  selectedId: string | null,
  selectedFreshness: RequestFreshnessResponse | null,
): FreshnessDisplay {
  if (node.id === selectedId && selectedFreshness) return classifyFreshness(selectedFreshness);
  return classifyFreshness(freshnessFromMetadata(node.metadata));
}

function isFreshnessMissingError(err: unknown): boolean {
  const text = errorText(err).toLowerCase();
  return text.includes('404') || text.includes('request_freshness not found');
}

function LineageGraph(props: {
  lineage: LineageResponse | null;
  onSelect: (id: string) => void;
  selectedId: string | null;
  selectedFreshness: RequestFreshnessResponse | null;
}) {
  if (!props.lineage) {
    return <div className="emptyState graphEmpty">왼쪽에서 객체를 찾고 선택한 뒤 Lineage를 실행하세요. 객체명을 몰라도 Find in BW로 후보를 검색할 수 있습니다.</div>;
  }
  const groups = groupNodesByDisplayLayer(props.lineage.nodes);
  const visibleLayers = DISPLAY_LAYER_ORDER
    .filter((layer) => layer !== 'Unknown' || groups.some((group) => group.layer === 'Unknown'))
    .sort(compareDisplayLayers);
  const positions = layoutLayerPositions(props.lineage, visibleLayers);
  const maxRows = Math.max(1, ...visibleLayers.map((layer) => groups.find((group) => group.layer === layer)?.nodes.length ?? 0));
  const width = Math.max(940, visibleLayers.length * 214 + 84);
  const height = Math.max(390, maxRows * 92 + 128);
  return (
    <div className="graphSurface sliceGGraphSurface">
      <div className="graphToolbar">
        <div>
          <strong>Layer-lane lineage graph</strong>
          <span>Source → Transform → Model → Semantic → Runtime · {props.lineage.nodes.length} nodes · {props.lineage.edges.length} edges</span>
        </div>
        <div className="legendList freshnessLegend">
          <FreshnessBadge display={{ state: 'fresh', label: 'Fresh < 2h' }} compact />
          <FreshnessBadge display={{ state: 'stale', label: 'Stale 3d' }} compact />
          <FreshnessBadge display={{ state: 'none', label: 'No requests' }} compact />
          <FreshnessBadge display={{ state: 'unknown', label: 'Unknown' }} compact />
        </div>
      </div>
      {props.lineage.truncated ? (
        <div className="graphWarning">
          일부 neighbor가 cap/depth 제한으로 생략되었습니다. omitted={props.lineage.truncation.omitted_neighbor_total}
        </div>
      ) : null}
      <div className="graphCanvas">
        <svg className="lineageSvg laneLineageSvg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="SAP BW layer-lane lineage graph">
          <defs>
            <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" />
            </marker>
          </defs>
          {visibleLayers.map((layer, index) => {
            const x = 38 + index * 214;
            const count = groups.find((group) => group.layer === layer)?.nodes.length ?? 0;
            return (
              <g key={layer} className={`layerLaneSvg layer-${layer.toLowerCase()}`}>
                <rect x={x} y="52" width="194" height={height - 86} rx="12" />
                <text x={x + 14} y="34" className="layerLabelSvg">{layer}</text>
                <text x={x + 168} y="34" className="layerCountSvg">{count}</text>
              </g>
            );
          })}
          {props.lineage.edges.map((edge) => {
            const source = positions[edge.source];
            const target = positions[edge.target];
            if (!source || !target) return null;
            const goesRight = source.x <= target.x;
            const startX = source.x + (goesRight ? 90 : -90);
            const targetX = target.x + (goesRight ? -90 : 90);
            const curve = Math.max(54, Math.abs(targetX - startX) / 2);
            const c1 = goesRight ? startX + curve : startX - curve;
            const c2 = goesRight ? targetX - curve : targetX + curve;
            const labelX = (source.x + target.x) / 2;
            const labelY = (source.y + target.y) / 2 - 12;
            return (
              <g key={edge.id} className={`edgeGroup ${edgeTypeClass(edge.type)}`}>
                <path d={`M ${startX} ${source.y} C ${c1} ${source.y}, ${c2} ${target.y}, ${targetX} ${target.y}`} className="edgeLine" markerEnd="url(#arrow)" />
                <text x={labelX} y={labelY} className="edgeLabel">{shortLabel(edge.type, 18)}</text>
              </g>
            );
          })}
          {props.lineage.nodes.map((node) => {
            const point = positions[node.id];
            const omitted = props.lineage?.omitted_neighbor_counts[node.id] ?? 0;
            const freshness = freshnessForLineageNode(node, props.selectedId, props.selectedFreshness);
            const layer = inferDisplayLayer(node).label;
            if (!point) return null;
            return (
              <g
                key={node.id}
                transform={`translate(${point.x - 90}, ${point.y - 32})`}
                onClick={() => props.onSelect(node.id)}
                className={`nodeGroup ${nodeTypeClass(node.type)} ${node.id === props.lineage?.start_id ? 'start' : ''} ${node.id === props.selectedId ? 'selected' : ''}`}
              >
                <title>{node.id} · {node.type} · {layer} · {freshness.label}</title>
                <rect className="nodeCard laneNodeCard" width="180" height="64" rx="8" />
                <rect className="nodeAccent" width="3" height="64" rx="1.5" />
                <text x="12" y="18" className="nodeId">{shortLabel(node.id, 24)}</text>
                <text x="12" y="34" className="nodeType">{shortLabel(`${node.type} · ${layer}`, 24)}</text>
                <text x="12" y="52" className={`nodeFreshnessText freshness-${freshness.state}`}>{freshness.label}</text>
                {omitted > 0 ? <text x="142" y="52" className="nodeBadge">+{omitted}</text> : null}
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}

function layoutLayerPositions(
  lineage: LineageResponse,
  layers: DisplayLayer[],
): Record<string, { x: number; y: number }> {
  const byLayer = new Map<DisplayLayer, LineageResponse['nodes']>();
  layers.forEach((layer) => byLayer.set(layer, []));
  lineage.nodes.forEach((node) => {
    const layer = inferDisplayLayer(node).layer;
    byLayer.set(layer, [...(byLayer.get(layer) ?? []), node]);
  });

  const positions: Record<string, { x: number; y: number }> = {};
  layers.forEach((layer, layerIndex) => {
    const nodes = [...(byLayer.get(layer) ?? [])].sort((left, right) => {
      const leftLevel = lineage.levels[left.id] ?? 0;
      const rightLevel = lineage.levels[right.id] ?? 0;
      if (leftLevel !== rightLevel) return leftLevel - rightLevel;
      return left.id.localeCompare(right.id);
    });
    nodes.forEach((node, rowIndex) => {
      positions[node.id] = { x: 135 + layerIndex * 214, y: 96 + rowIndex * 92 };
    });
  });
  return positions;
}

function shortLabel(value: string, maxLength: number): string {
  return value.length <= maxLength ? value : `${value.slice(0, maxLength - 1)}…`;
}

function nodeTypeClass(type: string): string {
  return `type-${type.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
}

function edgeTypeClass(type: string): string {
  return `edge-${type.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
}

function NumberField(props: { label: string; value: number; min: number; max: number; onChange: (value: number) => void }) {
  return (
    <label>{props.label}
      <input
        type="number"
        min={props.min}
        max={props.max}
        value={props.value}
        onChange={(event) => {
          const next = Number(event.target.value);
          if (Number.isNaN(next)) return;
          props.onChange(Math.min(props.max, Math.max(props.min, Math.trunc(next))));
        }}
      />
    </label>
  );
}

function Metric(props: { label: string; value: string }) {
  return <div className="metric"><span>{props.label}</span><strong>{props.value}</strong></div>;
}

function StatusPill(props: { label: string; value: string; tone: 'ok' | 'warn' | 'info' | 'neutral' }) {
  return <span className={`statusPill ${props.tone}`}><small>{props.label}</small><strong>{props.value}</strong></span>;
}

function TabButton(props: { id: AppTab; active: AppTab; label: string; onClick: (tab: AppTab) => void }) {
  return <button className={props.id === props.active ? 'tabButton active' : 'tabButton'} onClick={() => props.onClick(props.id)}>{props.label}</button>;
}

function SetupStep(props: { index: number; title: string; status: string; done: boolean }) {
  return (
    <div className={props.done ? 'setupStep done' : 'setupStep'}>
      <span>{props.index}</span>
      <strong>{props.title}</strong>
      <small>{props.status}</small>
    </div>
  );
}

function CaptureOutcomeCard(props: { snapshot: SnapshotSummary | null; compact?: boolean }) {
  const capture = props.snapshot?.capture;
  if (!capture) {
    return (
      <section className={props.compact ? 'captureOutcome compact empty' : 'captureOutcome empty'}>
        <strong>Capture 없음</strong>
      </section>
    );
  }
  const hasFailures = capture.failed > 0;
  const className = props.compact
    ? `captureOutcome compact ${hasFailures ? 'warn' : 'ok'}`
    : `captureOutcome ${hasFailures ? 'warn' : 'ok'}`;
  return (
    <section className={className} aria-label="selected snapshot capture outcome">
      <div className="captureOutcomeHeader">
        <div>
          <span className="eyebrow">Capture</span>
          <strong>{hasFailures ? '부분 완료 · 확인 필요' : '완료'}</strong>
        </div>
        <div className="captureCounts">
          <Metric label="성공" value={String(capture.succeeded)} />
          <Metric label="실패" value={String(capture.failed)} />
        </div>
      </div>
      <ul className="captureOps" aria-label="capture operations">
        {capture.operations.map((op, index) => (
          <li key={`${op.name}-${op.label}-${index}`} className={op.ok ? 'opOk' : 'opError'}>
            <span>{op.name}</span>
            <small>{op.label}</small>
            {op.ok ? (
              <b>{op.payload_kind ?? 'ok'}{op.item_count != null ? ` · ${op.item_count}` : ''}</b>
            ) : (
              <code>{op.error ?? 'redacted error'}</code>
            )}
          </li>
        ))}
      </ul>
      <p className={hasFailures ? 'nextAction warn' : 'nextAction'}>{captureNextAction(capture)}</p>
    </section>
  );
}

function bwStatus(runtime: RuntimeConfigResponse | null): string {
  if (!runtime) return '확인 중';
  if (!runtime.bw.configured) return '미설정';
  if (runtime.connection_status === 'ok') return '연결 성공';
  if (runtime.connection_status === 'failed') return '실패';
  if (runtime.connection_status === 'stale') return '재테스트';
  return '미테스트';
}

function llmStatus(runtime: RuntimeConfigResponse | null): string {
  if (!runtime) return '확인 중';
  if (!runtime.llm.enabled || !runtime.llm.configured) return 'disabled';
  if (runtime.llm.source === 'env') return `.env 설정 (${runtime.llm.model ?? 'model set'})`;
  return `UI 설정 (${runtime.llm.model ?? 'model set'})`;
}

function compactDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function connectionTestBadge(test: LiveSmokeResult | null, status?: ConnectionStatus): string {
  if (test) {
    if (test.status === 'ok') return 'ok';
    if (test.status === 'partial') return 'warn';
    return 'error';
  }
  if (status === 'ok') return 'ok';
  if (status === 'failed') return 'error';
  if (status === 'untested' || status === 'stale') return 'warn';
  return 'idle';
}

function connectionTestLabel(test: LiveSmokeResult | null, runtime: RuntimeConfigResponse | null): string {
  if (!runtime?.bw.configured) return 'BW 설정 필요';
  if (test) {
    if (test.status === 'ok') return '연결 성공';
    if (test.status === 'partial') return '부분 성공';
    return '실패';
  }
  if (runtime.connection_status === 'ok') return '연결 성공';
  if (runtime.connection_status === 'failed') return '실패';
  if (runtime.connection_status === 'stale') return '재테스트 필요';
  return '미실행';
}

function captureNextAction(capture: LiveCaptureSummary): string {
  if (capture.failed === 0) {
    return 'Lineage/Impact를 실행하세요.';
  }
  if (capture.succeeded > 0) {
    return '실패 항목을 확인하세요.';
  }
  return '설정/연결 테스트를 확인하세요.';
}

function liveCaptureButtonTitle(
  connectionReady: boolean,
  liveCaptureTargetReady: boolean,
): string {
  if (!connectionReady) return '먼저 Test connection을 실행해 성공해야 합니다.';
  if (!liveCaptureTargetReady) return 'object name 또는 좁은 search term을 입력하세요.';
  return '';
}

function mergeSnapshotCapture(
  snapshots: SnapshotSummary[],
  capturedSnapshot?: SnapshotSummary,
): SnapshotSummary[] {
  if (!capturedSnapshot?.capture) return snapshots;
  let found = false;
  const merged = snapshots.map((snapshot) => {
    if (snapshot.id !== capturedSnapshot.id) return snapshot;
    found = true;
    return { ...snapshot, ...capturedSnapshot, capture: capturedSnapshot.capture };
  });
  return found ? merged : [capturedSnapshot, ...merged];
}
