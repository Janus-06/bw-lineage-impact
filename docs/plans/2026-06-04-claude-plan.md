# BW Lineage & Change Impact Analyzer — 구현 계획 (Planning Only)

> 본 계획은 Claude baseline입니다. 최신 의사결정은 `docs/plans/2026-06-04-hermes-execution-plan.md`가 우선합니다.
> 보정된 결정: local-first 실행, 중앙 hosted analyzer 제외, LLM은 local OpenAI-compatible endpoint 우선, Native SQL View SQL 로직 설명/최적화 후보는 optional LLM explainer 범위에 포함.

> 본 계획은 파일을 수정하지 않고 채팅 응답으로만 제공됩니다. 승인 후 슬라이스 1부터 구현 진입합니다.

---

## 1. 아키텍처 & 스택 권고

**핵심 원칙**: 결정론적 수집/그래프/임팩트 코어 → 그 위에 옵션 LLM "설명 레이어". LLM은 BW API/시크릿/그래프 mutation에 절대 접근하지 않음.

**3-Layer 분리**
1. **Collector (Live BW I/O 전용)** — SAP REST 호출, CSRF/세션, XML/JSON 파싱, raw payload + 정규화 객체 모두 스냅샷에 저장.
2. **Core (Pure, 오프라인 가능)** — 스냅샷 → 그래프 모델 → lineage/impact 룰 → diff. 네트워크/LLM/시크릿 의존 0.
3. **Surface** — CLI 우선, JSON 출력 안정화, 이후 옵션 Web UI/MCP.

**스택 결정**
- **Python 3.11 + Typer + httpx + lxml + networkx + pydantic v2 + DuckDB + Jinja2**.
  - 참조 레포는 TS지만 우리는 **read-only 분석/리포팅 워크로드**가 본질 → 그래프(networkx), 데이터 다루기(DuckDB/pandas), 리포트(Jinja2/markdown), 테스트 픽스처(pytest+inline-snapshot) 생태계가 Python이 압도적.
  - 사내 배포 시 단일 wheel + `uv`/`pipx` 또는 Docker. PyInstaller로 단일 바이너리도 가능(아래 §3).
- 스냅샷 포맷: **파일 시스템 트리(JSON + raw XML) + DuckDB 인덱스 1개 파일**. Git diff 친화, 에어갭 친화.
- 그래프 직렬화: 내부 JSON + 내보내기 GraphML / DOT(Graphviz) / Mermaid.

**대안 (TS로 가야 할 경우만)**: 참조 레포 client 코드를 그대로 가져와 재사용 + MCP 서버를 1순위로 가야 한다면 TS. 그렇지 않다면 Python 권고.

---

## 2. 정확한 레포 레이아웃

