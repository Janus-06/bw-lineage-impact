import { useEffect, useMemo, useState } from 'react';
import {
  captureFixtureSnapshot,
  captureLiveSnapshot,
  clearRuntimeConfig,
  draftSql,
  explainSql,
  getCaptureScope,
  getGlossary,
  getObject,
  getRepository,
  getRuntimeConfig,
  listObjects,
  listSnapshots,
  postConnectionTest,
  postImpactAdvice,
  postImpactScenario,
  postLineage,
  postLineageAdvice,
  putRuntimeConfig,
  refreshSnapshotFromBw,
  searchBwObjects,
  type AppTab,
  type BwSearchItem,
  type CaptureScopeItem,
  type CatalogObject,
  type CatalogObjectDetail,
  type ChangeType,
  type ConnectionStatus,
  type DataflowDirection,
  type Direction,
  type GlossaryTerm,
  type ImpactAdviceResponse,
  type ImpactScenarioResponse,
  type LineageAdviceResponse,
  type LineageResponse,
  type LiveCaptureSummary,
  type LiveSmokeResult,
  type RepositoryNode,
  type RuntimeConfigResponse,
  type SnapshotSummary,
  type SqlDraftResponse,
  type SqlExplainResponse,
} from './api';

const fixtureGraphPath = 'tests/fixtures/sample-graph.json';
const fixtureSqlPath = 'tests/fixtures/native_sql_view.sql';
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

