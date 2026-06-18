# BW Lineage Impact — UX·메타데이터·쿼리분석 보강 구현 플랜

> **For Hermes:** Use subagent-driven-development or Codex OMX slice-by-slice to implement this plan task-by-task.
>
> **Source:** Claude Code `--model opus`; verified `modelUsage` includes `claude-opus-4-8`.
> **Raw run artifact:** `.hermes/plans/claude-runs/2026-06-18-opus48-ui-api-rework-plan/result.md`.
> **Status:** planning-only; no implementation changes.

---

## BW Lineage Impact — UX·메타데이터·쿼리분석 보강 구현 플랜 (Opus 4.8)

> 모드: **계획 전용**. 파일 미수정. 본 문서는 9개 피드백 항목을 코드 사실에 정렬한 슬라이스로 변환한 것입니다.
> 사전 확인: `main@ab32c54` clean. 구현 PR별 검증 기준은 `uv run pytest -q`, `uv run ruff check .`, `uv run mypy src`, `npm --prefix web run test:slice-g`, `npm --prefix web run build`.
> 불변식: BW 런타임 GET-only, 변형(POST/PUT/DELETE/run/activate/transport) 금지, LLM 기본 비활성·sanitized·인용 결속, 시크릿/쿠키 비영속.

---

## 1. 현재 진단 (코드 사실 기반)

각 피드백 항목의 **근본 원인을 실제 식별자/라인**에 정렬:

| # | 피드백 | 근본 원인 (코드 근거) |
|---|---|---|
| 1 | unknown 항목 과다 | `store/catalog.py` xref 인게스트(`_ingest_xref_payload`/`_ingest_xref_xml`)가 양 끝 노드를 `type="UNKNOWN"`으로 생성(1496–1500, 1520–1521). DTP/datasource 인게스트도 source/target type 미상 시 `"UNKNOWN"`(1114, 1148). `_MutableCatalog.add_object`는 기존 UNKNOWN을 후속 타입으로만 보강(1898)하므로, 후속 증거가 없으면 UNKNOWN 잔존. 프런트 `sliceG.ts inferDisplayLayer`는 미매핑 타입을 `'Unknown'` 레인으로 떨굼(128). **즉 "unknown"은 ① 메타데이터 미수집(증거 부족) ② 타입 키 미매핑 ③ 파서 형태 미지원의 혼재인데, 현재 셋을 구분하지 않고 한 버킷에 표시.** |
| 2 | 오브젝트명 잘림 | `web/src/styles.css`: `.objectItem strong`(170), `.searchResultMain strong`(298), `.repoNode strong`(265), `.summaryHero strong`(643), `.metric strong`(195), `.sectionTitle h1/.controlCard h1`(135)이 모두 `white-space:nowrap; overflow:hidden; text-overflow:ellipsis`. BW 기술명(예: `ZADSO_SALES_MARGIN_DETAIL`)이 패널 폭에서 잘림. tooltip(`title`) 부재. |
| 3 | 우측 드로어 텍스트 오버랩 | 우측 컬럼 폭 `minmax(260px,300px)`(`.sliceGWorkspaceGrid` 629). `.evidencePill, .tourEvidenceList code`(742)와 `.freshnessBadge`(678)가 `white-space:nowrap` → 긴 evidence id/타임스탬프가 300px 드로어를 넘침. `.detailsDrawer`는 `position:sticky`(228)라 컨테이너 오버플로 시 형제 위로 겹쳐 보임. |
| 4 | 최상단 바 부자연 | `.topStatus { position:sticky; top:10px; z-index:10; backdrop-filter:blur(10px) }`(50–64). `top:10px` 갭 + 블러 동시 적용으로 스크롤 시 "툭 붙는" 점프. 진입/고정 전환 트랜지션·스크롤 그림자 없음. |
| 5 | MCP API 재분석/호출테스트 | 우리 `client.fetch_query`는 **404만** active→inactive 폴백(`client.py` 172–179)하지만, 참조 `src/tools/query.ts queryAccept()`(19–22)는 **discovery 문서의 `MEDIA_TYPES['query']`를 우선**한 뒤 정적 버전 범위를 폴백. 우리 `endpoints.py`엔 discovery/`MEDIA_TYPES` 개념 자체가 없음 → 406/415(미디어타입 불일치) 미처리. 라이브 스모크는 `bw_search/dataflow/xref` 3종만(`live.py run_live_smoke` 132–174). query/dtp/datasource/processchain/request 경로의 **읽기 전용 계약 테스트 부재**. |
| 6 | tour/llm notes 모호 | `lineage/tour`·`impact/tour`·`*/advice` 라우트(`server.py` 868–956) 존재하나 UI 라벨이 "tour", "LLM notes"로 기능 의도 불명. LLM 비활성 시 의미 있는 결정적 대체물 빈약. |
| 7 | 글로서리 집계/별도 DB | `glossary_terms`가 **catalog.sqlite 동일 파일**의 테이블(`_init_schema` 629–647). 집계 카운트(후보/확정/소스별) API 없음. dedupe는 `term_id` PK 수준에 그침. 별도 DB 경계 부재. |
| 8 | 임팩트 필드 수동입력 | `App.tsx`의 `fieldName` 기본 `'AMOUNT'` 자유 입력(158), `impactRequestBody.field`(1106). 선택 오브젝트의 필드를 자동 제시하는 경로 없음. **데이터 측: `_ingest_datasource_xml`만 `metadata["fields"]` 채움(1207). ADSO 인게스트 함수 자체가 catalog에 없음**(client엔 `fetch_adso` 있으나 `_ingest_adso_xml` 부재), HCPR/QUERY도 필드 미추출 → 자동 필드의 소스가 RSDS로 한정. |
| 9 | 쿼리분석이 쿼리명→내용 미작동 | **UI엔 Query 탭이 없음.** `AppTab='lineage'|'impact'|'sql'|'glossary'`(`api.ts` 5). "sql" 탭은 `NATIVE_SQL_VIEW`용(`view_id`+`sql_file/sql_text`, `sql/explain`·`sql/draft` 957–984)으로 **BEx 쿼리와 무관**. 백엔드 `_ingest_query_xml`은 쿼리 id+provider 링크만 추출(1247–1289)하고 **variables/CKF/RKF/restricted/local member를 버림**. 반면 참조 `query.ts`는 이 전부를 파싱(`bwGetQuery`). 사용자 기대(쿼리명 입력→쿼리 내용)와 구현 사이 큰 격차. |