```
bw-lineage-impact/
├── README.md
├── CLAUDE.md                              # 이미 존재 (operating rules)
├── pyproject.toml                         # uv/hatch, deps, entry points
├── uv.lock
├── .python-version                        # 3.11
├── .env.example                           # BW_URL=... (값 없음, placeholder만)
├── .gitignore                             # snapshots/, .env, secrets/
├── Dockerfile
├── docker-compose.yml                     # dev용
├── docs/
│   ├── brief.md                           # 이미 존재
│   ├── architecture.md
│   ├── snapshot-format.md
│   ├── graph-model.md
│   ├── impact-rules.md
│   ├── llm-policy.md                      # §4의 정책 본문
│   ├── security.md
│   ├── adr/
│   │   ├── 0001-python-over-typescript.md
│   │   ├── 0002-snapshot-as-source-of-truth.md
│   │   └── 0003-llm-as-optional-explainer.md
│   └── runbook.md
├── src/bwli/
│   ├── __init__.py
│   ├── __main__.py                        # `python -m bwli`
│   ├── cli.py                             # Typer app, sub-commands
│   ├── config.py                          # pydantic Settings, env+file+secret-ref
│   ├── logging.py                         # structlog, secret redaction filter
│   ├── errors.py
│   ├── collector/
│   │   ├── __init__.py
│   │   ├── client.py                      # httpx 기반, CSRF/cookie/sap-client
│   │   ├── auth.py                        # basic + (옵션) OAuth/X.509 hook
│   │   ├── endpoints.py                   # URL 빌더 + media types
│   │   ├── dataflow.py                    # /dmod/8TRANSIENT 파서
│   │   ├── xref.py                        # /repo/is/xref
│   │   ├── search.py                      # /repo/is/bwsearch
│   │   ├── transformation.py              # /trfn/.../m XML 파서 (mapping/routine ref)
│   │   ├── dtp.py                         # /dtpa/.../m
│   │   ├── composite_provider.py          # /hcpr/...
│   │   ├── query.py                       # /query/{compid}/{objvers}
│   │   ├── infoobject.py
│   │   ├── adso.py
│   │   ├── process_chain.py               # /rspc, /bw4/v1/.../variants
│   │   ├── role.py
│   │   └── rate_limit.py                  # 동시성/backoff
│   ├── snapshot/
│   │   ├── __init__.py
│   │   ├── writer.py                      # 디렉터리 트리 + DuckDB 인덱스
│   │   ├── reader.py
│   │   ├── manifest.py                    # snapshot meta: tenant, ts, version, hash
│   │   └── differ.py                      # 두 스냅샷 diff
│   ├── model/
│   │   ├── __init__.py
│   │   ├── nodes.py                       # pydantic: BwNode union (ADSO/IOBJ/TRFN/...)
│   │   ├── edges.py                       # FlowEdge, FieldEdge, ChainEdge, RoleEdge
│   │   ├── field_lineage.py               # source_field → rule → target_field + confidence
│   │   └── graph.py                       # networkx wrapper, invariants
│   ├── analyze/
│   │   ├── __init__.py
│   │   ├── lineage.py                     # up/down BFS, level cap, cycle guard
│   │   ├── impact_rules/
│   │   │   ├── __init__.py
│   │   │   ├── base.py                    # Rule protocol: applies?, evaluate(change) → Findings
│   │   │   ├── adso_field_delete.py
│   │   │   ├── adso_field_type_change.py
│   │   │   ├── infoobject_change.py
│   │   │   ├── transformation_routine.py
│   │   │   ├── dtp_filter.py
│   │   │   └── composite_provider_mapping.py
│   │   ├── confidence.py                  # High/Medium/Low + 근거 enum
│   │   ├── severity.py                    # Blocker/Major/Minor/Info
│   │   └── verification_gaps.py           # 룰이 결론낼 수 없는 case → "수동 확인 필요"
│   ├── report/
│   │   ├── __init__.py
│   │   ├── json_report.py                 # 안정 schema, JSON Schema 동봉
│   │   ├── markdown_report.py             # Jinja2
│   │   ├── graph_export.py                # GraphML/DOT/Mermaid
│   │   └── templates/
│   │       ├── lineage.md.j2
│   │       └── impact.md.j2
│   ├── llm/                               # 옵션 모듈, import는 lazy
│   │   ├── __init__.py
│   │   ├── policy.py                      # 허용/금지 작업 enum
│   │   ├── sanitizer.py                   # PII/시크릿/내부 호스트 제거, 토큰 제한
│   │   ├── providers/
│   │   │   ├── base.py                    # Protocol
│   │   │   ├── anthropic.py               # `claude-sonnet-4-6` 등
│   │   │   └── local_ollama.py            # 에어갭 옵션
│   │   ├── prompts/
│   │   │   ├── explain_transformation.j2
│   │   │   ├── summarize_lineage.j2
│   │   │   ├── explain_routine.j2
│   │   │   └── verify_steps.j2
│   │   ├── citations.py                   # 응답 내 node/edge id 검증
│   │   └── audit_log.py                   # prompt+response+model+tokens 영구 보관
│   ├── schemas/
│   │   ├── snapshot.schema.json
│   │   ├── graph.schema.json
│   │   ├── impact_report.schema.json
│   │   └── lineage_report.schema.json
│   └── _version.py
├── tests/
│   ├── conftest.py
│   ├── fixtures/
│   │   ├── http/                          # vcr.py cassettes (sanitized)
│   │   ├── xml/                           # 실제 SAP 응답 익명화본
│   │   │   ├── trfn_simple_mapping.xml
│   │   │   ├── trfn_with_abap_routine.xml
│   │   │   ├── dtp_with_filter.xml
│   │   │   ├── hcpr_join.xml
│   │   │   ├── query_with_ckf.xml
│   │   │   └── dataflow_8transient.xml
│   │   └── snapshots/
│   │       └── sample_tenant_v1/
│   ├── unit/
│   │   ├── collector/test_dataflow_parse.py
│   │   ├── collector/test_xref_parse.py
│   │   ├── collector/test_transformation_parse.py
│   │   ├── snapshot/test_writer_reader.py
│   │   ├── snapshot/test_differ.py
│   │   ├── analyze/test_lineage_traversal.py
│   │   ├── analyze/test_impact_field_delete.py
│   │   ├── analyze/test_impact_routine_change.py
│   │   ├── llm/test_sanitizer.py
│   │   └── llm/test_citation_check.py
│   ├── integration/
│   │   ├── test_collect_to_snapshot.py    # vcr fixture로 entire collect
│   │   ├── test_lineage_end_to_end.py
│   │   └── test_impact_end_to_end.py
│   ├── smoke/                             # 실제 BW 옵션, 기본 skip
│   │   └── test_live_bw_smoke.py
│   └── schemas/test_json_schemas_valid.py
├── scripts/
│   ├── anonymize_xml.py                   # 캡처본 → 픽스처 익명화 도구
│   └── verify_release.sh
└── .github/workflows/
    ├── ci.yml                             # lint + unit + integration (no live)
    └── release.yml                        # wheel + docker + checksums
```

