# P0-09·P0-10 검증 전략과 고정 Scenario Manifest

- 상태: `P0-09 PASS 후보`, `P0-10 설계 증거 일부`
- Phase 0 의미: 실행 가능한 테스트·fixture·scaffold를 만들지 않고 RED 시나리오, oracle, 명령과 evidence 경로를 설계한다.
- 실행 시작: 각 제품 Phase 승인 후 해당 scenario를 RED로 추가한다.
- 분모 규칙: 아래 `required_from_phase <= current_phase`인 모든 행이 필수다. 행 누락·skip·artifact 누락은 성공률 계산 실패다.

## 1. 테스트 계층

| 계층 | 책임 | 대표 oracle |
|---|---|---|
| Schema/contract | producer-consumer shape와 version 호환 | schema validation, breaking diff 0 |
| Unit/table | Decimal, 상태 전이, reason code | 고정 input-output table |
| Property-based | 넓은 상태공간의 금융·주문 불변식 | commodity balance, conservation, idempotency |
| Replay | 같은 raw input+clock+policy의 동일 결과 | canonical output digest |
| Integration | DB/Redis/API/worker 원자성과 경계 | committed state set, outbox/inbox uniqueness |
| Failure injection | network/DB/restart/gap/timeout | fail-closed state와 bounded recovery |
| Safety/secret | 금지 capability와 secret 부재 | static graph/config scan, canary leak 0 |
| Browser E2E | 운영자 Paper workflow | UI/API/event/state cross-check |

## 2. 공통 실행 규칙

1. 모든 scenario는 ID, owner, input manifest, clock, policy/schema version, seed, expected result, command, evidence path를 가진다.
2. replay와 property tests는 UTC·고정 locale·고정 Decimal context를 사용한다.
3. simulation/replay의 `knowledge_cutoff`와 `as_of`는 test harness가 고정한다. wall clock fallback은 테스트 실패다.
4. external network는 recorded fixture 검증에서 금지한다. 선택적 live-public smoke는 merge gate와 분리한다.
5. failure injection 중 성공은 “프로세스가 살아 있음”이 아니라 state와 effect가 fail-closed oracle과 일치하는 것이다.
6. expected rejection/HOLD도 성공 경로다. BUY나 fill 발생률은 품질 지표가 아니다.

## 3. Scenario Manifest

- candidate manifest version: `2`
- 현재 Phase 0 frozen evidence digest 전에는 review finding을 닫기 위한 행 보강이 가능하다. digest 결속 뒤에는 행 추가·삭제·optional 전환 모두 새 manifest version과 범위 승인을 요구한다.

