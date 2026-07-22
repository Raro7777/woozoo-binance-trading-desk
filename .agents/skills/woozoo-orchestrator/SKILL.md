---
name: woozoo-orchestrator
description: "Woozoo Binance Trading Desk의 Phase 0~9 설계·구현·테스트·재실행을 에이전트 팀으로 조율한다. Binance 공개 데이터, LangGraph 분석 조직, Paper Broker, Risk Engine, 원장, 승인 UI, Testnet, 운영 또는 이전 결과의 수정·보완·업데이트 요청이면 반드시 사용한다. 현재 Phase 밖의 구현과 실거래 기능은 조율하지 않고 차단한다."
---

# Woozoo Orchestrator

Woozoo 개발 작업을 현재 승인된 Phase 안에서 분해하고, 전문 에이전트의 산출물을 안전 게이트로 통합한다.

## Paper MVP 정리 모드 우선 규칙

`phase-state.json`의 `maintenance_mode.active=true`이면 이 절이 아래의 일반 Phase 마감·증거 절차보다 우선한다.

- Phase 8 구현을 중단하고 `codex/phase-7-trading-room`의 Paper MVP만 유지보수한다.
- 일반 변경 완료 기준은 구현, 관련 테스트, diff 자체 검토, Draft PR 보고다.
- 일반 변경에 `_workspace` 산출물, 파일별 digest, JSON acceptance artifact, 다중 Codex 검토, 외부 리뷰, Phase 승인 기록을 요구하지 않는다.
- 금융 원장, Risk Engine, 주문 멱등성, Kill Switch, Testnet 주문, Mainnet 전환만 강화 검증 대상으로 분류한다.
- 과거 Manifest와 Evidence는 보존하지만 일반 CI 입력으로 갱신하지 않는다.
- `docs/DEVELOPMENT_WORKFLOW_KO.md`와 `pnpm test:core|safety|integration|e2e`를 운영 정본으로 사용한다.

이 모드는 Phase를 되돌리거나 올리는 전환이 아니다. 안전 경계는 그대로 적용한다.

## 시작 전 필수 로드

1. `docs/woozoo-trading-desk/phase-state.json`
2. `.agents/skills/woozoo-phase-gates/SKILL.md`
3. `.agents/skills/woozoo-safety-boundaries/SKILL.md`
4. 작업이 금액·주문·원장·리스크를 건드리면 `.agents/skills/woozoo-financial-invariants/SKILL.md`
5. Binance 데이터가 관련되면 `.agents/skills/woozoo-binance-public-data/SKILL.md`
6. `docs/woozoo-trading-desk/working_history/`가 있으면 시각순 최신 결과서 하나의 `## 7. 다음 단계 참조`를 읽는다.

사용자의 현재 지시가 원본 핸드오프와 충돌하거나 안전 범위를 넓히려면 임의로 해석하지 말고 차이를 보고한다.

## 실행 모드

- Claude Code: 팀 도구가 가용하면 에이전트 팀을 사용한다.
- Codex: `.codex/agents/*.toml` 전문 subagent를 사용하고 `_workspace/` 파일로 전달한다.
- 동시 실행 기본 3, 최대 5. 외부 리뷰 동시성은 별도 2다.
- Codex 런타임의 일반 교차검토는 Codex 전용 `woozoo-codex-cross-reviewer`가 맡는다. 동일 엔진·분리 컨텍스트의 내부 검토이며 외부 독립 리뷰로 계산하지 않는다.
- Claude Code 런타임에는 이 역할을 로컬 Claude agent로 복제하지 않는다. 일반 교차엔진 리뷰가 필요하면 `external-review-loop`가 외부 `codex exec --sandbox read-only`를 호출한다.
- 현재 제품 Phase 0에서는 architect와 세 도메인 builder를 모두 **설계 전용 모드**로 호출하고, safety QA를 역할 분리된 내부 관점에서 후행시킨다. builder는 코드 작성에 사용하지 않는다.

## 에이전트 구성