---

## 3. 배포 전략 & 트레이드오프

| 채널 | 대상 | 장점 | 단점 | MVP 포함 |
|---|---|---|---|---|
| **wheel + `pipx install bwli`** | 사내 Python 사용자 | 가장 간단, 업데이트 쉬움 | Python 3.11 필요 | ✅ |
| **Docker 이미지** (`distroless-python` base) | Python 안 까는 BW 운영팀 | 의존성 0, CI 친화 | 인증서/프록시/볼륨 마운트 가이드 필요 | ✅ |
| **PyInstaller 단일 바이너리** | 노트북 배포·에어갭 | 더블클릭 가능 | 빌드 OS별 산출, lxml 번들 주의 | △ (M3) |
| **MCP 서버** | Claude/IDE 에이전트 | 자연어 lineage 질의 | 보안 검토 부담 | ❌ (MVP 이후) |
| **Web UI (FastAPI+Cytoscape.js)** | 비-CLI 사용자 | 그래프 시각화 | 인증/배포 부담 | ❌ (M4) |

**Config 우선순위**: CLI flag > 환경변수 > `~/.bwli/config.toml` > 프로젝트 `bwli.toml`. 시크릿은 값이 아니라 **참조**(`vault://`, `keyring://`, `env://BW_PASSWORD`, `file://`)만 저장. 평문 password는 `~/.bwli/config.toml`에서 금지(파서가 reject).

**에어갭**: (1) 사전 캡처된 스냅샷만 가지고 분석/리포팅이 동작해야 함, (2) LLM은 `local_ollama` 프로바이더만 허용 또는 완전 OFF, (3) 모든 외부 fetch는 collect 서브커맨드에 한정.

---

## 4. LLM 관여 결정 매트릭스 & 구현 패턴

### 4.1 분류 매트릭스

