# P0-12 Phase 1 정확 범위와 Phase 0 완료 보고서 후보

- 상태: `POLICY_AND_GIT_APPROVED / FINAL_REVIEW_AND_EVIDENCE_PENDING`
- 제품 Phase: `0`
- 정본 Phase 상태: `../phase-state.json`
- 목적: 원본 부록 A의 운영 규칙, Phase 0 산출물, 다음 Phase의 정확한 변경 허용선을 하나의 인계 계약으로 묶는다.
- 판정 주의: 이 문서는 Phase 1 착수 승인이 아니다. P0-01~12 전건 PASS, safety QA, 외부 리뷰 기록, Git 게이트, 동결 artifact digest에 결속된 사용자 승인이 있기 전에는 Phase 1 파일을 만들지 않는다.

## 1. 권장 MVP와 릴리스 경계

| 릴리스 | 범위 | 완료선 |
|---|---|---|
| MVP / Release 1a | Phase 0~7, 로컬 단일 운영자 Paper Trading 폐루프 | `mvp-plan-and-acceptance.md`의 MVP-01~12 전건 PASS와 `UNVERIFIED` 0 |
| Testnet Validation | Phase 8, 별도 package/process/env와 Binance Spot Testnet | 별도 digest-bound 사용자 승인, 기본 OFF, 인증·nonce·대조·불명확 결과 복구 게이트 |
| Readiness | Phase 9, 장기 모의운영·장애훈련·보안/한계 보고 | 운영 증거와 독립 검토; Mainnet live 승인이 아님 |
| Release 1.1 후보 | 공개 파생 telemetry, 뉴스·소셜 | 출처·라이선스·provenance·prompt-injection·retention 계약을 새로 승인한 뒤에만 설계 |

MVP는 공개 BTC/ETH Spot 데이터에서 immutable Evidence를 만들고, AI가 구조화 분석과 Proposal만 생성하며, 결정론적 Risk와 인증된 사람 승인 뒤 내부 Paper 주문·복식 원장·감사 UI까지 닫히는 운영 콘솔이다. Mainnet private/live, 출금, Futures, Margin, leverage, short와 AI 주문 도구는 MVP 이후에도 승인 대상이 아니라 금지 범위다.

## 2. 원본 부록 A 루트 운영 규칙 추적

`AGENTS.md`와 `CLAUDE.md`의 `루트 운영 규칙 — 원본 부록 A 집약`이 공통 정본이다. 런타임별 agent/skill adapter만 각 파일에서 다르게 유지한다.

| 부록 A 축 | 루트 규칙 | 설계 증거 |
|---|---|---|
| Mission·Default mode | 공개 Spot 연구·Paper 운영실, 명시적 `TRADING_MODE=paper`, 누락·미지 mode fail-closed | MVP-01, P0-01, P0-04·05 |
| Hard safety·AI boundary | 금지 거래 capability 0, AI는 Evidence→Proposal만 | MVP-04·12, P0-05·10 |
| Financial correctness | Decimal, FIFO 후보, commodity별 복식 원장, immutable+reversal, atomic transaction | MVP-07·08·11, P0-08 |
| Data integrity | raw/provenance, 시간 의미, watermark·quality, 미래 오염·gap 차단 | MVP-02·03, P0-02·06·09 |
| Engineering | versioned 계약, typed 경계, idempotency, inbox/domain/ledger/outbox 원자성 | MVP-07·10·11, P0-04·07·08 |
| Security | 비밀 최소화·redaction, process/egress 분리, Phase 1~7 private/Testnet zero | MVP-12, P0-05·10 |
| DoD | 코드·계약·문서·테스트·감사 evidence 동시 갱신, 필수 manifest 전건 PASS | MVP-01~12, P0-09·11 |
| Workflow | phase-state preflight, Phase별 branch/PR, RED→GREEN, diff review, safety QA | P0-09·11과 phase contract |
| 품질 명령 | 루트 `pnpm` 명령과 `uv`/Docker 하위 도구, 빈 성공 script 금지 | P0-11 |

## 3. Phase 0 변경·산출물 보고

### 3.1 문서

