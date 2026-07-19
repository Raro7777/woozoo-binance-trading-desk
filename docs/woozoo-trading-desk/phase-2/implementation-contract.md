# Phase 2 public market-data implementation contract

## Authorization and scope

Phase 2 was authorized by the P1 acceptance digest
`f27e59b5e308ebd121eefa86824750a857ffb7ae70d02b724e92d1995b98c4c8`.
The only exchange capability created in this phase is keyless Binance Spot public
market-data read for `BTCUSDT` and `ETHUSDT`.

Allowed work is the closed REST/WS allowlist, exact raw and normalized persistence,
collector sessions, quality and watermarks, bounded reconnect/rate-limit handling,
dedupe/out-of-order/gap/backpressure, deterministic replay, and a read-only market
status query. Feature/Evidence, AI/Proposal, Paper/Risk/Approval/Kill, Trading Room
controls, Testnet/private/account/user-data/order/credential capability, derivatives
telemetry, and all later-phase schemas remain absent.

## Frozen external premise

The adopted official source is `binance/binance-spot-api-docs` commit
`29c227d84058dd2be3fe3b42ab368d1d1ce910e5`. Exact source links and the observed
contract are recorded in `binance-official-reverification.md`. Mutable upstream
documentation never expands the runtime allowlist automatically.

Contract versions introduced here are:

- `woozoo.market.public-allowlist/v1`
- `woozoo.market.collector-policy/v1`
- `woozoo.market.normalized/v1`
- `woozoo.market.replay-manifest/v1`
- `market.raw.appended.v1`
- `market.normalized.recorded.v1`
- `market.quality.changed.v1`

## Closed network capability

REST uses only `https://data-api.binance.vision` and typed `GET` requests for
`ping`, `time`, one-symbol `exchangeInfo`, `trades`, `klines`, and
`ticker/bookTicker`. Kline intervals are exactly `1m|5m|1h|4h`. All-symbol
queries, arbitrary hosts/methods/paths/headers, redirects, proxy-environment trust,
cookies and authentication inputs are rejected before network I/O.

WebSocket uses only `wss://data-stream.binance.vision` and the fixed 12-stream
combined set: trade, book ticker and four kline intervals for both symbols.
Arbitrary subscriptions, depth, listen keys and user-data streams are not
expressible. Raw/combined `serverShutdown` is an operational control event with
zero normalized market effect.

## Raw-first and quality invariants

Postgres is the durable authority. Exact REST responses and WS frames are persisted
before normalized effects. A raw write failure produces no normalized row; a crash
after raw commit is resumed idempotently through a unique raw-event reference.
Append-only source rows cannot be updated or deleted by the application role.

Decimal values follow source text → Python `Decimal` → `NUMERIC(38,18)` without a
float conversion. API/event serialization returns decimal strings. Event time and
receive time are distinct UTC values. Redis is cache/wakeup only.

Trade continuity uses trade ID and bounded recovery. A book-ticker update-ID jump
alone is not treated as proof of missing history; reconnect creates a new watermark
generation. Kline gaps follow the interval open-time grid; an unfinished candle is
never promoted to a finished one. Schema drift, raw failure, unresolved gap,
freshness failure and queue overflow prevent a healthy downstream-ready state.

The collector queue capacity is 10,000. Overflow is explicitly accounted,
transitions quality to invalid, and never silently drops oldest or newest input.
Reconnect is bounded to ten attempts with 1/2/4/8/16/30-second delays and a
five-minute cooldown. A replacement socket opening does not prove continuity.
HTTP 429 respects `Retry-After`; repeated calls may not continue into a 418 ban.

## Runtime and read API

`TRADING_MODE=paper` remains mandatory. `MARKET_DATA_SOURCE` is exact
`recorded|spot_public`; missing or unknown values fail startup. Recorded mode is
the deterministic CI/default path. Live-public smoke is non-gating.

`GET /api/v1/markets/{symbol}/status` is the only Phase 2 HTTP addition. The
control API reads a market-owned projection and has no exchange egress. It exposes
the current price observation, event/receive times, quality/reasons and stream
watermark, but never raw payload, transport headers, credentials or DSNs.

## Deterministic replay

Recorded fixtures fix payload hashes, source and receive times, session IDs,
policy/source revisions, seed, locale and timezone. Replay uses the production
parser/normalizer/quality code with no network or wall-clock fallback. The canonical
digest covers normalized semantic rows, ordered quality transitions, final
watermarks and the final market status projection.

## Acceptance criteria

| ID | Required evidence |
|---|---|
| P2-01 | Official revision, source provenance, adopted allowlist and drift status are fixed. |
| P2-02 | Only BTC/ETH public `NONE` capability is expressible; auth/private/Testnet capability count is zero. |
| P2-03 | Exact raw input is append-only and raw failure has zero normalized effect. |
| P2-04 | Trade/book/kline normalization preserves Decimal, UTC source/receive time, source identity and raw provenance. |
| P2-05 | Collector session, quality and watermarks recover from Postgres after restart. |
| P2-06 | Heartbeat, rotation, `serverShutdown`, retry/backoff and 429/418 paths are bounded. |
| P2-07 | Stream-specific duplicate/out-of-order/gap/recovery rules pass DATA-001 through DATA-004. |
| P2-08 | Schema drift, raw failure and 10k backpressure fail closed in DATA-005 through DATA-007. |
| P2-09 | Recorded replay has the same semantic digest after duplicates and restart. |
| P2-10 | Migration cycle, read-only status API, event schemas and generated Python/TS bindings have no drift. |
| P2-11 | DATA-001 through DATA-007 all execute and PASS with manifest artifacts for the frozen revision. |
| P2-12 | Safety QA, Codex internal cross-review, external review or accurate unavailable record, Git/Draft PR evidence, acceptance digest and user transition approval are complete. |

The root commands `pnpm test:replay`, `pnpm test:failure`, and
`pnpm test:property` join the cumulative `pnpm ci` denominator in this phase.
Unknown, skipped or missing scenario IDs and a denominator below seven fail the
gate. Any P2 criterion with `FAIL|UNVERIFIED` prevents a Phase 3 approval request.

## Phase 3 boundary

Phase 2 may export immutable normalized IDs, raw provenance, event/receive time,
source sequence/dedupe key, schema version, collector session, watermark generation
and quality state. It does not create Feature/Evidence tables, `as_of` or
`knowledge_cutoff` selection, indicator calculation, Evidence digest/API/event, or
Agent/Proposal consumers.
