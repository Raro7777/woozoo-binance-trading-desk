# Woozoo Paper MVP — Phase 0 결정 패키지

- 상태: **POLICY_APPROVED — 사용자 위임 승인 기록됨, frozen acceptance digest 결속 대기**
- 작성일: 2026-07-19
- 적용 전제: `docs/woozoo-trading-desk/phase-state.json`의 현재 Phase 0
- 목적: 구현을 시작하기 전에 MVP 범위와 금융·Risk 정책의 빈칸을 명시적으로 결정한다.

사용자 위임 승인은 `phase-0/phase-0-approval-record.md`에 기록되어 있다. 이 문서는 그 정책의 정본 계약이며, 아직 Phase 전환 증거는 아니다. 이 내용을 P0 산출물에 반영·동결하고 재검토·evidence digest·digest-bound Phase 1 전환을 거치기 전에는 제품 코드를 만들지 않는다.

## 1. 한 번에 승인할 권고안

아래 묶음은 **DP-D01~DP-D11로 승인되었고**, 구현 가능한 Paper MVP의 경계를 닫는다.

| ID | 권고 결정 | 승인 시 의미 |
|---|---|---|
| DP-D01 | MVP는 Phase 0~7의 로컬 단일 운영자 Paper Trading으로 끝낸다. Phase 8은 Spot Testnet 검증, Phase 9는 운영 준비로 분리한다. | Testnet, credential, private API, 외부 주문은 MVP에 들어오지 않는다. |
| DP-D02 | 데이터는 Binance Spot의 공개·무인증 REST/WS와 `BTCUSDT`, `ETHUSDT`만 허용한다. candle은 `1m/5m/1h/4h`만 사용한다. | 다른 심볼·파생시장·뉴스·소셜은 Release 1.1 결정 전 구현하지 않는다. |
| DP-D03 | AI는 단일 `agent-orchestrator` process에서 Evidence를 읽어 구조화된 분석·Proposal만 만든다. 내부 graph/schema는 시장·기술, 체결흐름, Bull, Bear, Trader, Portfolio, Audit 역할을 논리적으로 분리하되, 별도 모델 process·도구 권한·금융 권위를 만들지 않는다. 로컬 Mock/replay provider를 MVP 필수 기준으로 한다. | AI에는 주문·잔고·원장·Risk 판정 도구가 없다. 기존 분석 역할과 section은 하나의 schema-valid report graph로 보존한다. |
| DP-D04 | 금융 권위는 `NUMERIC(38,18)`과 canonical Decimal 문자열, FIFO lot, immutable double-entry ledger로 둔다. | float·UI 계산·AI 산출물은 금융 권위가 될 수 없다. |
| DP-D05 | Paper 체결은 LIMIT/GTC, Long/Cash, 보수적 관측 기반 부분 체결만 허용한다. | market/stop/short/leverage와 낙관적인 candle-only 체결은 없다. |
| DP-D06 | Risk는 아래의 보수적 versioned 정책을 사용하며 누락값은 `ERROR`로 차단한다. | 한도나 계산기가 없으면 '허용'으로 폴백하지 않는다. |
| DP-D07 | 단일 운영자는 Argon2id password verifier와 server-side session으로 인증한다. plaintext bootstrap password는 local secret path에서만 읽고, browser에는 HttpOnly·Secure·SameSite=Strict session cookie만 제공한다. Paper approval TTL은 5분·철회 가능·실행 authorization은 단회 사용으로 둔다. | 임의 actor ID나 공유 UI 플래그로 승인할 수 없다. |
| DP-D08 | 보존·pin·purge는 아래 R0~R6 정책을 쓰고, ledger/audit는 MVP에서 hard purge하지 않는다. | provenance와 감사 체인이 TTL 때문에 끊기지 않는다. |
| DP-D09 | API/event의 최소 호환 기간은 `2개 제품 Phase 또는 90일 중 긴 기간`으로 둔다. | 안전·금융 contract를 consumer 확인 없이 제거하지 않는다. |
| DP-D10 | Windows 11 + PowerShell 7 또는 Linux와 Docker Compose를 개발 환경으로 지원하고, Chromium 기반 브라우저에서 desktop/mobile E2E와 serious/critical 접근성 결함 0을 MVP 조건으로 둔다. | Phase 1부터 동일한 `pnpm` 명령·고정 scenario manifest를 로컬/CI에서 사용한다. |
| DP-D11 | Git stage/commit/push/PR은 Phase 0 동결·검토·digest 이후 이 사용자 승인에 따라 수행한다. | 현재 사용자의 포괄 승인 기록이 Git 외부 변경 권한이다. |

