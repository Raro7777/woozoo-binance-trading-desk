# Phase 3 working history — 2026-07-19

- Recorded the exact P2 acceptance approval and advanced the sole phase state to 3.
- Wrote RED tests for closed features, dual cutoffs, late arrival, read-only API,
  closed contracts and forbidden capabilities.
- Corrected the approved feature set to return, SMA20 and Wilder RSI14 over 21
  contiguous completed candles and bound the frozen Binance source revision.
- Added immutable feature/Evidence tables, provenance, least-privilege roles,
  atomic outbox persistence and a read-only Evidence projection.
- Regressed and fixed Phase 2 kline source identity so an update with the same last
  trade ID remains distinguishable by event time, close flag and payload hash.
- Verified targeted core, contract, migration, replay, property and failure paths.
- Phase 4 remains blocked pending complete Phase 3 CI, separated reviews, acceptance
  digest and exact user approval.
