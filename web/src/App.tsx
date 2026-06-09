import { useEffect, useMemo, useState } from 'react';
import {
  captureFixtureSnapshot,
  captureLiveSnapshot,
  clearRuntimeConfig,
  draftSql,
  explainSql,
  getHealth,
  getObject,
  getRuntimeConfig,
  listObjects,
  listSnapshots,
  postImpactAdvice,
  postImpactScenario,
  postLineage,
  postLineageAdvice,
  putRuntimeConfig,
  type AppTab,
  type CatalogObject,
  type CatalogObjectDetail,
  type ChangeType,
  type DataflowDirection,
  type Direction,
  type XrefDirection,
  type HealthResponse,
  type ImpactAdviceResponse,
  type ImpactScenarioResponse,
  type LineageAdviceResponse,
  type LineageResponse,
  type RuntimeConfigResponse,
  type SnapshotSummary,
  type SqlDraftResponse,
  type SqlExplainResponse,
} from './api';

const fixtureGraphPath = 'tests/fixtures/sample-graph.json';
const fixtureSqlPath = 'tests/fixtures/native_sql_view.sql';
const typeFilters = ['', 'ADSO', 'HCPR', 'TRFN', 'QUERY', 'NATIVE_SQL_VIEW'];
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
  const [health, setHealth] = useState<HealthResponse | null>(null);
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
  const [liveObjectType, setLiveObjectType] = useState('ADSO');
  const [liveSourceSystem, setLiveSourceSystem] = useState('');
  const [liveDataflowDirection, setLiveDataflowDirection] = useState<DataflowDirection>('downwards');
  const [liveXrefDirection, setLiveXrefDirection] = useState<XrefDirection>('downstream');
  const [liveDataflowLevels, setLiveDataflowLevels] = useState(3);
  const [liveReadOnlyConfirmed, setLiveReadOnlyConfirmed] = useState(false);

  const selectedSnapshot = snapshots.find((snapshot) => snapshot.id === selectedSnapshotId) ?? null;
  const selectedObject = objects.find((item) => item.id === selectedObjectId)
    ?? (allowHiddenSelection && objectDetail?.id === selectedObjectId ? objectDetail : null);
  const runtimeMissing = runtime ? !runtime.bw.configured : true;
  const liveObjectNameTokens = useMemo(() => parseObjectNamesText(liveObjectNames), [liveObjectNames]);
  const snapshotPickObjects = useMemo(() => objects.slice(0, 16), [objects]);

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
      void refreshObjects(selectedSnapshotId);
    } else {
      setObjects([]);
      setObjectNextCursor(null);
      setSelectedObjectId('');
    }
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
    : '스냅샷 없음';

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

  function addLiveObjectName(objectId: string) {
    if (!objectId.trim()) return;
    setLiveObjectNames((current) => joinObjectNames([...parseObjectNamesText(current), objectId.trim()]));
  }

  function removeLiveObjectName(objectId: string) {
    setLiveObjectNames((current) => joinObjectNames(parseObjectNamesText(current).filter((item) => item !== objectId)));
  }

  function clearLiveObjectNames() {
    setLiveObjectNames('');
  }

  async function refreshAll() {
    setBusy('status');
    try {
      const [healthResponse, runtimeResponse, snapshotResponse] = await Promise.all([
        getHealth(),
        getRuntimeConfig(),
        listSnapshots(),
      ]);
      setHealth(healthResponse);
      setRuntime(runtimeResponse);
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
      if (runtimeResponse.bw.configured) {
        setDiagnosticsOpen(false);
      }
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
      const bwConfigRequested = bwSetupTouched;
      const llmFieldsProvided =
        setupForm.llmEnabled || Boolean(setupForm.llmBaseUrl.trim() || setupForm.llmModel.trim() || setupForm.llmApiKey.trim());
      const next = await putRuntimeConfig({
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
      setDiagnosticsOpen(false);
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
      setRuntime(await clearRuntimeConfig());
      setSetupForm((current) => ({ ...current, password: '', llmApiKey: '' }));
      setBwSetupTouched(false);
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
      await reloadSnapshots(snapshot.id);
      setError('');
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy('');
    }
  }

  async function captureLive() {
    const objectNames = parseLiveObjectNames();
    if (objectNames.length === 0) {
      setError('Live capture에는 dataflow/xref edge 수집 대상 object name이 최소 1개 필요합니다.');
      return;
    }
    setBusy('snapshot');
    try {
      const snapshot = await captureLiveSnapshot({
        confirmReadOnly: liveReadOnlyConfirmed,
        objectNames,
        objectType: liveObjectType.trim() || undefined,
        sourceSystem: liveSourceSystem.trim() || undefined,
        dataflowDirection: liveDataflowDirection,
        dataflowLevels: liveDataflowLevels,
        xrefDirection: liveXrefDirection,
      });
      await reloadSnapshots(snapshot.id);
      setError('');
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy('');
    }
  }

  async function reloadSnapshots(preferredId?: string) {
    const snapshotResponse = await listSnapshots();
    const nextSnapshotId = preferredId ?? snapshotResponse.snapshots[0]?.id ?? '';
    setSnapshots(snapshotResponse.snapshots);
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
          sql_file: sqlFile,
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
          sql_file: sqlFile,
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
          <span className="brandMark">BW</span>
          <div>
            <strong>BW Lineage Impact</strong>
            <span>로컬 전용 SAP BW/4HANA Lineage analyzer</span>
          </div>
        </div>
        <div className="statusStrip">
          <StatusPill label="BW" value={bwStatus(runtime)} tone={runtime?.bw.configured ? 'ok' : 'warn'} />
          <StatusPill label="Snapshot" value={latestSnapshotLabel} tone={selectedSnapshot ? 'info' : 'warn'} />
          <StatusPill label="Local-only" value={health?.local_only ? '로컬 전용' : '확인 중'} tone="ok" />
          <StatusPill label="Read-only" value={health?.read_only ? 'GET only' : '확인 중'} tone="ok" />
          <StatusPill label="LLM" value={runtime?.llm.configured ? 'local 설정됨' : 'disabled'} tone="neutral" />
        </div>
        <button className="ghostButton" onClick={() => setDiagnosticsOpen((value) => !value)}>
          Settings
        </button>
      </header>

      {error ? <div className="errorBar">{error}</div> : null}

      {runtimeMissing && !diagnosticsOpen ? (
        <div className="setupPrompt">
          BW runtime 설정이 없어 Live GET capture는 비활성입니다. <button onClick={() => setDiagnosticsOpen(true)}>Settings 열기</button>
        </div>
      ) : null}

      {diagnosticsOpen ? (
        <div className="settingsOverlay" onClick={() => setDiagnosticsOpen(false)}>
          <aside className="settingsDrawer" role="dialog" aria-modal="true" aria-label="Settings" onClick={(event) => event.stopPropagation()}>
            <div className="drawerHeader">
              <div>
                <span className="eyebrow">Settings</span>
                <h2>실행 설정</h2>
                <p>Secrets는 process memory에만 보관됩니다. BW capture는 읽기 전용 GET metadata 호출만 사용합니다.</p>
              </div>
              <button className="iconButton" onClick={() => setDiagnosticsOpen(false)} aria-label="Settings 닫기">×</button>
            </div>

            <section className="drawerSection">
              <h3>Runtime / Diagnostics</h3>
              <p>
                {runtime?.bw.source === 'env'
                  ? 'BW 설정은 .env/environment에서 로드되었습니다. UI 재입력은 필요 없습니다.'
                  : 'Live capture에는 BW_URL, BW_USER, BW_PASSWORD, BW_CLIENT가 필요합니다.'}
              </p>
              <div className="setupGrid">
                <input placeholder="BW_URL" value={setupForm.url} onChange={(event) => { setBwSetupTouched(true); setSetupForm({ ...setupForm, url: event.target.value }); }} />
                <input placeholder="BW_USER" value={setupForm.user} onChange={(event) => { setBwSetupTouched(true); setSetupForm({ ...setupForm, user: event.target.value }); }} />
                <input placeholder="BW_PASSWORD" type="password" value={setupForm.password} onChange={(event) => { setBwSetupTouched(true); setSetupForm({ ...setupForm, password: event.target.value }); }} />
                <input placeholder="BW_CLIENT" value={setupForm.client} onChange={(event) => { setBwSetupTouched(true); setSetupForm({ ...setupForm, client: event.target.value }); }} />
                <input placeholder="BW_LANGUAGE (예: EN)" value={setupForm.language} onChange={(event) => { setBwSetupTouched(true); setSetupForm({ ...setupForm, language: event.target.value }); }} />
                <input placeholder="BW_CA_BUNDLE (optional)" value={setupForm.caBundle} onChange={(event) => { setBwSetupTouched(true); setSetupForm({ ...setupForm, caBundle: event.target.value }); }} />
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
                <label className="checkField fullSpan">
                  <input
                    type="checkbox"
                    checked={setupForm.llmEnabled}
                    onChange={(event) => setSetupForm({ ...setupForm, llmEnabled: event.target.checked })}
                  />
                  로컬 OpenAI-compatible LLM advisory 활성화
                </label>
                <input
                  placeholder="BWLI_LLM_BASE_URL (local only)"
                  value={setupForm.llmBaseUrl}
                  onChange={(event) => setSetupForm({ ...setupForm, llmBaseUrl: event.target.value })}
                />
                <input placeholder="BWLI_LLM_MODEL" value={setupForm.llmModel} onChange={(event) => setSetupForm({ ...setupForm, llmModel: event.target.value })} />
                <input
                  placeholder={runtime?.llm.configured ? 'BWLI_LLM_API_KEY 설정됨' : 'BWLI_LLM_API_KEY'}
                  type="password"
                  value={setupForm.llmApiKey}
                  onChange={(event) => setSetupForm({ ...setupForm, llmApiKey: event.target.value })}
                />
                <p className="setupHint fullSpan">LLM: {llmStatus(runtime)} · SQL/Lineage/Impact advisory only · SQL/BW write 실행 없음.</p>
                <button className="primaryButton" onClick={saveSetup} disabled={busy === 'setup'}>설정 저장</button>
                <button className="secondaryButton" onClick={clearSetup} disabled={busy === 'setup'}>초기화 / env fallback</button>
              </div>
            </section>

            <section className="drawerSection">
              <h3>Snapshot capture</h3>
              <p>Fixture는 로컬 샘플 검증용입니다. Live GET capture는 선택한 object names만 좁게 조회합니다.</p>
              <label className="fieldLabel">
                Live object names
                <textarea
                  className="liveObjectInput"
                  placeholder="ZADSO_SALES, ZTRFN_MARGIN — dataflow/xref edge 수집 대상"
                  value={liveObjectNames}
                  onChange={(event) => setLiveObjectNames(event.target.value)}
                />
              </label>
              <div className="liveObjectTools">
                <button className="secondaryButton" onClick={() => addLiveObjectName(selectedObjectId)} disabled={!selectedObjectId}>
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
                      {name} ×
                    </button>
                  ))}
                </div>
              ) : (
                <p className="livePickerHint">카탈로그에서 객체를 선택한 뒤 “선택 객체 추가”를 누르거나 직접 입력하세요.</p>
              )}
              {snapshotPickObjects.length > 0 ? (
                <div className="snapshotPickList" aria-label="snapshot object quick picker">
                  {snapshotPickObjects.map((item) => (
                    <button key={item.id} className="snapshotPickButton" onClick={() => addLiveObjectName(item.id)}>
                      <span>{item.type}</span>{item.id}
                    </button>
                  ))}
                </div>
              ) : null}
              <div className="liveOptionsGrid">
                <label>
                  Object type
                  <input value={liveObjectType} onChange={(event) => setLiveObjectType(event.target.value)} />
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
                  Where-used
                  <select value={liveXrefDirection} onChange={(event) => setLiveXrefDirection(event.target.value as XrefDirection)}>
                    <option value="downstream">Downstream</option>
                    <option value="upstream">Upstream</option>
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
              <label className="checkField liveConfirm fullSpan">
                <input
                  type="checkbox"
                  checked={liveReadOnlyConfirmed}
                  onChange={(event) => setLiveReadOnlyConfirmed(event.target.checked)}
                />
                읽기 전용 GET metadata capture임을 확인합니다.
              </label>
              <div className="captureRow">
                <button className="secondaryButton" onClick={captureFixture} disabled={busy === 'snapshot'}>
                  Fixture capture
                </button>
                <button className="primaryButton" onClick={captureLive} disabled={!runtime?.bw.configured || !liveReadOnlyConfirmed || busy === 'snapshot'}>
                  Live GET capture
                </button>
              </div>
            </section>
          </aside>
        </div>
      ) : null}

      <main className="appFrame">
        <aside className="catalogPane">
          <div className="paneHeader">
            <div>
              <span className="eyebrow">Object Catalog</span>
              <h2>객체 카탈로그</h2>
            </div>
            <button className="iconButton" onClick={() => void refreshAll()} disabled={busy === 'status'}>↻</button>
          </div>

          <label className="fieldLabel">
            Snapshot
            <select value={selectedSnapshotId} onChange={(event) => chooseSnapshot(event.target.value)}>
              <option value="">스냅샷 없음</option>
              {snapshots.map((snapshot) => (
                <option key={snapshot.id} value={snapshot.id}>
                  {compactDate(snapshot.created_at)} · {snapshot.object_count} objects
                </option>
              ))}
            </select>
          </label>

          <div className="catalogActionCard">
            <strong>Capture 설정</strong>
            <p>Fixture / Live GET capture와 object names 선택은 Settings 패널로 이동했습니다.</p>
            <button className="secondaryButton wide" onClick={() => setDiagnosticsOpen(true)}>Settings 열기</button>
            {liveObjectNameTokens.length > 0 ? <small>{liveObjectNameTokens.length}개 live capture 대상 선택됨</small> : null}
          </div>

          <input
            className="catalogSearch"
            placeholder="object 이름 검색"
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
              <div className="emptyState">객체가 없습니다. 먼저 snapshot을 capture하세요.</div>
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
            <TabButton id="sql" active={activeTab} onClick={setActiveTab} label="SQL Assistant" />
          </nav>

          {activeTab === 'lineage' ? (
            <LineageTab
              selectedObject={selectedObject}
              objectDetail={objectDetail}
              lineage={lineage}
              lineageAdvice={lineageAdvice}
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
              runtime={runtime}
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
              question={sqlQuestion}
              setQuestion={setSqlQuestion}
              explain={sqlExplain}
              draft={sqlDraft}
              onExplain={() => void runSqlExplain()}
              onDraft={() => void runSqlDraft()}
              busy={busy}
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
          <span className="eyebrow">Bounded graph</span>
          <h1>{props.selectedObject?.id ?? '객체 선택'}</h1>
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
          로컬 LLM graph notes
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
        <span className="eyebrow">Node details</span>
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
            <button className="secondaryButton wide" onClick={() => props.onExpand(props.objectDetail!.id)}>
              이 node에서 확장
            </button>
          </>
        ) : <p>카탈로그 또는 graph node를 선택하세요.</p>}
        {props.lineageAdvice ? (
          <div className={`llmAdviceBox ${props.lineageAdvice.status}`}>
            <h3>Lineage LLM advisory</h3>
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
  runtime: RuntimeConfigResponse | null;
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
        <span className="eyebrow">Scenario form</span>
        <h1>Impact / 변경 영향</h1>
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
          deterministic Impact 실행
        </button>
        <button className="secondaryButton wide" onClick={props.onAdvice} disabled={!props.selectedObject || props.busy || props.adviceBusy}>
          로컬 LLM review notes
        </button>
        <p className="mutedSmall">
          LLM: {props.runtime?.llm.configured ? `${props.runtime.llm.source} · ${props.runtime.llm.model}` : '로컬 endpoint 설정 전 disabled'}
        </p>
      </section>
      <section className="resultCard">
        <span className="eyebrow">Affected objects</span>
        {props.impactAdvice ? (
          <div className={`llmAdviceBox ${props.impactAdvice.status}`}>
            <h3>LLM advisory review</h3>
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
                <small>Evidence IDs: {item.evidence_ids.join(', ') || '—'}</small>
              </article>
            ))}
            {props.impact.affected_objects.length === 0 ? <div className="emptyState">Downstream Impact가 없습니다.</div> : null}
          </div>
        ) : <div className="emptyState">시나리오를 실행하세요. changes_path는 필요 없습니다.</div>}
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
        <span className="eyebrow">SQL Assistant</span>
        <h1>Native SQL View</h1>
        <p className="warningText">Advisory only · execution_disabled=true · SQL은 실행하지 않습니다.</p>
        <label>View ID
          <input value={props.viewId} onChange={(event) => props.setViewId(event.target.value)} />
        </label>
        <label>SQL file
          <input value={props.sqlFile} onChange={(event) => props.setSqlFile(event.target.value)} />
        </label>
        <button className="secondaryButton wide" onClick={props.onExplain} disabled={props.busy === 'sql-explain'}>
          deterministic view 설명
        </button>
        <label>NL-to-SQL advisory prompt
          <textarea value={props.question} onChange={(event) => props.setQuestion(event.target.value)} rows={4} />
        </label>
        <button className="primaryButton wide" onClick={props.onDraft} disabled={props.busy === 'sql-draft'}>
          advisory SQL 초안
        </button>
        <p className="mutedSmall">
          LLM: {props.runtime?.llm.configured ? `${props.runtime.llm.source} · ${props.runtime.llm.model}` : '로컬 endpoint 설정 전 disabled'}
        </p>
      </section>
      <section className="resultCard">
        <span className="eyebrow">Citations / 실행 차단</span>
        {props.explain ? (
          <div className="sqlEvidence">
            <h2>{props.explain.result.view.id}</h2>
            <p>{props.explain.execution_disabled_warning}</p>
            <div className="metaGrid">
              <Metric label="Parser" value={props.explain.result.parser} />
              <Metric label="Refs" value={String(props.explain.result.reference_edges.length)} />
              <Metric label="Citations" value={String(props.explain.citations.length)} />
            </div>
            <pre>{JSON.stringify(props.explain.result.reference_edges, null, 2)}</pre>
          </div>
        ) : <div className="emptyState">로컬 SQL file을 설명하면 citation evidence가 표시됩니다.</div>}
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