---

## 2. Unknown 처리 정책

### 2.1 Unknown 분류 체계 (4종 구분)

현재 단일 `"UNKNOWN"`을 **출처가 다른 4개 사유**로 분해. `metadata["unknown_reason"]`(신규, 결정적)로 태깅:

| 코드 | 의미 | 결정적 판별 규칙 | 해소 경로 |
|---|---|---|---|
| `METADATA_MISSING` | BW 메타데이터를 아직 안 가져옴(증거 부족) | 노드가 xref/dataflow 끝점으로만 등장, 자기 자신의 fetch 증거(`*:adso`/`*:hcpr`/`*:query` 등) 없음 | 해당 오브젝트를 캡처 대상에 추가 → 타입 확정 |
| `TYPE_UNMAPPED` | 타입 문자열은 있으나 레인/표시 매핑이 없음 | `type != "UNKNOWN"` 이지만 `sliceG TYPE_LAYER_ALIASES` 미스 | `layers.py`(백엔드)·`TYPE_LAYER_ALIASES`(프런트)에 매핑 추가(결정적) |
| `PARSER_UNSUPPORTED` | payload는 받았으나 파서가 형태를 못 읽음 | 인게스트가 빈 `IngestedCatalog` 반환 + 해당 kind 증거 존재 | 파서 보강(슬라이스 C2) |
| `FRESHNESS_UNAVAILABLE` | 메타는 있으나 request 신선도만 부재 | `request_freshness` 없음/권한오류 | 선택 기능, 신선도 미표시(그래프 정상) |

