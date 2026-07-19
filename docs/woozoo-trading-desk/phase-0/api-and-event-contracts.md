# P0-07 Versioned API·Event 계약

- 상태: `POLICY_APPROVED — consumer review/evidence digest pending`
- 계약 major: HTTP `/api/v1`, event envelope `woozoo.event/v1`
- 제품 Phase: `0` (OpenAPI/JSON Schema, generated binding, route, broker를 만들지 않는 문서 설계)
- 표현: 모든 가격·수량·금액·수수료·PnL·노출은 Decimal string, 모든 시각은 UTC ISO-8601 (`Z`)이다.

## 1. 계약 결정

| ID | 결정 | 이유 | 대안과 기각 이유 |
|---|---|---|---|
| C-01 | HTTP major는 path에, event major는 envelope와 type suffix에 명시한다. | breaking change를 소비자가 명시적으로 선택하게 한다. | unversioned payload는 producer-consumer drift를 숨긴다. |
| C-02 | Query는 read-only `GET`, Command는 action resource `POST`로 분리한다. | retry·권한·감사·idempotency 의미가 선명하다. | CRUD PATCH로 승인/Risk/Kill 전이를 표현하면 불변식 우회가 쉽다. |
| C-03 | 모든 command는 `Idempotency-Key`와 canonical request hash를 요구한다. | 중복 click/retry/restart에서 effect를 한 번으로 제한한다. | browser-generated timestamp만으로 dedupe하면 재시도와 새 명령을 구분하지 못한다. |
| C-04 | mutable aggregate command는 `If-Match`와 body `expected_version`을 요구한다. | stale 승인·Kill recovery·cancel race를 거절한다. | last-write-wins는 승인 후 상태 악화나 terminal 역전을 숨긴다. |
| C-05 | event는 Postgres outbox에서만 발행하고 consumer inbox와 domain effect를 같은 transaction에 commit한다. | at-least-once delivery에서도 effect를 exactly-once에 가깝게 만든다. | Redis pub/sub ack를 권위로 쓰면 유실 복구가 불가능하다. |
| C-06 | finance/safety command schema는 closed이며 unknown field를 거절한다. | 숨은 default와 미래 field를 잘못 해석하지 않는다. | 무조건 unknown ignore는 수량·승인 필드를 누락시킬 수 있다. |
| C-07 | API query response만 optional additive field를 허용하고, command/event data 변경은 명시적 schema version을 올린다. | read 호환성과 상태변경 안전을 분리한다. | 모든 변경을 minor로 처리하면 오래된 consumer가 안전 의미를 놓친다. |

## 2. HTTP v1 공통 규약

### 2.1 Request headers

| Header | Query | Command | 계약 |
|---|---|---|---|
| `X-Request-Id` | 권고 | 필수 | UUID; 없으면 query에서 서버 생성 가능, command는 client가 고정 |
| `X-Correlation-Id` | 선택 | 필수 | 전체 Evidence→Ledger trace ID |
| `Idempotency-Key` | 금지/불필요 | 필수 | scope 내 1~128자; 같은 key+다른 canonical body는 `IDEMPOTENCY_CONFLICT` |
| `If-Match` | 해당 projection query 후 | mutable command 필수 | quoted integer ETag, 예: `"7"`; 없으면 `PRECONDITION_REQUIRED` |
| `Content-Type` | 없음 | 필수 | `application/json`; command schema unknown field 거절 |

모든 command body에도 `command_id`, `expected_version`, `occurred_at`가 있다. `occurred_at`는 감사 시각이지 만료 검사의 권위 clock을 client에 주는 필드가 아니다. 서버는 injectable UTC clock으로 TTL/current state를 판정한다.

### 2.2 성공 envelope

```json
{
  "api_version": "v1",
  "request_id": "018f6f5d-1111-7aaa-8111-111111111111",
  "correlation_id": "018f6f5d-2222-7aaa-8222-222222222222",
  "served_at": "2026-07-19T00:00:00.000Z",
  "data": {},
  "meta": {
    "resource_version": "7",
    "next_cursor": null
  }
}
```

`served_at`는 query 관측 시각이며 금융 계산 input으로 사용하지 않는다. Decimal JSON number는 금지하고 `"125000.00000000"`처럼 string을 사용한다.

### 2.3 error envelope

```json
{
  "api_version": "v1",
  "request_id": "018f6f5d-1111-7aaa-8111-111111111111",
  "correlation_id": "018f6f5d-2222-7aaa-8222-222222222222",
  "served_at": "2026-07-19T00:00:00.000Z",
  "error": {
    "code": "STALE_RESOURCE_VERSION",
    "message": "The aggregate changed; reload before retrying.",
    "retryable": false,
    "details": {
      "aggregate_type": "kill_switch",
      "aggregate_id": "global",
      "expected_version": "6",
      "actual_version": "7"
    }
  }
}
```

오류에는 secret, auth header, provider response 원문, SQL, stack trace를 넣지 않는다. 고정 code 후보:

