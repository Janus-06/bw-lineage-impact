import { type ReactNode, useEffect, useMemo, useState } from 'react';
import {
  clearRuntimeConfig,
  getHealth,
  getRuntimeConfig,
  postJson,
  postRendered,
  putRuntimeConfig,
  type HealthResponse,
  type LiveCollectResponse,
  type LiveSmokeResponse,
  type OutputFormat,
  type RuntimeConfigResponse,
} from './api';

type Tool = 'settings' | 'live' | 'lineage' | 'impact' | 'sql-view' | 'field-lineage';
type AnalysisTool = Exclude<Tool, 'settings' | 'live'>;
type LiveMode = 'smoke' | 'collect' | 'dataflow';
type Tone = 'ok' | 'warning' | 'danger' | 'neutral' | 'info' | 'accent';

interface FormState {
  graphPath: string;
  changesPath: string;
  objectId: string;
  lineageDirection: 'upstream' | 'downstream' | 'both';
  lineageMaxDepth: string;
  impactMaxDepth: string;
  sqlFile: string;
  viewId: string;
  xmlFile: string;
  transformationId: string;
  sourceObject: string;
  targetObject: string;
  format: OutputFormat;
  liveMode: LiveMode;
  liveSearchTerm: string;
  liveObjectName: string;
  liveObjectType: string;
  liveSourceSystem: string;
  liveDataflowDirection: 'upwards' | 'downwards' | 'both';
  liveDataflowLevels: string;
  liveOutDir: string;
  liveConfirm: boolean;
}

interface SettingsState {
  bwUrl: string;
  bwUser: string;
  bwPassword: string;
  bwClient: string;
  bwLanguage: string;
  bwVerifySsl: boolean;
  bwCaBundle: string;
  llmEnabled: boolean;
  llmBaseUrl: string;
  llmModel: string;
  llmApiKey: string;
}

interface ToolMeta {
  id: Tool;
  label: string;
  shortLabel: string;
  eyebrow: string;
  description: string;
  endpoint: string;
  runLabel: string;
  outputLabel: string;
  emptyHint: string;
  helperCards: HelperCard[];
}

interface HelperCard {
  tag: string;
  title: string;
  body: string;
  tone?: Tone;
}

interface ResultMeta {
  title: string;
  endpoint: string;
  format: string;
  timestamp: string;
  mode: string;
  summary: string;
}

const defaultState: FormState = {
  graphPath: 'tests/fixtures/sample-graph.json',
  changesPath: 'tests/fixtures/sample-changes.json',
  objectId: 'SRC',
  lineageDirection: 'downstream',
  lineageMaxDepth: '3',
  impactMaxDepth: '3',
  sqlFile: 'tests/fixtures/native_sql_view.sql',
  viewId: 'ZSQL_VIEW',
  xmlFile: 'tests/fixtures/sample-transformation.xml',
  transformationId: 'T1',
  sourceObject: 'SRC',
  targetObject: 'TGT',
  format: 'md',
  liveMode: 'smoke',
  liveSearchTerm: 'Z*',
  liveObjectName: '',
  liveObjectType: 'ADSO',
  liveSourceSystem: '',
  liveDataflowDirection: 'downwards',
  liveDataflowLevels: '3',
  liveOutDir: '.tmp/live-snapshot',
  liveConfirm: false,
};

const defaultSettings: SettingsState = {
  bwUrl: '',
  bwUser: '',
  bwPassword: '',
  bwClient: '100',
  bwLanguage: 'EN',
  bwVerifySsl: true,
  bwCaBundle: '',
  llmEnabled: false,
  llmBaseUrl: 'http://127.0.0.1:11434/v1',
  llmModel: '',
  llmApiKey: '',
};

const toolOrder: Tool[] = ['settings', 'live', 'lineage', 'impact', 'sql-view', 'field-lineage'];