### 2.2 신뢰도/증거 노출
- 각 노드에 `metadata["evidence_ids"]`는 이미 존재. UI는 **Unknown 칩 클릭 시** `unknown_reason` + 보유 증거 수 + "어떻게 해소"(예: "이 오브젝트를 가져오면 타입 확정") 액션을 표시.
- 집계: 그래프/스냅샷 헤더에 `unknown_breakdown = {metadata_missing, type_unmapped, parser_unsupported}` 카운트. **"Unknown N개"가 아니라 "미수집 3 · 미매핑 1 · 파서 0"** 로 표기 → 사용자가 행동 가능.

### 2.3 결정적 Unknown 감축 (LLM 무관)
1. **타입 추론 보강**: xref/dataflow 끝점 노드가 후속에 ADSO/HCPR/QUERY 등으로 재등장하면 `add_object` 병합으로 이미 보강됨(1898). 추가로 **이름 규칙 기반 보수적 추론**(예: 접두 `8`,`/BIC/`, `2LIS_`→RSDS)을 `metadata`에만 `inferred_type`로 부착(원본 `type`은 보존, 신뢰도 표기).
2. **레인 매핑 확장**: `TYPE_LAYER_ALIASES`에 RSPC/ALVL/AGGRLEVEL/INFOSOURCE/TRCS 등 누락 키 추가.
3. **타입 매핑 단일 출처화**: 백엔드 `layers.py`(신설) ↔ 프런트 `sliceG.ts`가 동일 매핑 테이블을 공유하도록 `web`이 백엔드 제공 `layer` 필드를 우선 사용(현재 프런트 단독 추론).

---

## 3. BW Modeling MCP API 재분석 + 읽기 전용 호출 테스트

### 3.1 1:1 비교 대상 (참조 `/tmp/bw-modeling-mcp-latest`)
| 우리 코드 | 참조 | 격차 |
|---|---|---|
| `endpoints.build_query_endpoint`(213) + `client.fetch_query`(172, 404 폴백) | `tools/query.ts bwGetQuery`(354) + `queryAccept()`(19) | 우리는 **discovery 미디어타입 미반영**, 406/415 폴백 없음 |
| `ACCEPT_HEADERS`(31) 정적 | `bw-client.ts MEDIA_TYPES`(discovery로 채움) | discovery 개념 부재 |
| `_ingest_query_xml`(1247) | `query.ts` 전체 파서 | variables/CKF/RKF/filter/layout/exceptions 미추출 |
| `fetch_datasource/source_system/dtp/process_chain`(166–202) | `tools/datasource.ts`·`dtp.ts`·`processchain.ts` | 경로/Accept는 정렬됨, 응답 필드 커버리지 점검 필요 |
| `fetch_list_requests`/`fetch_request`(184–202) | `tools/request_monitor.ts` | read-only 정렬 확인됨, 가드 테스트 필요 |

거부 목록(불변): `activation.ts`(`bw_activate*`), `delete.ts`, `push.ts`, `roles.ts`(`bw_set_query_roles`), `infoobject.ts`/`infosource.ts`/`transformation.ts`/`dtp.ts`의 create/update, `processvariant.ts` 변형 — **코드 어디에도 미노출**(가드 테스트로 강제).

### 3.2 추가할 읽기 전용 스모크/계약 테스트 (모킹, 라이브 무접속)
- `tests/test_client.py`:
  - `test_fetch_query_prefers_discovered_media_type_then_falls_back`
  - `test_fetch_query_negotiates_on_406_415_then_404`
  - `test_client_public_surface_has_no_mutating_verbs`(가드: `BwClient` public 메서드명에 create/update/activate/delete/run/push/unlock/move 부재)
- `tests/test_endpoints.py`: `test_query_endpoint_active_then_inactive_paths`, `test_request_monitor_endpoints_get_only_with_top_cap`
- `tests/test_live.py`: `test_run_live_smoke_covers_query_datasource_processchain_when_requested`(스모크 작업에 read 경로 추가), `test_live_collection_isolates_per_object_failures`(기존 격리 패턴 회귀)
- `tests/test_catalog_ingest.py`: 신규/보강 파서별 골든 픽스처 케이스.

