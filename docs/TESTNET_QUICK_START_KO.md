# Binance Spot Testnet 빠른 시작

이 실행 경로는 **실제 Binance Spot Testnet 주문 효과**를 만듭니다. 자산은 Testnet의 가상 자산이며 실거래소 주문, 출금, 선물, 마진, 레버리지, 숏은 지원하지 않습니다.

화면의 시장 데이터는 현재 기록 재생(Fixture), AI는 Mock Provider입니다. 즉 주문 전송·조회·취소와 Testnet 계정 대사는 실제지만, 투자 판단은 실제 전략이나 실제 LLM 판단이 아닙니다.

## 1. Testnet 전용 키 만들기

1. [Binance Spot Test Network](https://testnet.binance.vision/)에 로그인합니다.
2. Testnet 전용 API Key와 Secret Key를 만듭니다.
3. 이 저장소에는 **Testnet 전용 키만** 사용합니다. 키를 채팅, 이슈, PR, 화면 캡처에 붙여 넣지 않습니다.

Spot Testnet은 가상 자산을 사용하며 주기적으로 초기화될 수 있습니다. `/api` 기능만 제공되고 `/sapi`는 지원하지 않습니다.

## 2. 실행

Docker Desktop을 켠 뒤 저장소 루트의 다음 파일을 더블 클릭합니다.

```text
START_TESTNET.cmd
```

최초 실행에는 세 가지 숨김 입력이 나타납니다.

1. Woozoo 로컬 로그인 비밀번호와 확인 입력
2. Spot Testnet API Key
3. Spot Testnet Secret Key

입력값은 화면이나 `.env`에 표시되지 않습니다. `.secrets/phase8/` 아래의 Git 무시 파일에 저장되고, 거래소 키 두 개는 Gateway 컨테이너에만 마운트됩니다.

Docker 이미지 빌드와 초기 데이터 준비가 끝나면 브라우저가 다음 주소로 열립니다.

```text
https://localhost:3443/testnet
```

로컬 자체 서명 인증서 경고가 나오면 주소가 정확히 `localhost:3443`인지 확인한 뒤 로컬 사이트 접속을 계속합니다.

## 3. 실제 Testnet 주문 흐름

1. 최초에 만든 Woozoo 로컬 비밀번호로 로그인합니다.
2. `Testnet` 화면에서 `Spot Testnet 활성화 요청`을 누르고 이유를 입력합니다.
3. Gateway가 실제 Testnet의 서버 시각, 계정 잔고, BTCUSDT·ETHUSDT 미체결 주문을 조회합니다.
4. 상태가 `READY`, 대사가 `HEALTHY`, 안전 차단이 `INACTIVE`가 될 때까지 기다립니다.
5. 홈에서 BTC 또는 ETH 분석을 실행합니다. 이 분석은 기록 재생 데이터와 Mock AI를 사용합니다.
6. 제안 화면에서 `Testnet 미리보기`를 엽니다.
7. 종목·방향·수량·지정가·최대 명목금액을 직접 확인합니다.
8. 별도 확인 창에서 주문별 승인을 제출합니다.
9. 승인된 `LIMIT GTC` 주문만 Gateway가 실제 Spot Testnet으로 한 번 전송합니다.
10. 실행 화면에서 거래소 주문 상태와 체결 관측을 확인합니다. 미체결 또는 부분 체결 주문은 별도 취소 승인으로 취소할 수 있습니다.

활성화만으로 주문이 나가지는 않습니다. 각 주문에는 별도의 5분 이내 승인과 일회성 권한이 필요합니다.

## 4. 안전 장치

- `TRADING_MODE=paper`가 아니면 시작하지 않습니다.
- Gateway는 `https://testnet.binance.vision`과 `wss://ws-api.testnet.binance.vision/ws-api/v3`만 사용합니다.
- 허용 주문은 BTCUSDT·ETHUSDT의 `LIMIT GTC` 매수·매도와 기존 주문 조회·취소뿐입니다.
- 브라우저와 Mock AI는 거래소 키, 잔고 원문, 서명, Gateway 경로를 받지 않습니다.
- Kill Switch, 데이터 이상, 대사 실패, 계정 초기화 의심, 알 수 없는 주문 결과가 있으면 신규 주문을 차단합니다.
- 네트워크 타임아웃이나 5xx 결과는 재주문하지 않고 같은 `clientOrderId`로 상태를 확인합니다.

## 5. 종료와 재시작

실행 창에서 `Ctrl+C`를 누르면 화면과 컨테이너가 종료됩니다. PostgreSQL 데이터 볼륨과 secret 파일은 유지되므로 다음 실행에는 키를 다시 입력하지 않습니다.

키를 교체하려면 앱을 종료한 뒤 다음 두 파일만 삭제하고 `START_TESTNET.cmd`를 다시 실행합니다.

```text
.secrets\phase8\spot-testnet-api-key.secret
.secrets\phase8\spot-testnet-signing-secret.secret
```

## 6. 자주 발생하는 오류

### API Key가 잘못됐거나 권한이 없음

Testnet에서 새 키를 만든 뒤 위의 키 파일 두 개를 삭제하고 다시 실행합니다. 실거래소 키를 사용하지 않습니다.

### Gateway가 ACTIVATING에서 멈춤

Docker 로그를 확인합니다.

```powershell
docker compose --env-file .env.phase8.local -f compose.phase8.yaml logs --tail 100 spot-testnet-gateway-reconciliation testnet-execution-worker
```

계정 대사 실패나 Testnet 초기화가 감지되면 화면의 차단 이유를 확인합니다. 초기화 확인은 현재 세대의 체크포인트를 검토한 뒤에만 제출합니다.

### 주문이 거래소에서 거절됨

거래소의 현재 종목 필터, 가상 잔고, 지정가 범위를 확인합니다. 거절된 주문은 자동 재전송하지 않습니다. 시장 데이터와 AI가 Fixture/Mock이므로 미리보기 가격이 현재 시장과 맞지 않을 수 있습니다.

### 5438, 8008, 3001 또는 3443 포트가 이미 사용 중

기존 Woozoo 실행 창을 먼저 종료합니다. 그래도 남아 있으면 다음으로 사용 프로세스를 확인합니다.

```powershell
Get-NetTCPConnection -LocalPort 5438,8008,3001,3443 -ErrorAction SilentlyContinue
```

### 포트 8008 대기 시간이 초과됨

Phase 8은 내부 전용 DB·API 네트워크를 외부 통신 가능하게 바꾸지 않고, 비밀이 없는 loopback 프록시만 `127.0.0.1`에 연결합니다. 다음 두 프록시가 `healthy`인지 확인합니다.

```powershell
docker compose --env-file .env.phase8.local -f compose.phase8.yaml --profile runtime --profile testnet-gateway ps postgres-loopback-proxy control-api-loopback-proxy
Test-NetConnection 127.0.0.1 -Port 5438
Test-NetConnection 127.0.0.1 -Port 8008
```

두 포트의 `TcpTestSucceeded`가 `True`가 아니면 기존 실행 창을 닫고 `START_TESTNET.cmd`를 다시 실행합니다. 이 오류만으로 Testnet 키 파일을 삭제할 필요는 없습니다.

### 전체 컨테이너 상태 확인

```powershell
docker compose --env-file .env.phase8.local -f compose.phase8.yaml --profile runtime --profile testnet-gateway ps
```