| 에이전트 | 책임 | 주요 스킬 | 산출물 규약 |
|----------|------|-----------|-------------|
| `woozoo-system-architect` | 구조, 신뢰 경계, API·이벤트·ERD, Phase 계획 | phase-gates, safety-boundaries | `_workspace/{phase}_woozoo-system-architect_{artifact}` |
| `woozoo-market-agent-builder` | 공개 데이터, Evidence, LangGraph 분석 조직 | binance-public-data, safety-boundaries | `_workspace/{phase}_woozoo-market-agent-builder_{artifact}` |
| `woozoo-risk-ledger-builder` | Risk, Paper Broker, 원장, 멱등성, 대조 | financial-invariants, safety-boundaries | `_workspace/{phase}_woozoo-risk-ledger-builder_{artifact}` |
| `woozoo-web-platform-builder` | Next.js, FastAPI 경계, 승인·Kill Switch UI, 운영 | phase-gates, safety-boundaries | `_workspace/{phase}_woozoo-web-platform-builder_{artifact}` |
| `woozoo-safety-qa-inspector` | 계약 교차 비교, 보안·금융 불변식, 단계 준수 | 네 개 전용 스킬 전체 | `_workspace/{phase}_woozoo-safety-qa-inspector_report.md` |
| `woozoo-codex-cross-reviewer` (Codex 전용) | 산출물·diff의 적대적 내부 교차검토, 누락·회귀·검증 주장 반증 | phase-gates, safety-boundaries | `_workspace/internal-reviews/{phase}_woozoo-codex-cross-reviewer_report.md` |

파일명은 각 에이전트 정의의 입력/출력 프로토콜을 단일 출처로 삼고 이 표는 그 규약을 그대로 반영한다. 에이전트 정의는 역할을 나타내고 스킬은 절차를 나타낸다. 동일 책임을 새 이름으로 추가하지 않는다.

모든 전문 에이전트 handoff의 일반 경로 계약은 `_workspace/{phase}_{agent}_{artifact}`이며 `{artifact}`가 확장자를 포함한다. 에이전트는 완결된 보고서 텍스트만 반환하고 `_workspace` 파일을 직접 생성·수정·이동하지 않는다. 오케스트레이터만 반환문을 할당된 경로에 영속화하며, 표의 경로는 이 일반 계약의 구체 예다. 내부 Codex 교차검토의 별도 `internal-reviews/` 경로도 같은 단일 persistence-owner 원칙을 따른다.

이들은 저장소를 만드는 **개발 하네스 에이전트**이며 제품에 배포되는 시장 분석·Trader 에이전트가 아니다. 어떤 개발 에이전트에도 거래소 Secret, 서명 기능, 주문·취소 도구 또는 거래 MCP를 제공하지 않는다. Phase 8 승인 전에는 Testnet Gateway 전용 builder를 정의하지 않으며, 승인 시 별도 최소 권한 역할로 추가한다.

`woozoo-safety-qa-inspector`는 PASS/FAIL/UNVERIFIED로 결정적 안전·금융·통합 게이트를 검증한다. `woozoo-codex-cross-reviewer`는 생산자와 다른 컨텍스트에서 FINDINGS/NO_CONFIRMED_FINDINGS/UNVERIFIED와 P0~P3 근거를 반환한다. 둘은 대체 관계가 아니며 서로의 보고서를 먼저 읽지 않는다. 후자는 Codex 러너와 동일 엔진이므로 외부 독립 리뷰로 계산하지 않는다.

## 제품 Phase별 역할 라우팅

| 제품 Phase | 주 역할 | 검증 |
|------------|---------|------|
| 0 | architect + market/risk/web builder 설계 전용 fan-out | safety QA 후행 |
| 1 | architect + web-platform | safety QA |
| 2~3 | market-agent, 계약 변경 시 architect | safety QA |
| 4~5 | risk-ledger + architect | safety QA |
| 6 | market-agent + architect | safety QA |
| 7 | web-platform + 관련 market/risk consumer 검토 | safety QA + 브라우저 QA |
| 8 | 새 최소 권한 gateway builder + risk-ledger + architect + web-platform | safety QA + reconciliation E2E |
| 9 | web-platform 중심, 장애 범위에 따라 market/risk | safety QA + 운영 훈련 |