### 3.3 미디어타입 discovery 폴백 설계
- `endpoints.py`에 `negotiate_accept(kind, *, discovered: str|None)` 순수 함수 추가: `discovered`가 있으면 선두에 두고 정적 범위 뒤따름(참조 `queryAccept` 이식). discovery 문서 fetch는 **선택**(없으면 정적 범위만; 오프라인/테스트 무영향).
- `client._fetch_with_fallback`의 `fallback_statuses`에 `{404, 406, 415}` 확장(쿼리 한정), 각 후보 endpoint를 순차 시도.

### 3.4 라이브 안전 테스트 원칙
- 모든 신규 테스트는 `FakeLiveBwClient`/transport mock + `tests/fixtures/query.xml` 등 골든. 실 BW 미접속. 라이브 검증은 사용자 환경에서 `POST /api/v1/connection/test`(이미 GET-only) 수동 실행으로 분리.

---

## 4. UI/UX 재설계

### 4.1 오브젝트명 가시성 (#2)
- `.objectItem strong`/`.searchResultMain strong`/`.repoNode strong`/`.summaryHero strong`에서 **단일 라인 nowrap 제거** → 2줄 클램프(`display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; word-break:break-all`).
- 모든 기술명 요소에 `title={id}`(네이티브 tooltip) 추가(`App.tsx` 렌더).
- 목록 행: 타입 칩 폭 고정 유지, 이름 영역 `min-width:0`로 클램프 동작 보장(이미 grid `minmax(0,1fr)` 적용됨 — 클램프만 추가).
- 카탈로그 행 hover 시 전체 이름 표시(이미 `title`로 충분).

### 4.2 우측 드로어 긴 텍스트 래핑 (#3)
- `.evidencePill, .tourEvidenceList code, .freshnessBadge`의 `white-space:nowrap` → `white-space:normal; overflow-wrap:anywhere`.
- `.detailsDrawer/.sliceGDrawer`: `position:sticky` 유지하되 `max-height: calc(100vh - 96px); overflow:auto`로 내부 스크롤(형제 위 겹침 방지).
- evidence id 칩은 `max-width:100%`로 줄바꿈, 길면 중간 생략(`text-overflow` 대신 wrap).

### 4.3 최상단 sticky 바 자연화 (#4)
- `.topStatus { top:10px }` → `top:0` + `.appShell` 상단 패딩으로 갭 흡수.
- 스크롤 상태에 따라 그림자만 강해지는 방식: 컨테이너에 `transition: box-shadow .18s ease, background .18s ease`; 스크롤 감지(`window.scrollY>4`)로 `.topStatus.scrolled` 토글(JS, `App.tsx` scroll listener) → 블러 점프 대신 부드러운 그림자 등장.
- `backdrop-filter` 유지하되 `will-change: box-shadow`로 리페인트 안정화.

### 4.4 밝은 엔터프라이즈 레이아웃 정련
- 기존 라이트 토큰(`:root` 464–489) 유지. Unknown 분해 배지·필드 칩에 동일 토큰(`--blue-soft`, `--warn` 등) 적용해 일관성.

### 4.5 접근성
- tooltip은 `title` + `aria-label`. Unknown/freshness 배지에 `role="status"`/명시 라벨. 탭 키 순서: 카탈로그→탭바→컨트롤→드로어.
- 색만으로 의미 전달 금지: freshness/severity에 텍스트 라벨 병기(이미 일부 적용).

### 4.6 반응형 브라우저 스모크 매트릭스
- 폭 1440 / 1280 / 900 / 700px (기존 미디어쿼리 경계와 일치, 325·816·878 라인) × Chromium·WebKit.
- 체크: 최상단 바 비겹침, 우측 드로어 내부 스크롤, 긴 이름 클램프, 탭바 가로 스크롤. 산출물: 스크린샷 + 콘솔 에러 0.

---

## 5. Tour / LLM notes → 의미 있는 기능 재설계 (#6)

### 5.1 리네이밍/포지셔닝
| 현재 | 신규 | 성격 |
|---|---|---|
| Lineage "tour" | **Evidence Walkthrough**(증거 워크스루) | 결정적: 그래프 노드/엣지를 레이어 순서로 단계화. LLM 없이 동작 |
| Impact "tour" | **Impact Brief**(영향 브리핑) | 결정적 요약(영향 객체 수·최고 심각도·신선도) + 선택적 LLM 산문 |
| `*/advice` "LLM notes" | **Business Summary**(업무 요약, opt-in) | LLM 활성 시에만, sanitized·인용 결속 |