| HTTP | Code | 의미 |
|---:|---|---|
| 400 | `SCHEMA_INVALID`, `DECIMAL_INVALID`, `UTC_TIMESTAMP_REQUIRED` | closed schema/표현 위반 |
| 401/403 | `ACTOR_UNAUTHENTICATED`, `ACTOR_FORBIDDEN` | actor/authz 실패 |
| 404 | `RESOURCE_NOT_FOUND` | 존재하지 않거나 공개 금지 |
| 409 | `IDEMPOTENCY_CONFLICT`, `INVALID_STATE_TRANSITION`, `HASH_MISMATCH`, `DUPLICATE_EFFECT`, `AUTHORIZATION_ALREADY_ATTEMPTED` | 동일 key 다른 body, 불변식 충돌 또는 이미 성공/blocked된 authorization 재사용 |
| 412 | `STALE_RESOURCE_VERSION` | `If-Match`/expected version 불일치 |
| 428 | `PRECONDITION_REQUIRED` | 필요한 idempotency/version/hash 누락 |
| 422 | `RISK_DENIED`, `ORDER_COMMAND_REJECTED`, `APPROVAL_EXPIRED`, `APPROVAL_REVOKED`, `KILL_SWITCH_ACTIVE`, `DATA_UNHEALTHY`, `LEDGER_UNHEALTHY` | 의미 검증의 결정적 거절; Paper guard 실패는 order가 아니라 immutable command receipt를 반환 |
| 503 | `DEPENDENCY_UNAVAILABLE`, `HOLD_REQUIRED` | 비권위 dependency 장애; fail-closed |

## 3. Command / Query catalog

catalog의 creation phase 전에는 route/schema/placeholder가 존재하면 안 된다. creation과 activation이 분리된 위험 command는 creation phase에 closed consumer contract와 test binding만 허용하고 activation phase 전 production route/handler/scheduler binding은 존재하면 안 된다. Phase 1~7 catalog에는 Testnet/order-private route가 없다.

## 2.3 Local operator authentication and session contract

Phase 5에 auth schema와 RED contract를 만들고 Phase 7 browser activation에서만 browser route를 연다. 유일한 local operator identity는 server-created `operator-local-1`이며, browser body, query, header의 `actor_id`는 무시하지 않고 closed-schema 위반으로 거절한다. command/event audit의 `actor_id`는 검증된 server session에서만 주입한다.

- bootstrap plaintext password는 repository, DB, fixture, prompt, log, trace에 저장하지 않는다. 서버는 로컬 secret path에서 한 번 읽어 Argon2id PHC verifier를 만들거나 검증하며, DB에는 verifier와 operator ID만 둔다.
- `POST /api/v1/session/login`은 same-origin HTTPS에서만 동작한다. 성공 시 기존 session을 폐기하고 cryptographically random opaque session ID를 새로 발급한다. DB에는 session ID의 SHA-256 digest, actor, issued/last-seen/idle-expiry/absolute-expiry/revoked timestamp만 저장한다.
- browser에는 `__Host-woozoo_session` cookie만 `HttpOnly; Secure; SameSite=Strict; Path=/`로 준다. idle TTL은 30분, absolute TTL은 8시간이며 매 인증 요청에서 last-seen만 갱신하고 absolute TTL은 연장하지 않는다. `POST /api/v1/session/logout`은 현재 session을 즉시 revoke하고 cookie를 만료시킨다.
- `GET /api/v1/session`만 인증된 HTTPS JSON body의 `csrf_token` field로 one-time non-cookie CSRF token을 반환하고 `Cache-Control: no-store`를 준다. token은 random opaque value이며 서버는 `(session_id_digest, csrf_token_digest, issued_at, expires_at=issued_at+10m, consumed_at)`만 저장한다. 모든 browser state-changing command는 `X-CSRF-Token`과 exact same-origin `Origin`을 요구하고, valid token은 성공·business rejection·guard block 어느 outcome이든 한 번 소비된다. missing/mismatch/cross-session/replay/foreign/null origin, expired/revoked/rotated session은 effect 0과 `ACTOR_UNAUTHENTICATED` 또는 `CSRF_INVALID`다.
- local development도 plaintext HTTP login을 허용하지 않는다. `env:init`은 secretless local HTTPS loopback certificate workflow만 안내하고, browser E2E는 `https://localhost`에서 실행한다. non-localhost HTTPS response는 `Strict-Transport-Security: max-age=31536000; includeSubDomains`를 보낸다.
- password, verifier, session ID, Cookie/Authorization header의 raw value는 response, audit payload, log, trace, error, telemetry, fixture에 절대 기록하지 않는다. raw CSRF token의 유일한 허용 sink는 위 authenticated `GET /api/v1/session`의 HTTPS no-store response body이며, 그 밖의 response·audit·log·trace·error·telemetry·fixture에는 기록하지 않고 서버에도 digest만 저장한다. 내부 workflow는 browser session을 사용하지 않고 별도 allowlisted service principal만 사용한다.

### 3.1 Queries

