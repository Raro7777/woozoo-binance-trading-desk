# Paper MVP 개발 절차

Phase 8 작업은 일시 중단되어 있다. 현재 기본 개발 대상은 PR #8의 Phase 7 Paper Trading Room이다. 과거 Phase 0~7 Manifest, acceptance JSON, digest, 리뷰 기록은 감사 이력으로 보존하지만 일반 변경의 완료 조건으로 다시 생성하지 않는다.

## 일반 변경 완료 기준

일반 기능·버그·문서·UI 변경은 다음 네 단계로 끝낸다.

1. 코드와 문서를 구현한다.
2. 변경 범위에 맞는 테스트를 통과시킨다.
3. diff를 자체 검토한다.
4. Draft PR에 실제 상태와 테스트 결과를 보고한다.

일반 변경에는 파일별 SHA-256 승인, 테스트별 JSON artifact, acceptance digest, 동일 Codex 엔진의 반복 검토, 매 작업의 외부 리뷰, 사소한 UI 변경의 Phase 승인을 요구하지 않는다.

## 강화 검증 대상

다음 변경은 일반 변경보다 강한 테스트와 역할 분리 검토를 유지한다.

- 금융 원장과 Decimal 계산
- Risk Engine과 정책 경계
- 주문 멱등성과 중복 주문 방지
- Kill Switch 활성화·복구
- Binance Spot Testnet 주문 경로
- 향후 Mainnet 전환

Testnet과 Mainnet은 현재 구현 대상이 아니다. 강화 검증은 해당 기능을 미리 만드는 허가가 아니다.

## 표준 테스트 명령

```powershell
corepack pnpm test:core
corepack pnpm test:safety
corepack pnpm test:integration
corepack pnpm test:e2e
corepack pnpm ci
```

- `test:core`: 단위·계약·Property·Replay 테스트, Decimal 금융 계산, Paper 주문·PnL·원장 불변식
- `test:safety`: Mainnet private·출금·파생상품·AI 주문 접근 차단, 중복 주문, Kill Switch, stale 데이터, Failure 테스트
- `test:integration`: PostgreSQL·Redis·FastAPI·서비스 연결, 승인부터 Paper 체결·원장 반영까지
- `test:e2e`: HTTPS 로그인부터 분석·Risk·승인·Paper 주문·포트폴리오·Kill Switch까지
- `ci`: bootstrap과 환경 검사, lint, typecheck, 네 테스트 그룹, build

과거 acceptance 재현이 꼭 필요할 때만 `test:legacy:*` 명령을 명시적으로 사용한다. 일반 CI는 Phase Manifest나 artifact digest가 오래되었다는 이유만으로 실패하지 않는다.

## 계속 유지하는 안전 경계

- `TRADING_MODE=paper`가 아니면 시작 실패
- Binance 비공개 Mainnet API, 출금, Futures, Margin, Leverage, Short 없음
- Testnet private Gateway 없음
- AI는 Evidence를 읽고 Proposal만 생성하며 주문·잔고·Risk 정책·비밀에 접근하지 않음
- 금융 계산은 Decimal과 결정론적 코드가 권위
- 사용자 승인과 Paper 실행 권한은 일회성
- Kill Switch 또는 Reconciliation 실패 시 신규 주문 차단