### 5.2 결정적 대체물 (LLM off가 기본)
- `lineage/tour`·`impact/tour`가 LLM 비활성 시 **빈 advisory가 아니라** 결정적 워크스루를 반환: 단계 = 레이어 그룹(Source→…→Runtime), 각 단계 `node_ids`/`edge_ids`/`evidence_ids`는 실 그래프에서 채움(프런트 `normalizeGuidedTourSteps`가 이미 이 형태 소비, `sliceG.ts` 188).
- Impact Brief는 `deriveImpactSummary`(sliceG 224) 결과를 그대로 노출 — 이미 결정적.

### 5.3 인용 결속 LLM 거동
- LLM 단계는 **그래프에 실재하는 node id만** 인용(검증 게이트). sanitizer 화이트리스트 유지.

### 5.4 테스트
- `tests/test_server.py`: `test_lineage_tour_returns_deterministic_walkthrough_when_llm_disabled`, `test_impact_brief_deterministic_fields_present`.
- `tests/test_llm.py`: `test_business_summary_requires_sanitized_cited_evidence`, `test_walkthrough_steps_reference_only_present_node_ids`, `test_llm_disabled_by_default_no_network`.
- `web/tests/sliceG.test.mjs`: `normalizeGuidedTourSteps`가 결정적 워크스루 payload를 단계로 변환하는 케이스.

---

## 6. 글로서리 집계 + 별도 DB (#7)

### 6.1 별도 DB 경계
- 신규 `bwli/store/glossary_store.py` + `glossary.sqlite`(경로: `catalog_path_for` 형제 — `BWLI_HOME`/`.bwli/glossary.sqlite`). catalog.sqlite의 `glossary_terms` 테이블은 **읽기 호환 유지**하되, 집계/확정 라이프사이클은 신규 store로 이관(이중쓰기 마이그레이션 → 후속 단일화).
- 로컬 전용, 시크릿 비저장(`assert_no_persisted_secrets` 적용).

### 6.2 스키마 제안 (`glossary.sqlite`)
```sql
CREATE TABLE glossary_terms (
  term_id TEXT, snapshot_id TEXT, term TEXT, normalized_term TEXT,
  source TEXT, lifecycle TEXT,            -- candidate|confirmed|rejected
  object_id TEXT, object_type TEXT, field_name TEXT,
  occurrences INTEGER DEFAULT 1, evidence_ids_json TEXT, metadata_json TEXT,
  first_seen TEXT, last_seen TEXT, PRIMARY KEY(normalized_term, object_id, field_name));
CREATE INDEX idx_gloss_norm ON glossary_terms(normalized_term);
CREATE INDEX idx_gloss_life ON glossary_terms(lifecycle, source);
CREATE TABLE glossary_aggregates (    -- 사전 집계
  normalized_term TEXT PRIMARY KEY, display_term TEXT, total INTEGER,
  candidate INTEGER, confirmed INTEGER, object_count INTEGER, last_seen TEXT);
```

### 6.3 인게스트 소스 & 라이프사이클
- 소스: 기존 metadata 글로서리(`_replace_metadata_glossary_terms`) + SQL explain 추출(`_sql_glossary_terms`) + datasource/ADSO/query 필드명·설명.
- 라이프사이클: `candidate`(자동 추출) → 사용자 확인 시 `confirmed` → `rejected`. dedupe 키 = `(normalized_term, object_id, field_name)`, 재등장 시 `occurrences++` + `last_seen` 갱신.

### 6.4 집계 카운트 / API / UI
- API: `GET /api/v1/glossary/aggregate`(전역 집계: total/candidate/confirmed/object_count), 스냅샷별은 기존 `…/glossary` 확장(`counts` 블록 추가).
- 변형은 `confirm/reject`만 **로컬 DB 쓰기**(BW write 아님 — 불변식 무관); 별도 라우트 `POST /api/v1/glossary/{term_id}/lifecycle` (로컬 전용 명시).
- UI: glossary 탭 상단에 집계 메트릭 3종(`.glossaryMetrics` 이미 존재 304), 행에 confirm/reject 액션(이미 `.glossaryActions` 313).

