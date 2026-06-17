# BW Lineage Impact — 상세 구현 플랜 (Claude Opus 4.8)

> Source: Claude Code `--model opus`; verified model usage included `claude-opus-4-8`.
> Raw run artifact: `.hermes/plans/claude-runs/2026-06-17-opus48-detailed-implementation-plan/result.md`.
> Status: planning-only; no implementation changes.

> 작성일: 2026-06-17 KST · 브랜치 기준: `feat/live-object-workbench` · 모드: **계획 전용(파일 미수정)**
> 검증 기준선: `uv run pytest -q` 241 passed · `ruff check .` pass · `mypy src` pass

---

## 1. 실행 요약 (Executive Verdict)

**권고 시퀀스: A → B → C → D → (E ∥ F) → G**

| 순서 | 슬라이스 | 한 줄 근거 |
|---|---|---|
| 1 | **A. 그래프 스키마 v1.1 + 레이어/지문 골격** | 라이브 BW·LLM·웹 의존 0. 이후 모든 슬라이스가 올라타는 결정적(deterministic) 토대. 가장 낮은 위험. |
| 2 | **B. 지문(fingerprint) + 변경 등급(change-grade)** | A의 노드 모델 위에서 순수 함수로 구현 가능. diff에 "왜/얼마나" 판단을 더함. |
| 3 | **C. 읽기 전용 메타데이터 엔드포인트 확장** | `endpoints.py`/`client.py`의 검증된 GET 패턴을 그대로 복제. 라이브지만 GET-only. |
| 4 | **D. 런타임 request 신선도 증거** | C의 클라이언트 위에 `bw_list_requests`/`bw_get_request` 두 개만 추가. run/activate는 명시적 거부. |
| 5 | **E. 가이드 투어 / 도메인 요약 (LLM, 선택)** | A의 `TourStep`/레이어와 기존 sanitizer 위에서. 인용 결속. |
| 5 | **F. BW Bridge 쿠키 인증 + 데이터 게이트** | C/D와 독립. 보안 게이트 뒤에 둠. E와 병렬 가능. |
| 6 | **G. 웹 워크벤치 시각화 (레이어 레인 + 신선도 배지 + 투어)** | A·D·E의 산출물을 UI로. 픽스처만으로 동작. |

핵심 원칙: **결정적 코어가 먼저, 라이브는 GET-only, LLM은 sanitized 증거에만 후처리.** 첫 PR(A)은 SAP·자격증명·네트워크·LLM 없이 전부 픽스처로 통과한다.

---

## 2. 코드 사실 기반 (계획의 전제, 검증 완료)

이 플랜은 다음 **실제 식별자**에 정렬되어 있다(추정 아님):

