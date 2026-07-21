# Phase 8 전환 하네스 계약

## 상태와 권위

- Phase 7 승인 snapshot commit은 `cecbe3c7fe43dda466e645ec3c94edf0549fb380`이다.
- 승인된 Phase 7 manifest SHA-256은 `a7d13e1d04f80bbcee94bcdad997357aa191e6510739a7e3415edd5d25ad194f`이며, 결속된 65개 파일은 해당 commit에 byte-for-byte 보존한다.
- `phase-state.json`은 사용자 승인에 결속된 단조 P7→P8 전환을 기록하고 현재 Phase 8 `active`다.
- 이 계약은 Testnet Gateway 구현 완료나 Phase 8 acceptance가 아니다.

## Phase 8 증거 격리

- Phase 8에서 생성하는 JUnit과 scenario evidence는 `artifacts/phase-8/` 아래에만 기록한다.
- Phase 7의 `artifacts/`, `_workspace/` 검토 보고서와 acceptance manifest는 재실행 출력으로 덮어쓰지 않는다.
- 현재 전환 하네스가 개방한 evidence target은 `corepack pnpm test:safety` 하나다.
- 다른 `test:*`와 `ci` target은 Phase 8 전용 scenario manifest와 artifact namespace가 정의될 때까지 시작 전에 fail-closed한다. lint, typecheck와 build는 evidence를 생성하지 않으므로 이 임시 차단 대상이 아니다.

## 유지되는 안전 불변조건

- Kill recovery writer token scan과 허용된 수동 recovery writer 정확한 목록은 Phase 7과 동일하다.
- scanner의 적용 Phase만 승인된 로드맵 범위 `7..9`로 확장한다.
- Phase 8 Risk/Kill regression은 `p8-risk-regression-manifest.json`의 source SHA-256과 `artifacts/phase-8/...` 경로에 결속한다.
- Mainnet private/live, 출금, Futures, 마진, 레버리지, 숏, 브라우저·AI 직접 Gateway 접근과 secret 노출은 계속 금지다.
- Gateway, credential, private exchange dependency/configuration/schema와 외부 Testnet 호출은 이 전환 하네스 변경에 포함하지 않는다.

## 검증

- `corepack pnpm test:safety`: PASS
- 실행 분모: bulk safety Pytest 38 + capability Node 1, EVID-007 2, PTI-003 15, PAPER-SAFE-001 2, RISK-SAFE-001 3, KILL-002 7, AI-002 1, SEC-001 1, SEC-002 2
- Phase 8 Risk/Kill manifest SHA-256: `4c00f037d6b02b40cd6cffd7f0f5c35d92e7c2c71008098493307fd72cca88cf`
- Phase 8 KILL-002 source SHA-256: `830301a5ea374c1b8cfa2d97da60e5862dde8faff4e79ee4e58c597848b92c32`
- Phase 7 acceptance package validator: `failures=[]`, 65/65 artifacts, 43/43 leaves

## 다음 구현 게이트

Phase 8 architect와 gateway/risk/web builder가 공식 Binance Spot Testnet 전제를 다시 검증한 뒤 versioned command·event·DB·secret·egress 계약과 RED 테스트를 먼저 정의한다. 해당 계약과 namespace가 준비된 target만 순차적으로 개방하며, 외부 Testnet 주문은 별도 사람 승인·기본 OFF·reconciliation E2E 전에는 연결하지 않는다.