| 작업 | 결정론 가능? | LLM 권장? | 이유 |
|---|---|---|---|
| BW REST 호출, CSRF/세션 | ✅ | ❌ | 시크릿/멱등성 위험 |
| XML/JSON 파싱, 그래프 빌드 | ✅ | ❌ | 결정적, 테스트 가능 |
| Lineage 트리/필드 추적 (정적 매핑) | ✅ | ❌ | 룰로 충분 |
| 임팩트 룰 (필드 삭제/타입 변경/CP 매핑) | ✅ | ❌ | 명시 규칙, 감사 필요 |
| 그래프 diff (스냅샷 간) | ✅ | ❌ | 결정적 |
| **ABAP/AMDP 루틴 자연어 설명** | ⚠️ 일부 | ✅ 옵션 | 사람이 보기 어려운 코드 요약 |
| **장문 lineage 보고서 → 비즈니스 요약** | ⚠️ | ✅ 옵션 | 결정적 출력 보존, 요약은 부가 |
| **동적 의존성(루틴 내 RFC/SQL) 후보 추정** | ❌ | ✅ 옵션, **always low-confidence** | 어차피 사람이 확인 |
| **수동 검증 체크리스트 생성** | ⚠️ | ✅ 옵션 | 보일러플레이트 작성 효율 |
| BW에 쓰기/transport/활성화 | — | ❌ **금지** | 비목표, 정책상 차단 |
| 시크릿/raw payload 송신 | — | ❌ **금지** | 데이터 누출 |
| 임팩트 severity 최종 결정 | — | ❌ | 룰 엔진이 결정, LLM은 설명만 |

### 4.2 안전한 LLM 구현 패턴 — "Citation-Bound Explainer"

1. **Pure-deterministic 코어가 먼저 모든 결과를 산출.** JSON에 모든 노드/엣지/findings에 안정 ID(`adso:ZADSO_SALES#field:NETVAL`) 부여.
2. LLM 호출은 **post-processing only**. 입력 = sanitizer를 거친 그래프 슬라이스(노드 ≤ N개, 루틴 코드 ≤ M lines, 호스트/계정/IP/사번/이메일 마스킹).
3. **Citation 강제**: 프롬프트가 "모든 주장은 입력에 등장한 ID로만 인용"하도록 지시 → 응답에서 ID 외 사실 주장은 검증기가 제거 또는 경고 표시.
4. **Confidence label 강제**: LLM 출력은 항상 `High/Medium/Low`, 그리고 출처 ID. UI/리포트에서 LLM 섹션은 시각적으로 분리("AI 보조 설명, 검증 필요").
5. **Audit log**: prompt/response/model/tokens/sha256(input) 영구 보관, 기본 `~/.bwli/audit/`.
6. **Provider 추상화**: 기본 OFF. `--explain` flag 또는 `BWLI_LLM=on` 일 때만 활성. 에어갭은 `provider=local_ollama` 강제.
7. **차단 리스트**: collector/snapshot/impact-rule 코드 경로에서는 `from bwli.llm` import 자체 금지(`importlinter` contract로 CI 검사).

---

## 5. MVP 마일스톤 슬라이스 (Bite-sized)

각 슬라이스는 머지 가능한 단위. `M0→M5`. 각 슬라이스 끝에 데모 + 승인 게이트.

### M0 — 부트스트랩 (0.5일)
- `pyproject.toml`, `uv` 환경, `bwli --version`, `pre-commit` (ruff+mypy+pytest), CI green.
- 빈 Typer 앱 `bwli collect|lineage|impact|report|diff` 스텁.

### M1 — Snapshot 핵심 (3일)
- `collector.client` (CSRF, cookie, sap-client, TLS opt-out flag) — vcr cassette 기반 테스트만.
- `collector.dataflow` + `collector.xref` + `collector.search` 3개만.
- `snapshot.writer/reader/manifest`.
- CLI: `bwli collect --object ADSO:ZADSO_X --depth 2 --out snapshots/2026-06-04/`.
- **데모**: 픽스처 BW로 ADSO 1개 수집 → 스냅샷 디렉터리 생성 + DuckDB 인덱스.