- `src/bwli/graph.py`: `BwNode(id,name,type,label,metadata)`, `BwEdge(id,source,target,type,confidence,metadata)`, `BwGraph(schema_version="1.0", nodes, edges)`. **세 모델 모두 `ConfigDict(extra="forbid")`.** `BwGraph.traverse()`가 BFS+depth-cap+cycle-guard로 `LineageResult` 반환. `LineageResult.to_payload()`는 `"schema_version": "1.0"`을 **하드코딩**.
- `src/bwli/impact.py`: `ChangeType`(7종 StrEnum), `ImpactSeverity`, `ChangeEvent/ChangeSet`, `ImpactFinding/ImpactReport`, **이미 존재하는** `SnapshotDiff(added/removed/changed × node/edge ids)`와 `diff_graphs(before, after)`. 변경 "등급" 개념은 **없음**.
- `src/bwli/snapshot.py`: `PayloadMetadata`에 **이미 `sha256` 필드 존재**(payload 바이트 해시). `SnapshotWriter/Reader`, `SnapshotManifest(schema_version="1.0")`.
- `src/bwli/endpoints.py`: `ACCEPT_HEADERS` 딕셔너리 + `build_*_endpoint()` 팩토리들(`Endpoint(path,params,accept)` 반환). 모두 GET 전용 형태.
- `src/bwli/client.py`: `BwClient` — `fetch_*` (전부 `_request_read` = HTTP **GET**), CSRF 토큰 캐시(`CSRF_TOKEN_TTL_SECONDS=240`), HTTP Basic 인증, 401/403 시 1회 재시도. **쿠키 인증 없음.**
- `src/bwli/live.py`: `BwReadClient` Protocol, `run_live_smoke()`, `collect_live_snapshot()`, 실패시 `redact_text`로 마스킹.
- `src/bwli/config.py`: `BwConnectionConfig.from_env()` (`BW_URL/USER/PASSWORD/CLIENT/LANGUAGE` + `BW_VERIFY_SSL/CA_BUNDLE/TRUST_ENV`), `LlmConfig.resolve_runtime()` + `_validate_local_llm_base_url()` (메타데이터/링크로컬 차단). **`BW_COOKIE_FILE` 없음.**
- `src/bwli/store/catalog.py`: SQLite `CatalogStore`, `CatalogObjectRecord/CatalogEdgeRecord`, `ingest_*` 함수군. `metadata: JsonDict` 자유 필드 존재.
- `src/bwli/llm/`: `sanitizer.sanitize_llm_evidence() → SanitizedPayload`, `openai_compatible.py`, `lineage_advisor.py`, `impact_advisor.py`, `explainer.py`, `sql_assistant.py`.
- `src/bwli/server.py` (2280줄): FastAPI 라우트 — 읽기 계열 `/api/v1/snapshots*`, `/api/v1/repository`, 분석 POST(`/lineage/advice`,`/impact/*`,`/sql/*`), 캡처 `POST /api/v1/snapshots/capture`. `PUT/DELETE /api/(v1/)runtime-config`는 **로컬 설정**이지 BW write 아님.

**참조 레포 사실:**
- BW MCP v0.8.0 도구 — 채택 대상(읽기): `bw_get_process_chain`, `bw_get_process_variant`, `bw_get_dtps`, `bw_get_dtp`, `bw_list_source_systems`, `bw_get_source_system`, `bw_list_datasources`, `bw_get_datasource`, `bw_get_query`, `bw_get_composite_provider`, `bw_get_ckf`, `bw_get_rkf`, `bw_get_structure`, `bw_list_contents`, **`bw_list_requests`/`bw_get_request`**(런타임 신선도, read-only 명시). 데이터 보유(유보): `bw_preview_datasource`, `bw_query_data`, `bw_get_filter_values`.
- Understand Anything — `ChangeLevel = "NONE"|"COSMETIC"|"STRUCTURAL"`, `UpdateDecision.action = "SKIP"|"PARTIAL_UPDATE"|"ARCHITECTURE_UPDATE"|"FULL_UPDATE"`, `classifyUpdate()` 결정 행렬, `contentHash/extractFileFingerprint/compareFingerprints/analyzeChanges`, `FingerprintStore`.

---

## 3. 읽기 전용 경계 — 명시적 거부 목록 (MVP 불변식)

다음 BW MCP 도구는 **MVP에서 구현·노출 금지**. CI 가드 테스트로 강제(슬라이스 C):

> `bw_run_dtp`, `bw_activate_request`, `bw_create_adso`, `bw_update_adso`, `bw_create_infoobject`, `bw_update_infoobject`, `bw_create_infoarea`, `bw_create_transformation`, `bw_update_transformation`, `bw_set_transformation_routine`, `bw_set_transformation_runtime`, `bw_delete_transformation_routine`, `bw_activate`, `bw_delete`, `bw_unlock`, `bw_move_object`, `bw_create_infosource`, `bw_update_infosource`, `bw_create_dtp`, `bw_update_dtp`, `bw_set_dtp_filter_routine`, `bw_push_data`, `bw_set_query_roles`

규칙: (1) 런타임 BW 호출은 GET only. (2) BW에 `POST/PUT/DELETE` 금지(MCP에 구현돼 있어도). (3) 결정적 코어는 픽스처/스냅샷만으로 오프라인 동작. (4) LLM은 sanitized 그래프 슬라이스만 보고 BW API 호출 불가. (5) 시크릿·쿠키는 런타임 입력 전용 — 스냅샷/리포트/git/메모리에 비영속.

---

## 4. 구현 슬라이스 (PR 단위)

