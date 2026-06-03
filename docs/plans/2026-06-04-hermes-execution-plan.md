# Hermes Execution Plan — BW Lineage & Change Impact Analyzer

Date: 2026-06-04 KST
Project: `/Users/jclee/Projects/bw-lineage-impact`
Claude baseline plan: `docs/plans/2026-06-04-claude-plan.md`

## 0. 결론

Claude의 계획은 채택 가능하다. 다만 Hermes 관점에서는 아래처럼 고정한다.

1. **MVP는 로컬 실행형 Python CLI + Docker**로 간다.
   - 보안상 각 사용자/환경에서 자체 실행할 수 있어야 하며, 중앙 hosted analyzer backend는 MVP에서 제외한다.
   - 참조 저장소는 TypeScript/MCP지만, 우리 제품의 핵심은 MCP가 아니라 graph extraction, snapshot, deterministic impact rule, report다.
   - Python은 XML parsing, graph, DuckDB, fixture-based testing, report generation에서 유리하다.
   - 배포 난이도는 `pipx/uvx`, wheel, Docker로 낮춘다.

2. **MCP/Web UI/단일 바이너리는 MVP 밖**으로 둔다.
   - MCP는 자연어 질의에는 좋지만 보안 검토와 tool-surface 관리 비용이 커진다.
   - Web UI는 시각화에는 좋지만 인증/배포 부담이 있다.
   - 단일 바이너리는 PyInstaller/lxml/인증서 이슈 때문에 M3 이후 검토한다.

3. **LLM은 core 기능이 아니라 로컬 optional explainer**다.
   - lineage, impact, severity, confidence는 rule engine이 결정한다.
   - LLM endpoint는 local OpenAI-compatible API를 1순위로 한다. 예: `http://127.0.0.1:11434/v1`, LM Studio, vLLM, llama.cpp server, Ollama OpenAI-compatible mode.
   - LLM은 ABAP/AMDP routine 설명, Native SQL View 로직 설명·최적화 후보 제안, 긴 리포트 요약, 수동 검증 체크리스트 생성에만 쓴다.
   - LLM 입력은 sanitized graph slice + routine/sql snippet으로 제한하고, node/edge/finding ID citation을 강제한다.

4. **초기 구현은 read-only safety를 코드 구조로 강제**한다.
   - collector client는 GET-only API만 노출한다.
   - create/update/delete/activate/transport 계열은 코드에 만들지 않는다.
   - import-linter 또는 테스트로 `collector -> llm`, `analyze -> collector` 같은 위험 의존을 차단한다.

## 1. 실행 범위

JC님이 진행하기로 한 1, 2, 3은 아래 세 제품 축으로 매핑한다.

- 1) Read-only BW Graph Collector
  - `bw_search`, `bw_get_dataflow`, `bw_xref`에 해당하는 endpoint를 우선 구현한다.
  - 결과는 raw payload와 normalized graph artifact로 스냅샷화한다.

- 2) Lineage Analyzer
  - snapshot을 입력으로 upstream/downstream object lineage를 계산한다.
  - field-level lineage는 Transformation/DTP/HCPR/Query parser가 충분히 쌓인 뒤 단계적으로 넣는다.

- 3) Change Impact Analyzer
  - proposed change 또는 snapshot diff를 입력으로 rule-based finding을 산출한다.
  - `severity`, `confidence`, `evidence_ids`, `manual_verification_gaps`를 반드시 포함한다.

## 2. 배포 전략 보정

### MVP 배포 채널

- 실행 모델: **local-first**
  - 각 사용자/분석가/개발자가 자기 보안 환경에서 CLI 또는 Docker로 실행한다.
  - 중앙 서버형 분석 서비스, shared cloud backend, hosted report service는 MVP에서 제외한다.
  - snapshot, report, LLM audit log는 로컬 디스크 또는 명시적으로 마운트한 내부 볼륨에만 저장한다.

- 1순위: `pipx install bwli` 또는 `uvx bwli`
  - 개발자/분석가 노트북에서 가장 빠르다.
  - Python 3.11+ requirement를 명확히 둔다.

- 2순위: Docker image
  - 운영팀/보안팀/CI에서 재현성이 좋다.
  - SAP 사내 인증서, proxy, snapshot volume mount 가이드가 필요하다.

- 3순위: wheel artifact + checksum
  - 사내 artifact registry에 올리기 쉽다.

### MVP 이후 채널

- PyInstaller single binary
  - Windows/macOS 운영자 배포에는 좋지만, lxml/CA bundle/keyring 처리 확인 후 진행한다.

- MCP server
  - Claude/Codex/IDE에서 자연어로 lineage 질의할 수 있다.
  - 단, read-only tool allowlist와 approval boundary가 설계된 뒤에만 진행한다.

- Web UI
  - Cytoscape graph explorer로 유용할 수 있지만 인증/권한/배포 이슈가 있어 MVP 이후다.

## 3. LLM 개입 판단

### LLM 금지 영역

- BW API 호출
- credential/config 로딩
- raw snapshot 저장
- graph extraction
- deterministic lineage traversal
- impact severity/confidence 최종 결정
- SQL rewrite 자동 적용 또는 DB object 변경
- BW object write/activate/transport 관련 모든 행동

