# Phase 8 Implementation Contract

Status: active implementation contract
Branch: `codex/phase-8-testnet-gateway`
Exchange environment: `BINANCE_SPOT_TESTNET` only
Default: disabled and HOLD

## Outcome

Phase 8 adds a separate least-privilege Gateway and deterministic execution authority for Binance Spot Testnet. Phase 7 Paper authority remains unchanged. Testnet orders are real external Testnet effects but never real-money effects. Mainnet private/live, withdrawal, derivatives, leverage, short, fallback hosts, browser/AI Gateway access, and shared secrets remain structurally absent.

## Process and trust boundaries

```text
Korean browser UI
  -> same-origin Control API
  -> Postgres activation/approval/outbox authority
  -> deterministic Testnet execution worker
  -> authenticated versioned Gateway ingress
  -> exact Binance Spot Testnet allowlist

Binance User Data + allowlisted REST observations
  -> Gateway
  -> append-only observations
  -> deterministic reconciliation worker
  -> local order/account-generation/checkpoint authority
```

- The Control API never imports or calls a Gateway client.
- The browser never receives an exchange URL, account balance, raw Binance response, credential, signature, nonce, or Gateway route.
- AI processes have no Testnet contracts, DB role, network route, account/balance/approval/order tools, or credential environment.
- Gateway credentials are read only from two explicit Gateway-only file paths. Raw secret values and shared application environment variables are rejected.
- Gateway startup and every command independently validate environment, exact allowlist digest, configuration digest, internal caller, activation, Testnet barrier, reconciliation, and account generation.

## Capability contract

The exact host, paths, methods, order types, timing, User Data mechanism, and source revision are frozen in `binance-spot-testnet-source-lock.md`.

Order-effect commands are closed and versioned:

- `SUBMIT_LIMIT_GTC`: consumes one Testnet authorization and retains the full Proposal/Risk/approval/preview/account-generation binding.
- `CANCEL_EXISTING`: targets one known order and retains the original order command and approval binding. An operator cancel requires its own cancel intent; a safety cancel additionally binds a Testnet barrier reason. Neither creates a new order authorization.
- `QUERY_EXISTING`: targets the same stable Client Order ID and retains the original order binding. It cannot submit, replace, or mutate an order.

Account, open-order, trade, time, exchange-info, and User Data reconciliation traffic is a versioned `ObservationRequest`, not an order-effect command. It binds service identity, account binding, account generation, checkpoint, capability, idempotency key, and time bounds. It cannot carry order submission fields.

This distinction resolves the Phase state wording conservatively: every order-effect command preserves the complete approval chain, while non-effect reconciliation observations are separately authenticated and incapable of producing an order.

## Separate Testnet authority

Paper approval and `paper-global` Kill are not reinterpreted. Phase 8 creates:

- Testnet activation intent and version
- Testnet safety barrier, default `ACTIVE`
- Testnet order preview and approval input digest
- Testnet approval audit record and one-time authorization
- Testnet execution command and immutable receipt
- local Testnet order, observation, User Data event, and reconciliation checkpoint
- explicit account generation and reset confirmation

New Testnet effects require both the existing Paper/global safety condition and the dedicated Testnet barrier to permit execution. Testnet recovery may never weaken or recover the Paper Kill authority.

## Exact approval binding

The canonical approval input binds all of:

- environment `BINANCE_SPOT_TESTNET`
- opaque account binding ID and account generation
- Proposal ID/hash
- latest complete `ALLOWED` Risk Decision ID/hash/input digest
- risk policy and Testnet preview calculator versions
- Evidence ID/digest, data-state digest, `as_of`, and `knowledge_cutoff`
- exact `BTCUSDT|ETHUSDT`, `BUY|SELL`, `LIMIT`, `GTC`, Decimal quantity and Decimal limit price
- stable Client Order ID and Testnet preview digest
- actor/session/CSRF/origin bindings
- activation/configuration/allowlist/Testnet-barrier/Paper-Kill versions
- reconciliation and ledger checkpoint digests
- approval nonce, decision time, expiry, and revocation state