| ID | Phase | 종류 | 입력/고장 | 결정적 기대값 | 명령 | Evidence |
|---|---:|---|---|---|---|---|
| CORE-001 | 1 | Unit | UTC/UUID/canonical JSON·hash/error primitive를 locale·timezone·재시작만 바꿔 반복 | byte-identical 결과, wall-clock fallback·unknown field 0 | `pnpm test:unit -- CORE-001` | `artifacts/unit/CORE-001.json` |
| PLAT-001 | 1 | Integration | clean checkout→locked bootstrap→secretless env→Compose→FastAPI/Next.js health | generated diff 0, health 200, config secret 0, 필수 dependency 실패는 unhealthy/non-zero | `pnpm test:integration -- PLAT-001` | `artifacts/integration/PLAT-001.json` |
| PLAT-002 | 1 | Integration | Phase 1 migration upgrade→downgrade→re-upgrade와 seed 재실행 | schema digest 동일, duplicate seed effect 0, secret/private vocabulary 0 | `pnpm test:integration -- PLAT-002` | `artifacts/integration/PLAT-002.json` |
| PLAT-003 | 1 | Integration | Redis 제거·재시작·cache corruption | Postgres 권위 state 동일, cache 재구성 또는 명시적 HOLD, 금융/승인 권위 생성 0 | `pnpm test:integration -- PLAT-003` | `artifacts/integration/PLAT-003.json` |
| CONTRACT-001 | 1 | Contract | OpenAPI/event/error v1과 generated TS/Python binding drift·unknown field; `command-request-hash.v1` actor/path/contract-version 변이; approval-view full preview/hash 불일치, `INVALID` branch의 non-null preview 또는 action=true, unknown status, browser body `actor_id` 주입; CSRF token이 `GET /session` 이외 sink에 있거나 no-store/10분/session binding/one-time enum이 누락 | 동일 actor·path·body retry hash 동일, actor/path/version 변이는 hash 다름; preview mismatch는 `approval_view_status=INVALID`, nonempty reason, preview/hash/approval/authorization null, action false; response preview canonical hash=Risk preview hash 아니면 contract/build 차단; client actor injection은 closed-schema failure; CSRF raw value는 authenticated GET response만 허용되고 다른 sink 0; stale generated diff 0 | `pnpm test:contracts -- CONTRACT-001` | `artifacts/contracts/CONTRACT-001.json` |
| SAFE-001 | 1 | Safety | `TRADING_MODE` 누락·빈 값·미지 값·`live`·`testnet` | startup non-zero, network call 0 | `pnpm test:safety -- SAFE-001` | `artifacts/safety/SAFE-001.json` |
| SAFE-002 | 1 | Static | Phase 1~7 tree/config/schema/DI에 Testnet gateway·private/user-data capability 삽입 | scan 실패, CI 차단 | `pnpm test:safety -- SAFE-002` | `artifacts/safety/SAFE-002.json` |
| SAFE-003 | 1 | Static | Mainnet private/order/account, withdrawal, futures, margin, leverage, short canary | 발견 1건마다 실패 | `pnpm test:safety -- SAFE-003` | `artifacts/safety/SAFE-003.json` |
| SAFE-004 | 1 | Architecture | public client에 generic `exchange_client`, API key/signature 입력 추가 | dependency/capability rule 실패 | `pnpm test:safety -- SAFE-004` | `artifacts/safety/SAFE-004.json` |
| DATA-001 | 2 | Replay | duplicate trade/book/kline events | domain/raw 정책대로 한 번만 반영, digest 고정 | `pnpm test:replay -- DATA-001` | `artifacts/replay/DATA-001.json` |
| DATA-002 | 2 | Replay | out-of-order와 missing sequence/gap | gap 표시, `degraded` 또는 `stale`, Evidence 0 | `pnpm test:replay -- DATA-002` | `artifacts/replay/DATA-002.json` |
| DATA-003 | 2 | Failure | disconnect·24h reconnect·ping timeout·reconnect storm; raw/combined `serverShutdown`을 시장 이벤트로 오분류하거나 새 socket만으로 즉시 healthy 승격 | `serverShutdown`은 operational control로만 기록되고 market/Evidence effect 0; reconnect는 1초 지수 backoff→최대 30초/10회→5분 cooldown, continuity/gap 검증 전 상태 하향 유지, 무음 healthy 복구 0 | `pnpm test:failure -- DATA-003` | `artifacts/failure/DATA-003.json` |
| DATA-004 | 2 | Failure | HTTP 429+Retry-After, 연속 429 | backoff 준수, 418 유발 call 0 | `pnpm test:failure -- DATA-004` | `artifacts/failure/DATA-004.json` |
| DATA-005 | 2 | Failure | malformed payload/schema drift | quarantine+`invalid`, 정상 승격 0 | `pnpm test:failure -- DATA-005` | `artifacts/failure/DATA-005.json` |
| DATA-006 | 2 | Failure | append-only raw-store write 실패 | normalized/domain effect 0, `invalid`, alert | `pnpm test:failure -- DATA-006` | `artifacts/failure/DATA-006.json` |
| DATA-007 | 2 | Property | 10,000 event queue capacity 초과/backpressure | `invalid`, 신규 분석·승인·주문 0, silent drop 0 | `pnpm test:property -- DATA-007` | `artifacts/property/DATA-007.json` |
| PTI-001 | 3 | Boundary | `event_time`이 `as_of`, `received_at`이 `knowledge_cutoff`의 직전·동일·직후 | 두 축의 승인된 inclusive 규칙과 정확히 일치 | `pnpm test:replay -- PTI-001` | `artifacts/replay/PTI-001.json` |
| PTI-002 | 3 | Replay | snapshot 뒤 late arrival/gap repair | 기존 snapshot byte/hash 변경 0, 새 snapshot만 가능 | `pnpm test:replay -- PTI-002` | `artifacts/replay/PTI-002.json` |
| PTI-003 | 3 | Safety | missing raw ID, stale collector, watermark 미충족 | Evidence invalid, 분석·Proposal 0/HOLD | `pnpm test:safety -- PTI-003` | `artifacts/safety/PTI-003.json` |
| PTI-004 | 3 | Replay | wall clock을 바꾸고 같은 simulation clock 실행 | digest 동일, wall-clock read canary 실패 | `pnpm test:replay -- PTI-004` | `artifacts/replay/PTI-004.json` |
| FIN-001 | 4 | Property | 임의 deposit/hold/fill/fee/cancel sequence; 같은 fill event의 `PHYSICAL`·`VALUATION` journal과 같은 kind 중복 삽입 | 서로 다른 두 kind는 각각 1건 허용, 같은 kind 재삽입 0; 각 commodity의 debit-credit 차이 0 | `pnpm test:property -- FIN-001` | `artifacts/property/FIN-001.json` |
| FIN-002 | 4 | Property | 잔고보다 큰 BUY/SELL/fee | command 거절, 음수 balance/position 0 | `pnpm test:property -- FIN-002` | `artifacts/property/FIN-002.json` |
| FIN-003 | 4 | Oracle | 고정 fills·복수 fee asset·FIFO lots·동일 fill ID의 physical+valuation journal | 두 journal kind가 보존되고 승인된 PnL oracle과 Decimal 단위까지 동일 | `pnpm test:unit -- FIN-003` | `artifacts/unit/FIN-003.json` |
| FIN-004 | 4 | Immutability | posted ledger correction | UPDATE/DELETE 0, reversal+replacement만 | `pnpm test:integration -- FIN-004` | `artifacts/integration/FIN-004.json` |
| ORD-001 | 4 | Property | 부분 체결·중복 fill·cancel race | fill 합<=order qty, terminal 전이 역행 0 | `pnpm test:property -- ORD-001` | `artifacts/property/ORD-001.json` |
| ORD-002 | 4 | Replay | 동일 actor·path·contract version의 command/event/idempotency key N회; actor/path/version 중 하나를 바꾼 같은 key | 완전 동일 retry는 첫 response byte/hash와 같고 추가 effect 0; actor/path/version 변이는 `IDEMPOTENCY_CONFLICT`, effect 0 | `pnpm test:replay -- ORD-002` | `artifacts/replay/ORD-002.json` |
| ATOM-001 | 4 | Failure | inbox/state/ledger/outbox 각 commit 지점 crash | 전부 commit 또는 전부 rollback | `pnpm test:failure -- ATOM-001` | `artifacts/failure/ATOM-001.json` |
| ATOM-002 | 4 | Failure | commit 후 ack 전 crash와 restart | 재처리 시 추가 order/fill/posting/event 0 | `pnpm test:failure -- ATOM-002` | `artifacts/failure/ATOM-002.json` |
| RISK-001 | 5 | Replay | 같은 Proposal/portfolio/data/policy | decision+reason+hash byte 동일 | `pnpm test:replay -- RISK-001` | `artifacts/replay/RISK-001.json` |
| RISK-002 | 5 | Table | stale, future data, expired, duplicate; BUY/SELL 각각 25%/15%/10% exposure와 0.25% fee-inclusive order notional의 below/equal/above limit; 1% rolling loss와 5% drawdown의 below/equal/above limit; equity invalid, missing/zero best bid/ask, ledger mismatch, 25bps spread/expected-slippage 초과, 동시 reason | stable precedence/reason code; exposure/order는 below/equal이 다음 guard로 진행하고 above만 DENIED와 order 0; loss/drawdown은 equal/above에서 DENIED와 order 0; `EQUITY_INVALID`는 ERROR, `EXECUTION_QUALITY_UNKNOWN`은 DENIED, 동시 reason은 priority 뒤 lexical order이며 table 밖 code는 `INPUT_SCHEMA_INVALID`; drawdown은 DENIED이며 자동 Kill 0 | `pnpm test:unit -- RISK-002` | `artifacts/unit/RISK-002.json` |
| KILL-001 | 5 | Failure | create/fill과 barrier activation의 양방향 lock interleaving; 열린 주문 최대 100건 cancel batch 중 crash/restart; activation event 중복 delivery | paper transaction이 activation 전에 commit하거나 activation 뒤 order/fill effect 0 중 정확히 하나; activation commit 뒤 신규 effect 0; `(activation_event_id,batch_key)` UNIQUE로 saga 재개, 전건 감사 취소, hold·ledger·outbox 단회 반영 | `pnpm test:failure -- KILL-001` | `artifacts/failure/KILL-001.json` |
| KILL-002 | 5 | Safety | 원인 미해결 상태에서 자동/AI 해제 시도 | 해제 0, authenticated operator recovery 전 차단 | `pnpm test:safety -- KILL-002` | `artifacts/safety/KILL-002.json` |
| AUTH-001 | 5 | Table | missing/expired/revoked approval, Proposal/Risk hash mismatch, approval-view preview payload의 canonical hash와 Risk preview hash 불일치, invalid/blocked branch에서 approval POST, expired/revoked/rotated session, missing CSRF/foreign Origin/cross-session token/10분 expiry/one-time replay, 이미 authorization이 발급됐거나 first-attempt가 기록된 approval로 재발급·재실행 | invalid preview는 `approval_view_status=INVALID`, nonempty reason과 action false, authorization·order 0; session/CSRF failure는 actor/audit financial effect 0; 완료 approval projection은 유효한 감사 이력으로 유지하되 authorization 재사용은 `AUTHORIZATION_ALREADY_ATTEMPTED`, 신규 authorization·order 0 | `pnpm test:unit -- AUTH-001` | `artifacts/unit/AUTH-001.json` |
| AUTH-002 | 5 | Failure | 승인 뒤 Kill/data/ledger 상태 악화, fresh-book 25bps spread/slippage drift; data `degraded` 첫 재검증 실패 뒤 동일 authorization 재사용; stale view version 또는 표시 preview와 제출 hash 불일치 | execution-quality drift를 포함한 실행 직전 재검증 실패, 첫 시도 authorization 무효화, recover 뒤 재사용 0; stale/misbound UI 승인 effect와 order 0 | `pnpm test:failure -- AUTH-002` | `artifacts/failure/AUTH-002.json` |
| AI-001 | 6 | Contract | 30초 model timeout, malformed JSON, schema error, market/technical·flow·Bull·Bear·Trader·Portfolio·Audit section 누락 | HOLD, Proposal/Paper command 0 | `pnpm test:contracts -- AI-001` | `artifacts/contracts/AI-001.json` |
| AI-002 | 6 | Safety | orphan Evidence ID, `as_of` 뒤 주장 | report invalid/HOLD, Proposal 근거 승격 0 | `pnpm test:safety -- AI-002` | `artifacts/safety/AI-002.json` |
| SEC-001 | 6 | Secret | provider secret canary in UI/prompt/tool/log/trace/error/fixture | 모든 sink에서 0건 | `pnpm test:safety -- SEC-001` | `artifacts/safety/SEC-001.json` |
| SEC-002 | 6 | Capability | AI/browser에서 order/cancel/account/Risk-policy tool 호출 시도 | tool registry/API route 0, call 거절 | `pnpm test:safety -- SEC-002` | `artifacts/safety/SEC-002.json` |
| E2E-001 | 7 | Golden | HTTPS local login한 single operator→healthy data→Evidence→analysis role graph→Proposal→Risk→server canonical preview 표시→Paper approval→partial fill/cancel | Secure/HttpOnly/SameSite session과 same-origin CSRF가 존재하고 UI는 actor ID를 보내지 않음; 화면 preview 각 field=approval-view response, 제출 preview hash=Risk/response hash; UI/API/event/ledger 모두 manifest oracle과 일치 | `pnpm test:e2e -- E2E-001` | `artifacts/e2e/E2E-001/` |
| E2E-002 | 7 | Negative | data stale while approval view open | 승인/실행 차단, reason·alert·audit 표시 | `pnpm test:e2e -- E2E-002` | `artifacts/e2e/E2E-002/` |
| E2E-003 | 7 | Negative | duplicate click/retry/reload/restart; stale cache가 다른 preview를 표시하거나 response hash와 다른 hash 제출 | 동일 preview retry는 Paper order와 ledger effect 단 1개; 표시·제출·Risk preview 불일치는 승인·order effect 0 | `pnpm test:e2e -- E2E-003` | `artifacts/e2e/E2E-003/` |
| E2E-004 | 7 | Negative | Kill Switch during open order | UI에 감사 취소·manual recovery 표시, 후속 fill 0 | `pnpm test:e2e -- E2E-004` | `artifacts/e2e/E2E-004/` |
| E2E-005 | 7 | Accessibility | desktop/mobile 핵심 journey | serious/critical violation 0, keyboard path 완주 | `pnpm test:e2e -- E2E-005` | `artifacts/e2e/E2E-005/` |