## 2. 범위와 금지 경계

### DP-D01 — 릴리스 컷

권고: **Phase 0~7 Paper MVP 승인, Phase 8/9 및 Release 1.1 이월 승인.**

Paper MVP의 성공 경로는 다음으로 고정한다.

```text
공개 BTC/ETH 시장 데이터
  → 불변 Evidence
  → AI 분석·Proposal
  → 결정론적 Risk
  → 사람의 Paper 승인
  → 단회 PaperExecutionAuthorization
  → 내부 Paper LIMIT 주문·부분 체결·원장·감사 UI
```

다음은 MVP 및 현재 Phase 0~7에서 금지다: Mainnet private/live, 출금, Futures, Margin, leverage, short, AI의 주문·계좌 도구, Testnet URL/credential/signed request/private schema/gateway.

### DP-D02 — 데이터와 분석 범위

권고: `BTCUSDT`, `ETHUSDT`만 symbol allowlist에 넣고, 공식 Spot의 공개 endpoint/stream만 쓴다. `exchangeInfo` filter의 원문 Decimal 문자열·관측시각·hash를 보존한다. 허용되지 않은 심볼·인증 요구 경로·schema 오류·gap·stale·raw-store 실패는 quarantine 또는 `HOLD`이며 신규 분석·승인·주문을 차단한다.

캔들은 거래소가 제공한 `1m/5m/1h/4h` 완결 kline을 각각 원본으로 보존하며, 미완성 candle이나 현재 wall-clock으로 합성한 candle은 Evidence에 쓰지 않는다. MVP feature는 OHLCV, 한 candle 수익률, 20-period SMA, 14-period RSI로 한정한다. 이 feature는 분석 근거일 뿐 Risk·원장·체결 가격의 권위가 아니다.

공개 파생 telemetry, 뉴스, 소셜은 Release 1.1 후보로 이월한다. 이 데이터들은 출처·라이선스·provenance·prompt-injection·보존 계약을 새로 승인하지 않는 한 MVP에 추가하지 않는다.

### DP-D03 — AI 최소 역할

권고: MVP에는 **분석 process 하나**를 두되, 내부 graph/schema가 market/technical, execution-flow, Bull, Bear, Trader, Portfolio, Audit 역할을 각각 선언된 read-only Evidence 입력·출력 schema로 분리한다. 출력은 이 필수 section을 모두 가진 versioned 분석 보고서와 Proposal 또는 `HOLD`다. 개발·CI·replay의 기준 provider는 Mock/replay이며, 실모델은 추후 별도 provider adapter, 비용 상한 및 secret 관리 승인이 있을 때만 활성화한다. AI run은 30초 안에 schema-valid 결과를 내지 못하면 `HOLD`이며, Mock/replay 경로의 모델 비용은 0이다.

AI의 권한은 Evidence→분석→Proposal까지다. Risk, 승인, authorization, order, fill, balance, ledger를 생성·변경하거나 그 결과를 권위로 선언할 수 없다.

## 3. 금융·Paper Broker 결정

### DP-D04 — 수치·원장·PnL

권고안은 다음과 같다.