### LLM 불필요 영역

- XML/JSON parsing
- SQL text에서 테이블/뷰/컬럼/조인/필터 후보 추출 가능한 범위의 정적 parsing
- graph model validation
- snapshot diff
- known change type rule evaluation
- test fixture generation 후 schema validation

### LLM 유용 영역

- ABAP/AMDP routine snippet의 자연어 설명
- routine 내부의 동적 의존성 후보를 `LOW confidence`로 제안
- Native SQL View가 graphical view가 아닌 SQL text 기반일 때 로직을 설명
- Native SQL View의 조인/필터/집계/서브쿼리/함수 사용에 대한 최적화 후보를 제안
  - 단, 성능 개선 확정이 아니라 `advisory`로 표기
  - 가능하면 SQL fragment, parsed table/column evidence, optional EXPLAIN/plan evidence에 citation 연결
- 긴 lineage/impact report의 executive summary
- BW 개발자용 수동 검증 checklist 생성
- 비즈니스 사용자에게 보여줄 변경 영향 요약문 작성

### 구현 방식

- 기본값: LLM off
- LLM provider 기본 구현: **OpenAI-compatible local endpoint only**
  - endpoint/model/API key는 사용자가 실행 시 입력한다. 입력 경로는 interactive prompt, CLI flag, env var, secret reference를 모두 허용하되 실제 값은 저장하지 않는다.
  - config 예: `llm.base_url_ref=env://BWLI_LLM_BASE_URL`, `llm.model_ref=env://BWLI_LLM_MODEL`, `llm.api_key_ref=env://BWLI_LLM_API_KEY` 또는 로컬 서버의 dummy key 허용
  - OpenAI SDK 호환 client를 쓰되, cloud OpenAI/Anthropic endpoint는 MVP 기본값에서 제외
  - Docker 실행 시에는 사용자가 `host.docker.internal` 또는 내부 LLM server URL을 명시하도록 문서화
- BW 연결값 입력 방식
  - reference MCP와 호환되도록 `BW_URL`, `BW_USER`, `BW_PASSWORD`, `BW_CLIENT`, optional `BW_LANGUAGE` env var를 1차 지원한다.
  - 같은 값은 CLI flag 또는 interactive prompt로도 받을 수 있다.
  - real endpoint/key/password/client 값은 `.env.example`, docs, tests, fixtures, memory, git commit에 저장하지 않는다.
- CLI opt-in: `--explain` 또는 config `llm.enabled=true`
- input sanitizer: host/user/client/email/IP/secret-like token 제거
- input cap: graph node/edge 수, routine/sql line 수, token 수 제한
- citation validator: LLM output의 모든 주장은 `node_id`, `edge_id`, `finding_id`, `sql_fragment_id`와 연결되어야 함
- audit log: prompt hash, sanitized input hash, model, token count, response, timestamp 저장
- architecture guard: core 모듈에서는 `bwli.llm` import 금지

### Native SQL View 분석 추가 범위

Graphical view가 아니라 Native SQL View/SQL text 기반 artifact일 때는 별도 path를 둔다.

- deterministic layer
  - SQL text 또는 view definition을 read-only로 수집한다.
  - `sqlglot` 같은 SQL parser로 table/view reference, selected column, join condition, filter, aggregation, subquery, function call 후보를 추출한다.
  - 추출 가능한 의존성은 `SqlViewNode`, `SqlReferenceEdge`, `SqlColumnReference`로 graph에 반영한다.
  - parser가 확정하지 못한 동적 SQL, macro, DB-specific syntax는 `UNKNOWN` 또는 `manual_verification_gap`으로 표기한다.

- LLM explainer layer
  - sanitized SQL snippet과 deterministic parser evidence만 입력한다.
  - 출력은 `logic_summary`, `optimization_candidates`, `risk_notes`, `manual_checks`로 나눈다.
  - 최적화 후보는 advisory이며 자동 rewrite/적용 금지다.
  - 권장 citation: `sql_fragment_id`, `node_id`, `edge_id`, optional `explain_plan_id`.

- optional EXPLAIN/plan evidence
  - 보안 정책상 허용되는 환경에서만 read-only EXPLAIN 또는 plan extraction을 추가한다.
  - 실제 데이터 preview/query execution은 MVP 범위가 아니다.

## 4. 우선 구현 순서

### M0 — Project Bootstrap

- `pyproject.toml`, `uv.lock`, `.python-version`
- `src/bwli/__init__.py`, `__main__.py`, `cli.py`
- `bwli --version`
- CLI stubs: `collect`, `lineage`, `impact`, `diff`, `report`
- ruff, mypy, pytest, import-linter, CI
- README with read-only scope and non-goals

### M1 — Read-only Collector Smoke Path

- config loader with env/file/secret-ref policy
- GET-only `BwClient`
- endpoint builder for search/dataflow/xref
- XML/JSON parser fixtures
- snapshot manifest + writer/reader
- offline fixture-based collect command
- optional live smoke test gated by `BWLI_LIVE=1`

