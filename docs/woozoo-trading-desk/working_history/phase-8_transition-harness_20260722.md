# Phase 8 작업 결과 — 승인 전환과 증거 격리

## 1. 작업 요약

- 사용자가 승인한 정확한 P7 acceptance digest를 별도 approval envelope와 단조 P7→P8 상태 이력에 결속했다.
- 승인 당시 ignored evidence 65개를 acceptance snapshot commit에 추가해 Git에서 byte-for-byte 복구 가능하게 했다.
- Phase 8 안전 재실행이 frozen P7 evidence를 덮어쓰지 않도록 생성 경로를 `artifacts/phase-8/`로 분리했다.
- Phase 8 Risk/Kill regression manifest를 만들고 Kill recovery scanner의 적용 범위만 7~9로 확장했다.

## 2. 변경 경계

- 제품 Gateway, schema, dependency, credential, private API와 외부 Testnet 호출은 추가하지 않았다.
- Kill recovery token scan, 허용 writer 목록, 사람 인증·reconciliation 요구는 약화하지 않았다.
- Phase 8 전용 manifest가 아직 없는 evidence target은 실행 전에 fail-closed한다.

## 3. 검증

- P7 manifest SHA-256: `a7d13e1d04f80bbcee94bcdad997357aa191e6510739a7e3415edd5d25ad194f`
- P7 acceptance validator: `failures=[]`, artifact 65/65, leaf 43/43
- `corepack pnpm test:safety`: PASS
- Phase 8 safety artifact root: `artifacts/phase-8/`
- Phase 8 Risk/Kill manifest SHA-256: `4c00f037d6b02b40cd6cffd7f0f5c35d92e7c2c71008098493307fd72cca88cf`
- 같은 엔진 Codex 교차검토는 외부 독립 리뷰가 아니며 외부 gate를 충족하지 않는다.

## 7. 다음 단계 참조

1. Phase 8 공식 Binance Spot Testnet 문서 전제와 URL·method·auth allowlist를 현재 정보로 재검증한다.
2. architect가 versioned execution command, approval binding, timeout unknown, User Data, reconciliation과 secret boundary 계약을 고정한다.
3. gateway·risk-ledger·web builder가 RED 계약과 negative E2E를 분리 소유로 구현한다.
4. 기본 OFF와 별도 사람 승인·Kill·reconciliation이 모두 PASS하기 전에는 외부 Testnet capability를 활성화하지 않는다.