### M2 — 그래프 + Lineage (3일)
- `model.nodes/edges/graph`, `analyze.lineage` (up/down, level cap, cycle 안전).
- `collector.transformation/dtp/composite_provider/infoobject` 추가.
- CLI: `bwli lineage --object ADSO:ZADSO_X --direction both --levels 5 --format text|json|mermaid`.
- **데모**: 트리 + Mermaid 다이어그램 출력.

### M3 — Impact 룰 v1 (4일)
- `impact_rules`: ADSO field delete / type change / IOBJ change / TRFN routine ref change / DTP filter change / HCPR mapping change (총 6 룰).
- `confidence/severity/verification_gaps`.
- `snapshot.differ`: 두 스냅샷 → 변경 후보 자동 추출 후 룰 평가.
- CLI: `bwli impact --change-file change.yaml --snapshot snapshots/...` AND `bwli impact --diff snap-a snap-b`.
- 산출: `impact_report.json` + `impact_report.md`.
- **데모**: ADSO 필드 삭제 시나리오 → 영향받는 TRFN/DTP/Query/CKF 목록 + severity/confidence.

### M4 — Field-level Lineage v1 (3일)
- TRFN XML의 mapping(1:1/expression/routine ref) 파서 → `FieldEdge` with confidence(`direct`/`expression`/`routine:opaque`).
- CKF/RKF의 키 피겨 의존성.
- CLI: `bwli lineage --field ADSO:ZADSO_X.NETVAL --direction up`.

### M5 — 옵션 LLM Explainer + 배포 (3일)
- `llm.sanitizer`, citation 검증기, `--explain` flag (lineage/impact 두 명령에 부착).
- Provider: Anthropic + Ollama. 기본 OFF.
- Docker 이미지 + GitHub release(wheel + image digest + checksums).
- **승인 게이트**: 보안팀 리뷰(시크릿 핸들링, LLM 정책).

### (이후, MVP 외) M6+ — Web UI / MCP / 단일 바이너리. 별도 승인.

---

## 6. 테스트 전략

**원칙**: 라이브 BW 없이도 전 시나리오 재현 가능해야 함.

| 레벨 | 도구 | 대상 | 비고 |
|---|---|---|---|
| Unit | pytest | 파서, 룰, sanitizer, graph 알고리즘 | 코드 커버 ≥85% 코어 |
| Cassette | `vcrpy` (시크릿 스크럽 후크) | collector.client + 엔드포인트 모듈 | 카세트는 익명화 후 커밋 |
| Schema | `jsonschema` | 모든 export 산출물 | CI 차단 |
| Snapshot | `syrupy`/`inline-snapshot` | markdown 리포트, mermaid | diff 시 사람 검토 |
| Property | `hypothesis` | graph 순회/cycle guard | random DAG |
| Architecture | `import-linter` | `collector → llm` 금지, `analyze → collector` 금지 | CI 차단 |
| Live smoke | pytest `@pytest.mark.live` | 실제 BW 1개 객체 수집/lineage | `BWLI_LIVE=1`일 때만 |
| Mutation | `mutmut` (룰만) | 임팩트 룰 견고성 | 옵션, 분기별 |

**픽스처 만들기**: 실제 BW 응답을 `scripts/anonymize_xml.py`로 객체명/설명/사용자/호스트 치환 → `tests/fixtures/xml/`에 커밋. 시크릿/PII는 거부.

---

## 7. 보안 / 신뢰 경계

```
┌─────────── User Host (trusted) ───────────┐
│  bwli CLI                                  │
│    ├─ Config (env / vault / keyring)       │
│    ├─ collector ──► HTTPS ──► BW system    │   ◄── 유일한 outbound BW 경로
│    ├─ snapshot writer ──► local disk       │
│    ├─ analyze (pure)                       │
│    └─ llm (optional) ──► HTTPS ──► LLM API │   ◄── sanitizer 강제 통과
└────────────────────────────────────────────┘
```

