# Woozoo Phase 7 MVP external review

Review the exact frozen implementation on branch `codex/phase-7-trading-room` as a major financial-safety change.

- Frozen commit: `c0baf4cedd56818da0cc9d5718950b15eaf48b2b`
- Frozen tree: `6f66d2f761a689ab4ed98924b1485f78f7c25478`
- Base commit: `03369ceebc84da904bf6cab6818154f96670dc43`
- Canonical command: `corepack pnpm ci`
- Canonical result: PASS / exit 0 / P7-43 43 of 43
- Artifact test coverage count: 1,209 (shared result runs are intentionally repeated across acceptance artifacts; this is not an execution count)
- Unique evidenced test executions: 700 across 44 result-bound run IDs and JUnit SHA-256 digests
- Worktree digest: `fb55680be162ccb23ef0632e97c8880d0cb449b2bad59f1f6dc582d56d969bd4`
- Scenario manifest SHA-256: `bf4079bcb6a003e7d32176f8bf153414ba927ab42ba927776281835f1b47e668`
- Aggregate output digest: `193076ba203113b98987e1d2758859c91242d43fddea51ac291ff4e9bfe4dc04`
- P7-43 SHA-256: `809eda7c8a8ae84cb7ef87c900a1ec402edf3878228a27442056ddb1217b6abf`
- E2E infrastructure preflight SHA-256: `13d0a01cf2a19be5499ed7c79b25341245f17aa899c46e36840fcfa9d5214032`

Current Phase: 7 (MVP GUI and internal Paper Trading closed loop).

Allowed: Binance Spot public data only; deterministic Evidence, Agent proposal, Risk, human Paper approval or rejection, five-minute single-use Paper authorization, internal Paper order/ledger, local single-operator UI, manual audited Kill recovery.

Forbidden: Binance private or Testnet gateway capability, credentials, signed requests, live trading, withdrawal, futures, margin, leverage, short, browser/AI order or account tools, AI authority over balances/Risk/approval/orders.

Inspect the exact `03369ceebc84da904bf6cab6818154f96670dc43..c0baf4cedd56818da0cc9d5718950b15eaf48b2b` diff, contracts, migrations, least-privilege grants and SECURITY DEFINER functions, approval/revocation/session/CSRF/Origin binding, Kill/reconciliation behavior, Paper first-attempt idempotency and ledger atomicity, nonce/browser projection, desktop/mobile real Postgres E2E, capability scans, migration downgrade handling, and Compose fresh-volume readiness.

Pay special attention to:

1. immutable Agent run to persisted Risk decision binding;
2. Control accepting IDs/server time only while DB constructs financial Risk context;
3. approval issuance being separate from the one-shot Paper worker;
4. terminal blocked authorization semantics and replay;
5. opening account ledger/reconciliation and Decimal scale;
6. whether tests use real authority rather than fake state or skips;
7. any route/schema/event/UI inconsistency or security escalation;
8. command-guard/logout serialization, one-time CSRF consumption, session touch/revocation, lock ordering, and transaction rollback;
9. rejected-receipt namespace dialect, receipt-to-attempt request-hash binding, and terminal outcome authority;
10. Evidence freshness immediately before browser-driven Risk analysis;
11. worker-health failure allowing human rejection for an otherwise-ready ALLOWED Risk decision while keeping approval fail-closed;
12. both human decisions rejecting DENIED/ERROR Risk, and reject-enabled approval-view responses requiring complete ALLOWED Risk/preview bindings and no existing approval/authorization;
13. unknown worker states failing closed and every closed worker reason having a Korean UI label;
14. PostgreSQL health checks waiting for the final TCP server rather than the temporary init socket server;
15. failed cleanup or a surviving named volume structurally preventing E2E startup and migration;
16. `E2E-INFRA-001` binding cleanup, volume absence, startup, migration and public-data bootstrap into every E2E result.
17. worker readiness being revalidated and row-locked inside the same approval/authorization transaction after Risk/Kill/reconciliation checks and before every write.
18. a committed APPROVE receipt replaying before mutable worker-health checks after ACK loss, with changed-body conflict and exactly one effect;
19. proposal-scoped serialization across Risk evaluation/persistence, approval and Paper first attempt, including latest-ALLOWED deferred guards and a newer-DENIED race;
20. every `phase-state.json` Phase 7 acceptance runtime path and Markdown anchor resolving to a real, coherent contract section;
21. JUnit result SHA-256 and invocation-bound run IDs separating artifact coverage from unique evidenced test executions without double counting.
22. approval Risk TTL and worker freshness using one database wall clock sampled only after every potentially blocking authority lock, including the exact five-minute boundary;
23. the Paper first attempt sampling its expiry/freshness/effect clock only after exact raw/current-market verifier locks, so waiting across authorization expiry cannot create an order;
24. Kill recovery locking market and worker authority and sampling its recovery clock only after the account/authority waits, so stale worker or data cannot clear Kill.
25. normalized-book selection remaining exact across an in-flight newer writer: the verifier must serialize before projection locks, reselect the newest symbol event ID after any wait, and hold that authority through Risk persistence, Paper first attempt/fill, and Kill recovery.
26. collector-session, normalized and raw/no-raw quality writers sharing one leading writer/normalized authority fence and the exact watermark → market → collector order; quality persistence conflicts must retry only bounded idempotent SQLSTATEs, then fail-stop rather than leave a memory-only invalid state while DB projections remain healthy.
27. `QualityPersistenceError` remaining fatal across the live supervisor producer/consumer boundary: queue overflow or any final quality persistence failure must be published before another dequeue, prevent queue drain and reconnect, and prohibit a next collector session.

Report only actionable findings in this exact form, ordered by severity:

```text
1. [P0|P1|P2|P3] Title
- Location: file:line
- Claim: what is wrong
- Reproduction/evidence: static or command evidence
- Recommendation: minimal correction
```

If there are no actionable findings, state `NO_ACTIONABLE_FINDINGS` and list the primary evidence inspected. Do not modify files. Do not use Claude. The canonical gate is `corepack pnpm ci`; the frozen acceptance artifact records 43/43 PASS, artifact coverage 1,209, and 700 unique evidenced test executions across 44 result-bound runs.
