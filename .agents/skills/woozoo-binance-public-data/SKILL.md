---
name: woozoo-binance-public-data
description: "Woozoo에서 Binance Spot 시세·체결·호가·캔들 WebSocket/REST 수집, 재연결, 중복 제거, replay, freshness, Evidence snapshot 또는 데이터 품질을 설계·구현·검토할 때 사용한다. 무인증 공개 market-data capability만 허용하고 private/account/order/user-data 접근을 차단하므로 Binance 데이터가 언급되면 반드시 사용한다."
---

# Woozoo Binance Public Data

시장 데이터 클라이언트를 주문 클라이언트로 확장 가능한 범용 거래소 클라이언트로 만들지 않는다. Release 1의 수집 경계는 keyless public market data다.

## 작업 절차

1. 현재 Phase를 확인한다. Phase 0에서는 계약과 테스트만 작성한다.
2. 구현 직전에 Binance 공식 Spot 문서에서 host, path, stream 이름, payload, sequence, rate limit과 변경사항을 다시 검증한다.
3. `references/public-data-contract.md`에 따라 endpoint capability와 데이터 품질 계약을 작성한다.
4. allowlist 밖 path, API key, signature, account/order/user-data 요구가 있으면 safety-boundaries로 차단한다.
5. fixture·replay·재연결·stale·중복 테스트를 먼저 정의한다.

## 공개 데이터 경계

- REST는 무인증 공개 시장 정보 조회만, WebSocket은 공개 시장 스트림만 허용한다.
- 인증 헤더, 서명, listen key, 계좌·주문·user-data 스트림을 public collector에 추가하지 않는다.
- symbol은 Release 1의 BTCUSDT와 ETHUSDT allowlist를 기본으로 한다.
- raw event를 보존하고 정규화 이벤트에 source, symbol, event_time, received_at, sequence/update ID, payload hash, schema version을 남긴다.

## 시간·Evidence 무결성

- 의사결정 `as_of`와 `knowledge_cutoff`를 먼저 고정한다. `event_time <= as_of`이면서 `received_at <= knowledge_cutoff`인 데이터만 Evidence에 포함한다.
- 늦게 도착한 과거 이벤트가 당시에는 알 수 없었던 정보를 섞지 못하도록 snapshot에 stream watermark와 수집 상태를 결속한다. 뉴스는 `published_at`과 `observed_at`을 함께 검사한다.
- 이벤트 시간과 수신 시간을 구분하며 서버 시계 오차와 지연 한도를 측정한다.
- freshness와 completeness가 기준 미달이면 데이터 상태를 stale/invalid로 바꾸고 Proposal·주문 경로를 차단한다.
- Evidence snapshot은 불변이고 사용한 raw event/feature/news ID와 품질 상태를 역추적할 수 있어야 한다.

## 스트림 복원력

- bounded retry, 지수 backoff와 jitter, 재연결 한도, heartbeat를 정의한다.
- 재연결 시 snapshot+delta 또는 공식 sequence 규칙으로 gap을 복구한다. 추측으로 이어 붙이지 않는다.
- 중복 이벤트는 안정적 키로 제거하고 out-of-order/gap을 관측 가능하게 기록한다.
- raw event는 append-only로 보존한다. bounded queue와 backpressure 정책을 두며 overflow에서 이벤트를 조용히 버리지 않고 상태를 stale/invalid로 전환해 결정 경로를 차단한다.
- 기록 이벤트 replay가 live normalization과 동일한 결정적 결과를 내는지 검증한다.

## 테스트 시나리오

- 정상: BTCUSDT 공개 체결·book ticker·kline 입력을 정규화하고 `as_of` 이전 Evidence만 만든다.
- 장애: 연결 단절과 sequence gap에서 상태를 stale로 전환하고 복구 전 신규 Proposal을 차단한다.
- 차단: API key나 signed timestamp가 필요한 endpoint를 collector에 추가하려는 변경을 거절한다.
