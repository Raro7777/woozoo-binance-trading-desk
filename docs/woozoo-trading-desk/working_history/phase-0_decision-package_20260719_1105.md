# Phase 0 작업결과서: MVP 결정 패키지 보완

## 1. 작업 요약

- 사용자의 MVP 구현 목표를 현재 Phase 0의 문서·하네스 범위 안에서 진전시키기 위해 승인용 결정 패키지를 만들었다.
- 최초 패키지의 누락을 점검해 candle/feature, AI timeout·비용, API/event deprecation, 로컬·CI·browser/a11y, 고정 scenario 분모를 추가했다.
- 이 문서는 정책 **제안**이며 사용자 승인, 동결, review, evidence digest 또는 Phase 전환을 주장하지 않는다.

## 2. 변경 파일

- `docs/woozoo-trading-desk/phase-0-decision-package.md`
  - D-01~D-10: MVP 컷, 데이터, AI, 금융/Paper, Risk/승인, 보존, contract, 플랫폼/검증의 권고값
  - D-11: 별도 Git 권한 경계
  - 승인·수정에 사용할 짧은 응답 문구와 이후 고정 게이트

## 3. 검증 결과

- `python docs/woozoo-trading-desk/phase-0/tools/validate_requirements_trace.py`
  - `TRACE_VALIDATION=PASS requirements=123 aliases=8 targets=77`
  - `ACCEPTANCE_STATUS=UNVERIFIED` (의도된 상태)
- 원본 `Woozoo_Binance_Trading_Desk_Codex_Handoff-5.docx` SHA-256은 계속 `2ded3774ee98f4f808853c03a072fbbdefb3135473711463da9939daa188e8a7`이다.
- `apps`, `services`, `packages`, `tests`, `package.json`, `compose.yaml`, `pnpm-lock.yaml`은 모두 부재해 Phase 0 제품 코드 delta가 0이다.
- D-01~D-11의 모든 결정 ID가 패키지에 존재한다.

## 4. 안전·단계 판정

- `phase-state.json`은 계속 Phase 0이며 `application_implementation`, `trading_logic_implementation`, `paper_broker_implementation`, 주문 실행과 credential 저장은 금지다.
- 패키지는 Binance Spot 공개·무인증 데이터만 권고하며 Mainnet private/live, 출금, Futures, Margin, leverage, short, Testnet capability와 AI 주문/계좌 도구를 허용하지 않는다.
- 금융 권위는 deterministic Decimal, Risk, Paper authorization, immutable ledger에만 두며 AI/UI는 권위가 아니다.

## 5. 미해결 / No-Go

- D-01~D-10의 사용자 승인 또는 수정이 아직 없다.
- Phase 0 package 동결 뒤 수행할 safety QA, Codex same-engine 교차검토, 외부 엔진 review, P0-01~12 final verdict/SHA manifest, acceptance digest가 없다.
- Git stage/commit/push/Draft PR은 D-11의 별도 사용자 권한 없이는 수행하지 않는다.
- 따라서 Phase 1~7 코드, migration, fixture, 테스트 실행 evidence는 여전히 `NO-GO`다.

## 6. 다음 단계 참조

1. 사용자는 `권고안 D-01~D-10 승인. Git 작업은 보류.` 또는 수정할 D-ID/값을 응답한다.
2. 승인된 값만 원래 P0 설계 문서에 반영하고 package를 동결한다.
3. 동결본에 대해 분리 safety QA, Codex 내부 교차검토, 외부 엔진 review를 수행한다.
4. P0-01~12의 path/SHA/verdict manifest와 acceptance evidence digest를 생성한 뒤 digest-bound Phase 1 전환 승인을 요청한다.
5. Phase 1 전환 승인 전에는 제품 코드를 만들지 않는다.