| 경로 | 역할 | 현재 판정 |
|---|---|---|
| `../mvp-plan-and-acceptance.md` | Phase 0~7 MVP 컷, 12개 must-pass, Go/No-Go | 동결 전 후보 |
| `requirements-and-repository-audit.md` | 원본·저장소 감사, 모순·누락·decision register | P0-01 후보; P0-03 policy approved, final review pending |
| `requirements-trace-manifest.json` | 123개 canonical `REQ-*`, exact source anchor·text digest·target·상태와 8개 alias group | source classification 미분류 anchor 0; acceptance는 UNVERIFIED |
| `binance-official-assumptions.md` | 공식 Spot·Spot Testnet 전제와 공개 allowlist | P0-02 후보 |
| `architecture-and-boundaries.md` | monorepo, process/DB/network/secret 경계, capability-zero 증명 | P0-04·05·10 후보 |
| `data-model-and-retention.md` | ERD, owner/FK/UNIQUE/immutable, 보존·pin·purge | P0-06 후보; policy approved, final review pending |
| `api-and-event-contracts.md` | API/event v1, idempotency/OCC, producer-consumer, 승인 순서 | P0-07 후보; policy approved, final review pending |
| `financial-and-order-invariants.md` | Decimal/FIFO/fee/PnL, LIMIT lifecycle, ledger·atomicity·Risk | P0-08 후보; policy approved, final review pending |
| `verification-strategy.md` | 43개 candidate 고정 RED scenario와 evidence contract | P0-09·10 후보; 실행은 후속 Phase |
| `local-ci-contract.md` | toolchain, 루트 명령, CI graph, Phase별 활성화 | P0-11 후보 |
| 이 파일과 루트 운영 규칙 | Phase 1 정확 범위와 완료 보고 | P0-12 후보 |

### 3.2 하네스

- Codex 전문 역할, phase/safety preflight와 오케스트레이터 단일 persistence owner를 정렬했다.
- 일반 대조 검토는 읽기 전용 `woozoo-codex-cross-reviewer`로 분리하되 같은 엔진 내부 검토임을 명시했다. 이는 외부 독립 리뷰나 safety QA가 아니다.
- Claude 일반 검토를 제거했다. 외부 review gate에는 프로젝트 selector가 허용한 `agy|gemini|re-review` source만 인정하고 다른 source를 validator가 fail-closed한다.
- Phase 0에서는 애플리케이션 source, 실행 가능한 test/fixture/scaffold, migration, Paper Broker와 주문 gateway를 만들지 않았다.

## 4. Phase 1 목적

Phase 1은 **실행 골격만** 만든다. Python/TypeScript workspace, FastAPI와 Next.js health/config shell, Postgres·Redis·Docker Compose, 최소 platform contracts, migration framework, 관측·redaction, root quality/CI 진입점을 clean checkout에서 재현 가능하게 한다. 시장 수집, Evidence, Paper 주문, Risk, AI, Trading Room 기능은 구현하지 않는다.

## 5. Phase 1 허용 파일·디렉터리

아래만 새 제품 경로로 허용한다. 정확한 파일명은 Phase 1 RED contract에서 먼저 고정하되 다른 Phase의 domain placeholder를 만들 수 없다.

```text
/
├─ package.json
├─ pnpm-workspace.yaml
├─ pnpm-lock.yaml
├─ .node-version
├─ pyproject.toml
├─ uv.lock
├─ .python-version
├─ .env.example
├─ compose.yaml
├─ .github/workflows/ci.yml            # Phase-aware P1 required jobs only
├─ apps/trading-room-web/              # Next.js health/config shell only
├─ services/control-api/               # FastAPI health/config ingress only
├─ services/outbox-relay/              # platform outbox delivery skeleton
├─ packages/contracts/                 # health/config/error/event envelope v1 only
├─ packages/python/platform-core/      # UTC, ID, canonical hash, error, DB primitives
├─ packages/typescript/contract-bindings/ # health/config generated bindings only
├─ db/migrations/                      # Phase 1 platform/auth/receipt/outbox schema only
├─ db/seeds/                           # secretless local reference data only
├─ infra/compose/
├─ infra/network-policy/
├─ infra/observability/
├─ tests/unit/                         # Phase 1 config/platform primitive only
├─ tests/contract/                     # Phase 1 RED→GREEN only
├─ tests/safety/                       # mode/capability/secret boundary only
└─ tests/integration/                  # compose/health/migration/Redis-loss only
```

`compose.yaml`은 `infra/compose/`의 pinned 로컬 정의를 정본 root command로 묶는 진입점이다. exact Python/Node/pnpm/Postgres/Redis image versions는 Phase 1 착수 시 공식 지원 상태를 확인해 pin하고 lockfile에 고정한다.

## 6. Phase 1 명시적 제외와 구조적 zero gate

다음 경로·개념은 만들지 않는다.