| Creation / activation Phase | Method/path | Owner | 결과 |
|---:|---|---|---|
| 5 / 7 | `GET /api/v1/session` | control-api | authenticated actor/idle·absolute expiry와 10분 single-use non-cookie `csrf_token`만 HTTPS no-store body로 반환; raw session 0 |
| 1 | `GET /api/v1/health` | control-api | process/DB/Redis 비권위 상태; secret 없음 |
| 2 | `GET /api/v1/markets/{symbol}/status` | market-data-worker (control-api projection) | price, event/received time, quality, watermark |
| 3 | `GET /api/v1/evidence/{evidence_id}` | evidence-worker (control-api projection) | immutable Evidence와 provenance links |
| 6 | `GET /api/v1/analysis-runs/{run_id}` | agent-orchestrator (control-api projection) | report/Proposal/HOLD 상태 |
| 5 / 7 | `GET /api/v1/risk-decisions/{decision_id}` | risk-engine (control-api projection) | verdict/reason/policy/input hash와 full canonical Paper order preview+hash |
| 5 / 7 | `GET /api/v1/proposals/{proposal_id}/approval-view` | control read model | Proposal+allowed Risk+current safety, full canonical `paper_order_preview`+hash, TTL, approval/authorization projection |
| 4 / 7 | `GET /api/v1/paper/orders/{order_id}` | paper-engine (control-api projection) | order projection, fills, resource version |
| 4 / 7 | `GET /api/v1/paper/command-receipts/{command_id}` | paper-engine (control-api projection) | accepted/rejected/unknown command 결과; rejected에는 order ID가 없음 |
| 4 / 7 | `GET /api/v1/paper/portfolio` | paper-engine (control-api projection) | cash/position/PnL/ledger watermark |
| 1 | `GET /api/v1/audit/events` | control read model | cursor 기반 correlation timeline |
| 5 / 7 | `GET /api/v1/ops/kill-switch` | risk-engine (control-api projection) | active state, reason, version, recovery eligibility |

### 3.2 Commands

| Creation / activation Phase | Method/path | Actor/producer | 소비 owner | 필수 결속/효과 |
|---|---|---|---|---|
| 5 / 7 | `POST /api/v1/session/login` | local browser | control-api | Argon2id verify, HTTPS-only, session rotation; actor ID body 0 |
| 5 / 7 | `POST /api/v1/session/logout` | authenticated browser | control-api | current server-side session revoke와 Secure cookie expiry |
| 3 | `POST /api/v1/commands/evidence-snapshots` | internal scheduler/operator | evidence-worker | symbol, `as_of`, `knowledge_cutoff`, watermark recipe |
| 6 | `POST /api/v1/commands/analysis-runs` | operator | agent-orchestrator | immutable Evidence ID/digest; 실패는 HOLD |
| 5 / 7 | `POST /api/v1/commands/risk-evaluations` | internal workflow/operator | risk-engine | canonical Proposal, portfolio, data, preview, policy/calculator, Kill, reconciliation, clock, duplicate/exposure snapshots → `risk_input_digest`; 승인 불필요 |
| 5 / 7 | `POST /api/v1/commands/paper-approvals` | authenticated operator | control-api | allowed RiskDecision + Proposal + exact `paper_order_preview_hash`; approve/reject |
| 5 / 7 | `POST /api/v1/commands/paper-approval-revocations` | authenticated operator | control-api | approval ID/hash/version/reason |
| 5 / 7 | `POST /api/v1/internal/commands/paper-authorizations` | deterministic workflow only | risk-engine | approval+Proposal+Risk+preview hash와 current Kill/data/ledger recheck; immutable authorization append |
| 4 / 7 | `POST /api/v1/internal/commands/paper-orders` | paper authorization workflow only | paper-engine | authorization first attempt를 성공/blocked 모두 영구 소비; LIMIT BUY/SELL; Decimal strings |
| 4 / 7 | `POST /api/v1/commands/paper-order-cancellations` | authenticated operator/Kill workflow | paper-engine | order ID, reason, expected order version |
| 5 / 7 | `POST /api/v1/commands/kill-switch-activations` | authenticated operator/safety service | risk-engine | scope, reason, expected version; new commands 0 |
| 5 / 7 | `POST /api/v1/commands/kill-switch-recoveries` | authenticated operator only | risk-engine | incident evidence, manual confirmation, expected version |

`/internal/` command는 browser와 AI network policy에서 접근할 수 없다. Control API가 browser command를 받더라도 internal authorization/order schema를 그대로 proxy하지 않는다.

`x / y`로 표기한 행은 x가 creation, y가 activation이다. x에서는 consumer contract/schema와 RED test만 생성할 수 있고 y 전에는 route registration, production handler binding, scheduler ingress와 production aggregate 생성이 모두 `OFF`다. Phase 5는 auth/Risk/approval/authorization/Kill schema와 RED contract만, Phase 6은 test namespace fixture 기반 Proposal→Risk chain 검증만 허용한다. login/session/approval/authorization/Paper production handlers는 Phase 7에서 함께 활성화한다. P4 Paper는 P5 Authorization fixture, P5 Risk는 P6 Proposal fixture를 test namespace에서만 사용하며 미래 producer FK나 production row를 만들지 않는다. fixture로 만든 RiskDecision/Authorization/PaperOrder를 production query나 approval UI에 노출하는 것은 금지한다.

Evidence snapshot command의 `as_of`와 `knowledge_cutoff`는 모두 필수 UTC 값이다. scheduler 또는 replay harness가 둘을 명시하고 Evidence worker는 `event_time <= as_of AND received_at <= knowledge_cutoff`를 적용한다. 누락값을 server wall clock으로 보충하거나 한 값을 다른 값으로 대체하면 `SCHEMA_INVALID`다.

