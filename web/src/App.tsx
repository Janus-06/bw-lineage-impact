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
  postImpactScenario,
  postLineage,
  putRuntimeConfig,
  type AppTab,
  type CatalogObject,
  type CatalogObjectDetail,
  type ChangeType,
  type DataflowDirection,
  type Direction,
  type XrefDirection,
  type HealthResponse,
  type ImpactScenarioResponse,
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
  const [impact, setImpact] = useState<ImpactScenarioResponse | null>(null);
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
  const showDiagnostics = diagnosticsOpen || runtimeMissing;

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
      setImpact(null);
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
    : 'No snapshot / 스냅샷 없음';

  const graphStats = useMemo(() => {
    if (!lineage) return 'Run lineage';
    const capText = lineage.truncated ? `truncated ${lineage.truncation.omitted_neighbor_total}` : 'complete';
    return `${lineage.nodes.length} nodes · ${lineage.edges.length} edges · ${capText}`;
  }, [lineage]);

  function clearAnalysisState() {
    setLineage(null);
    setImpact(null);
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
    const raw = liveObjectNames.trim() || selectedObjectId;
    return raw
      .split(/[\n,]+/)
      .map((value) => value.trim())
      .filter(Boolean);
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
      setError('Live capture requires at least one object name so dataflow/xref edges can be collected.');
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
      setSelectedObjectId(startId);
      setError('');
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy('');
    }
  }

  async function runImpact() {
    if (!selectedSnapshotId || !selectedObjectId) return;
    setBusy('impact');
    try {
      setImpact(
        await postImpactScenario(selectedSnapshotId, {
          object_id: selectedObjectId,
          change_type: changeType,
          field: fieldName.trim() || null,
          description: scenarioDescription.trim() || null,
          depth: Math.max(impactDepth, 1),
          node_cap: nodeCap,
          edge_cap: edgeCap,
        }),
      );
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
            <span>local-first SAP BW/4HANA lineage analyzer</span>
          </div>
        </div>
        <div className="statusStrip">
          <StatusPill label="BW" value={bwStatus(runtime)} tone={runtime?.bw.configured ? 'ok' : 'warn'} />
          <StatusPill label="Snapshot" value={latestSnapshotLabel} tone={selectedSnapshot ? 'info' : 'warn'} />
          <StatusPill label="Local-only" value={health?.local_only ? '로컬 전용' : 'checking'} tone="ok" />
          <StatusPill label="Read-only" value={health?.read_only ? 'GET only' : 'checking'} tone="ok" />
          <StatusPill label="LLM" value={runtime?.llm.configured ? 'local configured' : 'disabled'} tone="neutral" />
        </div>
        <button className="ghostButton" onClick={() => setDiagnosticsOpen((value) => !value)}>
          Diagnostics / 설정
        </button>
      </header>

      {error ? <div className="errorBar">{error}</div> : null}

      {showDiagnostics ? (
        <section className="diagnosticsPanel">
          <div>
            <h2>Setup / Diagnostics</h2>
            <p>
              {runtime?.bw.source === 'env'
                ? 'BW is configured from .env/environment; duplicate UI setup is not required.'
                : 'BW 환경 설정이 없으면 live capture는 비활성입니다. Secrets stay in process memory only.'}
            </p>
          </div>
          <div className="setupGrid">
            <input placeholder="BW_URL" value={setupForm.url} onChange={(event) => { setBwSetupTouched(true); setSetupForm({ ...setupForm, url: event.target.value }); }} />
            <input placeholder="BW_USER" value={setupForm.user} onChange={(event) => { setBwSetupTouched(true); setSetupForm({ ...setupForm, user: event.target.value }); }} />
            <input placeholder="BW_PASSWORD" type="password" value={setupForm.password} onChange={(event) => { setBwSetupTouched(true); setSetupForm({ ...setupForm, password: event.target.value }); }} />
            <input placeholder="BW_CLIENT" value={setupForm.client} onChange={(event) => { setBwSetupTouched(true); setSetupForm({ ...setupForm, client: event.target.value }); }} />
            <input placeholder="BW_CA_BUNDLE (optional)" value={setupForm.caBundle} onChange={(event) => { setBwSetupTouched(true); setSetupForm({ ...setupForm, caBundle: event.target.value }); }} />
            <label className="checkField">
              <input
                type="checkbox"
                checked={setupForm.verifySsl}
                onChange={(event) => { setBwSetupTouched(true); setSetupForm({ ...setupForm, verifySsl: event.target.checked }); }}
              />
              Verify SSL
            </label>
            <label className="checkField">
              <input
                type="checkbox"
                checked={setupForm.trustEnv}
                onChange={(event) => { setBwSetupTouched(true); setSetupForm({ ...setupForm, trustEnv: event.target.checked }); }}
              />
              Trust proxy env
            </label>
            <label className="checkField">
              <input
                type="checkbox"
                checked={setupForm.llmEnabled}
                onChange={(event) => setSetupForm({ ...setupForm, llmEnabled: event.target.checked })}
              />
              Enable local OpenAI-compatible SQL drafts
            </label>
            <input
              placeholder="BWLI_LLM_BASE_URL (local only)"
              value={setupForm.llmBaseUrl}
              onChange={(event) => setSetupForm({ ...setupForm, llmBaseUrl: event.target.value })}
            />
            <input placeholder="BWLI_LLM_MODEL" value={setupForm.llmModel} onChange={(event) => setSetupForm({ ...setupForm, llmModel: event.target.value })} />
            <input
              placeholder={runtime?.llm.configured ? 'BWLI_LLM_API_KEY already configured' : 'BWLI_LLM_API_KEY'}
              type="password"
              value={setupForm.llmApiKey}
              onChange={(event) => setSetupForm({ ...setupForm, llmApiKey: event.target.value })}
            />
            <p className="setupHint">LLM: {llmStatus(runtime)} · SQL Assistant never executes SQL.</p>
            <button className="primaryButton" onClick={saveSetup} disabled={busy === 'setup'}>Save runtime</button>
            <button className="secondaryButton" onClick={clearSetup} disabled={busy === 'setup'}>Clear / env fallback</button>
          </div>
        </section>
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
              <option value="">No snapshot</option>
              {snapshots.map((snapshot) => (
                <option key={snapshot.id} value={snapshot.id}>
                  {compactDate(snapshot.created_at)} · {snapshot.object_count} objects
                </option>
              ))}
            </select>
          </label>

          <label className="fieldLabel">
            Live object names
            <textarea
              className="liveObjectInput"
              placeholder="ZADSO_SALES, ZTRFN_MARGIN — required for live dataflow/xref edges"
              value={liveObjectNames}
              onChange={(event) => setLiveObjectNames(event.target.value)}
            />
          </label>

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

          <label className="checkField liveConfirm">
            <input
              type="checkbox"
              checked={liveReadOnlyConfirmed}
              onChange={(event) => setLiveReadOnlyConfirmed(event.target.checked)}
            />
            I confirm read-only GET metadata capture / 읽기 전용 메타데이터 조회를 확인합니다.
          </label>

          <div className="captureRow">
            <button className="secondaryButton" onClick={captureFixture} disabled={busy === 'snapshot'}>
              Fixture capture
            </button>
            <button className="secondaryButton" onClick={captureLive} disabled={!runtime?.bw.configured || !liveReadOnlyConfirmed || busy === 'snapshot'}>
              Live GET capture
            </button>
          </div>

          <input
            className="catalogSearch"
            placeholder="Search object / 이름 검색"
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
                {filter || 'All'}
              </button>
            ))}
          </div>

          <div className="objectList" aria-busy={busy === 'catalog'}>
            {objects.length === 0 ? (
              <div className="emptyState">No objects. Capture a snapshot first.</div>
            ) : (
              objects.map((item) => (
                <button
                  key={item.id}
                  className={item.id === selectedObjectId ? 'objectItem active' : 'objectItem'}
                  onClick={() => {
                    setAllowHiddenSelection(false);
                    setSelectedObjectId(item.id);
                    setLineage(null);
                    setImpact(null);
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
                Load more objects
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
              onSelect={(id) => {
                setAllowHiddenSelection(true);
                setSelectedObjectId(id);
                setImpact(null);
              }}
              onExpand={(id) => {
                setAllowHiddenSelection(true);
                setImpact(null);
                void runLineage(id);
              }}
              busy={busy === 'lineage'}
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
              impact={impact}
              busy={busy === 'impact'}
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
  onSelect: (id: string) => void;
  onExpand: (id: string) => void;
  busy: boolean;
}) {
  return (
    <div className="workspaceGrid">
      <section className="controlCard">
        <div className="sectionTitle">
          <span className="eyebrow">Bounded graph</span>
          <h1>{props.selectedObject?.id ?? 'Select object'}</h1>
          <p>{props.graphStats}</p>
        </div>
        <div className="compactForm three">
          <label>Direction
            <select value={props.direction} onChange={(event) => props.setDirection(event.target.value as Direction)}>
              <option value="downstream">downstream</option>
              <option value="upstream">upstream</option>
              <option value="both">both</option>
            </select>
          </label>
          <NumberField label="Depth" value={props.depth} min={0} max={20} onChange={props.setDepth} />
          <NumberField label="Node cap" value={props.nodeCap} min={1} max={500} onChange={props.setNodeCap} />
          <NumberField label="Edge cap" value={props.edgeCap} min={0} max={1000} onChange={props.setEdgeCap} />
        </div>
        <button className="primaryButton wide" onClick={props.onRun} disabled={!props.selectedObject || props.busy}>
          Run lineage / 영향 경로 보기
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
        <LineageGraph lineage={props.lineage} onSelect={props.onSelect} />
      </section>
      <aside className="detailsDrawer">
        <span className="eyebrow">Node details</span>
        <h2>{props.objectDetail?.id ?? 'No node selected'}</h2>
        {props.objectDetail ? (
          <>
            <p>{props.objectDetail.name || props.objectDetail.label || 'No description'}</p>
            <div className="detailRows">
              <span>Type</span><strong>{props.objectDetail.type}</strong>
              <span>Incoming</span><strong>{props.objectDetail.incoming_count}</strong>
              <span>Outgoing</span><strong>{props.objectDetail.outgoing_count}</strong>
              <span>Evidence</span><strong>{props.objectDetail.evidence_ids.length}</strong>
            </div>
            <button className="secondaryButton wide" onClick={() => props.onExpand(props.objectDetail!.id)}>
              Expand from node
            </button>
          </>
        ) : <p>카탈로그에서 객체를 선택하세요.</p>}
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
  impact: ImpactScenarioResponse | null;
  busy: boolean;
}) {
  return (
    <div className="impactLayout">
      <section className="controlCard">
        <span className="eyebrow">Scenario form</span>
        <h1>Impact / 변경 영향</h1>
        <div className="scenarioObject">Selected: <strong>{props.selectedObject?.id ?? 'none'}</strong></div>
        <label>Change type
          <select value={props.changeType} onChange={(event) => props.setChangeType(event.target.value as ChangeType)}>
            {changeTypes.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
        <label>Field / 필드
          <input value={props.fieldName} onChange={(event) => props.setFieldName(event.target.value)} />
        </label>
        <label>Description / 설명
          <textarea value={props.description} onChange={(event) => props.setDescription(event.target.value)} rows={4} />
        </label>
        <NumberField label="Impact depth" value={props.impactDepth} min={1} max={20} onChange={props.setImpactDepth} />
        <button className="primaryButton wide" onClick={props.onRun} disabled={!props.selectedObject || props.busy}>
          Run deterministic impact
        </button>
      </section>
      <section className="resultCard">
        <span className="eyebrow">Affected objects</span>
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
            {props.impact.affected_objects.length === 0 ? <div className="emptyState">No downstream impacts.</div> : null}
          </div>
        ) : <div className="emptyState">Run a scenario. No changes_path required.</div>}
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
          Explain deterministic view
        </button>
        <label>NL-to-SQL advisory prompt
          <textarea value={props.question} onChange={(event) => props.setQuestion(event.target.value)} rows={4} />
        </label>
        <button className="primaryButton wide" onClick={props.onDraft} disabled={props.busy === 'sql-draft'}>
          Draft advisory SQL
        </button>
        <p className="mutedSmall">
          LLM: {props.runtime?.llm.configured ? `${props.runtime.llm.source} · ${props.runtime.llm.model}` : 'disabled until local endpoint configured'}
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
        ) : <div className="emptyState">Explain a local SQL file to see cited evidence.</div>}
        {props.draft ? (
          <div className="draftBox">
            <h3>Draft status: {props.draft.status}</h3>
            <pre>{props.draft.draft_sql || props.draft.message}</pre>
            <small>Citations: {props.draft.citations.join(', ') || 'none'}</small>
          </div>
        ) : null}
      </section>
    </div>
  );
}

function LineageGraph(props: { lineage: LineageResponse | null; onSelect: (id: string) => void }) {
  if (!props.lineage) {
    return <div className="emptyState graphEmpty">Run lineage to render a bounded graph.</div>;
  }
  const positions = layoutPositions(props.lineage);
  const width = Math.max(720, (Math.max(...Object.values(props.lineage.levels)) + 1) * 210);
  const height = Math.max(420, props.lineage.nodes.length * 74);
  return (
    <svg className="lineageSvg" viewBox={`0 0 ${width} ${height}`} role="img">
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" />
        </marker>
      </defs>
      {props.lineage.edges.map((edge) => {
        const source = positions[edge.source];
        const target = positions[edge.target];
        if (!source || !target) return null;
        return (
          <g key={edge.id}>
            <line x1={source.x + 72} y1={source.y} x2={target.x - 72} y2={target.y} className="edgeLine" markerEnd="url(#arrow)" />
            <text x={(source.x + target.x) / 2} y={(source.y + target.y) / 2 - 8} className="edgeLabel">{edge.type}</text>
          </g>
        );
      })}
      {props.lineage.nodes.map((node) => {
        const point = positions[node.id];
        return (
          <g key={node.id} transform={`translate(${point.x - 70}, ${point.y - 28})`} onClick={() => props.onSelect(node.id)} className="nodeGroup">
            <rect width="140" height="56" rx="12" />
            <text x="12" y="22" className="nodeId">{node.id}</text>
            <text x="12" y="40" className="nodeType">{node.type}</text>
          </g>
        );
      })}
    </svg>
  );
}

function layoutPositions(lineage: LineageResponse): Record<string, { x: number; y: number }> {
  const byLevel = new Map<number, string[]>();
  Object.entries(lineage.levels).forEach(([id, level]) => {
    byLevel.set(level, [...(byLevel.get(level) ?? []), id]);
  });
  const positions: Record<string, { x: number; y: number }> = {};
  byLevel.forEach((ids, level) => {
    ids.sort().forEach((id, index) => {
      positions[id] = { x: 110 + level * 210, y: 90 + index * 86 };
    });
  });
  return positions;
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
  if (!runtime) return 'checking';
  if (!runtime.bw.configured) return 'unset';
  if (runtime.bw.source === 'env') return 'configured from .env';
  return 'configured in UI';
}

function llmStatus(runtime: RuntimeConfigResponse | null): string {
  if (!runtime) return 'checking';
  if (!runtime.llm.enabled || !runtime.llm.configured) return 'disabled';
  if (runtime.llm.source === 'env') return `configured from .env (${runtime.llm.model ?? 'model set'})`;
  return `configured in UI (${runtime.llm.model ?? 'model set'})`;
}

function compactDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
