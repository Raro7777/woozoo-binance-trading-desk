# Woozoo Paper MVP 빠른 시작

이 안내서는 Windows PowerShell 기준이다. 기본 실행은 **실제 Binance 실시간 데이터가 아닌 BTC·ETH 기록 재생(Fixture)** 과 **실제 LLM이 아닌 Mock AI**를 사용한다. 거래소 계정이나 API Key는 필요하지 않으며 실제 주문은 발생하지 않는다.

## 가장 쉬운 방법: 더블클릭 한 번

1. Docker Desktop을 실행한다.
2. 저장소 폴더의 `START_PAPER_MVP.cmd`를 더블클릭한다.
3. 처음 한 번만 검은 창에서 사용할 로그인 비밀번호를 두 번 입력한다. 입력 문자는 화면에 표시되지 않는다.
4. 준비가 끝나면 브라우저가 `https://localhost:3443`으로 자동 실행된다.
5. 앱을 끌 때는 검은 창에서 `Ctrl+C`를 누른다. Docker 컨테이너까지 끄려면 `STOP_PAPER_MVP.cmd`를 더블클릭한다.

실행 파일이 의존성 설치, `.env.local` 생성, 비밀번호 verifier 생성, PostgreSQL·Redis 시작, DB 초기화, 서버 실행을 순서대로 처리한다. 비밀번호는 채팅에 보내거나 명령줄에 적지 않는다. 아래 절차는 수동 실행이나 오류 해결이 필요할 때만 사용한다.

## 1. 필요한 프로그램

- Git
- Node.js 20.9 이상, 25 미만
- Python 3.13
- Docker Desktop과 Docker Compose
- PowerShell

Docker Desktop을 먼저 실행한 뒤 PowerShell에서 저장소 폴더로 이동한다.

```powershell
cd C:\Users\xxx\woozoo-binance-trading-desk
git fetch origin
git switch codex/phase-7-trading-room
```

로컬 브랜치가 아직 없다면 마지막 명령 대신 다음을 실행한다.

```powershell
git switch --track origin/codex/phase-7-trading-room
```

## 2. 의존성과 환경변수 준비(수동 실행)

```powershell
corepack enable
corepack pnpm bootstrap
corepack pnpm env:init
```

`.env.local`은 다음 안전한 기본값을 사용한다.

- `TRADING_MODE=paper`
- `MARKET_DATA_SOURCE=recorded`
- `LLM_PROVIDER=mock`
- PostgreSQL `127.0.0.1:5433`
- Redis `127.0.0.1:6380`
- 웹 `https://localhost:3443`

이 값을 Testnet이나 Mainnet 값으로 바꾸지 않는다. 잘못된 거래 모드나 데이터·AI 설정은 실행기가 시작을 거부한다.

## 3. 최초 로그인 비밀번호 준비(수동 실행)

다음 명령은 입력 내용을 화면에 표시하지 않는다. 비밀번호를 저장소 밖의 Windows 임시 파일에 잠시 쓰고 Argon2id verifier를 만든 뒤 즉시 임시 파일을 지운다.

```powershell
$secretFile = Join-Path $env:TEMP "woozoo-operator-secret.txt"
$securePassword = Read-Host "Woozoo에서 사용할 로컬 비밀번호" -AsSecureString
$passwordPointer = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
try {
  $operatorPassword = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
  [System.IO.File]::WriteAllText($secretFile, $operatorPassword, [System.Text.UTF8Encoding]::new($false))
} finally {
  $operatorPassword = $null
  [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
}
python -m uv run --locked python scripts/bootstrap-local-operator.py --secret-file $secretFile --verifier-file .secrets/operator.argon2id
Remove-Item -LiteralPath $secretFile
```

이 작업은 최초 한 번만 한다. 이후 로그인에는 방금 정한 비밀번호를 사용한다. verifier 파일은 `.secrets/`에 있으며 Git에 포함되지 않는다.

## 4. Docker, DB, 서버 실행(수동 실행)

아래 한 명령이 PostgreSQL·Redis를 시작하고, DB migration과 기록 재생 데이터를 준비한 뒤 FastAPI·Paper worker·웹·로컬 HTTPS 프록시를 실행한다.

```powershell
corepack pnpm paper:mvp:start
```

터미널에 `실행 준비가 끝났습니다`와 접속 주소가 표시될 때까지 기다린다. 브라우저에서 다음 주소를 연다.

```text
https://localhost:3443
```

로컬 자체 서명 인증서 경고가 나오면 주소가 정확히 `localhost:3443`인지 확인한 뒤 로컬 사이트 접속을 계속한다.

## 5. 로그인과 BTC·ETH 데이터 확인

1. 오른쪽 위 `운영자 로그인` 또는 `세션 관리`를 누른다.
2. 최초 설정에서 정한 비밀번호를 입력하고 `안전하게 로그인`을 누른다.
3. 첫 화면에서 BTC / USDT와 ETH / USDT 카드가 `정상`인지 확인한다.
4. `운영` 화면의 제품 상태에서 다음을 확인한다.
   - 데이터 원천: `기록 재생(Fixture)`
   - AI 제공자: `Mock`
   - 거래 모드: `PAPER`
   - Testnet: `비활성`
   - Mainnet: `지원 안 함`
   - PostgreSQL·Redis: `정상`