- `market-data-worker`, market-data transport/schema/fixture와 Binance URL: Phase 2
- `evidence-worker`, feature·Evidence domain: Phase 3
- `paper-engine`, order/fill/position/ledger domain·route·schema: Phase 4
- `risk-engine`, Risk/approval/authorization/Kill domain·route·schema: Phase 5
- `agent-orchestrator`, model provider adapter·prompt·Proposal: Phase 6
- Trading Room domain 화면, 승인·Kill·Paper control binding과 Playwright E2E: Phase 7
- Testnet gateway, credential, signed/private request, account/user-data, Testnet URL/mode/schema/config: Phase 8 별도 승인 후
- Mainnet private/live, withdrawal, Futures, Margin, leverage, short와 범용 `exchange-client|broker-adapter|live-mode`: 모든 Release 1 Phase에서 금지

Phase 1의 `control-api`, web shell과 contracts는 health/config/error 이외 미래 business route나 enum을 미리 표현하지 않는다. 빈 service directory나 성공만 반환하는 future test script도 금지한다.

## 7. Phase 1 RED→GREEN 실행 순서

1. `phase-state.json`, 동결 Phase 0 manifest digest와 사용자 승인을 preflight한다.
2. `codex/phase-1-platform-skeleton` 브랜치를 만들고 root toolchain/version/lock 계약을 RED로 고정한다.
3. missing/unknown `TRADING_MODE`, `live|testnet`, private/Testnet vocabulary, secret leak를 거절하는 safety tests를 먼저 만든다.
4. health/config OpenAPI·event/error envelope와 generated binding contract tests를 RED로 만든다.
5. Postgres·Redis Compose, reversible Phase 1 migration과 FastAPI/Next.js health shell을 최소 구현한다.
6. Redis loss가 권위 상태를 만들지 않고, Postgres/필수 config 장애가 health/startup을 fail-closed하는 integration test를 통과시킨다.
7. root quality commands, CI job graph, artifact paths와 docs를 연결한 뒤 diff self-review, Codex cross-review, 역할 분리 safety QA를 수행한다.
8. Phase 1 evidence digest와 사용자 승인이 있기 전 Phase 2 공개-data collector를 시작하지 않는다.

## 8. Phase 1 완료 기준

| ID | must-pass | evidence 후보 |
|---|---|---|
| P1-01 | clean checkout에서 exact locked `pnpm bootstrap` 성공 | lock/install log, generated-diff 0 |
| P1-02 | `pnpm env:init`이 secretless local env를 만들고 기존 파일을 덮어쓰지 않음 | env schema test |
| P1-03 | `TRADING_MODE=paper`만 시작; 누락·빈 값·미지·`live|testnet`은 non-zero | startup matrix |
| P1-04 | FastAPI와 Next.js health/config shell, Postgres·Redis Compose가 기동하고 secret을 반환하지 않음 | compose/health integration log |
| P1-05 | Postgres는 durable authority, Redis는 제거·재시작 가능한 비권위 cache/hint | Redis-loss scenario |
| P1-06 | OpenAPI/JSON Schema/event/error v1과 generated binding이 drift 없이 호환 | contract manifest |
| P1-07 | Phase 1 migration upgrade/downgrade/re-upgrade가 deterministic하고 seed에 비밀 없음 | migration smoke digest |
| P1-08 | log/trace/error/config 출력에서 credential·auth header·provider secret 패턴 0 | secret/redaction scan |
| P1-09 | Testnet/private/order/account/user-data와 금지 거래 capability가 dependency/config/schema/DI/network에서 0 | capability-zero scan |
| P1-10 | `pnpm lint`, `pnpm typecheck`, `pnpm test:unit`, `pnpm test:contracts`, `pnpm test:safety`, `pnpm test:integration`, `pnpm build`, `pnpm ci` 전건 PASS; 빈 성공 script 0 | CI artifacts와 고정 분모 |
| P1-11 | 변경 코드·계약·문서·scenario manifest·working history가 같은 revision을 가리킴 | docs-evidence check |
| P1-12 | diff review, 역할 분리 safety QA, review gate, Conventional Commit, `main` 대상 Draft PR와 digest-bound 사용자 승인 | Phase 1 evidence manifest |

하나라도 `FAIL|UNVERIFIED`, skip, artifact 누락이면 Phase 1은 완료가 아니며 Phase 2로 넘어가지 않는다.

## 9. 현재 실행·검증 기록

