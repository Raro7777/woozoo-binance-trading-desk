# P0-02 Binance 공식 전제 검증

- 상태: `PASS` 후보 — 최종 Phase 0 evidence digest에 이 파일의 SHA-256을 결속하기 전까지 Phase 전환 근거로 단독 사용하지 않는다.
- 확인일: 2026-07-19 (Asia/Seoul)
- 근거 범위: Binance 공식 Developer Docs의 Spot 문서만 사용
- 적용 범위: Phase 2 공개 시장 데이터 설계와 Phase 8 이후 Spot Testnet 경계 설계
- 금지 해석: 이 문서는 Testnet gateway, 주문 schema, credential 또는 주문 코드를 Phase 0~7에 추가할 권한을 만들지 않는다.

## 1. 검증한 공식 문서

| 문서 | 이 프로젝트가 채택한 근거 |
|---|---|
| [Spot REST API](https://developers.binance.com/en/docs/products/spot/rest-api) | 공개 전용 REST base, 보안 유형, HTTP/rate-limit 처리, 동적 rate limit |
| [Spot WebSocket Streams](https://developers.binance.com/en/docs/products/spot/web-socket-streams) | 공개 전용 stream base, 연결 수명, ping/pong, 연결·메시지·stream 제한, `serverShutdown` raw/combined payload와 재연결 요구 |
| [Spot API Changelog](https://github.com/binance/binance-spot-api-docs/blob/master/CHANGELOG.md) | 2026-06-09 `serverShutdown` 정정: 고정 사전 통지 시간 가정을 제거하고 수신 즉시 새 연결을 요구 |
| [Spot Testnet General Info](https://developers.binance.com/en/docs/products/spot/testnet/general-info) | Testnet REST/WS 주소, `/api` 범위, 가상 자산, 동적 filter/rate limit, 주기적 reset |
| [Spot Testnet REST API](https://developers.binance.com/en/docs/products/spot/testnet/rest-api) | `exchangeInfo`, trades, klines, book ticker의 현재 endpoint와 응답 의미 |
| [Spot Testnet WebSocket Streams](https://developers.binance.com/en/docs/products/spot/testnet/web-socket-streams) | `trade`, `bookTicker`, `kline` stream 이름과 payload 의미 |

공식 문서가 바뀔 수 있으므로 구현 시 문서 제목이나 예제의 복사본보다 아래 allowlist와 contract test를 정본으로 삼고, Phase 2와 Phase 8 시작 시 이 검증을 다시 수행한다.

## 2. Phase 2 공개 데이터 전제

### 2.1 인증과 endpoint 경계

1. 공개 시장 데이터 REST의 기본 host는 `data-api.binance.vision`을 사용한다.
2. WebSocket은 user data를 제공하지 않는 공개 시장 전용 `data-stream.binance.vision`을 사용한다.
3. 허용 보안 유형은 `NONE`뿐이다. `TRADE`, `USER_DATA`, `USER_STREAM`, `SIGNED`는 Phase 2 package의 타입·설정·DI·환경 변수에서 표현할 수 없어야 한다.
4. API key, secret, signature, `X-MBX-APIKEY`, `timestamp`, `recvWindow`를 Phase 2 public client 입력으로 받지 않는다.
5. production Spot의 public host 사용은 실거래 capability가 아니다. 안전성은 host 차단 하나가 아니라 method, path, 인증 요구, capability allowlist로 판정한다.

### 2.2 고정 allowlist

| 채널 | 허용 method/path 또는 stream | 용도 | 금지 조건 |
|---|---|---|---|
| REST | `GET /api/v3/ping` | 연결 상태 점검 | 다른 method 금지 |
| REST | `GET /api/v3/time` | server time 관측 | 주문 timestamp 생성 용도로 사용 금지 |
| REST | `GET /api/v3/exchangeInfo` | symbol·filter·동적 rate limit | `BTCUSDT`, `ETHUSDT` 외 symbol의 도메인 승격 금지 |
| REST | `GET /api/v3/trades` | recent raw trades bootstrap/recovery | 인증 header 금지 |
| REST | `GET /api/v3/klines` | candle bootstrap/recovery | `1m`, `5m`, `1h`, `4h` 외 interval은 MVP 제외 |
| REST | `GET /api/v3/ticker/bookTicker` | best bid/ask bootstrap/recovery | all-symbol 무제한 호출 금지 |
| WS | `<symbol>@trade` | unique raw trade stream | symbol은 lowercase `btcusdt`, `ethusdt`만 |
| WS | `<symbol>@bookTicker` | best bid/ask 실시간 stream | payload schema 불일치 시 invalid |
| WS | `<symbol>@kline_<interval>` | candle 진행/종료 관측 | interval allowlist 밖 구독 금지 |

URI는 자유 문자열 연결로 만들지 않는다. `PublicCapability` 열거형과 typed request/stream builder가 위 조합만 생성하고, allowlist 밖 값은 네트워크 호출 전에 거절한다.

### 2.3 시간과 point-in-time 의미

- REST와 WebSocket의 시간 필드는 기본 millisecond다. 원본 단위와 source timestamp를 보존한다.
- 각 raw record는 최소 `source_event_time`, 가능한 경우 `source_trade_time` 또는 kline open/close time, `received_at`, `ingested_at`, connection/session ID를 가진다.
- kline은 open time으로 식별하며 `x=true`와 같은 source close 상태를 별도 보존한다. 미완성 candle을 완성 candle로 취급하지 않는다.
- Evidence의 `as_of`는 source event time만으로 결정하지 않고 수집 watermark, gap, reconnect, schema 상태를 함께 결속한다.
- 동일 timestamp의 tie-break는 trade ID, update ID, source sequence, raw ID 순서처럼 문서화된 안정 키로 결정한다. wall clock 정렬만 사용하지 않는다.

### 2.3.1 승인된 MVP feature 범위

DP-D02는 완료된 `1m/5m/1h/4h` kline을 원본으로 보존하고 OHLCV, 한 candle 수익률, SMA20, RSI14만 Feature/Evidence 입력으로 승인한다. 이 feature는 AI 분석 근거일 뿐 Risk·원장·체결 가격의 권위가 아니며, candle을 wall clock으로 합성하거나 미완성 candle을 완성값으로 승격하지 않는다.

### 2.4 연결과 rate-limit 운영 계약

공식 WebSocket 계약에 따라 다음을 고정한다.

- 연결은 24시간에 종료될 수 있으므로 24시간 경계 이전의 계획된 재연결과 즉시 재연결 경로를 모두 가진다.
- server ping에 같은 payload의 pong을 신속히 돌려준다. unsolicited pong만으로 연결 건강을 주장하지 않는다.
- 한 연결의 incoming message는 초당 5개 한도이며 ping, pong, subscribe/unsubscribe control message가 모두 분모에 들어간다.
- 한 연결 최대 1024 streams, IP당 5분 동안 300 connection attempts를 상한으로 취급한다.
- reconnect는 지수 backoff+jitter와 전체 attempt budget을 사용한다. 연결 폭주 시 collector를 `degraded` 또는 `stale`로 내리고 신규 Evidence를 차단한다.
- `serverShutdown`은 raw stream의 `{"e":"serverShutdown","E":...}` 또는 combined stream의 `{"stream":"!serverShutdown","data":{"e":"serverShutdown","E":...}}` 형식으로 올 수 있다. 이는 가격·체결·호가·candle이 아니므로 normalized market event나 Evidence item으로 승격하지 않고, 명시적으로 allowlist한 operational control event로만 기록한다.
- `serverShutdown` 수신 시 첫 새 연결 시도는 기존 연결의 실제 종료를 기다리지 않고 즉시 수행하되, 동시 연결 수와 전체 reconnect attempt budget 안에서만 수행한다. 첫 시도가 실패한 뒤의 재시도는 위 bounded backoff+jitter를 따른다. 공식 문서가 고정 사전 통지 시간을 보장하지 않으므로 “10분 남음” 같은 timer 전제를 두지 않는다.
- 새 연결이 열렸다는 사실만으로 `healthy`로 복귀하지 않는다. 먼저 기존 stream watermark 이후의 continuity를 공식 stream별 ID·snapshot/recovery 규칙으로 확인하고 gap을 복구하거나 `stale|invalid`로 차단한다. control event, reconnect 시도, continuity 판정과 상태 전이는 감사 가능한 quality event로 남겨 무음 정상 복구를 금지한다.
- REST rate limit 값은 `/api/v3/exchangeInfo`와 response headers에서 읽는다. 정적 숫자를 영구 전제로 고정하지 않는다.
- HTTP 429는 `Retry-After`를 존중하며 즉시 backoff한다. 429 이후 계속 호출해 418 ban을 유발하는 경로는 실패 테스트 대상이다.
- malformed payload, schema drift, raw append 실패, gap, out-of-order, duplicate는 무음 drop하지 않는다. raw quarantine와 quality event를 남기고 건강 상태를 내린다.

### 2.5 데이터 품질 상태

| 상태 | 의미 | 신규 Evidence/Proposal |
|---|---|---|
| `healthy` | schema, freshness, continuity, raw append 모두 만족 | 허용 |
| `degraded` | bounded recovery 중이며 완전성을 보장하지 못함 | 기본 HOLD |
| `stale` | freshness threshold 초과 또는 reconnect/gap 미복구 | 차단 |
| `invalid` | schema 불일치, 필수 필드 누락, raw append 실패 | 차단 |

quality 상태를 정상 값으로 자동 복구하지 않는다. continuity와 raw durability가 다시 검증된 뒤에만 명시적 상태 전이를 수행한다.

## 3. `exchangeInfo`와 거래 규칙 사용 원칙

`GET /api/v3/exchangeInfo`는 현재 거래 규칙과 symbol 정보를 제공하며 rate limit도 포함한다. 이 프로젝트는 이를 다음처럼 사용한다.

1. `BTCUSDT`, `ETHUSDT`를 명시적으로 조회하고 symbol allowlist와 정확히 일치하는지 검사한다.
2. price, quantity, notional 관련 filter는 원문 decimal string과 관측 시각을 보존한다.
3. filter는 Paper Broker와 향후 Testnet preview의 입력이지만, 관측 당시 버전/hash가 없는 filter로 과거 결정을 다시 계산하지 않는다.
4. filter 누락 또는 미지원 rule은 관대한 fallback이 아니라 `HOLD`다.
5. permissions 파라미터가 없으면 Spot 외 permission이 노출될 수 있으므로 응답에 Margin/Leveraged 정보가 있어도 거래 capability로 승격하지 않고 quarantine/무시한다.

## 4. Phase 8 이후 Spot Testnet 전제

이 섹션은 미래 설계 검증용이며 Phase 0~7 구현 범위가 아니다.

- REST base는 `https://testnet.binance.vision/api`다.
- Spot Testnet은 `/api` endpoint만 제공하고 `/sapi`는 제공하지 않는다.
- Testnet 자산은 가상이며 입출금할 수 없다.
- IP/order rate limit과 exchange/symbol filter는 일반적으로 Spot API와 같지만, 최신 값은 `exchangeInfo`로 다시 읽어야 한다.
- Testnet은 사전 통지 없이 대략 월 1회 blank reset될 수 있고 pending/executed orders가 사라질 수 있다. 대조 시스템은 reset을 데이터 손실/불일치로 관측하고, 원인을 확인하기 전 신규 주문을 차단해야 한다.
- Phase 8 전에는 Testnet URL, credential 입력, signed request, order/user-data schema, gateway package/process/DI/config를 제품 코드에 만들지 않는다.
- Phase 8에서도 기본은 `enabled=false`; 별도 process/env, Spot Testnet capability allowlist, 인증된 deterministic execution service inbound, 사람 승인과 reconciliation이 필수다.

## 5. 공식 문서와 원본 요구의 충돌 판정

| 항목 | 판정 | 처리 |
|---|---|---|
| production public data 사용 | 충돌 없음 | market-only REST/WS로 제한 |
| API key 없는 Phase 2 | 공식 보안 유형 `NONE`과 일치 | public client에 secret type 자체 없음 |
| 1m/5m/1h/4h candle | 공식 지원 interval과 일치 | UTC 기준만 MVP 허용 |
| long-lived WS | 24시간 연결 제한과 충돌 | 계획 재연결+gap recovery 필수 |
| 고정 rate limit 가정 | 공식 동적 limit과 충돌 | runtime headers/exchangeInfo 기반 |
| Testnet 영속 상태 가정 | 주기적 reset과 충돌 | reset-aware reconciliation 필수 |
| Mainnet private/live fallback | 안전 정책과 충돌 | 설정 누락·미지 값은 fail-closed |

## 6. 검증 가능한 Acceptance

P0-02는 다음이 모두 참일 때만 `PASS`다.

- 공식 문서 URL, 확인일, 채택한 사실과 설계 영향이 기록되어 있다.
- public REST/WS allowlist가 security type `NONE`만 표현한다.
- rate limit, 24시간 연결, ping/pong, reconnect, schema drift, raw-store failure의 설계 반례가 있다.
- Testnet 전제는 Phase 8로 격리되고 Phase 1~7 capability 0 gate와 모순되지 않는다.
- 원본 요구와 공식 문서의 충돌을 명시적으로 판정했다.
- 이 파일의 SHA-256과 `PASS` verdict가 최종 acceptance evidence 목록에 포함된다.

## 7. 다음 단계 참조

- 미해결: Binance 문서는 계속 변하므로 Phase 2 시작 직전 같은 URL을 다시 확인하고 변경사항을 contract test 입력에 반영한다.
- 핵심 결정: MVP 공개 데이터는 `BTCUSDT`, `ETHUSDT`의 market-only REST/WS와 `NONE` 보안 유형으로 한정한다.
- 안전 결정: Testnet 관련 endpoint·credential·schema·gateway capability는 Phase 8 전 제품 코드에 0개다.
- 다음 단계: P0-04~P0-10 설계가 이 allowlist, quality 상태, 시간 의미와 capability zero gate를 직접 참조해야 한다.
