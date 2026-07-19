# Phase 4 Paper Broker and Ledger Implementation Contract

Status: implementation candidate; acceptance is not yet frozen.

## Scope and activation

Phase 4 creates an internal, deterministic, network-free Paper LIMIT/GTC domain for
BTCUSDT and ETHUSDT. It implements partial fills, quote fees, cancellation, long-only
SELL, FIFO lots, realized/unrealized PnL, holds, immutable commodity-balanced journals,
idempotency, rollback and deterministic replay.

The engine accepts only `ExecutionFixture(namespace="test")`. It has no FastAPI app,
HTTP route, scheduler, queue consumer, compose service, browser tool or exchange client.
The dormant schemas record `creation_phase=4` and `activation_phase=7`; the active
OpenAPI has zero Paper paths. Risk, Approval, Kill, Proposal and their foreign keys are
absent. Production Paper account funding and production aggregate creation remain off.

## Policy v1

- exact DB representation: `NUMERIC(38,18)`; input is a plain Decimal string
- quote fee: `0.001`; actual fee and holds round up at the posting boundary
- fill participation: `10%` of displayed liquidity, floor to the symbol step
- execution price: order limit; no market fallback or better-price optimism
- order eligibility: a book observation after acceptance, BUY ask <= limit or SELL bid >= limit
- FIFO tie: acquisition time then canonical source fill ID
- every journal balances debit and credit independently for BTC, ETH, USDT or USDT_VAL
- posted history is immutable; correction is complete reversal plus replacement

The official unauthenticated exchangeInfo response was rechecked on 2026-07-19 and is
frozen in `binance-public-symbol-rule-reverification.json` with a hash-bound deterministic
field projection in `binance-symbol-rule-projection.json`. BTCUSDT uses tick `0.01`,
step/min quantity `0.00001`, min notional `5`; ETHUSDT uses tick `0.01`, step/min
quantity `0.0001`, min notional `5`.

## Transaction and replay

The Postgres design owns command receipt, authorization first-attempt receipt, broker
input, order/hold/fill/lot, ledger, response and outbox in one transaction. A deferred
constraint trigger rejects per-commodity imbalance at commit. Append-only triggers
reject mutations of receipts, fills, lots, consumptions and ledger history. Same
idempotency key/hash replays the stored result and a different hash conflicts.

The calculation engine remains IO-free, while an internal non-ingress Postgres store is
the durable authority. It commits receipt, authorization attempt, ordered inputs, versioned
order/balance state, FIFO lots, ledger entries and a schema-checked outbox in one database
transaction. The store supports orderless rejected receipts and distinct create, observation,
fill and cancel lifecycle writes. It hydrates the deterministic engine from durable rows after
restart, including receipts, allocation budgets, lots, journals and event history.

Recorded-book identity is stored once, while `paper_observation_effects` records each
order's `FILL` or `NO_FILL` result. This lets one immutable observation allocate its
floor-stepped participation budget across already-accepted orders in canonical
`accepted_broker_seq`, client-order and order-ID order, including after restart. Transaction
advisory locks serialize same-command and same-observation retries before the first read.
Every effect of one observation reuses its single source broker sequence; allocation checks
use the canonical order tuple rather than caller-influenced fill identifiers.

Deferred commit-time checks bind fills to immutable fee/symbol policies, recorded-book
liquidity, physical and valuation journals, FIFO conservation, complete balance commodities,
order history and outbox versions. The minimum-privilege writer can mutate only versioned
order/balance projections; unledgered changes fail at commit. Failure injection at each SQL
boundary proves zero partial effect. Reconciliation compares the union of durable balances
and physical-ledger commodities, persists a fail-closed checkpoint, and any latest failed
checkpoint places subsequent new lifecycle commands on HOLD.

The database derives rather than trusts durable identity and lifecycle authority. It
recomputes every outbox event ID and payload hash from the canonical event material, binds
authorization and fill/cancel references to their owning aggregates, and rejects lifecycle
events after a terminal state. Opaque identifiers used in canonical hashes have one closed
ASCII grammar in Python, Postgres and JSON Schema. An eligible observation may record
`NO_FILL` only when its floor-stepped canonical budget is exhausted, evaluated against the
order lifecycle as of that observation rather than a later projection. Each order has an exact
two-line hold journal; partial fills reduce the order hold projection and terminal release
equals the residual hold. Fill sequence equals its source observation sequence, every fill has
exactly one correctly versioned lifecycle event, and cancellation receipt, event and outbox
form a one-to-one causal chain. Ledger transaction identifiers use deterministic lowercase
64-hex IDs at every contract boundary. A BUY lot's acquisition time equals its source fill's
authoritative creation time. SELL basis is the exact proportional or residual basis of the
oldest available lot at the sale's causal point, so timestamp forgery or later sales cannot
repair a prior FIFO violation.

Commit-time templates also bind book symbol and side-specific quote eligibility, quote fee
asset, BUY lot quantity/cost, SELL consumption quantity/FIFO basis, and every physical and
valuation journal amount to the fill. The security-definer outbox entry point accepts only
the seven closed Paper event shapes, exact envelope/data keys, typed values, matching domain
rows and account links; `PUBLIC` execution is revoked.

## Required acceptance denominator

`FIN-001..004`, `ORD-001..002`, `ATOM-001..002`, `PAPER-CONTRACT-001`,
`PAPER-SAFE-001` and `PAPER-MIGRATION-001` are mandatory alongside all prior Phase
tests. Each financial artifact binds the financial policy version, oracle fixture hash,
fixed seed, UTC and `network_enabled=false`. No skip or missing artifact may be treated
as PASS.
