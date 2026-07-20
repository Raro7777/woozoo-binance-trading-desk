# Woozoo MVP 계획 및 합격 기준

- 문서 상태: `POLICY_APPROVED — Phase 0 acceptance evidence pending`
- 현재 제품 Phase: `0`
- 현재 구현 상태: `NOT_STARTED / UNVERIFIED`
- 현재 Go/No-Go: `NO-GO — Phase 0 acceptance evidence 미완료`
- 근거: `Woozoo_Binance_Trading_Desk_Codex_Handoff-5.docx`, Phase 계약, 안전 정책, 공개 데이터 계약, 금융 불변조건
- 이 문서는 범위와 향후 합격 계약을 정의하며 Phase 1 코드 착수를 승인하지 않는다.

## 1. 권장 MVP 경계

### 한 문장 정의

운영자가 BTCUSDT·ETHUSDT의 무인증 Binance Spot 공개 데이터로 시점 고정 Evidence를 만들고, AI의 근거 기반 BUY/SELL/HOLD 제안을 결정론적 Risk Engine과 사람의 Paper 승인을 거쳐 내부 Paper Broker·복식 원장에 단 한 번 반영하며, 전 과정을 GUI에서 재현·감사할 수 있는 로컬 연구 플랫폼이다.

### 릴리스 구분

| 구분 | 제품 Phase | 의미 |
|---|---:|---|
| **MVP / Release 1a** | 0~7 | 내부 Paper Trading 폐루프와 단일 운영자 GUI |
| Testnet Validation | 8 | 별도 최소 권한 Spot Testnet Gateway, 사람 승인, timeout·reconciliation 검증 |
| Release 1 Readiness | 9 | 장기 모의운영, benchmark, 장애 훈련, 보안·운영 증거와 원본 요구 추적 완료 |

**권고:** MVP 완료선은 Phase 7이다. 원본 Release 1의 Testnet 요구는 삭제하지 않고 Phase 8의 별도 확장 게이트로 남긴다. Phase 8은 최초로 거래소 credential, signed private 요청, user data와 외부 주문의 불명확 상태를 도입하므로 MVP와 섞지 않는다. 그 밖의 원본 요구도 아래 추적표에서 MVP, 후속 gate 또는 명시적 사용자 scope 변경 중 하나에 반드시 연결한다.

### MVP 성공의 의미

- 수익률이나 시장 예측 정확도를 증명하는 것이 아니다.
- 데이터 시점 무결성, 금융 계산 정확성, 결정 재현성, 안전 차단과 감사 가능성을 증명한다.
- BUY를 만들어내는 것이 목표가 아니다. 근거가 부족하거나 시스템이 불건전하면 일관되게 `HOLD` 또는 거절하는 것도 성공이다.

## 2. 대상 사용자와 핵심 작업

초기 사용자는 로컬 환경의 단일 운영자다.

1. BTCUSDT·ETHUSDT의 시장·데이터 품질 상태를 확인한다.
2. 고정된 Evidence를 바탕으로 AI 분석·토론과 TradeProposal을 검토한다.
3. 결정론적 Risk 결과와 거절 사유를 확인한다.
4. 허용된 Proposal만 Paper 전용으로 승인하거나 거절한다.
5. 실행 직전 Paper 실행 권한이 승인 hash·현재 안전 상태를 재검증한 뒤 주문·부분 체결·수수료·포트폴리오·원장을 확인한다.
6. Evidence부터 원장까지 전체 판단 경로를 감사하고 장애 시 Kill Switch로 차단한다.

## 3. MVP 범위

### 반드시 포함

