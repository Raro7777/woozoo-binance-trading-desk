# 루프 자체 평가 (Loop Self-Evaluation) — scorecard & 단계적 도입

루프(external-review-loop 등)가 자기 실행을 측정해 흐름 개선으로 환류하는 닫힌 고리. **외부 리뷰(codex/agy) 검증을 거쳐 교정된 정본** — 순진한 precision·자동 적용·grading.json 재사용을 모두 제거했다.

## 핵심 경계 (먼저 읽을 것)
- **자기채점 ≠ 품질.** 오케스트레이터가 자기 판정으로 산출한 지표는 "정밀도"가 아니라 *자기와의 정합도*다. 그래서 precision이 아니라 **`alignment_score`**로 부른다. 리뷰어가 아무것도 안 내도 alignment는 좋아 보인다 — **놓친 결함(recall/miss)은 Ground Truth로만** 측정한다(아래).
- **측정과 자동화를 분리한다.** 측정은 안전, 자동 흐름 변경은 고위험(Goodhart·플래핑). **단계적 도입**으로 측정부터.

## 단계적 도입 (한 번에 다 넣지 말 것)
| 단계 | 내용 | 졸업 기준(다음 단계로) | 자동화 |
|------|------|----------------------|--------|
| **1 (현재 정본)** | `loop_scorecard.json` 로깅만. 측정·기록 | 로깅 **≥ 10회** | 없음 |
| 2 (사람) | 누적 요약을 사람이 수동 검토·판단 | rolling N≥10 + `min_adjudicated_claims≥30` + 사람 sign-off | 없음 |
| 3 (실험) | 수치 트리거가 **개선안 "제안"**만 emit | 제안 holdout 통과율 ≥ θ + 명시 승인 | 제안만 + 승인 게이트 |
| 4 (실험) | holdout 검증 후 자동 흐름 개선 | — | 최후, 승인 필수 |

> **수치(10/30/θ)는 "관찰 시작 최소치"이지 통계적 확정 임계가 아니다** — LLM 평가 노이즈상 비율 지표는 표본이 더 필요할 수 있다. 리스크/단계/리뷰어가 섞이면 신뢰구간을 함께 보고, θ는 리스크 등급별 기본값으로 둔다. 3·4단계는 롤링윈도우·3회 연속 하락 시에만, 단일 실행 노이즈로 흐름을 바꾸지 않는다(플래핑 방지). **2단계까지가 실용 권장 — 3·4(자동 환류)는 실험적**, 데이터 충분+holdout 후에만.

## 읽기 경로 (1단계에도 소비자 필수 — write-only 방지)
측정만 하고 안 읽으면 낭비. 1단계부터 **읽기 경로**를 둔다:
- `scripts/build-scorecard.sh`가 매 루프 종료 시 scorecard 발행 + `_workspace/evals/{loop}/summary.jsonl`에 최근 N회 집계(append).
- 오케스트레이터는 **Phase 0(현황 감사)·Phase 7(진화) 진입 시 `summary.jsonl` 1줄 요약만** 읽는다(원본 JSON 미로드 — Lean). 악화 추세가 보이면 사람에게 보고(2단계 수동 검토).

## loop_scorecard.json 스키마 (신규 — grading.json 재사용 아님)
실행 단위 디렉터리에 발행: `_workspace/evals/{loop}/{stage_id}/{run_id}/scorecard.json`.
```json
{
  "schema_version": "1",
  "loop": "external-review",
  "stage_id": "design-auth",
  "run_id": "20260612_1530",
  "rounds": 3,
  "termination_reason": "converged-good | exhausted | max-rounds | failed-quality-gate",
  "verdict_counts": { "confirmed": 6, "partial": 2, "deferred": 1, "rejected": 1, "duplicate": 1 },
  "new_per_round": [10, 1, 0],
  "alignment_score": 0.67,        // (confirmed + 0.5*partial) / adjudicated_non_deferred. deferred 분모 제외
  "rejected_rate": 0.11,          // rejected / adjudicated_new_claims (1-alignment 아님, 별도)
  "deferred_rate": 0.10,
  "duplicate_rate": 0.09,
  "rounds_normalized": 0.6,       // rounds / f(diff_lines, risk_level) — 난이도 보정
  "diff_lines": 120, "risk_level": "standard",
  "cost_per_run_tokens": 48000,
  "cost_per_confirmed": 8000,     // confirmed>0일 때만. 0이면 null
  "quality_label": "gate_pass | failed-quality-gate | converged | n/a",  // 설계단계 품질 자기단정 금지
  "regression_catch_rate": 0.33,  // round>1 재리뷰가 잡은(confirmed+partial) / round1 confirmed+partial. "수정 diff의 회귀/누출"이지 전체 recall 아님
  "warnings": [],                 // round>1 source 태깅 누락 등 — 조용한 0 방지
  "missed_defect_rate": null,     // 진짜 recall: 외부 Ground Truth(seeded·사후 회귀·사용자 반박) 있을 때만
  "overturned_rejection_rate": null,
  "computed_by": "scripts/build-scorecard.sh",  // 사실 필드는 스크립트 계산(LLM 자기보고 아님). quality_label만 LLM 해석
  "links": { "grading": "../grading.json", "timing": "../timing.json", "verdicts": "../../{stage_id}_verdicts.json" }
}
```
- **계산 도출(메타 자기채점 제거):** `verdict_counts`·`rounds`·`new_per_round`·`*_rate`·`cost`·`regression_catch_rate`는 **`scripts/build-scorecard.sh`가 `verdicts.json`+`timing.json`에서 기계적으로 산출**한다. LLM은 라벨 해석에만 관여(`quality_label` 등). 카운트를 LLM이 손으로 적지 않는다(오기·낙관 편향 방지).
- **Lean:** 원본 JSON을 세션에 상시 로드하지 않는다. 파일로만 보존, **Phase 시작 시 요약본만** 읽는다.
- `grading.json`/`timing.json`은 assertion·토큰 정보가 있을 때 **링크**로 연결(중복 보관 금지).

