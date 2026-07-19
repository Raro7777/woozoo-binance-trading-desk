---
name: woozoo-phase-gates
description: "Woozoo의 모든 설계·구현·테스트·수정 요청에서 현재 개발 Phase와 허용 변경 범위를 판정하고 다음 Phase 선행 구현을 차단한다. Phase 0 문서 전용, Phase 8 전 Testnet Gateway 금지, 단계별 브랜치·검증·사용자 승인 규칙을 적용하므로 Woozoo 저장소를 변경할 때 반드시 먼저 사용한다."
---

# Woozoo Phase Gates

`docs/woozoo-trading-desk/phase-state.json`을 단계 상태의 유일한 권위로 사용한다. 대화나 코드에 더 높은 Phase가 언급되어도 상태 파일과 명시적 사용자 승인이 없으면 진행하지 않는다.

## 범위 판정

1. 현재 Phase와 `allowed_change_scope`, `forbidden_change_scope`를 읽는다.
2. 사용자 요청의 각 산출물을 현재 Phase의 허용 항목에 매핑한다.
3. 하나라도 범위 밖이면 그 항목만 차단하고 안전하게 분리 가능한 현재 Phase 작업만 수행한다.
4. 다음 Phase 전제는 문서의 미해결 질문이나 backlog로 남기되 코드를 미리 만들지 않는다.

## 단계 전환 조건

다음 항목이 모두 있어야 Phase를 올릴 수 있다.

- 현재 Phase acceptance criteria 통과 증거
- 관련 테스트·문서·내부 safety QA 통과
- 중대 변경이면 외부 독립 리뷰 완료 또는 리뷰어 부재 기록
- 미해결 안전 차단 항목 0건
- acceptance evidence digest에 결속된 사용자의 명시적 단계 전환 승인

Codex 러너에서 동일 엔진인 `woozoo-codex-cross-reviewer` 결과는 내부 보조 증거다. 컨텍스트가 분리돼도 외부 독립 리뷰를 충족하지 않으며, 외부 엔진이 없으면 반드시 리뷰어 부재로 기록한다.

에이전트는 `phase-state.json`을 스스로 올리지 않는다. 사용자 승인 후 오케스트레이터만 이전 값, 새 값, acceptance evidence digest와 승인 정보를 transition history에 append하며 한 단계씩 갱신한다. Phase를 건너뛰거나 이력을 덮어쓰지 않는다.

## 현재 Phase 0

- 허용: 저장소 조사, 요구사항 분석, 구조·신뢰·비밀 경계, ERD·보존, API/event 계약, 불변조건, 테스트·CI 설계, 하네스 구성.
- 금지: 애플리케이션 골격과 핵심 코드, 거래 로직, Paper Broker, API 키 요청, Binance 주문, Phase 1 구현.
- 브랜치: `main`이 아닌 `codex/phase-0-design`.
- 종료: 설계 문서·검증·미결정 질문·정확한 Phase 1 범위를 보고하고 사용자 승인을 기다린다.

전체 로드맵은 `references/phase-contract.md`를 필요한 Phase만 읽는다.

## 테스트 시나리오

- 정상: Phase 0에서 ERD와 이벤트 계약 문서를 작성한다.
- 차단: Phase 0에서 FastAPI health endpoint를 구현해 달라는 요청은 Phase 1 backlog로 돌린다.
- 강제 차단: 어떤 Phase에서도 Mainnet private 주문이나 출금은 safety-boundaries에 따라 금지한다.
