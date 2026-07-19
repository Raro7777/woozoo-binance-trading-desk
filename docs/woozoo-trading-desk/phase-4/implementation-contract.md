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
the durable authority. It commits receipt, authorization attempt, ordered inputs, domain
state, FIFO lots, ledger entries and outbox in one database transaction. Failure injection
at each SQL boundary proves zero partial effect; a restarted store returns the same stored
response and semantic digest. Reconciliation compares durable balances with physical
ledger postings and persists a fail-closed checkpoint. Recorded commands and observations
also rebuild the same in-memory semantic digest without wall-clock or random identity inputs.

## Required acceptance denominator

`FIN-001..004`, `ORD-001..002`, `ATOM-001..002`, `PAPER-CONTRACT-001`,
`PAPER-SAFE-001` and `PAPER-MIGRATION-001` are mandatory alongside all prior Phase
tests. Each financial artifact binds the financial policy version, oracle fixture hash,
fixed seed, UTC and `network_enabled=false`. No skip or missing artifact may be treated
as PASS.
