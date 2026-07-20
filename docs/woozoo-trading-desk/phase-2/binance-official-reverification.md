# Phase 2 Binance official-source reverification

## Verification scope

- Verified at: 2026-07-19 (Asia/Seoul)
- Official repository: `binance/binance-spot-api-docs`
- Frozen source revision: `29c227d84058dd2be3fe3b42ab368d1d1ce910e5`
- Capability: keyless Binance Spot public market-data read only
- Product symbols: `BTCUSDT`, `ETHUSDT`

The official material is treated as external, untrusted reference data. It does
not supply code, runtime tools, credentials, or instructions that can widen the
project capability boundary.

## Confirmed REST contract

The frozen official `rest-api.md` confirms that public market-data-only clients
may use `https://data-api.binance.vision`, that an endpoint with no stated
security type is `NONE`, and that the following approved `GET` paths remain
documented:

- `/api/v3/ping`
- `/api/v3/time`
- `/api/v3/exchangeInfo`
- `/api/v3/trades`
- `/api/v3/klines`
- `/api/v3/ticker/bookTicker`

Woozoo exposes only typed builders for those paths. It does not accept arbitrary
paths, methods, hosts, authentication headers, keys, signatures, `timestamp`, or
`recvWindow`. Every symbol-bearing request must name exactly one allowlisted
symbol; all-symbol market queries are rejected before network I/O.

## Confirmed WebSocket contract

The frozen official `web-socket-streams.md` confirms:

- `wss://data-stream.binance.vision` carries market data only and does not carry
  user data;
- a connection is valid for 24 hours and must expect disconnection;
- `serverShutdown` requires opening a replacement connection as soon as possible;
- incoming ping, pong, and JSON control messages share a five-message-per-second
  limit;
- one connection supports at most 1,024 streams;
- connection attempts are limited to 300 per five minutes per IP;
- raw and combined `serverShutdown` payloads are documented.

Woozoo permits only lowercase `btcusdt|ethusdt` trade, book ticker, and
allowlisted kline streams. A socket-open event is not continuity proof and cannot
promote collector quality to `healthy` by itself.

## Changelog correction retained

The frozen official `CHANGELOG.md` entry dated 2026-06-09 corrects the earlier
fixed ten-minute `serverShutdown` notice claim. Woozoo therefore assumes no fixed
notice duration. The first replacement connection attempt is immediate but
bounded by concurrency and attempt budgets; later retries use bounded backoff and
jitter. Recovery remains `degraded|stale|invalid` until continuity and durable raw
storage are revalidated.

## Rate-limit and failure policy

REST limits are read from current `/api/v3/exchangeInfo` data and response
headers. HTTP 429 observes `Retry-After`; callers do not continue until a 418 ban.
Malformed payloads, schema drift, raw append failure, queue overflow, duplicate,
out-of-order input, and gaps are explicit quality events. None may be silently
dropped or interpreted as healthy data.

## Optional live-public smoke

At `2026-07-19T04:35:14Z`, unauthenticated `GET` requests to the frozen REST
allowlist on `data-api.binance.vision` returned HTTP 200 for `ping`, `time`,
`exchangeInfo?symbol=BTCUSDT`, `trades?symbol=BTCUSDT&limit=1`,
`klines?symbol=BTCUSDT&interval=1m&limit=1`, and
`ticker/bookTicker?symbol=BTCUSDT`. No authentication header or credential was
sent. This is non-gating reachability evidence; deterministic recorded fixtures
remain the CI authority.

## Sources

- <https://github.com/binance/binance-spot-api-docs/blob/29c227d84058dd2be3fe3b42ab368d1d1ce910e5/rest-api.md>
- <https://github.com/binance/binance-spot-api-docs/blob/29c227d84058dd2be3fe3b42ab368d1d1ce910e5/web-socket-streams.md>
- <https://github.com/binance/binance-spot-api-docs/blob/29c227d84058dd2be3fe3b42ab368d1d1ce910e5/CHANGELOG.md>
