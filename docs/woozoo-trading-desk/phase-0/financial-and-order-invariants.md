# P0-08 금융·주문 불변조건 설계 후보

- 상태: `POLICY_APPROVED — final package review/evidence digest pending`
- 제품 Phase: `0`
- 범위: Long/Cash, 내부 Paper Trading, `BTCUSDT`·`ETHUSDT`, LIMIT only
- 제외: 애플리케이션 구현, 실행 가능한 테스트·fixture·migration·scaffold, Testnet Gateway, 거래소 credential·signed/private 요청, Mainnet 주문
- 관련 acceptance: P0-08, MVP-05~08·11, FIN-001~004, ORD-001~002, ATOM-001~002, RISK-001~002, KILL-001~002, AUTH-001~002
- 판정 주의: 이 문서는 Phase 4~5 구현에 앞선 계약과 RED 시나리오다. 정책값은 `phase-0-approval-record.md`로 승인됐지만 실행 증거·final safety QA·acceptance evidence digest 전에는 P0-08 또는 Phase 0 `PASS`를 주장하지 않는다.

## 1. 범위와 권위 경계

금융 상태의 유일한 권위는 로컬 관계형 DB다. Redis, UI 상태, LLM 출력, 로그, outbox consumer와 외부 시장 데이터는 잔고·원장·주문 상태의 권위가 아니다. 결정론적 Risk Engine은 AI의 `TradeProposal`을 입력으로 받을 수 있지만 AI가 수량, 잔고, fee, PnL, Risk verdict, 승인 유효성 또는 주문 상태를 정하지 않는다.

Paper workflow의 순서는 다음과 같이 고정한다.

```text
TradeProposal
  -> RiskDecision (사람 승인과 독립적으로 먼저 계산)
  -> PaperApproval (allowed 결정에 대한 운영자 승인/거절)
  -> PaperExecutionAuthorization (실행 직전 hash·TTL·현재 안전 상태 재검증)
  -> PaperOrder create
  -> deterministic fills/cancel
  -> position·PnL·immutable ledger·outbox
```

`PaperApproval`과 `PaperExecutionAuthorization`은 외부 주문 승인이 아니다. Phase 8의 Testnet 승인과 nonce·account/environment 계약은 별도이며, 이 문서는 Gateway나 외부 주문 schema를 만들지 않는다.

제품 Phase 구현은 production dataflow와 반대로 P4 Paper→P5 Risk→P6 Proposal 순으로 진행되므로 creation과 activation을 분리한다. P4는 P5 Authorization closed consumer contract fixture로 Paper/ledger 불변식을 검증하되 production create/fill ingress와 미래 FK가 0이다. P5는 P6 Proposal fixture로 Risk/Authorization/Kill을 검증하고 P4 authorization reference의 FK만 이때 추가할 수 있지만 risk/create/authorization production chain은 계속 OFF다. P6에 authoritative Proposal producer와 실제 Proposal FK가 생겨도 full-chain contract·integration·replay, Kill barrier와 reconciliation은 fixture/test namespace에서만 검증하고 production aggregate·route는 계속 OFF다. P7에서만 authenticated login/session과 사람 Approval, Authorization, Paper production chain을 함께 활성화한다. fixture ID는 test namespace 전용이며 production DB row, seed 또는 activation evidence가 아니다.

## 2. 핵심 불변조건 행렬

| ID | 경계 | 불변조건 | 강제 수단 후보 | 최소 검증 ID |
|---|---|---|---|---|
| INV-NUM-01 | 모든 금융 값 | price, quantity, balance, fee, cost, PnL, exposure는 유한 Decimal이다. float 입력·NaN·Infinity·지수 표기 허용 여부 불명확 값은 거절한다. API/event에는 정규화된 10진 문자열로 직렬화한다. | Decimal parser, field별 scale metadata, schema constraint | FIN-003 |
| INV-NUM-02 | 반올림 | scale과 rounding은 `financial_policy_version`에 결속한다. 중간 계산은 조기 반올림하지 않고 명시된 posting/validation 경계에서만 quantize한다. | versioned policy, canonical Decimal encoder | FIN-003, RISK-001 |
| INV-ORD-01 | 주문 유형 | `LIMIT`만 허용하고 side는 `BUY|SELL`, TIF 후보는 `GTC`만 허용한다. market/stop/short는 표현하거나 fallback하지 않는다. | schema enum+domain validation | ORD-001, SAFE-003 |
| INV-ORD-02 | Long/Cash | SELL 가능량은 현재 available BTC/ETH와 허용된 hold 이내다. position, available, held는 항상 `>= 0`이다. credit·margin·borrow는 없다. | row lock, check constraint, ledger projection assertion | FIN-002 |
| INV-ORD-03 | 체결 상한 | `filled_qty = sum(unique fill.qty)`이고 `0 <= filled_qty <= original_qty`, `remaining_qty = original_qty - filled_qty`다. | unique fill key, locked order aggregate, check constraint | ORD-001 |
| INV-ORD-04 | terminal 단조성 | 생성된 order는 `FILLED|CANCELLED`에서 non-terminal로 돌아가지 않는다. terminal 뒤 새로운 fill은 거절·격리하고 상태나 원장을 바꾸지 않는다. | transition table, version column | ORD-001 |
| INV-ORD-05 | create rejection | create guard 실패는 `REJECTED` order aggregate를 만들지 않는다. immutable command receipt와 rejection outbox만 남고 order/hold/fill/ledger는 0건이다. | command receipt, closed reason enum, absence assertion | AUTH-001~002, ORD-002 |
| INV-FEE-01 | 수수료 | 모든 fill은 `fee_asset`, `fee_rate`, `fee_amount`, `fee_policy_version`을 보존한다. fee가 0이어도 명시하며 누락을 0으로 추론하지 않는다. | non-null fields, policy lookup | FIN-003 |
| INV-POS-01 | cost basis | position은 immutable acquisition lot의 잔량 합이다. SELL은 FIFO로 lot를 소비하며 lot mutation 대신 consumption record를 추가한다. | lot+consumption unique relation | FIN-003 |
| INV-PNL-01 | PnL | realized/unrealized PnL은 고정된 lots, fee, mark source, `as_of`, rate와 policy version에서 재현 가능해야 한다. UI 계산값은 권위가 아니다. | deterministic oracle, valuation snapshot hash | FIN-003 |
| INV-LED-01 | 복식 원장 | 각 journal과 commodity별로 `sum(debit)-sum(credit)=0`이다. BTC와 USDT를 서로 상계하지 않는다. | deferred balance assertion/property test | FIN-001 |
| INV-LED-02 | immutable | posted journal/entry는 UPDATE/DELETE하지 않는다. 정정은 원 journal을 가리키는 완전 reversal 뒤 replacement로만 한다. | append-only permission, correction relation | FIN-004 |
| INV-LED-03 | 중복 방지 | 하나의 `(business_event_type,business_event_id)`는 journal kind별로 최대 한 journal만 만든다. 같은 fill의 `PHYSICAL`과 `VALUATION`은 각각 허용하되 같은 kind 재삽입은 거절한다. | unique `(business_event_type,business_event_id,journal_kind)` | FIN-001, FIN-003, ORD-002, ATOM-002 |
| INV-ATOMIC-01 | local command/event | inbox 수락, domain state, hold/position, ledger, stored response와 outbox를 한 로컬 권위 DB transaction에서 모두 commit하거나 모두 rollback한다. | DB transaction+unique constraints | ATOM-001 |
| INV-IDEM-01 | command replay | 같은 `(scope, idempotency_key)`와 같은 request hash는 저장된 동일 response를 반환하고 효과를 추가하지 않는다. hash가 다르면 conflict다. | unique key+request hash | ORD-002 |
| INV-IDEM-02 | 식별자 | `client_order_id`, inbox event key, fill key, approval nonce와 outbox event ID는 각 scope에서 unique다. | DB unique constraints | ORD-002, ATOM-002 |
| INV-RISK-01 | 결정론 | Proposal, portfolio, data, order preview, policy/calculator, Kill, reconciliation, decision clock, duplicate/exposure snapshot 전체를 canonicalize한 같은 `risk_input_digest`는 byte-identical verdict, ordered reason codes와 decision hash를 만든다. `risk_input_digest`가 UNIQUE authority다. | canonical serialization+pure evaluator+DB UNIQUE | RISK-001 |
| INV-RISK-02 | fail-closed | missing/stale/future data, Kill Switch, reconciliation failure, hash/schema 오류와 한도 초과는 허용으로 fallback하지 않는다. | precedence table | RISK-002 |
| INV-AUTH-01 | 순서 | Risk는 승인 없이 평가한다. Paper create는 allowed Risk 뒤 유효한 Paper approval과 실행 직전 authorization을 모두 요구한다. | state/hash binding | AUTH-001 |
| INV-AUTH-02 | 단회 실행 | approval nonce는 사람 결정의 idempotent identity이고 execution token이 아니다. authorization nonce만 단일 command/`client_order_id` 실행에 결속하며 첫 시도의 성공·guard 실패·version drift 모두 영구 소비한다. | risk immutable authorization + paper `authorization_id UNIQUE` attempt receipt | AUTH-001, ORD-002 |
| INV-KILL-01 | Kill Switch | risk-owned barrier activation을 먼저 commit한 뒤 신규 Paper create/fill은 0건이며 열린 주문은 paper-owned idempotent saga/batch로 정렬 감사 취소하고 hold를 해제한다. 자동 해제하지 않는다. | shared barrier row lock/version+cancel batch UNIQUE | KILL-001~002 |
| INV-REC-01 | 대조 | order/fill/hold/lot/balance/ledger projection이 불일치하면 신규 주문을 차단한다. cash/asset/physical-ledger 불일치와 authorization first-attempt receipt 불일치는 즉시 Kill Switch를 활성화하고, 그 밖의 불일치는 fail-closed로 신규 주문을 차단한다. | reconciliation checkpoint+health gate | RISK-002, AUTH-002 |

