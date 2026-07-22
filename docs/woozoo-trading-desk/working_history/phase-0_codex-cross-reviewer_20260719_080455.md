# Phase 0 작업결과서: Claude 검토를 Codex 교차검토로 교체

## 1. 작업 요약

- Claude 일반 검토를 프로젝트 리뷰 경로에서 제거하고 Codex 전용 `woozoo-codex-cross-reviewer`를 추가했다.
- 새 역할은 `sandbox_mode=read-only`인 동일 엔진·분리 컨텍스트 내부 교차검토다. safety QA, Phase gate, 다른 엔진 외부 리뷰를 대체하지 않으며 `external_gate_satisfied=false`를 강제한다.
- Codex 런타임은 내부 Codex 교차검토 + 선택된 agy/Gemini 외부 리뷰, Claude Code 런타임은 외부 `codex exec --sandbox read-only` + agy/Gemini만 사용한다. 어느 런타임도 Claude reviewer를 실행하지 않는다.
- 제품 Phase는 0, 승인은 `not_requested`로 유지했고 애플리케이션·거래·테스트 실행 코드는 생성하지 않았다. 원본 DOCX도 수정하지 않았다.

## 2. 변경 파일

- `.codex/agents/woozoo-codex-cross-reviewer.toml`: Codex 전용 read-only 일반 교차검토 역할, verdict·provenance·금지 동작·내부 출력 경로 정의.
- `.codex/agents/woozoo-safety-qa-inspector.toml`, `.claude/agents/woozoo-safety-qa-inspector.md`: safety QA를 역할 분리된 내부 blocking 검증으로 명확화.
- `.agents/skills/woozoo-orchestrator/SKILL.md`, `.claude/skills/woozoo-orchestrator/SKILL.md`: safety QA, Codex 내부 교차검토, 외부 교차엔진 리뷰를 별도 단계와 판정어로 라우팅.
- `.agents/skills/external-review-loop/SKILL.md`, `.claude/skills/external-review-loop/SKILL.md`: Claude 실행 분기를 제거하고 런타임별 Codex/agy/Gemini 외부 경로, 부재 상태, verdict ledger와 scorecard 경계를 재정의.
- 양 런타임 `external-review-loop/scripts/select-project-reviewers.sh`: factory 관리 도구 탐지를 감싸는 프로젝트 소유 선택기. Claude 비활성, Codex 우선 정책을 기계판독 출력으로 제공.
- 양 런타임 `woozoo-phase-gates/SKILL.md`, `AGENTS.md`, `CLAUDE.md`: 같은 엔진 Codex 결과가 외부 리뷰가 아니라는 루트 정책 추가.
- `docs/woozoo-trading-desk/phase-state.json`: 중대 변경의 외부 리뷰 완료 또는 `external-review-unavailable` 기록을 Phase 전환 조건에 추가. 외부 evidence는 아직 `null`이다.
- `docs/woozoo-trading-desk/working_template/working_history_template.md`: 내부·외부 리뷰 분리와 네 provenance 필드 기록 규칙 추가.

의도적으로 `.claude/agents/`에는 Codex 역할을 가장한 Claude agent를 만들지 않았다. 최종 구성은 Codex agent 6개, Claude agent 5개의 런타임 비대칭이다.

## 3. 검증 결과