| 영역 | 최소 범위 |
|---|---|
| 심볼·시장 | Binance Spot 공개 데이터, `BTCUSDT`, `ETHUSDT`, Long 또는 Cash |
| 공개 데이터 | 무인증 trade, book ticker 또는 승인된 최소 호가, kline allowlist |
| 복원력 | reconnect, bounded retry/backoff, rate-limit, malformed payload, dedupe, out-of-order/gap/stale 감지, backpressure, append-only raw-store 실패, 결정적 replay |
| Evidence | `as_of`, `knowledge_cutoff`, watermark, collector state, 원본 ID·hash와 품질 상태에 결속된 불변 snapshot. 생성 후 gap repair나 late arrival로 수정하지 않음 |
| AI 분석 | 단일 `agent-orchestrator` process 안에서 시장/기술, 체결흐름, Bull/Bear, Trader, Portfolio, Audit 역할의 입력·출력 schema를 논리적으로 분리한 최소 수직 절편 |
| AI 출력 | Evidence ID, 불확실성, 무효화 조건, 모델·프롬프트·워크플로 버전을 가진 구조화 보고서와 BUY/SELL/HOLD 후보안 |
| Risk Decision | 승인 전에 Proposal·portfolio·data quality·정책 버전·노출·손실·낙폭·중복·Kill Switch를 평가하는 결정론적 판정. 승인 존재를 요구하지 않음 |
| Paper 실행 권한 | 승인 후 `PaperExecutionAuthorization`이 Proposal/Risk hash, actor, TTL, 만료·철회와 현재 Kill Switch·data/ledger 상태를 다시 검증. 외부 주문 승인이 아니라 내부 Paper workflow 검증용 |
| Paper Broker | LIMIT, 부분 체결, 수수료, 취소, Long 축소 SELL, 포지션·PnL |
| 금융 상태 | Decimal, 결정된 cost basis·fee·valuation·PnL, commodity별 복식 원장, 비음수 잔고. Ledger entry는 immutable이고 correction은 reversal+replacement만 허용. 거래·fee commodity마다 clearing/exchange account로 각각 균형. Idempotency/Client Order/event key와 DB unique constraint로 inbox·domain state·ledger·outbox를 동일 트랜잭션에 commit하고 replay·대조 |
| GUI | 시장 상태, Evidence/분석, Proposal/Risk/Paper 승인, 포트폴리오/원장, 감사·운영 화면 |
| 운영·비밀 | correlation ID, structured log, heartbeat, alert, 수동 해제 Kill Switch. 프런트엔드는 비밀을 받지 않고, model-provider credential은 별도 최소 권한 secret path에서 provider adapter만 읽으며 prompt/tool/log/trace/error/fixture에 노출하지 않음 |
| 기본 모드 | 명시적 `TRADING_MODE=paper`; 누락·알 수 없는 값은 시작 실패 |

### MVP에서 명시적으로 제외

- Phase 8 Spot Testnet Gateway, signed/private endpoint, user-data stream과 거래소 credential.
- Mainnet private/account, 실거래, 출금, Futures, Margin, leverage, short.
- AI의 주문·취소·계좌 도구, 수량·공식 잔고·최종 Risk 결정, 정책 변경 권한.
- DP-D02로 MVP에서 이월된 공개 파생시장 telemetry와 뉴스·소셜 collector. Release 1.1의 별도 source/provenance 계약 승인 전에는 넣지 않는다.
- BTCUSDT·ETHUSDT 외 심볼, 다른 거래소, full-depth order book, 시장가·stop·고급 주문.
- 10개 역할의 개별 모델화, 멀티모델 라우팅, 자동 전략 최적화.
- 거래소급 matching engine, 다중 사용자 SaaS, 복잡한 RBAC, HA·24/7 운영, 네이티브 모바일 앱.
- 수익 보장, alpha 증명, 실거래 준비 완료 주장.

### 원본 Release 1 요구 추적

원본 요구는 MVP 제외만으로 삭제되지 않는다. 아래 Release 1.1 이월 행은 DP-D02로 결정됐으며, 그 source/provenance 계약과 별도 scope 승인이 있기 전에는 MVP에 다시 넣지 않는다.

| 원본 Release 1 요구 | MVP/후속 연결 | 완료·변경 게이트 |
|---|---|---|
| BTC/ETH Spot 공개 시세·체결·캔들·최소 호가 | MVP Phase 2~3 | MVP-02·03 |
| 시장국면·기술·체결흐름 분석 | MVP Phase 6 | MVP-04 |
| Bull/Bear·TradeProposal | MVP Phase 6 | MVP-04·05 |
| Risk·Kill Switch | MVP Phase 5 | MVP-05·06 |
| Paper Broker·포트폴리오·복식 원장 | MVP Phase 4 | MVP-07·08 |
| GUI Trading Room | MVP Phase 7 | MVP-09·10 |
| Spot Testnet 주문 생명주기·대조 | 후속 Phase 8 | Phase 8 전용 안전 gate |
| 장기 운영·성과 검증 | 후속 Phase 9 | 합의된 SLO·soak·장애 훈련 evidence |
| 파생시장 문맥 분석 | **DP-D02 승인: MVP 제외, Release 1.1 이월** | 전용 무인증 allowlist와 구현 Phase/acceptance를 별도 승인할 때만 추가 |
| 뉴스·거시 분석 | **DP-D02 승인: MVP 제외, Release 1.1 이월** | 출처·관측시각·prompt-injection 경계를 별도 승인할 때만 추가 |
| 전체 감사·승인·모델/프롬프트 버전 | MVP Phase 1~7, 후속까지 지속 | MVP-06·10과 각 Phase audit evidence |

