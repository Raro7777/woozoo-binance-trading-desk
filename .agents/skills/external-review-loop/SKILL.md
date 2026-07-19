---
name: external-review-loop
description: "Woozoo의 중대 설계·계약·코드·문서를 현재 러너와 다른 엔진으로 대조 리뷰하고, 근거가 확인된 지적만 TDD로 수정·재리뷰·채점하는 외부 리뷰 루프. 외부 리뷰, 리뷰 게이트, 대조 검증, scorecard를 요청하면 사용한다."
---

# 외부 리뷰 루프 — Codex 런타임

## 1. 이 스킬의 경계

- 현재 러너는 `codex`다.
- 프로젝트 선택기는 `.agents/skills/external-review-loop/scripts/select-project-reviewers.sh codex`다.
- 공식 리뷰어 정책 입력은 위 프로젝트 선택기의 `EXTERNAL_REVIEWERS` 출력뿐이다. factory-managed `check-review-tools.sh`는 설치 탐지 원시 신호일 뿐이며 직접 호출하거나 그 `REVIEWERS` 출력을 정책 선택기로 사용하지 않는다.
- 이 런타임의 외부 리뷰 후보는 선택기가 반환한 `agy` 또는 `gemini`뿐이다.
- `woozoo-codex-cross-reviewer`는 fresh-context의 **동일 엔진 내부 교차검토**다. 결과는 `_workspace/internal-reviews/`에 두며, 이 스킬의 외부 verdict ledger와 scorecard에 절대 넣지 않는다.
- 내부 safety QA와 내부 Codex 교차검토는 역할 분리 증거일 뿐이다. 외부 독립 리뷰를 통과했다는 증거로 승격하지 않는다.
- Claude 리뷰어는 사용하지 않는다. 선택기의 `CLAUDE_REVIEWER: disabled` 정책을 우회하지 않는다.
- factory-managed `check-review-tools.sh`, `build-scorecard.sh`, manifest는 수정하지 않는다.

외부 리뷰 산출물은 오직 `_workspace/reviews/`에 둔다. 외부 엔진이 없거나 실행할 수 없으면 `external-review-unavailable`을 기록하고, 외부 PASS를 선언하지 않는다.

## 2. 입력

- `{artifact}`: 리뷰 대상 설계, 계약, 코드, 문서 또는 RED 테스트
- `{stage_id}`: 파일명에 안전한 단계 식별자
- `{commit_id}`: 있으면 기록하고, 없으면 생략
- `{gate_command}`: 프로젝트 통합 게이트. 없으면 문서·스키마·경계 검증으로 대체
- `{risk_level}`: `normal` 또는 `major`

리뷰 프롬프트에는 대상, 현재 Phase, 허용/금지 범위, 검증 명령을 함께 적는다. 각 지적은 다음 형식을 요구한다.

```text
1. [P0|P1|P2|P3] 제목
- 위치: 파일:라인
- 주장: 무엇이 왜 잘못되었는가
- 재현/근거: 실파일 또는 실행 결과
- 권고: 최소 수정 범위
```

## 3. 종료 가능한 라운드 루프

한 번 실행하고 끝내지 않는다. 단, 무한 반복도 금지한다.

```text
round = 1
dry_streak = 0
K = 2 if risk_level == major else 1
MAX_ROUNDS = 3

while round <= MAX_ROUNDS:
  round 1은 전체 대상을 리뷰한다.
  round > 1은 직전 라운드에서 바뀐 diff만 재리뷰한다.
  외부 출력의 실내용을 확인하고 신규 지적만 판정한다.
  확인 또는 부분 확인된 신규 지적이 없으면 dry_streak += 1
  아니면 dry_streak = 0; 확인된 범위만 TDD 수정하고 게이트를 실행한다.
  dry_streak >= K이면 converged-good로 종료한다.
  마지막 라운드에도 미수정 확인 지적이 남으면 max-rounds 또는 failed-quality-gate로 종료한다.
```

종료 사유는 `converged-good`, `exhausted`, `max-rounds`, `failed-quality-gate`, `external-review-unavailable` 중 하나로 기록한다.

## 4. 외부 리뷰어 선택과 실행

Windows PowerShell에서 실행할 때는 WSL launcher가 아닌 Git Bash(`C:\Program Files\Git\bin\bash.exe`)를 명시한다. 아래 블록 자체는 Git Bash 세션에서 실행한다.