| 검사 | 결과 |
|---|---|
| phase-state preflight | `current_phase=0`, approval=`not_requested`, allowed scope=문서·하네스·설계·test design |
| 제품 코드·migration·실행 가능한 test/fixture/scaffold | 생성 0 |
| P0 검증 scenario | 43개 candidate 문서 계약; 실제 GREEN/failure injection은 Phase별 구현 전이므로 `NOT RUN` |
| 금융 고정 oracle 재계산 | realized `3.916000`, unrealized `1.980000`, total `5.896000`; 구현 검증은 `NOT RUN` |
| 하네스 source validator | 허용 external source 통과, Codex/Claude 등 비허용 source 거절 |
| 내부 Codex cross-review | same-engine 내부 evidence로만 기록; 외부 독립성 없음 |
| 외부 review | 기존 frozen MVP/harness artifact에 agy 기록 존재; 최종 Phase 0 package 동결 뒤 재검토 필요 |
| factory scorecard | `jq` 부재로 `eval-unavailable`; validator 원본 결과는 별도 보존 |
| Git gate | DP-D11 및 P0-GIT-01로 Git authority 승인됨; final package freeze 뒤 stage/commit/push/Draft PR evidence를 기록 |

Phase 1 제품 명령은 아직 존재하지 않으므로 실행하지 않았으며 성공으로 간주하지 않는다.
Phase 0의 실행 게이트는 필수 문서 링크·SHA-256·verdict, 금지 제품 경로 0, branch, 하네스 validator와 review evidence를 검사한다. `pnpm ci`는 root 제품 명령이 생기는 Phase 1부터 필수이며 Phase 0을 위해 빈 script나 product scaffold를 만들지 않는다.

## 10. 사용자 위임 승인 기록

DP-D01~DP-D10과 DP-D11 Git authority는 `phase-0-approval-record.md`에 사용자의 포괄 위임으로 기록됐다. 아래 정책 목록은 새 선택을 요청하는 질문이 아니라 frozen package가 이 승인 범위를 벗어나지 않는지 확인하는 checklist다.

Phase 0을 닫으려면 frozen package가 다음 승인 정책 묶음을 정확히 따르는지 판정해야 한다.

1. **릴리스 컷:** Phase 0~7을 Paper MVP, Phase 8을 Spot Testnet Validation, Phase 9를 Readiness로 분리한다.
2. **이월 범위:** 공개 파생 telemetry와 뉴스·소셜은 MVP에서 제외하고 Release 1.1에서 출처/provenance 계약부터 설계한다.
3. **아키텍처:** 목표 process split, 로컬 stable actor ID와 인증 방식 후보를 채택한다.
4. **데이터:** retention R0~R6, pin/purge, UUIDv7/UTC/Decimal/immutable enforcement 후보를 채택한다.
5. **금융·Risk:** FIFO, fee asset/rate, Decimal scale/rounding, 보수 fill/participation, valuation, Risk limit/precedence, approval TTL, reconciliation→Kill 정책을 확정한다.
6. **계약:** `Risk→Approval→PaperExecutionAuthorization`, approval identity nonce와 execution one-time authorization nonce의 분리, deprecation 기간 후보를 확정한다.
7. **Git 외부 변경:** 확정본을 stage하고 Conventional Commit, push와 `main` 대상 Draft PR로 만드는 작업을 별도 승인한다.

정책 값은 선택지가 많을 경우 별도 decision sheet로 동결한다. 승인되지 않은 숫자를 코드 default로 추론하지 않는다.

## 11. 알려진 한계와 현재 No-Go

- P0-03과 정책 숫자는 승인됐지만 final safety QA, Codex cross-review, external review/evidence digest가 완료되지 않아 P0 verdict는 아직 `UNVERIFIED`다.
- 전체 Phase 0 package의 최종 safety QA·Codex cross-review·외부 review는 문서 동결 뒤 다시 수행해야 한다.
- 저장소가 unborn/all-untracked이고 승인된 Conventional Commit·Draft PR이 없어 Git 게이트가 닫히지 않았다.
- `jq`가 없어 factory scorecard를 실행하지 못했지만 이를 review PASS로 대체하지 않는다.
- Phase 1~7 실행 코드와 MVP-01~12 GREEN evidence는 아직 없으며 현재 제품 상태는 `NO-GO`다.

## 다음 단계 참조

1. 사용자 결정 1~7을 반영해 P0 문서를 동결한다.
2. 전체 package에 역할 분리 safety QA, Codex same-engine 교차검토와 외부 review를 수행하고 confirmed issue를 수렴한다.
3. P0-01~12의 정렬된 `path + SHA-256 + verdict` manifest를 만들고 Git 게이트를 닫는다.
4. 동결 manifest SHA-256에 결속된 Phase 1 전환 승인을 요청한다.
5. 승인 전에는 이 문서의 Phase 1 tree를 생성하지 않는다.