추적표에서 `MVP ID`, `후속 Phase gate`, `사용자 승인된 scope 변경` 중 어느 것도 없는 orphan 요구는 0건이어야 한다.

## 4. 실행 계획과 Phase 종료 게이트

각 Phase는 이전 Phase의 acceptance evidence digest와 사용자 전환 승인이 있어야 시작한다. 모든 Phase에서 테스트·문서·diff 리뷰·safety QA, 중대 변경의 외부 리뷰 완료 또는 `external-review-unavailable`, 미해결 안전 차단 0건이 필요하다.

**Phase 0 Git 차단:** 작업 브랜치는 정확히 `codex/phase-0-design`이어야 하고 `main` 직접 수정은 금지한다. 검증된 diff 자체 리뷰 뒤 승인된 Conventional Commit과 `main` 대상 독립 Draft PR 증거가 모두 있어야 Phase 0을 닫을 수 있다. `branch=main`, 승인 commit 부재, Draft PR 부재 중 하나라도 참이면 `NO-GO`이며 전환 승인을 요청하지 않는다. 이후 각 Phase도 `codex/phase-{n}-{slug}` 별도 브랜치와 독립 Draft PR을 사용한다.

**Phase 1~7 공통 비회귀 차단:** 각 Phase의 종료 때 Testnet gateway/package/schema/DI/config/credential 입력, signed/private capability와 user-data stream이 모두 0인지 검사한다. 이 검사를 Phase 7까지 미루지 않는다. Mainnet·출금·Futures·Margin·leverage·short와 AI/browser 주문 capability도 같은 방식으로 매 Phase 0건이어야 한다.

| Phase | 목적과 핵심 산출물 | 종료 게이트 |
|---:|---|---|
| **0 — 설계·검증** | MVP 컷, 모노레포, 서비스/신뢰/비밀 경계, ERD·보존, API/event, 금융 불변식, RED 테스트·CI 설계 | P0-01~P0-12 전부 `PASS`+경로+SHA-256, `UNVERIFIED` 0건, 금지 코드 delta 0, safety QA PASS, evidence digest와 사용자 승인 |
| **1 — 실행 골격** | Python/TS workspace, FastAPI, Next.js, Postgres, Redis, Docker, CI와 typed contract 기반 | clean checkout 로컬 기동, health 및 품질 명령 전부 통과, 누락/미지 mode와 금지 private capability 설정은 시작 실패 |
| **2 — 공개 데이터** | BTC/ETH trade·book ticker·kline 수집, raw/normalized store, 품질·재연결·replay | 인증 endpoint 0, allowlist 밖 호출 0, rate-limit/429·malformed payload·raw-store 실패·duplicate/out-of-order/gap/reconnect/backpressure 시나리오 전부 통과, 동일 입력 normalization digest 동일 |
| **3 — Feature·Evidence** | 1m/5m/1h/4h candle·feature, `as_of` 고정 Evidence와 provenance | 모든 Evidence item이 raw ID로 역추적, digest에 watermark·quality·collector state 포함, 경계 직전/동일/직후·late arrival 테스트 통과, 생성된 snapshot 변경 0, stale/invalid 데이터의 Proposal 입력 0 |
| **4 — Paper Broker·원장** | LIMIT lifecycle, 부분 체결·수수료·취소, Long/Cash position, cost basis·PnL·복식 원장 | commodity별 차변-대변 차이 0, 승인된 fee/cost-basis/valuation/rounding oracle과 PnL 일치, 음수 잔고·초과 체결·중복 posting 0, commit-failure에서도 inbox/state/ledger/outbox 원자성 유지, duplicate/restart replay digest 동일 |
| **5 — Risk Engine** | 승인 전 버전 정책, Proposal·portfolio·data quality 결속, 노출·손실·낙폭·spread·예상 slippage·stale·중복·Kill Switch와 reason code | 승인 없이 Risk 평가 가능, 동일 전체 `risk_input_digest`의 decision hash 동일, 모든 강제 거절 반례 통과, Kill Switch 자동 해제 0 |
| **6 — AI 조직** | 단일 process의 Mock LLM 우선 role graph, 분리 schema의 Evidence 보고서, Bull/Bear, TradeProposal, Audit | schema-invalid·orphan Evidence ID 0, 실패·누락은 HOLD, AI tool/env의 주문·계좌·거래소 secret capability 0 |
| **7 — GUI Trading Room** | 단일 운영자 Paper 폐루프 UI와 감사·운영 화면 | desktop/mobile web viewport에서 분석→Proposal→Risk→Paper 승인/거절→체결/취소→포트폴리오·원장·감사 E2E 통과, serious/critical 접근성 결함 0, Testnet 제어·schema·credential 입력 0 |
| **8 — Testnet 확장** | 별도 package/process/env의 최소 권한 Spot Testnet Gateway, 정확한 preview·TTL·nonce 승인, timeout unknown 상태와 대조 | 기본 OFF, Spot Testnet host/path/method/auth/capability allowlist, browser·AI 직접 inbound 0, Gateway inbound는 인증된 결정론적 execution service의 승인된 command schema만 허용, Paper와 별도 승인, Proposal/Risk/policy/account/environment/symbol/side/quantity/price-or-rule/TIF/TTL/nonce·인증된 approver ID·`approved_at`·`expires_at`·revocation 상태 결속, 내용 변경·사용·만료·철회 시 기존 승인 무효화, command 생성 직전에 승인 유효성·현재 Kill Switch·데이터 freshness/integrity·reconciliation health를 재검증하고 하나라도 불건전하면 command·주문 0건, 익명·만료·철회·변경 승인·비인증 inbound·승인 후 상태 악화의 negative E2E, nonce는 단일 command/Client Order ID에만 사용, Mainnet·출금·Futures·Margin·leverage·short capability 0, 주문/취소/조회/user data/reconciliation negative E2E. **MVP 이후** |
| **9 — 운영 검증** | 장기 Paper/Testnet 관찰, benchmark, fee/slippage, 장애 훈련, 보안 검토 | 합의된 soak 기간·SLO·복구 훈련 증거. **MVP 이후** |