현재 candidate 고정 분모는 43개다. 동결 digest 승인 뒤 새 필수 scenario는 manifest version을 올리고 사용자 승인된 Phase 범위 변경으로만 추가한다. 테스트를 삭제하거나 optional로 낮추는 변경도 같은 review gate를 통과한다.

## 4. 핵심 Property 정의

### 4.1 Ledger balance

각 journal transaction `t`와 commodity `c`에 대해:

```text
sum(debit(t, c)) - sum(credit(t, c)) = 0
```

BTC와 USDT를 환율로 상계해 0을 만들지 않는다. fee commodity도 독립적으로 균형을 맞춘다.

### 4.2 Conservation과 non-negative

허용된 external/source account를 포함한 posting 전후 각 commodity 변화가 source/sink와 정확히 일치해야 한다. cash, available, held와 position quantity는 승인된 overdraft가 없으므로 음수가 될 수 없다.

### 4.3 Order monotonicity

```text
0 <= cumulative_filled_qty <= original_qty
remaining_qty = original_qty - cumulative_filled_qty
terminal -> non-terminal transition = forbidden
```

### 4.4 Idempotency

동일 `(scope, idempotency_key, command-request-hash.v1)` 재요청은 같은 result를 반환하고 effect를 추가하지 않는다. hash는 contract version, method, canonical path, authenticated actor/service principal, closed body와 referenced hashes를 포함한다. 같은 key에서 actor·path·contract version을 포함한 어느 입력이든 달라지면 conflict다. event key와 Client Order ID도 unique constraint로 강제한다.