### 슬라이스 A — 그래프 스키마 v1.1 + 레이어/지문 골격
- **목적:** Understand Anything의 레이어/요약/투어 구조를 결정적 코어에 도입하되, 기존 v1.0 JSON 100% 하위호환 유지.
- **건드릴 파일:**
  - `src/bwli/graph.py` — `BwNode`에 옵션 필드 `summary: str|None`, `tags: list[str]=[]`, `complexity: int|None`, `layer: BwLayer|None`; `BwEdge`에 `weight: float|None`, `description: str|None`(기존 `confidence` 보존); 신규 `BwLayer(StrEnum)`(`SOURCE/ACQUISITION/STAGING/TRANSFORMATION/PROVIDER/REPORTING/RUNTIME`), `TourStep(BaseModel)`; `BwGraph`에 `layers: list[Layer]=[]`, `tour: list[TourStep]=[]`, `schema_version` 기본값 `"1.1"`로 상향.
  - `src/bwli/layers.py` **(신규)** — `assign_layer(node: BwNode) -> BwLayer` 결정적 규칙(객체 type 문자열 기준 매핑: `RSDS/LSYS→SOURCE`, `DTP/TRFN→TRANSFORMATION`, `ADSO/HCPR/CUBE→PROVIDER`, `QUERY/CKF/RKF/STRUCTURE→REPORTING`, `RSPC/PROCESS→RUNTIME` 등); `assign_layers(graph)` 헬퍼.
  - `src/bwli/lineage.py` — `LineageResult.to_payload()`의 하드코딩 `"1.0"`을 그래프 `schema_version` 전달 방식으로 수정(회귀 방지).
- **추가/수정 테스트:** `tests/test_graph_schema.py`(신규):
  - `test_graph_schema_v11_loads_v10_payload_unchanged`
  - `test_bwnode_optional_fields_default_empty`
  - `test_bwedge_weight_and_description_optional_confidence_preserved`
  - `test_tour_step_roundtrip_serialization`
  - `tests/test_layers.py`: `test_assign_layer_by_bw_object_type[adso-provider]` 등 파라미터화, `test_assign_layer_unknown_type_returns_none`.
  - 기존 `tests/test_lineage.py`에 `test_lineage_payload_uses_graph_schema_version` 추가.
- **수용 기준:** 기존 241 테스트 무수정 통과. `tests/fixtures/sample-graph.json`(v1.0, schema_version 없음/1.0)이 그대로 로드. 새 필드는 전부 옵션·기본값 → 기존 직렬화 출력 키 순서/내용 회귀 없음(`extra="forbid"` 유지).
- **검증:** `uv run pytest -q && uv run ruff check . && uv run mypy src`
- **비목표:** 레이어를 실제로 채우는 라이브 수집 X, LLM 요약 생성 X, 웹 변경 X.
- **롤백 위험:** **낮음.** 순수 모델 추가. 유일 위험 지점은 `to_payload()` schema_version 변경 → 골든 출력 테스트가 즉시 잡음.

### 슬라이스 B — 지문 + 변경 등급 분류기
- **목적:** 스냅샷 객체의 정규화 지문을 저장하고, diff를 `SKIP/PARTIAL_UPDATE/ARCHITECTURE_UPDATE/FULL_UPDATE`로 등급화해 영향분석 우선순위에 사용.
- **건드릴 파일:**
  - `src/bwli/fingerprint.py` **(신규)** — `object_fingerprint(node: BwNode) -> str`(정규화된 type+정렬된 metadata 부분집합의 sha256; 휘발성 키 제외), `ChangeLevel(StrEnum)`=`NONE/COSMETIC/STRUCTURAL`, `classify_node_change(before, after) -> ChangeLevel`.
  - `src/bwli/impact.py` — 기존 `SnapshotDiff` 위에 `ChangeGrade(StrEnum)`와 `grade_diff(diff, *, total_nodes) -> ChangeGradeResult`(UA `classifyUpdate` 결정 행렬 이식: 구조변경 0→SKIP, >30 또는 >50%→FULL, 디렉터리/레이어 변동 또는 >10→ARCHITECTURE, 그 외 PARTIAL). `run_impact_analysis()`에 등급 기반 우선순위 메타 부착(거동 변경 없이 메타만).
  - `src/bwli/snapshot.py` — `PayloadMetadata`에 옵션 `object_fingerprints: dict[str,str]|None` 누적(기존 payload `sha256`은 보존).
  - `src/bwli/cli.py` — `diff` 명령에 `--grade` 출력 옵션.