```bash
mkdir -p _workspace/reviews
TO="$(command -v timeout || command -v gtimeout || true)"
S={stage_id}
SELECTION="$(bash .agents/skills/external-review-loop/scripts/select-project-reviewers.sh codex)"
REVIEWERS="$(printf '%s\n' "$SELECTION" | sed -n 's/^EXTERNAL_REVIEWERS: //p')"

if [ -z "$REVIEWERS" ] || [ "$REVIEWERS" = "none" ]; then
  printf '%s\n' \
    '{"status":"external-review-unavailable","runner":"codex","external_gate_satisfied":false,"note":"internal QA and same-engine Codex cross-review are not external PASS"}' \
    > "_workspace/reviews/${S}_review_status.json"
else
  pids=""
  case " $REVIEWERS " in
    *" agy "*)
      ${TO:+$TO 600s} agy -p "$(cat _workspace/reviews/${S}_prompt_external.md)" \
        --model "Gemini 3.1 Pro (High)" --sandbox --print-timeout 600s < /dev/null \
        > "_workspace/reviews/${S}_agy.md" 2>&1 &
      pids="$pids $!"
      ;;
  esac
  case " $REVIEWERS " in
    *" gemini "*)
      ${TO:+$TO 600s} gemini -p "$(cat _workspace/reviews/${S}_prompt_external.md)" < /dev/null \
        > "_workspace/reviews/${S}_gemini.md" 2>&1 &
      pids="$pids $!"
      ;;
  esac
  failed=0
  for pid in $pids; do wait "$pid" || failed=1; done
  [ "$failed" -eq 0 ] || printf '%s\n' \
    '{"status":"external-review-tool-failed","runner":"codex","external_gate_satisfied":false}' \
    > "_workspace/reviews/${S}_review_status.json"
fi
```

선택기 결과를 라운드 시작 시 한 번만 고정한다. 리뷰 도구가 실패하면 같은 엔진을 가장한 내부 검토로 대체하지 않는다. 재시도는 최대 1회이며, 이후에는 도구별 실패 원인과 미수행 범위를 기록한다.

## 5. 실파일 판정과 verdict ledger

파일이 존재한다는 이유만으로 리뷰 성공으로 보지 않는다. `_agy.md` 또는 `_gemini.md`를 직접 읽어 다음을 확인한다.

- 정상적인 리뷰 본문인지, 인증 오류·timeout·도움말·빈 출력이 아닌지
- 위치와 주장이 실제 대상 파일에 대응하는지
- 동일한 지적이 fingerprint 기준으로 중복되지 않았는지

사용 가능한 외부 리뷰 결과가 하나도 없으면 `_workspace/reviews/{stage_id}_review_status.json`에 `external-review-unavailable`을 기록한다. 이 경우 외부 verdict/scorecard의 PASS를 만들지 않는다.

외부 지적은 먼저 `_workspace/reviews/{stage_id}_verdicts.candidate.json`에 조립한다. 아래 프로젝트 소유 validator가 성공한 경우에만 이를 `_workspace/reviews/{stage_id}_verdicts.json`으로 승격한다. validator 실행 자체가 불가능하거나 검증이 실패하면 fail-closed하여 정식 ledger를 만들거나 덮어쓰지 않고 scorecard도 실행하지 않는다.

```json
{
  "loop": "external-review",
  "stage_id": "{stage_id}",
  "rounds": 1,
  "termination_reason": "converged-good",
  "issues": [
    {
      "fingerprint": "file|line|normalized-claim",
      "source": "agy",
      "round": 1,
      "verdict": "confirmed",
      "evidence": "실파일 또는 실행 근거"
    }
  ]
}
```

`source`는 이 런타임에서 `agy`, `gemini`, 재리뷰 집계 시 `re-review`만 허용한다. `_workspace/internal-reviews/` 파일과 `woozoo-codex-cross-reviewer` 지적은 ledger에 넣지 않는다.

```bash
CANDIDATE="_workspace/reviews/{stage_id}_verdicts.candidate.json"
LEDGER="_workspace/reviews/{stage_id}_verdicts.json"
VALIDATOR=".agents/skills/external-review-loop/scripts/validate-external-verdict-sources.py"

if ! python "$VALIDATOR" "$CANDIDATE"; then
  printf '%s\n' \
    '{"status":"external-review-ledger-invalid","runner":"codex","external_gate_satisfied":false}' \
    > "_workspace/reviews/{stage_id}_review_status.json"
  exit 1
fi
mv -- "$CANDIDATE" "$LEDGER"
```

validator는 모든 issue의 비어 있지 않은 `fingerprint`, `round >= 1` 정수, factory scorecard가 지원하는 verdict enum, 그리고 정확한 `source=agy|gemini|re-review`를 검사한다. 필드 누락, 알 수 없는 verdict, `codex`, `claude`, `orchestrator`, 중복 JSON 키, 잘못된 JSON을 거부한다. Python launcher 부재도 검증 실패이므로 수동 판정이나 factory scorecard 경고로 우회하지 않는다. 실패한 candidate는 조사 증거로 남기고 정식 ledger로 취급하지 않는다.