## 4. Idempotency와 optimistic concurrency

1. 공유 정본은 `command-request-hash.v1`이다. canonical request hash는 `contract_version`, uppercase method, 정규화된 canonical path, authenticated `actor_id`와 authorization scope 또는 internal service principal, closed-schema body, referenced domain hashes를 이 순서의 closed object로 포함한다. `scope`는 DB UNIQUE namespace일 뿐 actor/path를 대신하지 않는다. request/correlation ID, 전달 header, 수신 시각, retry count 같은 volatile transport metadata는 제외한다.
2. `(scope, Idempotency-Key)`가 처음이면 command receipt를 domain transaction과 함께 commit한다.
3. 같은 key+같은 hash 재요청은 최초 status/body hash를 반환하고 event/effect를 추가하지 않는다.
4. 같은 key+다른 hash는 HTTP 409 `IDEMPOTENCY_CONFLICT`다.
5. `If-Match`와 `expected_version`이 다르거나 current aggregate version과 다르면 effect 0, HTTP 412다.
6. `approval_nonce`는 사람의 approve/reject command를 idempotently 식별하고 정확한 Proposal/Risk/preview 결정을 재현하는 nonce다. 여러 실행에 쓰는 토큰도, 실행 소비 상태도 아니다.
7. `authorization_nonce`만 execution single-use다. paper-engine은 risk-owned authorization을 UPDATE하지 않고 `authorization_id UNIQUE`인 immutable attempt/consumption receipt를 order/ledger 또는 rejection receipt와 같은 paper transaction에 append한다. 첫 attempt가 guard 실패나 version drift여도 `BLOCKED`로 영구 소비되며 재사용은 0이다.
8. timeout은 unknown outcome이다. client는 같은 key와 `client_order_id`로 command receipt/order를 query/retry하고 새 authorization, key 또는 order ID로 즉시 replacement하지 않는다.

## 5. Paper workflow ordering 계약

정본 순서:

```text
EvidenceSnapshot
  → TradeProposal
  → RiskDecision(allowed)
  → PaperApproval(approved by authenticated operator)
  → PaperExecutionAuthorization(current-state recheck)
  → PaperOrder
```

전제와 불변식:

- Risk evaluation은 approval 존재를 요구하지 않는다.
- Risk input의 유일한 동일성 권위는 `risk_input_digest`다. 이 digest는 Proposal full payload/hash, portfolio, data/Evidence, exact order preview, policy/calculator, Kill, reconciliation, deterministic decision clock, duplicate/cooldown과 existing exposure/order snapshot 전부의 canonical hash다.
- Approval은 정확한 `proposal_hash`, `risk_decision_hash`, `risk_policy_version`, `paper_order_preview_hash`, actor, `approved_at`, `expires_at`, expected versions에 결속한다.
- Authorization은 approval 뒤에만 생성하며 기존 Risk hash와 현재 data quality, Kill Switch, ledger/reconciliation health, fresh-book observed spread와 limit 대비 expected slippage를 같은 policy version으로 다시 확인한다. execution-quality guard가 drift/limit 초과면 `EXECUTION_QUALITY_DRIFT`로 effect 0이고 새 Proposal/Risk/Approval cycle을 요구한다.
- 재검증은 승인 뒤 새로운 RiskDecision을 소급 생성하는 것이 아니다. 상태가 바뀌면 기존 approval로 실행하지 않고 새 Proposal/Risk/Approval cycle을 요구한다.
- approval 전 Risk 없이 승인하거나 `Approval → 새 RiskDecision → Authorization` 순서를 허용하지 않는다.
- guard 실패 또는 expected-version drift는 order aggregate를 만들지 않는다. paper-engine은 immutable `REJECTED` command receipt와 `BLOCKED` authorization attempt event를 같은 local transaction에 남기며, UI는 이를 주문 history가 아니라 command/activity timeline의 “주문 미생성” 결과로 표시한다.
- delegated 요청의 `Paper approval→Risk→PaperExecutionAuthorization` 문구는 기존 MVP 계약과 문자상 충돌할 수 있었다. 오케스트레이터가 기존 권위 MVP 계약을 적용해 `Risk→Approval→Authorization`으로 판정했으므로, 이 문서는 이를 `RESOLVED_BY_AUTHORITATIVE_MVP_CANDIDATE`로 기록한다. affected builders는 같은 순서를 사용하고 safety QA가 교차 확인한다.

## 6. Approval view와 Paper approval command

### 6.1 Approval-view closed response

`GET /api/v1/proposals/{proposal_id}/approval-view`는 아래 closed `data` union을 반환한다. 최상위 `approval_view_status`는 `PENDING_RISK|PENDING_APPROVAL|READY|APPROVED|AUTHORIZATION_ISSUED|BLOCKED|INVALID` 중 하나이고 `approval_action_allowed`는 explicit boolean이다. `INVALID`는 projection integrity failure이고 안정된 `reason_codes`가 비어 있지 않아야 하며 `paper_order_preview`, `paper_order_preview_hash`, `paper_approval`, `paper_execution_authorization`은 모두 `null`, action은 false다. `BLOCKED`도 action=false다. `READY`만 approved Risk와 complete preview/hash를 갖고 action=true다. `APPROVED`와 `AUTHORIZATION_ISSUED`는 action=false다. 예시는 authorization 발급 뒤 상태다.

