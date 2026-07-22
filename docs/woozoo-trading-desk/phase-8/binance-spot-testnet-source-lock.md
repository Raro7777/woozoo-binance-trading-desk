# Binance Spot Testnet 공식 사양 잠금

검증 시각: 2026-07-21T19:53:27Z
공식 저장소: `binance/binance-spot-api-docs`
고정 revision: `a5e0bc3ddc0fd7e6bb696849323b74423fa3a54d`
revision 시각: 2026-07-21T06:29:45Z

이 문서는 Phase 8 구현에 허용할 최소 capability만 고정한다. Binance 문서 전체가
허용 목록은 아니며, 이 목록 밖 기능은 문서에 존재하더라도 차단한다.

## 1. 공식 근거

- Testnet 일반 정보: `testnet/general-info.md`, SHA-256
  `f6ff938f0d8bb8bb6b0496ce3010aaf847c385060da49d271d34b9b8ebb60be1`
- REST·서명·시간·주문·계좌: `testnet/rest-api.md`, SHA-256
  `a73a3d2103387e4442b91b28b12f7e59bbf2479a1ee117fdd261d2521c5625a2`
- User Data 구독: `testnet/web-socket-api.md`, SHA-256
  `5ee59d1a59f8b5c3410af618b235203a4e1c6959afdf735950fbaa5bc068de24`
- User Data event: `testnet/user-data-stream.md`, SHA-256
  `294359e1784330a89bbece4528f1fa7ad2032588d3864bb38a3c3aa3b6abdbf0`
- reset 이력: `testnet/CHANGELOG.md`, SHA-256
  `dafa7dc19acad93676115cd1629d402bfc8b42e7a98e0c421f4c6e19690da3a3`
- 전역 제거 공지: `CHANGELOG.md`, SHA-256
  `fe345417d817bb7f64f087d87f11204a7311a9e97c13af5a5ed2a8bef26ba172`

각 파일은 위 고정 revision의 raw bytes를 기준으로 한다. 구현과 인수 manifest는
revision뿐 아니라 사용한 파일의 SHA-256도 결속해야 한다.

## 2. 최소 네트워크 allowlist

### REST

정확한 origin은 `https://testnet.binance.vision` 하나다. 공식 문서에 있는 보조
호스트를 자동 fallback으로 사용하지 않는다.

| Capability | Method | Path | Security | 추가 제한 |
|---|---|---|---|---|
| server-time | GET | `/api/v3/time` | NONE | 시간 오차 측정 전용 |
| exchange-info | GET | `/api/v3/exchangeInfo` | NONE | `BTCUSDT`, `ETHUSDT` filter만 사용 |
| create-limit-order | POST | `/api/v3/order` | TRADE/SIGNED | LIMIT, GTC, 정확한 `newClientOrderId` 필수 |
| cancel-order | DELETE | `/api/v3/order` | TRADE/SIGNED | symbol + 정확한 `origClientOrderId` 필수 |
| query-order | GET | `/api/v3/order` | USER_DATA/SIGNED | symbol + 정확한 `origClientOrderId` 필수 |
| query-open-orders | GET | `/api/v3/openOrders` | USER_DATA/SIGNED | symbol 생략 금지 |
| query-account | GET | `/api/v3/account` | USER_DATA/SIGNED | 대조 전용 |
| query-trades | GET | `/api/v3/myTrades` | USER_DATA/SIGNED | symbol + 알려진 orderId에 한정 |

`/sapi`, Mainnet private origin, 주문 목록, 시장가, stop, iceberg, peg, SOR,
cancel-replace, bulk cancel, order amend는 허용하지 않는다.

### User Data WebSocket API

- 정확한 URL: `wss://ws-api.testnet.binance.vision/ws-api/v3`
- 허용 method: `userDataStream.subscribe.signature`, `session.subscriptions`,
  `userDataStream.unsubscribe`
- 허용 event: `executionReport`, `outboundAccountPosition`, `balanceUpdate`,
  `eventStreamTerminated`
- WebSocket을 통한 주문·취소·계좌 query method는 금지한다.
- 2026-02-20 제거된 REST listen-key lifecycle인 `POST|PUT|DELETE
  /api/v3/userDataStream`은 구현하지 않는다.

## 3. 서명과 시간

- 초기 구현은 HMAC-SHA-256 Testnet key만 지원한다. 키 형식을 자동 추론하거나 다른
  서명 방식으로 fallback하지 않는다.
- `X-MBX-APIKEY`와 signature는 Gateway 프로세스 안에서만 생성한다.
- 모든 SIGNED request는 millisecond `timestamp`와 명시적 `recvWindow=5000`을
  사용한다. 60000까지 자동 확대하지 않는다.
- canonical query serialization은 키 순서를 고정하고 동일 bytes를 서명·전송한다.
- 서버 시간 오차가 정책 한도를 넘거나 시간 조회가 실패하면 외부 효과를 만들지 않는다.

## 4. 결과 불명 계약

- HTTP 5xx, transport timeout, connection loss와 Binance `-1007 TIMEOUT`은
  `UNKNOWN`이다. 실패나 거절로 확정하지 않는다.
- `UNKNOWN` 뒤에는 같은 approval, nonce 또는 새 Client Order ID로 주문을 다시
  제출하지 않는다.
- 동일 `newClientOrderId`를 `origClientOrderId`로 조회하고 User Data event와 REST
  조회를 대조해 `FOUND`, `REJECTED`, `NOT_FOUND_CONFIRMED` 중 하나로 확정한다.
- 하나의 REST 응답이나 하나의 WebSocket event만으로 최종 권위를 만들지 않는다.

## 5. Testnet reset 계약

공식 Testnet은 약 월 1회, 사전 통지 없이 pending/executed order를 포함한 데이터를
초기화하고 새 가상 잔고를 지급한다. API key는 보존될 수 있다.

- 알려진 주문의 설명되지 않는 소실이나 전 계좌 잔고 세대 변화는
  `RESET_SUSPECTED`와 Kill/HOLD를 만든다.
- 자동으로 새 계좌 세대를 승인하거나 소실 주문을 실패·체결·손실로 해석하지 않는다.
- 인증된 운영자가 reset 증거와 checkpoint를 확인해야 새 `account_generation`을
  시작할 수 있다.
- 이전 세대의 주문·fill·승인·원장은 immutable하게 보존하고 새 세대와 합산하지 않는다.

## 6. Secret·활성화 경계

- Gateway 기본값은 `enabled=false`다.
- credential 값 자체를 일반 환경 변수, 브라우저, Control API, AI, 로그, trace,
  fixture에 넣지 않는다. Gateway 전용 file path 설정만 허용한다.
- credential file이 없거나 권한·형식·Testnet account binding이 검증되지 않으면
  프로세스는 외부 capability를 열지 않는다.
- 실제 외부 Testnet 주문은 별도 digest-bound 사람 승인, Kill OFF, fresh Evidence,
  ALLOWED Risk, healthy reconciliation, 단일 nonce와 command/client-order binding이
  모두 같은 transaction에서 확인된 뒤에만 가능하다.

## 7. 재검증 조건

공식 revision이 바뀌거나 endpoint/event/error 의미가 달라지면 source lock을 새로
작성하고 계약·fixture·negative test를 재검토한다. 자동 업그레이드는 금지한다.
