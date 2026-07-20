# {단계ID} 작업결과서: {단계 제목}

## 1. 작업 요약

- 목표, 범위와 완료 항목을 기록한다.

## 2. 변경 파일

- 경로와 변경 사유를 기록한다.

## 3. 검증 결과

- 테스트, 회귀, 린트와 안전 게이트 결과를 기록한다.
- 코드 단계에서는 RED→GREEN 매트릭스를 기록한다.
- 내부 safety QA의 PASS/FAIL/UNVERIFIED와 Codex 교차검토의 FINDINGS/NO_CONFIRMED_FINDINGS/UNVERIFIED를 별도 항목으로 기록한다.
- Codex 교차검토에는 `review_class=internal_same_engine_cross_review`, `engine_family=codex`, `independence_class=same_engine_separate_context`, `external_gate_satisfied=false`를 남기고 외부 독립 검토로 계산하지 않는다.

## 4. 미해결 / 후속

- 알려진 한계와 백로그를 기록한다. 없으면 `없음`으로 쓴다.

## 5. 외부 엔진 리뷰 반영

- 현재 러너와 다른 엔진의 외부 리뷰가 실행된 경우에만 판정 요약, 게이트 수치와 실제 리뷰어를 기록한다.
- 외부 리뷰어가 없거나 실행에 실패하면 이 섹션에 `external-review-unavailable`과 사유를 기록한다. 내부 safety QA나 같은 엔진 Codex 교차검토를 외부 PASS로 승격하지 않는다.

## 6. 실패 컨텍스트

- 치명 실패나 디버그 근거가 있을 때만 결정적 로그를 `<details>`에 기록한다.

## 7. 다음 단계 참조

- 미해결 이슈, 핵심 결정과 이유, 다음 단계 착수 조건과 주의를 기록한다.