## 3. Decimal, scale과 rounding 계약

### 3.1 표현

- 외부/계약 입력은 정규 10진 문자열이다. binary float를 거쳐 만든 Decimal은 금지한다.
- canonical 형식은 부호, 정수부, 소수부를 명시하고 불필요한 지수 표기와 음수 0을 제거한다. hash 전에는 field별 승인 scale로 정규화한다.
- DB는 field별 충분한 precision의 exact numeric을 사용하되 precision/scale 숫자는 schema 설계와 사용자 승인 뒤 고정한다.
- `price_scale`, `quantity_scale`, `asset_scale`, `fee_scale`, `valuation_scale`은 symbol/asset rule version에서 읽는다. scale을 코드 상수나 UI formatting에서 추론하지 않는다.
- 곱셈·나눗셈은 승인된 Decimal context에서 guard digits를 유지한다. posting 전에 한 번만 quantize하고, 원 입력과 quantized 결과를 함께 감사한다.

### 3.2 rounding 권고안

| 목적 | 권고 rounding | 안전 이유 | 상태 |
|---|---|---|---|
| 주문 수량을 step에 맞춤 | `ROUND_DOWN` | 요청 수량·보유량을 초과하지 않음 | 승인됨 |
| BUY principal/fee hold | 각각 `ROUND_UP` 후 합산 | 실제 debit보다 적게 reserve하지 않음 | 승인됨 |
| SELL base fee reserve | `ROUND_UP` | 보유량 초과를 사전에 차단 | 승인됨 |
| 실제 Paper fee debit | policy scale의 `ROUND_UP` | fee를 과소 계상하지 않음 | 승인됨 |
| 장부 원시 체결 수량 | 이미 검증된 exact 값, 재반올림 금지 | 체결·원장 drift 방지 | 계약 후보 |
| valuation/PnL 표시 | 계산 원값 보존 후 표시만 `ROUND_HALF_EVEN` | 반복 집계 편향 축소 | 승인됨 |

반올림 잔차를 조용히 버리지 않는다. posting scale에서 잔차가 생기면 승인된 `RoundingResidual` valuation account에 원인·정책 버전과 함께 기록한다.

### 3.3 승인된 운영 정책

다음 값은 `phase-0-approval-record.md`와 DP-D04~DP-D08에 결속된 운영 정책이다. symbol filter와 oracle 검증은 구현 전제이지 값의 재승인 조건이 아니다.

| 결정 ID | 항목 | 설계 권고 | 현재 판정 |
|---|---|---|---|
| FP-01 | Decimal precision과 field별 scale | DB `NUMERIC(38,18)`; versioned symbol/asset metadata가 scale을 제공 | 승인됨 |
| FP-02 | LIMIT price tick, quantity step, min quantity/notional | 공식 public rule snapshot을 Phase 4 직전 검증하고 Paper policy에 pin | 승인됨 |
| FP-03 | fee asset·rate·최소 fee·rounding | quote-asset `0.100000%`; 실제 fee commodity는 모든 fill에 보존 | 승인됨 |
| FP-04 | fill participation cap과 displayed-liquidity 사용률 | displayed liquidity의 `10%`, floor-to-step | 승인됨 |
| FP-05 | valuation mark source·freshness·scale | fresh public bid/ask midpoint와 fixed `as_of`; book freshness 5초 | 승인됨 |
| FP-06 | Risk 한도·window·drawdown 기준·중복 cooldown | DP-D06의 exposure/notional/loss/drawdown/spread/slippage/cooldown 계산 | 승인됨 |
| FP-07 | Paper approval TTL과 인증 actor/revocation 절차 | stable local actor, 5분 TTL, revoke, one-time authorization | 승인됨 |
| FP-08 | reconciliation 주기·허용 오차 | startup/replay/60초, exact asset tolerance 0; critical mismatch→Kill | 승인됨 |

## 4. fee와 FIFO cost basis 권고

### 4.1 fee 정책

`fee_asset`은 fill마다 명시한다. 초기 Paper MVP에는 quote asset fee를 권고하지만, 원장 계약은 base/quote/승인된 제3 asset을 서로 다른 commodity로 기록할 수 있어야 한다. 승인되지 않은 fee asset이나 valuation source가 없는 제3 asset fee는 0으로 추론하지 않고 fill을 격리해 reconciliation을 실패시킨다.

- BUY, quote fee: fee를 lot acquisition cost에 가산한다.
- BUY, base fee: gross acquired base와 fee outflow를 모두 posting하고 lot quantity는 net base, quote cost는 principal로 기록한다.
- SELL, quote fee: gross proceeds에서 차감해 realized PnL을 계산한다.
- SELL, base fee: 판매 수량의 `trade_disposed_basis`와 fee 수량의 `fee_disposal_basis`를 서로 다른 FIFO consumption으로 기록한다. fee의 fair value expense와 fee asset disposal gain/loss도 별도 valuation posting한다.
- 제3 asset fee: 해당 asset의 실제 outflow, `fee_disposal_basis`, `fee_fair_value_expense`, `fee_disposal_gain_loss`와 `fee_valuation_source/as_of/rate`를 별도 valuation posting한다.