### Phase 0 acceptance index

| ID | 필수 산출·검증 |
|---|---|
| P0-01 | 저장소와 원본 핸드오프 전체 감사 |
| P0-02 | 공식 Binance Spot·Spot Testnet 문서로 변동 가능한 전제 검증 |
| P0-03 | 모순·누락·위험 가정·미결정 질문과 파생 telemetry/news scope 결정 |
| P0-04 | 최종 모노레포 구조 |
| P0-05 | 아키텍처, 서비스 책임, 신뢰·비밀 경계 |
| P0-06 | ERD와 데이터 보존 계획 |
| P0-07 | 버전이 있는 API·이벤트 계약 |
| P0-08 | 회계·cost basis·멱등성·주문 불변조건 |
| P0-09 | replay·property·failure-injection·Mainnet 차단 테스트 전략 |
| P0-10 | Mainnet private 주문 접근이 구조적으로 불가능한 설계 |
| P0-11 | 로컬 개발·CI 명령 설계 |
| P0-12 | 관련 문서·루트 운영 규칙·정확한 Phase 1 범위·완료 보고서 갱신 |

각 항목은 `PASS|FAIL|UNVERIFIED`, 증거 경로와 SHA-256을 가진다. 하나라도 누락되거나 `UNVERIFIED`면 Phase 1 전환 승인을 요청하지 않는다.

### Critical path

```text
Phase 0 계약
  → Phase 1 실행 골격
  → Phase 2 공개 데이터
  → Phase 3 Evidence
  → Phase 4 Paper Broker·원장
  → Phase 5 Risk
  → Phase 6 AI Proposal
  → Phase 7 GUI·E2E = MVP
  → Phase 8 Testnet
  → Phase 9 운영 검증
```

## 5. 핵심 사용자 여정 Acceptance

### UJ-01 시장·데이터 상태 확인

- **Given** `TRADING_MODE=paper`와 BTC/ETH 공개 endpoint allowlist가 적용돼 있다.
- **When** 운영자가 Trading Room을 연다.
- **Then** 심볼별 가격, `event_time`, `received_at`, freshness, quality, watermark와 연결 상태를 확인할 수 있다.
- `degraded`는 Proposal 근거에서 제외하고, raw/normalized schema 오류·필수 데이터 누락·`stale/invalid`는 신규 분석·Proposal·Paper 주문을 차단하며 reason code를 보인다.

### UJ-02 Evidence 기반 분석

