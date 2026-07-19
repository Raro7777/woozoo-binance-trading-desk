#!/usr/bin/env bash
# Woozoo 프로젝트 리뷰어 정책: Codex 우선, Claude 리뷰어 비활성.
# 사용: select-project-reviewers.sh <codex|claude>
set -uo pipefail

RUNNER="${1:-}"
case "$RUNNER" in
  codex|claude) ;;
  *) echo "사용: $0 <codex|claude>" >&2; exit 2 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
BASE="$(bash "$SCRIPT_DIR/check-review-tools.sh" "$RUNNER")"
AVAILABLE="$(printf '%s\n' "$BASE" | sed -n 's/^AVAILABLE: //p')"

has_tool() {
  case " $AVAILABLE " in
    *" $1 "*) return 0 ;;
    *) return 1 ;;
  esac
}

CODEX_REVIEW_MODE="unavailable"
CODEX_REVIEWER="none"
external=()

if [ "$RUNNER" = "codex" ]; then
  CODEX_REVIEW_MODE="internal-cross-review"
  CODEX_REVIEWER="woozoo-codex-cross-reviewer"
else
  if has_tool codex; then
    CODEX_REVIEW_MODE="external-independent"
    CODEX_REVIEWER="codex"
    external+=("codex")
  fi
fi

if has_tool agy; then
  external+=("agy")
elif has_tool gemini; then
  external+=("gemini")
fi

echo "PROJECT_REVIEW_POLICY: codex-primary-no-claude"
echo "RUNNER: $RUNNER"
echo "CODEX_REVIEW_MODE: $CODEX_REVIEW_MODE"
echo "CODEX_REVIEWER: $CODEX_REVIEWER"
if [ "$RUNNER" = "codex" ]; then
  echo "INTERNAL_REVIEWER: woozoo-codex-cross-reviewer"
else
  echo "INTERNAL_REVIEWER: none"
fi
echo "CLAUDE_REVIEWER: disabled"
if [ "${#external[@]}" -eq 0 ]; then
  echo "EXTERNAL_REVIEWERS: none"
  echo "REVIEWERS: none"
else
  echo "EXTERNAL_REVIEWERS: ${external[*]}"
  echo "REVIEWERS: ${external[*]}"
fi
exit 0
