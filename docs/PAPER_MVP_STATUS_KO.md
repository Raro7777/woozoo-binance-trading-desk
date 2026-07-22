# PR #8 — Phase 7 Paper Trading Room MVP

이 PR은 **BTC·ETH 기록 재생 데이터와 Mock AI를 사용해, 사람 승인 후 결정론적 Paper 주문·원장까지 검증하는 로컬 모의투자 운영실**이다. 실시간 투자 서비스나 실제 매매전략은 아니다.

## 실제로 동작하는 기능

- PostgreSQL·Redis·FastAPI·Next.js를 연결한 로컬 HTTPS 실행
- BTCUSDT·ETHUSDT 캔들 정규화, Decimal Feature와 immutable Evidence Snapshot
- 구조화 분석 workflow, TradeProposal 영속화, 결정론적 Risk Engine
- 로컬 운영자 로그인, Proposal 승인·거절, 일회성 Paper 실행 권한
- 최우선 호가에 결합한 Paper LIMIT 주문, 부분 체결·수수료·취소
- 잔고·포지션·주문 projection, 복식 원장, Reconciliation, 감사 로그
- Kill Switch 활성화, 열린 주문 취소, 보호 조건 확인 후 수동 복구
- Trading Room, 분석, 승인, Paper, 감사, 운영 화면

## Mock 또는 미완성 기능

- AI는 실제 LLM을 호출하지 않는 deterministic `MockLlmProvider`다.
- Bull/Bear/Trader 결과는 실제 시장 판단이 아니라 고정된 예제 응답이다.
- 기본 로컬 실행 데이터는 실제 Binance 실시간 연결이 아니라 기록 재생(Fixture)이다.
- Paper 체결은 최신 최우선 bid/ask와 표시 수량을 보지만, 주문장 깊이·대기열·지연·시장 충격은 모델링하지 않는 단순 시뮬레이션이다.
- 수수료는 실제 계정 등급이 아닌 고정 0.1% 모형이다.
- PnL 계산 코드는 있으나 현재 Portfolio UI에는 PnL 항목이 표시되지 않는다.
- 공개 WebSocket·REST 수집 코어는 있으나 제품용 실시간 수집·REST warm-up·Evidence scheduler로 조립되지 않았다.

## 아직 없는 기능

- Binance Spot Testnet Gateway와 실제 API Key 연결
- Binance Mainnet private 주문, 실거래, 출금
- Futures, Margin, Leverage, Short
- 실제 LLM Provider
- 백테스트로 검증된 투자전략·시장 regime 판단
- 다중 사용자·SSO·권한 관리
- 실제 거래소 수준의 matching·latency·market impact 모델

## 기능별 현재 판정