- **테스트:** `tests/test_fingerprint.py`(신규):
  - `test_object_fingerprint_stable_for_same_payload`
  - `test_object_fingerprint_ignores_volatile_metadata_keys`
  - `test_classify_node_change_cosmetic_vs_structural`
  - `tests/test_impact.py`에: `test_grade_diff_skip_when_no_structural_change`, `test_grade_diff_full_update_over_threshold`, `test_grade_diff_architecture_update_on_layer_shift`, `test_grade_diff_partial_update_localized`.
- **수용 기준:** 동일 payload 재지문 시 등급 `SKIP`. `tests/fixtures/sample-graph.json` ↔ `sample-graph-after.json` diff가 결정적 등급 산출. 기존 impact 테스트 무회귀.
- **검증:** 위와 동일 3종 명령.
- **비목표:** 등급으로 traversal depth를 실제 가변화하지 않음(메타데이터까지만). 라이브/LLM 무관.
- **롤백 위험:** **낮음.** 신규 모듈 + 부가 필드. 임계값(30/50%/10)은 상수로 분리해 튜닝 가능.

### 슬라이스 C — 읽기 전용 메타데이터 엔드포인트 확장
- **목적:** process chain/variant, DTP, source system/DataSource, query/CKF/RKF/Structure/CompositeProvider 읽기 경로 추가. 전부 GET-only.
- **건드릴 파일:**
  - `src/bwli/endpoints.py` — `ACCEPT_HEADERS`에 신규 media type 추가 + `build_process_chain_endpoint`, `build_process_variant_endpoint`, `build_dtp_endpoint`, `build_datasource_endpoint`, `build_source_system_endpoint`, `build_query_endpoint`, `build_composite_provider_endpoint` 등(BW MCP `src/tools/*.ts`의 경로/Accept를 1:1 이식).
  - `src/bwli/client.py` — 대응 `fetch_process_chain` 등 `fetch_*`(모두 `_request_read` 경유). discovery 기반 media-type 협상(415/406 fallback) 추가.
  - `src/bwli/live.py` — `collect_live_snapshot()`에 신규 read 작업 통합(실패 격리 패턴 유지).
  - `src/bwli/store/catalog.py` — 신규 payload kind ingest(`_ingest_processchain_*`, `_ingest_datasource_*` 등).
- **테스트:** `tests/test_endpoints.py`/`tests/test_client.py`(기존 패턴 — `FakeLiveBwClient`/transport mock):
  - `test_build_process_chain_endpoint_path_and_accept`
  - `test_client_media_type_discovery_falls_back_on_415`
  - `test_fetch_query_uses_get_only`
  - **`test_client_exposes_no_mutating_verbs`** (가드: `BwClient` public 메서드에 create/update/activate/delete/run/push 없음 단언)
  - `tests/test_catalog_ingest.py`에 신규 kind ingest 케이스.
- **수용 기준:** 신규 fetch는 전부 GET. 거부 목록(3장) 도구명이 코드 어디에도 없음(가드 테스트 통과). 신규 payload가 catalog에 type 보존 ingest.
- **검증:** 3종 + `npm --prefix web run build`(스키마 영향 시).
- **비목표:** `bw_preview_datasource`/`bw_query_data`/`bw_get_filter_values`(데이터 보유) 제외 — 슬라이스 F 게이트. write 계열 일절 X.
- **롤백 위험:** **중.** 신규 BW 경로의 실제 응답 포맷 가변성 → 픽스처 기반 파서로 격리, 라이브 미연결시 무영향.