```json
{
  "api_version": "v1",
  "request_id": "018f6f5d-6100-7aaa-8666-666666666666",
  "correlation_id": "018f6f5d-2222-7aaa-8222-222222222222",
  "served_at": "2026-07-19T00:00:01.000Z",
  "data": {
    "approval_view_status": "AUTHORIZATION_ISSUED",
    "approval_action_allowed": false,
    "reason_codes": [],
    "proposal_id": "018f6f5d-4000-7aaa-8444-444444444444",
    "proposal_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "risk_decision_id": "018f6f5d-5000-7aaa-8555-555555555555",
    "risk_decision_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "risk_input_digest": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
    "risk_verdict": "ALLOWED",
    "risk_reason_codes": [],
    "risk_policy_version": "risk-v1",
    "paper_order_preview": {
      "schema_version": "paper-order-preview.v1",
      "paper_account_id": "paper-default",
      "symbol": "BTCUSDT",
      "side": "BUY",
      "order_type": "LIMIT",
      "quantity": "0.01000000",
      "limit_price": "65000.00000000",
      "time_in_force": "GTC",
      "notional_quote": "650.00000000",
      "fee_asset": "USDT",
      "fee_rate": "0.00100000",
      "estimated_fee": "0.65000000",
      "max_debit": "650.65000000",
      "fill_policy_version": "paper-fill-v1",
      "fee_policy_version": "fee-v1",
      "spread_bps": "1.25000000",
      "expected_slippage_bps": "2.00000000"
    },
    "paper_order_preview_hash": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
    "approval_policy": {
      "ttl_seconds": "300",
      "expires_at": "2026-07-19T00:05:00.000Z"
    },
    "current_safety": {
      "data_status": "HEALTHY",
      "kill_switch_active": false,
      "reconciliation_status": "HEALTHY"
    },
    "paper_approval": {
      "paper_approval_id": "018f6f5d-6200-7aaa-8777-777777777777",
      "status": "APPROVED",
      "approval_nonce": "eeeeeeee-eeee-7eee-8eee-eeeeeeeeeeee",
      "approved_at": "2026-07-19T00:00:00.000Z",
      "expires_at": "2026-07-19T00:05:00.000Z"
    },
    "paper_execution_authorization": {
      "authorization_id": "018f6f5d-6300-7aaa-8888-888888888888",
      "authorization_nonce": "99999999-9999-7999-8999-999999999999",
      "status": "ISSUED",
      "expires_at": "2026-07-19T00:05:00.000Z",
      "reason_codes": []
    }
  },
  "meta": {"resource_version": "3", "next_cursor": null}
}
```

`paper_approval.status`는 `APPROVED|REJECTED|EXPIRED|REVOKED`, authorization status는 `ISSUED|BLOCKED|CONSUMED|EXPIRED|REVOKED|INVALIDATED`의 closed enum이다. `approval_nonce`는 사람 결정 identity이고 `authorization_nonce`는 실행 single-use임을 projection에서도 섞지 않는다. control read model은 `risk.decision.recorded.v1`의 full canonical preview와 hash를 함께 투영하며, 둘 중 하나만 갱신하거나 stale preview를 최신 hash와 조합하면 closed `INVALID` branch와 `PREVIEW_HASH_MISMATCH`를 내고 승인 동작을 차단한다. 누락된 data field, 알 수 없는 status 또는 status별 허용되지 않은 null/non-null 조합은 response를 만들지 않고 `SCHEMA_INVALID`로 fail-closed한다.

브라우저는 quantity, price, notional, fee, max debit, spread 또는 slippage를 재계산·재가격하지 않는다. 서버의 Decimal 문자열을 그대로 표시하고 승인 command에는 같은 response의 `paper_order_preview_hash`와 resource version만 제출한다. contract test는 shared canonicalizer로 response preview를 다시 hash하고 Risk hash와의 동일성을 검증한다. browser E2E는 화면에 표시된 각 preview field가 그 response와 같고 실제 제출 hash가 같은 response hash인지 검증한다.

### 6.2 Paper approval command

```http
POST /api/v1/commands/paper-approvals
X-Request-Id: 018f6f5d-3000-7aaa-8333-333333333333
X-Correlation-Id: 018f6f5d-2222-7aaa-8222-222222222222
Idempotency-Key: approve-proposal-018f6f5d
If-Match: "3"
Content-Type: application/json
```

```json
{
  "command_id": "018f6f5d-3000-7aaa-8333-333333333333",
  "expected_version": "3",
  "occurred_at": "2026-07-19T00:00:00.000Z",
  "decision": "APPROVE",
  "proposal_id": "018f6f5d-4000-7aaa-8444-444444444444",
  "proposal_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "risk_decision_id": "018f6f5d-5000-7aaa-8555-555555555555",
  "risk_decision_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "risk_policy_version": "risk-v1",
  "paper_order_preview_hash": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
  "expires_at": "2026-07-19T00:05:00.000Z",
  "reason": "Reviewed proposal, risk reasons, and current Paper-only scope."
}
```

