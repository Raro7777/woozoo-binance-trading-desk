# Phase 2 public market-data working history

## Scope

Phase 2 adds only keyless Binance Spot public market data for `BTCUSDT` and
`ETHUSDT`. It does not add Testnet, private/account data, credentials, orders,
derivatives, feature/Evidence computation, Paper Broker, Risk, Approval, AI
Proposal, or Trading Room controls.

## Implemented boundary

- closed public REST and 12-stream WebSocket capability builders;
- raw-first append-only Postgres history with normalized and quality records;
- source-owned watermarks, status projections, process-specific DB roles, and
  atomic raw/normalized/quality outbox events;
- bounded queue, freshness, exact server-shutdown handling, reconnect budget,
  cooldown, seeded jitter, and 23-hour-45-minute planned rotation;
- deterministic recorded replay, duplicate/out-of-order/gap checks, future
  kline quarantine, restart recovery, and read-only market status API;
- generated TypeScript and Python closed contract bindings;
- DATA-001 through DATA-007 replay, failure, and property evidence.

## Review-driven remediation

The implementation was revised after role-separated safety QA and same-engine
Codex cross-review. Remediation covered durable restart continuity, authoritative
runner dispatch, exact shutdown wrappers, all-stream projection updates,
stream-local previous status, fixed DB roles, transactionally coupled outbox
events, complete Python bindings, planned rotation/jitter, future-kline status
propagation, JUnit denominator enforcement, and repeatable integration state.

## Verification

The canonical command is `corepack pnpm ci`. It includes locked bootstrap,
non-writing environment validation, lint, typecheck, unit, contract, safety,
production web build and integration, DATA-001 through DATA-007, and final
package builds. Replay and failure suites were also executed twice consecutively
to verify repeatability.

## Review classification

Safety QA is role-separated internal verification. Codex cross-review is a
same-engine, separate-context challenge review and is not external independence.
The permitted agy/Gemini attempt produced no review because headless file access
was denied; this is recorded as `external-review-unavailable`, not PASS. Claude
was not used.