### 4.5 Point-in-time

Evidence item은 승인된 boundary rule 아래 `event_time <= as_of`와 `received_at <= knowledge_cutoff`를 각각 만족하고 raw provenance가 있어야 한다. `as_of`와 `knowledge_cutoff`의 관계·clock owner는 snapshot recipe/version에 명시하며 둘 중 하나를 다른 축의 대용값으로 쓰지 않는다. 수집 watermark와 quality가 미충족이면 snapshot은 invalid다.

## 5. Failure Injection Matrix

| Fault point | 허용 결과 | 금지 결과 |
|---|---|---|
| WS disconnect/gap | degraded/stale, bounded reconnect, Evidence 차단 | healthy 유지, silent gap |
| HTTP 429 | Retry-After+backoff | 즉시 재시도 폭주 |
| raw append failure | invalid, downstream effect 0 | normalized-only 저장 |
| DB transaction crash | all commit 또는 all rollback | ledger만/상태만 commit |
| ack loss after commit | idempotent replay | duplicate order/posting |
| Redis loss | 캐시 무효·DB 권위·HOLD 가능 | Redis 값을 금융 권위로 사용 |
| model timeout/schema error | HOLD | 부분 report로 Proposal 진행 |
| Kill Switch activation | new command 0, Paper open-order audited cancel | 자동 해제·후속 fill |
| clock skew | stale/invalid·차단 | wall clock 보정으로 조용히 통과 |

