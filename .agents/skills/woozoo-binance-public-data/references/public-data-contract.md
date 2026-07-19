# Binance 공개 데이터 계약

## 공식 근거

- [Binance Spot WebSocket Streams](https://developers.binance.com/en/docs/products/spot/web-socket-streams), 확인일 2026-07-19 (Asia/Seoul): `serverShutdown` raw/combined payload와 가능한 빠른 새 연결 요구
- [Binance Spot API Changelog](https://github.com/binance/binance-spot-api-docs/blob/master/CHANGELOG.md), 확인일 2026-07-19 (Asia/Seoul): 2026-06-09 고정 사전 통지 시간 제거 정정

## Endpoint 승인 기록

각 endpoint/stream마다 다음을 기록한다.

- 공식 문서 URL과 확인 일자
- host, protocol, method, path 또는 stream name
- 인증 요구: 반드시 none
- capability: market-data-read
- symbols와 rate/connection limit
- event time, sequence/update ID, snapshot/delta 규칙
- 오류·재연결·gap 복구 방식
- 소유 서비스와 normalized schema version

host만으로 public/private를 판정하지 않는다. 같은 host가 양쪽 capability를 제공할 수 있으므로 method, path, 인증, 기능을 함께 allowlist한다.

## 정규화 시장 이벤트 필수 필드

- `event_id`, `event_type`, `schema_version`
- `source`, `symbol`
- `event_time`, `received_at`
- `sequence` 또는 공식 dedupe 식별자
- `raw_payload_hash`, `correlation_id`
- `quality_status`, `quality_reasons`
- `stream_watermark` 또는 snapshot별 동등한 완결성 표식

Decimal 값은 문자열로 직렬화하고 UTC ISO-8601을 사용한다.

`knowledge_cutoff`은 개별 normalized market event의 속성이 아니다. Evidence 생성 command와 immutable snapshot recipe에는 `as_of`, `knowledge_cutoff`과 bound stream watermark가 필수이며, 포함되는 모든 항목은 `event_time <= as_of`와 `received_at <= knowledge_cutoff`를 각각 만족해야 한다.

## 운영 제어 이벤트

`serverShutdown`은 공식 raw payload `{"e":"serverShutdown","E":...}`와 combined payload `{"stream":"!serverShutdown","data":{"e":"serverShutdown","E":...}}`만 allowlist한다. 이는 symbol별 시장 관측값이 아닌 connection-scoped operational control event다. normalized market event schema, feature 계산, Evidence market item에 넣지 않으며 `source`, source event time, `received_at`, connection/session ID, raw payload hash와 처리 결과를 별도 감사 레코드로 보존한다.

수신 시 첫 새 연결 시도는 기존 socket 종료를 기다리지 않고 즉시 수행하되 동시 연결 한도와 전체 attempt budget으로 bounded해야 하며, 첫 실패 뒤의 재시도는 bounded backoff+jitter를 따른다. 새 socket 연결 성공은 건강성 증거가 아니다. 이전 bound stream watermark 이후 continuity를 stream별 공식 ID·snapshot/delta 규칙으로 검증하고 gap을 복구한 뒤에만 명시적으로 `healthy`로 전이한다. 검증 실패·시간 초과는 `degraded|stale|invalid`와 Evidence 차단을 유지하며 control event, 재연결, gap 판정 또는 상태 전이를 무음 처리하지 않는다.

## 데이터 품질 상태

- healthy: freshness, sequence, completeness, clock 기준 통과
- degraded: 분석은 가능하나 Proposal 근거로 사용하지 않는 제한 상태
- stale: freshness 초과, 신규 결정 차단
- invalid: gap, schema, 미래 오염 또는 무결성 실패, 신규 결정 차단

## 검증 묶음

- recorded fixtures에 대한 schema·Decimal·timestamp 검증
- 중복, out-of-order, gap, reconnect, rate-limit, malformed payload
- raw/combined `serverShutdown`을 operational control로만 수용하고 market/Evidence effect 0, bounded immediate reconnect, continuity 검증 전 healthy 승격 0
- bounded queue 포화, backpressure, append-only raw store 실패와 무음 drop 차단
- `as_of` 경계와 `knowledge_cutoff` 경계 직전·동일·직후 이벤트 포함 여부
- 늦게 도착한 과거 이벤트와 뉴스 `published_at`/`observed_at` look-ahead 차단
- 프로세스 재시작 후 replay 동등성
- allowlist 밖 endpoint와 인증 요구 endpoint의 결정적 거절