### 6.5 마이그레이션/백필
- 최초 기동 시 catalog.sqlite `glossary_terms` → glossary.sqlite 백필(idempotent). 테스트 `tests/test_glossary_store.py`: `test_backfill_idempotent`, `test_aggregate_counts_dedupe`, `test_lifecycle_candidate_to_confirmed`, `test_glossary_db_separate_file`.

---

## 7. 임팩트 탭 자동 필드 리스트 (#8)

### 7.1 필드 소스 파이프라인 (결정적)
1. 선택 오브젝트 detail의 `metadata["fields"]` 우선(현재 RSDS만 채워짐, 1207).
2. **누락 파서 보강**: `_ingest_adso_xml`(신규, `fetch_adso` 이미 존재) — ADSO 키/데이터 필드 추출 → `metadata["fields"]`. HCPR(`_ingest_composite_provider_xml`)·QUERY(`_ingest_query_xml`)에도 필드/특성·키피겨 목록 부착.
3. catalog 인게스트 라우팅(`_ingest_payload` 808–815)에 ADSO kind 추가.

### 7.2 API/UI
- 신규 읽기 라우트 `GET /api/v1/snapshots/{id}/objects/{object_id:path}/fields` → `{fields:[{name,type,role,description}]}`(object detail metadata에서 파생, 라이브 호출 없음).
- `App.tsx`: `fieldName` 자유 입력 → **선택 오브젝트 변경 시 자동 필드 목록 로드**한 `<select>`(데이터 없으면 자유 입력 폴백 유지). `change_type`이 field 계열(`field_removed`/`field_type_changed`)일 때만 필수.
- 폴백: 필드 미수집 시 "필드를 가져오려면 이 오브젝트를 라이브 캡처하세요" 안내 + 수동 입력 허용.

### 7.3 테스트
- `tests/test_catalog_ingest.py`: `test_ingest_adso_xml_extracts_fields`, `test_query_fields_attached`, `test_hcpr_fields_attached`.
- `tests/test_v1_api.py`: `test_object_fields_endpoint_returns_catalog_fields`, `test_object_fields_endpoint_empty_when_unknown`.
- `web/tests/sliceG.test.mjs`: 필드 select 채움/폴백 헬퍼(필드 파싱 순수 함수 분리 시).

---

## 8. 쿼리명 기반 쿼리분석 (#9)

### 8.1 왜 현재 SQL 탭이 기대를 못 맞추나
"sql" 탭은 `NATIVE_SQL_VIEW`(HANA SQL 텍스트) 전용(`view_id`+`sql_file/sql_text`)이라 **BEx 쿼리명을 넣을 자리가 없고**, 백엔드 query 파서는 provider 링크만 추출(variables/KF 폐기). 사용자가 말한 "쿼리분석=쿼리명→내용"은 **BW 쿼리(QUERY) 읽기 경로**가 필요.

### 8.2 결정적 쿼리 읽기/파싱 경로
- 백엔드 `bwli/query_analysis.py`(신규): 참조 `tools/query.ts`를 Python으로 이식한 **결정적 파서** — `Qry:subComponents`에서 Variable/CalculatedMeasure/RestrictedMeasure 맵 구성, mainComponent에서 filter/rows/columns/free/exceptions/cells/settings, provider 링크(`atom:link rel=related` → hcpr/alvl/adso 판별). 출력은 구조화 dict + node/edge(쿼리→provider, 쿼리→변수/KF as 증거).
- `_ingest_query_xml`(1247)을 이 파서로 교체/강화하여 catalog에 variables/CKF/RKF/provider를 객체·엣지·glossary로 적재.
- fetch: `client.fetch_query`(이미 active→inactive). 여기에 §3.3 미디어타입 협상 추가.

### 8.3 UI: Query 탭 신설
- `AppTab`에 `'query'` 추가(`api.ts` 5, `App.tsx` 탭바). 입력: **쿼리명**(+선택 active/inactive). 동작:
  1. 라이브 연결 준비 시 쿼리명을 `captureLiveSnapshot({queries:[name]})` 경로로 캡처(또는 신규 `POST /api/v1/snapshots/{id}/query/analyze`로 스냅샷 기반 파싱).
  2. 파싱 결과(변수/제한·계산 키피겨/필터/레이아웃/provider)를 패널로 렌더, 각 항목 `evidence_ids` 인용.
