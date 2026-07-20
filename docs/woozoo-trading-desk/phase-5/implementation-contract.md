# Phase 5 Risk Engine and Kill Switch Implementation Contract

Status: implementation candidate; acceptance is not yet frozen.

## Scope and activation

Phase 5 creates a deterministic, network-free Risk domain for BTCUSDT and ETHUSDT. It
evaluates complete test-namespace Proposal fixtures independently of human approval and
produces immutable `ALLOWED`, `DENIED`, or `ERROR` decisions. It also creates the shared
Kill barrier and an idempotent Paper cancellation consumer.

No FastAPI Risk or Kill command route, browser control, scheduler ingress, AI process,
exchange client, credential setting, Paper approval, or `PaperExecutionAuthorization` is
created. Production Risk rows cannot originate from fixtures. Activation remains Phase 7.

## Canonical input and decision

`risk_input_digest` is SHA-256 over closed, schema-versioned canonical JSON containing the
full Proposal payload/hash; portfolio snapshot/hash; data/Evidence quality, time and
watermark; exact Paper preview/hash; policy and calculator versions/hashes; Kill state and
version; reconciliation checkpoint/hash/health; deterministic decision clock; duplicate and
cooldown material; and existing order/exposure snapshot/hash. Human approval is not an
input. Missing or unknown fields fail closed.

Reasons are collected completely, sorted by fixed priority and then lexical code, and the
first is primary. The decision hash binds decision schema version, risk input digest,
verdict, and the ordered reason list. Row IDs, worker identity, wall time, trace IDs and
display text are excluded.

Policy v1 fixes portfolio exposure at 25%, BTC at 15%, ETH at 10%, fee-inclusive candidate
order notional at 0.25% of equity, rolling realized loss at 1%, drawdown at 5%, duplicate
symbol/side cooldown at 15 minutes, and spread/expected slippage at 25 bps. Calculations use
validated Decimal values without float conversion. Limit comparison is strict `>` except
loss and drawdown, which reject at `>=`.

The normative formulas and closed reason precedence are
`phase-0/financial-and-order-invariants.md` §10.2–10.3. Equity is available quote plus held
quote plus each `(base available + base held) * midpoint`, less fee liabilities. Existing BUY
commitment is remaining quantity times limit plus remaining worst-case quote fee; SELL adds
no quote exposure. Candidate notional is `abs(quantity * limit) + worst-case quote fee` for
both sides, while candidate exposure is added only for BUY. Spread is
`(ask-bid)/((ask+bid)/2)` and expected slippage is limit-versus-best-executable, clamped at
zero. Rolling loss uses the closed 24-hour realized PnL window. Drawdown is
`max(0, high_water-equity)/high_water`; nonpositive equity/high-water is an error. No
intermediate financial value is rounded.

## Kill transaction and Paper cancellation

Activation locks the singleton `paper-global` Kill row `FOR UPDATE`, verifies expected
version, increments the version, appends an immutable activation event and risk outbox
record, then commits. Every Paper create and fill transaction first obtains a compatible lock
on that exact row, verifies the version and `active=false`, and keeps the lock until its local
financial transaction commits. Thus a Paper effect linearizes either before activation or
observes the active barrier and has zero effect; activation cannot commit between the check
and Paper commit. Paper consumes
the activation through an inbox-deduped local transaction. It selects open orders in
`paper_account_id, accepted_broker_seq, client_order_id, order_id` order, at most 100 per
batch, and atomically records cancellations, releases residual holds, posts balanced
cancellation journals and outbox events. `(activation_event_id,batch_key)` is unique.

One transaction-level advisory lock on `(activation_event_id, paper_account_id)` serializes
batch claimers. `batch_key` is the SHA-256 of activation ID, account ID and the first/last
canonical order tuple in that batch; the persisted cursor is the last tuple. Order rows are
then locked in the same canonical order. Inbox receipt, batch/item rows, order cancellation,
hold release, ledger and outbox commit or roll back together.

Kill activation is not a distributed transaction with cancellation. The active barrier blocks
new effects before the saga completes. Duplicate delivery and crash restart resume by the
same keys. Timers, restart, Redis expiry, AI, or an anonymous caller can never recover the
switch. Phase 5 implements no active recovery ingress.

Every activation event binds `trigger_kind`, authenticated operator or allowlisted safety
service `actor_id`, full reason, closed reason code, `observed_at`, context digest and prior/new
version. Automatic activation is limited to commodity journal imbalance, cash/assets versus
physical-ledger mismatch, and the future authorization first-attempt receipt mismatch. The
last code is contract-only until Phase 7 and has no Phase 5 producer. Drawdown, ordinary
reconciliation failure, timer, AI, restart and Redis expiry never activate or recover Kill.

## Migration lifecycle

An unused Phase 5 schema can downgrade to Phase 4 and re-upgrade without changing Phase 4
Paper semantics. Once any immutable Risk decision, Kill activation, receipt, outbox link, or
Paper Kill-cancellation history exists, downgrade is deliberately unsupported and fails
before changing schema or data. Operators must retain the Phase 5 schema and use forward
recovery; deleting audit history or fabricating a Phase 4 human-cancel receipt is forbidden.

## Required acceptance denominator

`RISK-001..002`, `KILL-001..002`, `RISK-CONTRACT-001`, `RISK-MIGRATION-001`, and
`RISK-SAFE-001` are mandatory alongside every prior Phase command. No skip, missing
artifact, approval/authorization implementation, production route, or forbidden exchange
capability may be treated as PASS.