모든 역할을 매번 호출하지 않는다. 현재 Phase와 계약 변경 범위에 필요한 expert pool만 선택한다.

표준·중대 변경의 단계 마감에는 관련 builder와 safety QA 뒤에 Codex reviewer를 후행한다. 경량 문구 수정은 내부 QA만으로 끝낼 수 있다.

## 워크플로우

### Run Stage 0: 컨텍스트 확인

1. 현재 브랜치와 작업트리를 읽고 사용자 변경을 보존한다.
2. `phase-state.json`의 `current_phase`, 허용·금지 범위를 읽는다.
3. 기존 `_workspace/`가 없으면 초기 실행, 있고 부분 수정이면 에이전트별 산출물 규약으로 식별한 해당 산출물만 재실행한다.
4. 새 입력으로 전면 재실행할 때만 기존 `_workspace/`의 절대 경로가 저장소 내부인지 확인한 뒤 `_workspace_{YYYYMMDD_HHMMSS}/`로 이동한다. 삭제하지 않는다.
5. 실행 ID와 적용 Phase를 `_workspace/status/orchestrator.json`에 기록한다.

### Run Stage 1: 범위·위험 판정

1. 요청을 현재 Phase의 허용 범위에 매핑한다.
2. 범위 밖이면 구현하지 않고 차단 근거와 다음 승인 조건을 제시한다.
3. 다음 중 하나면 `중대`로 분류한다: API·이벤트·DB 계약, 인증·비밀, 주문, 잔고, 원장, 리스크, 승인, 거래소 URL, Phase 변경.
4. 중대 작업은 내부 safety QA, Codex 내부 교차검토, 가용한 외부 교차엔진 리뷰를 서로 구분해 수행한다. 외부 엔진이 없으면 부재를 기록한다.

### Run Stage 2: 계획·RED 계약

1. `woozoo-system-architect`가 변경 계약과 성공 기준을 작성해 반환하고 오케스트레이터가 할당 경로에 저장한다.
2. 제품 Phase 0에서는 market-agent, risk-ledger, web-platform builder가 각 도메인의 설계·테스트 초안을 병렬 작성해 반환한다. 테스트 매트릭스와 반례를 포함한 문서만 허용하며 실행 가능한 테스트 코드·fixture·scaffold는 만들지 않는다.
3. 오케스트레이터가 초안을 저장한 뒤 `woozoo-safety-qa-inspector`가 생산자와 역할을 분리한 내부 관점에서 교차 검증하고 보고서를 반환한다.
4. 코드 단계에서는 구현 전 실패 테스트를 먼저 정의한다. 계약·스키마·보안 RED 테스트는 외부 리뷰 대상이다.
5. 제품 Phase 0에서는 문서 산출물만 작성하고 Phase 1 코드나 거래 로직을 만들지 않는다.

### Run Stage 3: 승인된 제품 Phase만 실행

1. 파일 소유권을 에이전트별로 분리하고 충돌 경로를 병렬 수정하지 않는다.
2. 구현 에이전트는 자신에게 필요한 전용 스킬과 `.agents/skills/woozoo-orchestrator/references/dev-rules.md`, `.agents/skills/woozoo-orchestrator/references/dev-rules.local.md`, `.agents/skills/woozoo-orchestrator/references/tdd-doctrine.md`, `.agents/skills/woozoo-orchestrator/references/codex-runtime-paths.local.md`를 따른다. Codex에서는 factory 정본의 `.claude` 주입 예시나 과거 `external-review-loop.md` 링크를 실행 경로로 사용하지 않는다.
3. AI 에이전트 코드와 결정론적 주문·리스크·원장 코드를 같은 권한 경계에 두지 않는다.
4. Phase 8 전에는 Testnet 주문 게이트웨이를 만들지 않는다.