SELL의 nonquote(base 또는 승인된 제3 asset) fee valuation은 승인된 **fixed quote oracle**을 사용한다. oracle은 fee 발생 시 고정된 `source`, `as_of`, `rate`, `source_evidence_hash`, `valuation_policy_version`을 보존하며 이후 mark로 소급 변경하지 않는다. basis 또는 fixed oracle 하나라도 없으면 fee를 0으로 추론하지 않고 fill transaction을 거절·격리한다.

### 4.2 FIFO lot

lot은 `(lot_id, asset, acquired_qty, remaining_qty_projection, quote_cost, source_fill_id, acquired_at, policy_version)`으로 식별한다. 원본 acquired quantity/cost는 immutable이고, `remaining_qty`는 immutable consumption들의 합으로 재구성 가능한 projection이다. 동일 acquisition time이면 `source_fill_id`의 canonical byte order로 FIFO tie를 끊는다.

SELL realized PnL은 trade disposal과 nonquote fee asset disposal을 분리한다.

```text
gross_proceeds = sum(sell_fill_qty * sell_fill_price)
trade_disposed_basis = FIFO basis consumed by sold quantity only
quote_fee_expense = fee amount when fee_asset = quote

fee_disposal_basis = FIFO basis consumed by nonquote fee quantity only
fee_fair_value_expense = nonquote fee quantity * fixed_quote_oracle_rate
fee_disposal_gain_loss = fee_fair_value_expense - fee_disposal_basis

trade_realized_pnl = gross_proceeds - trade_disposed_basis - quote_fee_expense
total_realized_pnl = trade_realized_pnl
                     - fee_fair_value_expense
                     + fee_disposal_gain_loss
```

따라서 nonquote fee의 순 PnL 효과는 정확히 `-fee_disposal_basis`이며 `fee_fair_value_expense`와 `fee_disposal_basis`를 동시에 비용으로 빼지 않는다. `trade_disposed_basis`에는 fee quantity basis가 들어가지 않는다. 이 네 posting kind와 원본 lot consumption을 별도 보존해 합산 시 double count가 0임을 검증한다.

unrealized PnL은 position lot을 수정하지 않고 별도 valuation snapshot에서 계산한다.

```text
mark_value = remaining_qty * approved_mark_price(as_of)
remaining_basis = sum(remaining FIFO lot quote_cost)
unrealized_pnl = mark_value - remaining_basis
```

## 5. Paper LIMIT 주문 상태 기계

### 5.1 상태와 전이

| 현재 상태 | 입력 | guard | 다음 상태 | 효과 |
|---|---|---|---|---|
| 없음 | create | Risk allowed, approval 유효, execution authorization PASS, switch OFF, reconciliation healthy, balance 충분 | `OPEN` | order+hold+journal+outbox atomic commit |
| 없음 | create | guard 중 하나 실패 또는 expected-version drift | order 없음; command receipt=`REJECTED` | authorization attempt=`BLOCKED`, hold/fill/ledger/order 0, immutable rejection receipt+outbox commit |
| `OPEN` | unique eligible fill | `0 < qty < remaining` | `PARTIALLY_FILLED` | fill, hold 소비, lot/lot consumption, journal, outbox |
| `OPEN` | unique eligible fill | `qty = remaining` | `FILLED` | fill과 잔여 hold 정산, terminal event |
| `PARTIALLY_FILLED` | unique eligible fill | `0 < qty < remaining` | `PARTIALLY_FILLED` | 누적 fill 증가, 기존 fill/fee 보존 |
| `PARTIALLY_FILLED` | unique eligible fill | `qty = remaining` | `FILLED` | 잔여 수량 체결, terminal event |
| `OPEN|PARTIALLY_FILLED` | cancel | remaining `> 0` | `CANCELLED` | 미체결 reserve 전액 해제, 기존 fill/fee 보존 |
| `OPEN|PARTIALLY_FILLED` | Kill Switch cancel | switch activation sequence 이후 | `CANCELLED` | reason=`KILL_SWITCH_ACTIVE`, 감사 outbox |
| terminal | duplicate 동일 event | event/request hash 일치 | 동일 상태 | 저장 response replay, 추가 효과 0 |
| terminal | 새 fill·상충 event | hash/key 불일치 또는 terminal 역전 | 동일 상태+quarantine | 금융 효과 0, reconciliation alert |

`REJECTED`는 order state가 아니라 immutable command receipt outcome이다. 따라서 rejected 결과에는 `paper_order_id`, order history, hold, fill, ledger가 없다. UI는 이를 주문 목록의 terminal order처럼 표시하지 않고 command/activity timeline에 stable reason과 “주문 미생성”으로 표시한다. event consumer도 `paper.order-command.rejected`를 order projection 생성 신호로 해석하면 안 된다.

`SUBMIT_OUTCOME_UNKNOWN`은 주문 domain state가 아니라 호출자의 관측 상태다. 응답 timeout 또는 연결 단절 시 새 authorization이나 `client_order_id`로 replacement를 만들지 않는다. 같은 idempotency key와 `client_order_id`로 command receipt와 order를 함께 조회·재시도한다. 결과는 (a) 생성된 order의 `OPEN|PARTIALLY_FILLED|FILLED|CANCELLED`, (b) order 없이 command receipt `REJECTED`, (c) 둘 다 `not-found` 중 하나다. `not-found`도 권위 DB 조회와 reconciliation이 first-attempt receipt 부재를 확정하기 전에는 replacement 근거가 아니다.

### 5.2 결정론적 보수 fill/cancel 모델

권고 모델은 거래소 matching을 재현한다고 주장하지 않는다.

1. order acceptance 당시 또는 그 이전 관측으로 즉시 fill하지 않는다. `accepted_market_seq`보다 뒤인 승인된 book observation만 사용한다.
2. BUY는 best ask `<= limit`, SELL은 best bid `>= limit`일 때만 eligible하다. candle high/low touch만으로 fill하지 않는다.
3. 경제적 낙관을 줄이기 위해 execution price는 더 좋은 관측 가격이 아니라 주문 limit price를 사용한다.
4. fill quantity는 `min(remaining_qty, floor_to_step(displayed_qty * 0.10), observation_liquidity_remaining)`이다. 0이면 fill 없음이다.
5. 하나의 observation liquidity를 여러 주문에 중복 사용하지 않는다. `accepted_broker_seq, client_order_id` 순으로 예산을 배정한다.
6. command와 market observation은 DB가 발급한 단조 `broker_seq` 순서로 적용한다. cancel의 sequence 이후 observation은 fill할 수 없다. 동일 외부 timestamp는 순서를 정하지 않으며 `broker_seq`가 tie-breaker다.
7. cancel 이전 sequence의 fill은 보존한다. cancel은 remaining만 종료하고 이미 발생한 fee·lot·PnL을 되돌리지 않는다.
8. duplicate/out-of-order observation은 market event key와 source sequence로 dedupe/격리한다. replay는 같은 ordered input log에서 같은 fills와 digest를 만든다.

시장가 fallback, hidden liquidity 추정, queue position 낙관, candle-only fill, future observation 사용은 금지한다. slippage/participation/price 정책은 FP-03~05로 승인됐고 policy hash에 결속된다.

## 6. hold, balance와 position

BUY create의 최대 reserve 후보는 `rounded_up(limit_price * quantity) + rounded_up(max_quote_fee)`다. SELL create는 `quantity + max_base_fee`를 base asset에서 reserve하며 quote/제3 asset fee 정책이면 해당 fee asset도 별도 reserve한다. fee reserve가 결정되지 않으면 주문을 받지 않는다.

각 asset에서 다음이 항상 성립한다.

```text
available >= 0
held >= 0
custody_balance = available + held
open_order_required_hold = sum(order remaining reserves)
held = open_order_required_hold + other explicitly classified holds
position_qty = sum(immutable acquisition qty) - sum(immutable lot consumption qty)
position_qty >= 0
```

