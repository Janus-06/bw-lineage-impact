import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import test from 'node:test';

const outDir = '/tmp/bwli-slice-g-test';
mkdirSync(outDir, { recursive: true });
execFileSync(
  process.platform === 'win32' ? 'npx.cmd' : 'npx',
  [
    'tsc',
    'src/sliceG.ts',
    '--target',
    'ES2022',
    '--module',
    'ES2022',
    '--moduleResolution',
    'bundler',
    '--outDir',
    outDir,
    '--skipLibCheck',
    '--declaration',
    'false',
  ],
  { cwd: new URL('..', import.meta.url), stdio: 'inherit' },
);

const sliceG = await import(pathToFileURL(`${outDir}/sliceG.js`).href);

function appFunctionBody(source, functionStart, nextFunctionStart) {
  const start = source.indexOf(functionStart);
  assert.notEqual(start, -1, `${functionStart} should be present`);
  const end = source.indexOf(nextFunctionStart, start + functionStart.length);
  assert.notEqual(end, -1, `${nextFunctionStart} should follow ${functionStart}`);
  return source.slice(start, end);
}

test('infers Slice G display layers from graph layer and BW object type', () => {
  assert.equal(sliceG.inferDisplayLayer({ type: 'RSDS' }).label, 'Source');
  assert.equal(sliceG.inferDisplayLayer({ type: 'DTP' }).label, 'Transform');
  assert.equal(sliceG.inferDisplayLayer({ layer: 'transformation', type: 'ADSO' }).label, 'Transform');
  assert.equal(sliceG.inferDisplayLayer({ type: 'ADSO' }).label, 'Model');
  assert.equal(sliceG.inferDisplayLayer({ type: 'HCPR' }).label, 'Semantic');
  assert.equal(sliceG.inferDisplayLayer({ type: 'QUERY' }).label, 'Semantic');
  assert.equal(sliceG.inferDisplayLayer({ type: 'Process Chain' }).label, 'Runtime');
  assert.equal(sliceG.inferDisplayLayer({ type: 'mystery' }).label, 'Unknown');

  const groups = sliceG.groupNodesByDisplayLayer([
    { id: 'Q', type: 'QUERY' },
    { id: 'R', type: 'RSDS' },
    { id: 'A', type: 'ADSO' },
    { id: 'T', type: 'TRFN' },
    { id: 'P', type: 'Process Chain' },
  ]);
  assert.deepEqual(groups.map((group) => group.layer), ['Source', 'Transform', 'Model', 'Semantic', 'Runtime']);
});

test('classifies request freshness into fresh, stale, none, and unknown labels', () => {
  const now = new Date('2026-06-18T10:00:00Z');
  assert.deepEqual(
    sliceG.classifyFreshness({ latest: { timestamp: '2026-06-18T09:10:00Z', status: 'G' } }, now),
    {
      state: 'fresh',
      label: 'Fresh < 2h',
      timestamp: '2026-06-18T09:10:00Z',
      status: 'G',
      ageHours: 0.83,
    },
  );
  const stale = sliceG.classifyFreshness({ latest: { timestamp: '2026-06-15T09:00:00Z' } }, now);
  assert.equal(stale.state, 'stale');
  assert.equal(stale.label, 'Stale 3d');
  assert.equal(sliceG.classifyFreshness({ latest: null, requests: [] }, now).label, 'No requests');
  assert.equal(sliceG.classifyFreshness(null, now).label, 'Unknown');
});

test('normalizes guided tour steps into 1-based navigation-safe display data', () => {
  const steps = sliceG.normalizeGuidedTourSteps({
    citations: ['EID-GLOBAL'],
    tour: [
      {
        id: 'transform',
        title: 'Transformation evidence [EID-TRFN-014]',
        description: 'TRFN maps 0NET_VALUE [EID-TRFN-014].',
        node_ids: ['TRFN'],
        edge_ids: ['edge-1'],
      },
      {
        title: 'Semantic exposure',
        description: 'HCPR exposes field.',
        node_ids: ['HCPR', 'QUERY'],
      },
    ],
  });

  assert.equal(steps.length, 2);
  assert.equal(steps[0].index, 1);
  assert.equal(steps[0].total, 2);
  assert.equal(steps[0].canPrevious, false);
  assert.equal(steps[0].canNext, true);
  assert.deepEqual(steps[0].nodeIds, ['TRFN']);
  assert.deepEqual(steps[0].edgeIds, ['edge-1']);
  assert.deepEqual(steps[0].evidenceIds, ['EID-GLOBAL', 'EID-TRFN-014']);
  assert.equal(steps[1].index, 2);
  assert.equal(steps[1].canPrevious, true);
  assert.equal(steps[1].canNext, false);
});

test('normalizes legacy tour body text as description', () => {
  const steps = sliceG.normalizeGuidedTourSteps({
    tour: [
      {
        id: 'deterministic',
        title: 'Deterministic fallback',
        body: 'Backend deterministic walkthrough body should remain visible.',
        node_ids: ['SRC'],
      },
    ],
  });

  assert.equal(steps[0].description, 'Backend deterministic walkthrough body should remain visible.');
});

