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
- Approval issuance and Kill recovery hold the Paper account lock and require
  the latest reconciliation checkpoint's stamped authority sequence to equal
  the current Paper outbox sequence. Any committed Paper effect therefore
  requires a new reconciliation before either writer can proceed; a healthy
  but stale checkpoint is never accepted merely because its digest still
  matches an earlier Risk decision or cancellation completion.
- Kill cancellation completion is written only after every open Paper order is
  terminally cancelled and every already-issued Paper authorization has made
  its one terminal attempt under the active Kill barrier. The worker drains
  those authorizations before completion, and the deferred database binding
  rejects a completion while any authorization remains pending. Completion's
  immutable full semantic digest can therefore remain the exact input digest
  required of the later healthy recovery checkpoint without creating a
  permanent recovery hold.
- Manual Kill recovery requires the active activation event, expected version,
  an incident reference, authenticated actor, and healthy data, ledger, and
  reconciliation. Its reader and writer select the same latest BTCUSDT and
  ETHUSDT book event IDs and require the raw-bound current-market verifier to
  pass for both while transaction locks are held. A stale projection,
  watermark, collector session, raw provenance, future clock, or lock conflict
  therefore keeps recovery on HOLD even when an immutable normalized row still
  says healthy. Timer, AI, restart, and Redis cannot recover Kill.

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

The same inbound-less Paper runtime may consume only append-only, healthy Binance
Spot public `book_ticker` rows received after an order was accepted. It selects
the canonical order and recorded book in a deterministic sequence, revalidates
Kill and reconciliation under the Paper account lock, then calls a DB-owned,
least-privilege verifier that binds the row to the newest active collector
session, exact allowlist, a freshly recomputed SHA-256 of the raw bytes, strict
Binance `bookTicker` symbol/bid/ask fields, the normalized Decimal values, healthy
stream watermark and healthy current market projection. Concurrent session
changes, a watermark stream that differs from the exact 1m/5m/1h/4h raw kline
stream, malformed or mismatched provenance, stale timestamps, quality reasons,
Kill, cancellation and reconciliation drift return a no-effect HOLD; unexpected
DB/runtime conditions remain fatal. BUY/SELL book sides have independent canonical
order and liquidity budgets, and same-side orders compete by accepted time then ID.
An immutable observation effect preserves the first `NO_FILL` response even after
the order later fills or is cancelled. A successful transaction atomically records
the observation, partial/full fill, recorded `received_at` provenance, balance
versions, lots, journals, order event and outbox. The Paper role can execute the
verifier but cannot read its source market tables directly. The browser and AI
cannot supply price, liquidity, fill or ledger values.

## Acceptance

The repository pins pnpm 7.33.7 so the canonical `pnpm ci` command executes the
project quality aggregator rather than a package-manager clean-install alias. It
performs its own frozen-lockfile bootstrap and every approved quality command.
The final gate independently validates the exact 43-row Phase 0 denominator:
every artifact must exist, be PASS, contain a non-empty zero-skip JUnit result,
bind the same commit/tree/worktree digest/file count, and retain its declared
source/configuration hashes; E2E flat and directory results are byte-identical.

Real HTTPS E2E covers golden flow, stale-data block, mismatched-preview no-effect,
idempotent retry/reload, Kill cancellation/recovery, a post-acceptance public
recorded-book partial fill followed by an operator cancellation, and a fresh
keyboard approval in both desktop and mobile viewports. All user-visible labels,
errors and command dialogs are Korean, while canonical values remain internal
hash-bound data. Serious/critical accessibility findings, missing commands,
skipped scenarios or unverified evidence are not PASS.