function parseObjectNamesText(value: string): string[] {
  return value
    .split(/[\n,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function joinObjectNames(values: string[]): string {
  return Array.from(new Set(values)).join(', ');
}

function isBroadLiveSearchTerm(term: string): boolean {
  return term.trim().replace(/[*%]/g, '').trim() === '';
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

export default function App() {
  const [runtime, setRuntime] = useState<RuntimeConfigResponse | null>(null);
  const [snapshots, setSnapshots] = useState<SnapshotSummary[]>([]);
  const [selectedSnapshotId, setSelectedSnapshotId] = useState('');
  const [objects, setObjects] = useState<CatalogObject[]>([]);
  const [objectNextCursor, setObjectNextCursor] = useState<string | null>(null);
  const [selectedObjectId, setSelectedObjectId] = useState('');
  const [allowHiddenSelection, setAllowHiddenSelection] = useState(false);
  const [objectDetail, setObjectDetail] = useState<CatalogObjectDetail | null>(null);
  const [activeTab, setActiveTab] = useState<AppTab>('lineage');
  const [catalogQuery, setCatalogQuery] = useState('');
  const [objectType, setObjectType] = useState('');
  const [direction, setDirection] = useState<Direction>('downstream');
  const [depth, setDepth] = useState(1);
  const [nodeCap, setNodeCap] = useState(25);
  const [edgeCap, setEdgeCap] = useState(60);
  const [changeType, setChangeType] = useState<ChangeType>('field_removed');
  const [fieldName, setFieldName] = useState('AMOUNT');
  const [scenarioDescription, setScenarioDescription] = useState('컬럼/로직 변경 영향 검토');
  const [impactDepth, setImpactDepth] = useState(3);
  const [sqlViewId, setSqlViewId] = useState('ZSQL_VIEW');
  const [sqlFile, setSqlFile] = useState(fixtureSqlPath);
  const [sqlText, setSqlText] = useState('');
  const [sqlQuestion, setSqlQuestion] = useState('이 뷰의 주요 소스와 집계 로직을 설명하는 조회 초안');
  const [lineage, setLineage] = useState<LineageResponse | null>(null);
  const [lineageAdvice, setLineageAdvice] = useState<LineageAdviceResponse | null>(null);
  const [impact, setImpact] = useState<ImpactScenarioResponse | null>(null);
  const [impactAdvice, setImpactAdvice] = useState<ImpactAdviceResponse | null>(null);
  const [sqlExplain, setSqlExplain] = useState<SqlExplainResponse | null>(null);
  const [sqlDraft, setSqlDraft] = useState<SqlDraftResponse | null>(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
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
  const [glossaryQuery, setGlossaryQuery] = useState('');
  const [bwSearchTerm, setBwSearchTerm] = useState('');
  const [bwSearchType, setBwSearchType] = useState('');
  const [bwSearchResults, setBwSearchResults] = useState<BwSearchItem[]>([]);
  const [bwSearchTruncated, setBwSearchTruncated] = useState(false);

  const selectedSnapshot = snapshots.find((snapshot) => snapshot.id === selectedSnapshotId) ?? null;
  const selectedObject = objects.find((item) => item.id === selectedObjectId)
    ?? (allowHiddenSelection && objectDetail?.id === selectedObjectId ? objectDetail : null);
  const runtimeMissing = runtime ? !runtime.bw.configured : true;
  const connectionReady = runtime?.connection_status === 'ok' || connectionTestOk;
  const liveObjectNameTokens = useMemo(() => parseObjectNamesText(liveObjectNames), [liveObjectNames]);
  const liveSearchTermTokens = useMemo(() => parseObjectNamesText(liveSearchTerms), [liveSearchTerms]);
  const liveCaptureTargetReady = liveObjectNameTokens.length > 0 || liveSearchTermTokens.length > 0;
  const snapshotPickObjects = useMemo(() => objects.slice(0, 16), [objects]);
  const bwSavedForTesting = Boolean(runtime?.bw.configured && !bwSetupTouched);
  const bwTestedForCapture = Boolean(connectionReady && !bwSetupTouched);
  const refreshableAnalysisBasis = Boolean(
    selectedSnapshot?.mode === 'live-read-only' && captureScope.some((item) => item.role === 'selected'),
  );
  const selectedObjectGlossary = useMemo(
    () => objectDetail?.glossary_terms ?? glossaryTerms.filter((term) => term.object_id === selectedObjectId).slice(0, 12),
    [glossaryTerms, objectDetail, selectedObjectId],
  );

  useEffect(() => {
    void refreshAll();
  }, []);

  useEffect(() => {
    if (!selectedSnapshotId && snapshots.length > 0) {
      setSelectedSnapshotId(snapshots[0].id);
    }
  }, [selectedSnapshotId, snapshots]);

  useEffect(() => {
    if (selectedSnapshotId) {
      void refreshSnapshotContext(selectedSnapshotId);
    } else {
      setObjects([]);
      setObjectNextCursor(null);
      setSelectedObjectId('');
      setCaptureScope([]);
      setGlossaryTerms([]);
    }
  }, [selectedSnapshotId]);

  useEffect(() => {
    if (!selectedSnapshotId) return;
    const timer = window.setTimeout(() => void refreshObjects(selectedSnapshotId), 200);
    return () => window.clearTimeout(timer);
  }, [selectedSnapshotId, catalogQuery, objectType]);

  useEffect(() => {
    if (!selectedObjectId && objects.length > 0) {
      setSelectedObjectId(objects[0].id);
      setAllowHiddenSelection(false);
    }
    if (selectedObjectId && !objects.some((item) => item.id === selectedObjectId) && !allowHiddenSelection) {
      setSelectedObjectId(objects[0]?.id ?? '');
      setLineage(null);
      setLineageAdvice(null);
      setImpact(null);
      setImpactAdvice(null);
    }
  }, [allowHiddenSelection, objects, selectedObjectId]);

  useEffect(() => {
    if (selectedSnapshotId && selectedObjectId) {
      void loadObjectDetail(selectedSnapshotId, selectedObjectId);
    } else {
      setObjectDetail(null);
    }
  }, [selectedSnapshotId, selectedObjectId]);

  const latestSnapshotLabel = selectedSnapshot
    ? compactDate(selectedSnapshot.created_at)
    : '분석 기준 없음';

  const graphStats = useMemo(() => {
    if (!lineage) return 'Lineage 미실행';
    const capText = lineage.truncated ? `일부 생략 ${lineage.truncation.omitted_neighbor_total}` : '전체 표시';
    return `${lineage.nodes.length} nodes · ${lineage.edges.length} edges · ${capText}`;
  }, [lineage]);

  function clearAnalysisState() {
    setLineage(null);
    setLineageAdvice(null);
    setImpact(null);
    setImpactAdvice(null);
    setSqlExplain(null);
    setSqlDraft(null);
    setObjectDetail(null);
  }

  function chooseSnapshot(snapshotId: string) {
    setSelectedSnapshotId(snapshotId);
    setSelectedObjectId('');
    setObjectNextCursor(null);
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
    setBusy('catalog');
    try {
      const response = await listObjects(snapshotId, {
        q: catalogQuery.trim() || undefined,
        type: objectType || undefined,
        limit: 80,
        cursor,
      });
      setObjects((current) => (cursor ? [...current, ...response.items] : response.items));
      setObjectNextCursor(response.next_cursor);
      setError('');
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy('');
    }
  }

  async function refreshSnapshotContext(snapshotId: string) {
    try {
      const [scopeResponse, glossaryResponse] = await Promise.all([
        getCaptureScope(snapshotId),
        getGlossary(snapshotId),
      ]);
      setCaptureScope(scopeResponse.items);
      setGlossaryTerms(glossaryResponse.items);
    } catch (err) {
      setCaptureScope([]);
      setGlossaryTerms([]);
      setError(errorText(err));
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

  async function loadObjectDetail(snapshotId: string, objectId: string) {
    try {
      setObjectDetail(await getObject(snapshotId, objectId));
    } catch (err) {
      setObjectDetail(null);
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
  }): Promise<SnapshotSummary> {
    return captureLiveSnapshot({
      confirmReadOnly: true,
      objectNames: options.objectNames,
      searchTerms: options.searchTerms && options.searchTerms.length > 0 ? options.searchTerms : undefined,
      objectType: liveCaptureObjectTypeFor(options.objectNames, options.objectType),
      sourceSystem: liveSourceSystem.trim() || undefined,
      dataflowDirection: liveDataflowDirection,
      dataflowLevels: liveDataflowLevels,
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
    setActiveTab('lineage');
    setAllowHiddenSelection(true);
    setSelectedObjectId(item.object_id);
    setLineage(null);
    setLineageAdvice(null);
    setImpact(null);
    setImpactAdvice(null);
    setObjectDetail(null);
    setBusy('live-analyze');
    try {
      const snapshot = await captureLiveWithTargets({
        objectNames: [item.object_id],
        objectType: item.object_type,
      });
      await reloadSnapshots(snapshot.id, snapshot);
      setActiveTab('lineage');
      setAllowHiddenSelection(true);
      setSelectedObjectId(item.object_id);
      const response = await postLineage(snapshot.id, {
        object_id: item.object_id,
        direction,
        depth,
        node_cap: nodeCap,
        edge_cap: edgeCap,
      });
      setLineage(response);
      setLineageAdvice(null);
      setImpact(null);
      setImpactAdvice(null);
      setError('');
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy('');
    }
  }

  async function refreshAnalysisBasis() {
    if (!selectedSnapshotId) return;
    const objectToRerun = selectedObjectId;
    setBusy('refresh-bw');
    try {
      const snapshot = await refreshSnapshotFromBw(selectedSnapshotId);
      await reloadSnapshots(snapshot.id, snapshot);
      if (objectToRerun) {
        setAllowHiddenSelection(true);
        setSelectedObjectId(objectToRerun);
      }
      if (activeTab === 'lineage' && objectToRerun) {
        const response = await postLineage(snapshot.id, {
          object_id: objectToRerun,
          direction,
          depth,
          node_cap: nodeCap,
          edge_cap: edgeCap,
        });
        setLineage(response);
        setLineageAdvice(null);
      } else if (activeTab === 'impact' && objectToRerun) {
        setImpact(await postImpactScenario(snapshot.id, { ...impactRequestBody(), object_id: objectToRerun }));
        setImpactAdvice(null);
      } else if (activeTab === 'glossary') {
        const response = await getGlossary(snapshot.id, glossaryQuery.trim() || undefined);
        setGlossaryTerms(response.items);
      }
      setError('');
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy('');
    }
  }

  async function searchGlossary() {
    if (!selectedSnapshotId) return;
    setBusy('glossary');
    try {
      const response = await getGlossary(selectedSnapshotId, glossaryQuery.trim() || undefined);
      setGlossaryTerms(response.items);
      setError('');
    } catch (err) {
      setGlossaryTerms([]);
      setError(errorText(err));
    } finally {
      setBusy('');
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

  async function reloadSnapshots(preferredId?: string, capturedSnapshot?: SnapshotSummary) {
    const snapshotResponse = await listSnapshots();
    const nextSnapshots = mergeSnapshotCapture(snapshotResponse.snapshots, capturedSnapshot);
    const nextSnapshotId = preferredId ?? nextSnapshots[0]?.id ?? '';
    setSnapshots(nextSnapshots);
    chooseSnapshot(nextSnapshotId);
  }

  async function runLineage(startId = selectedObjectId) {
    if (!selectedSnapshotId || !startId) return;
    setBusy('lineage');
    try {
      const response = await postLineage(selectedSnapshotId, {
        object_id: startId,
        direction,
        depth,
        node_cap: nodeCap,
        edge_cap: edgeCap,
      });
      setLineage(response);
      setLineageAdvice(null);
      setSelectedObjectId(startId);
      setError('');
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy('');
    }
  }

  async function runLineageAdvice() {
    if (!selectedSnapshotId || !selectedObjectId) return;
    setBusy('lineage-advice');
    try {
      const response = await postLineageAdvice(selectedSnapshotId, {
        object_id: selectedObjectId,
        direction,
        depth,
        node_cap: nodeCap,
        edge_cap: edgeCap,
      });
      setLineage(response.lineage);
      setLineageAdvice(response);
      setError('');
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy('');
    }
  }

  function impactRequestBody() {
    return {
      object_id: selectedObjectId,
      change_type: changeType,
      field: fieldName.trim() || null,
      description: scenarioDescription.trim() || null,
      depth: Math.max(impactDepth, 1),
      node_cap: nodeCap,
      edge_cap: edgeCap,
    };
  }

  async function runImpact() {
    if (!selectedSnapshotId || !selectedObjectId) return;
    setBusy('impact');
    try {
      setImpact(await postImpactScenario(selectedSnapshotId, impactRequestBody()));
      setImpactAdvice(null);
      setError('');
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy('');
    }
  }

  async function runImpactAdvice() {
    if (!selectedSnapshotId || !selectedObjectId) return;
    setBusy('impact-advice');
    try {
      const response = await postImpactAdvice(selectedSnapshotId, impactRequestBody());
      setImpact(response.impact);
      setImpactAdvice(response);
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
      <header className="topStatus">
        <div className="brandBlock">
          <div>
            <strong>Live BW Workbench</strong>
            <span>Find in BW · Lineage · Impact · SQL · Glossary</span>
          </div>
        </div>
        <div className="statusStrip">
          <StatusPill label="BW" value={bwStatus(runtime)} tone={runtime?.connection_status === 'ok' ? 'ok' : runtime?.bw.configured ? 'warn' : 'warn'} />
          <StatusPill label="Basis" value={latestSnapshotLabel} tone={selectedSnapshot ? 'info' : 'warn'} />
          <StatusPill label="LLM" value={runtime?.llm.configured ? 'local 설정됨' : 'disabled'} tone="neutral" />
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

          <TermsOverview terms={glossaryTerms} onOpen={() => setActiveTab('glossary')} />

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
            {objects.length === 0 ? (
              <div className="emptyState">Snapshot을 capture하세요.</div>
            ) : (
              objects.map((item) => (
                <button
                  key={item.id}
                  className={item.id === selectedObjectId ? 'objectItem active' : 'objectItem'}
                  onClick={() => {
                    setAllowHiddenSelection(false);
                    setSelectedObjectId(item.id);
                    setLineage(null);
                    setLineageAdvice(null);
                    setImpact(null);
                    setImpactAdvice(null);
                  }}
                >
                  <span className="objectType">{item.type}</span>
                  <strong>{item.id}</strong>
                  <small>{item.name || item.label || '—'}</small>
                </button>
              ))
            )}
          </div>
            {objectNextCursor ? (
              <button
                className="secondaryButton fullWidth"
                disabled={busy === 'catalog'}
                onClick={() => void refreshObjects(selectedSnapshotId, objectNextCursor)}
              >
                objects 더 보기
              </button>
            ) : null}
        </aside>

        <section className="workspacePane">
          <nav className="tabBar">
            <TabButton id="lineage" active={activeTab} onClick={setActiveTab} label="Lineage" />
            <TabButton id="impact" active={activeTab} onClick={setActiveTab} label="Impact" />
            <TabButton id="sql" active={activeTab} onClick={setActiveTab} label="SQL Analysis" />
            <TabButton id="glossary" active={activeTab} onClick={setActiveTab} label="Glossary" />
          </nav>

          {activeTab === 'lineage' ? (
            <LineageTab
              selectedObject={selectedObject}
              objectDetail={objectDetail}
              lineage={lineage}
              lineageAdvice={lineageAdvice}
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
              onSelect={(id) => {
                setAllowHiddenSelection(true);
                setSelectedObjectId(id);
                setLineageAdvice(null);
                setImpact(null);
                setImpactAdvice(null);
              }}
              onExpand={(id) => {
                setAllowHiddenSelection(true);
                setLineageAdvice(null);
                setImpact(null);
                setImpactAdvice(null);
                void runLineage(id);
              }}
              busy={busy === 'lineage'}
              adviceBusy={busy === 'lineage-advice'}
            />
          ) : null}

          {activeTab === 'impact' ? (
            <ImpactTab
              selectedObject={selectedObject}
              changeType={changeType}
              setChangeType={setChangeType}
              fieldName={fieldName}
              setFieldName={setFieldName}
              description={scenarioDescription}
              setDescription={setScenarioDescription}
              impactDepth={impactDepth}
              setImpactDepth={setImpactDepth}
              onRun={() => void runImpact()}
              onAdvice={() => void runImpactAdvice()}
              impact={impact}
              impactAdvice={impactAdvice}
              busy={busy === 'impact'}
              adviceBusy={busy === 'impact-advice'}
            />
          ) : null}

          {activeTab === 'sql' ? (
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

          {activeTab === 'glossary' ? (
            <GlossaryTab
              selectedSnapshot={selectedSnapshot}
              query={glossaryQuery}
              setQuery={setGlossaryQuery}
              terms={glossaryTerms}
              onSearch={() => void searchGlossary()}
              onSelectObject={(objectId) => {
                setAllowHiddenSelection(true);
                setSelectedObjectId(objectId);
                setLineage(null);
                setLineageAdvice(null);
                setImpact(null);
                setImpactAdvice(null);
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

function LineageTab(props: {
  selectedObject: CatalogObject | null;
  objectDetail: CatalogObjectDetail | null;
  lineage: LineageResponse | null;
  lineageAdvice: LineageAdviceResponse | null;
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
  onSelect: (id: string) => void;
  onExpand: (id: string) => void;
  busy: boolean;
  adviceBusy: boolean;
}) {
  return (
    <div className="workspaceGrid">
      <section className="controlCard">
        <div className="sectionTitle">
          <span className="eyebrow">Lineage</span>
          <h1>{props.selectedObject?.id ?? '객체 선택'}</h1>
          <p className="tabPurpose">데이터 흐름 지도: 이 객체의 데이터가 어디서 와서 어디로 가는지 확인합니다.</p>
          <p>{props.graphStats}</p>
        </div>
        <div className="compactForm three">
          <label>Direction
            <select value={props.direction} onChange={(event) => props.setDirection(event.target.value as Direction)}>
              <option value="downstream">Downstream</option>
              <option value="upstream">Upstream</option>
              <option value="both">Both</option>
            </select>
          </label>
          <NumberField label="Depth" value={props.depth} min={0} max={20} onChange={props.setDepth} />
          <NumberField label="Node cap" value={props.nodeCap} min={1} max={500} onChange={props.setNodeCap} />
          <NumberField label="Edge cap" value={props.edgeCap} min={0} max={1000} onChange={props.setEdgeCap} />
        </div>
        <button className="primaryButton wide" onClick={props.onRun} disabled={!props.selectedObject || props.busy || props.adviceBusy}>
          Lineage 실행
        </button>
        <button className="secondaryButton wide" onClick={props.onAdvice} disabled={!props.selectedObject || props.busy || props.adviceBusy}>
          LLM notes
        </button>
        {props.lineage ? (
          <div className="metaGrid">
            <Metric label="Truncated" value={props.lineage.truncated ? 'Yes' : 'No'} />
            <Metric label="Cycles" value={props.lineage.cycles_detected ? 'Detected' : 'None'} />
            <Metric label="Evidence" value={String(props.lineage.evidence_ids.length)} />
          </div>
        ) : null}
      </section>
      <section className="graphCard">
        <LineageGraph lineage={props.lineage} onSelect={props.onSelect} selectedId={props.objectDetail?.id ?? null} />
      </section>
      <aside className="detailsDrawer">
        <span className="eyebrow">Details</span>
        <h2>{props.objectDetail?.id ?? '선택된 node 없음'}</h2>
        {props.objectDetail ? (
          <>
            <p>{props.objectDetail.name || props.objectDetail.label || '설명 없음'}</p>
            <div className="detailRows">
              <span>Type</span><strong>{props.objectDetail.type}</strong>
              <span>Incoming</span><strong>{props.objectDetail.incoming_count}</strong>
              <span>Outgoing</span><strong>{props.objectDetail.outgoing_count}</strong>
              <span>Evidence</span><strong>{props.objectDetail.evidence_ids.length}</strong>
            </div>
            <GlossaryList terms={props.objectGlossary} title="Glossary" emptyText="Glossary 용어 없음" />
            <button className="secondaryButton wide" onClick={() => props.onExpand(props.objectDetail!.id)}>
              Expand from node
            </button>
          </>
        ) : <p>카탈로그 또는 graph node를 선택하세요.</p>}
        {props.lineageAdvice ? (
          <div className={`llmAdviceBox ${props.lineageAdvice.status}`}>
            <h3>LLM notes</h3>
            <p>{props.lineageAdvice.message}</p>
            {props.lineageAdvice.advice ? <pre>{props.lineageAdvice.advice}</pre> : null}
            <small>Citations: {props.lineageAdvice.citations.join(', ') || 'none'}</small>
          </div>
        ) : null}
      </aside>
    </div>
  );
}

function ImpactTab(props: {
  selectedObject: CatalogObject | null;
  changeType: ChangeType;
  setChangeType: (value: ChangeType) => void;
  fieldName: string;
  setFieldName: (value: string) => void;
  description: string;
  setDescription: (value: string) => void;
  impactDepth: number;
  setImpactDepth: (value: number) => void;
  onRun: () => void;
  onAdvice: () => void;
  impact: ImpactScenarioResponse | null;
  impactAdvice: ImpactAdviceResponse | null;
  busy: boolean;
  adviceBusy: boolean;
}) {
  return (
    <div className="impactLayout">
      <section className="controlCard">
        <span className="eyebrow">Impact</span>
        <h1>변경 영향</h1>
        <p className="tabPurpose">변경 사전 시뮬레이션: 이 객체를 바꾸면 무엇이 영향받는지 심각도순으로 확인합니다.</p>
        <div className="scenarioObject">선택 object: <strong>{props.selectedObject?.id ?? '없음'}</strong></div>
        <label>Change type
          <select value={props.changeType} onChange={(event) => props.setChangeType(event.target.value as ChangeType)}>
            {changeTypes.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
        <label>Field / 필드
          <input value={props.fieldName} onChange={(event) => props.setFieldName(event.target.value)} />
        </label>
        <label>설명
          <textarea value={props.description} onChange={(event) => props.setDescription(event.target.value)} rows={4} />
        </label>
        <NumberField label="Impact depth" value={props.impactDepth} min={1} max={20} onChange={props.setImpactDepth} />
        <button className="primaryButton wide" onClick={props.onRun} disabled={!props.selectedObject || props.busy || props.adviceBusy}>
          Impact 실행
        </button>
        <button className="secondaryButton wide" onClick={props.onAdvice} disabled={!props.selectedObject || props.busy || props.adviceBusy}>
          LLM notes
        </button>
      </section>
      <section className="resultCard">
        <span className="eyebrow">Affected</span>
        {props.impactAdvice ? (
          <div className={`llmAdviceBox ${props.impactAdvice.status}`}>
            <h3>LLM notes</h3>
            <p>{props.impactAdvice.message}</p>
            {props.impactAdvice.advice ? <pre>{props.impactAdvice.advice}</pre> : null}
            <small>Citations: {props.impactAdvice.citations.join(', ') || 'none'}</small>
          </div>
        ) : null}
        {props.impact ? (
          <div className="severityList">
            {props.impact.affected_objects.map((item) => (
              <article key={item.object_id} className={`severityItem ${item.severity.toLowerCase()}`}>
                <div>
                  <strong>{item.object_id}</strong>
                  <span>{item.object_type} · {item.confidence}</span>
                </div>
                <b>{item.severity}</b>
                <p>{item.reason}</p>
                {item.glossary_terms && item.glossary_terms.length > 0 ? (
                  <div className="inlineTerms">
                    {item.glossary_terms.slice(0, 4).map((term) => <span key={term.id} title={term.evidence_ids.join(', ')}>{term.term}</span>)}
                  </div>
                ) : null}
                <small>Evidence IDs: {item.evidence_ids.join(', ') || '—'}</small>
              </article>
            ))}
            {props.impact.affected_objects.length === 0 ? <div className="emptyState">이 범위 내 영향 없음 (깊이 기준 확인 필요)</div> : null}
          </div>
        ) : <div className="emptyState">변경 시나리오(예: 필드 삭제)를 고르면 영향 객체를 심각도순으로 보여드립니다. Lineage에서 본 객체가 자동으로 선택됩니다.</div>}
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
            <GlossaryList terms={props.explain.glossary_terms} title="Glossary" emptyText="Glossary 용어 없음" />
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
  onSearch: () => void;
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
          <Metric label="Terms" value={String(props.terms.length)} />
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

function LineageGraph(props: { lineage: LineageResponse | null; onSelect: (id: string) => void; selectedId: string | null }) {
  if (!props.lineage) {
    return <div className="emptyState graphEmpty">왼쪽에서 객체를 찾고 선택한 뒤 Lineage를 실행하세요. 객체명을 몰라도 Find in BW 또는 Glossary로 검색할 수 있습니다.</div>;
  }
  const levelValues = Object.values(props.lineage.levels);
  const minLevel = Math.min(0, ...levelValues);
  const maxLevel = Math.max(0, ...levelValues);
  const positions = layoutPositions(props.lineage, minLevel);
  const maxRows = Math.max(
    1,
    ...Array.from(
      Object.values(props.lineage.levels).reduce((counts, level) => {
        counts.set(level, (counts.get(level) ?? 0) + 1);
        return counts;
      }, new Map<number, number>()).values(),
    ),
  );
  const width = Math.max(900, (maxLevel - minLevel + 1) * 220 + 188);
  const height = Math.max(340, maxRows * 84 + 124);
  const nodeTypes = Array.from(new Set(props.lineage.nodes.map((node) => node.type))).slice(0, 8);
  return (
    <div className="graphSurface">
      <div className="graphToolbar">
        <div>
          <strong>Lineage graph</strong>
          <span>{props.lineage.nodes.length} nodes · {props.lineage.edges.length} edges</span>
        </div>
        <div className="legendList">
          {nodeTypes.map((type) => <span key={type} className={`legendPill ${nodeTypeClass(type)}`}>{type}</span>)}
        </div>
      </div>
      {props.lineage.truncated ? (
        <div className="graphWarning">
          일부 neighbor가 cap/depth 제한으로 생략되었습니다. omitted={props.lineage.truncation.omitted_neighbor_total}
        </div>
      ) : null}
      <div className="graphCanvas">
        <svg className="lineageSvg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="SAP BW Lineage graph">
          <defs>
            <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" />
            </marker>
          </defs>
          {Array.from(new Set(Object.values(props.lineage.levels))).sort((a, b) => a - b).map((level) => {
            const x = 112 + (level - minLevel) * 220;
            return (
              <g key={`level-${level}`}>
                <line x1={x} y1="64" x2={x} y2={height - 38} className="levelGuide" />
                <text x={x - 30} y="38" className="levelLabel">Level {level}</text>
              </g>
            );
          })}
          {props.lineage.edges.map((edge) => {
            const source = positions[edge.source];
            const target = positions[edge.target];
            if (!source || !target) return null;
            const goesRight = source.x <= target.x;
            const startX = source.x + (goesRight ? 88 : -88);
            const targetX = target.x + (goesRight ? -88 : 88);
            const curve = Math.max(70, Math.abs(targetX - startX) / 2);
            const c1 = goesRight ? startX + curve : startX - curve;
            const c2 = goesRight ? targetX - curve : targetX + curve;
            const labelX = (source.x + target.x) / 2;
            const labelY = (source.y + target.y) / 2 - 10;
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
            return (
              <g
                key={node.id}
                transform={`translate(${point.x - 88}, ${point.y - 25})`}
                onClick={() => props.onSelect(node.id)}
                className={`nodeGroup ${nodeTypeClass(node.type)} ${node.id === props.lineage?.start_id ? 'start' : ''} ${node.id === props.selectedId ? 'selected' : ''}`}
              >
                <title>{node.id} · {node.type}</title>
                <rect className="nodeCard" width="176" height="50" rx="6" />
                <rect className="nodeAccent" width="3" height="50" rx="1.5" />
                <text x="12" y="19" className="nodeId">{shortLabel(node.id, 24)}</text>
                <text x="12" y="36" className="nodeType">{shortLabel(node.type, 20)}</text>
                {omitted > 0 ? <text x="138" y="36" className="nodeBadge">+{omitted}</text> : null}
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}

function layoutPositions(lineage: LineageResponse, minLevel = 0): Record<string, { x: number; y: number }> {
  const byLevel = new Map<number, string[]>();
  Object.entries(lineage.levels).forEach(([id, level]) => {
    byLevel.set(level, [...(byLevel.get(level) ?? []), id]);
  });
  const positions: Record<string, { x: number; y: number }> = {};
  byLevel.forEach((ids, level) => {
    ids.sort().forEach((id, index) => {
      positions[id] = { x: 112 + (level - minLevel) * 220, y: 84 + index * 84 };
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
