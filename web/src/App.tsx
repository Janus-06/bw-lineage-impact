import { useEffect, useMemo, useState } from 'react';
import { getHealth, postRendered, type HealthResponse, type OutputFormat } from './api';

type Tool = 'lineage' | 'impact' | 'sql-view' | 'field-lineage';

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
};

const toolLabels: Record<Tool, string> = {
  lineage: 'Lineage',
  impact: 'Change Impact',
  'sql-view': 'Native SQL View',
  'field-lineage': 'Field Lineage',
};

export default function App() {
  const [tool, setTool] = useState<Tool>('lineage');
  const [form, setForm] = useState<FormState>(defaultState);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [result, setResult] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch((err: unknown) => setError(`Backend 연결 실패: ${String(err)}`));
  }, []);

  const endpoint = useMemo(() => `/api/${tool}`, [tool]);

  async function runAnalysis() {
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

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

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
            <span>v{health?.version ?? '—'} · read-only · LLM off by default</span>
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
            <select
              value={form.format}
              onChange={(event) => update('format', event.target.value as OutputFormat)}
            >
              <option value="md">Markdown</option>
              <option value="json">JSON</option>
              {tool === 'lineage' ? <option value="mermaid">Mermaid</option> : null}
            </select>
          </div>

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

          <button className="run" disabled={busy} onClick={() => void runAnalysis()} type="button">
            {busy ? '실행 중…' : 'Run local analysis'}
          </button>
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

function Field({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function buildRequest(tool: Tool, form: FormState): unknown {
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