hold 이동과 release도 ledger journal이다. in-memory decrement 뒤 사후 원장 기록처럼 분리하지 않는다. SELL fill은 held base를 소비하고 BUY fill은 held quote를 소비한다. fee asset별 hold가 부족하면 음수로 진행하지 않고 transaction을 rollback하고 reconciliation failure로 차단한다.

## 7. commodity별 immutable double-entry ledger

### 7.1 book과 account 역할

| Book | Commodity 예 | 목적 | spendable 여부 |
|---|---|---|---|
| `PHYSICAL` | `BTC`, `USDT` | 실제 Paper custody 수량, holds, exchange/fee source·sink | Assets의 available만 가능 |
| `VALUATION` | `USDT_VAL` | FIFO basis, disposal proceeds, realized/unrealized PnL, rounding residual | 불가 |

권장 account 군은 `Assets:Paper:{asset}:Available|Held`, `Clearing:PaperExchange:{asset}`, `External:PaperFunding:{asset}`, `External:PaperExchangeFee:{asset}`와 valuation 전용 `Valuation:*`, `PnL:*`이다. clearing/exchange account를 사용해 BTC 유입과 USDT 유출을 각각 같은 commodity 안에서 균형시킨다.

각 journal은 `journal_id`, `book`, `business_event_id`, `journal_kind`, `correlation_id`, `occurred_at`, `recorded_at`, `policy_version`, optional `reversal_of`를 가진다. entry는 commodity, account, side, amount를 가진다. amount는 양수이고 방향은 debit/credit으로만 표현한다.

### 7.2 고정 journal/PnL oracle 후보

아래 값은 운영 fee·scale 정책이 아니라 FIN-001·FIN-003용 exact oracle fixture다. DP-D04의 fee/scale 정책을 대체하거나 실제 시장·Testnet/Mainnet 수수료로 사용하지 않는다.

```text
초기 funding       = 200.000000 USDT
BUY limit          = 0.01000000 BTC @ 10000.000000 USDT/BTC
fee candidate      = quote asset, 0.001000 (0.1%), fee scale 6, ROUND_UP
oracle valuation   = scale 6, ROUND_HALF_EVEN; FIFO unit cost first, final consumption gets residual
BUY partial fill   = 0.00600000 BTC @ limit
BUY fee            = 0.060000 USDT
BUY cancel reserve = 40.040000 USDT
SELL limit/fill    = 0.00400000 BTC @ 11000.000000 USDT/BTC
SELL fee           = 0.044000 USDT
mark               = 11000.000000 at fixed oracle as_of
```

#### J-000 Paper funding

| Commodity | Debit account | Credit account | Amount |
|---|---|---|---:|
| USDT | `Assets:Paper:USDT:Available` | `External:PaperFunding:USDT` | 200.000000 |

#### J-001 BUY order hold

BUY 최대 principal 100.000000과 최대 quote fee 0.100000을 reserve한다.

| Commodity | Debit account | Credit account | Amount |
|---|---|---|---:|
| USDT | `Assets:Paper:USDT:Held` | `Assets:Paper:USDT:Available` | 100.100000 |

#### J-002 BUY partial fill principal·asset·fee

| Commodity | Debit account | Credit account | Amount |
|---|---|---|---:|
| BTC | `Assets:Paper:BTC:Available` | `Clearing:PaperExchange:BTC` | 0.00600000 |
| USDT | `Clearing:PaperExchange:USDT` | `Assets:Paper:USDT:Held` | 60.000000 |
| USDT | `External:PaperExchangeFee:USDT` | `Assets:Paper:USDT:Held` | 0.060000 |

같은 business fill의 valuation journal은 physical journal과 다른 `journal_kind`로 분리한다.

| Commodity | Debit account | Credit account | Amount |
|---|---|---|---:|
| USDT_VAL | `Valuation:BTC:FIFOInventoryBasis` | `Valuation:AcquisitionClearing` | 60.060000 |

#### J-003 BUY cancel의 미체결 hold release

remaining principal 40.000000과 remaining fee reserve 0.040000만 해제한다. 기존 0.006 BTC와 0.060000 fee는 유지한다.

| Commodity | Debit account | Credit account | Amount |
|---|---|---|---:|
| USDT | `Assets:Paper:USDT:Available` | `Assets:Paper:USDT:Held` | 40.040000 |

#### J-004 SELL order hold

| Commodity | Debit account | Credit account | Amount |
|---|---|---|---:|
| BTC | `Assets:Paper:BTC:Held` | `Assets:Paper:BTC:Available` | 0.00400000 |

#### J-005 SELL fill principal·asset·fee

| Commodity | Debit account | Credit account | Amount |
|---|---|---|---:|
| BTC | `Clearing:PaperExchange:BTC` | `Assets:Paper:BTC:Held` | 0.00400000 |
| USDT | `Assets:Paper:USDT:Available` | `Clearing:PaperExchange:USDT` | 44.000000 |
| USDT | `External:PaperExchangeFee:USDT` | `Assets:Paper:USDT:Available` | 0.044000 |

이 fixture의 FIFO unit cost는 먼저 `60.060000 / 0.00600000 = 10010.000000 USDT_VAL/BTC`로 계산하고, disposed basis는 `10010.000000 * 0.00400000 = 40.040000`으로 valuation scale에서 한 번 quantize한다. 부분 소비마다 비율을 먼저 나눠 순환소수 중간값을 만들지 않는다. 일반 lot에서 나눗셈 잔차가 생기면 승인된 allocation rounding을 적용하고 마지막 consumption이 lot 원가의 잔차 전부를 가져가 `sum(consumed basis) = original lot cost`를 보존한다. SELL valuation은 raw commodity journal과 분리한다.

| Commodity | Debit account | Credit account | Amount |
|---|---|---|---:|
| USDT_VAL | `PnL:COGS:FIFO` | `Valuation:BTC:FIFOInventoryBasis` | 40.040000 |
| USDT_VAL | `Valuation:RealizedPnLClearing` | `PnL:DisposalRevenue` | 44.000000 |
| USDT_VAL | `PnL:TradingFeeExpense` | `Valuation:RealizedPnLClearing` | 0.044000 |

#### J-006 fixed mark valuation

남은 BTC는 0.00200000, remaining basis는 20.020000, mark value는 22.000000이다.

| Commodity | Debit account | Credit account | Amount |
|---|---|---|---:|
| USDT_VAL | `Valuation:BTC:FairValueAdjustment` | `PnL:UnrealizedGain` | 1.980000 |

mark journal은 `valuation_source`, `as_of`, `rate=11000.000000`, `source_evidence_hash`를 가진다. 다음 mark에서 J-006을 UPDATE하지 않고 reversal+새 mark journal을 추가한다.

### 7.3 oracle 결과

```text
USDT available     = 183.896000
USDT held          = 0.000000
BTC available      = 0.00200000
BTC held           = 0.00000000
realized PnL       = 44.000000 - 40.040000 - 0.044000 = 3.916000 USDT_VAL
unrealized PnL     = 22.000000 - 20.020000 = 1.980000 USDT_VAL
total PnL          = 5.896000 USDT_VAL
physical equity at mark = 183.896000 + 22.000000 = 205.896000 USDT-equivalent
```

모든 J-000~J-006은 각 commodity 안에서 debit과 credit이 정확히 같다. `USDT_VAL`은 spendable USDT가 아니며 physical balance에 합산하지 않는다.

## 8. idempotency, concurrency와 unique 계약

### 8.1 canonical request와 replay

공유 정본 `command-request-hash.v1`을 사용한다.