### 슬라이스 D — 런타임 request 신선도 증거
- **목적:** `bw_list_requests`/`bw_get_request`만 추가해 InfoProvider 노드에 최근 적재 상태/레코드수/타임스탬프/TSN을 증거로 부착.
- **건드릴 파일:**
  - `src/bwli/endpoints.py` — `build_list_requests_endpoint(target, *, target_type="ADSO", top=3, created_from=None)`, `build_get_request_endpoint(request_tsn, storage="AQ")` (`/sap/bc/http/sap/bw4/v1/manage` 계열, read-only).
  - `src/bwli/client.py` — `fetch_list_requests`, `fetch_request` (GET).
  - `src/bwli/graph.py` / `catalog.py` — 노드 `metadata["request_freshness"]`에 `{status, records, timestamp, tsn}` 부착(스키마 추가 없이 metadata 활용).
  - `src/bwli/server.py` — `GET /api/v1/snapshots/{id}/objects/{object_id}/freshness`(읽기 전용).
- **테스트:** `tests/test_client.py`: `test_fetch_list_requests_get_only_with_top_cap`; `tests/test_live.py`: `test_request_freshness_attached_to_provider_node`; **`test_endpoints_get_only_for_runtime_request_monitor`**, **`test_run_dtp_and_activate_request_absent_from_surface`**(가드).
- **수용 기준:** 두 read 도구만 노출, `top` 기본 캡 적용. run/activate는 surface에 부재(가드 통과). 신선도 메타가 노드에 결정적으로 매핑.
- **검증:** 3종 명령.
- **비목표:** DTP 실행·request 활성화·스케줄링 X. 신선도의 시계열 저장 X(최신 1건 스냅샷).
- **롤백 위험:** **중.** manage 엔드포인트 권한/포맷 차이 → 픽스처 우선, 실패는 `LiveOperationSummary` 오류로 격리.

### 슬라이스 E — 가이드 투어 / 도메인 요약 (LLM, 선택)
- **목적:** sanitized·인용 결속(citation-bound) 영향 투어와 한국어 요약을 옵션 후처리로 생성.
- **건드릴 파일:** `src/bwli/llm/lineage_advisor.py`, `impact_advisor.py`(투어 단계 생성), `src/bwli/llm/sanitizer.py`(슬라이스 evidence 화이트리스트 강화), 선택 신규 `src/bwli/domain.py`(InfoArea/이름 기반 도메인 그룹, **결정적** 1차), `web/src/*`(렌더).
- **테스트:** `tests/test_llm.py`: `test_llm_tour_requires_sanitized_cited_evidence`, `test_tour_steps_reference_only_present_node_ids`, `test_korean_summary_opt_in_flag`, `test_llm_disabled_by_default_no_network`.
- **수용 기준:** LLM 비활성(기본)일 때 네트워크 0. 투어 단계는 그래프에 실재하는 node id만 인용. 한국어는 opt-in. `_validate_local_llm_base_url` 게이트 유지.
- **검증:** 3종(LLM 모킹).
- **비목표:** 클라우드 LLM 기본화 X, 임베딩 시맨틱 검색/그래프 챗 X(후속).
- **롤백 위험:** **중.** LLM 비결정성 → 인용 검증으로 가드, 기능 플래그로 즉시 비활성.

### 슬라이스 F — BW Bridge 쿠키 인증 + 데이터 보유 게이트
- **목적:** SAML/OAuth 전면 시스템용 `BW_COOKIE_FILE` 읽기 전용 인증을 게이트 뒤에 추가. 데이터 미리보기 도구는 별도 명시 승인.
- **건드릴 파일:** `src/bwli/config.py`(`BW_COOKIE_FILE` 옵션, 파일 권한 검사), `src/bwli/client.py`(쿠키 인증 분기 — BW MCP `bw-client.ts` 쿠키/CSRF 흐름 이식), `src/bwli/store/secret_guard.py`·`src/bwli/redact.py`(쿠키 이름/값 마스킹), 데이터 게이트 플래그.
- **테스트:** `tests/test_config.py`: `test_cookie_file_requires_safe_permissions`; `tests/test_redact.py`: `test_cookie_file_redaction_and_no_snapshot_persistence`; `tests/test_client.py`: `test_cookie_auth_get_only_no_csrf_when_frozen`.
- **수용 기준:** 쿠키 파일은 스냅샷/리포트/UI 응답에 비영속. 데이터 보유 도구는 별도 승인 + row cap + LLM 비기본.
- **검증:** 3종 명령.
- **비목표:** 쿠키 자동 갱신/브라우저 통합 X.
- **롤백 위험:** **중상(보안).** 시크릿 누출이 최대 리스크 → `assert_no_persisted_secrets` 확장 + redaction 테스트로 강제, 게이트 기본 off.

