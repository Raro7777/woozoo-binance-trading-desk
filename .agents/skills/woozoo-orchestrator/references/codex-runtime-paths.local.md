# Codex 런타임 경로 오버라이드

Factory 정본의 주입 예시에는 다른 런타임용 `.claude` 경로나 과거 문서명 `external-review-loop.md`가 남아 있을 수 있다. Codex 에이전트는 그 예시를 실행 경로로 사용하지 않고 아래 프로젝트 실경로만 사용한다.

- 개발 규칙: `.agents/skills/woozoo-orchestrator/references/dev-rules.md`
- 프로젝트 추가 규칙: `.agents/skills/woozoo-orchestrator/references/dev-rules.local.md`
- TDD 규율: `.agents/skills/woozoo-orchestrator/references/tdd-doctrine.md`
- 외부 리뷰 루프: `.agents/skills/external-review-loop/SKILL.md`

Codex 런타임에서 `.claude/...`를 fallback으로 읽거나 `external-review-loop.md`를 추측해 찾지 않는다. 필요한 `.agents/...` 실경로가 없으면 해당 절차를 중단하고 누락을 보고한다.
