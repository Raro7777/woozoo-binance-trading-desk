# Woozoo Phase 0 설계 원장

- 현재 상태: `ACTIVE / NO-GO`
- 제품 코드: 0
- Phase 전환 승인: 요청하지 않음
- 정본 상태: `docs/woozoo-trading-desk/phase-state.json`
- Acceptance 계약: `.agents/skills/woozoo-phase-gates/references/phase-contract.md#phase-0`

이 디렉터리는 P0-01~P0-12의 영속 설계 증거를 보관한다. 계획, 템플릿, 리뷰가 존재한다는 이유만으로 항목을 PASS 처리하지 않는다. 각 항목은 동결된 파일의 경로·SHA-256·verdict를 최종 evidence manifest에 가져야 한다.

## 산출물 지도

| Acceptance | 영속 산출물 | 현재 상태 |
|---|---|---|
| P0-01 | `requirements-and-repository-audit.md`, `requirements-trace-manifest.json` | 원자 요구 123건·alias 8개 추적 완료; package review 대기, 최종 verdict는 UNVERIFIED |
| P0-02 | `binance-official-assumptions.md` | PASS 후보, package review 대기 |
| P0-03 | `requirements-and-repository-audit.md`의 decision register | DP-D01~D10 policy approved; final package review/evidence pending |
| P0-04 | `architecture-and-boundaries.md` | policy approved design, package review/evidence pending |
| P0-05 | `architecture-and-boundaries.md` | policy approved design, package review/evidence pending |
| P0-06 | `data-model-and-retention.md` | 설계 후보, 보존 정책·package review 대기 |
| P0-07 | `api-and-event-contracts.md` | 설계 후보, 계약 정책·package review 대기 |
| P0-08 | `financial-and-order-invariants.md` | 설계 후보, 금융 숫자·package review 대기 |
| P0-09 | `verification-strategy.md` | PASS 후보, package review 대기 |
| P0-10 | `architecture-and-boundaries.md`, `verification-strategy.md` | 설계 후보, capability-zero package review 대기 |
| P0-11 | `local-ci-contract.md` | PASS 후보, package review 대기 |
| P0-12 | `phase-1-scope-and-phase-0-report.md`, 루트 운영 규칙 | policy/Git authority approved; package review/evidence pending |

## 증거 규칙

1. 문서 안의 자체 `PASS 후보`는 최종 verdict가 아니다.
2. 최종 판정은 safety QA, Codex same-engine cross-review, 외부 엔진 review gate를 분리 기록한다.
3. 외부 리뷰가 없거나 실패하면 `external-review-unavailable`이지 외부 PASS가 아니다.
4. 정렬된 `path + SHA-256 + verdict` manifest의 SHA-256이 acceptance evidence digest다.
5. digest 생성 뒤 파일이 바뀌면 digest와 사용자 승인은 무효다.
6. 승인된 Conventional Commit과 `main` 대상 Draft PR 증거가 없으면 Phase 0은 닫히지 않는다.

## 다음 단계 참조

- P0-01~P0-12 설계 후보가 기록된 정책 승인과 서로 정합한지 닫는다.
- 승인된 MVP/Testnet split, derivatives/news 이월, 세부 정책 묶음이 frozen package에 정확히 반영됐는지 검증한다.
- 전체 package를 동결해 안전 QA·내부 Codex 교차검토·외부 리뷰를 다시 실행한다.
- 모든 verdict와 Git gate가 PASS인 경우에만 evidence digest에 결속된 Phase 1 승인을 요청한다.