function LineageGraph(props: { lineage: LineageResponse | null; onSelect: (id: string) => void; selectedId: string | null }) {
  if (!props.lineage) {
    return <div className="emptyState graphEmpty">Lineage를 실행하면 bounded graph가 표시됩니다.</div>;
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
  const width = Math.max(860, (maxLevel - minLevel + 1) * 260 + 120);
  const height = Math.max(520, maxRows * 110 + 150);
  const nodeTypes = Array.from(new Set(props.lineage.nodes.map((node) => node.type))).slice(0, 8);
  return (
    <div className="graphSurface">
      <div className="graphToolbar">
        <div>
          <strong>Layered Lineage graph</strong>
          <span>Level, object type, edge direction, truncation 표시</span>
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
            const x = 140 + (level - minLevel) * 260;
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
            const startX = source.x + (goesRight ? 104 : -104);
            const targetX = target.x + (goesRight ? -104 : 104);
            const curve = Math.max(70, Math.abs(targetX - startX) / 2);
            const c1 = goesRight ? startX + curve : startX - curve;
            const c2 = goesRight ? targetX - curve : targetX + curve;
            const labelX = (source.x + target.x) / 2;
            const labelY = (source.y + target.y) / 2 - 10;
            return (
              <g key={edge.id} className={`edgeGroup ${edgeTypeClass(edge.type)}`}>
                <path d={`M ${startX} ${source.y} C ${c1} ${source.y}, ${c2} ${target.y}, ${targetX} ${target.y}`} className="edgeLine" markerEnd="url(#arrow)" />
                <text x={labelX} y={labelY} className="edgeLabel">{edge.type}</text>
              </g>
            );
          })}
          {props.lineage.nodes.map((node) => {
            const point = positions[node.id];
            const omitted = props.lineage?.omitted_neighbor_counts[node.id] ?? 0;
            return (
              <g
                key={node.id}
                transform={`translate(${point.x - 102}, ${point.y - 34})`}
                onClick={() => props.onSelect(node.id)}
                className={`nodeGroup ${nodeTypeClass(node.type)} ${node.id === props.lineage?.start_id ? 'start' : ''} ${node.id === props.selectedId ? 'selected' : ''}`}
              >
                <rect width="204" height="68" rx="16" />
                <text x="14" y="24" className="nodeId">{shortLabel(node.id, 24)}</text>
                <text x="14" y="46" className="nodeType">{node.type}</text>
                {omitted > 0 ? <text x="154" y="46" className="nodeBadge">+{omitted}</text> : null}
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
      positions[id] = { x: 140 + (level - minLevel) * 260, y: 104 + index * 112 };
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
        onChange={(event) => props.onChange(Number(event.target.value))}
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

function bwStatus(runtime: RuntimeConfigResponse | null): string {
  if (!runtime) return '확인 중';
  if (!runtime.bw.configured) return '미설정';
  if (runtime.bw.source === 'env') return '.env 설정';
  return 'UI 설정';
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