test('Slice 2 Lineage IA keeps default controls task-first with progressive advanced limits', () => {
  const source = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
  const styles = readFileSync(new URL('../src/styles.css', import.meta.url), 'utf8');
  const lineageBody = appFunctionBody(source, 'function LineageTab', '\nfunction ImpactTab');
  const controlStart = lineageBody.indexOf('<section className="controlCard">');
  const advancedStart = lineageBody.indexOf('<details className="lineageAdvancedControls">');
  assert.notEqual(controlStart, -1, 'LineageTab should keep a control card');
  assert.notEqual(advancedStart, -1, 'LineageTab should use progressive disclosure for advanced limits');

  assert.match(lineageBody, /Trace Lineage \/ 흐름 보기/, 'Lineage copy should be task-first');
  assert.match(lineageBody, /aria-label="selected object context"/, 'selected object context should remain visible');
  assert.match(lineageBody, /className=\{props\.direction === option\.value \? 'directionChip active' : 'directionChip'\}/);
  ['Upstream', 'Downstream', 'Both'].forEach((label) => {
    assert.match(lineageBody, new RegExp(`label: '${label}'`), `${label} direction chip should be declared`);
  });
  assert.doesNotMatch(lineageBody, /<select value=\{props\.direction\}/, 'direction should no longer be an always-visible select control');
  assert.match(lineageBody, /className="primaryButton wide"[\s\S]*흐름 보기/, 'the primary Lineage task CTA should be 흐름 보기');
  assert.doesNotMatch(lineageBody, />Run Lineage</, 'old feature-tool CTA copy should not remain');

  const defaultControlBody = lineageBody.slice(controlStart, advancedStart);
  ['Depth', 'Node cap', 'Edge cap'].forEach((label) => {
    assert.doesNotMatch(defaultControlBody, new RegExp(`label="${label}"`), `${label} must not be in always-visible controls`);
  });
  const advancedBody = lineageBody.slice(advancedStart, lineageBody.indexOf('<AssistantPresetLinks', advancedStart));
  assert.match(advancedBody, /<summary>Advanced limits<\/summary>/);
  ['Depth', 'Node cap', 'Edge cap'].forEach((label) => {
    assert.match(advancedBody, new RegExp(`label="${label}"`), `${label} should remain available inside Advanced limits`);
  });

  ['Dataflow', 'Where-used', 'Object detail', 'Freshness'].forEach((label) => {
    assert.match(lineageBody, new RegExp(`'${label}'|>${label}<`), `${label} evidence health label should be visible`);
  });
  assert.match(lineageBody, /Evidence panel/, 'Evidence Walkthrough should be de-emphasized as evidence panel preset content');
  assert.match(lineageBody, /Evidence Walkthrough preset/, 'legacy walkthrough remains available only as an assistant preset');
  assert.doesNotMatch(lineageBody, /title="Evidence Walkthrough"/, 'walkthrough should not be a primary drawer title');
  assert.doesNotMatch(lineageBody, /Generate Evidence Walkthrough/, 'walkthrough should not be presented as the default action copy');

  assert.match(styles, /\.directionChipGroup\s*\{[^}]*grid-template-columns:\s*repeat\(3,\s*minmax\(0,\s*1fr\)\)/);
  assert.match(styles, /\.evidenceHealthSummary\s*\{[\s\S]*?background:\s*linear-gradient/);
  assert.match(styles, /\.lineageAdvancedControls\s*\{[\s\S]*?border:/);
});

test('Glossary object selection clears stale tour and freshness state before Lineage', () => {
  const source = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
  const handlerMatch = source.match(
    /<GlossaryTab[\s\S]*?onSelectObject=\{\(objectId\) => \{([\s\S]*?)\n\s*\}\}\n\s*onAddTarget=/,
  );

  assert.ok(handlerMatch, 'GlossaryTab onSelectObject handler should be present');
  const handler = handlerMatch[1];
  const switchToLineageIndex = handler.indexOf("setActiveTab('lineage')");
  assert.notEqual(switchToLineageIndex, -1, 'Glossary selection should switch to Lineage');

  [
    'setObjectDetail(null)',
    'setLineageTour(null)',
    'setLineageTourStepIndex(0)',
    'setImpactTour(null)',
    'setImpactTourStepIndex(0)',
    'setObjectFreshness(null)',
  ].forEach((resetCall) => {
    const resetIndex = handler.indexOf(resetCall);
    assert.notEqual(resetIndex, -1, `${resetCall} should reset stale Glossary selection state`);
    assert.ok(resetIndex < switchToLineageIndex, `${resetCall} should run before switching to Lineage`);
  });

  assert.match(
    source,
    /objectFreshness\?\.snapshotId === selectedSnapshotId && objectFreshness\.objectId === selectedObjectId/,
    'freshness state must be keyed to the selected snapshot and object',
  );
  assert.match(
    source,
    /objectDetail=\{selectedObjectDetail\}/,
    'LineageTab must receive only object details for the currently selected object',
  );
  assert.doesNotMatch(
    source,
    /console\.warn\('freshness lookup failed',\s*err\)/,
    'supplemental freshness failures must not log raw error details',
  );
  assert.match(source, /const selectionRef = useRef\(\{ snapshotId: '', objectId: '' \}\);/);
  assert.match(source, /const analysisRequestRef = useRef\(0\);/);
  assert.match(source, /const queryAnalysisRequestRef = useRef\(0\);/);
  assert.match(source, /function isCurrentAnalysisRequest\(requestId: number, snapshotId: string, objectId: string\): boolean/);
  assert.match(
    source,
    /\['lineage', 'lineage-advice', 'lineage-tour', 'impact', 'impact-advice', 'impact-tour', 'impact-agentic', 'live-analyze', 'refresh-bw'\]\.includes\(current\)/,
    'invalidating analysis requests should also clear analysis-owned busy states',
  );
  [
    'async function runLineage',
    'async function runLineageAdvice',
    'async function runLineageTour',
    'async function runImpact',
    'async function runImpactAdvice',
    'async function runImpactTour',
  ].forEach((functionStart) => {
    const start = source.indexOf(functionStart);
    assert.notEqual(start, -1, `${functionStart} should be present`);
    const end = source.indexOf('\n  async function', start + functionStart.length);
    const body = source.slice(start, end === -1 ? source.indexOf('\n  return (', start) : end);
    assert.match(body, /const requestId = nextAnalysisRequestId\(\);/, `${functionStart} should key async analysis requests`);
    assert.match(body, /isCurrentAnalysisRequest\(requestId, requestSnapshotId, requestObjectId\)/, `${functionStart} should ignore stale async responses`);
  });
  ['async function fetchAndAnalyzeObject', 'async function refreshAnalysisBasis'].forEach((functionStart) => {
    const start = source.indexOf(functionStart);
    assert.notEqual(start, -1, `${functionStart} should be present`);
    const end = source.indexOf('\n  async function', start + functionStart.length);
    const body = source.slice(start, end === -1 ? source.indexOf('\n  async function searchGlossary', start) : end);
    assert.match(body, /nextAnalysisRequestId\(\)/, `${functionStart} should key capture\/refresh requests`);
    assert.match(body, /analysisRequestRef\.current !==/, `${functionStart} should ignore stale capture\/refresh responses`);
  });
  const queryBody = appFunctionBody(source, 'async function runQueryAnalysis', '\n  async function confirmGlossaryTerm');
  assert.match(queryBody, /const requestSnapshotId = selectedSnapshotId;/);
  assert.match(queryBody, /const requestQueryName = name;/);
  assert.match(queryBody, /const requestId = nextQueryAnalysisRequestId\(\);/);
  assert.match(queryBody, /getQueryAnalysis\(requestSnapshotId, requestQueryName\)/);
  assert.match(queryBody, /isCurrentQueryAnalysisRequest\(requestId, requestSnapshotId, requestQueryName\)/);
  const freshnessMatch = source.match(
    /const selectedFreshness = useMemo<RequestFreshnessResponse \| null>\(\(\) => \{([\s\S]*?)\n\s*\}, \[[^\]]*\]\);/,
  );
  assert.ok(freshnessMatch, 'selectedFreshness derivation should be present');
  assert.match(freshnessMatch[1], /freshnessFromMetadata\(selectedObjectDetail\?\.metadata\)/);
  assert.doesNotMatch(
    freshnessMatch[1],
    /freshnessFromMetadata\(objectDetail\?\.metadata\)/,
    'selectedFreshness must not fall back to stale objectDetail metadata/request_freshness',
  );
});

test('same-object stale detail is keyed off snapshot before detail and freshness rendering', () => {
  const source = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
  assert.match(
    source,
    /interface ObjectDetailState \{\s*snapshotId: string;\s*objectId: string;\s*value: CatalogObjectDetail;\s*\}/,
    'object detail state must carry the request snapshot and object keys',
  );
  assert.match(
    source,
    /const \[objectDetail, setObjectDetail\] = useState<ObjectDetailState \| null>\(null\);/,
    'object detail state should store the keyed wrapper instead of a raw detail value',
  );

  const detailMatch = source.match(/const selectedObjectDetail =([\s\S]*?)\n  const selectedObject =/);
  assert.ok(detailMatch, 'selectedObjectDetail derivation should be present');
  assert.match(detailMatch[1], /objectDetail\?\.snapshotId === selectedSnapshotId/);
  assert.match(detailMatch[1], /objectDetail\.objectId === selectedObjectId/);
  assert.match(detailMatch[1], /objectDetail\.value\.id === selectedObjectId/);
  assert.match(detailMatch[1], /\? objectDetail\.value\s*: null/);
  assert.doesNotMatch(
    detailMatch[1],
    /objectDetail\?\.id === selectedObjectId \? objectDetail : null/,
    'same-object stale details from an older snapshot must not match the refreshed selection',
  );

  const loadBody = appFunctionBody(source, 'async function loadObjectDetail', '\n  async function saveSetup');
  assert.match(
    loadBody,
    /setObjectDetail\(\{ snapshotId, objectId, value: detail \}\);/,
    'loadObjectDetail should store details under the request snapshot and object',
  );
  assert.doesNotMatch(loadBody, /setObjectDetail\(detail\);/);

  const freshnessMatch = source.match(
    /const selectedFreshness = useMemo<RequestFreshnessResponse \| null>\(\(\) => \{([\s\S]*?)\n\s*\}, \[[^\]]*\]\);/,
  );
  assert.ok(freshnessMatch, 'selectedFreshness derivation should be present');
  assert.match(freshnessMatch[1], /freshnessFromMetadata\(selectedObjectDetail\?\.metadata\)/);
  assert.doesNotMatch(
    freshnessMatch[1],
    /\bobjectDetail\b/,
    'selectedFreshness must read detail metadata only through the current snapshot/object guard',
  );
});

test('Glossary is hidden behind a frontend feature flag for the current release', () => {
  const source = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');

  assert.match(source, /const GLOSSARY_VISIBLE: boolean = false;/);
  assert.match(
    source,
    /\{GLOSSARY_VISIBLE \? <TabButton id="glossary" active=\{activeTab\} onClick=\{setActiveTab\} label="Glossary" \/> : null\}/,
    'Glossary tab must stay compiled but not render while the feature flag is false',
  );
  assert.match(
    source,
    /\{GLOSSARY_VISIBLE \? <TermsOverview terms=\{glossaryTerms\} onOpen=\{\(\) => setActiveTab\('glossary'\)\} \/> : null\}/,
    'Glossary overview entry point must be hidden while the feature flag is false',
  );
  assert.match(
    source,
    /GLOSSARY_VISIBLE \? getGlossary\(snapshotId\) : Promise\.resolve\(null\)/,
    'snapshot context refresh must not auto-fetch glossary while hidden',
  );
  assert.match(
    source,
    /GLOSSARY_VISIBLE && tabToRerun === 'glossary'/,
    'refresh reruns must skip hidden Glossary tab work',
  );
  assert.match(
    source,
    /GLOSSARY_VISIBLE && activeTab === 'glossary'/,
    'Glossary tab body must be guarded by the feature flag',
  );
  assert.match(
    source,
    /GLOSSARY_VISIBLE \? <GlossaryList terms=\{props\.objectGlossary\}/,
    'object detail glossary terms must be hidden in the current release',
  );
});