```text
request_hash = SHA-256(canonical_json({
  contract_version,
  method,
  canonical_path,
  authenticated_actor: { actor_id, authorization_scope_or_service_principal },
  closed_schema_body,
  referenced_domain_hashes
}))
```

`method`는 uppercase, path는 API 계약의 정규화 규칙을 적용한다. closed body에는 command type, account/mode, Proposal/Risk/approval hashes, symbol, side, type, quantity, limit price, TIF, policy versions와 intended `client_order_id`가 포함된다. `scope`는 idempotency UNIQUE namespace일 뿐 actor/path를 대신하지 않는다. transport request/correlation ID, 전달 header, 수신 시각과 retry count는 제외한다. 따라서 같은 actor·path·body·reference·contract version의 retry만 같은 hash이며 actor, path 또는 contract version 중 하나라도 바뀌면 다른 hash다.

| 요청 | 결과 |
|---|---|
| 새 `(scope, idempotency_key)` | transaction 안에서 처리하고 request hash+response body/hash 저장 |
| 같은 key+같은 hash | 저장 response byte를 반환, domain/ledger/outbox 추가 효과 0 |
| 같은 key+다른 hash | `IDEMPOTENCY_CONFLICT`, 효과 0 |
| timeout/ack loss | 같은 key와 `client_order_id`로 query/retry |
| query not-found가 불확정 | replacement 금지, reconciliation/운영자 확인 |

`approval_nonce`는 사람 approve/reject 결정을 idempotently 식별하며 execution 소비 토큰이 아니다. `authorization_nonce`만 실행 single-use이고 `client_order_id`와 command ID에 결속해 결정적으로 생성하거나 사전 할당한다. 어느 방식이든 재시도 시 동일해야 하고 unique violation을 “다른 authorization/주문을 만들라”는 신호로 해석하지 않는다.

### 8.2 최소 unique/check constraint 계약

| 대상 | 제약 후보 |
|---|---|
| command inbox | unique `(scope, idempotency_key)`, stored request hash |
| Paper order | unique `(paper_account_id, client_order_id)` |
| external/domain event inbox | unique `(producer, event_key)` |
| fill | unique `(paper_order_id, fill_key)` |
| journal | unique `(business_event_type, business_event_id, journal_kind)`; 같은 event의 `PHYSICAL`·`VALUATION`은 각각 1건 허용 |
| outbox | unique `outbox_event_id`, unique `(aggregate_id, aggregate_version, event_type)` |
| approval | unique `approval_nonce`, unique bound approval command ID와 `(proposal,risk,preview)`; 실행 consumed 상태 없음 |
| authorization | risk-owned immutable unique `authorization_nonce`와 authorization hash |
| authorization attempt/consumption | paper-owned unique `authorization_id`, unique `authorization_nonce`; first outcome `CONSUMED_ORDER_CREATED|BLOCKED` |
| command receipt | unique command id/idempotency key; `REJECTED`이면 `paper_order_id IS NULL` |
| lot consumption | unique `(sell_fill_id, source_lot_id, sequence)` |
| order aggregate | check `0 <= filled_qty <= original_qty`, optimistic version monotonic |
| balances/holds | check projection `>= 0`; DB transaction 안에서 ledger-derived assertion |

### 8.3 concurrency 순서

한 paper transaction은 먼저 idempotency row와 risk-owned shared Kill barrier row/version을 잠그고 검사한 뒤 reconciliation snapshot, account+asset rows를 canonical asset 순서로, order aggregate를 잠근다. concurrent fills/cancel/create는 barrier와 order/account별로 직렬화하며 unique constraint가 최종 중복 방어선이다. isolation 후보는 PostgreSQL `SERIALIZABLE` 또는 동일 효과의 명시적 row locking이다. serialization retry는 같은 idempotency key와 authorization/client order ID로만 최대 3회이며, 세 번째 serialization failure는 effect 0과 retryable `DEPENDENCY_UNAVAILABLE` receipt로 끝낸다.

Redis lock, process-local mutex 또는 메시지 순서만으로 금융 exactly-once를 주장하지 않는다.

## 9. 로컬 권위 DB transaction 경계

각 paper command/event 처리의 **local owner transaction**은 다음 순서를 하나의 commit으로 묶는다. risk-engine의 Authorization·Kill event와 paper-engine의 order/ledger/cancellation을 하나의 cross-owner transaction으로 묶었다고 주장하지 않는다.

```text
BEGIN
  1. inbox/idempotency row INSERT 또는 같은 hash replay 판정
  2. shared Kill barrier row/version lock·검증, reconciliation/policy와 immutable authorization 검증
  3. 첫 create attempt면 paper-owned authorization_consumption INSERT (authorization_id UNIQUE)
  4. account/order rows를 canonical order로 lock
  5a. 성공: state transition과 fill/lot/hold/balance 계산 + journal/commodity assertion
  5b. guard/version 실패: order/hold/fill/journal 0, BLOCKED consumption + REJECTED command receipt
  6. domain aggregate/version 또는 immutable command receipt와 stored response 기록
  7. paper outbox event INSERT
COMMIT
```

1~7 중 어느 지점의 실패도 inbox만, state만, ledger만 또는 outbox만 남기면 안 된다. paper-engine은 risk-owned authorization row의 used/blocked field를 UPDATE하지 않는다. 성공·guard 실패·expected-version drift의 durable 첫 attempt event를 risk-engine이 inbox-dedupe해 consumed/blocked projection으로 재구성한다. transaction 자체가 commit 전에 rollback되면 같은 key/authorization으로만 재시도하고 replacement를 만들지 않는다. outbox publish는 commit 뒤 at-least-once이며 consumer가 event ID를 dedupe한다. publish 성공은 금융 transaction commit 조건이 아니고, publish 실패가 domain transaction을 rollback한 것처럼 위장하지 않는다.

## 10. Risk policy와 decision hash

### 10.1 고정 입력

Risk evaluator는 다음 전부를 schema-versioned canonical JSON에 포함해 `risk_input_digest = SHA-256(canonical_json(risk_input))`를 만든다.

- Proposal schema version, full canonical payload와 `proposal_hash`
- portfolio snapshot ID/hash: available, held, FIFO lots, realized PnL, open orders
- data state: Evidence ID/hash, `as_of`, `knowledge_cutoff`, freshness/quality, watermark
- proposed order preview의 full canonical payload와 `paper_order_preview_hash`: symbol, side, LIMIT, quantity, limit price, TIF, worst-case fee/hold/notional, observed spread와 deterministic worst-case slippage input
- policy version/hash, Decimal/fee/valuation/spread/slippage calculator name+version+hash
- Kill Switch state/version/event ID
- reconciliation checkpoint ID/hash/health
- deterministic `decision_as_of`와 approved clock source/version
- duplicate/cooldown key, window와 기존 order/exposure snapshot ID/hash

위 목록 중 하나라도 누락되면 `INPUT_SCHEMA_INVALID`이고 RiskDecision을 허용하지 않는다. `risk_input_digest`는 DB UNIQUE로 강제하는 Risk 입력 동일성의 유일 권위이며 일부 FK/column 조합, `proposal_hash` 또는 `decision_hash`로 대체하지 않는다. 사람 승인 존재/부재는 Risk input이 아니다. 승인 전에 같은 입력으로 Risk를 평가할 수 있어야 한다.

### 10.2 precedence와 reason code

모든 해당 reason을 수집한 뒤 아래 priority, code lexical order로 정렬한다. 가장 앞의 code가 primary reason이고 전체 ordered list가 hash에 들어간다.

