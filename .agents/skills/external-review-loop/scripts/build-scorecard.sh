#!/usr/bin/env bash
# loop_scorecard.json을 verdicts.json(+선택 timing.json)에서 기계적으로 계산한다.
# LLM 자기보고 제거 — 사실 필드는 스크립트가 산출, LLM은 라벨 해석만.
# 사용: build-scorecard.sh <verdicts.json> <out_scorecard.json> [timing.json]
#   verdicts.json: {"loop","stage_id","rounds","diff_lines","risk_level","termination_reason",
#                   "issues":[{"fingerprint","verdict","round","source"}...]}
#   verdict ∈ confirmed|partial|deferred|rejected|duplicate
#   regression_catch_rate = (round>1 재리뷰가 잡은 confirmed+partial) / (round==1 confirmed+partial)
#     ※ 이것은 "수정 diff에서 잡힌 회귀/누출"이지 전체 산출물 recall이 아니다(과대 해석 금지).
set -uo pipefail
V="${1:?verdicts.json 경로}"; OUT="${2:?출력 경로}"; T="${3:-}"

# graceful degradation: 측정은 부가 기능 — jq 없으면 루프를 깨지 않고 경고만 (eval-unavailable)
if ! command -v jq >/dev/null; then
  echo '{"eval_status":"eval-unavailable","reason":"jq not installed"}' > "$OUT" 2>/dev/null || true
  echo "WARN: jq 없음 → scorecard 생략(eval-unavailable). 루프는 계속." >&2
  exit 0
fi

mkdir -p "$(dirname "$OUT")"   # 깊은 출력 경로 보장(없으면 리다이렉션 실패)
tok=0
[ -n "$T" ] && [ -f "$T" ] && tok="$(jq -r '.total_tokens // 0' "$T" 2>/dev/null || echo 0)"

jq -n --slurpfile v "$V" --argjson tok "$tok" '
  ($v[0]) as $d | ($d.issues // []) as $i |
  ($i | map(select(.verdict=="confirmed")) | length) as $c |
  ($i | map(select(.verdict=="partial"))   | length) as $p |
  ($i | map(select(.verdict=="deferred"))  | length) as $df |
  ($i | map(select(.verdict=="rejected"))  | length) as $r |
  ($i | map(select(.verdict=="duplicate")) | length) as $dup |
  (($c+$p+$df+$r)) as $adj |
  (($c+$p+$r)) as $adj_nondef |
  # regression: round>1 & confirmed/partial & source=="re-review"
  ($i | map(select(.round>1 and (.verdict=="confirmed" or .verdict=="partial") and .source=="re-review")) | length) as $reg_num |
  # 분모: round==1 confirmed+partial (초기 라운드 기준 — 누적 아님)
  ($i | map(select(.round==1 and (.verdict=="confirmed" or .verdict=="partial"))) | length) as $reg_den |
  # 태깅 무결성: round>1 confirmed/partial 중 source 누락/비허용 → 경고(조용한 0 방지)
  ($i | map(select(.round>1 and (.verdict=="confirmed" or .verdict=="partial") and ((.source//"")|IN("re-review","codex","claude","agy","gemini","orchestrator")|not))) | length) as $bad_src |
  {
    schema_version:"1", loop:($d.loop//"external-review"), stage_id:($d.stage_id//"?"),
    rounds:($d.rounds // ($i|map(.round)|max // 1)),
    termination_reason:($d.termination_reason//"unknown"),
    verdict_counts:{confirmed:$c,partial:$p,deferred:$df,rejected:$r,duplicate:$dup},
    alignment_score: (if $adj_nondef>0 then (($c + 0.5*$p)/$adj_nondef) else null end),
    rejected_rate:   (if $adj>0 then ($r/$adj) else null end),
    deferred_rate:   (if $adj>0 then ($df/$adj) else null end),
    duplicate_rate:  (if $adj>0 then ($dup/$adj) else null end),
    regression_catch_rate: (if $reg_den>0 then ($reg_num/$reg_den) else null end),
    cost_per_run_tokens:$tok,
    cost_per_confirmed: (if $c>0 then ($tok/$c) else null end),
    diff_lines:($d.diff_lines//null), risk_level:($d.risk_level//null),
    warnings: ( [ if $bad_src>0 then "round>1 confirmed/partial \($bad_src)건 source 태깅 누락 — regression_catch_rate 과소측정 가능" else empty end ] ),
    computed_by:"scripts/build-scorecard.sh"
  }' > "$OUT"
echo "scorecard → $OUT"

# 집계: stage-level summary.jsonl에 원자적 append(flock — 병렬 경합 방지). 실패는 노출.
SUM="$(dirname "$OUT")/../summary.jsonl"
LINE="$(jq -c '{stage_id,rounds,termination_reason,alignment_score,regression_catch_rate,cost_per_run_tokens,warnings}' "$OUT")"
if command -v flock >/dev/null; then
  flock "$SUM.lock" -c "printf '%s\n' '$LINE' >> '$SUM'" || echo "WARN: summary append 실패" >&2
else
  printf '%s\n' "$LINE" >> "$SUM" || echo "WARN: summary append 실패(flock 없음)" >&2
fi