| 항목 | 권고값 |
|---|---|
| 저장/전송 수치 | DB `NUMERIC(38,18)`, API·event는 canonical Decimal string |
| cost basis | FIFO, lot 원본은 immutable, SELL은 consumption record를 추가 |
| 수수료 | 초기 Paper 수수료는 quote asset `0.100000%` (`0.001`)로 versioned policy에 고정; 모든 fill은 실제 `fee_asset`, `fee_rate`, `fee_amount`를 명시 |
| 수수료 미결정/비정상 | 0으로 추정하지 않고 fill을 거절 또는 quarantine |
| 반올림 | 주문 수량 `ROUND_DOWN`; BUY hold·fee reserve와 SELL base-fee reserve `ROUND_UP`; valuation/PnL 표시 `ROUND_HALF_EVEN`; 중간 계산의 조기 반올림 금지 |
| 반올림 잔차 | 승인된 posting scale의 잔차는 원인·policy version과 함께 `RoundingResidual` valuation account에 기록 |
| valuation | 관측된 공개 bid/ask의 midpoint와 고정 `as_of`·Evidence hash를 사용. 최신 mark 또는 policy가 없으면 PnL 확정·승인을 차단 |
| 대조 | asset 수량·commodity별 분개는 허용오차 0. 불일치는 신규 주문을 즉시 차단하고 금융 corruption 가능성이면 Kill Switch를 켠다. |

위 `0.100000%`는 실제 거래소 수수료의 대리가 아니라 Paper simulation의 공개·재현 가능한 정책값이다. 실거래 수수료로 해석하거나 Testnet/Mainnet에 재사용하지 않는다.

### DP-D05 — 보수적 LIMIT 체결

권고안은 다음과 같다.

- 주문은 `LIMIT` + `GTC`, `BUY|SELL`만 허용한다. SELL은 보유 Long을 줄이는 경우만 허용한다.
- 주문 수락 뒤의 관측만 체결 후보로 사용한다. BUY는 best ask가 limit 이하일 때, SELL은 best bid가 limit 이상일 때만 체결한다.
- 체결 가격은 더 유리한 미래 가격이 아닌 **주문 limit 가격**으로 고정한다.
- 한 관측의 체결 상한은 displayed liquidity의 **10%**이며, 수량 step 아래로 내림한다. 동일 liquidity를 여러 주문이 중복 소비할 수 없다.
- cancel 뒤의 관측은 fill을 만들 수 없다. duplicate/out-of-order 관측은 효과 없이 quarantine한다.
- Kill Switch가 활성화되면 열린 Paper order는 canonical 순서로 최대 100건씩 local transaction batch로 취소한다. batch 재시도는 `(activation_event_id,batch_key)`로 멱등 처리한다.
- hidden liquidity 추정, queue position 낙관, market-price fallback, candle high/low만으로 하는 체결은 금지한다.

## 4. Risk와 승인 결정

### DP-D06 — 초기 Risk 정책

권고값은 모두 `risk_policy_version`과 계산기 hash에 결속한다. 매 UI/API에서의 런타임 수정은 MVP에서 금지하고, 승인된 migration/config 변경과 actor 감사만 허용한다.

| 정책 | 권고값과 계산 의미 |
|---|---|
| 전체 노출 | mark-to-market equity 대비 BTC+ETH의 fee-inclusive open notional 합계 최대 `25%` |
| 심볼 노출 | BTC 최대 `15%`, ETH 최대 `10%` |
| 주문 한도 | fee-inclusive 단일 주문 notional 최대 equity의 `0.25%`. MVP에는 stop이 없으므로 이를 손실 보장으로 부르지 않는다. |
| 24시간 손실 | rolling 24시간 realized PnL이 equity 기준 `-1%` 이하이면 거절 |
| 최대 낙폭 | 고정된 mark/as_of로 계산한 high-water equity 대비 `5%` 이상 하락이면 거절 |
| 물타기 | 기존 같은 심볼 position을 늘리는 BUY 금지. SELL은 Long 축소만 허용 |
| 중복 cooldown | canonical Proposal hash는 재실행 효과 0; 같은 symbol/side의 새 order intent는 15분 안에 하나만 허용 |
| 데이터/대조 | healthy Evidence, freshness, reconciliation health가 모두 필수. 하나라도 불명확하면 `DENIED` 또는 `ERROR` |
| 최대 spread | `(best_ask - best_bid) / midpoint` 최대 `0.25%` (25 bps) |
| 최대 예상 slippage | limit price와 승인 시점 best executable price의 불리한 차이 최대 `0.25%` (25 bps) |
| Kill 승격 | commodity 원장 불균형, cash/asset↔physical-ledger 불일치, authorization first-attempt receipt 불일치는 즉시 Kill. 그 밖의 대조 불일치는 신규 승인·주문 차단 후 운영자 확인이 필요 |
| drawdown 동작 | 5% drawdown은 `DENIED`이며 자동 Kill activation은 하지 않음 |