### M2 — Object Lineage

- graph node/edge model
- upstream/downstream traversal
- cycle guard and level cap
- JSON and Mermaid output
- first lineage report template

### M3 — Change Impact v1

- manual change file schema
- snapshot diff schema
- rules:
  - ADSO field delete
  - ADSO field type change
  - InfoObject attribute/type change
  - Transformation routine changed
  - DTP filter changed
  - CompositeProvider mapping changed
- `impact_report.json` and `impact_report.md`

### M4 — Field-level Lineage + Native SQL View evidence

- Transformation field mapping parser
- DTP source/target relationship
- HCPR mapping and join dependency
- Query CKF/RKF dependency
- Native SQL View SQL text/definition collector contract
- SQL parser evidence model:
  - `SqlViewNode`
  - `SqlReferenceEdge`
  - `SqlColumnReference`
  - `SqlFragment`
- `field_edge.confidence`: `direct`, `expression`, `routine_opaque`, `sql_parsed`, `sql_unknown`, `unknown`

### M5 — Local OpenAI-compatible LLM Explainer + Release Packaging

- sanitizer + citation validator
- provider abstraction: OpenAI-compatible local endpoint first
  - Ollama/LM Studio/vLLM/llama.cpp server 등 `base_url` configurable
  - cloud provider adapter는 MVP 기본 구현에서 제외하거나 별도 approval gate로 둔다.
- Native SQL View prompt templates:
  - `explain_sql_view_logic`
  - `suggest_sql_view_optimizations`
  - `sql_view_manual_checks`
- `--explain` report section
- Dockerfile
- release script with checksums/SBOM/security scan

## 5. 첫 번째 구현 슬라이스

승인 없이도 안전한 로컬 skeleton 범위는 아래까지다. 실제 BW 접속, credential 입력, live smoke는 별도 승인/정보가 필요하다.

1. Python package skeleton and CLI stubs
2. config schema with explicit secret reference policy
3. local execution defaults: no server mode, local output paths only, no hosted report backend
4. OpenAI-compatible local LLM config schema only, disabled by default, with endpoint/model/key supplied by user at runtime
5. Reference MCP-compatible BW env input contract: `BW_URL`, `BW_USER`, `BW_PASSWORD`, `BW_CLIENT`, optional `BW_LANGUAGE`
6. GET-only client interface with unit tests using mocked transport
7. parser contracts and sample anonymized fixture shape
8. docs/ADR for architecture, local-first deployment, snapshot, LLM policy
9. CI-local verification script

완료 기준:

- `uv run bwli --version` 성공
- `uv run pytest -q` 성공
- `uv run ruff check .` 성공
- `uv run mypy src` 성공
- GET 외 HTTP method가 public API에 없는 것을 테스트로 확인
- LLM config가 local OpenAI-compatible endpoint만 기본 허용하고, 기본값은 disabled임을 테스트로 확인
- docs에 중앙 hosted analyzer/backend가 MVP non-goal로 명시됨
- docs에 write/activate/transport가 non-goal로 명시됨

## 6. 실제 BW 검증이 필요한 지점

아래는 로컬 구현만으로 확정할 수 없다.

- BW/4HANA 버전별 media type negotiation
- `/sap/bw/modeling/dmod/8TRANSIENT` 응답 구조 차이
- `/repo/is/xref`가 운영 객체에서 충분히 where-used를 반환하는지
- Transformation/DTP/HCPR XML namespace/shape 차이
- routine/AMDP 내부 참조 누락 정도
- Native SQL View definition을 어떤 BW/HANA metadata endpoint/권한으로 read-only 수집할 수 있는지
- Native SQL View SQL dialect가 `sqlglot` 등 정적 parser로 어느 정도 해석되는지
- process chain variant detail endpoint 접근 권한

최소 live smoke 후보:

1. 대표 ADSO 1개
2. 대표 Query 1개
3. `bw_search`
4. `bw_get_dataflow`
5. `bw_xref`
6. BWMT transient data flow와 결과 대조
7. Native SQL View 샘플 1개가 있다면 SQL definition read-only 수집 가능 여부와 parser coverage 확인

## 7. 구현 운영 방식

- Planning source: Claude plan + Hermes 보정안
- Implementation: Hermes가 작은 slice로 진행
- Coding discipline: TDD, RED/GREEN evidence 보존
- Review: 구현 후 independent review subagent 또는 Claude/Codex review
- Commit policy: verification 통과 후 commit
- Credentials: 대화/파일/메모리에 저장하지 않음

## 8. Hermes 최종 권고

바로 다음 액션은 **M0 skeleton + M1 collector contract 일부 구현**이다.

단, 실제 BW 접속 smoke는 JC님이 다음 정보를 별도로 제공하거나 로컬 환경에서 직접 설정해야 한다.

- BW base URL
- SAP client
- read-only user/password 또는 사내 secret reference
- VPN/네트워크 접근 여부
- smoke 대상 ADSO/Query 이름

이 정보는 프로젝트 파일이나 memory에 저장하지 않고, 실행 시 env/secret reference로만 받는다.