TTL is at most 300 seconds. The authorization nonce is separate, one-time, and consumed atomically when the first command is durably created. The browser submits only Proposal ID, decision, expected view version, preview digest, approval-input digest, and reason; all financial and security fields are re-derived by the server.

## Stable identity and unknown outcome

- Client Order ID is deterministic from the authorization and command identity, ASCII, and stable across restart/replay.
- Inbox, authorization consumption, command receipt, local order state, domain event, and outbox commit atomically in Postgres.
- Duplicate request/body replays the durable receipt. The same idempotency key with another body is rejected.
- A timeout, disconnect, HTTP 5xx, or Binance `-1007` is `UNKNOWN_OUTCOME`, never a rejection.
- UNKNOWN consumes no second authorization and creates no new Client Order ID. Only `QUERY_EXISTING` for the same Client Order ID and User Data reconciliation may resolve it.
- Resolution is `FOUND`, `REJECTED`, or `NOT_FOUND_CONFIRMED`. The latter requires bounded repeated authoritative observations and cannot be inferred from one missing response.

## Order and fill invariants

- Financial values are canonical Decimal strings; binary float is forbidden.
- Filled quantity is monotonic, non-negative, and never exceeds order quantity.
- Terminal order states do not regress. Duplicate or out-of-order events cannot add fills, ledger effects, or outbox events.
- Exchange order/trade observations alone do not authorize local ledger posting. Reconciliation must bind them to the known command, account generation, and checkpoint.
- Any unexplained order, fill, balance discontinuity, digest mismatch, or invariant failure activates the Testnet barrier and blocks new commands.

## Reset-aware reconciliation

- Every authority row and observation carries `account_generation`.
- Missing known orders plus account discontinuity, vanished history, or a reset marker produces `RESET_SUSPECTED`; it is never interpreted as fill, cancel, loss, or profit.
- `RESET_SUSPECTED` activates the Testnet barrier and transitions to `AWAITING_OPERATOR_CONFIRMATION` only after an authenticated, digest-bound confirmation of the exact checkpoint/version.
- Confirmation does not restore execution. A new account generation and a complete healthy reconciliation checkpoint are required before the Testnet barrier can be recovered.
- Old-generation events remain immutable and cannot mutate new-generation state.

## Browser boundary

Allowed browser routes are sanitized local projections and intent recording only:

- `GET /api/v1/testnet/operator-state`
- `POST /api/v1/testnet-activations`
- `POST /api/v1/testnet-activations/{activation_id}/deactivations`
- `GET /api/v1/proposals/{proposal_id}/testnet-approval-view`
- `POST /api/v1/testnet-approvals`
- `POST /api/v1/testnet-approvals/{approval_id}/revocations`
- `GET /api/v1/testnet-executions/{execution_id}`
- `POST /api/v1/testnet-reconciliation/{checkpoint_id}/confirmations`

There is no browser submit/retry/replace/exchange-query/account/balance/Gateway/raw-error route. Unknown schema fields or states result in Korean HOLD with all effect buttons disabled. CSP restricts connections and form actions to self; authenticated responses are no-store.

## Test-first gates

1. Closed JSON Schema, generated Python/TypeScript bindings, browser-only OpenAPI, capability and secret isolation.
2. Canonical Decimal, HMAC fixture signing, allowlist rejection, stable IDs, nonce/idempotency, monotonic state transitions, UNKNOWN resolution, reset generation.
3. Postgres migration, role separation, atomic inbox/domain/outbox, replay, reconciliation, and failure injection.
4. Korean desktop/mobile/keyboard browser flows, security headers, closed runtime parsing, no unsafe routes/buttons/data, Axe serious/critical 0.
5. Root `pnpm ci`, role-separated Safety QA, Codex cross-review, and allowed external review or explicit `external-review-unavailable`.

No real Testnet request is permitted in automated acceptance. Live credential provisioning and a real smoke request require a later, explicit operator action after all offline gates pass; credentials are never supplied in chat.
