# Phase 8 전환 하네스 계약

## 상태와 권위

- Phase 7 인수 스냅샷과 승인 다이제스트는 이전 기록 그대로 보존한다.
- `phase-state.json`은 사용자의 다이제스트 결속 승인에 따른 P7→P8 전환과 현재 Phase 8 `active` 상태의 유일한 권위다.
- 이 문서는 Phase 8 구현 완료나 인수 승인을 뜻하지 않는다.

## Phase 8 증거 격리

- Phase 8의 JUnit·Playwright·시나리오·집계 증거는 `artifacts/phase-8/` 아래에만 생성한다.
- Phase 7의 `artifacts/`, `_workspace/` 검토 보고서와 인수 매니페스트를 Phase 8 재실행 출력으로 덮어쓰지 않는다.
- `p8-scenario-manifest.json`이 단위·계약·안전·통합·속성·재현·실패·E2E의 8개 필수 분모와 출력 경로를 고정한다.
- lint, typecheck, build는 증거를 생성하지 않지만 `pnpm ci`의 필수 선행 게이트다.
- `pnpm ci`는 실제 코드·안전·통합·E2E·빌드가 통과하면 성공한다. 단계 인수 시에만 별도 `pnpm test:acceptance`가 8개 필수 산출물을 현재 Git·작업 트리 다이제스트에 결속하고 `artifacts/phase-8/acceptance/P8-8.json`을 만든다.

## 안전 불변조건

- Testnet Gateway는 기본 OFF이며 `TRADING_MODE=paper`가 아니면 시작하지 않는다.
- 자동 인수에서는 실제 Testnet 네트워크와 자격증명을 사용하지 않는다.
- Mainnet private/live, 출금, Futures, Margin, leverage, short, 브라우저·AI 직접 Gateway 접근과 secret 노출은 금지한다.
- Paper Kill, Testnet barrier, activation, account generation, reconciliation 또는 구성 다이제스트가 맞지 않으면 신규 주문은 HOLD다.
- 응답 유실·timeout·5xx·`-1007`은 거절이 아니라 `SUBMISSION_UNKNOWN`이며 같은 Client Order ID 조회와 User Data 대조만 허용한다.

## 구현 게이트

1. 공식 Binance Spot Testnet 소스 잠금과 폐쇄형 계약
2. 결정론적 실행·Risk·Decimal·원장 불변조건
3. 분리 DB role과 inbox/domain/outbox·Gateway write-ahead 원자성
4. 한국어 인증 UI와 intent-only 브라우저 경계
5. `corepack pnpm ci`, 역할 분리 Safety QA, Codex 교차검토, 허용 외부 검토 또는 `external-review-unavailable`

정확한 Phase 8 인수 매니페스트 SHA-256을 사용자가 명시적으로 승인하기 전에는 Phase 8을 `accepted`로 바꾸거나 Phase 9로 전환하지 않는다.