**불변 규칙(코드/CI/문서 일치)**
- 시크릿은 메모리에 있는 시간을 최소화, 절대 로그/스냅샷/리포트/LLM 입력에 등장 금지(`logging.SecretRedactionFilter` + sanitizer 화이트리스트).
- 스냅샷 디렉터리에 호스트/계정 정보는 manifest의 **해시**로만(예: `tenant_id = sha256(BW_URL+BW_CLIENT)[:12]`).
- TLS 검증은 기본 ON. `--insecure-tls` flag는 명시적으로 ALL CAPS 경고 후 로그에 기록.
- LLM 프로바이더 호출은 평균 토큰/요청·분당 횟수 cap. 사용자가 명시적 동의(`bwli config set llm.enabled true`)해야 ON.
- MVP에서 **쓰기 동작 부재** 자체를 보안 자산으로 다룸: `collector`의 HTTP 메서드는 GET-only로 화이트리스트(`client.get`만 export, `post/put/delete` 없음). CI에서 import-linter로 강제.
- 의존성: `pip-audit`/`safety` CI, lockfile commit, SBOM 산출(릴리스 워크플로).

---

## 8. 검증 명령 (사람·CI 공용)

```bash
# 환경
uv sync --frozen
uv run bwli --version

# 정적 검사
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run lint-imports                    # import-linter contracts

# 테스트
uv run pytest -m "not live" -q          # 기본 CI
uv run pytest --cov=bwli --cov-fail-under=85
BWLI_LIVE=1 uv run pytest -m live -q    # 옵션, 사내 BW에서만

# 스키마 검증
uv run python -m bwli.schemas.validate snapshots/sample/manifest.json
uv run jsonschema -i out/impact_report.json src/bwli/schemas/impact_report.schema.json

# 보안
uv run pip-audit
uv run bandit -r src

# 산출물 스모크
uv run bwli collect --from-cassette tests/fixtures/http/m1_basic.yaml --out /tmp/snap
uv run bwli lineage --object ADSO:ZTEST --snapshot /tmp/snap --format json | jq .
uv run bwli impact --change-file tests/fixtures/changes/field_delete.yaml \
                   --snapshot /tmp/snap --format md

# Docker
docker build -t bwli:dev .
docker run --rm -e BWLI_OFFLINE=1 -v $PWD/snapshots:/snap bwli:dev lineage --object ADSO:ZTEST --snapshot /snap/sample
```

---

## 9. 위험 / 미지 & 검증 방법

| 위험 | 영향 | 검증 |
|---|---|---|
| `/dmod/8TRANSIENT`가 루틴 기반 의존성 누락 | impact false-negative | M3에서 known-routine 픽스처로 누락 확인, 리포트에 "routine-based may be missing" 워닝 항상 표시 |
| BW/4HANA 버전별 미디어 타입 차이 | collect 실패 | `/sap/bw/modeling/discovery`로 시작 시 동적 검색 후 캐시 |
| BW 7.5 시스템에서 사용 시도 | 406 등 오류 | 시작 시 systeminfo 호출 → 버전 미충족시 hard fail |
| 대용량 lineage(>수천 노드) 성능 | UX/메모리 | networkx graph + DuckDB 인덱스로 슬라이스. CLI `--levels` 강제. |
| ABAP 루틴 텍스트가 거대/이진 | LLM 토큰 폭주, 시크릿 노출 | sanitizer에서 cap, hash, fingerprint만 보내는 옵션 |
| 카세트 익명화 누락 | 사내 정보 누출 | `anonymize_xml.py` + 커밋 훅에서 SAP host/유저 패턴 grep 차단 |
| Web UI/MCP로 스코프 확장 압박 | 보안 검토 폭증 | 비목표 명시 + M5 게이트에서만 별도 결정 |

---

## 10. 비목표 (MVP에서 명시 제외)

