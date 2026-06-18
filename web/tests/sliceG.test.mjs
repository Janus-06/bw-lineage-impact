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
  assert.match(source, /function isCurrentAnalysisRequest\(requestId: number, snapshotId: string, objectId: string\): boolean/);
  assert.match(
    source,
    /\['lineage', 'lineage-advice', 'lineage-tour', 'impact', 'impact-advice', 'impact-tour', 'live-analyze', 'refresh-bw'\]\.includes\(current\)/,
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
    /if \(isCurrentGlossarySearchRequest\(glossaryRequestId, snapshotId\)\) \{\s*setGlossaryTerms\(glossaryResponse\.items\);/,
    'snapshot glossary terms must be set only for the current glossary request',
  );
  assert.match(refreshContextBody, /const contextStillCurrent = isCurrentSnapshotContextRequest\(contextRequestId, snapshotId\);/);
  assert.match(refreshContextBody, /const glossaryStillCurrent = isCurrentGlossarySearchRequest\(glossaryRequestId, snapshotId\);/);
  assert.match(
    refreshContextBody,
    /if \(contextStillCurrent && glossaryStillCurrent\) \{\s*setError\(errorText\(err\)\);/,
    'snapshot context errors must be guarded against stale snapshot or superseded glossary requests',
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
