# Phase 3 point-in-time Evidence implementation contract

## Authorization and boundary

Phase 3 is authorized by the P2 acceptance digest
`2d9c8158b12de3ebe55f040fccfe0872d6a39e5368b737182bb500c96bb3368f`.
It adds deterministic BTCUSDT/ETHUSDT research features and immutable Evidence only.
Paper Broker, Risk, approval, Kill Switch, AI proposal generation, Testnet/private
exchange access, credentials, orders, derivatives, leverage and shorts remain absent.

## Frozen feature recipe

The recipe is `woozoo.evidence.closed-candles-approved-features/v1`, bound to
`binance-spot-api-docs@29c227d84058dd2be3fe3b42ab368d1d1ce910e5` and feature
definition `woozoo.feature.ohlcv-return-sma20-rsi14/v1`.

For each of `1m|5m|1h|4h`, the worker requires the latest 21 contiguous, completed
candles eligible at both cutoffs. It computes with Python `Decimal`, precision 80,
round-half-even and 18 output decimal places:

- one-candle close return;
- 20-period close SMA;
- 14-period close RSI with Wilder initialization and smoothing.

RSI is 100 when loss is zero and gain is positive, 50 when both are zero, and 0
for an all-loss window. Features are research evidence only and have no authority
over Risk, ledger, fills or execution.

## Point-in-time and provenance invariants

An input is eligible only when `event_time <= as_of`, `close_time <= as_of`,
`received_at <= knowledge_cutoff`, and the source marks it closed. Both boundaries
are inclusive and independent. A same-bucket repair received after the cutoff
cannot alter an existing snapshot; a later cutoff may create a new digest.

Missing or non-contiguous candles, unsupported symbols/intervals, non-healthy
quality, quality reasons, an unapproved source schema/revision, a terminal window
older than one interval at the effective cutoff horizon, invalid provenance, or an
invalid watermark digest stop creation. No forward fill or synthetic candle exists.
Canonical JSON binds the validated raw/normalized schema versions and ordered
OHLCV inputs, raw and normalized IDs/hashes, feature definition, source revision,
cutoffs, recipe, quality, collector session and watermark into SHA-256 identities.

## Persistence and API

`feature_observations`, `feature_observation_inputs`, `evidence_snapshots`,
`evidence_items`, and command receipts are append-only. On the first valid command,
feature/input rows, snapshot/items, receipt, and `evidence.snapshot.created.v1`
outbox event commit in one transaction. An exact idempotent retry returns the
receipt-bound immutable identity without rebuilding; reusing the key for a different
command fails with 409. The evidence writer has SELECT-only access to the required
Phase 2 source history, cannot read raw payload bytes, and has only the minimum
INSERT/SELECT grants needed for its owned history and retry semantics.

The internal evidence-worker owns the approved
`POST /api/v1/commands/evidence-snapshots` materialization boundary. It accepts only
the closed `{symbol, as_of, knowledge_cutoff}` schema with explicit UTC timestamps
and an idempotency key. A trusted mutual-TLS ingress must inject the authenticated,
allowlisted `internal-evidence-scheduler` principal into the ASGI scope; direct,
missing, or unknown principals fail closed with 403 and no effect. The verified
principal is bound into the canonical request hash. The control API owns read-only
`GET /api/v1/evidence/{evidence_id}` and projects ordered items, candle content, and
feature content without raw payload bytes. Malformed/missing IDs are closed 404
responses without a database query; projection failure is 503. No trading or
exchange command capability is introduced.

## Acceptance criteria

| ID | Required evidence |
|---|---|
| P3-01 | Exact source revision, recipe and feature definition are digest-bound. |
| P3-02 | OHLCV, return, SMA20 and Wilder RSI14 use Decimal and exact 21-candle contiguous windows. |
| P3-03 | `as_of` and `knowledge_cutoff` are independently inclusive and exclude future knowledge. |
| P3-04 | Missing/gapped/open or unhealthy inputs fail closed with no snapshot. |
| P3-05 | Raw/normalized provenance, collector session, quality and watermark are preserved. |
| P3-06 | Late arrival never mutates prior Evidence; later cutoffs create deterministic new identities. |
| P3-07 | Feature/Evidence/outbox persistence is atomic, append-only and idempotent. |
| P3-08 | Approved idempotent Evidence command, read-only Evidence API, event schema and generated Python/TypeScript bindings are closed and drift-free. |
| P3-09 | Replay/restart/order variation produces the same semantic digest and one durable effect. |
| P3-10 | Migration upgrade/downgrade/re-upgrade and least-privilege roles pass against Postgres. |
| P3-11 | EVID-001 through EVID-007 all execute with zero failure, error or skip. |
| P3-12 | Safety QA, Codex cross-review, external review or accurate unavailable record, Git/PR evidence and digest-bound user approval complete. |

Any `FAIL|UNVERIFIED`, skip, missing scenario artifact, or review-gate failure blocks
Phase 4.