| Priority | 분류 | reason code 후보 | verdict |
|---:|---|---|---|
| 10 | evaluator/config integrity | `RISK_POLICY_MISSING`, `RISK_POLICY_HASH_MISMATCH`, `DECIMAL_POLICY_INVALID`, `INPUT_SCHEMA_INVALID`, `EQUITY_INVALID` | `ERROR` |
| 20 | Kill Switch | `KILL_SWITCH_ACTIVE` | `DENIED` |
| 30 | reconciliation/ledger | `RECONCILIATION_UNHEALTHY`, `LEDGER_IMBALANCE`, `PORTFOLIO_SNAPSHOT_MISMATCH` | `DENIED` |
| 40 | data integrity | `EVIDENCE_MISSING`, `DATA_INVALID`, `DATA_STALE`, `FUTURE_CONTAMINATION`, `WATERMARK_INCOMPLETE` | `DENIED` |
| 50 | Proposal identity/duplicate | `PROPOSAL_HASH_MISMATCH`, `DUPLICATE_PROPOSAL`, `DUPLICATE_ORDER_INTENT` | `DENIED` |
| 60 | product boundary | `SYMBOL_NOT_ALLOWED`, `ORDER_TYPE_NOT_LIMIT`, `SIDE_NOT_LONG_CASH`, `TIF_NOT_ALLOWED` | `DENIED` |
| 70 | funds/quantity | `INVALID_PRICE_OR_QTY`, `INSUFFICIENT_AVAILABLE_BALANCE`, `FEE_RESERVE_INSUFFICIENT`, `SELL_EXCEEDS_POSITION` | `DENIED` |
| 75 | execution quality | `EXECUTION_QUALITY_UNKNOWN`, `SPREAD_LIMIT_EXCEEDED`, `EXPECTED_SLIPPAGE_LIMIT_EXCEEDED` | `DENIED` |
| 80 | exposure | `ORDER_NOTIONAL_LIMIT_EXCEEDED`, `SYMBOL_EXPOSURE_LIMIT_EXCEEDED`, `PORTFOLIO_EXPOSURE_LIMIT_EXCEEDED` | `DENIED` |
| 90 | loss/drawdown | `REALIZED_LOSS_LIMIT_EXCEEDED`, `DRAWDOWN_LIMIT_EXCEEDED` | `DENIED` |
| 100 | 통과 | `RISK_ALLOWED` | `ALLOWED` |

이 표는 evaluator가 낼 수 있는 reason code의 closed enum이다. 표 밖 code를 산출하려는 상태는 `INPUT_SCHEMA_INVALID` priority 10 `ERROR`로 대체하며, 미정의 code를 정렬·hash·응답에 기록하지 않는다.

FP-06은 전체/심볼 노출 `25%/15%/10%`, fee-inclusive 주문 notional `0.25%`, rolling 24시간 realized loss `1%`, high-water drawdown `5%`, 15분 symbol/side cooldown, 25 bps spread/slippage로 승인됐다. 정책 누락은 무제한 허용이 아니라 `RISK_POLICY_MISSING`으로 fail-closed한다.

### 10.2.1 승인된 한도 산식

모든 비율은 같은 `decision_as_of`의 healthy midpoint mark와 `ROUND_HALF_EVEN` display scale 이전의 full Decimal 값으로 계산한다. `equity`는 `available_quote + held_quote + Σ(base_available + base_held) × midpoint - fee_liabilities`이며, 0 이하 또는 mark/fee/liability가 불명확하면 `EQUITY_INVALID`로 거절한다.

- `position_exposure[symbol] = (base_available + base_held) × midpoint`이다. 기존 Long position은 항상 포함한다.
- `open_buy_commitment[symbol] = Σ(open BUY remaining_qty × limit_price + remaining_worst_case_quote_fee)`이다. SELL은 Long을 줄일 뿐 새 quote exposure를 만들지 않으므로 0으로 센다.
- `candidate_order_notional = abs(candidate_qty × candidate_limit) + candidate_worst_case_quote_fee`는 BUY와 SELL 모두에 적용한다. `candidate_buy_commitment = candidate_order_notional`은 BUY일 때만 적용하고 candidate SELL은 0이다. 수수료가 quote asset이 아니거나 worst-case 값을 만들 수 없으면 fail-closed한다.
- `symbol_exposure_after = position_exposure + open_buy_commitment + candidate_buy_commitment`; `portfolio_exposure_after`는 두 allowlisted symbol의 합이다. 각각 `symbol_exposure_after / equity`, `portfolio_exposure_after / equity`를 BTC `15%`, ETH `10%`, 전체 `25%`와 비교한다. 어느 하나라도 해당 한도를 초과(`>`)하면 각각의 exposure reason code로 `DENIED`다.
- `order_notional_ratio = candidate_order_notional / equity`이며 BUY와 SELL 모두 fee-inclusive limit notional의 절댓값을 사용해 `0.25%`와 비교한다. `order_notional_ratio > 0.25%`이면 `ORDER_NOTIONAL_LIMIT_EXCEEDED`로 `DENIED`다. 이는 stop 없는 MVP에서 손실 보증이 아닌 주문 크기 한도다.
- rolling loss window는 `[decision_as_of - 24h, decision_as_of]`에 finalized된 FIFO realized PnL의 합이다. `loss_ratio = max(0, -realized_pnl_window) / equity_at_window_close`; `equity_at_window_close`는 동일 `decision_as_of` snapshot equity다. `loss_ratio >= 1%`이면 `REALIZED_LOSS_LIMIT_EXCEEDED`다.
- high-water equity는 각 healthy reconciliation checkpoint에서 계산한 `equity`의 단조 최대값이며, 현재 checkpoint 전에 기록된 값만 사용한다. `drawdown_ratio = max(0, high_water_equity - equity) / high_water_equity`; high-water가 0 이하이면 fail-closed한다. `drawdown_ratio >= 5%`는 `DRAWDOWN_LIMIT_EXCEEDED`와 `DENIED`이고 자동 Kill은 아니다.
- BUY expected slippage는 `(limit_price - best_ask) / best_ask`, SELL은 `(best_bid - limit_price) / best_bid`다. 음수 값은 0으로 clamp하고, best bid/ask가 없거나 0이면 `EXECUTION_QUALITY_UNKNOWN`으로 거절한다. spread는 `(best_ask - best_bid) / ((best_ask + best_bid) / 2)`다. 둘 모두 25 bps 이하이어야 한다.

### 10.3 decision hash

```text
decision_hash = SHA-256(canonical_json({
  decision_schema_version,
  risk_input_digest,
  verdict,
  ordered_reason_codes
}))
```

`risk_input_digest`가 위 10.1의 모든 입력을 이미 결속하므로 decision hash가 누락된 하위 입력을 별도 조합으로 재정의하지 않는다. DB row ID, worker ID, wall-clock `recorded_at`, trace ID와 reason의 자연어 문구는 hash에서 제외한다. display message 변경이 금융 결정을 바꾸지 않게 하고, 반대로 입력/policy/calculator/Kill/reconciliation/clock/duplicate/exposure 변경은 반드시 새 digest와 decision hash를 만든다.

## 11. 승인과 실행 직전 PaperExecutionAuthorization