const toolCatalog: Record<Tool, ToolMeta> = {
  settings: {
    id: 'settings',
    label: 'Runtime Settings',
    shortLabel: 'Settings',
    eyebrow: 'Secure runtime drawer',
    description:
      'Configure SAP BW and optional local LLM access for this process only. Secrets are transient and write-only.',
    endpoint: '/api/runtime-config',
    runLabel: 'Save runtime settings',
    outputLabel: 'Configuration status',
    emptyHint: '저장 후 BW/LLM 상태와 secret redaction 결과가 여기에 표시됩니다.',
    helperCards: [
      {
        tag: 'Safety',
        title: 'Process-memory only',
        body: 'Password and API key fields are never persisted to files or browser storage. Save clears the form values immediately.',
        tone: 'ok',
      },
      {
        tag: 'LLM',
        title: 'Optional local explainer',
        body: 'OpenAI-compatible local endpoints can be enabled for explanations without changing the read-only BW contract.',
        tone: 'info',
      },
    ],
  },
  live: {
    id: 'live',
    label: 'Live BW Workbench',
    shortLabel: 'Live BW',
    eyebrow: 'Read-only live metadata',
    description:
      'Run smoke checks, collect a local snapshot, or render a BW dataflow with explicit read-only confirmation.',
    endpoint: '/api/live/*',
    runLabel: 'Run confirmed read-only action',
    outputLabel: 'Live response',
    emptyHint: 'BW runtime 설정과 read-only 확인을 완료한 뒤 smoke/collect/dataflow 결과를 확인하세요.',
    helperCards: [
      {
        tag: 'Gate',
        title: 'Explicit safety confirmation',
        body: 'The API rejects live calls unless confirm_read_only is true. The UI keeps this confirmation visible at execution time.',
        tone: 'ok',
      },
      {
        tag: 'Dataflow',
        title: 'Mermaid graph preview',
        body: 'Choose Dataflow + Mermaid to receive copyable graph source for reviews or Markdown reports.',
        tone: 'accent',
      },
      {
        tag: 'Snapshot',
        title: 'Collect for offline evidence',
        body: 'Collect writes a local manifest and evidence snapshot under the selected output directory.',
        tone: 'info',
      },
    ],
  },
  lineage: {
    id: 'lineage',
    label: 'Lineage Graph',
    shortLabel: 'Lineage',
    eyebrow: 'Graph traversal workspace',
    description:
      'Traverse a local BW lineage graph by object, direction, and depth; export Markdown, JSON, or Mermaid source.',
    endpoint: '/api/lineage',
    runLabel: 'Run lineage traversal',
    outputLabel: 'Lineage result',
    emptyHint: 'Fixture graph defaults are loaded. Run traversal or switch to Mermaid for a graph-source preview.',
    helperCards: [
      {
        tag: 'Sample path',
        title: 'SRC → downstream impact chain',
        body: 'Start with tests/fixtures/sample-graph.json and object SRC at depth 3 to validate local analysis before live snapshots.',
        tone: 'info',
      },
      {
        tag: 'Review',
        title: 'Direction matters',
        body: 'Use upstream for dependency discovery, downstream for blast radius, and both for object-centered reviews.',
        tone: 'accent',
      },
    ],
  },
  impact: {
    id: 'impact',
    label: 'Change Impact',
    shortLabel: 'Impact',
    eyebrow: 'Risk review',
    description:
      'Combine a graph snapshot and change list to produce affected-object review evidence for release decisions.',
    endpoint: '/api/impact',
    runLabel: 'Run change-impact review',
    outputLabel: 'Risk report',
    emptyHint: '변경 파일을 지정하면 영향 경로, 위험 요약, 검토용 Markdown/JSON evidence를 생성합니다.',
    helperCards: [
      {
        tag: 'Risk cards',
        title: 'Release-ready impact summary',
        body: 'Markdown output is optimized for reviewer handoff; JSON is better for downstream automation.',
        tone: 'warning',
      },
      {
        tag: 'Evidence',
        title: 'Local graph + changes only',
        body: 'No backend mutation is required. The report is deterministic for the selected graph and change inputs.',
        tone: 'ok',
      },
    ],
  },
  'sql-view': {
    id: 'sql-view',
    label: 'Native SQL Evidence',
    shortLabel: 'SQL Evidence',
    eyebrow: 'SQL view analyzer',
    description:
      'Parse a Native SQL View file and extract structured evidence that supports lineage and review conversations.',
    endpoint: '/api/sql-view',
    runLabel: 'Analyze SQL evidence',
    outputLabel: 'SQL evidence',
    emptyHint: 'SQL view id and file path are ready with fixture defaults. Run to extract native SQL evidence.',
    helperCards: [
      {
        tag: 'Native SQL',
        title: 'Reviewer-readable evidence',
        body: 'Use Markdown for audit packets and JSON when another tool should consume parsed SQL metadata.',
        tone: 'info',
      },
      {
        tag: 'Boundary',
        title: 'No database execution',
        body: 'This workflow parses local SQL text; it does not execute SQL against BW or any database.',
        tone: 'ok',
      },
    ],
  },
  'field-lineage': {
    id: 'field-lineage',
    label: 'Field Lineage',
    shortLabel: 'Fields',
    eyebrow: 'Transformation mapping',
    description:
      'Inspect transformation XML and render source-to-target field lineage for object-level evidence reviews.',
    endpoint: '/api/field-lineage',
    runLabel: 'Render field lineage',
    outputLabel: 'Field mapping',
    emptyHint: 'Transformation fixture defaults are ready. Run to review source/target field mapping evidence.',
    helperCards: [
      {
        tag: 'Mapping',
        title: 'Source → target field paths',
        body: 'Pair this with object-level lineage to explain exactly which fields move through a transformation.',
        tone: 'accent',
      },
      {
        tag: 'Audit',
        title: 'Deterministic XML parsing',
        body: 'The parser uses the selected local transformation XML and returns stable Markdown or JSON evidence.',
        tone: 'ok',
      },
    ],
  },
};

const liveModeDetails: Record<LiveMode, { title: string; endpoint: string; body: string }> = {
  smoke: {
    title: 'Smoke only',
    endpoint: '/api/live/smoke',
    body: 'Health-oriented read-only calls for search, xref, and dataflow reachability.',
  },
  collect: {
    title: 'Collect snapshot',
    endpoint: '/api/collect/live',
    body: 'Writes local manifest evidence for offline lineage and review workflows.',
  },
  dataflow: {
    title: 'Render dataflow',
    endpoint: '/api/live/dataflow',
    body: 'Fetches one BW dataflow XML payload and renders JSON, Markdown, or Mermaid.',
  },
};