표시 가격은 실제 현재 Binance 가격이 아니라 검증용 기록 재생 가격이다.

## 6. AI 분석과 Paper 주문

1. `트레이딩룸`으로 이동한다.
2. `BTC / USDT 분석` 또는 `ETH / USDT 분석`을 누른다.
3. 분석 화면에서 Evidence와 Mock 분석 결과를 확인한다.
4. `승인 화면 열기`를 누른다.
5. Risk 결과가 `허용됨`인지, 종목·수량·지정가·수수료·최우선 호가가 맞는지 확인한다.
6. 주문을 진행하려면 `정확한 미리보기 승인`을 누르고 확인 창에서 `승인 제출`을 누른다.
7. 거절하려면 `거절`을 누른다.

승인은 해당 미리보기 한 건에만 사용할 수 있다. 화면이 오래되었거나 서버 상태가 달라지면 409로 보류되며 다시 확인해야 한다.

## 7. 포트폴리오와 감사 로그 확인

1. 상단 `모의투자` 또는 첫 화면의 `모의투자 데스크`를 연다.
2. USDT·BTC·ETH 잔고, 보류 금액, 주문 상태, 원장 checkpoint를 확인한다.
3. `감사 기록`에서 분석·Risk·승인·주문·체결·원장 이벤트를 시간순으로 확인한다.

현재 화면은 잔고·포지션·주문을 보여주지만 PnL 항목은 아직 표시하지 않는다.

## 8. Kill Switch 사용

1. `운영` 화면으로 이동한다.
2. 실제 차단이 필요한 경우에만 `킬 스위치 활성화`를 누른다.
3. 사고 사유를 입력하고 `활성화 제출`을 누른다.
4. ON 상태에서는 신규 Paper 주문과 체결이 차단되고 열린 주문 취소가 진행된다.
5. 데이터·대사·원장·주문 취소 상태가 모두 정상인지 확인한다.
6. 원인을 해결했다고 판단한 뒤에만 `해결 검증 후 복구`를 누르고 내용을 입력한다.

복구는 자동으로 실행되지 않는다. 서버 보호 조건이 하나라도 실패하면 버튼이 활성화되지 않는다.

## 9. 종료와 재시작

앱을 실행한 PowerShell 창에서 `Ctrl+C`를 누르면 웹·API·worker만 종료되고 DB·Redis 데이터는 유지된다.

컨테이너까지 종료하려면 다음을 실행한다.

```powershell
corepack pnpm paper:mvp:stop
```

Windows에서는 저장소 폴더의 `STOP_PAPER_MVP.cmd`를 더블클릭해도 같은 작업을 한다. DB 데이터와 로그인 설정은 삭제되지 않는다.

다시 시작할 때는 다음 한 명령만 실행한다.

```powershell
corepack pnpm paper:mvp:start
```

## 10. 자주 발생하는 오류

### Docker 연결 오류

Docker Desktop이 실행 중인지 확인한다.

```powershell
docker version
docker compose version
```

### 5433, 6380 또는 3443 포트 사용 중

다른 PostgreSQL·Redis·Woozoo 실행을 종료한 뒤 다시 실행한다.

```powershell
Get-NetTCPConnection -LocalPort 5433,6380,3443 -ErrorAction SilentlyContinue
```

### verifier가 없다는 오류

3절의 최초 로그인 비밀번호 준비 명령을 실행한다. 이미 verifier가 있으면 새로 만들지 말고 처음 정한 비밀번호를 사용한다.

### 브라우저에서 로그인되지 않음

반드시 `https://localhost:3443`으로 접속한다. `http://localhost:3001`이나 API 포트로 직접 접속하면 보안 쿠키 로그인은 실패한다.

### 409 오류

409는 오래된 화면·일회용 토큰 재사용·데이터 freshness·상태 버전 변경을 안전하게 거부한 결과다.

1. 화면을 새로 고친다.
2. 운영 화면에서 데이터, Risk Engine, Kill Switch, 대사, 원장을 확인한다.
3. 새 분석을 실행하고 새 Proposal 미리보기로 다시 승인한다.

검사를 우회하거나 같은 승인 요청을 반복 재사용하지 않는다.

### 데이터가 지연 또는 중단으로 표시됨

`paper:mvp:start`를 실행한 터미널에서 BTC·ETH 기록 재생 갱신 오류를 확인한다. Docker가 정상인지 확인하고 앱을 `Ctrl+C`로 종료한 뒤 다시 시작한다. 상태가 정상으로 돌아오기 전에는 주문을 승인하지 않는다.

### Kill Switch 복구 버튼이 비활성

데이터·Reconciliation·복식 원장·미체결 주문 취소 중 하나가 아직 정상 상태가 아니다. 운영 화면의 보호 조건을 확인하고 기다린 뒤 새로 고친다. 강제로 우회하지 않는다.