### Run Stage 4: 점진 QA

각 모듈 직후 `woozoo-safety-qa-inspector`를 실행한다. 존재 확인이 아니라 생산자와 소비자를 동시에 읽어 다음을 교차 비교한다.

- DB ↔ 도메인 모델 ↔ API/event schema ↔ UI 타입
- Proposal/Risk/Approval hash ↔ 상태 전이 ↔ 실행 명령
- 주문 명령 ↔ idempotency ↔ 원장 ↔ reconciliation
- Binance 데이터 이벤트 시간 ↔ `as_of` ↔ Evidence ↔ 에이전트 보고서
- 구성·URL·비밀 경계 ↔ 시작 시 fail-closed 검사

표준·중대 변경에서는 검토 대상을 먼저 동결한다. Codex 런타임은 `woozoo-safety-qa-inspector`와 `woozoo-codex-cross-reviewer`에 같은 동결본을 주되 서로의 1차 보고서를 보여주지 않고 별도 컨텍스트로 실행한다. 동시성 제한 때문에 순차 실행해도 이 분리를 지킨다. cross-reviewer는 파일을 고치거나 다른 agent·외부 CLI를 호출하지 않고 FINDINGS/NO_CONFIRMED_FINDINGS/UNVERIFIED와 P0~P3 근거만 반환한다. 오케스트레이터가 반환문을 내부 리뷰 경로에 저장한다.

Claude Code 런타임은 로컬 Claude agent로 일반 교차검토를 대체하지 않는다. 그 런타임의 일반 Codex 검토는 Stage 5의 외부 교차엔진 경로에서만 수행한다.

### Run Stage 5: 단계 마감 게이트

1. 내부 테스트·정적 검사와 blocking safety QA를 통과시킨다.
2. Codex 런타임의 표준·중대 변경은 별도 내부 경로로 `woozoo-codex-cross-reviewer`를 실행하고 결과를 판정한다. 이 결과에는 `external_gate_satisfied=false`를 유지한다.
3. `external-review-loop`는 다른 엔진 리뷰에만 사용한다. 후보는 `select-project-reviewers.sh`의 프로젝트 정책 출력만 신뢰한다. factory-managed `check-review-tools.sh`는 원시 설치 탐지기로만 간접 사용하며 그 출력을 직접 정책 선택기로 삼지 않는다. Claude reviewer는 모든 런타임에서 비활성이다. Codex 러너 후보는 agy/Gemini뿐이고, Claude 러너는 외부 Codex와 agy/Gemini를 사용할 수 있다.
4. 외부 verdict candidate는 프로젝트 소유 `validate-external-verdict-sources.py`를 통과한 뒤에만 정식 ledger와 scorecard 입력이 된다. `agy|gemini|re-review` 밖의 source 또는 검증 불가는 fail-closed이며 외부 게이트를 충족하지 않는다.
5. 외부 엔진이 없거나 실행에 실패하면 `external-review-unavailable`을 기록한다. safety QA나 같은 엔진 Codex 교차검토를 외부 PASS로 승격하지 않는다.
6. 모든 리뷰 지적은 실파일과 대조해 확인·부분 확인·이월·기각으로 판정한다. 합의만으로 확정하지 않는다.
7. 중대 작업의 외부 리뷰 루프는 신규 확인 0건이 2회 연속일 때 수렴으로 본다. 최대 3라운드다. 외부 리뷰어가 없으면 수렴을 허위 산출하지 않고 부재 상태로 끝낸다.
8. 개발 Phase 승인은 acceptance artifact digest에 결속한다. 승인 전에는 Phase를 올리거나 push하지 않는다.
9. Testnet 주문 승인은 별개다. Proposal hash, Risk Decision hash, 정확한 order preview, TTL과 일회성 nonce에 결속하며 한 번 사용하거나 만료되면 재사용하지 않는다.