- 폴백: 쿼리 미수집 시 "쿼리를 가져오려면 BW에서 캡처" 안내.

### 8.4 테스트
- `tests/test_query_analysis.py`: `test_parse_query_extracts_variables_ckf_rkf`, `test_parse_query_provider_link_resolves_type`, `test_parse_query_handles_inactive_fallback_note`.
- `tests/test_catalog_ingest.py`: `test_query_ingest_emits_variable_and_keyfigure_nodes`.
- `tests/test_v1_api.py`: `test_query_analyze_endpoint_read_only`, `test_query_analyze_unknown_query_404`.
- 픽스처: `tests/fixtures/query.xml`(변수+CKF+RKF+filter 포함, 참조 스키마 기준).

---

## 9. PR 슬라이싱 & 태스크 그래프

소형·의존성 순. 각 PR: TDD(실패 테스트 먼저) → 구현 → `uv run pytest -q && uv run ruff check . && uv run mypy src` (+UI는 `npm --prefix web run test:slice-g && npm --prefix web run build`).

| PR | 제목 | 핵심 파일 | TDD 테스트 | 수용 기준 | 위험/롤백 |
|---|---|---|---|---|---|
| **S1** | 결정적 타입/레인 매핑 + Unknown 분해 | `bwli/layers.py`(신규), `store/catalog.py`(`unknown_reason` 태깅), `web/src/sliceG.ts`(별칭 확장) | `test_assign_layer_by_type`, `test_unknown_reason_classified`, sliceG 별칭 케이스 | 241 무회귀, Unknown이 3버킷으로 분해 | 낮음. 메타만 추가 |
| **S2** | UI #2/#3/#4 (CSS+sticky JS+title) | `web/src/styles.css`, `web/src/App.tsx` | `web/tests/sliceG.test.mjs`(스크롤 상태 헬퍼), 빌드 | 이름 2줄 클램프+tooltip, 드로어 내부 스크롤, 상단 바 부드러운 그림자 | 낮음. 프런트 한정 |
| **S3** | 미디어타입 협상 + 읽기 가드 테스트 (#5) | `endpoints.py`(`negotiate_accept`), `client.py`(406/415 폴백), `live.py`(스모크 확장) | `test_fetch_query_prefers_discovered_media_type`, `test_client_public_surface_has_no_mutating_verbs` | 신규 fetch 전부 GET, 거부동사 부재 | 중. 응답 포맷 편차→픽스처 격리 |
| **S4** | 쿼리 파서 이식 + Query 인게스트 (#9 백엔드) | `bwli/query_analysis.py`(신규), `store/catalog.py`(`_ingest_query_xml` 강화) | `test_parse_query_extracts_variables_ckf_rkf`, `test_query_ingest_emits_*` | 변수/CKF/RKF/provider 결정적 추출 | 중. SP별 스키마 편차→버전 픽스처 |
| **S5** | ADSO/HCPR 필드 추출 + 필드 API (#8 백엔드) | `store/catalog.py`(`_ingest_adso_xml` 신규 등), `server.py`(`…/fields`) | `test_ingest_adso_xml_extracts_fields`, `test_object_fields_endpoint_*` | 필드 API가 catalog 필드 반환 | 중. ADSO XML 변형 |
| **S6** | Query 탭 + 임팩트 자동 필드 (#8/#9 프런트) | `web/src/api.ts`, `App.tsx`, `styles.css` | sliceG 헬퍼, 빌드 | 쿼리명→내용 렌더, 임팩트 필드 자동 select | 낮음. 프런트 |
| **S7** | Tour/LLM 리네이밍 + 결정적 워크스루 (#6) | `server.py`(tour 결정적 분기), `llm/*`, `web/*` | `test_lineage_tour_deterministic_when_llm_disabled`, 인용 검증 | LLM off에서 의미 있는 워크스루, 인용 결속 | 중. LLM 비결정성→인용 가드 |
| **S8** | 글로서리 별도 DB + 집계 (#7) | `store/glossary_store.py`(신규), `server.py`(aggregate/lifecycle) | `test_backfill_idempotent`, `test_aggregate_counts_dedupe` | 별도 파일, 집계/라이프사이클, 백필 idempotent | 중. 마이그레이션→idempotent 테스트 |

**의존성**: S1 → (S2 ∥ S3). S3 → S4 → (S5 ∥ S6). S1 → S7. S8 독립(S5 후 필드 글로서리 강화 권장). 임계 경로 **S1→S3→S4→S6**.

---

## 10. 보안 / 읽기 전용 게이트 / 비목표

- **GET-only 게이트**: 신규 fetch/엔드포인트 전부 GET. CI 가드 `test_client_public_surface_has_no_mutating_verbs` + 거부 도구명 부재 단언.
- **로컬 쓰기 ≠ BW 쓰기**: 글로서리 confirm/reject·runtime-config는 **로컬 SQLite/.env**만 변경(BW API 무관). 라우트 docstring에 명시.
- **시크릿/쿠키 비영속**: `assert_no_persisted_secrets`를 glossary_store에도 적용. 쿠키 인증(`BW_COOKIE_FILE`)은 본 작업 범위 밖(별도 게이트, 직전 플랜 슬라이스 F).
- **LLM**: 기본 off, sanitized 슬라이스만, 인용 검증, 로컬 엔드포인트 우선.
- **비목표**: BW create/update/activate/transport/run_dtp; 데이터 미리보기(`bw_query_data`/`preview`); 쿠키 인증; 클라우드 LLM 기본화; 중앙 호스팅 백엔드.

---

## 11. 권고 첫 슬라이스 (Codex OMX용 프롬프트 개요)

**첫 PR = S1**(라이브·LLM·시크릿·네트워크 무의존, 가장 안전한 가치 토대이자 #1의 직접 해소).

```
역할: BW Lineage Impact 리포지토리의 읽기 전용 결정적 코어 구현자.
제약: BW 변형 API 금지(GET-only), LLM 무관, 기존 241 테스트 무회귀, TDD(실패 테스트 먼저).
작업(S1 — 타입/레인 매핑 + Unknown 분해):
 1) 실패 테스트 작성:
    - tests/test_layers.py: test_assign_layer_by_bw_object_type[adso->model, rsds->source,
      trfn->transform, hcpr->semantic, rspc->runtime], test_assign_layer_unknown_type_returns_none
    - tests/test_catalog_ingest.py: test_xref_endpoints_tag_unknown_reason_metadata_missing,
      test_type_unmapped_vs_metadata_missing_distinguished
 2) 구현:
    - src/bwli/layers.py 신규: BwLayer StrEnum + assign_layer(node)->BwLayer|None (결정적 type 매핑)
    - src/bwli/store/catalog.py: UNKNOWN 노드 생성부(_ingest_xref_*, dtp/datasource source/target)에서
      metadata["unknown_reason"] in {METADATA_MISSING, TYPE_UNMAPPED, PARSER_UNSUPPORTED} 태깅.
      _MutableCatalog.add_object 병합 시 type 확정되면 unknown_reason 제거.
    - web/src/sliceG.ts: TYPE_LAYER_ALIASES에 rspc/alvl/aggrlevel/infosource/trcs 등 누락 키 추가.
 3) 검증(모두 그린이어야 완료):
    uv run pytest -q && uv run ruff check . && uv run mypy src
    npm --prefix web run test:slice-g && npm --prefix web run build
수용 기준: 기존 241 통과 + 신규 통과; UNKNOWN이 3개 사유로 분해되어 metadata에 노출;
레인 매핑 누락 타입 0(테스트로 고정). 거동/스키마 회귀 없음(BwNode 신규 필드는 옵션·기본값).
금지: 라이브 BW 호출 추가, LLM, 시크릿/쿠키 영속, 변형 동사.
```

이후 S2~S8을 §9 의존성 순서로 진행. 각 PR 본문에 (목적/수용기준/검증/비목표/롤백)을 복사하고, 머지 게이트는 위 5개 명령 그린입니다.