판정은 다음 네 가지다.

| 판정 | 의미 | 처리 |
|---|---|---|
| `confirmed` | 결함이 재현되거나 계약 위반이 실재 | 수정 대상 |
| `partial` | 핵심 일부만 유효 | 유효 범위만 수정 |
| `deferred` | 유효하나 현재 Phase 밖 | 백로그와 근거 기록 |
| `rejected` | 재현 불가, 범위 밖, 오판 | 근거를 남기고 수정 금지 |

판정 권한은 오케스트레이터에 있다. 지적이 10건 이상이면 보조 에이전트가 근거 수집과 판정 초안만 만들고, 오케스트레이터가 최종 확인한다. 이전 라운드 fingerprint를 `seen`으로 유지해 같은 지적을 신규로 세지 않는다.

## 6. 확인분 TDD 수정, 게이트, 재리뷰

`confirmed`와 `partial`이 0건이면 수정하지 않고 dry streak만 갱신한다. 하나 이상이면 현재 Phase 안에서 다음 순서를 지킨다.

1. 실패를 재현하는 RED 테스트 또는 결정론적 검증을 추가한다.
2. 최소 변경으로 GREEN을 만든다.
3. 구조를 정리하되 동작 계약을 바꾸지 않는다.
4. `{gate_command}`를 실행한다. 게이트 실패 상태로 다음 라운드에 들어가지 않는다.
5. 다음 라운드에는 직전 수정 diff만 외부 엔진에 재리뷰한다.

단순 문서 단계에서는 테스트 대신 링크·스키마·명령·금지 경계의 결정론적 검증을 사용한다. `deferred`와 `rejected`는 수정하지 않는다.

## 7. 결과와 scorecard

결과 문서에는 외부 엔진별 실행 성공 여부, 라운드별 신규 지적 수, 최종 판정, 반영한 테스트/수정, 게이트 결과, 종료 사유를 적는다. 내부 Codex 교차검토는 별도 섹션에서 경로만 참조하고 외부 reviewer 수나 외부 PASS에 합산하지 않는다.

사용 가능한 외부 결과가 있고 정식 ledger가 프로젝트 validator를 다시 통과할 때만 루프 종료 후 factory-managed 스크립트로 scorecard를 계산한다. `build-scorecard.sh`를 validator 없이 직접 호출하는 경로는 이 프로젝트의 공식 절차가 아니다.

기존 scorecard를 재사용하거나 게이트 증거로 인용할 때도 먼저 그 입력 ledger를 같은 validator로 검증한다. 실패하면 기존 ledger와 파생 scorecard를 감사 증거로 보존하되 비권위 상태로 취급하고, 외부 게이트 충족이나 PASS 근거로 사용하지 않는다.

```bash
if ! python .agents/skills/external-review-loop/scripts/validate-external-verdict-sources.py \
  "_workspace/reviews/{stage_id}_verdicts.json"; then
  exit 1
fi
bash .agents/skills/external-review-loop/scripts/build-scorecard.sh \
  "_workspace/reviews/{stage_id}_verdicts.json" \
  "_workspace/evals/external-review/{stage_id}/{run_id}/scorecard.json" \
  "_workspace/reviews/{stage_id}_timing.json"
```

`verdicts.json`의 각 지적에는 validator가 강제하는 `fingerprint`, `verdict`, `round`, `source`가 있어야 한다. scorecard는 측정 로그이며 판단 권한이 아니다. `jq` 부재로 `eval-unavailable`이 생성되어도 리뷰 루프 자체의 실증 결과는 유지하되, 외부 리뷰가 없었던 것을 PASS로 바꾸지 않는다.

## 8. 최소 검증 시나리오

- `REVIEWERS: none`: `external-review-unavailable`, `external_gate_satisfied=false`, 외부 PASS 없음
- agy 또는 gemini 오류/빈 출력: 실파일 판정에서 실패로 분류, 지적 0건으로 오인 금지
- round 1에서 확인분 발생: TDD 수정과 게이트 후 수정 diff만 round 2 재리뷰
- round 2 신규 확인분 0건: 일반 변경은 종료, 중대 변경은 한 번 더 dry 라운드 요구
- round 3에도 확인 지적 잔존: 강제 종료하고 미수정 위험을 명시
- 내부 Codex 보고서만 존재: 외부 verdict와 scorecard 입력에서 제외
- `source=codex|claude|orchestrator` 또는 source 누락: validator 비정상 종료, 정식 ledger·scorecard 생성 금지
- `fingerprint|verdict|round` 누락 또는 알 수 없는 verdict: validator 비정상 종료, 정식 ledger·scorecard 생성 금지