- **Given** 고정된 `as_of`와 불변 Evidence snapshot이 있다.
- **When** 운영자가 분석을 실행하거나 완료된 run을 연다.
- **Then** 모든 보고서와 Proposal에서 Evidence ID, 품질, 불확실성, 무효화 조건, 모델·프롬프트·workflow 버전을 역추적할 수 있다.
- Evidence 누락, schema 오류, 필수 보고서 실패의 결과는 `HOLD`다.

### UJ-03 Risk, Paper 승인과 실행 권한

- **Given** Proposal, 포트폴리오 snapshot, 데이터 상태와 Risk policy version이 고정돼 있다.
- **When** Risk Engine이 평가한다.
- **Then** 승인이 아직 없어도 동일 입력은 동일 verdict, reason code와 decision hash를 만든다.
- denied/error/Kill Switch에서는 승인 동작이 불가능하다.
- allowed인 경우 운영자가 Paper 전용 승인 또는 거절을 남기고 actor, 시각, Proposal/Risk hash를 감사할 수 있다.
- 실행 직전 `PaperExecutionAuthorization`이 hash mismatch, 만료·철회와 현재 Kill Switch·data/ledger 상태를 재검증한다. 하나라도 실패하면 Paper 실행은 0건이다.

### UJ-04 Paper 체결·원장

- **Given** 승인된 Paper LIMIT 주문과 초기 Cash/Position이 있다.
- **When** 주문이 부분 체결·취소되거나 이벤트가 중복·재처리된다.
- **Then** `filled_quantity = sum(unique fills)`이고 주문 수량 초과, 음수 잔고와 중복 분개가 없다.
- 모든 금융 값은 Decimal이며 commodity별 원장 차변·대변 차이가 0이고, 재시작 replay 후 portfolio/ledger digest가 동일하다.

### UJ-05 감사·장애 대응

- **Given** 전체 workflow에 correlation ID가 있다.
- **When** 운영자가 disconnect, gap, queue overflow, stale, DB 중단 또는 Kill Switch를 주입한다.
- **Then** Evidence→Agent→Proposal→Risk→Paper Approval→Order→Fill→Ledger를 재구성할 수 있다.
- 무음 drop 없이 품질 상태가 fail-closed로 전환되고 신규 Paper 주문은 차단된다. Kill Switch가 켜지면 열려 있는 Paper 주문은 감사 이벤트와 함께 결정적으로 취소되며 자동 해제되지 않는다. Phase 8 외부 주문의 취소 정책은 별도 gateway 계약으로 다룬다.

## 6. MVP Must-pass 기준

모든 항목은 필수다. 하나라도 `FAIL` 또는 `UNVERIFIED`면 MVP가 아니다. 각 acceptance는 필수 scenario ID, fixture/입력, 기대 결과, 실행 명령과 증거 경로가 있는 manifest에 등록한다. `100%`는 manifest의 필수 ID 전건 실행·PASS를 뜻하며 미등록·미실행 ID가 하나라도 있으면 실패한다.

