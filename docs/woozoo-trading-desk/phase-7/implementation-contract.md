# Phase 7 MVP implementation contract

Status: frozen for implementation on `codex/phase-7-trading-room`.

## Product boundary

Phase 7 completes the MVP as a local, single-operator, Paper-only Trading Room.
It introduces no Binance credential, private endpoint, external order, Testnet,
Mainnet, withdrawal, derivative, margin, leverage, short, or AI/browser order
tool capability.

The authoritative flow is:

```text
immutable Evidence
  -> Mock-provider analysis and TradeProposal
  -> deterministic Risk input and decision
  -> atomic approval view and server Paper preview
  -> authenticated human approve/reject
  -> distinct one-time authorization
  -> terminal Paper attempt (order created or blocked)
```

## Resolved contract decisions

- P4-P6 fixture schemas remain frozen. Production activation uses new
  `analysis-run/v2`, `risk-input/v3`, `paper-order/v2`, approval-view,
  approval, authorization, session, receipt, and v2 event contracts.
- Browser approval bodies exclude `actor_id`, `approved_at`, `expires_at`,
  `approval_hash`, price, quantity, fee, Risk verdict, and authorization data.
  The server derives actor and a maximum five-minute expiry from its clock.
- Only `operator-local-1` exists. Login is exact-origin HTTPS. Sessions use an
  opaque Secure/HttpOnly/Strict `__Host-woozoo_session` cookie. Repositories
  store SHA-256 digests only. Idle expiry is 30 minutes; absolute expiry is
  eight hours.
- `GET /api/v1/session` is no-store and returns a one-time ten-minute CSRF
  value. Mutation handlers require exact Origin and consume a valid token on
  success or domain rejection.
- Approval and authorization nonces are distinct. The first Paper attempt is
  terminal whether it creates one order or records `BLOCKED`.
- Manual Kill recovery requires the active activation event, expected version,
  an incident reference, authenticated actor, and healthy data, ledger, and
  reconciliation. Timer, AI, restart, and Redis cannot recover Kill.

## Paper order preview policy v1

`woozoo.paper-order-preview-policy/v1` is deterministic code, not an AI or UI
input.

- Supported symbols: BTCUSDT and ETHUSDT; LIMIT/GTC; BUY or SELL.
- BUY price is the fresh best ask; SELL price is the fresh best bid, rounded
  down to the symbol tick.
- Gross candidate notional is capped by the smaller of available resources and
  `0.0025 * current Paper equity`.
- BUY sizing reserves the versioned 0.001 Paper fee before quantity is rounded
  down to step size. SELL sizing is additionally capped by available base.
- Quantity must meet the frozen symbol minimum quantity and notional.
- Preview includes server Decimal strings and a canonical SHA-256 hash. Any
  price, balance, policy, Kill, freshness, ledger, or reconciliation drift
  requires a new Risk/approval cycle.

The local MVP has one versioned `paper-default` genesis account. Its immutable
bootstrap policy credits `10000.00000000 USDT` and zero BTC/ETH through a
balanced genesis journal; it is not a browser or AI input. Re-running bootstrap
with the same policy hash is idempotent and any different amount/hash is a
conflict. SELL therefore remains unavailable until a prior Paper BUY creates an
authoritative base position.

## Storage and transaction boundary

Postgres stores Argon2id verifier, session/CSRF digests, immutable approvals,
separate revocations, immutable authorizations, terminal authorization
attempts, receipts, orders, ledger and outbox rows. Production rows use the
closed `paper` namespace and cannot reference `test` fixtures.

The Paper first-attempt transaction locks authorization, Kill, reconciliation,
ledger, balance and order state in one documented order. Success writes receipt,
attempt, order, holds/ledger and outbox atomically. Guard failure writes a
blocked attempt, rejected receipt and outbox atomically with no order, hold,
fill or ledger effect.

## Acceptance

All canonical quality commands, including real HTTPS browser E2E, must pass.
E2E covers golden flow, stale-data block, idempotent retry/reload, Kill
cancellation/recovery, desktop/mobile keyboard flow, and zero serious/critical
accessibility findings. Missing commands or unverified evidence are not PASS.