### 슬라이스 G — 웹 워크벤치 시각화
- **목적:** 레이어 레인(Source→…→Runtime), 변경 등급 패널, request 신선도 배지, 가이드 투어 next/prev를 픽스처만으로 구동.
- **건드릴 파일:** `web/src/App.tsx`, `web/src/api.ts`, `web/src/styles.css`; 필요 read API는 D/E에서 제공.
- **테스트:** `web` 빌드 + `tests/test_server.py`/`tests/test_v1_api.py`에 read 라우트 응답 계약 테스트(`test_freshness_endpoint_read_only`, `test_objects_carry_layer_field`).
- **수용 기준:** 라이브/LLM 없이 픽스처 스냅샷으로 레이어·등급·신선도·투어 1~4단계 렌더. write UI 없음.
- **검증:** `npm --prefix web run build` + 3종.
- **비목표:** 그래프 챗·대시보드 탐색 고도화 X.
- **롤백 위험:** **낮음.** 프런트 한정, 백엔드 계약 불변.

---

## 5. 슬라이스 의존성 그래프

```text
A (graph v1.1 + layers)
├──> B (fingerprint + change-grade)        [A의 BwNode/BwLayer 필요]
├──> C (read-only endpoint 확장)            [A와 독립이나 catalog layer 부착에 A 권장]
│     └──> D (request freshness)            [C의 client 패턴 필요]
│            └──> G (web: 레이어/등급/신선도/투어)  [A,B,D 산출물]
├──> E (LLM tour/도메인)                     [A의 TourStep + 기존 sanitizer]   ─┐
└──> F (쿠키 인증 + 데이터 게이트)            [C와 독립, 보안 게이트]            ─┴─> (E ∥ F 병렬 가능)
```

임계 경로: **A → C → D → G**. E·F는 A/C 이후 병렬.

---

## 6. 브랜치 / 커밋 전략

- 베이스: 현재 `feat/live-object-workbench`에서 분기하지 말고, 각 슬라이스를 **`main` 기준 단일 목적 브랜치**로:
  - `feat/graph-schema-v11-layers` (A)
  - `feat/change-grade-fingerprint` (B)
  - `feat/read-only-endpoint-expansion` (C)
  - `feat/request-freshness-evidence` (D)
  - `feat/llm-guided-tour` (E) · `feat/bw-cookie-auth-gate` (F) · `feat/web-layered-workbench` (G)
- 1 슬라이스 = 1 PR. PR 본문에 본 플랜의 (목적/수용기준/검증/비목표/롤백) 섹션 복사.
- 커밋 스타일은 기존 히스토리 준수: `feat:` / `fix:` 접두 + PR 번호. 작은 원자 커밋(모델→규칙→테스트 순).
- 머지 게이트: 세 명령(`pytest -q`, `ruff check .`, `mypy src`) + 영향 시 `web build` 그린일 때만.
- 커밋/푸시는 JC님 승인 후에만(현재 `CLAUDE.md`: 계획 단계).

---

## 7. 리스크 레지스터