| ID | Must-pass acceptance | 최소 반례·검증 | 필수 증거 |
|---|---|---|---|
| MVP-01 | clean checkout에서 명시적 paper 구성으로 로컬 스택 기동; 누락·미지 mode는 실패 | invalid config, 금지 private URL/env를 주입하는 Safety test | CI·startup log, config schema |
| MVP-02 | BTC/ETH 공개 allowlist만 사용하고 인증·signature·listen key 요구 경로 0. raw/normalized schema 오류나 필수 history 누락은 invalid로 전환 | 허용 밖 symbol/path, rate-limit/429, malformed payload, 필수 field/history 누락, duplicate, out-of-order, gap, reconnect, queue overflow, append-only raw-store 실패 | endpoint 승인 기록, schema contract, Integration/Replay/Failure-injection 결과 |
| MVP-03 | Evidence의 모든 item이 `event_time <= as_of`와 `received_at <= knowledge_cutoff`를 만족하고 raw ID로 역추적; digest가 watermark·quality·collector state를 포함하며 생성 후 불변. schema 오류·필수 Evidence 누락은 HOLD와 실행 차단 | 경계 직전/동일/직후, late arrival, future-contaminated event, missing required Evidence, gap repair 뒤 기존 snapshot 수정 시도 | snapshot digest, provenance query, Property/Replay test |
| MVP-04 | **수락·저장된** AI 보고서/Proposal은 manifest 전건 schema-valid·유효 Evidence ID 포함; malformed·실패·필수 누락은 저장된 Proposal이 아니라 HOLD | orphan ID, prompt injection, timeout, malformed model output | contract test, tool registry, audit report와 manifest coverage |
| MVP-05 | 승인 존재를 요구하지 않고 같은 Proposal·portfolio·quality·order preview·policy/calculator·Kill·reconciliation·clock·duplicate/exposure snapshot은 동일 Risk input/decision hash 생성. spread·예상 slippage를 포함한 한도 초과는 거절하고 Kill Switch는 신규 명령을 막고 열린 Paper 주문을 감사 취소 | stale, future data, spread/slippage threshold, exposure/loss/drawdown, duplicate, Kill Switch, open Paper order, ledger imbalance | Unit/Property test와 reason code·open-order matrix |
| MVP-06 | 승인 UI가 서버 canonical Paper order preview 전체를 그대로 표시하고 표시 payload hash=Risk/제출 hash일 때만 Paper 승인; 이후 실행 권한이 Proposal/Risk hash·actor·시각·상태와 현재 Kill Switch·data/ledger를 재검증하고 변경·만료·철회 후 실행 0 | stale/misbound preview, 표시와 제출 hash 불일치, 무승인 Risk 평가, 승인 뒤 Proposal/Risk 수정, expired/revoked approval 재사용 | approval-view contract, preview/hash browser oracle, approval audit, 순서 contract와 E2E negative test |
| MVP-07 | LIMIT·부분 체결·수수료·취소·Long 축소 SELL을 결정적으로 처리하고 command Idempotency Key·Client Order ID·event key를 unique로 보호 | duplicate fill, fill 합 초과, terminal 역전, 재시작·응답 유실, ledger 후 dedupe 전 crash | order lifecycle·Replay·commit Failure-injection 결과 |
| MVP-08 | Decimal, 승인된 cost-basis·fee asset·valuation source/as_of·rounding으로 cash/position/realized·unrealized PnL oracle과 일치; 거래·fee commodity마다 clearing/exchange account를 사용해 commodity별 원장 균형·비음수·중복 posting 0. entry는 immutable, correction은 reversal+replacement. inbox/state/ledger/outbox는 동일 DB transaction | float 입력, fee 누락 PnL, BTC/USDT 원시 수량 상계, 복수 fee asset, UPDATE/DELETE correction, reversal, duplicate event, commit 중간 실패 | Property test, 고정 PnL oracle, immutable-ledger query, transaction/restart digest |
| MVP-09 | 다섯 최소 UI 영역에서 UJ-01~05 완료; 차단 사유가 행동과 함께 표시 | loading/error/stale/denied/partial/revoked 상태 누락 | Playwright E2E, 접근성 보고서, 화면 상태 매트릭스 |
| MVP-10 | manifest의 모든 상태변경 event에서 correlation ID·주요 domain ID·schema version 감사 연결률 100%, 비밀 패턴 0 | 서비스 경계 전파 누락, 필수 event 미등록, log/prompt/tool/trace/error/fixture에 credential 삽입 | 분자·분모가 고정된 trace/audit query, secret scan, restart drill |
| MVP-11 | recorded input을 중복·재시작 replay해 normalized/Evidence/portfolio/ledger의 결정적 digest 동일; commit failure에서도 inbox/state/ledger/outbox가 함께 반영 또는 함께 rollback | 순서 섞기, process restart, DB/Redis 중단, transaction 중간 crash | Replay·Failure injection 결과와 digest |
| MVP-12 | Mainnet private, live, 출금, Futures, Margin, leverage, short, Testnet gateway capability 0. 프런트엔드 비밀 수신 0, model-provider credential은 별도 최소권한 path에서 provider adapter만 접근 | 금지 host/path/method/config/schema/tool, AI tool registry의 account/order, provider key가 prompt/tool/log/trace/error/fixture에 노출 | static capability/secret scan, startup rejection, egress·secret-boundary test |

## 7. 최소 UI와 필수 상태

| 화면 | 최소 정보·행동 |
|---|---|
| Trading Room | BTC/ETH 가격·캔들, freshness/quality, `as_of`, watermark, 연결 상태 |
| Analysis | Evidence, agent 진행, Bull/Bear 근거, 불확실성, 무효화 조건, HOLD fallback |
| Proposal & Risk | Proposal diff, Risk verdict/reason, 정책 버전, Paper 승인·거절 |
| Paper Portfolio | Cash/position/PnL, 주문·부분 체결·수수료, 원장·대조 상태 |
| Audit & Ops | correlation timeline, heartbeat, alert, Kill Switch와 수동 복구 기록 |

