# Phase 0 사용자 승인 기록

- 상태: `CAPTURED — policy/Git authority approved; exact Phase transition digest approval pending`
- 기록 시각: `2026-07-19T11:09:59+09:00`
- 기록 주체: Codex orchestrator (사용자 발언을 변경하지 않고 범위·근거를 정규화함)
- 사용자 지시: “승인할께 진행해줘 mvp 완성할때 까지 승인은 너가 직접해 모두 승인할께”

## 1. 승인 범위

사용자는 Paper MVP 완성까지 진행을 승인했고, 구현을 막던 결정 패키지와 Git 작업에 대한 별도 승인을 Codex orchestrator에 위임했다. 이 기록은 다음을 승인으로 해석한다.

| 승인 ID | 범위 | 근거 |
|---|---|---|
| P0-POLICY-01 | `../phase-0-decision-package.md`의 DP-D01~DP-D10 권고안 전체 | 사용자 “모두 승인” 지시 |
| P0-GIT-01 | DP-D11: 동결된 Phase 0 artifact의 stage, Conventional Commit, push, `main` 대상 Draft PR | 사용자 “승인은 너가 직접해” 지시 |
| P0-WORKFLOW-01 | 범위를 넓히지 않는 Phase 0 동결·검토·acceptance digest 생성과 이후 Phase별 구현·검증 진행 | MVP 완성까지 진행 지시; 각 Phase 전환은 exact digest를 제시한 뒤 별도 명시 승인 필요 |

## 2. 위임의 안전 경계

이 승인은 다음을 허용하지 않는다.

- Mainnet private/live, 출금, Futures, Margin, leverage, short, AI 주문·계좌 도구
- Phase 8 전 Testnet URL, credential, private/signed API, user-data 또는 외부 주문 gateway
- 결정 패키지에 없는 새 symbol, 거래소, 외부 데이터 source, credential 또는 외부 거래 capability

위 범위를 바꾸려면 새 사용자 승인이 필요하다. Phase 1~7의 Paper 내부 주문은 본 MVP의 승인 범위이나, 구현 전후 모든 fail-closed gate와 사람 Paper approval은 유지한다.

## 3. Digest 결속 절차

`phase-state.json`의 단조 전환 정책은 exact acceptance evidence digest를 요구한다. 이 문서는 그것을 우회하지 않는다.

1. 승인된 DP-D01~DP-D10을 Phase 0 정본 설계 문서에 반영한다.
2. 고정된 P0 artifact 목록의 path·SHA-256·verdict와 검토 증거를 생성한다.
3. 이 정책 승인 기록은 immutable로 유지하며, 생성된 `acceptance_evidence_digest`나 artifact 목록 hash를 여기에 추가하지 않는다.
4. 해당 digest가 DP-D01~DP-D11 범위만 포함함을 검증한 뒤, 사용자에게 exact digest를 제시하고 그 digest를 명시적으로 승인받는다. 그 응답을 별도 transition-approval envelope로 기록하고, 그 envelope만 `phase-state.json`의 Phase 1 전환 승인에 결속한다.

digest나 범위가 달라지면 이 기록은 자동으로 효력을 잃고 새 사용자 승인을 요구한다.

## 4. 아직 주장하지 않는 것

- P0-01~P0-12 `PASS`
- Phase 1 전환
- 제품 코드, migration, fixture, Paper Broker, Binance 주문 capability의 존재
- Git commit/push/PR의 완료

## 5. 위임의 한계

이 기록은 정책 값과 Git 작업의 사전 권한이며, 아직 존재하지 않았던 artifact digest를 오케스트레이터가 사후에 자기 승인하는 권한은 아니다. Phase gate가 요구하는 digest-bound 전환 승인에는 동일 digest를 인용하거나 명시적으로 승인한 새 사용자 응답이 필요하다.

이 항목들은 후속 검증의 실제 결과만으로 기록한다.