test('object list metadata and freshness fallback are keyed to the selected snapshot', () => {
  const source = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');

  assert.match(
    source,
    /const \[objectsSnapshotId, setObjectsSnapshotId\] = useState\(''\);/,
    'object list state should carry the snapshot key it belongs to',
  );
  assert.match(source, /const objectListRequestRef = useRef\(0\);/);
  assert.match(
    source,
    /const currentObjects = useMemo\(\s*\(\) => \(objectsSnapshotId === selectedSnapshotId \? objects : \[\]\),\s*\[objects, objectsSnapshotId, selectedSnapshotId\],\s*\);/,
    'rendered objects must be hidden unless the list belongs to the selected snapshot',
  );
  assert.match(
    source,
    /const currentObjectNextCursor = objectsSnapshotId === selectedSnapshotId \? objectNextCursor : null;/,
    'pagination cursor must be hidden when the object list is stale',
  );
  assert.match(
    source,
    /const selectedObjectFromCurrentObjects = currentObjects\.find\(\(item\) => item\.id === selectedObjectId\) \?\? null;/,
    'selected catalog object must come from the snapshot-keyed list',
  );
  assert.match(
    source,
    /const selectedObject = selectedObjectFromCurrentObjects\s*\?\?\s*\(allowHiddenSelection && selectedObjectDetail \? selectedObjectDetail : null\);/,
    'hidden selection may fall back only to the snapshot-keyed detail wrapper',
  );
  assert.match(
    source,
    /const snapshotPickObjects = useMemo\(\(\) => currentObjects\.slice\(0, 16\), \[currentObjects\]\);/,
    'quick picker must not use stale unkeyed objects',
  );

  const freshnessMatch = source.match(
    /const selectedFreshness = useMemo<RequestFreshnessResponse \| null>\(\(\) => \{([\s\S]*?)\n\s*\}, \[[^\]]*\]\);/,
  );
  assert.ok(freshnessMatch, 'selectedFreshness derivation should be present');
  assert.match(freshnessMatch[1], /freshnessFromMetadata\(selectedObjectFromCurrentObjects\?\.metadata\)/);
  assert.doesNotMatch(
    freshnessMatch[1],
    /freshnessFromMetadata\(selectedObject\?\.metadata\)/,
    'freshness must not fall back through selectedObject because it can include preserved hidden selections',
  );

  const listHelpers = appFunctionBody(source, 'function nextObjectListRequestId', '\n  function clearAnalysisState');
  assert.match(listHelpers, /objectListRequestRef\.current \+= 1;/);
  assert.match(listHelpers, /selectionRef\.current\.snapshotId === snapshotId/);
  assert.match(listHelpers, /function markObjectsStaleForSnapshot\(snapshotId: string\)/);
  assert.match(listHelpers, /setObjectsSnapshotId\(snapshotId\);/);
  assert.match(listHelpers, /setObjects\(\[\]\);/);
  assert.match(listHelpers, /setObjectNextCursor\(null\);/);

  const chooseBody = appFunctionBody(source, 'function chooseSnapshot', '\n  function parseLiveObjectNames');
  assert.match(chooseBody, /selectionRef\.current = \{ snapshotId, objectId: '' \};/);
  assert.match(chooseBody, /markObjectsStaleForSnapshot\(snapshotId\);/);

  const refreshBody = appFunctionBody(source, 'async function refreshObjects', '\n  async function refreshSnapshotContext');
  assert.match(refreshBody, /const requestId = nextObjectListRequestId\(\);/);
  assert.match(
    refreshBody,
    /if \(!isCurrentObjectListRequest\(requestId, snapshotId\)\) return;/,
    'stale listObjects responses must be ignored before mutating object state',
  );
  assert.match(refreshBody, /setObjectsSnapshotId\(snapshotId\);/);
  assert.match(refreshBody, /if \(isCurrentObjectListRequest\(requestId, snapshotId\)\) \{\s*setError\(errorText\(err\)\);/);

  const reloadBody = appFunctionBody(source, 'async function reloadSnapshots', '\n  async function runLineage');
  assert.match(
    reloadBody,
    /if \(options\.preserveAnalysisSelection\) \{\s*selectionRef\.current = \{ snapshotId: nextSnapshotId, objectId: selectionRef\.current\.objectId \};\s*markObjectsStaleForSnapshot\(nextSnapshotId\);\s*markSnapshotContextStale\(\);\s*setSelectedSnapshotId\(nextSnapshotId\);/,
    'preserved reloads must mark the previous object list stale before rendering the new basis',
  );

  const listRender = source.slice(source.indexOf('<div className="objectList"'), source.indexOf('<section className="workspacePane">'));
  assert.match(listRender, /currentObjects\.length === 0/);
  assert.match(listRender, /currentObjects\.map\(\(item\) =>/);
  assert.doesNotMatch(listRender, /\bobjects\.map\(\(item\) =>/);
  assert.match(listRender, /currentObjectNextCursor \?/);
  assert.match(listRender, /refreshObjects\(selectedSnapshotId, currentObjectNextCursor\)/);
});


test('BW Search QUERY capture clears dataflow targets and sends query list only', () => {
  const source = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
  const captureBody = appFunctionBody(source, 'async function captureLiveWithTargets', '\n  async function captureLive');
  const fetchBody = appFunctionBody(source, 'async function fetchAndAnalyzeObject', '\n  async function refreshAnalysisBasis');

  assert.match(
    captureBody,
    /const queries = options\.queries \?\? \(objectType === 'QUERY' \? options\.objectNames : \[\]\);/,
    'QUERY capture should populate queries from the selected BW Search object ids',
  );
  assert.match(
    captureBody,
    /const objectNames = objectType === 'QUERY' \? \[\] : options\.objectNames;/,
    'QUERY capture must clear objectNames so V1 does not run dataflow\/xref on query ids',
  );
  assert.match(captureBody, /objectNames,\s*searchTerms:/, 'capture request should use the guarded objectNames list');
  assert.match(captureBody, /queries,\s*objectType,/, 'capture request should include the query list');
  assert.match(
    fetchBody,
    /captureLiveWithTargets\(\{\s*objectNames: \[item\.object_id\],\s*objectType: item\.object_type,\s*\}\)/,
    'BW Search capture should preserve the found object type so QUERY items take the query-only capture path',
  );
});

test('query-name input changes synchronously invalidate pending Query Analysis', () => {
  const source = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');

  assert.match(
    source,
    /const \[queryName, setQueryNameState\] = useState\(''\);/,
    'query input should use a wrapped state setter so input edits can update refs synchronously',
  );
  assert.match(
    source,
    /setQueryName=\{setQueryNameFromInput\}/,
    'QueryTab should receive the synchronous input setter, not the raw state setter',
  );

  const queryGuardBody = appFunctionBody(source, 'function isCurrentQueryAnalysisRequest', '\n  function nextQueryAnalysisRequestId');
  assert.match(
    queryGuardBody,
    /queryAnalysisRequestRef\.current === requestId/,
    'Query Analysis should be guarded by the query-only request token',
  );
  assert.doesNotMatch(
    queryGuardBody,
    /analysisRequestRef\.current === requestId/,
    'Query Analysis guard must not depend on the shared analysis request token',
  );

  const helperBody = appFunctionBody(source, 'function invalidateQueryAnalysisRequests', '\n  function nextObjectListRequestId');
  const helperTokenIndex = helperBody.indexOf('queryAnalysisRequestRef.current += 1;');
  const helperBusyClearIndex = helperBody.indexOf(
    "setBusy((current) => current === 'query-analysis' ? '' : current);",
  );
  assert.notEqual(helperTokenIndex, -1, 'query invalidation helper should invalidate the query-analysis request token');
  assert.notEqual(helperBusyClearIndex, -1, 'query invalidation helper should clear pending query-analysis busy state');
  assert.ok(helperTokenIndex < helperBusyClearIndex, 'query invalidation token must advance before clearing busy');
  assert.doesNotMatch(
    helperBody,
    /analysisRequestRef/,
    'query invalidation helper must not touch the shared analysis request token',
  );

  const setterBody = appFunctionBody(source, 'function setQueryNameFromInput', '\n  function applyImpactFieldsForSelection');
  const refIndex = setterBody.indexOf('queryNameRef.current = value;');
  const invalidateIndex = setterBody.indexOf('invalidateQueryAnalysisRequests();');
  const stateIndex = setterBody.indexOf('setQueryNameState(value);');
  assert.notEqual(refIndex, -1, 'query input setter should update queryNameRef immediately');
  assert.notEqual(invalidateIndex, -1, 'query input setter should invalidate pending query-analysis immediately');
  assert.equal(
    setterBody.indexOf('analysisRequestRef.current += 1;'),
    -1,
    'query input setter must not invalidate the shared analysis request token',
  );
  assert.notEqual(stateIndex, -1, 'query input setter should still update React state');
  assert.ok(refIndex < invalidateIndex, 'queryNameRef must update before invalidating/evaluating stale responses');
  assert.ok(invalidateIndex < stateIndex, 'query-analysis busy must clear before scheduling setQueryName state update');

  const queryBody = appFunctionBody(source, 'async function runQueryAnalysis', '\n  async function confirmGlossaryTerm');
  assert.match(queryBody, /const requestId = nextQueryAnalysisRequestId\(\);/);
  assert.match(queryBody, /if \(!isCurrentQueryAnalysisRequest\(requestId, requestSnapshotId, requestQueryName\)\) return;/);
  assert.match(queryBody, /applyQueryName\(response\.query_name\);/);
});

test('snapshot and object selection invalidation clears pending Query Analysis with query-only token', () => {
  const source = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');

  const helperBody = appFunctionBody(source, 'function invalidateQueryAnalysisRequests', '\n  function nextObjectListRequestId');
  assert.match(helperBody, /queryAnalysisRequestRef\.current \+= 1;/);
  assert.match(helperBody, /setBusy\(\(current\) => current === 'query-analysis' \? '' : current\);/);
  assert.doesNotMatch(
    helperBody,
    /analysisRequestRef/,
    'query-only invalidation must stay separate from shared lineage/impact request tokens',
  );

  const snapshotSetterBody = appFunctionBody(source, 'function setSelectedSnapshotId', '\n  function setSelectedObjectId');
  assert.match(
    snapshotSetterBody,
    /if \(snapshotId !== selectedSnapshotId\) \{\s*resetImpactFieldSelection\(\);\s*invalidateQueryAnalysisRequests\(\);/,
    'snapshot selection changes should invalidate pending query analysis without depending on shared analysis tokens',
  );
  assert.doesNotMatch(snapshotSetterBody, /analysisRequestRef/);

  const objectSetterBody = appFunctionBody(source, 'function setSelectedObjectId', '\n  function applyQueryName');
  assert.match(
    objectSetterBody,
    /if \(objectId !== selectedObjectId\) \{\s*resetImpactFieldSelection\(\);\s*invalidateQueryAnalysisRequests\(\);/,
    'object selection changes should invalidate pending query analysis without depending on shared analysis tokens',
  );
  assert.doesNotMatch(objectSetterBody, /analysisRequestRef/);

  const clearBody = appFunctionBody(source, 'function clearAnalysisState', '\n  function clearRenderedAnalysisStateForRefresh');
  const sharedInvalidationIndex = clearBody.indexOf('invalidateAnalysisRequests();');
  const queryInvalidationIndex = clearBody.indexOf('invalidateQueryAnalysisRequests();');
  assert.notEqual(sharedInvalidationIndex, -1, 'clearAnalysisState should still invalidate non-query analysis requests');
  assert.notEqual(queryInvalidationIndex, -1, 'clearAnalysisState should also invalidate pending query analysis');
  assert.ok(sharedInvalidationIndex < queryInvalidationIndex, 'query invalidation should be explicit and separate');
  assert.match(clearBody, /setQueryAnalysis\(null\);/);

  const refreshClearBody = appFunctionBody(source, 'function clearRenderedAnalysisStateForRefresh', '\n  function chooseSnapshot');
  assert.match(
    refreshClearBody,
    /invalidateQueryAnalysisRequests\(\);/,
    'refresh stale-render clearing should invalidate pending query analysis when clearing query results',
  );
  assert.doesNotMatch(
    refreshClearBody,
    /analysisRequestRef|invalidateAnalysisRequests|clearAnalysisState/,
    'refresh query invalidation must not disturb the active shared analysis request token',
  );

  const chooseBody = appFunctionBody(source, 'function chooseSnapshot', '\n  function parseLiveObjectNames');
  assert.match(
    chooseBody,
    /setSelectedSnapshotId\(snapshotId\);\s*setSelectedObjectId\(''\);\s*setAllowHiddenSelection\(false\);\s*clearAnalysisState\(\);/,
    'snapshot chooser should route through guarded selection setters and clearAnalysisState',
  );

  const queryInputBody = appFunctionBody(source, 'function setQueryNameFromInput', '\n  function applyImpactFieldsForSelection');
  assert.match(queryInputBody, /invalidateQueryAnalysisRequests\(\);/);
  assert.doesNotMatch(
    queryInputBody,
    /analysisRequestRef|nextAnalysisRequestId|invalidateAnalysisRequests\(/,
    'query input changes must not rely on or mutate the shared analysis request token',
  );
});

test('stale Query Analysis completion cannot clear a newer pending busy state', () => {
  const source = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
  const queryBody = appFunctionBody(source, 'async function runQueryAnalysis', '\n  async function confirmGlossaryTerm');
  const finallyBodyMatch = queryBody.match(/finally\s*\{([\s\S]*?)\n\s*\}\n\s*\}/);

  assert.ok(finallyBodyMatch, 'runQueryAnalysis should have a finally block');
  const finallyBody = finallyBodyMatch[1];
  const guardIndex = finallyBody.indexOf(
    'isCurrentQueryAnalysisRequest(requestId, requestSnapshotId, requestQueryName)',
  );
  const busyClearIndex = finallyBody.indexOf(
    "setBusy((current) => current === 'query-analysis' ? '' : current);",
  );
  assert.notEqual(
    guardIndex,
    -1,
    'query-analysis busy clearing must be guarded by the request/snapshot/query identity',
  );
  assert.notEqual(busyClearIndex, -1, 'query-analysis busy state should still clear for the current request');
  assert.ok(guardIndex < busyClearIndex, 'stale query-analysis requests must not clear newer pending busy state');
});

test('guarded capture and refresh keep snapshot reload inside the request token', () => {
  const source = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
  const reloadBody = appFunctionBody(source, 'async function reloadSnapshots', '\n  async function runLineage');
  const refreshClearBody = appFunctionBody(
    source,
    'function clearRenderedAnalysisStateForRefresh',
    '\n  function chooseSnapshot',
  );

  assert.match(reloadBody, /options: ReloadSnapshotsOptions = \{\}/);
  assert.match(reloadBody, /if \(options\.isStale\?\.\(\)\) return null;/);
  assert.match(
    reloadBody,
    /if \(options\.preserveAnalysisSelection\) \{\s*selectionRef\.current = \{ snapshotId: nextSnapshotId, objectId: selectionRef\.current\.objectId \};\s*markObjectsStaleForSnapshot\(nextSnapshotId\);\s*markSnapshotContextStale\(\);\s*setSelectedSnapshotId\(nextSnapshotId\);\s*\} else \{\s*chooseSnapshot\(nextSnapshotId\);\s*\}/,
    'guarded reloads must not call chooseSnapshot, because chooseSnapshot invalidates the request token',
  );
  [
    'setLineage(null)',
    'setLineageAdvice(null)',
    'setLineageTour(null)',
    'setLineageTourStepIndex(0)',
    'setImpact(null)',
    'setImpactAdvice(null)',
    'setImpactTour(null)',
    'setImpactTourStepIndex(0)',
    'setObjectDetail(null)',
    'setObjectFreshness(null)',
    'setCaptureScope([])',
    'setGlossaryTerms([])',
  ].forEach((resetCall) => {
    assert.match(refreshClearBody, new RegExp(resetCall.replace(/[()[\]]/g, '\\$&')), `${resetCall} should clear stale rendered refresh state`);
  });
  assert.doesNotMatch(
    refreshClearBody,
    /analysisRequestRef|invalidateAnalysisRequests|clearAnalysisState/,
    'guarded refresh stale-render clearing must not invalidate the active request token',
  );

  const fetchBody = appFunctionBody(
    source,
    'async function fetchAndAnalyzeObject',
    '\n  async function refreshAnalysisBasis',
  );
  assert.doesNotMatch(fetchBody, /await reloadSnapshots\(snapshot\.id, snapshot\);/);
  assert.match(
    fetchBody,
    /const captureSnapshotId = await reloadSnapshots\(snapshot\.id, snapshot, \{\s*preserveAnalysisSelection: true,\s*isStale: \(\) => analysisRequestRef\.current !== captureRequestId,\s*\}\);/,
    'capture flow should carry its request token into reloadSnapshots',
  );
  const captureReloadIndex = fetchBody.indexOf('const captureSnapshotId = await reloadSnapshots');
  const captureGuardIndex = fetchBody.indexOf('if (!captureSnapshotId || analysisRequestRef.current !== captureRequestId) return;');
  const lineageRequestIndex = fetchBody.indexOf('const lineageRequestId = nextAnalysisRequestId();');
  const captureSelectionIndex = fetchBody.indexOf(
    'selectionRef.current = { snapshotId: captureSnapshotId, objectId: item.object_id };',
  );
  assert.ok(captureReloadIndex < captureGuardIndex, 'capture reload should be followed by a stale-token check');
  assert.ok(captureGuardIndex < lineageRequestIndex, 'capture flow must re-check before minting a rerun request');
  assert.ok(lineageRequestIndex < captureSelectionIndex, 'capture flow should only reset selectionRef after the guarded rerun starts');
  assert.match(fetchBody, /postLineage\(captureSnapshotId, \{/);

  const refreshBody = appFunctionBody(source, 'async function refreshAnalysisBasis', '\n  async function searchGlossary');
  assert.doesNotMatch(refreshBody, /await reloadSnapshots\(snapshot\.id, snapshot\);/);
  assert.match(
    refreshBody,
    /const refreshedSnapshotId = await reloadSnapshots\(snapshot\.id, snapshot, \{\s*preserveAnalysisSelection: true,\s*isStale: \(\) => analysisRequestRef\.current !== activeRequestId,\s*\}\);/,
    'refresh flow should carry its request token into reloadSnapshots',
  );
  const refreshReloadIndex = refreshBody.indexOf('const refreshedSnapshotId = await reloadSnapshots');
  const refreshGuardIndex = refreshBody.indexOf('if (!refreshedSnapshotId || analysisRequestRef.current !== activeRequestId) return;');
  const refreshClearIndex = refreshBody.indexOf('clearRenderedAnalysisStateForRefresh();');
  const rerunRequestIndex = refreshBody.indexOf('const rerunRequestId = nextAnalysisRequestId();');
  const refreshSelectionIndex = refreshBody.indexOf(
    'selectionRef.current = { snapshotId: refreshedSnapshotId, objectId: objectToRerun };',
  );
  const refreshPostLineageIndex = refreshBody.indexOf('postLineage(refreshedSnapshotId, {');
  const refreshPostImpactIndex = refreshBody.indexOf('postImpactScenario(refreshedSnapshotId, impactBody)');
  const refreshGetGlossaryIndex = refreshBody.indexOf('getGlossary(refreshedSnapshotId, glossaryQuery.trim() || undefined)');
  assert.ok(refreshReloadIndex < refreshGuardIndex, 'refresh reload should be followed by a stale-token check');
  assert.ok(refreshGuardIndex < refreshClearIndex, 'refresh flow must clear stale rendered state after the guarded token check');
  assert.ok(refreshClearIndex < rerunRequestIndex, 'refresh flow must clear stale rendered state before minting a rerun request');
  assert.ok(refreshGuardIndex < rerunRequestIndex, 'refresh flow must re-check before minting a rerun request');
  assert.ok(rerunRequestIndex < refreshSelectionIndex, 'refresh flow should only reset selectionRef after the guarded rerun starts');
  assert.ok(refreshClearIndex < refreshPostLineageIndex, 'lineage refresh must clear stale state before rerunning lineage');
  assert.ok(refreshClearIndex < refreshPostImpactIndex, 'impact refresh must clear stale state before rerunning impact');
  assert.ok(refreshClearIndex < refreshGetGlossaryIndex, 'glossary refresh must clear stale terms before reloading glossary');
  assert.match(refreshBody, /postLineage\(refreshedSnapshotId, \{/);
  assert.match(refreshBody, /postImpactScenario\(refreshedSnapshotId, impactBody\)/);
  assert.match(refreshBody, /getGlossary\(refreshedSnapshotId, glossaryQuery\.trim\(\) \|\| undefined\)/);
});

test('responsive shell does not force narrow viewports into horizontal overlap', () => {
  const css = readFileSync(new URL('../src/styles.css', import.meta.url), 'utf8');

  assert.doesNotMatch(
    css,
    /body\s*\{[^}]*min-width:\s*(?:9\d{2}|1\d{3})px/i,
    'body must not force 960px+ width on narrow laptops/tablets',
  );
  assert.match(
    css,
    /@media\s*\(max-width:\s*900px\)[\s\S]*?\.appFrame\s*\{[^}]*grid-template-columns:\s*1fr/i,
    'the main shell should collapse to one column below tablet width',
  );
  assert.match(
    css,
    /@media\s*\(max-width:\s*900px\)[\s\S]*?\.sliceGWorkspaceGrid\s*\{[^}]*grid-template-columns:\s*1fr/i,
    'Slice G workbench columns should stack below tablet width',
  );
  assert.match(
    css,
    /@media\s*\(max-width:\s*900px\)[\s\S]*?\.catalogPane\s*\{[^}]*position:\s*static/i,
    'the sticky sidebar should become an in-flow card on narrow screens',
  );
  assert.match(
    css,
    /@media\s*\(max-width:\s*700px\)[\s\S]*?\.topStatus\s*\{[^}]*grid-template-columns:\s*1fr/i,
    'the header status strip should stack on phone-sized widths',
  );

  const lastConnectionRule = css.lastIndexOf('.connectionOps li {');
  const lastMobileRule = css.lastIndexOf('@media (max-width: 700px)');
  assert.ok(lastMobileRule > lastConnectionRule, 'phone-width grid overrides must come after connectionOps rules');
  const finalMobileCss = css.slice(lastMobileRule);
  assert.match(
    finalMobileCss,
    /\.connectionOps li[\s\S]*?grid-template-columns:\s*1fr/,
    'connection diagnostics rows should remain one column on phone widths after cascade ordering',
  );
  assert.match(
    finalMobileCss,
    /\.captureOps li[\s\S]*?grid-template-columns:\s*1fr/,
    'capture operation rows should remain one column on phone widths after cascade ordering',
  );
});

test('Claude Design light workbench theme avoids dark override and keeps desktop panels readable', () => {
  const css = readFileSync(new URL('../src/styles.css', import.meta.url), 'utf8');

  assert.match(css, /Claude Design light workbench/i, 'stylesheet should document the light design pass');
  assert.doesNotMatch(css, /color-scheme:\s*dark/i, 'final workbench theme must not force dark color-scheme');
  assert.doesNotMatch(css, /Slice G Open Design direction:\s*dark/i, 'stale dark-workbench block should be removed');
  assert.doesNotMatch(css, /--bg:\s*#(?:0d0e15|0f1018|101119)/i, 'dark page background tokens must not override light theme');
  assert.match(
    css,
    /\.sliceGWorkspaceGrid\s*\{[^}]*grid-template-columns:\s*minmax\(240px,\s*280px\)\s+minmax\(420px,\s*1fr\)\s+minmax\(260px,\s*300px\)/i,
    'desktop Slice G workbench should use bounded, readable columns',
  );
  assert.match(
    css,
    /@media\s*\(max-width:\s*1280px\)[\s\S]*?\.sliceGWorkspaceGrid\s*\{[^}]*grid-template-columns:\s*minmax\(240px,\s*300px\)\s+minmax\(0,\s*1fr\)/i,
    'laptop Slice G layout should drop the drawer below before columns crowd',
  );
  assert.match(
    css,
    /\.lineageSummaryStrip\s*\{[^}]*background:\s*linear-gradient\([^;]*(?:#ffffff|#f8fbff)/i,
    'summary strip should use a bright surface instead of a dark panel',
  );
});

test('snapshot context and glossary writes are guarded by snapshot and request identity', () => {
  const source = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');

  assert.match(source, /const snapshotContextRequestRef = useRef\(0\);/);
  assert.match(source, /const glossarySearchRequestRef = useRef\(0\);/);

  const contextHelpers = appFunctionBody(source, 'function nextSnapshotContextRequestId', '\n  function clearAnalysisState');
  assert.match(contextHelpers, /snapshotContextRequestRef\.current \+= 1;/);
  assert.match(contextHelpers, /glossarySearchRequestRef\.current \+= 1;/);
  assert.match(contextHelpers, /selectionRef\.current\.snapshotId === snapshotId/);
  assert.match(contextHelpers, /function markSnapshotContextStale\(\)/);
  assert.match(contextHelpers, /setCaptureScope\(\[\]\);/);
  assert.match(contextHelpers, /setGlossaryTerms\(\[\]\);/);
  assert.doesNotMatch(
    contextHelpers,
    /analysisRequestRef|invalidateAnalysisRequests|clearAnalysisState/,
    'snapshot context stale clearing must not invalidate analysis request tokens',
  );

  const chooseBody = appFunctionBody(source, 'function chooseSnapshot', '\n  function parseLiveObjectNames');
  assert.match(
    chooseBody,
    /selectionRef\.current = \{ snapshotId, objectId: '' \};\s*markObjectsStaleForSnapshot\(snapshotId\);\s*markSnapshotContextStale\(\);\s*setSelectedSnapshotId\(snapshotId\);/,
    'normal snapshot selection must clear unkeyed snapshot context before rendering the new basis',
  );

  const reloadBody = appFunctionBody(source, 'async function reloadSnapshots', '\n  async function runLineage');
  assert.match(
    reloadBody,
    /preserveAnalysisSelection[\s\S]*markObjectsStaleForSnapshot\(nextSnapshotId\);\s*markSnapshotContextStale\(\);\s*setSelectedSnapshotId\(nextSnapshotId\);/,
    'preserved reloads must mark capture scope and glossary terms stale without calling chooseSnapshot',
  );

  const refreshContextBody = appFunctionBody(
    source,
    'async function refreshSnapshotContext',
    '\n  async function loadRepository',
  );
  assert.match(refreshContextBody, /const contextRequestId = nextSnapshotContextRequestId\(\);/);
  assert.match(refreshContextBody, /const glossaryRequestId = nextGlossarySearchRequestId\(\);/);
  assert.match(
    refreshContextBody,
    /if \(isCurrentSnapshotContextRequest\(contextRequestId, snapshotId\)\) \{\s*setCaptureScope\(scopeResponse\.items\);/,
    'capture scope must be set only for the current snapshot context request',
  );
  assert.match(
    refreshContextBody,
    /if \(GLOSSARY_VISIBLE && glossaryResponse && isCurrentGlossarySearchRequest\(glossaryRequestId, snapshotId\)\) \{\s*setGlossaryTerms\(glossaryResponse\.items\);/,
    'snapshot glossary terms must be set only when Glossary is visible and the glossary request is current',
  );
  assert.match(refreshContextBody, /const contextStillCurrent = isCurrentSnapshotContextRequest\(contextRequestId, snapshotId\);/);
  assert.match(refreshContextBody, /const glossaryStillCurrent = isCurrentGlossarySearchRequest\(glossaryRequestId, snapshotId\);/);
  assert.match(
    refreshContextBody,
    /if \(contextStillCurrent && \(!GLOSSARY_VISIBLE \|\| glossaryStillCurrent\)\) \{\s*setError\(errorText\(err\)\);/,
    'snapshot context errors must be guarded against stale snapshot or superseded visible glossary requests',
  );
  assert.doesNotMatch(
    refreshContextBody,
    /catch \(err\) \{\s*setCaptureScope\(\[\]\);\s*setGlossaryTerms\(\[\]\);\s*setError\(errorText\(err\)\);/,
    'refreshSnapshotContext must not clear context or set errors unguarded',
  );

  const searchBody = appFunctionBody(source, 'async function searchGlossary', '\n  async function runConnectionTest');
  assert.match(searchBody, /const requestSnapshotId = selectedSnapshotId;/);
  assert.match(searchBody, /const requestId = nextGlossarySearchRequestId\(\);/);
  assert.match(searchBody, /getGlossary\(requestSnapshotId, glossaryQuery\.trim\(\) \|\| undefined\)/);
  assert.match(
    searchBody,
    /if \(!isCurrentGlossarySearchRequest\(requestId, requestSnapshotId\)\) return;\s*setGlossaryTerms\(response\.items\);/,
    'searchGlossary must check the selected snapshot before setting terms',
  );
  assert.match(
    searchBody,
    /if \(isCurrentGlossarySearchRequest\(requestId, requestSnapshotId\)\) \{\s*setGlossaryTerms\(\[\]\);\s*setError\(errorText\(err\)\);/,
    'searchGlossary errors must be guarded by the same request and snapshot identity',
  );
});

test('derives compact change grade and impact summary', () => {
  const summary = sliceG.deriveImpactSummary({
    affected_objects: [
      { object_id: 'Q1', object_type: 'QUERY', severity: 'MEDIUM', evidence_ids: ['E1'], manual_verification: false },
      { object_id: 'A1', object_type: 'ADSO', severity: 'LOW', evidence_ids: ['E2', 'E3'], manual_verification: true },
    ],
    lineage_bounds: { truncated: false },
  });
  assert.equal(summary.grade, 'B');
  assert.equal(summary.affectedCount, 2);
  assert.equal(summary.severityCounts.MEDIUM, 1);
  assert.equal(summary.manualVerificationCount, 1);
  assert.equal(summary.evidenceCount, 3);
  assert.match(summary.headline, /2 affected/);

  const empty = sliceG.deriveImpactSummary(null);
  assert.equal(empty.grade, '—');
  assert.equal(empty.headline, 'Run impact to grade the selected change.');
});


test('additional BW object aliases and unknown breakdown are actionable', () => {
  assert.equal(sliceG.inferDisplayLayer({ type: 'DTPA' }).label, 'Transform');
  assert.equal(sliceG.inferDisplayLayer({ type: 'ALVL' }).label, 'Semantic');
  assert.equal(sliceG.inferDisplayLayer({ type: 'AGGR_LEVEL' }).label, 'Semantic');
  assert.equal(sliceG.inferDisplayLayer({ type: 'INFOSOURCE' }).label, 'Source');
  assert.equal(sliceG.inferDisplayLayer({ type: 'TRCS' }).label, 'Transform');

  assert.deepEqual(sliceG.unknownBreakdown([
    { id: 'missing', type: 'UNKNOWN', metadata: { unknown_reason: 'METADATA_MISSING' } },
    { id: 'unmapped', type: 'CUSTOM_WIDGET', metadata: { unknown_reason: 'TYPE_UNMAPPED' } },
    { id: 'parser', type: 'UNKNOWN', metadata: { unknown_reason: 'PARSER_UNSUPPORTED' } },
    { id: 'fresh', type: 'ADSO', metadata: { unknown_reason: 'FRESHNESS_UNAVAILABLE' } },
  ]), {
    metadata_missing: 1,
    type_unmapped: 1,
    parser_unsupported: 1,
    freshness_unavailable: 1,
    unknown: 0,
  });
});

test('object field helpers auto-select fields with manual fallback', () => {
  const fields = sliceG.objectFieldsFromMetadata({ fields: [
    { name: 'CUSTOMER_ID', type: 'CHAR', role: 'key' },
    { name: 'NET_VALUE', type: 'CURR', role: 'data' },
  ] });
  assert.deepEqual(fields.map((field) => field.name), ['CUSTOMER_ID', 'NET_VALUE']);
  assert.equal(sliceG.firstAutoFieldName(fields), 'CUSTOMER_ID');
  assert.equal(sliceG.nextImpactFieldName('NET_VALUE', fields), 'NET_VALUE');
  assert.equal(sliceG.nextImpactFieldName('AMOUNT', fields), 'CUSTOMER_ID');
  assert.equal(
    sliceG.nextImpactFieldName('NET_VALUE', [{ name: 'CUSTOMER_ID', type: 'CHAR' }]),
    'CUSTOMER_ID',
  );
  assert.equal(sliceG.nextImpactFieldName('NET_VALUE', [], { objectChanged: true }), 'AMOUNT');
  assert.equal(sliceG.nextImpactFieldName('MANUAL_FIELD', [], { objectChanged: false }), 'MANUAL_FIELD');
  assert.deepEqual(sliceG.objectFieldsFromMetadata({}), []);
  assert.equal(sliceG.firstAutoFieldName([]), '');
});

test('App clears stale impact fields synchronously when selection changes', () => {
  const source = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
  const resetIndex = source.indexOf('function resetImpactFieldSelection');
  const getFieldsIndex = source.indexOf('getObjectFields(selectedSnapshotId, selectedObjectId)');
  assert.notEqual(resetIndex, -1, 'impact field reset helper should be present');
  assert.notEqual(getFieldsIndex, -1, 'object fields fetch should remain present');
  assert.ok(resetIndex < getFieldsIndex, 'stale fields should be cleared before async object field fetch logic');

  const resetBody = appFunctionBody(source, 'function resetImpactFieldSelection', '\n  function setSelectedSnapshotId');
  assert.match(resetBody, /setObjectFields\(\[\]\);/);
  assert.match(resetBody, /setFieldName\('AMOUNT'\);/);
  assert.doesNotMatch(resetBody, /getObjectFields|await|async/, 'reset must not wait on field/detail fetches');

  const selectionSetters = appFunctionBody(source, 'function setSelectedSnapshotId', '\n  function nextAnalysisRequestId');
  assert.match(
    selectionSetters,
    /function setSelectedSnapshotId\(snapshotId: string\) \{\s*if \(snapshotId !== selectedSnapshotId\) \{\s*resetImpactFieldSelection\(\);[\s\S]*?\}\s*setSelectedSnapshotIdState\(snapshotId\);/,
    'snapshot changes should synchronously clear stale impact fields',
  );
  assert.match(
    selectionSetters,
    /function setSelectedObjectId\(objectId: string\) \{\s*if \(objectId !== selectedObjectId\) \{\s*resetImpactFieldSelection\(\);[\s\S]*?\}\s*setSelectedObjectIdState\(objectId\);/,
    'object changes should synchronously clear stale impact fields',
  );
});

test('App source exposes unified Impact evidence UI, field auto-select, evidence/business labels, and sticky/wrapping CSS', () => {
  const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
  const api = readFileSync(new URL('../src/api.ts', import.meta.url), 'utf8');
  const styles = readFileSync(new URL('../src/styles.css', import.meta.url), 'utf8');

  assert.match(app, /const IMPACT_UNIFIED: boolean = true;/);
  assert.match(app, /!IMPACT_UNIFIED \? <TabButton id="query" active=\{activeTab\} onClick=\{setActiveTab\} label="Query Analysis" \/> : null/);
  assert.match(app, /!IMPACT_UNIFIED \? <TabButton id="sql" active=\{activeTab\} onClick=\{setActiveTab\} label="SQL Analysis" \/> : null/);
  assert.match(app, /postImpactReview\(requestSnapshotId, impactReviewRequestBody\(requestObjectId\)\)/);
  assert.match(app, /<ImpactEvidenceCards review=\{props\.impactReview\} \/>/);
  assert.match(app, /Query exposure evidence/);
  assert.match(app, /SQL \/ Native SQL reference evidence/);
  assert.match(app, /No BW query execution · No data preview/);
  assert.match(app, /Parse only · DB execution disabled/);
  assert.match(app, /impact\.py remains the final authority for severity, confidence, affected objects, and manual verification\./);
  assert.match(app, /getObjectFields\(selectedSnapshotId, selectedObjectId\)/);
  assert.match(app, /fieldSelectionRef/);
  assert.match(app, /nextImpactFieldName/);
  assert.match(app, /<select value=\{props\.fieldName\}/);
  assert.match(api, /export interface ImpactReviewResponse/);
  assert.match(api, /postImpactReview/);
  assert.match(api, /queries\?: string\[\];/);
  assert.match(api, /queries: options\.queries \?\? \[\]/);
  assert.match(app, /Evidence Walkthrough/);
  assert.match(app, /Impact Brief/);
  assert.match(app, /Business Summary/);
  assert.doesNotMatch(app, />LLM notes</);
  assert.match(app, /topStatus \$\{topStatusScrolled \? 'scrolled' : ''\}/);

  assert.match(styles, /\.topStatus \{[\s\S]*?top: 0;/);
  assert.match(styles, /\.topStatus\.scrolled/);
  assert.match(styles, /\.detailsDrawer \{[\s\S]*?max-height: calc\(100vh - 96px\);[\s\S]*?overflow: auto;/);
  assert.match(styles, /\.objectItem strong[\s\S]*?-webkit-line-clamp: 2;/);
  assert.match(styles, /\.evidenceCard/);
  assert.match(styles, /\.authorityCallout/);
  assert.match(styles, /\.evidenceChips code \{[\s\S]*?overflow-wrap: anywhere;[\s\S]*?white-space: normal;/);
  assert.match(styles, /\.evidencePill, \.tourEvidenceList code \{[\s\S]*?overflow-wrap: anywhere;[\s\S]*?white-space: normal;/);
});

test('Slice 3 Impact IA is scenario-first with deterministic result hierarchy', () => {
  const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
  const api = readFileSync(new URL('../src/api.ts', import.meta.url), 'utf8');
  const impactBody = appFunctionBody(app, 'function ImpactTab(props:', '\nfunction AgenticReviewWorkspace');
  const changeTypeBlock = api.match(/export type ChangeType =([\s\S]*?);/);
  assert.ok(changeTypeBlock, 'ChangeType union should be present');
  const existingChangeTypes = new Set([...changeTypeBlock[1].matchAll(/'([^']+)'/g)].map((match) => match[1]));
  const cardBlockStart = app.indexOf('const impactScenarioCards');
  const cardBlockEnd = app.indexOf('const impactScenarioDefaultDescriptions', cardBlockStart);
  assert.notEqual(cardBlockStart, -1, 'scenario card definitions should be present');
  assert.notEqual(cardBlockEnd, -1, 'scenario card definitions should be bounded before derived defaults');
  const cardBlock = app.slice(cardBlockStart, cardBlockEnd);
  const cardChangeTypes = [...cardBlock.matchAll(/changeType: '([^']+)'/g)].map((match) => match[1]);
  assert.ok(cardChangeTypes.length >= 5, 'Slice 3 should expose the requested scenario cards');
  cardChangeTypes.forEach((changeType) => {
    assert.ok(existingChangeTypes.has(changeType), `${changeType} must reuse an existing backend ChangeType`);
  });
  [
    'ADSO / InfoObject field change',
    'Transformation logic change',
    'DTP / Process Chain change',
    'CompositeProvider / Query change',
    'Recent load / freshness risk',
  ].forEach((title) => assert.match(cardBlock, new RegExp(title.replace(/\//g, '\\/'))));

  const controlStart = impactBody.indexOf('<section className="controlCard impactScenarioWorkspace">');
  const cardsStart = impactBody.indexOf('<div className="impactScenarioCards"');
  const advancedStart = impactBody.indexOf('<details className="advancedSection evidenceScopeAdvanced">');
  assert.notEqual(controlStart, -1, 'Impact controls should use the scenario workspace');
  assert.notEqual(cardsStart, -1, 'scenario cards should be the lead control');
  assert.notEqual(advancedStart, -1, 'advanced evidence scope should be present');
  assert.ok(controlStart < cardsStart, 'scenario cards should appear near the top of controls');
  assert.doesNotMatch(
    impactBody.slice(controlStart, advancedStart),
    /<label>Change type/,
    'raw Change type dropdown must no longer be the lead/default control',
  );

  assert.match(impactBody, /activeScenario\.fieldOriented \? \(/, 'field controls should be conditional by scenario');
  assert.match(impactBody, /aria-label="field-oriented scenario controls"/);
  assert.match(impactBody, /aria-label="field selector not required"/);
  assert.match(app, /const field = isFieldOrientedChangeType\(changeType\) \? fieldName\.trim\(\) \|\| null : null;/);

  const defaultControls = impactBody.slice(controlStart, advancedStart);
  ['Impact depth', 'Query evidence names', 'SQL text', 'SQL file'].forEach((label) => {
    assert.doesNotMatch(defaultControls, new RegExp(label), `${label} should not be in default controls`);
  });
  const advancedControls = impactBody.slice(advancedStart, impactBody.indexOf('<button className="primaryButton wide"', advancedStart));
  assert.match(advancedControls, /<summary>Evidence scope \(Advanced\)<\/summary>/);
  assert.match(advancedControls, /<NumberField label="Impact depth"/);
  assert.match(advancedControls, /Query evidence names/);
  assert.match(advancedControls, /SQL text/);
  assert.match(advancedControls, /SQL file/);
  assert.match(impactBody, /영향 보기 \/ Assess Impact/);

  const gradeIndex = impactBody.indexOf('aria-label="change grade impact summary"');
  const severityIndex = impactBody.indexOf('<section className="affectedSeverityGroups"');
  const evidenceIndex = impactBody.indexOf('<ImpactEvidenceCards review={props.impactReview} />');
  const manualIndex = impactBody.indexOf('<ManualVerificationChecklist review={props.impactReview} impact={props.impact} />');
  const assistantIndex = impactBody.indexOf('<AssistantPresetLinks', manualIndex);
  [gradeIndex, severityIndex, evidenceIndex, manualIndex, assistantIndex].forEach((index) => {
    assert.notEqual(index, -1, 'result hierarchy anchor should be present');
  });
  assert.ok(
    gradeIndex < severityIndex && severityIndex < evidenceIndex && evidenceIndex < manualIndex && manualIndex < assistantIndex,
    'Impact result hierarchy should be grade -> severity groups -> evidence cards -> manual checklist -> assistant links',
  );
  assert.match(impactBody, /impactSeverityOrder\.map/);
  assert.match(impactBody, /label: 'Ask BW \/ Review'/, 'Ask BW / Review should remain an assistant preset link in Impact');
  assert.doesNotMatch(impactBody, /<AgenticReviewWorkspace/, 'Impact must not nest the agentic review workspace');
});

test('Review assistant API clients are typed and post to citation-bound review routes', () => {
  const api = readFileSync(new URL('../src/api.ts', import.meta.url), 'utf8');

  [
    'export interface AssistantEvidenceContext',
    'export interface AssistantManualCheck',
    'export interface AssistantSafety',
    'export interface AssistantReviewRequest',
    'export interface AssistantReviewResponse',
    'export interface AgenticReviewRun',
    'export interface ReviewObjective',
    'export interface ReviewHypothesis',
    'export interface EvidenceGap',
    'export interface ManualCheck',
    'export interface AgenticReviewCard',
    'export interface ReviewTraceStep',
    'export interface AgenticReviewBudget',
    'export interface AgenticReviewBudgetUsage',
    'export interface EvidenceRequestDecision',
    'export interface LlmAuditMetadata',
    'export interface AgenticReviewRequest',
  ].forEach((typeName) => assert.match(api, new RegExp(typeName)));

  assert.match(api, /deterministic_pack: ImpactReviewResponse;/);
  assert.match(api, /export async function postAssistantReview\(/);
  assert.match(api, /\/api\/v1\/snapshots\/\$\{encodeURIComponent\(snapshotId\)\}\/assistant\/review/);
  assert.match(api, /export async function postAgenticReview\(/);
  assert.match(api, /\/api\/v1\/snapshots\/\$\{encodeURIComponent\(snapshotId\)\}\/impact\/review\/agentic/);
});

test('Slice 1 IA centers Lineage, Impact, and Ask BW Review with one primary run CTA per analysis tab', () => {
  const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
  const api = readFileSync(new URL('../src/api.ts', import.meta.url), 'utf8');
  const styles = readFileSync(new URL('../src/styles.css', import.meta.url), 'utf8');

  assert.match(api, /export type AppTab = 'lineage' \| 'impact' \| 'ask' \| 'query' \| 'sql' \| 'glossary';/);
  assert.match(app, /BW Lineage \/ Impact \/ Ask BW Review/);
  assert.match(app, /Enterprise metadata workbench · local-first · evidence-bound LLM/);
  assert.match(
    app,
    /<TabButton id="lineage" active=\{activeTab\} onClick=\{setActiveTab\} label="Lineage" \/>[\s\S]*?<TabButton id="impact" active=\{activeTab\} onClick=\{setActiveTab\} label="Impact" \/>[\s\S]*?<TabButton id="ask" active=\{activeTab\} onClick=\{setActiveTab\} label="Ask BW \/ Review" \/>/,
    'task-first tabs should render as Lineage, Impact, Ask BW / Review',
  );
  assert.match(app, /<WorkspaceContextBar[\s\S]*?activeTab=\{activeTab\}/);
  assert.match(app, /Read-only metadata · no BW query execution · no data preview · local-first · evidence-bound LLM/);
  assert.match(styles, /\.appFrame\s*\{[^}]*grid-template-columns:\s*252px\s+minmax\(0,\s*1fr\)/);
  assert.match(styles, /\.workspaceContextBar\s*\{[\s\S]*?min-height:\s*56px;/);

  const lineageBody = appFunctionBody(app, 'function LineageTab(props:', '\nfunction ImpactTab');
  const impactBody = appFunctionBody(app, 'function ImpactTab(props:', '\nfunction AgenticReviewWorkspace');
  assert.equal((lineageBody.match(/className="primaryButton wide"/g) ?? []).length, 1, 'Lineage should keep one primary CTA');
  assert.equal((impactBody.match(/className="primaryButton wide"/g) ?? []).length, 1, 'Impact should keep one primary CTA');
  assert.match(lineageBody, /<AssistantPresetLinks[\s\S]*?title="Assistant presets"/);
  assert.match(impactBody, /<AssistantPresetLinks[\s\S]*?title="Assistant presets"/);
  assert.doesNotMatch(lineageBody, /<button className="secondaryButton wide"[\s\S]*?Evidence Walkthrough/);
  assert.doesNotMatch(impactBody, /<button className="secondaryButton wide"[\s\S]*?Impact Brief/);
  assert.match(impactBody, /<details className="advancedSection evidenceScopeAdvanced">[\s\S]*?<summary>Evidence scope \(Advanced\)<\/summary>/);
});

test('Ask BW / Review tab hosts AgenticReviewWorkspace outside deterministic Impact tab', () => {
  const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
  const api = readFileSync(new URL('../src/api.ts', import.meta.url), 'utf8');
  const askReviewBody = appFunctionBody(app, 'function AskReviewTab(props:', '\nfunction LineageTab');
  const impactBody = appFunctionBody(app, 'function ImpactTab(props:', '\nfunction AgenticReviewWorkspace');

  assert.match(app, /function AgenticReviewWorkspace\(props:/);
  assert.match(app, /<TabButton id="ask" active=\{activeTab\} onClick=\{setActiveTab\} label="Ask BW \/ Review" \/>/);
  assert.match(app, /activeTab === 'ask' \? \([\s\S]*?<AskReviewTab/);
  assert.match(
    askReviewBody,
    /<span className="eyebrow">Ask BW \/ Review<\/span>[\s\S]*?<AgenticReviewWorkspace[\s\S]*?review=\{props\.agenticReview\}/,
    'agentic workspace must render inside the Ask BW / Review surface',
  );
  assert.match(
    impactBody,
    /<ImpactEvidenceCards review=\{props\.impactReview\} \/>[\s\S]*?<AuthorityCallout review=\{props\.impactReview\} \/>/,
    'Impact should keep deterministic evidence cards and authority callout',
  );
  assert.doesNotMatch(impactBody, /<AgenticReviewWorkspace/, 'agentic workspace must not render inside the Impact panel');
  assert.match(api, /export type AppTab = 'lineage' \| 'impact' \| 'ask'/);
  assert.doesNotMatch(api, /export type AppTab = [^;]*'agentic'/, 'legacy top-level agentic tab id must not return');
  assert.doesNotMatch(
    app,
    /<TabButton id="agentic"/,
    'agentic workspace must not introduce a new top-level tab',
  );
});

test('Agentic review workspace preserves banners, provenance labels, copy boundaries, and required section headings', () => {
  const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
  const styles = readFileSync(new URL('../src/styles.css', import.meta.url), 'utf8');

  [
    'Ask BW / Review',
    'Citation-bound answer area',
    'LLM disabled — deterministic assistant fallback',
    'LLM validation fallback — deterministic assistant answer',
    'LLM disabled — deterministic findings only',
    'Autonomous review failed validation — showing deterministic findings',
    'Deterministic finding',
    'LLM proposed concern',
    'Manual verification required',
    'Read-only metadata',
    'No BW query execution',
    'No data preview',
    'Local-first · evidence-bound LLM',
    'Review objective',
    'Facts, review cards, and citations',
    'Evidence map',
    'Missing evidence / gaps',
    'Manual BWMT checklist',
    'Safety + validation',
    'Status without audit log details',
    'unified assistant review endpoint',
    'no live BW calls, query execution, data preview, or raw snapshot payload',
    'suggested_local_action',
  ].forEach((literal) => assert.match(app, new RegExp(literal.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))));
  assert.doesNotMatch(app, /Audit citation validation|prompt_sha256=|sanitized_input_sha256=|response_id=|Validator \+ budget \+ audit/);

  [
    '.workspaceContextBar',
    '.assistantPresetButton',
    '.assistantAnswerArea',
    '.agenticAdvancedEvidence',
    '.agenticWorkspace',
    '.agenticBanner',
    '.agenticGrid',
    '.agenticCard',
    '.provenanceBadge',
    '.citationChip',
    '.budgetGrid',
  ].forEach((className) => assert.match(styles, new RegExp(className.replace('.', '\\.'))));
});

test('Agentic review request guard and busy invalidation follow existing analysis patterns', () => {
  const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
  const body = appFunctionBody(app, 'async function runAgenticReview', '\n  async function runImpactAdvice');
  const runImpactBody = appFunctionBody(app, 'async function runImpact', '\n  async function runAgenticReview');

  assert.match(body, /const requestSnapshotId = selectedSnapshotId;/);
  assert.match(body, /const requestObjectId = selectedObjectId;/);
  assert.match(body, /const requestId = nextAnalysisRequestId\(\);/);
  assert.match(body, /setBusy\('impact-agentic'\);/);
  assert.match(body, /postImpactReview\(requestSnapshotId, impactReviewRequestBody\(requestObjectId\)\)/);
  assert.match(body, /postAssistantReview\(requestSnapshotId, \{[\s\S]*prompt: agenticQuestion\.trim\(\) \|\| 'Review selected BW lineage and impact evidence\.',[\s\S]*preset: assistantPreset,[\s\S]*context: buildAssistantContexts/);
  assert.match(body, /if \(!isCurrentAnalysisRequest\(requestId, requestSnapshotId, requestObjectId\)\) return;/);
  assert.match(body, /setAssistantReview\(response\);/);
  assert.match(body, /setImpactReview\(deterministicPack\);/);
  assert.match(body, /finally \{[\s\S]*?setBusy\(''\);[\s\S]*?\}/);
  assert.match(app, /'impact-agentic'/);
  assert.match(app, /agenticBusy=\{busy === 'impact-agentic'\}/);
  assert.match(runImpactBody, /setAgenticReview\(null\);/, 'regular Impact scenarios must clear stale agentic results');
  assert.match(runImpactBody, /setAssistantReview\(null\);/, 'regular Impact scenarios must clear stale assistant results');
});