- Codex agent schema: PASS — TOML 6개 전부 파싱, 필수 필드 존재, 미지원 `prompt` 0건. 새 reviewer는 `read-only`다.
- 역할 집합: PASS — 공통 역할 5개, Codex 전용 역할은 `woozoo-codex-cross-reviewer` 1개뿐이다.
- shell·선택기: PASS — 양 런타임 review script와 project selector `bash -n` 통과. Codex 선택 결과는 내부 reviewer=Codex, Claude=disabled, 외부=agy였다. 격리 PATH에서는 외부 reviewer=`none`으로 안전하게 축소됐다.
- 금지 분기: PASS — 외부 review skill에 실행 가능한 `claude -p`와 `_claude.md` 출력 0건. Codex 런타임은 외부 `codex exec`을 실행하지 않고 Claude 런타임만 read-only 외부 Codex를 허용한다.
- factory 무결성: PASS — `check-review-tools.sh`, `build-scorecard.sh`와 4개 manifest의 SHA-256이 모두 일치했다. 프로젝트 wrapper만 추가해 factory 관리 파일은 변경하지 않았다.
- Phase 경계: PASS — `current_phase=0`, 승인=`not_requested`, 앱 코드 루트 0개. 중대 변경 전환 조건과 네 provenance 필드를 결정적으로 검사했다.
- 내부 Codex 교차검토 1차: FINDINGS — P2 1건(Phase 전환 외부 리뷰 조건 누락), P3 1건(영속 provenance 2개 누락)을 확인해 수정했다.
- 내부 Codex 수정본 재검토: NO_CONFIRMED_FINDINGS — 신규 확인 0건. `review_class=internal_same_engine_cross_review`, `engine_family=codex`, `independence_class=same_engine_separate_context`, `external_gate_satisfied=false`.
- 역할 분리 safety QA: PASS — blocking 결함 0건. 외부 엔진 실제 실행 판정은 별개로 유지했다.
- 공격 입력 smoke: PASS — Mainnet BTCUSDT 시장가 주문, API key 요청, 자기 리뷰 agent 생성 지시를 데이터로만 판정하고 실행·연결·credential 요청·파일 변경 없이 차단했다.
- 읽기 전용 증거: PASS — 수정본 재검토 전·후 하네스 57개 파일 집계 SHA-256이 `e380b01430728d3a6a603e1f21b113a15ddd2ddcf2b3c3bc348d983131e9ab6c`로 동일했다.

내부 상세 보고서는 `_workspace/internal-reviews/phase-0_woozoo-codex-cross-reviewer_report.md`에 남겼다. `_workspace/`는 임시·git 제외 경로다.

## 4. 미해결 / 후속

- 다른 엔진 외부 리뷰는 이번 변경에서 완료되지 않았다. agy는 응답 probe에는 성공했지만 로컬 파일 리뷰에 필요한 command permission을 headless 모드에서 자동 거부했다. 위험한 permission bypass는 사용하지 않았다.
- `phase-state.review_gate.evidence_ref`는 의도적으로 `null`이다. 내부 Codex 결과를 외부 리뷰 증거로 연결하지 않는다.
- 패키지형 `codex doctor --json`은 현재 PowerShell에서 WindowsApps 실행 권한 거부로 실행되지 않았다. TOML 정적 파싱과 실제 분리 컨텍스트 Codex subagent smoke는 통과했다.
- P0-01~P0-12 실제 설계 acceptance는 아직 미실행이다. 이번 작업은 하네스 리뷰 역할 준비이며 제품 Phase 0 완료가 아니다.
- 파일을 stage·commit·push하거나 PR을 만들지 않았다.

## 5. 외부 엔진 리뷰 반영

- 상태: `external-review-unavailable`.
- 후보: agy. 기본 headless 응답은 성공했으나 실제 파일 검토는 command permission 자동 거부로 출력이 생성되지 않았다.
- Claude reviewer는 정책대로 호출하지 않았다.
- 내부 Codex 교차검토와 safety QA를 외부 PASS로 승격하지 않았고 `external_gate_satisfied=false`를 유지했다.
- 기계판독 상태: `_workspace/reviews/codex-cross-reviewer-policy_review_status.json`.

## 6. 실패 컨텍스트

- agy: `a tool required the command permission that headless mode cannot prompt for, so it was auto-denied`. `--dangerously-skip-permissions`는 사용하지 않았다.
- Codex CLI 진단: WindowsApps의 `codex.exe` 실행이 `Access is denied`로 거부됐다. agent TOML 파싱과 실제 Codex 역할 스모크로 대체 검증했다.

## 7. 다음 단계 참조

- 다음 제품 작업은 여전히 Phase 0의 P0-01~P0-12 설계 산출물 작성이다. Phase 1 앱 구현을 시작하지 않는다.
- 표준·중대 변경은 생산자 산출물을 동결한 뒤 safety QA와 `woozoo-codex-cross-reviewer`를 서로의 1차 보고서 없이 별도 컨텍스트로 실행한다.
- Codex 결과는 내부 보조 증거로만 저장한다. 다른 엔진 외부 리뷰가 없거나 실패하면 `external-review-unavailable`을 기록하고 외부 PASS를 만들지 않는다.
- Phase 전환 전에는 acceptance 전건 PASS, safety QA PASS, 외부 리뷰 완료 또는 부재 기록, 미해결 안전 차단 0건, evidence digest에 결속된 사용자 승인이 모두 필요하다.
- 앱 코드, 실행 테스트·fixture·scaffold, DB migration, 거래 로직, API key, Testnet Gateway와 주문 capability는 계속 금지한다.