1. Risk Engine이 승인 존재를 보지 않고 `RiskDecision`을 만든다.
2. `ALLOWED`인 정확한 Proposal/Risk hash에 대해 인증된 운영자가 Paper 전용 approve/reject를 남긴다.
3. approval은 actor, `approved_at`, `expires_at`, revocation, `approval_nonce`, Proposal/Risk/policy hash와 exact `paper_order_preview_hash`에 결속한다. approval nonce는 사람 결정의 idempotent identity이며 execution single-use가 아니다.
4. create transaction 직전에 `PaperExecutionAuthorization`이 approval missing/expired/revoked, hash/preview mismatch, current Kill Switch, data freshness/integrity, reconciliation health와 balance/hold를 재검증한다. 같은 policy version으로 fresh book의 observed spread와 limit 대비 expected slippage를 다시 계산해 한도 초과 또는 불명확이면 `EXECUTION_QUALITY_DRIFT`로 effect 0이며 새 Risk/approval이 필요하다.
5. 하나라도 실패하면 order·hold·fill·ledger 0건이고 stable AUTH reason code를 남긴다.
6. risk-engine은 `authorization_nonce`를 가진 immutable Authorization만 append한다. paper-engine은 이 row를 update하지 않는다.
7. paper create의 첫 실행 시도는 `authorization_id UNIQUE`인 paper-owned attempt/consumption receipt를 command receipt, order/ledger 또는 rejection outbox와 같은 transaction에 append한다. 성공은 `CONSUMED_ORDER_CREATED`, guard 실패·version drift는 `BLOCKED`이며 둘 다 nonce를 영구 invalidated한다.
8. risk-engine의 authorization consumed/blocked 상태는 paper event를 inbox-dedupe해 만든 projection일 뿐 원본 Authorization mutation이 아니다. 두 번째 시도는 같은 payload여도 order effect 0과 `AUTHORIZATION_ALREADY_ATTEMPTED`다.

실행 시점에 정책 또는 Proposal을 재계산해 기존 approval에 억지로 맞추지 않는다. 변경된 내용은 새 RiskDecision과 새 approval을 요구한다.

## 12. Kill Switch와 reconciliation

### 12.1 Kill Switch

1. risk-engine activation transaction은 shared authoritative `kill_switch_state` row를 exclusive lock하고 switch version 증가, `active=true`, activation event와 risk outbox를 **먼저 commit**한다. 열린 주문 취소와 한 cross-owner transaction이라고 주장하지 않는다.
2. 모든 paper create/fill transaction은 같은 barrier row/version을 lock/check한다. paper transaction이 먼저 compatible lock을 얻었다면 activation이 기다리므로 그 effect는 activation 이전에 commit된다. activation이 먼저 exclusive lock/commit했다면 이후 또는 대기 중인 create/fill은 새 active version을 보고 order/fill effect 0이다. activation commit 뒤 create/fill commit은 금지한다.
3. barrier commit 뒤 paper-engine은 activation event를 inbox-dedupe하고 열린 주문을 `(paper_account_id, accepted_broker_seq, client_order_id)` 순의 최대 100건 idempotent saga/batch로 `CANCELLED` 전이한다. 각 batch는 `(activation_event_id,batch_key)` UNIQUE이고 cancel event, remaining hold 해제, cancellation journal과 paper outbox를 한 local transaction에 기록한다. crash/duplicate delivery는 같은 key로 재개하며 gate는 cancel 완료 여부와 무관하게 닫혀 있다.

activation 이후 sequence의 market observation은 fill을 만들 수 없다. operator의 authenticated recovery와 원인 해소 evidence 없이 자동/AI 해제하지 않는다.

### 12.2 reconciliation 규칙

startup, replay 종료, 정기 checkpoint와 장애 복구 뒤 다음을 대조한다.

| 대조 | exact oracle |
|---|---|
| order vs fills | `order.filled_qty = sum(unique fills)`, remaining 공식 일치 |
| open orders vs holds | asset별 required hold와 held classification 일치 |
| positions vs FIFO | acquisition minus consumption = position, 음수 lot 0 |
| cash/assets vs physical ledger | available+held projection이 posted physical entries와 일치 |
| PnL vs valuation | realized/unrealized oracle가 policy/source/as_of와 일치 |
| journals | 모든 journal의 commodity별 debit-credit 0, orphan/collision 0 |
| inbox/domain/outbox | committed inbox마다 expected aggregate version/journal/outbox 존재, 중복 효과 0 |
| approval/auth | approval nonce는 사람 결정 identity; authorization_id/nonce당 first attempt receipt 정확히 1, 성공 order 최대 1, blocked 재사용 0, preview/hash 일치 |

mismatch는 `reconciliation_health=FAILED`와 checkpoint hash를 남기고 신규 Risk 허용·approval 실행·order create를 차단한다. commodity imbalance, cash/assets↔physical-ledger 불일치, authorization first-attempt receipt 불일치는 즉시 Kill Switch activation과 열린 Paper order 취소로 승격한다. 그 밖의 mismatch는 운영자 확인 전 신규 효과를 막는다. 복구는 원장 entry 수정이 아니라 원인 수정, reversal+replacement, replay, 새 checkpoint와 운영자 확인으로 수행한다.

## 13. RED 시나리오와 최소 반례

Phase 0에서는 아래를 실행하지 않는다. `RED 계약 정의됨`은 미래 테스트가 아직 없거나 구현 전 실패해야 한다는 뜻이고, `GREEN`과 실제 failure-injection 결과는 모두 `NOT RUN / UNVERIFIED`다.