필수 상태 집합:

- Data: `loading / healthy / degraded / stale / invalid / reconnecting`
- Agent run: `idle / running / partial / succeeded / HOLD / failed`
- Risk: `allowed / denied / error`
- Paper approval: `pending / approved / rejected / expired / revoked`
- Paper execution authorization: `not-issued / issued / blocked / invalidated / consumed`
- Paper command receipt: `accepted / rejected`와 stable reason code; guard 실패는 order aggregate를 만들지 않음
- Paper order: `open / partially-filled / filled / cancelled`

Phase 8 전에는 Testnet 주문 버튼·gateway 설정·credential 입력을 만들지 않는다. 정책 안내가 필요하면 정적인 “Phase 8 미승인” 상태만 표시한다.

## 8. MVP 데모·회귀 시나리오

### Golden path

1. recorded BTC/ETH 공개 데이터를 paper 모드로 replay하고 quality=`healthy`를 확인한다.
2. `as_of`가 고정된 BTC Evidence에서 구조화된 분석과 Proposal을 만든다.
3. Risk Engine이 versioned policy로 allowed/denied와 reason code를 반환한다.
4. allowed Proposal을 운영자가 Paper 전용으로 승인한다.
5. `PaperExecutionAuthorization`이 Proposal/Risk hash, 승인 상태와 현재 Kill Switch·data/ledger 상태를 재검증한다.
6. LIMIT 주문이 둘 이상의 부분 체결과 수수료를 반영한 뒤 취소 또는 완료된다.
7. 포트폴리오와 commodity별 원장 균형을 확인한다.
8. 동일 fill을 다시 전달해 상태·원장 변화가 0임을 확인한다.
9. Audit 화면에서 전체 correlation timeline과 Testnet/Mainnet private 호출 0건을 증명한다.

### 반드시 함께 통과할 negative path

- 미래 데이터가 섞인 Evidence 또는 gap repair로 기존 snapshot 수정 → invalid/HOLD 또는 불변 snapshot 유지.
- rate-limit/429, malformed payload, raw-store 실패, stale·gap·queue overflow → bounded recovery 또는 신규 Proposal·Paper 승인/주문 차단; 무음 drop 0.
- Risk limit 초과 또는 Kill Switch → denied, 자동 해제 없음.
- Kill Switch 활성화 시 열린 Paper 주문 → 감사 이벤트와 함께 전건 취소, 후속 fill 0.
- 승인 이후 Proposal/Risk 변경 또는 만료 → Paper 실행 0.
- duplicate fill·event replay → portfolio/ledger 변화 0.
- inbox/state/ledger/outbox commit 중간 장애 → 전부 commit 또는 전부 rollback, 재처리 후 단 한 번 반영.
- 금지 private URL, credential env, Testnet schema/tool → startup/CI 실패.
- AI 응답 실패·prompt injection·schema 오류 → HOLD, 주문 tool 호출 0. provider key의 prompt/tool/log/trace/error/fixture 노출 0.

## 9. 승인된 초기 Risk 정책

`phase-0-approval-record.md`에 결속된 보수적 초기값이다. 이 숫자는 투자 안전이나 성과를 보장하지 않으며, versioned policy/hash와 모든 강제 거절 반례의 결정적 집행만을 뜻한다.

| 정책 | 승인값 |
|---|---:|
| 최대 전체 노출 | 25% |
| BTC 최대 노출 | 15% |
| ETH 최대 노출 | 10% |
| 거래당 한도 | fee-inclusive order notional이 equity의 0.25% 이하만 허용; 초과만 거절 (stop-loss 위험 보장으로 해석 금지) |
| 24시간 최대 손실 | rolling 24시간 realized PnL이 equity 기준 -1% 이하이면 거절 |
| 시스템 최대 낙폭 | fixed mark/as_of high-water equity 대비 5% 이상이면 거절 |
| 물타기 | 금지 |
| 레버리지·숏 | 금지 |
| 데이터 상태 | healthy 필수 |
| 원장·계좌 대조 | 정상 필수 |
| 중복 cooldown | canonical Proposal hash 재실행 effect 0; symbol/side 새 intent 15분 |
| 최대 spread | midpoint 기준 25 bps |
| 최대 예상 slippage | limit과 best executable의 불리한 차이 25 bps; authorization 발급 시 fresh book으로 재검사 |