### Run Stage 6: 영속 결과서

중대 작업은 `docs/woozoo-trading-desk/working_history/{phase}_{title}_{timestamp}.md`에 T1 결과서를 쓴다. 변경 파일, 검증, 미해결, 결정 이유와 `## 7. 다음 단계 참조`를 포함한다. `_workspace/`가 없어도 판단을 재구성할 수 있어야 한다.

## 외부 스킬 라우팅

관련 외부 스킬의 사용·금지·핀 정책은 `references/external-skill-routing.md`를 읽는다. 설치 여부를 가정하지 말고 현재 런타임의 가용 목록을 확인한다.

## 하네스 자체 갱신

이 프로젝트는 factory 관리 reference를 `woozoo-orchestrator`에, review script를 `external-review-loop`에 분리 배치한다. 중앙 `harness-update.sh`를 개별 스킬에 직접 `apply`하면 다른 스킬의 파일을 `NEW`로 오인할 수 있으므로 금지한다. Git Bash에서 다음 어댑터를 사용해 현재 런타임과 myharness 팩토리 경로를 명시한다.

```bash
.agents/skills/woozoo-orchestrator/scripts/update-factory-managed.sh plan .agents <myharness_factory_dir>
.claude/skills/woozoo-orchestrator/scripts/update-factory-managed.sh plan .claude <myharness_factory_dir>
```

어댑터는 각 스킬에 해당하는 두 파일만 보이는 검증된 임시 factory view를 만들고 중앙 갱신기를 호출한다. `plan`을 먼저 검토하고, 실제 사용자 승인이 있는 경우에만 같은 어댑터의 `apply`를 사용한다. `jq`가 없으면 manifest 판정은 보수 모드이므로 설치를 우회하지 말고 결과서에 제한을 남긴다.

## 오류 처리

- 에이전트 실패: 1회 재시도 후 누락과 영향을 기록하고 진행 가능성을 판정한다.
- Codex cross-reviewer 실패: 1회 재시도 후 `UNAVAILABLE`로 기록한다. Claude reviewer로 자동 대체하거나 외부 독립 리뷰가 끝났다고 표시하지 않는다.
- 안전 또는 금융 QA 실패: fail-closed. 부분 결과로 구현을 승인하지 않는다.
- 공식 문서 확인 불가: 변동 가능한 Binance 전제를 확정하지 않고 미검증으로 남긴다.
- 작업트리 충돌: 사용자 변경을 보존하고 해당 파일 수정 전 보고한다.
- 과반 에이전트 실패 또는 계약 충돌: 자동 통합하지 않고 사용자에게 결정이 필요한 지점을 제시한다.

## 테스트 시나리오

### 정상

사용자가 “Phase 0 아키텍처 문서를 준비해줘”라고 요청한다. 현재 단계 0을 확인하고 architect와 세 domain builder를 설계 전용 fan-out으로 호출한 뒤 safety QA를 역할 분리된 내부 컨텍스트로 후행시킨다. 문서·테스트 설계와 결과서만 남기고 애플리케이션 코드는 생성하지 않는다.

### 범위 위반

사용자가 “바로 Binance 실계좌에 주문을 넣어줘”라고 요청한다. safety-boundaries와 phase-gates가 작업을 차단하고, 실행·키 요청·연결 없이 금지 근거만 보고한다.

### 후속 재실행

사용자가 “이전 결과 기반으로 원장 설계만 보완해줘”라고 요청한다. 최신 결과서와 기존 산출물을 읽고 risk-ledger와 safety QA만 부분 재실행한다.

### 리뷰어 교체

사용자가 “Claude 검토 대신 Codex를 써줘”라고 요청한다. Codex 런타임에서는 `woozoo-codex-cross-reviewer`를 읽기 전용 내부 교차검토로 실행하고 Claude CLI를 호출하지 않는다. 다른 엔진 리뷰어가 없으면 `external-review-unavailable`을 명시하며 Codex 결과를 외부 독립 검토로 오인하지 않는다.