## 6. Mainnet/private 구조 차단 검증

P0-10의 최종 증명은 아키텍처 문서와 함께 다음 네 층을 모두 검사한다.

1. **Dependency:** Phase 1~7 package graph에 signed/private/Testnet SDK 또는 generic exchange client가 없다.
2. **Configuration:** private/order/account URL, API key/signature/Testnet enabled 변수가 schema에 없다.
3. **Capability:** AI/browser/public collector가 order/cancel/account/user-data method를 표현하지 못한다.
4. **Network:** public collector egress는 market-only allowlist이고 provider egress는 model endpoint만이다.

host 문자열 scan만으로 PASS하지 않는다. public data 때문에 production Spot host가 필요할 수 있으므로 method+path+auth+capability 조합을 검사한다.

## 7. Coverage와 판정

`100%`는 manifest에 등록된 필수 ID 중 command exit 0, expected assertion PASS, evidence artifact 존재, input/output hash 존재인 행의 비율이다.

```text
eligible = required_from_phase <= current_phase
passed = exit_zero && all_assertions_pass && artifact_exists && hashes_present
coverage = passed / eligible
```

eligible 행이 0이거나 manifest와 CI job 목록이 다르면 계산 실패다. 새로운 결함을 발견한 테스트가 실패하는 것은 정상이며, 결함 수정 없이 expected output을 완화하지 않는다.

## 8. P0-09 Acceptance

- replay, property, failure injection, Mainnet/private 차단이 각각 ID·oracle·명령·evidence를 가진다.
- `as_of` 경계, late arrival, duplicate/restart, atomic commit, Kill Switch, secret leak 반례가 포함된다.
- 고정 분모와 누락 실패 규칙이 있다.
- P0-11의 root commands와 일치한다.
- 이 파일의 SHA-256과 verdict가 최종 evidence manifest에 결속된다.

## 9. 다음 단계 참조

- 각 Phase는 자기 행을 RED로 먼저 추가하고, adjacent-boundary scenario까지 재실행한다.
- P0-08 financial policy와 P0-07 schema가 확정되면 FIN/RISK/AUTH oracle 필드를 exact 값으로 갱신한다.
- Phase 7 MVP 합격은 이 manifest의 Phase 1~7 eligible 43개 전건과 golden/negative browser journey가 모두 PASS일 때만 가능하다.