Risk precedence는 `policy integrity → Kill → ledger/reconciliation → data integrity → duplicate → product boundary → funds → execution quality → exposure → loss/drawdown → allowed`로 고정한다. 모든 해당 reason code를 안정 순서로 기록하며, 첫 code가 primary reason이다.

### DP-D07 — 사람 승인과 실행 authorization

권고: 단일 운영자는 Argon2id password verifier와 server-side session으로 인증한다. plaintext bootstrap password는 server의 local secret path에서만 읽고 DB/prompt/log/trace/fixture에는 저장하지 않는다. browser에는 HttpOnly·Secure·SameSite=Strict session cookie만 제공하며 익명·공유 세션·UI만의 ‘승인됨’ 플래그는 허용하지 않는다.

- approval TTL: `5분`.
- approval은 approve/reject/revoke의 immutable audit 기록이고 `approval_nonce`는 사람 결정의 idempotent identity다.
- `PaperExecutionAuthorization`은 별도 `authorization_nonce`를 갖는 단회 실행 권한이다.
- 실행 직전에 Proposal/Risk/policy/preview hash, approval TTL·철회, Kill, data quality, reconciliation, balance/hold를 다시 검사한다.
- 실행 authorization 발급 시 fresh book으로 observed spread·expected slippage를 같은 policy version으로 다시 계산한다. 이 execution-quality guard가 한도를 넘거나 quality가 불명확하면 authorization/order effect는 0이며, 새 Risk/승인이 필요한 drift로 기록한다.
- 첫 실행 시도가 guard/version drift로 실패해도 immutable BLOCKED receipt를 남기고 그 authorization은 영구 소진한다. 두 번째 주문 효과는 항상 0이다.
- 승인 UI는 server가 생성한 canonical preview 전체와 hash만 표시한다. browser는 금융값을 다시 계산하지 않는다.

## 5. 데이터 보존과 운영 품질

### DP-D08 — retention·pin·purge

권고: Phase 0 설계의 R0~R6 기간을 채택한다.

| 분류 | hot 보존 | archive | hard purge |
|---|---:|---:|---|
| R0 ephemeral cache | 최대 24시간 | 없음 | 재구성 가능한 권위 밖 데이터만 |
| R1 raw market | 365일 | 이후 cold archive | 3년 뒤, provenance/pin 0 및 manifest 승인 후 |
| R2 feature/Evidence | 3년 | 이후 immutable archive | 모든 descendant 만료·pin 0일 때 7년 뒤 |
| R3 AI/Risk | 3년 | 이후 immutable archive | financial/approval descendant·pin 0일 때 7년 뒤 |
| R4 approval/Paper | 7년 | 2년 뒤 read-only archive 가능 | 7년 뒤 운영자 명시 승인·tombstone 및 ledger 참조 0일 때만 |
| R5 ledger/audit | 7년 이상 | 2년 뒤 read-only archive 가능 | **MVP 기본 no hard purge** |
| R6 delivery/dedupe | 관련 business row와 동일 | payload archive 가능 | business key tombstone이 관련 상태보다 먼저 삭제되지 않음 |

retention pin은 Evidence만이 아니라 raw provenance, Proposal, Risk, approval, authorization, Paper order, ledger까지 ancestor closure를 보호한다. archive digest와 restore drill 증거가 없으면 purge하지 않는다.

### 운영 품질의 초기 안전값

아래는 성능 목표가 아니라 fail-closed 품질 경계의 권고값이다.