export default function App() {
  const [tool, setTool] = useState<Tool>('settings');
  const [form, setForm] = useState<FormState>(defaultState);
  const [settings, setSettings] = useState<SettingsState>(defaultSettings);
  const [runtimeConfig, setRuntimeConfig] = useState<RuntimeConfigResponse | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [result, setResult] = useState('');
  const [resultMeta, setResultMeta] = useState<ResultMeta | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [commandText, setCommandText] = useState('');
  const [copyStatus, setCopyStatus] = useState('');

  useEffect(() => {
    void refreshStatus();
  }, []);

  useEffect(() => {
    const options = formatOptionsFor(tool, form.liveMode);
    if (options.length > 0 && !options.includes(form.format)) {
      setForm((current) => ({ ...current, format: options[0] }));
    }
  }, [tool, form.liveMode, form.format]);

  const endpoint = useMemo(() => endpointForTool(tool, form.liveMode), [tool, form.liveMode]);
  const meta = toolCatalog[tool];
  const formatOptions = formatOptionsFor(tool, form.liveMode);
  const bwConfigured = runtimeConfig?.bw.configured ?? false;
  const llmConfigured = runtimeConfig?.llm.configured ?? false;
  const runDisabledReason = getRunDisabledReason(tool, form, busy, runtimeConfig);
  const quickStats = buildQuickStats(health, runtimeConfig);

  useEffect(() => {
    setResult('');
    setResultMeta(null);
    setError('');
    setCopyStatus('');
  }, [tool, form.liveMode]);

  async function refreshStatus() {
    try {
      const [healthResponse, configResponse] = await Promise.all([getHealth(), getRuntimeConfig()]);
      setHealth(healthResponse);
      setRuntimeConfig(configResponse);
      hydrateSettingsFromRedactedConfig(configResponse);
      setError('');
    } catch (err: unknown) {
      setError(`Backend 연결 실패: ${String(err)}`);
    }
  }

  function hydrateSettingsFromRedactedConfig(config: RuntimeConfigResponse) {
    setSettings((current) => ({
      ...current,
      bwUrl: config.bw.url ?? current.bwUrl,
      bwUser: config.bw.user ?? current.bwUser,
      bwClient: config.bw.client ?? current.bwClient,
      bwLanguage: config.bw.language ?? current.bwLanguage,
      bwVerifySsl: config.bw.verify_ssl,
      bwCaBundle: config.bw.ca_bundle ?? current.bwCaBundle,
      llmEnabled: config.llm.enabled,
      llmBaseUrl: config.llm.base_url ?? current.llmBaseUrl,
      llmModel: config.llm.model ?? current.llmModel,
    }));
  }

  function publishResult(
    content: string,
    options: { title: string; endpoint: string; format: string; mode: string },
  ) {
    setResult(content);
    setResultMeta({
      ...options,
      timestamp: new Date().toISOString(),
      summary: summarizeResult(content, options.format, options.title),
    });
    setCopyStatus('');
  }

  function beginExecution() {
    setBusy(true);
    setError('');
    setResult('');
    setResultMeta(null);
    setCopyStatus('');
  }

  async function runAnalysis() {
    if (tool === 'settings') {
      await saveSettings();
      return;
    }
    if (tool === 'live') {
      await runLiveAction();
      return;
    }
    beginExecution();
    try {
      const rendered = await postRendered(endpoint, buildRequest(tool, form));
      publishResult(rendered.content, {
        title: toolCatalog[tool].outputLabel,
        endpoint,
        format: rendered.format,
        mode: toolCatalog[tool].label,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function runLiveAction() {
    beginExecution();
    try {
      if (form.liveMode === 'smoke') {
        const payload = await postJson<LiveSmokeResponse>('/api/live/smoke', {
          confirm_read_only: form.liveConfirm,
          search_term: form.liveSearchTerm,
          object_name: form.liveObjectName || undefined,
          xref_direction: 'downstream',
          object_type: form.liveObjectType,
          source_system: form.liveSourceSystem || undefined,
          dataflow_direction: form.liveDataflowDirection,
          dataflow_levels: clampNumber(form.liveDataflowLevels, 3),
        });
        publishResult(JSON.stringify(payload, null, 2), {
          title: 'Live smoke response',
          endpoint: '/api/live/smoke',
          format: 'json',
          mode: liveModeDetails.smoke.title,
        });
      } else if (form.liveMode === 'collect') {
        const payload = await postJson<LiveCollectResponse>('/api/collect/live', {
          confirm_read_only: form.liveConfirm,
          out_dir: form.liveOutDir,
          search_terms: form.liveSearchTerm ? [form.liveSearchTerm] : [],
          object_names: form.liveObjectName ? [form.liveObjectName] : [],
          include_dataflow: true,
          include_xref: true,
          xref_direction: 'downstream',
          object_type: form.liveObjectType,
          source_system: form.liveSourceSystem || undefined,
          dataflow_direction: form.liveDataflowDirection,
          dataflow_levels: clampNumber(form.liveDataflowLevels, 3),
        });
        publishResult(JSON.stringify(payload, null, 2), {
          title: 'Live collection manifest',
          endpoint: '/api/collect/live',
          format: 'json',
          mode: liveModeDetails.collect.title,
        });
      } else {
        const rendered = await postRendered('/api/live/dataflow', {
          confirm_read_only: form.liveConfirm,
          object_name: form.liveObjectName,
          object_type: form.liveObjectType,
          source_system: form.liveSourceSystem || undefined,
          direction: form.liveDataflowDirection,
          levels: clampNumber(form.liveDataflowLevels, 3),
          format: form.format,
        });
        publishResult(rendered.content, {
          title: 'Live dataflow render',
          endpoint: '/api/live/dataflow',
          format: rendered.format,
          mode: liveModeDetails.dataflow.title,
        });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function saveSettings() {
    beginExecution();
    try {
      const body = {
        bw: settings.bwUrl
          ? {
              url: settings.bwUrl,
              user: settings.bwUser,
              password: settings.bwPassword,
              client: settings.bwClient,
              language: settings.bwLanguage,
              verify_ssl: settings.bwVerifySsl,
              ca_bundle: settings.bwCaBundle || undefined,
            }
          : undefined,
        llm: {
          enabled: settings.llmEnabled,
          base_url: settings.llmEnabled ? settings.llmBaseUrl : undefined,
          model: settings.llmEnabled ? settings.llmModel : undefined,
          api_key: settings.llmEnabled ? settings.llmApiKey : undefined,
        },
      };
      const config = await putRuntimeConfig(body);
      setRuntimeConfig(config);
      setSettings((current) => ({ ...current, bwPassword: '', llmApiKey: '' }));
      publishResult(
        'Runtime settings saved in backend process memory only. Secret fields were cleared from the form and were not returned by the API.',
        {
          title: 'Runtime settings saved',
          endpoint: '/api/runtime-config',
          format: 'status',
          mode: 'Settings',
        },
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function clearSettings() {
    beginExecution();
    try {
      const config = await clearRuntimeConfig();
      setRuntimeConfig(config);
      setSettings(defaultSettings);
      publishResult('Runtime settings cleared from backend process memory.', {
        title: 'Runtime settings cleared',
        endpoint: '/api/runtime-config',
        format: 'status',
        mode: 'Settings',
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function updateSettings<K extends keyof SettingsState>(key: K, value: SettingsState[K]) {
    setSettings((current) => ({ ...current, [key]: value }));
  }

  function applyGlobalCommand() {
    const value = commandText.trim();
    if (!value) return;
    if (tool === 'live') {
      if (value.includes('*') || value.includes('?')) {
        update('liveSearchTerm', value);
      } else {
        update('liveObjectName', value);
      }
    } else if (tool === 'lineage') {
      update('objectId', value);
    } else if (tool === 'sql-view') {
      update('viewId', value);
    } else if (tool === 'field-lineage') {
      update('transformationId', value);
    } else if (tool === 'impact') {
      update('changesPath', value);
    } else if (value.startsWith('http://') || value.startsWith('https://')) {
      updateSettings('bwUrl', value);
    }
    setCommandText('');
  }

  async function copyResult() {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(result);
      setCopyStatus('Copied');
    } catch {
      setCopyStatus('Copy failed');
    }
  }

  function downloadResult() {
    if (!result) return;
    const extension = extensionForFormat(resultMeta?.format ?? form.format);
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    const blob = new Blob([result], { type: mimeForFormat(resultMeta?.format ?? form.format) });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `bw-lineage-impact-${tool}-${stamp}.${extension}`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  return (
    <main className="appShell">
      <TopStatusBar
        bwConfigured={bwConfigured}
        commandText={commandText}
        health={health}
        llmConfigured={llmConfigured}
        onApplyCommand={applyGlobalCommand}
        onCommandTextChange={setCommandText}
        onRefresh={() => void refreshStatus()}
        placeholder={placeholderForTool(tool)}
        runtimeConfig={runtimeConfig}
      />

      <div className="appFrame">
        <SideNavigation
          activeTool={tool}
          form={form}
          health={health}
          onSelectTool={setTool}
          runtimeConfig={runtimeConfig}
        />

        <section className="mainStage" aria-label="BW Lineage Impact workbench">
          <WorkbenchHero
            endpoint={endpoint}
            meta={meta}
            quickStats={quickStats}
            status={readinessForTool(tool, runtimeConfig, health, form)}
          />

          <div className="workspaceGrid">
            <section className="workbenchPanel controlPanel">
              <PanelTitle
                kicker={tool === 'settings' ? 'Secure configuration' : 'Execution controls'}
                rightSlot={
                  formatOptions.length > 0 ? (
                    <FormatSelect
                      format={form.format}
                      onChange={(value) => update('format', value)}
                      options={formatOptions}
                    />
                  ) : null
                }
                title={tool === 'settings' ? 'Runtime configuration workspace' : meta.label}
              />

              {tool === 'settings' ? (
                <SettingsPanel
                  busy={busy}
                  onClear={() => void clearSettings()}
                  onSave={() => void saveSettings()}
                  runtimeConfig={runtimeConfig}
                  settings={settings}
                  updateSettings={updateSettings}
                />
              ) : null}

              {tool === 'live' ? <LivePanel form={form} update={update} /> : null}

              {tool !== 'settings' && tool !== 'live' ? (
                <LocalAnalysisPanel form={form} tool={tool} update={update} />
              ) : null}

              {tool !== 'settings' ? (
                <div className="runDock">
                  <button
                    className="primaryButton"
                    disabled={Boolean(runDisabledReason)}
                    onClick={() => void runAnalysis()}
                    type="button"
                  >
                    {busy ? '실행 중…' : meta.runLabel}
                  </button>
                  {runDisabledReason && !busy ? <p className="runHint">{runDisabledReason}</p> : null}
                </div>
              ) : null}
            </section>

            <ResultViewer
              activeTool={tool}
              busy={busy}
              copyStatus={copyStatus}
              error={error}
              meta={resultMeta}
              onCopy={() => void copyResult()}
              onDownload={downloadResult}
              result={result}
              toolMeta={meta}
            />
          </div>

          <WorkflowSupport meta={meta} tool={tool} />
        </section>
      </div>
    </main>
  );
}

function TopStatusBar({
  bwConfigured,
  commandText,
  health,
  llmConfigured,
  onApplyCommand,
  onCommandTextChange,
  onRefresh,
  placeholder,
  runtimeConfig,
}: {
  bwConfigured: boolean;
  commandText: string;
  health: HealthResponse | null;
  llmConfigured: boolean;
  onApplyCommand: () => void;
  onCommandTextChange: (value: string) => void;
  onRefresh: () => void;
  placeholder: string;
  runtimeConfig: RuntimeConfigResponse | null;
}) {
  const backendOk = health?.status === 'ok';
  return (
    <header className="topBar">
      <div className="brandBlock" aria-label="Application identity">
        <div className="brandMark">BW</div>
        <div>
          <strong>BW Lineage Impact</strong>
          <span>Local-first read-only analyzer</span>
        </div>
      </div>

      <div className="commandBar">
        <span className="commandGlyph">⌘</span>
        <input
          aria-label="Global object or file command"
          onChange={(event) => onCommandTextChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') onApplyCommand();
          }}
          placeholder={placeholder}
          value={commandText}
        />
        <button onClick={onApplyCommand} type="button">
          Apply
        </button>
      </div>

      <div className="statusCluster" aria-label="Global runtime status">
        <StatusPill label="Backend" tone={backendOk ? 'ok' : 'warning'} value={health?.status ?? 'checking'} />
        <StatusPill label="BW" tone={bwConfigured ? 'ok' : 'neutral'} value={bwConfigured ? 'configured' : 'not set'} />
        <StatusPill label="LLM" tone={llmConfigured ? 'accent' : 'neutral'} value={llmConfigured ? 'ready' : 'off'} />
        <StatusPill
          label="Mode"
          tone={health ? (health.read_only === false ? 'danger' : 'ok') : 'neutral'}
          value={health ? (health.read_only === false ? 'write?' : 'read-only') : 'checking'}
        />
        <button className="ghostButton" onClick={onRefresh} type="button">
          Refresh
        </button>
        <span className="storageChip">{runtimeConfig?.storage ?? 'process-memory'}</span>
      </div>
    </header>
  );
}

function SideNavigation({
  activeTool,
  form,
  health,
  onSelectTool,
  runtimeConfig,
}: {
  activeTool: Tool;
  form: FormState;
  health: HealthResponse | null;
  onSelectTool: (tool: Tool) => void;
  runtimeConfig: RuntimeConfigResponse | null;
}) {
  return (
    <aside className="sideNav" aria-label="Analysis modules">
      <div className="navIntro">
        <span className="eyebrow">Enterprise modules</span>
        <h2>Workbench</h2>
        <p>실제 검토 흐름 중심의 BW lineage, impact, evidence tools.</p>
      </div>
      <nav className="navList">
        {toolOrder.map((item) => {
          const meta = toolCatalog[item];
          const readiness = readinessForTool(item, runtimeConfig, health, form);
          return (
            <button
              aria-current={item === activeTool ? 'page' : undefined}
              className={item === activeTool ? 'navItem active' : 'navItem'}
              key={item}
              onClick={() => onSelectTool(item)}
              type="button"
            >
              <span className={`navState ${readiness.tone}`} />
              <span>
                <strong>{meta.shortLabel}</strong>
                <small>{meta.description}</small>
              </span>
              <em>{readiness.label}</em>
            </button>
          );
        })}
      </nav>
      <div className="readOnlyCard">
        <span className="lockIcon">▣</span>
        <strong>Read-only safety</strong>
        <p>Live BW actions require explicit confirmation and runtime credentials remain process-memory only.</p>
      </div>
    </aside>
  );
}

function WorkbenchHero({
  endpoint,
  meta,
  quickStats,
  status,
}: {
  endpoint: string;
  meta: ToolMeta;
  quickStats: Array<{ label: string; value: string; tone: Tone; detail: string }>;
  status: { label: string; tone: Tone };
}) {
  return (
    <section className="heroPanel">
      <div className="heroCopy">
        <span className="eyebrow">{meta.eyebrow}</span>
        <div className="heroTitleRow">
          <h1>{meta.label}</h1>
          <StatusPill label="Module" tone={status.tone} value={status.label} />
        </div>
        <p>{meta.description}</p>
        <div className="endpointLine">
          <span>Endpoint</span>
          <code>{endpoint}</code>
        </div>
      </div>
      <div className="metricGrid" aria-label="Runtime quick stats">
        {quickStats.map((stat) => (
          <div className={`metricCard ${stat.tone}`} key={stat.label}>
            <span>{stat.label}</span>
            <strong>{stat.value}</strong>
            <small>{stat.detail}</small>
          </div>
        ))}
      </div>
    </section>
  );
}

function PanelTitle({
  kicker,
  rightSlot,
  title,
}: {
  kicker: string;
  rightSlot?: ReactNode;
  title: string;
}) {
  return (
    <div className="panelTitle">
      <div>
        <span className="panelKicker">{kicker}</span>
        <h2>{title}</h2>
      </div>
      {rightSlot}
    </div>
  );
}

function SettingsPanel({
  busy,
  settings,
  runtimeConfig,
  onClear,
  onSave,
  updateSettings,
}: {
  busy: boolean;
  settings: SettingsState;
  runtimeConfig: RuntimeConfigResponse | null;
  onClear: () => void;
  onSave: () => void;
  updateSettings: <K extends keyof SettingsState>(key: K, value: SettingsState[K]) => void;
}) {
  return (
    <div className="settingsStack">
      <div className="secureNotice">
        <span>Process-memory only</span>
        <p>
          입력값은 로컬 백엔드 프로세스 메모리에만 보관됩니다. 파일/env/browser storage에 저장하지 않고,
          secret 값은 API 응답에 반환되지 않습니다.
        </p>
      </div>

      <div className="runtimeSummaryGrid">
        <SummaryTile label="BW" tone={runtimeConfig?.bw.configured ? 'ok' : 'neutral'} value={runtimeConfig?.bw.configured ? 'configured' : 'not configured'} />
        <SummaryTile label="LLM" tone={runtimeConfig?.llm.configured ? 'accent' : 'neutral'} value={runtimeConfig?.llm.configured ? 'configured' : 'off'} />
        <SummaryTile label="Storage" tone="ok" value={runtimeConfig?.storage ?? 'process-memory'} />
      </div>

      <section className="formSection">
        <div className="sectionHeading">
          <h3>BW runtime</h3>
          <span>read-only SAP BW metadata access</span>
        </div>
        <Field
          autoComplete="off"
          label="BW_URL"
          onChange={(v) => updateSettings('bwUrl', v)}
          placeholder="https://bw.example.internal"
          value={settings.bwUrl}
        />
        <Field
          autoComplete="off"
          label="BW_USER"
          onChange={(v) => updateSettings('bwUser', v)}
          placeholder="read-only technical user"
          value={settings.bwUser}
        />
        <Field
          autoComplete="new-password"
          hint="Write-only. The field clears immediately after Save."
          inputType="password"
          label="BW_PASSWORD"
          onChange={(v) => updateSettings('bwPassword', v)}
          value={settings.bwPassword}
        />
        <div className="twoColumn">
          <Field label="BW_CLIENT" onChange={(v) => updateSettings('bwClient', v)} value={settings.bwClient} />
          <Field label="BW_LANGUAGE" onChange={(v) => updateSettings('bwLanguage', v)} value={settings.bwLanguage} />
        </div>
        <Checkbox
          checked={settings.bwVerifySsl}
          label="Verify SSL certificates"
          onChange={(value) => updateSettings('bwVerifySsl', value)}
        />
        <Field
          autoComplete="off"
          hint="Optional local corporate CA bundle path; never required for fixture-only workflows."
          label="BW_CA_BUNDLE"
          onChange={(v) => updateSettings('bwCaBundle', v)}
          placeholder="optional PEM path"
          value={settings.bwCaBundle}
        />
      </section>

      <section className="formSection">
        <div className="sectionHeading">
          <h3>Local OpenAI-compatible LLM</h3>
          <span>optional explanation layer</span>
        </div>
        <Checkbox
          checked={settings.llmEnabled}
          label="Enable optional local LLM explainer"
          onChange={(value) => updateSettings('llmEnabled', value)}
        />
        <Field
          label="BWLI_LLM_BASE_URL"
          onChange={(v) => updateSettings('llmBaseUrl', v)}
          value={settings.llmBaseUrl}
        />
        <Field label="BWLI_LLM_MODEL" onChange={(v) => updateSettings('llmModel', v)} value={settings.llmModel} />
        <Field
          autoComplete="new-password"
          hint="Write-only. No token is returned by the runtime-config API."
          inputType="password"
          label="BWLI_LLM_API_KEY"
          onChange={(v) => updateSettings('llmApiKey', v)}
          value={settings.llmApiKey}
        />
      </section>

      <div className="buttonRow">
        <button className="primaryButton" disabled={busy} onClick={onSave} type="button">
          {busy ? '저장 중…' : 'Save runtime settings'}
        </button>
        <button className="secondaryButton" disabled={busy} onClick={onClear} type="button">
          Clear process memory
        </button>
      </div>
    </div>
  );
}

function LivePanel({
  form,
  update,
}: {
  form: FormState;
  update: <K extends keyof FormState>(key: K, value: FormState[K]) => void;
}) {
  return (
    <div className="settingsStack">
      <div className={form.liveConfirm ? 'confirmCard confirmed' : 'confirmCard'}>
        <Checkbox
          checked={form.liveConfirm}
          label="I confirm this is a read-only live SAP BW metadata call"
          onChange={(value) => update('liveConfirm', value)}
        />
        <p>Runtime Settings의 BW 계정을 사용하며 metadata GET/read-only flow만 실행합니다.</p>
      </div>

      <div className="modeGrid" role="list" aria-label="Live mode selector">
        {(Object.keys(liveModeDetails) as LiveMode[]).map((mode) => (
          <button
            className={form.liveMode === mode ? 'modeCard active' : 'modeCard'}
            key={mode}
            onClick={() => update('liveMode', mode)}
            type="button"
          >
            <span>{liveModeDetails[mode].endpoint}</span>
            <strong>{liveModeDetails[mode].title}</strong>
            <small>{liveModeDetails[mode].body}</small>
          </button>
        ))}
      </div>

      <section className="formSection">
        <div className="sectionHeading">
          <h3>Object search</h3>
          <span>supports smoke, xref, collect and dataflow inputs</span>
        </div>
        <Field
          hint="Wildcard search term for smoke/collect. Example: Z*"
          label="Search term"
          onChange={(v) => update('liveSearchTerm', v)}
          value={form.liveSearchTerm}
        />
        <Field
          hint={form.liveMode === 'dataflow' ? 'Required for live dataflow rendering.' : 'Optional exact object name.'}
          label="Object name"
          onChange={(v) => update('liveObjectName', v)}
          placeholder="e.g. ZSALES_ADSO"
          value={form.liveObjectName}
        />
        <div className="twoColumn">
          <Field label="Object type" onChange={(v) => update('liveObjectType', v)} value={form.liveObjectType} />
          <Field
            hint="For RSDS-style source-specific dataflows."
            label="Source system"
            onChange={(v) => update('liveSourceSystem', v)}
            placeholder="optional"
            value={form.liveSourceSystem}
          />
        </div>
      </section>

      <section className="formSection compactSection">
        <div className="sectionHeading">
          <h3>Dataflow controls</h3>
          <span>direction and traversal limit</span>
        </div>
        <div className="twoColumn">
          <SelectField
            label="Direction"
            onChange={(value) => update('liveDataflowDirection', value as FormState['liveDataflowDirection'])}
            options={[
              { value: 'downwards', label: 'downwards' },
              { value: 'upwards', label: 'upwards' },
              { value: 'both', label: 'both' },
            ]}
            value={form.liveDataflowDirection}
          />
          <Field
            inputMode="numeric"
            label="Levels"
            onChange={(v) => update('liveDataflowLevels', v)}
            value={form.liveDataflowLevels}
          />
        </div>
        {form.liveMode === 'collect' ? (
          <Field label="Output directory" onChange={(v) => update('liveOutDir', v)} value={form.liveOutDir} />
        ) : null}
      </section>
    </div>
  );
}

function LocalAnalysisPanel({
  form,
  tool,
  update,
}: {
  form: FormState;
  tool: AnalysisTool;
  update: <K extends keyof FormState>(key: K, value: FormState[K]) => void;
}) {
  if (tool === 'lineage') {
    return (
      <div className="settingsStack">
        <section className="formSection">
          <div className="sectionHeading">
            <h3>Graph traversal</h3>
            <span>local snapshot evidence</span>
          </div>
          <Field label="Graph JSON path" onChange={(v) => update('graphPath', v)} value={form.graphPath} />
          <Field label="Start object id" onChange={(v) => update('objectId', v)} value={form.objectId} />
          <div className="twoColumn">
            <SelectField
              label="Direction"
              onChange={(value) => update('lineageDirection', value as FormState['lineageDirection'])}
              options={[
                { value: 'downstream', label: 'downstream' },
                { value: 'upstream', label: 'upstream' },
                { value: 'both', label: 'both' },
              ]}
              value={form.lineageDirection}
            />
            <Field
              inputMode="numeric"
              label="Max depth"
              onChange={(v) => update('lineageMaxDepth', v)}
              value={form.lineageMaxDepth}
            />
          </div>
        </section>
      </div>
    );
  }

  if (tool === 'impact') {
    return (
      <div className="settingsStack">
        <section className="formSection">
          <div className="sectionHeading">
            <h3>Change-impact review</h3>
            <span>risk and affected-object evidence</span>
          </div>
          <Field label="Graph JSON path" onChange={(v) => update('graphPath', v)} value={form.graphPath} />
          <Field label="Changes JSON path" onChange={(v) => update('changesPath', v)} value={form.changesPath} />
          <Field
            inputMode="numeric"
            label="Max depth"
            onChange={(v) => update('impactMaxDepth', v)}
            value={form.impactMaxDepth}
          />
        </section>
      </div>
    );
  }

  if (tool === 'sql-view') {
    return (
      <div className="settingsStack">
        <section className="formSection">
          <div className="sectionHeading">
            <h3>Native SQL evidence</h3>
            <span>local SQL text parsing only</span>
          </div>
          <Field label="Native SQL View id" onChange={(v) => update('viewId', v)} value={form.viewId} />
          <Field label="SQL file path" onChange={(v) => update('sqlFile', v)} value={form.sqlFile} />
        </section>
      </div>
    );
  }

  return (
    <div className="settingsStack">
      <section className="formSection">
        <div className="sectionHeading">
          <h3>Transformation field mapping</h3>
          <span>source-to-target mapping evidence</span>
        </div>
        <Field label="Transformation XML path" onChange={(v) => update('xmlFile', v)} value={form.xmlFile} />
        <Field label="Transformation id" onChange={(v) => update('transformationId', v)} value={form.transformationId} />
        <div className="twoColumn">
          <Field label="Source object" onChange={(v) => update('sourceObject', v)} value={form.sourceObject} />
          <Field label="Target object" onChange={(v) => update('targetObject', v)} value={form.targetObject} />
        </div>
      </section>
    </div>
  );
}

function ResultViewer({
  activeTool,
  busy,
  copyStatus,
  error,
  meta,
  onCopy,
  onDownload,
  result,
  toolMeta,
}: {
  activeTool: Tool;
  busy: boolean;
  copyStatus: string;
  error: string;
  meta: ResultMeta | null;
  onCopy: () => void;
  onDownload: () => void;
  result: string;
  toolMeta: ToolMeta;
}) {
  const visibleResult = !busy && !error ? result : '';
  const visibleMeta = !busy && !error ? meta : null;
  const lineCount = visibleResult ? visibleResult.split('\n').length : 0;
  const isMermaid = (visibleMeta?.format ?? '') === 'mermaid';
  return (
    <section className="workbenchPanel resultPanel" aria-label="Result viewer">
      <PanelTitle
        kicker="Evidence viewer"
        rightSlot={
          <div className="resultActions">
            <button className="secondaryButton compact" disabled={!visibleResult} onClick={onCopy} type="button">
              {copyStatus || 'Copy'}
            </button>
            <button className="secondaryButton compact" disabled={!visibleResult} onClick={onDownload} type="button">
              Download
            </button>
          </div>
        }
        title={toolMeta.outputLabel}
      />

      <div className="resultMetaBar">
        <span className={`formatBadge ${visibleMeta?.format ?? 'idle'}`}>{visibleMeta?.format ?? 'idle'}</span>
        <span>{visibleMeta?.endpoint ?? toolMeta.endpoint}</span>
        <span>{visibleMeta ? formatTimestamp(visibleMeta.timestamp) : 'not run yet'}</span>
        {visibleResult ? <span>{lineCount} lines</span> : null}
      </div>

      {busy ? (
        <div className="resultState loadingState">
          <span className="spinner" />
          <strong>Running {toolCatalog[activeTool].label}</strong>
          <p>요청을 실행하고 evidence payload를 기다리는 중입니다.</p>
        </div>
      ) : error ? (
        <div className="resultState errorState" role="alert">
          <strong>Execution failed</strong>
          <p>{error}</p>
        </div>
      ) : visibleResult ? (
        <>
          <div className="executionSummary">
            <span>{visibleMeta?.mode ?? toolMeta.label}</span>
            <strong>{visibleMeta?.summary ?? 'Result ready'}</strong>
          </div>
          {isMermaid ? (
            <div className="mermaidNotice">
              <span>Mermaid source preview</span>
              <p>Plain React/CSS mode keeps dependencies light. Copy or download this source into Mermaid-compatible review docs.</p>
            </div>
          ) : null}
          <pre className={isMermaid ? 'resultPre mermaidSource' : 'resultPre'}>{visibleResult}</pre>
        </>
      ) : (
        <EmptyResult hint={toolMeta.emptyHint} meta={toolMeta} />
      )}
    </section>
  );
}

function EmptyResult({ hint, meta }: { hint: string; meta: ToolMeta }) {
  return (
    <div className="resultState emptyState">
      <span className="emptyIcon">◇</span>
      <strong>No output yet</strong>
      <p>{hint}</p>
      <div className="emptyChecklist">
        <span>1. Confirm inputs</span>
        <span>2. Run {meta.shortLabel}</span>
        <span>3. Copy/download evidence</span>
      </div>
    </div>
  );
}

function WorkflowSupport({ meta, tool }: { meta: ToolMeta; tool: Tool }) {
  const checklist = checklistForTool(tool);
  return (
    <section className="supportGrid" aria-label="Workflow support panels">
      {meta.helperCards.map((card) => (
        <article className={`helperCard ${card.tone ?? 'neutral'}`} key={`${card.tag}-${card.title}`}>
          <span>{card.tag}</span>
          <h3>{card.title}</h3>
          <p>{card.body}</p>
        </article>
      ))}
      <article className="helperCard evidenceCard">
        <span>Execution summary</span>
        <h3>{meta.label} checklist</h3>
        <ol>
          {checklist.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ol>
      </article>
    </section>
  );
}

function FormatSelect({
  format,
  onChange,
  options,
}: {
  format: OutputFormat;
  onChange: (format: OutputFormat) => void;
  options: OutputFormat[];
}) {
  return (
    <label className="formatSelect">
      <span>Output</span>
      <select value={format} onChange={(event) => onChange(event.target.value as OutputFormat)}>
        {options.map((option) => (
          <option key={option} value={option}>
            {formatLabel(option)}
          </option>
        ))}
      </select>
    </label>
  );
}

function Field({
  autoComplete,
  hint,
  inputMode,
  inputType = 'text',
  label,
  onChange,
  placeholder,
  value,
}: {
  autoComplete?: string;
  hint?: string;
  inputMode?: 'text' | 'numeric' | 'decimal' | 'search' | 'email' | 'tel' | 'url';
  inputType?: 'text' | 'password';
  label: string;
  onChange: (value: string) => void;
  placeholder?: string;
  value: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input
        autoComplete={autoComplete}
        inputMode={inputMode}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        type={inputType}
        value={value}
      />
      {hint ? <small>{hint}</small> : null}
    </label>
  );
}

function SelectField({
  hint,
  label,
  onChange,
  options,
  value,
}: {
  hint?: string;
  label: string;
  onChange: (value: string) => void;
  options: Array<{ value: string; label: string }>;
  value: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {hint ? <small>{hint}</small> : null}
    </label>
  );
}

function Checkbox({
  checked,
  label,
  onChange,
}: {
  checked: boolean;
  label: string;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="checkboxRow">
      <input checked={checked} onChange={(event) => onChange(event.target.checked)} type="checkbox" />
      <span>{label}</span>
    </label>
  );
}

function StatusPill({ label, tone, value }: { label: string; tone: Tone; value: string }) {
  return (
    <span className={`statusPill ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </span>
  );
}

function SummaryTile({ label, tone, value }: { label: string; tone: Tone; value: string }) {
  return (
    <div className={`summaryTile ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function endpointForTool(tool: Tool, liveMode: LiveMode): string {
  if (tool === 'live') return liveModeDetails[liveMode].endpoint;
  return toolCatalog[tool].endpoint;
}

function formatOptionsFor(tool: Tool, liveMode: LiveMode): OutputFormat[] {
  if (tool === 'settings') return [];
  if (tool === 'live') return liveMode === 'dataflow' ? ['mermaid', 'md', 'json'] : [];
  if (tool === 'lineage') return ['md', 'json', 'mermaid'];
  return ['md', 'json'];
}

function buildRequest(tool: AnalysisTool, form: FormState): unknown {
  if (tool === 'lineage') {
    return {
      graph_path: form.graphPath,
      object_id: form.objectId,
      direction: form.lineageDirection,
      max_depth: clampNumber(form.lineageMaxDepth, 3),
      format: form.format,
    };
  }
  if (tool === 'impact') {
    return {
      graph_path: form.graphPath,
      changes_path: form.changesPath,
      max_depth: clampNumber(form.impactMaxDepth, 3),
      format: form.format === 'mermaid' ? 'md' : form.format,
    };
  }
  if (tool === 'sql-view') {
    return {
      view_id: form.viewId,
      sql_file: form.sqlFile,
      format: form.format === 'mermaid' ? 'md' : form.format,
    };
  }
  return {
    xml_file: form.xmlFile,
    transformation_id: form.transformationId,
    source_object: form.sourceObject,
    target_object: form.targetObject,
    format: form.format === 'mermaid' ? 'md' : form.format,
  };
}

function getRunDisabledReason(
  tool: Tool,
  form: FormState,
  busy: boolean,
  runtimeConfig: RuntimeConfigResponse | null,
): string {
  if (busy) return 'Execution in progress.';
  if (tool !== 'live') return '';
  if (!runtimeConfig) return 'Runtime status loading.';
  if (!runtimeConfig.bw.configured) return 'Runtime Settings에서 BW runtime을 먼저 저장하세요.';
  if (!form.liveConfirm) return 'Live BW 실행 전 read-only confirmation을 체크하세요.';
  if (form.liveMode === 'dataflow' && !form.liveObjectName.trim()) {
    return 'Dataflow rendering requires an object name.';
  }
  return '';
}

function readinessForTool(
  tool: Tool,
  runtimeConfig: RuntimeConfigResponse | null,
  health: HealthResponse | null,
  form: FormState,
): { label: string; tone: Tone } {
  if (tool === 'settings') {
    return runtimeConfig ? { label: 'synced', tone: 'ok' } : { label: 'syncing', tone: 'neutral' };
  }
  if (health && health.status !== 'ok') return { label: 'backend', tone: 'warning' };
  if (tool === 'live') {
    if (!runtimeConfig?.bw.configured) return { label: 'needs BW', tone: 'warning' };
    if (!form.liveConfirm) return { label: 'confirm', tone: 'warning' };
    return { label: 'ready', tone: 'ok' };
  }
  return { label: 'ready', tone: 'ok' };
}

function buildQuickStats(
  health: HealthResponse | null,
  runtimeConfig: RuntimeConfigResponse | null,
): Array<{ label: string; value: string; tone: Tone; detail: string }> {
  return [
    {
      label: 'Backend',
      value: health?.status ?? 'checking',
      tone: health ? (health.status === 'ok' ? 'ok' : 'warning') : 'neutral',
      detail: health?.version ? `v${health.version}` : 'waiting for /api/health',
    },
    {
      label: 'BW runtime',
      value: runtimeConfig?.bw.configured ? 'configured' : 'not set',
      tone: runtimeConfig?.bw.configured ? 'ok' : 'neutral',
      detail: runtimeConfig?.bw.client ? `client ${runtimeConfig.bw.client}` : 'process-memory config',
    },
    {
      label: 'Safety',
      value: health ? (health.read_only === false || health.local_only === false ? 'check' : 'read-only') : 'checking',
      tone: health ? (health.read_only === false || health.local_only === false ? 'danger' : 'ok') : 'neutral',
      detail: health ? (health.local_only === false ? 'remote mode reported' : 'local-only API') : 'waiting for /api/health',
    },
    {
      label: 'LLM',
      value: runtimeConfig?.llm.configured ? 'ready' : 'off',
      tone: runtimeConfig?.llm.configured ? 'accent' : 'neutral',
      detail: runtimeConfig?.llm.model ?? 'optional explainer',
    },
  ];
}

function summarizeResult(content: string, format: string, fallback: string): string {
  if (!content.trim()) return fallback;
  if (format === 'json') {
    try {
      const parsed = JSON.parse(content) as unknown;
      if (isRecord(parsed)) {
        if (Array.isArray(parsed.operations)) {
          const okCount = parsed.operations.filter((item) => isRecord(item) && item.ok === true).length;
          return `${parsed.operations.length} smoke operations · ${okCount} ok`;
        }
        if (typeof parsed.manifest_path === 'string') return `Manifest written: ${parsed.manifest_path}`;
        if (typeof parsed.node_count === 'number' && typeof parsed.edge_count === 'number') {
          return `${parsed.node_count} nodes · ${parsed.edge_count} edges`;
        }
        if (Array.isArray(parsed.nodes) && Array.isArray(parsed.edges)) {
          return `${parsed.nodes.length} nodes · ${parsed.edges.length} edges`;
        }
      }
    } catch {
      return fallback;
    }
  }
  if (format === 'mermaid') {
    const edges = content.split('\n').filter((line) => line.includes('-->')).length;
    return `Mermaid graph source · ${edges} edges`;
  }
  const firstHeading = content
    .split('\n')
    .map((line) => line.trim())
    .find((line) => line.startsWith('#'));
  return firstHeading ? firstHeading.replace(/^#+\s*/, '') : fallback;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function formatLabel(format: OutputFormat): string {
  if (format === 'md') return 'Markdown';
  if (format === 'json') return 'JSON';
  return 'Mermaid';
}

function formatTimestamp(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(value));
}

function placeholderForTool(tool: Tool): string {
  if (tool === 'settings') return 'Paste a BW URL to stage it in Runtime Settings';
  if (tool === 'live') return 'Search Z* or set exact object name';
  if (tool === 'lineage') return 'Jump to object id, e.g. SRC';
  if (tool === 'impact') return 'Set changes JSON path';
  if (tool === 'sql-view') return 'Set Native SQL View id';
  return 'Set transformation id';
}

function checklistForTool(tool: Tool): string[] {
  if (tool === 'settings') {
    return ['Enter only runtime values needed for this process.', 'Save clears secret fields from the browser form.', 'Use Clear to remove backend process-memory state.'];
  }
  if (tool === 'live') {
    return ['Confirm read-only safety before every live run.', 'Use Smoke for connection health before Collect or Dataflow.', 'Copy or download the response for review evidence.'];
  }
  if (tool === 'lineage') {
    return ['Select graph path, object id, direction, and depth.', 'Use Mermaid for graph-source review or Markdown for handoff.', 'Keep fixture runs as a baseline before live snapshots.'];
  }
  if (tool === 'impact') {
    return ['Pair graph snapshot with changes JSON.', 'Review risk summary and affected object paths.', 'Export Markdown for release review packets.'];
  }
  if (tool === 'sql-view') {
    return ['Set SQL view id and local SQL file.', 'Parse without database execution.', 'Attach Markdown/JSON evidence to lineage review.'];
  }
  return ['Select transformation XML and source/target objects.', 'Render field mapping evidence.', 'Use output with lineage and impact reports.'];
}

function extensionForFormat(format: string): string {
  if (format === 'json') return 'json';
  if (format === 'mermaid') return 'mmd';
  if (format === 'status') return 'txt';
  return 'md';
}

function mimeForFormat(format: string): string {
  if (format === 'json') return 'application/json;charset=utf-8';
  if (format === 'mermaid' || format === 'md') return 'text/markdown;charset=utf-8';
  return 'text/plain;charset=utf-8';
}

function clampNumber(value: string, fallback: number): number {
  const trimmed = value.trim();
  if (!trimmed) return fallback;
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(0, Math.min(20, Math.trunc(parsed)));
}
