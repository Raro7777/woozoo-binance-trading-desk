#!/usr/bin/env bash
# myharness v1의 4개 관리파일을 분리 배치한 Woozoo 하네스용 안전 갱신 어댑터.
# 사용: update-factory-managed.sh <plan|apply|manifest> <.agents|.claude> <myharness_factory_dir> [--approve rel1,rel2]
set -euo pipefail

CMD="${1:-}"
RUNTIME_ROOT="${2:-}"
FACTORY_ROOT="${3:-}"

case "$CMD" in
  plan|apply|manifest) ;;
  *) echo "사용: $0 <plan|apply|manifest> <.agents|.claude> <myharness_factory_dir> [--approve rel1,rel2]" >&2; exit 2 ;;
esac

[ -d "$RUNTIME_ROOT/skills/woozoo-orchestrator" ] || {
  echo "오류: orchestrator skill 없음 — $RUNTIME_ROOT" >&2; exit 2; }
[ -d "$RUNTIME_ROOT/skills/external-review-loop" ] || {
  echo "오류: external-review-loop skill 없음 — $RUNTIME_ROOT" >&2; exit 2; }
UPDATE="$FACTORY_ROOT/scripts/harness-update.sh"
[ -f "$UPDATE" ] || { echo "오류: 중앙 갱신기 없음 — $UPDATE" >&2; exit 2; }

for rel in references/dev-rules.md references/tdd-doctrine.md scripts/check-review-tools.sh scripts/build-scorecard.sh; do
  [ -f "$FACTORY_ROOT/$rel" ] || { echo "오류: 팩토리 관리파일 없음 — $rel" >&2; exit 2; }
done

TMP_ROOT="$(mktemp -d)"
TMP_REAL="$(cd "$TMP_ROOT" && pwd -P)"
case "$TMP_REAL" in
  /tmp/*|/var/tmp/*) ;;
  *) echo "오류: 검증되지 않은 임시 경로 — $TMP_REAL" >&2; exit 2 ;;
esac
cleanup() {
  [ -n "$TMP_REAL" ] && [ "$TMP_REAL" != "/" ] && rm -rf -- "$TMP_REAL"
}
trap cleanup EXIT

REF_FACTORY="$TMP_REAL/reference-factory"
SCRIPT_FACTORY="$TMP_REAL/script-factory"
mkdir -p "$REF_FACTORY/references" "$SCRIPT_FACTORY/scripts"
cp "$FACTORY_ROOT/references/dev-rules.md" "$REF_FACTORY/references/dev-rules.md"
cp "$FACTORY_ROOT/references/tdd-doctrine.md" "$REF_FACTORY/references/tdd-doctrine.md"
cp "$FACTORY_ROOT/scripts/check-review-tools.sh" "$SCRIPT_FACTORY/scripts/check-review-tools.sh"
cp "$FACTORY_ROOT/scripts/build-scorecard.sh" "$SCRIPT_FACTORY/scripts/build-scorecard.sh"

EXTRA=("${@:4}")
bash "$UPDATE" "$CMD" "$RUNTIME_ROOT/skills/woozoo-orchestrator" "$REF_FACTORY" "${EXTRA[@]}"
bash "$UPDATE" "$CMD" "$RUNTIME_ROOT/skills/external-review-loop" "$SCRIPT_FACTORY" "${EXTRA[@]}"

if [ "$CMD" = "plan" ]; then
  echo "note: 적용은 원본 plan 출력의 임시 경로가 아니라 이 어댑터에 apply를 지정해 실행한다."
fi