성공은 `202 Accepted`와 `paper_approval_id`, `approval_hash`, `approval_nonce`, `resource_version`을 반환한다. `approval_hash`의 canonical payload에는 `paper_order_preview_hash`가 필수다. `expires_at`의 최대 TTL은 승인된 5분이며 server policy 밖이면 422다. command와 event schema는 closed이므로 preview hash 누락·unknown field·Risk에 결속된 preview와 불일치는 effect 0이다.

## 7. Query example: portfolio

```json
{
  "api_version": "v1",
  "request_id": "018f6f5d-6000-7aaa-8666-666666666666",
  "correlation_id": "018f6f5d-2222-7aaa-8222-222222222222",
  "served_at": "2026-07-19T00:01:00.000Z",
  "data": {
    "portfolio_id": "paper-default",
    "as_of": "2026-07-19T00:00:59.000Z",
    "cash": [{"commodity": "USDT", "available": "7500.00000000", "held": "0.00000000"}],
    "positions": [{"commodity": "BTC", "quantity": "0.01000000", "available": "0.01000000", "held": "0.00000000"}],
    "realized_pnl": "0.00000000",
    "unrealized_pnl": "25.50000000",
    "valuation_commodity": "USDT",
    "valuation_source": "book-ticker-v1",
    "valuation_as_of": "2026-07-19T00:00:58.000Z",
    "ledger_watermark": "1842",
    "reconciliation_status": "HEALTHY"
  },
  "meta": {"resource_version": "19", "next_cursor": null}
}
```

## 8. Event envelope v1

```json
{
  "spec_version": "woozoo.event/v1",
  "event_id": "018f6f5d-7000-7aaa-8777-777777777777",
  "event_type": "paper.approval.recorded.v1",
  "event_version": 1,
  "occurred_at": "2026-07-19T00:00:00.010Z",
  "published_at": "2026-07-19T00:00:00.020Z",
  "producer": "control-api",
  "subject": "paper_approval/018f6f5d-8000-7aaa-8888-888888888888",
  "partition_key": "proposal/018f6f5d-4000-7aaa-8444-444444444444",
  "correlation_id": "018f6f5d-2222-7aaa-8222-222222222222",
  "causation_id": "018f6f5d-3000-7aaa-8333-333333333333",
  "aggregate": {
    "type": "paper_approval",
    "id": "018f6f5d-8000-7aaa-8888-888888888888",
    "version": "1"
  },
  "data": {
    "decision": "APPROVE",
    "proposal_id": "018f6f5d-4000-7aaa-8444-444444444444",
    "proposal_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "risk_decision_id": "018f6f5d-5000-7aaa-8555-555555555555",
    "risk_decision_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "risk_policy_version": "risk-v1",
    "paper_order_preview_hash": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
    "approved_at": "2026-07-19T00:00:00.000Z",
    "expires_at": "2026-07-19T00:05:00.000Z",
    "approval_nonce": "eeeeeeee-eeee-7eee-8eee-eeeeeeeeeeee",
    "approval_hash": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
  }
}
```

HTTP request body에는 `actor_id`, `approved_at`, `expires_at`, `approval_hash`를 받지 않는다. control-api는 authenticated server session에서 actor를, 승인된 policy와 server clock에서 timestamp/expiry를, canonical command에서 approval hash를 생성한 뒤 immutable event에만 기록한다. 호출자는 `approval_view_status=READY`, `approval_action_allowed=true`, response의 preview hash와 resource version을 제출해야 하며, 다른 branch에서의 request는 effect 0이다.

`published_at`은 delivery metadata이며 결정 시간으로 쓰지 않는다. producer는 동일 event ID/payload hash를 재전달할 수 있고 consumer는 `(consumer_name,event_id)` inbox UNIQUE로 dedupe한다. 같은 event ID의 다른 payload hash는 quarantine+alert다.

`risk.decision.recorded.v1`의 closed `data`는 `risk_input_digest`, `decision_hash`, verdict, ordered reason codes, `proposal_hash`, `portfolio_snapshot_hash`, `data_state_hash`, full canonical `paper_order_preview`와 `paper_order_preview_hash`, policy/calculator hashes, Kill version, reconciliation checkpoint hash, decision clock, duplicate/exposure snapshot hash를 포함한다. `risk_input_digest`는 이 하위 입력 전부를 canonicalize한 DB UNIQUE authority이며 consumer가 일부 필드 조합으로 입력 동일성을 다시 정의하면 안 된다. control read model은 preview payload와 hash를 하나의 event version에서 원자 투영한다.

`paper.authorization.issued.v1`은 `authorization_id`, `authorization_nonce`, approval/Proposal/Risk/preview hashes와 expiry를 포함한다. risk-owned 원본은 immutable이다. paper-owned `paper.authorization.attempted.v1`은 같은 `authorization_id`, nonce, command/client order ID, request hash, outcome=`CONSUMED_ORDER_CREATED|BLOCKED`와 reason을 포함하며 `authorization_id` first-attempt가 정확히 하나임을 소비자가 검증한다.

## 9. Producer-consumer map