- BW 객체 **생성/수정/삭제/활성화/transport** 일체.
- **데이터 조회**(BICS/reporting/data preview) — 메타데이터/모델링만.
- LLM이 BW에 직접 호출하거나, 임팩트 severity를 결정하는 동작.
- Web UI, MCP 서버, 단일 바이너리.
- 다중 시스템 cross-tenant lineage(같은 BW 내 lineage만, 후속).
- Authorization/Role 관리 변경.

---

## 11. 승인 게이트 (JC님 승인 필요)

| 게이트 | 시점 | 산출물 |
|---|---|---|
| **G0 — 본 계획 승인** | 지금 | 본 메시지 + ADR 0001~0003 초안 |
| **G1 — M1 종료** | Snapshot writer 시연 후 | 익명화된 스냅샷 1개, 보안 체크리스트 |
| **G2 — M3 종료** | 임팩트 룰 v1 시연 | 룰 표 + false-negative 워닝 표기 확인 |
| **G3 — LLM 활성화 결정** | M5 직전 | `docs/llm-policy.md` + 사내 보안팀 OK |
| **G4 — 배포 채널 선택** | M5 끝 | wheel만? Docker 포함? 에어갭 변형? |
| **G5 — MVP 이후 확장** | 별도 | Web UI / MCP / Write-mode 별도 RFC 필요 |

---

## 12. 첫 구현 슬라이스 (지금 바로 가능, M0+M1 일부)

목표: **승인 즉시 1~2일 안에 머지 가능한 첫 PR**. 라이브 BW 없이 통과.

1. `pyproject.toml`(uv 기반) + `src/bwli/__init__.py` + `bwli` entry point + `bwli --version`.
2. `src/bwli/cli.py`: Typer 앱과 4개 스텁(`collect/lineage/impact/diff`)이 `NotImplementedError` 대신 "not yet" 메시지 + exit 2.
3. `src/bwli/config.py`: pydantic Settings, env / `~/.bwli/config.toml` 로드, 시크릿 평문 거부 검증.
4. `src/bwli/logging.py`: structlog + `SecretRedactionFilter`(BW_PASSWORD, X-CSRF-Token, Cookie 마스킹).
5. `src/bwli/collector/client.py`: GET-only `httpx.Client`, CSRF fetch, cookie jar, `sap-client` 헤더, `--insecure-tls` flag. **POST/PUT/DELETE 메서드 미존재**.
6. `src/bwli/collector/dataflow.py`: `/dmod/8TRANSIENT` 응답 XML 파서(참조 레포 정규식 패턴을 lxml로 이식) → `list[DataflowNode]`.
7. `tests/fixtures/xml/dataflow_8transient.xml`: 익명화된 1개 응답.
8. `tests/unit/collector/test_dataflow_parse.py`: 파서가 노드 수/엣지/타입을 정확히 산출하는지(샘플 3건).
9. `tests/unit/collector/test_client_csrf.py`: vcr 카세트로 CSRF fetch 1회·재사용 확인 + 시크릿 redaction 어서션.
10. `.github/workflows/ci.yml`: ruff + mypy + pytest(`-m "not live"`) + import-linter.
11. `docs/adr/0001-python-over-typescript.md`, `0002-snapshot-as-source-of-truth.md`, `0003-llm-as-optional-explainer.md`.
12. `docs/architecture.md` 첫 버전(본 §1 요약).

**완료 정의(DoD)**
- `uv run pytest` green, mypy/ruff 0 errors.
- `uv run bwli --version` 동작, 4개 서브커맨드가 표시되며 "not yet implemented" 안내.
- 파서가 픽스처 XML에서 노드 수·엣지·타입을 정확히 추출.
- CI가 main에서 통과. README에 "M0 + M1 partial" 명시.

---

이 계획에 승인(G0)을 주시면 **첫 구현 슬라이스(§12)** 의 PR부터 진행하겠습니다. 변경 원하시면 짚어주세요 — 특히 (a) Python vs TS 선택, (b) MCP 서버를 MVP에 포함할지, (c) 사내 LLM/에어갭 요구사항이 있다면 §4 정책을 조정합니다.