## 메트릭 정의 (교정본)
- **alignment_score** = (confirmed + 0.5·partial) / (adjudicated 중 deferred 제외). 이름 그대로 "리뷰 보고 ↔ 오케스트레이터 판정" 정합도. **리뷰어 건강·정밀도라고 부르지 않는다.**
- **rejected_rate / deferred_rate / duplicate_rate** — 각각 별도. `false_positive_rate`는 *사후 확정 가능*할 때만(기각이 나중에 진짜 결함으로 판명) `overturned_rejection_rate`로 기록.
- **rounds_to_converge** 원시값은 K·MAX_ROUNDS·변경 규모에 좌우 → `diff_lines`·`risk_level`로 정규화한 `rounds_normalized`를 1차 지표로, 원시값은 보조.
- **cost_per_confirmed** confirmed=0이면 분모 0 → `null`. 항상 `cost_per_run`·`cost_per_adjudicated_claim`과 함께 본다.
- **regression_catch_rate (수정 회귀/누출 탐지 — 부분 신호)** = (round>1 재리뷰가 잡은 confirmed+partial) / (**round1** confirmed+partial). round>1은 *수정 diff만* 좁게 재리뷰하므로 이것은 "수정이 만든 회귀/이전 게이트 누출" 탐지율이지 **전체 산출물 recall이 아니다**(미수정 영역 누락은 관측 불가 → 과대 해석 금지). (예: timeout 수정이 2차에서 macOS 결함으로 잡힘.) 분모는 누적이 아닌 round1 기준(희석 방지). round>1 confirmed/partial에 `source` 태깅이 없으면 `warnings`에 기록(조용한 0 방지).
- **missed_defect_rate (진짜 recall)** — 전체 누락은 **외부 Ground Truth**(seeded 결함 탐지율·사후 회귀 역추적·사용자 반박)가 있을 때만. 없으면 null. regression_catch_rate는 보조 신호일 뿐 recall을 대체하지 않는다.

## 종료 사유 라벨 (P2 — 종료조건 아님, 라벨)
gate/assertion은 **코드/테스트 단계 전용**. 설계·문서 리뷰엔 측정값이 없으므로 종료조건에 넣지 않는다.
- `converged-good`: 신규 확인 0건 K회 + (코드 단계) 게이트 PASS·assertion ≥ θ.
- `converged`: 신규 0건 K회 (게이트 없는 단계의 중립 종료 — "더 찾을 신규 결함 없음". 품질 단정 아님). *주의: `exhausted`를 부정 라벨로 쓰지 말 것 — 게이트 없는 설계/문서는 이게 정상 수렴이다.*
- `max-rounds`: MAX_ROUNDS 강제 종료(미수렴 보고).
- `failed-quality-gate`: (코드 단계) 품질 θ 미달 명백 → **루프 중단**(MAX_ROUNDS 헛돌지 않게).
- **설계/문서 단계 품질은 라벨로 자기단정하지 않는다.** verdicts 완료 + 정본 대조 체크리스트는 종료 *조건*일 뿐, "양호" 단정(`design-ok` 같은)은 같은 오케스트레이터의 자기채점이 된다 → 금지. 품질 보증이 필요하면 독립 리뷰어 표본 감사·사용자 승인 같은 외부 신호를 별도로 받는다.

## 판정 보정 (P5 — Ground Truth만)
같은 오케스트레이터·같은 근거수집으로 재점검하면 편향 반복(에코체임버). 보정은 **독립 신호가 있을 때만** 발화: 사용자 반박 / 후속 결함 발견 / 독립 리뷰어 표본 감사. 결과는 `overturned_rejection_rate`로 기록하고, 임계 초과 시 기각 사유표·리뷰어 신뢰도를 *제안* 형태로 조정(자동 적용 금지).

## 환류(P3/P4) 안전장치 — 3·4단계에서만
- 자동 **"적용" 금지 → "제안"**만. 적용 전 사용자 또는 독립 검토 게이트.
- 롤링윈도우(최근 N회 평균)·3회 연속 하락만 발화(단일 노이즈 무시).
- `min_adjudicated_claims ≥ 30` 전에는 트리거 금지(표본 부족).
- 변경 후 holdout 시나리오·기존 회귀 케이스로 검증.
- θ·ε·N은 리스크 등급별 기본값 + 관찰 전용 시작(고정 자동화 금지).

> 테스트 개선 루프 수렴(assertion 통과율 델타 < ε)은 목적이 달라 분리한다 — `skill-testing-guide.md`에서 다루고, scorecard 링크 규약만 공유.