| Event v1 | Producer | Required consumers | 계약 결과 |
|---|---|---|---|
| `market.raw.appended.v1` | market-data-worker | market-data normalization, audit | raw durability 뒤 normalization 허용 |
| `market.normalized.recorded.v1` | market-data-worker | evidence-worker | Decimal/time/quality provenance 입력 |
| `market.quality.changed.v1` | market-data-worker | evidence-worker, risk-engine, control-api read model | degraded/stale/invalid이면 신규 decision 차단 |
| `evidence.snapshot.created.v1` | evidence-worker | agent-orchestrator, audit | immutable digest와 provenance 제공 |
| `analysis.run.completed.v1` | agent-orchestrator | control-api read model, audit | accepted reports 또는 HOLD |
| `trade.proposal.created.v1` | agent-orchestrator | risk-engine, control-api read model | Risk 평가 후보; order effect 없음 |
| `risk.decision.recorded.v1` | risk-engine | control-api approval/read model, audit | allowed만 approval 가능 |
| `paper.approval.recorded.v1` | control-api | risk-engine authorization evaluator, audit | current-state recheck 시작 가능 |
| `paper.approval.revoked.v1` | control-api | risk-engine, paper-engine, audit | unused authorization 무효화 |
| `paper.authorization.issued.v1` | risk-engine | paper-engine, audit | single-use Paper command만 허용 |
| `paper.authorization.issuance-blocked.v1` | risk-engine | control-api read model, audit | Authorization 미발급, order effect 0, reason 표시 |
| `paper.authorization.attempted.v1` | paper-engine | risk-engine projection, control-api read model, audit | `authorization_id` first-attempt UNIQUE; outcome이 성공이면 order 생성, blocked이면 guard/version drift로 order 0·nonce 영구 invalidated; risk-owned 원본 변경 0 |
| `paper.order-command.rejected.v1` | paper-engine | control-api read model, audit | order aggregate/hold/fill/ledger 0; immutable command receipt와 stable reason 표시 |
| `paper.order.accepted.v1` | paper-engine | control-api read model, audit | LIMIT Paper lifecycle 시작 |
| `paper.order.partially-filled.v1` | paper-engine | paper ledger/portfolio, control-api read model, audit | unique fill만 반영 |
| `paper.order.filled.v1` | paper-engine | paper portfolio, control-api read model, audit | terminal projection |
| `paper.order.cancelled.v1` | paper-engine | paper portfolio, control-api read model, audit | prior fills·fees 보존 |
| `ledger.transaction.posted.v1` | paper-engine | paper portfolio/reconciliation, audit | commodity별 균형 transaction |
| `kill-switch.activated.v1` | risk-engine | all command producers, paper-engine, control-api/UI/audit | 신규 effect 0, 열린 Paper order audited cancel |
| `kill-switch.recovery-confirmed.v1` | risk-engine | command producers, control-api/UI/audit | authenticated manual recovery 후에만 해제 |

한 event에 복수 consumer가 있어도 consumer별 inbox와 local transaction을 사용한다. consumer 순서는 partition key의 aggregate ordering만 보장 대상으로 삼고, 전역 순서를 가정하지 않는다. causation/hash/version 전제 미충족은 effect 0과 quarantine이다.

## 10. Kill Switch command/event 계약

Activation command 필수값은 `scope`, `reason_code`, `reason`, `expected_version`, `actor_id 또는 authenticated safety-service identity`, `observed_at`이다. 성공 시 risk-engine은 shared authoritative `kill_switch_state` barrier row를 exclusive lock하고 새 version의 `active=true`, activation event, audit와 risk outbox를 먼저 한 risk-owned transaction으로 commit한다. 모든 paper create/fill transaction은 같은 barrier row/version을 lock/check한다. paper transaction이 먼저 lock했다면 activation이 기다리므로 그 effect는 activation 이전에 commit되고, activation이 먼저 commit했다면 기다리던 create/fill은 active version을 보고 effect 0이다. activation commit 뒤 create/fill commit은 허용하지 않는다.

열린 주문 cancellation은 activation transaction과 원자적이라고 주장하지 않는다. `kill-switch.activated.v1`을 paper-engine이 inbox-dedupe한 뒤 `(activation_event_id,batch_key)` UNIQUE인 idempotent saga/batch로 처리한다. 각 batch는 order/cancel event/hold release/ledger/outbox를 paper-owned local transaction에 commit하고 중단 시 같은 key로 재개한다. cancellation 완료 전에도 barrier는 이미 active라 신규 create/fill은 0이다.

Recovery command는 authenticated operator만 제출하며 `activation_event_id`, `incident_resolution_ref`, `reconciliation_status=HEALTHY`, `data_status=HEALTHY`, `ledger_status=HEALTHY`, `expected_version`, reason을 요구한다. timer, AI, process restart, Redis expiry는 recovery command를 만들 수 없다. activation/recovery 중 stale version이면 effect 0이다.

## 11. Phase 1~7 Testnet/private zero 계약