MVP 합격은 후보값의 수익성을 평가하는 것이 아니라, 승인된 정책값이 버전되고 모든 강제 거절 반례에서 결정적으로 집행되는지를 평가한다.

## 10. Phase 0에서 확정한 결정

다음 결정은 `phase-0-decision-package.md` DP-D01~DP-D10과 `phase-0-approval-record.md`로 승인됐다. 구현 전에는 각 정책의 version/hash와 RED oracle을 고정하지만, 승인된 범위를 런타임 UI/API로 변경하지 않는다.

1. freshness 5초, 1m candle freshness 90초, clock skew 2초, reconnect 1→30초/10회/5분 cooldown 및 gap fail-closed.
2. queue 10,000 event, overflow=invalid과 신규 분석·승인·주문 차단.
3. R0~R6 보존, R5 no-hard-purge, provenance closure pin/restore-digest.
4. 거래소 완결 `1m/5m/1h/4h` kline, OHLCV·1bar return·SMA20·RSI14만 feature로 사용.
5. LIMIT/GTC, limit-price fill, displayed-liquidity 10%, quote fee 0.1%, Kill cancellation batch 100.
6. FIFO, public bid/ask midpoint fixed-as_of valuation, Decimal/rounding/residual account.
7. DP-D06 Risk 한도·precedence·critical reconciliation→Kill; runtime 정책 mutation 금지.
8. stable local actor, 5분 approval TTL, revoke, approval/authorization nonce 분리.
9. 단일 process의 논리적 역할 graph, Mock/replay 기준, 30초 timeout=`HOLD`, real provider는 별도 승인 전 비활성.
10. 파생 telemetry/news/social은 MVP 제외·Release 1.1 이월.
11. Windows 11+PowerShell 7 또는 Linux+Docker, Chromium 1440px/360px E2E, serious/critical a11y 0.
12. 최종 성능 SLO는 측정 기준선 뒤 별도 운영 승인; 임의 수치로 MVP 완료를 주장하지 않음.
13. 43개 fixed scenario는 누락/skip/artifact 누락 시 실패하는 고정 분모.
14. scheduler/test harness가 `as_of`와 `knowledge_cutoff`를 명시적으로 고정하고 wall-clock fallback을 금지.
15. commodity별 clearing/exchange account와 PHYSICAL/VALUATION journal 분리를 유지.

## 11. MVP Go / No-Go

### Go

- Phase 0~7의 Phase gate가 순서대로 통과했다.
- MVP-01~12가 모두 PASS이며 `UNVERIFIED`가 없다.
- P0/P1 blocking 안전·금융 결함이 0건이다.
- 외부 리뷰 완료 또는 `external-review-unavailable`이 정확히 기록됐다.
- 금지 capability와 credential 노출이 0건이다.
- golden path와 모든 negative path가 clean checkout CI에서 재현된다.
- evidence digest에 결속된 사용자의 명시적 MVP 승인 기록이 있다.

### No-Go

- 하나라도 금융 불변식, 미래 오염, stale 차단, replay 동등성 또는 Mainnet 구조 차단이 미검증이다.
- Paper와 Testnet 경계가 섞이거나 Phase 8 capability가 미리 존재한다.
- AI 결과나 UI 값을 잔고·Risk·원장의 권위로 사용한다.
- 원본 Release 1 요구 추적표에 orphan 또는 승인되지 않은 scope 삭제가 있다.
- 테스트 성공 대신 수익률·데모 화면·리뷰어 합의만으로 완료를 주장한다.

## 12. 다음 단계 참조

- 현재는 Phase 0이며 이 문서와 P0-01~P0-12 설계 패키지는 동결 전 후보이다. 제품 코드와 GREEN evidence는 아직 없다.
- 원본 요구 원자 추적과 아키텍처·ERD·API/event·금융·테스트·CI 설계 및 사용자 scope·정책 결정은 작성·승인됐다. 최종 package review, Git/PR evidence와 acceptance digest가 남아 있다.
- 다음 작업은 승인된 정책을 반영한 동결본의 Safety QA·Codex 내부 교차검토·외부 엔진 review gate를 수렴하고, 정렬된 evidence digest를 제시해 그 exact digest에 대한 새 사용자 전환 승인을 받는 것이다.
- Phase 0 acceptance evidence와 사용자 전환 승인 전에는 Phase 1 앱·fixture·migration·거래 코드를 만들지 않는다.