| ID | 리스크 | 영향 | 가능성 | 완화 |
|---|---|---|---|---|
| R1 | 스키마 v1.1 상향이 기존 JSON/골든 출력 회귀 | 중 | 중 | 전 필드 옵션+기본값, `extra="forbid"` 유지, v1.0 로드 테스트, `to_payload` 회귀 테스트 |
| R2 | 거부된 write 도구가 실수로 노출 | 높음(읽기전용 위반) | 낮음 | 슬라이스 C/D의 가드 테스트(`test_client_exposes_no_mutating_verbs` 등) CI 강제 |
| R3 | 신규 BW 엔드포인트 응답 포맷 SP/버전 편차 | 중 | 높음 | media-type 협상 + 415/406 fallback, 픽스처 우선, 실패 격리(`LiveOperationSummary`) |
| R4 | 쿠키/시크릿이 스냅샷·리포트·로그에 유출 | 높음 | 중 | redaction + `assert_no_persisted_secrets` 확장, 게이트 기본 off, 권한 검사 |
| R5 | LLM 환각/비인용 출력 | 중 | 중 | sanitized evidence only, node-id 인용 검증 테스트, 기본 비활성 |
| R6 | request monitor 권한 부재로 D 실패 | 낮음 | 중 | 선택 기능화, 실패시 신선도 미부착(그래프 정상) |
| R7 | 변경 등급 임계값 오탐 | 낮음 | 중 | 임계 상수 분리, 파라미터화 테스트로 경계 고정 |

---

## 8. 테스트 이름 + 픽스처 전략

**픽스처 전략(기존 관례 준수):** `conftest.py` 없음 — 인라인 test double(`FakeLiveBwClient`, `RecordingLiveClient`, `XmlDataflowClient`) + `tmp_path` + `tests/fixtures/*` 골든 파일 패턴 유지. 신규 픽스처(추가 권장):
- `tests/fixtures/sample-graph-v11.json` (레이어/투어 포함, A 검증)
- `tests/fixtures/process_chain.xml`, `datasource.xml`, `query.xml` (C 파서)
- `tests/fixtures/request_list.json`, `request_detail.json` (D 신선도)
- 라이브 호출은 **전부 모킹**(현 100% 모킹 정책 유지) — 실 BW 미접속.

**신규/변경 테스트 이름(요약):**
`test_graph_schema_v11_loads_v10_payload_unchanged`, `test_bwedge_weight_and_description_optional_confidence_preserved`, `test_tour_step_roundtrip_serialization`, `test_assign_layer_by_bw_object_type`, `test_lineage_payload_uses_graph_schema_version`, `test_object_fingerprint_stable_for_same_payload`, `test_classify_node_change_cosmetic_vs_structural`, `test_grade_diff_skip_when_no_structural_change`, `test_grade_diff_full_update_over_threshold`, `test_grade_diff_architecture_update_on_layer_shift`, `test_build_process_chain_endpoint_path_and_accept`, `test_client_media_type_discovery_falls_back_on_415`, `test_client_exposes_no_mutating_verbs`, `test_endpoints_get_only_for_runtime_request_monitor`, `test_run_dtp_and_activate_request_absent_from_surface`, `test_request_freshness_attached_to_provider_node`, `test_llm_tour_requires_sanitized_cited_evidence`, `test_tour_steps_reference_only_present_node_ids`, `test_cookie_file_requires_safe_permissions`, `test_cookie_file_redaction_and_no_snapshot_persistence`, `test_objects_carry_layer_field`.

---

## 9. 다음 액션 (권고)

**즉시 착수: 슬라이스 A를 단일 PR로.**
- 제목: `feat: add BW graph schema v1.1 with layers, tour scaffolding, and deterministic layer assignment`
- 범위: `graph.py` 모델 확장 + `layers.py` 신규 + `lineage.py` schema_version 회귀 수정 + `tests/test_graph_schema.py`/`test_layers.py`.
- 라이브 BW·LLM·웹·시크릿·네트워크 **무의존** → 가장 안전한 가치 토대.
- 완료 정의: 기존 241 테스트 무수정 통과 + 신규 테스트 통과 + v1.0 픽스처 무변경 로드 + `to_payload` 회귀 없음.

JC님 승인 시 `CLAUDE.md`의 "planning only" 단계를 슬라이스 A에 한해 해제하고 구현 착수를 권고드립니다. 나머지 B~G는 5장 의존성 그래프 순서로 진행하면 됩니다.

---

이 플랜은 코드 식별자와 두 참조 레포의 실제 도구/타입에 정렬되어 있어 코딩 에이전트가 추가 컨텍스트 없이 슬라이스 단위로 실행할 수 있습니다. 원하시면 이 문서를 `docs/plans/`에 저장하거나, 슬라이스 A의 파일별 변경 디프 초안을 작성해 드리겠습니다.