- HTTP catalog에 Testnet submit/cancel/query/user-data/account/credential route가 0개다.
- event catalog에 external order, Testnet account/balance/user-data event가 0개다.
- schema vocabulary에 API key, secret, signature, signed timestamp, listen key, Testnet enabled/URL이 0개다.
- public market events의 `source`는 approved `market-data-read` allowlist 값만 허용한다.
- Phase 8 계약은 Phase 8 승인 뒤 별도 major-compatible namespace와 gateway inbound schema로 처음 설계·추가한다. 이 문서의 Paper approval/authorization을 외부 주문 승인으로 재사용하지 않는다.

## 12. Compatibility, deprecation, consumer rollout

| 변경 | 규칙 |
|---|---|
| Query response optional field 추가 | v1에서 허용; 의미·default가 기존 필드를 바꾸면 안 됨 |
| Command request field·enum·검증 의미 변경 | closed schema이므로 새 command contract/version 필요 |
| Event data field·enum·ordering 의미 변경 | 새 `event_type ...v2`와 schema 필요; 기존 payload 변조 금지 |
| Error code 추가 | v1 허용; client는 unknown code를 fail-closed generic error로 처리 |
| 필드 삭제/이름·Decimal scale 의미 변경 | breaking; API major 또는 event version 증가 |

Producer는 consumer compatibility manifest가 전건 PASS하기 전 새 event version을 발행하지 않는다. 필요한 경우 v1/v2를 **각각 독립 event ID와 명시적 causation**으로 bounded dual-publish하고, 동일 business effect로 두 번 소비되지 않도록 consumer migration key를 정의한다.

Deprecation은 공지→consumer inventory→shadow validation→dual-read/publish→전건 cutover evidence→제거 순서다. 최소 지원 기간은 승인된 `2개 제품 Phase 또는 90일 중 긴 기간`이다. 안전/금융 contract는 consumer 미확인 상태에서 sunset하지 않는다.

## 13. 승인된 계약 정책과 충돌

| ID | 승인/충돌 | 채택값 | 구현 전 남은 조건 |
|---|---|---|---|
| D-CONTRACT-01 | Paper approval TTL 최대값 | 5분, one-time authorization | AUTH oracle는 Phase 5 구현 전 RED 계약 |
| D-CONTRACT-02 | deprecation 최소 기간 | 2 Phase 또는 90일 중 긴 기간 | compatibility evidence는 contract 구현 뒤 필요 |
| D-CONTRACT-03 | local actor 인증 방식 | single local operator의 Argon2id password verifier, server-side session, HttpOnly·Secure·SameSite=Strict cookie; bootstrap plaintext는 local secret path만 사용 | auth schema/RED는 Phase 5, login/approval route activation은 Phase 7; plaintext DB/log/trace/fixture 0 |
| D-CONTRACT-04 | session lifecycle/CSRF/TLS | login rotation, idle 30분/absolute 8시간, server-side revoke, exact Origin+non-cookie CSRF, HTTPS-only local loopback | actor ID client injection, plaintext login, expired/revoked session effect 0 |
| X-CONTRACT-01 | delegated 문구 `Approval→Risk→Authorization` vs 기존 MVP `Risk→Approval→Authorization` | `RESOLVED_BY_AUTHORITATIVE_MVP_CANDIDATE`: 기존 MVP 순서 유지; affected builders에 전달 | open conflict 아님; safety QA 교차 확인 전 최종 PASS 금지 |

## 14. P0-07 Acceptance 후보

- API/event v1 envelope, UTC/Decimal string, error, idempotency, optimistic concurrency가 정의된다.
- command/query와 browser/internal route가 분리되고 producer-consumer owner가 명시된다.
- Proposal→Risk→Approval→Authorization→Paper ordering과 hash/current-state 재검증이 일치한다.
- approval-view가 full canonical `paper_order_preview`와 hash를 원자 투영하고 브라우저 표시 payload·제출 hash·Risk preview hash가 일치한다. browser는 preview 금융 값을 계산하지 않는다.
- `paper_order_preview_hash`, approval/authorization nonce 의미, immutable authorization attempt/rejection event가 closed schema에서 일치한다.
- P4/P5 consumer contract creation, P6 fixture/test-namespace full-chain verification, P7 authenticated production activation을 분리하고 fixture가 production 권위를 만들지 않는다.
- Kill Switch activation/recovery가 versioned command/event로 fail-closed한다.
- Phase 1~7 Testnet/private API/event/config vocabulary가 0이다.
- compatibility/deprecation과 consumer rollout이 정의되고 contract tests가 breaking drift를 차단한다.
- 이 파일의 SHA-256·verdict가 final evidence manifest에 결속되고 X-CONTRACT-01 resolution을 safety QA가 확인한다.

## 다음 단계 참조

- safety QA는 이 문서와 `architecture-and-boundaries.md`, `data-model-and-retention.md`, MVP 계약의 ordering/hash/owner를 동시에 비교해야 한다.
- `verification-strategy.md`의 contract, SAFE-001~004, RISK-001~002, AUTH-001~002, KILL-001~002가 이 계약의 RED 명세다.
- D-CONTRACT-01~03은 `phase-0-approval-record.md`에 결속됐다. X-CONTRACT-01은 권위 MVP 계약으로 해결됐으며 final safety QA 확인이 남아 있다.
- Phase 1 승인 전 OpenAPI/JSON Schema, generated binding, route, event broker, test/fixture/scaffold를 만들지 않는다.