| 기능 | 상태 | 현재 의미 |
|---|---|---|
| Binance BTCUSDT·ETHUSDT 공개 데이터 수집 | 부분 구현 | 실제 공개 WS 전송 코어는 있으나 기본 MVP는 Fixture 재생 |
| WebSocket 데이터 | 부분 구현 | 실제 공개 stream 연결 코드는 구현, 제품 실행기에 미조립 |
| REST 데이터 | 부분 구현 | 무인증 REST 어댑터는 구현, 기본 runner에는 미연결 |
| 캔들 및 Feature 계산 | 실제 구현 | closed candle에서 Decimal 수익률·SMA20·RSI14 계산 |
| Evidence Snapshot | 실제 구현 | point-in-time 조건과 provenance를 지키며 DB에 불변 저장 |
| AI 분석 에이전트 | Mock | workflow는 실제지만 외부 LLM 호출 없음 |
| Bull/Bear 분석 | Mock | 역할 구조만 실제이며 판단 문구는 고정 예제 |
| TradeProposal | 부분 구현 | 계약·hash·DB 저장은 실제, 방향 신호는 Mock에서 생성 |
| Risk Engine | 실제 구현 | stale·Kill·대사·중복·잔고·노출·손실 등을 결정론적으로 검사 |
| 사용자 승인·거절 | 실제 구현 | 인증 세션·CSRF·버전·멱등성에 결합 |
| Paper Broker | 실제 구현 | 거래소가 아닌 로컬 결정론적 시뮬레이터 |
| 부분 체결 | 실제 구현 | 표시 수량의 10% 참여율을 사용한 단순 모형 |
| 수수료 | 실제 구현 | quote asset 고정 0.1% 모형 |
| 포트폴리오와 PnL | 부분 구현 | 잔고·포지션·주문은 UI 제공, PnL UI는 없음 |
| 복식 원장 | 실제 구현 | commodity별 debit=credit와 거래·entry 불변 저장 |
| Reconciliation | 실제 구현 | DB projection과 원장을 대조하며 실패 시 주문 차단 |
| Kill Switch | 실제 구현 | 신규 주문 차단·열린 주문 취소·수동 복구 |
| Trading Room GUI | 실제 구현 | 한국어 주요 화면과 로컬 HTTPS 실행기 제공 |
| 로그인·사용자 인증 | 실제 구현 | 단일 로컬 운영자 Argon2id·서버 세션, 다중 사용자는 아님 |
| Binance Spot Testnet | 없음 | Phase 8 중단, 이 PR에 포함하지 않음 |
| 실제 매매 전략과 시장 판단 로직 | 없음 | Feature는 계산하지만 매매 신호로 해석하는 전략은 없음 |

## 로컬 실행

전체 절차는 `docs/QUICK_START_KO.md`를 따른다.

```powershell
corepack pnpm bootstrap
corepack pnpm env:init
corepack pnpm paper:mvp:start
```

접속 주소는 `https://localhost:3443`이다. 첫 실행 전에 저장소 밖 임시 파일을 사용해 로컬 운영자 verifier를 한 번 생성해야 한다.

## 통과 기준 테스트

```powershell
corepack pnpm test:core
corepack pnpm test:safety
corepack pnpm test:integration
corepack pnpm test:e2e
corepack pnpm ci
```

현재 정리 commit 기준 결과:

- lint, Python·TypeScript typecheck, production build: 통과
- Core: 264개 통과(252 Python + 12 Node), 역사적 Manifest 검사 1개는 일반 CI에서 제외
- Safety: 70개 통과(69 Python + capability-zero Node 1개)
- Integration: 148개 통과(142 Python + 웹 통합 6개)
- E2E: 19개 통과(HTTPS 로그인, 분석, Risk, 승인, Paper 주문·부분 체결, 포트폴리오, 감사, Kill Switch, 데스크톱·모바일)
- `pnpm ci`: 통과

일반 CI는 제품 테스트 결과로 판정한다. 과거 Phase acceptance artifact는 보존하지만 새 일반 변경마다 다시 만들지 않는다.

## 안전 경계

- `TRADING_MODE=paper` 명시 필수, 누락·오류 값은 시작 실패
- 비공개 Mainnet API·Testnet private Gateway·출금·파생상품·레버리지·숏 없음
- Binance Credential을 요구하거나 저장하지 않음
- AI는 Proposal만 생성하고 주문·잔고·Risk 정책·Credential에 접근하지 않음
- Decimal 금융 계산, 단회 승인 권한, 주문 멱등성 유지
- Kill Switch ON 또는 Reconciliation 실패 시 신규 주문 차단

## 사용자 확인이 필요한 항목

1. `docs/QUICK_START_KO.md`대로 깨끗한 로컬 환경에서 실행되는지
2. 운영 화면이 Fixture·Mock·PAPER·Testnet 비활성·Mainnet 미지원 상태를 오해 없이 보여주는지
3. BTC/ETH 분석 → Proposal → Risk → 승인 → Paper 주문 → 포트폴리오·감사 흐름을 직접 확인할 수 있는지
4. Kill Switch 활성화와 보호 조건 확인 후 수동 복구가 이해하기 쉬운지

Phase 8 Testnet 작업은 이 PR에 포함하지 않았으며 계속 구현하지 않는다.
