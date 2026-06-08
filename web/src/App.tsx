import { useEffect, useMemo, useState } from 'react';
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
type LiveMode = 'smoke' | 'collect' | 'dataflow';

interface FormState {
  graphPath: string;
  changesPath: string;
  objectId: string;
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

const defaultState: FormState = {
  graphPath: 'tests/fixtures/sample-graph.json',
  changesPath: 'tests/fixtures/sample-changes.json',
  objectId: 'SRC',
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

const toolLabels: Record<Tool, string> = {
  settings: 'Runtime Settings',
  live: 'Live BW Smoke',
  lineage: 'Lineage',
  impact: 'Change Impact',
  'sql-view': 'Native SQL View',
  'field-lineage': 'Field Lineage',
};

export default function App() {
  const [tool, setTool] = useState<Tool>('settings');
  const [form, setForm] = useState<FormState>(defaultState);
  const [settings, setSettings] = useState<SettingsState>(defaultSettings);
  const [runtimeConfig, setRuntimeConfig] = useState<RuntimeConfigResponse | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [result, setResult] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void refreshStatus();
  }, []);

  const endpoint = useMemo(() => `/api/${tool}`, [tool]);

  async function refreshStatus() {
    try {
      const [healthResponse, configResponse] = await Promise.all([getHealth(), getRuntimeConfig()]);
      setHealth(healthResponse);
      setRuntimeConfig(configResponse);
      hydrateSettingsFromRedactedConfig(configResponse);
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

  async function runAnalysis() {
    if (tool === 'settings') {
      await saveSettings();
      return;
    }
    if (tool === 'live') {
      await runLiveAction();
      return;
    }
    setBusy(true);
    setError('');
    try {
      const rendered = await postRendered(endpoint, buildRequest(tool, form));
      setResult(rendered.content);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function runLiveAction() {
    setBusy(true);
    setError('');
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
          dataflow_levels: Number(form.liveDataflowLevels || 3),
        });
        setResult(JSON.stringify(payload, null, 2));
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
          dataflow_levels: Number(form.liveDataflowLevels || 3),
        });
        setResult(JSON.stringify(payload, null, 2));
      } else {
        const rendered = await postRendered('/api/live/dataflow', {
          confirm_read_only: form.liveConfirm,
          object_name: form.liveObjectName,
          object_type: form.liveObjectType,
          source_system: form.liveSourceSystem || undefined,
          direction: form.liveDataflowDirection,
          levels: Number(form.liveDataflowLevels || 3),
          format: form.format,
        });
        setResult(rendered.content);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function saveSettings() {
    setBusy(true);
    setError('');
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
      setResult(
        'Runtime settings saved in backend process memory only. Secrets were not returned by the API.',
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function clearSettings() {
    setBusy(true);
    setError('');
    try {
      const config = await clearRuntimeConfig();
      setRuntimeConfig(config);
      setSettings(defaultSettings);
      setResult('Runtime settings cleared from backend process memory.');
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

  const bwConfigured = runtimeConfig?.bw.configured ?? false;
  const llmConfigured = runtimeConfig?.llm.configured ?? false;

  return (
    <main className="shell">
      <section className="hero">
        <div>
          <p className="eyebrow">Local-first · Read-only · No hosted backend</p>
          <h1>BW Lineage Impact</h1>
          <p className="lede">
            로컬 파일과 안전한 BW metadata snapshot을 기준으로 lineage, change impact,
            field mapping, Native SQL View evidence를 확인합니다.
          </p>
        </div>
        <div className="statusCard">
          <span className={health?.status === 'ok' ? 'dot ok' : 'dot'} />
          <div>
            <strong>{health ? `Backend ${health.status}` : 'Backend 확인 중'}</strong>
            <span>
              v{health?.version ?? '—'} · BW {bwConfigured ? 'configured' : 'not configured'} · LLM{' '}
              {llmConfigured ? 'configured' : 'off'}
            </span>
          </div>
        </div>
      </section>

      <section className="workspace">
        <aside className="panel tools">
          <h2>분석 도구</h2>
          {(Object.keys(toolLabels) as Tool[]).map((item) => (
            <button
              className={item === tool ? 'tool active' : 'tool'}
              key={item}
              onClick={() => setTool(item)}
              type="button"
            >
              {toolLabels[item]}
            </button>
          ))}
        </aside>

        <section className="panel formPanel">
          <div className="panelHeader">
            <h2>{toolLabels[tool]}</h2>
            {tool === 'settings' ? <span>process-memory only</span> : null}
            {tool === 'live' ? <span>explicit read-only confirmation required</span> : null}
            {tool !== 'settings' && (tool !== 'live' || form.liveMode === 'dataflow') ? (
              <select
                value={form.format}
                onChange={(event) => update('format', event.target.value as OutputFormat)}
              >
                <option value="md">Markdown</option>
                <option value="json">JSON</option>
                {tool === 'lineage' || (tool === 'live' && form.liveMode === 'dataflow') ? <option value="mermaid">Mermaid</option> : null}
              </select>
            ) : null}
          </div>

          {tool === 'settings' ? (
            <SettingsPanel
              busy={busy}
              settings={settings}
              runtimeConfig={runtimeConfig}
              onClear={() => void clearSettings()}
              onSave={() => void saveSettings()}
              updateSettings={updateSettings}
            />
          ) : null}

          {tool === 'live' ? <LivePanel form={form} update={update} /> : null}

          {tool === 'lineage' || tool === 'impact' ? (
            <Field label="Graph JSON path" value={form.graphPath} onChange={(v) => update('graphPath', v)} />
          ) : null}
          {tool === 'lineage' ? (
            <Field label="Start object id" value={form.objectId} onChange={(v) => update('objectId', v)} />
          ) : null}
          {tool === 'impact' ? (
            <Field label="Changes JSON path" value={form.changesPath} onChange={(v) => update('changesPath', v)} />
          ) : null}
          {tool === 'sql-view' ? (
            <>
              <Field label="Native SQL View id" value={form.viewId} onChange={(v) => update('viewId', v)} />
              <Field label="SQL file path" value={form.sqlFile} onChange={(v) => update('sqlFile', v)} />
            </>
          ) : null}
          {tool === 'field-lineage' ? (
            <>
              <Field label="Transformation XML path" value={form.xmlFile} onChange={(v) => update('xmlFile', v)} />
              <Field label="Transformation id" value={form.transformationId} onChange={(v) => update('transformationId', v)} />
              <Field label="Source object" value={form.sourceObject} onChange={(v) => update('sourceObject', v)} />
              <Field label="Target object" value={form.targetObject} onChange={(v) => update('targetObject', v)} />
            </>
          ) : null}

          {tool !== 'settings' ? (
            <button className="run" disabled={busy} onClick={() => void runAnalysis()} type="button">
              {busy ? '실행 중…' : tool === 'live' ? 'Run confirmed live read-only action' : 'Run local analysis'}
            </button>
          ) : null}
          {error ? <p className="error">{error}</p> : null}
        </section>

        <section className="panel resultPanel">
          <div className="panelHeader">
            <h2>결과</h2>
            <span>{result ? 'local output' : 'not run yet'}</span>
          </div>
          <pre>{result || '왼쪽에서 분석을 실행하면 결과가 여기에 표시됩니다.'}</pre>
        </section>
      </section>
    </main>
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
      <p className="notice">
        입력값은 로컬 백엔드 프로세스 메모리에만 보관됩니다. 파일/env에 저장하지 않고, API 응답에는
        secret을 반환하지 않습니다.
      </p>
      <h3>BW runtime env</h3>
      <Field label="BW_URL" value={settings.bwUrl} onChange={(v) => updateSettings('bwUrl', v)} />
      <Field label="BW_USER" value={settings.bwUser} onChange={(v) => updateSettings('bwUser', v)} />
      <Field
        inputType="password"
        label="BW_PASSWORD"
        value={settings.bwPassword}
        onChange={(v) => updateSettings('bwPassword', v)}
      />
      <div className="twoColumn">
        <Field label="BW_CLIENT" value={settings.bwClient} onChange={(v) => updateSettings('bwClient', v)} />
        <Field
          label="BW_LANGUAGE"
          value={settings.bwLanguage}
          onChange={(v) => updateSettings('bwLanguage', v)}
        />
      </div>
      <Checkbox
        checked={settings.bwVerifySsl}
        label="BW_VERIFY_SSL"
        onChange={(value) => updateSettings('bwVerifySsl', value)}
      />
      <Field
        label="BW_CA_BUNDLE / corporate CA PEM path (optional)"
        value={settings.bwCaBundle}
        onChange={(v) => updateSettings('bwCaBundle', v)}
      />

      <h3>Local OpenAI-compatible LLM</h3>
      <Checkbox
        checked={settings.llmEnabled}
        label="Enable optional local LLM explainer"
        onChange={(value) => updateSettings('llmEnabled', value)}
      />
      <Field
        label="BWLI_LLM_BASE_URL"
        value={settings.llmBaseUrl}
        onChange={(v) => updateSettings('llmBaseUrl', v)}
      />
      <Field label="BWLI_LLM_MODEL" value={settings.llmModel} onChange={(v) => updateSettings('llmModel', v)} />
      <Field
        inputType="password"
        label="BWLI_LLM_API_KEY"
        value={settings.llmApiKey}
        onChange={(v) => updateSettings('llmApiKey', v)}
      />

      <div className="buttonRow">
        <button className="run" disabled={busy} onClick={onSave} type="button">
          {busy ? '저장 중…' : 'Save runtime settings'}
        </button>
        <button className="secondary" disabled={busy} onClick={onClear} type="button">
          Clear
        </button>
      </div>
      <p className="configSummary">
        BW: {runtimeConfig?.bw.configured ? 'configured' : 'not configured'} · LLM:{' '}
        {runtimeConfig?.llm.configured ? 'configured' : 'not configured'} · storage:{' '}
        {runtimeConfig?.storage ?? 'process-memory'}
      </p>
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
      <p className="notice">
        실제 SAP BW metadata GET 호출을 수행합니다. Runtime Settings에 read-only 계정을 저장한 뒤,
        아래 확인 체크박스를 켠 경우에만 실행됩니다.
      </p>
      <label className="field">
        <span>Live action</span>
        <select value={form.liveMode} onChange={(event) => update('liveMode', event.target.value as LiveMode)}>
          <option value="smoke">Smoke only</option>
          <option value="collect">Collect snapshot</option>
          <option value="dataflow">Render Dataflow</option>
        </select>
      </label>
      <Field label="Search term" value={form.liveSearchTerm} onChange={(v) => update('liveSearchTerm', v)} />
      <Field label="Object name" value={form.liveObjectName} onChange={(v) => update('liveObjectName', v)} />
      <div className="twoColumn">
        <Field label="Object type" value={form.liveObjectType} onChange={(v) => update('liveObjectType', v)} />
        <Field label="Source system for RSDS" value={form.liveSourceSystem} onChange={(v) => update('liveSourceSystem', v)} />
      </div>
      <div className="twoColumn">
        <label className="field">
          <span>Dataflow direction</span>
          <select
            value={form.liveDataflowDirection}
            onChange={(event) => update('liveDataflowDirection', event.target.value as FormState['liveDataflowDirection'])}
          >
            <option value="downwards">downwards</option>
            <option value="upwards">upwards</option>
            <option value="both">both</option>
          </select>
        </label>
        <Field label="Dataflow levels" value={form.liveDataflowLevels} onChange={(v) => update('liveDataflowLevels', v)} />
      </div>
      {form.liveMode === 'collect' ? (
        <Field label="Output directory" value={form.liveOutDir} onChange={(v) => update('liveOutDir', v)} />
      ) : null}
      <Checkbox
        checked={form.liveConfirm}
        label="I confirm this is a read-only live BW metadata call"
        onChange={(value) => update('liveConfirm', value)}
      />
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  inputType = 'text',
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  inputType?: 'text' | 'password';
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input type={inputType} value={value} onChange={(event) => onChange(event.target.value)} />
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

function buildRequest(tool: Exclude<Tool, 'settings' | 'live'>, form: FormState): unknown {
  if (tool === 'lineage') {
    return {
      graph_path: form.graphPath,
      object_id: form.objectId,
      direction: 'downstream',
      max_depth: 3,
      format: form.format,
    };
  }
  if (tool === 'impact') {
    return {
      graph_path: form.graphPath,
      changes_path: form.changesPath,
      max_depth: 3,
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