| 항목 | 권고값 |
|---|---|
| 실시간 trade/book freshness | 마지막 유효 관측 후 5초 초과 시 stale |
| 1m candle freshness | 예상 close 이후 90초 초과 시 stale |
| clock skew | source event time과 수집 clock 차이가 2초를 넘으면 quality를 degraded로 낮추고 Evidence 입력에서 제외 |
| collector queue | 최대 10,000 event; overflow 즉시 invalid, 신규 분석·승인·주문 차단 |
| reconnect/gap | 1초부터 지수 backoff(최대 30초)로 10회 시도; 이후 5분 cooldown. 무음 drop은 허용하지 않으며, gap 탐지 후 공개 REST backfill·integrity 재확인 전 `HOLD` |
| reconciliation | startup, replay 완료, 그리고 60초마다 수행; 실패 시 신규 주문 차단 |

최종 ingest/API/UI/AI/replay SLO는 구현 후 측정 기준선이 생긴 뒤 별도 승인한다. 지금 임의의 처리량 목표를 ‘완료’로 선언하지 않는다.

## 6. 계약·플랫폼·검증 결정

### DP-D09 — API/event 호환성

권고: API/event의 제거·이름 변경·Decimal scale 의미 변경은 breaking change다. 최소 지원 기간은 **2개 제품 Phase 또는 90일 중 긴 기간**으로 둔다. 새 contract version은 compatibility manifest 전건 PASS, consumer inventory, shadow validation, 필요한 경우 dual-read/dual-publish, cutover evidence를 거치기 전 발행하거나 기존 version을 제거하지 않는다.

### DP-D10 — 로컬·CI·브라우저 기준

권고: 개발 환경은 **Windows 11 + PowerShell 7 또는 Linux + Docker Engine/Compose**로 정한다. Phase 1에서 정확한 Python/Node/pnpm 버전과 lockfile을 고정한다. CI는 Binance/Testnet credential이나 실제 모델 key 없이 실행돼야 한다.

- 검증 브라우저는 Chromium 최신 안정판으로 하고, desktop `1440px`과 mobile `360px` viewport를 필수 E2E로 둔다.
- minimum accessibility는 serious/critical 결함 0이며, keyboard로 Kill/approval/cancel의 핵심 조작이 가능해야 한다.
- `pnpm bootstrap`, `env:init`, `lint`, `typecheck`, `test:*`, `build`, `ci`의 의미와 failure 조건은 `local-ci-contract.md`를 정본으로 한다. Phase가 열리기 전 빈 성공 script는 만들지 않는다.
- 43개 고정 scenario manifest는 성공률의 분모다. 누락·skip·evidence artifact 누락은 실패이며, expected `HOLD`/거절도 올바른 성공 결과가 될 수 있다.

## 7. 승인 기록과 다음 게이트

DP-D01~DP-D10의 정책과 DP-D11의 Git 권한은 사용자의 포괄 위임으로 승인되었고, 그 원문·범위·금지 경계는 `phase-0/phase-0-approval-record.md`의 `P0-POLICY-01`, `P0-GIT-01`에 보존한다. 정책 값이나 범위를 바꾸려면 새 사용자 승인이 필요하다.

이후 순서는 고정한다: 결정 반영 → Phase 0 package 동결 → Codex 내부 교차검토 및 분리 safety QA → 외부 엔진 review 또는 `external-review-unavailable` → `path + SHA-256 + verdict` evidence digest → 그 exact digest를 인용한 사용자 전환 승인으로 Phase 1 전환. 정책/Git 위임만으로 아직 생성되지 않았던 digest의 전환을 자기 승인할 수 없다. 이 순서 전에는 Phase 1 제품 코드, migration, fixture, 거래 코드를 만들지 않는다.

## 8. 근거 문서

- `docs/woozoo-trading-desk/mvp-plan-and-acceptance.md`
- `docs/woozoo-trading-desk/phase-0/binance-official-assumptions.md`
- `docs/woozoo-trading-desk/phase-0/data-model-and-retention.md`
- `docs/woozoo-trading-desk/phase-0/financial-and-order-invariants.md`
- `docs/woozoo-trading-desk/phase-0/phase-1-scope-and-phase-0-report.md`