| Scenario | 최소 반례/입력 | 결정적 oracle | Phase 0 evidence |
|---|---|---|---|
| FIN-001 | BTC journal은 debit 0.006/credit 0.00599999; BTC debit과 USDT credit을 환율로 상계; 같은 fill의 PHYSICAL+VALUATION 뒤 같은 kind 재삽입 | commodity별 imbalance는 rollback; 서로 다른 두 kind는 허용하고 같은 `(event_type,event_id,kind)` 재삽입 효과 0 | RED 계약 정의됨; GREEN 미실행 |
| FIN-002 | available 10 USDT에서 11 USDT hold; 0.002 BTC에서 0.003 SELL; fee hold 누락 | command 거절, balance/held/position 음수 0, journal 0 | RED 계약 정의됨; GREEN 미실행 |
| FIN-003 | J-000~J-006 고정 입력; nonquote SELL fee에서 trade basis와 fee basis를 합치거나 fee fair value와 fee basis를 둘 다 비용 차감; fixed oracle 누락 | quote fixture는 `realized=3.916000`, `unrealized=1.980000`; nonquote fee는 `trade_pnl - fee_fair_value_expense + fee_disposal_gain_loss`, 순효과 `-fee_disposal_basis`, double count 0 | RED 계약 정의됨; GREEN 미실행 |
| FIN-004 | posted J-002 UPDATE/DELETE, 또는 replacement만 추가하고 reversal 누락 | mutation 거절; complete reversal+replacement만 허용 | RED 계약 정의됨; GREEN 미실행 |
| ORD-001 | remaining 0.004에 fill 0.005, duplicate fill, cancel 뒤 fill, FILLED→OPEN | 추가 효과 0, quarantine/reconciliation alert, fill 상한 유지 | RED 계약 정의됨; GREEN 미실행 |
| ORD-002 | 같은 actor/path/version/key/hash N회, 같은 key에서 actor·path·contract version 또는 body 변경, 같은 `client_order_id` 병렬 create; guard 실패를 `REJECTED` order로 저장 | 완전 동일 retry만 response replay; 어느 hash 입력이든 바뀌면 conflict; 성공 시 order/journal/outbox 각 1, 실패 시 command receipt 1과 order/hold/fill/ledger 0 | RED 계약 정의됨; GREEN 미실행 |
| ATOM-001 | inbox 후, state 후, journal header/entry 사이, outbox 전 crash | inbox/state/ledger/outbox 전부 commit 또는 전부 rollback | RED 계약 정의됨; failure injection 미실행 |
| ATOM-002 | commit 성공 뒤 API ack 유실, process restart, outbox 중복 publish | 같은 ID 조회/replay, replacement 0, 금융 효과 1 | RED 계약 정의됨; failure injection 미실행 |
| RISK-001 | 동일 canonical input을 worker/locale/timezone/재시작만 바꿔 반복; 10.1의 각 입력을 한 번씩 1-byte 변경 | 동일 입력은 `risk_input_digest`/verdict/reasons/decision hash byte-identical; 각 필수 입력 변경은 새 digest; digest UNIQUE | RED 계약 정의됨; GREEN 미실행 |
| RISK-002 | stale/future data, Kill, reconciliation fail, missing policy, spread/slippage/각 한도 초과 동시 발생 | 승인된 §10.2.1 산식과 precedence 표 순서의 primary/all reasons, `SPREAD_LIMIT_EXCEEDED`/`EXPECTED_SLIPPAGE_LIMIT_EXCEEDED` 포함, 허용 0 | RED 계약 정의됨; GREEN 미실행 |
| KILL-001 | create/fill이 barrier lock 경합 중 switch 활성화, 열린 OPEN/PARTIALLY_FILLED 주문, 뒤 market observation | paper effect가 activation commit 전 또는 active 관측 후 0으로 직렬화; barrier 선commit, 전건 idempotent 감사 취소/hold 해제 | RED 계약 정의됨; failure injection 미실행 |
| KILL-002 | AI/자동 timer/미인증 actor의 해제 | switch OFF 전이 0, authenticated recovery 전 차단 | RED 계약 정의됨; GREEN 미실행 |
| AUTH-001 | approval 없음/만료/철회, Proposal/Risk/`paper_order_preview_hash` 1-byte 변경, approval-view full preview와 Risk hash 불일치, approval nonce를 execution token으로 사용, invalid/blocked approval-view branch, expired/revoked/rotated session 또는 CSRF/Origin 실패; 이미 발급·시도된 authorization 재사용 | invalid approval/preview는 `approval_view_status=INVALID`, reason nonempty/action false와 authorization/order/hold/journal 0; session/CSRF 실패도 effect 0; 완료 approval은 유효한 감사 이력으로 유지하고 authorization 재사용만 `AUTHORIZATION_ALREADY_ATTEMPTED`, 신규 효과 0 | RED 계약 정의됨; GREEN 미실행 |
| AUTH-002 | 첫 execution attempt에서 data stale, Kill ON, ledger mismatch, balance 감소 또는 expected-version drift; stale view version·표시 preview와 제출 hash 불일치; 같은 authorization 재시도 | 첫 attempt에 BLOCKED receipt 1, stale/misbound UI 승인·order 0, risk projection 재구성, 두 번째 시도 효과 0 | RED 계약 정의됨; failure injection 미실행 |
| E2E-003 | approve/create 클릭 재시도·reload·restart; stale cache의 다른 preview 표시 또는 response와 다른 preview hash 제출 | 동일 preview의 retry만 같은 `client_order_id`로 Paper order·ledger effect 1; preview 불일치는 승인·order 0 | Phase 7 설계 참조; 미실행 |
| E2E-004 | UI에 열린 주문이 있는 동안 Kill Switch | UI/API/ledger 모두 audited cancel, 수동 복구 표시 | Phase 7 설계 참조; 미실행 |

## 14. failure-injection 결과 계약

| Fault point | 예상 허용 결과 | 금지 결과 | 현재 실제 결과 |
|---|---|---|---|
| idempotency insert 직후 crash | 전체 rollback 또는 재시도 시 동일 결과 | inbox만 존재 | `NOT RUN` |
| hold/state 계산 뒤 crash | 전체 rollback | balance/hold만 변경 | `NOT RUN` |
| journal header와 entries 사이 crash | 전체 rollback | 불완전/불균형 journal | `NOT RUN` |
| ledger 뒤 outbox 전 crash | 전체 rollback | 금융 효과 있으나 event 없음 | `NOT RUN` |
| commit 뒤 ack loss | query/replay로 기존 결과 회수 | 새 ID replacement | `NOT RUN` |
| outbox publish 뒤 consumer ack loss | consumer dedupe, 금융 상태 변화 0 | 중복 fill/posting | `NOT RUN` |
| fill/cancel concurrency | broker sequence에 따른 단일 합법 전이 | overfill, cancel 뒤 fill | `NOT RUN` |
| authorization attempt receipt 뒤 guard/version 실패 | BLOCKED+REJECTED receipt와 outbox만 commit, 재사용 0 | risk row UPDATE, `REJECTED` order, 새 nonce replacement | `NOT RUN` |
| Kill activation/create/fill concurrency와 worker restart | barrier 선commit/직렬화, gate ON 유지, idempotent cancel batch 재개 | activation 뒤 fill/create commit, cross-owner 부분 transaction, 자동 OFF | `NOT RUN` |
| reconciliation 중 Redis loss | DB 권위로 재구성 또는 FAILED/HOLD | Redis balance를 권위로 채택 | `NOT RUN` |

실제 Phase 4~5에서는 `pnpm test:property`, `pnpm test:replay`, `pnpm test:failure`, `pnpm test:unit`의 해당 manifest command와 evidence artifact가 존재해야 GREEN을 주장할 수 있다. 현재 저장소에는 실행 가능한 테스트가 없으므로 pass rate나 failure-injection 성공률을 계산하지 않는다.

## 15. 승인된 정책 register

| Decision | 승인된 값 | 구현 전 동작 |
|---|---|---|
| Long/Cash, LIMIT/GTC only, BTCUSDT·ETHUSDT | 채택 | Phase 4 구현 전까지 계약만 존재 |
| FIFO cost basis | 채택 | Phase 4 oracle fixture 필요 |
| quote-asset Paper fee와 rate | quote fee `0.100000%` | fee policy hash가 없으면 fail-closed |
| Decimal precision/scale/rounding | `NUMERIC(38,18)`과 §3.2 | filter/scale 검증 전 migration 금지 |
| deterministic fill/cancel과 participation cap | §5.2, displayed liquidity `10%`, Kill batch 100 | Phase 4 RED/GREEN 필요 |
| valuation source/as_of와 PnL scale | fresh bid/ask midpoint, fixed `as_of`, display half-even | stale mark면 PnL 확정/승인 차단 |
| Risk limit/window/precedence | DP-D06 exact limits과 §10.2 | policy hash missing=`ERROR` |
| approval TTL/actor/revocation | stable actor, 5분, revoke, one-time authorization | Phase 5 contract/RED 필요 |
| reconciliation severity→Kill 매핑 | critical 3분류 즉시 Kill, 나머지는 new effect block | recovery evidence 필요 |
| J-000~J-006 fixed oracle | FIN-003 test fixture로 채택 | 운영 fee/시장 가정으로 사용 금지 |

## 다음 단계 참조

- FP-01~08, FIFO, quote/base/제3 asset fee 처리, fill model, Risk precedence·수치, approval TTL과 reconciliation→Kill 매핑은 `phase-0-approval-record.md`에 결속됐다.
- P0-06 ERD와 P0-07 API/event 계약이 이 문서의 IDs, Decimal string, policy/hash, unique key, journal/valuation book과 transaction 경계를 같은 의미로 표현하는지 교차 검토한다.
- P0-09 manifest의 FIN/ORD/ATOM/RISK/KILL/AUTH oracle을 승인된 exact 정책으로 갱신하되 필수 scenario를 삭제하거나 optional로 낮추지 않는다.
- 동결본에 대해 역할 분리 safety QA와 Codex same-engine 내부 교차검토를 수행하고, 외부 리뷰 완료 또는 `external-review-unavailable`을 별도로 기록한다.
- 모든 P0-01~12 evidence, Git gate와 사용자 승인이 결속되기 전에는 P0-08 PASS, Phase 0 완료 또는 Phase 1 전환을 주장하지 않는다.
