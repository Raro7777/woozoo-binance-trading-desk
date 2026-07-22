# Phase 0 작업결과서: MVP 계획·합격 기준과 설계 패키지

## 1. 작업 요약

- 원본 DOCX와 저장소를 감사하고 Phase 0~7을 로컬 Paper MVP, Phase 8을 Spot Testnet Validation, Phase 9를 Readiness로 분리한 권고안을 작성했다.
- MVP-01~12, 사용자 여정 UJ-01~05, P0-01~12, Phase 1~7의 43개 필수 scenario와 Go/No-Go 기준을 고정했다.
- 아키텍처·신뢰/비밀 경계, ERD·보존, API/event, 금융·주문·Risk 불변식, 로컬/CI 계약과 정확한 Phase 1 범위를 설계했다.
- Claude 일반 reviewer를 제거하고 Codex same-engine 내부 교차검토 역할과 외부 agy/Gemini 리뷰 경로를 분리했다.
- Phase 0 경계를 지켜 애플리케이션, migration, product fixture/test, Paper Broker, exchange credential·주문 capability는 만들지 않았다.

## 2. 변경 파일

- `docs/woozoo-trading-desk/mvp-plan-and-acceptance.md`: MVP 범위, 단계 계획, 12개 must-pass와 Go/No-Go.
- `docs/woozoo-trading-desk/phase-0/`: 요구 감사·원자 trace, Binance 전제, architecture/data/API/finance/test/CI/P1 handoff.
- `docs/woozoo-trading-desk/phase-0/tools/validate_requirements_trace.py`: DOCX/source/target trace와 축소 공격을 검증하는 stdlib 도구.
- `AGENTS.md`, `CLAUDE.md`, `.agents/skills/`, `.codex/agents/`: Phase/safety/orchestration과 Codex 교차검토 정책.

## 3. 검증 결과

- 원본 DOCX SHA-256: `2ded3774ee98f4f808853c03a072fbbdefb3135473711463da9939daa188e8a7`; 변경 없음.
- 요구 trace baseline/refresh: `123 requirements / 8 aliases / 77 targets`, acceptance `UNVERIFIED`.
- Target 분모: MVP 12 + P0 12 + Phase 10 + scenario 43. Scenario Phase 분모는 `9/7/4/8/6/4/5`, ID·표 열 중복/오류 0.
- Historical baseline negative controls 9건은 전부 기대 실패 또는 fail-closed였다. 현재 frozen policy-resolution binding은 별도 omission control을 추가해 total 10건으로 재검증한다.
- Catalog+candidate 동시 축소는 validator 밖 target count 77/fingerprint로 차단되고 original/temp manifest·catalog digest가 보존됐다.
- Codex 교차검토 최종: `NO_CONFIRMED_FINDINGS`; `review_class=internal_same_engine_cross_review`, `engine_family=codex`, `independence_class=same_engine_separate_context`, `external_gate_satisfied=false`.
- 역할 분리 Safety QA 최종: `SAFETY_PASS_CANDIDATE / NO_FINDINGS`, blocking safety defect 0.
- 외부 agy review final-v4: 2회 연속 `NO_CONFIRMED_FINDINGS`, `termination_reason=converged-good`. 외부 sandbox에는 Python이 없어 정적 분석을 수행했고, orchestrator 로컬에서 baseline/refresh/9 controls를 실제 실행했다.
- 외부 verdict ledger validator: `VALID`, issue 0. `jq` 부재로 scorecard는 `eval-unavailable`; 외부 리뷰 결과를 PASS로 대체하거나 왜곡하지 않았다.
- Codex agent TOML 6개 parse PASS, product root 0, branch `codex/phase-0-design`, 저장소는 unborn/all-untracked.

## 4. 미해결 / 후속

- MVP/Testnet/Readiness cut, 파생 telemetry·뉴스/소셜 Release 1.1 이월과 세부 데이터·금융·Risk·TTL·계약 정책의 사용자 승인이 필요하다.
- P0-01~12의 최종 path+SHA+verdict evidence manifest와 acceptance digest는 아직 만들지 않았다.
- 승인된 Conventional Commit, push와 `main` 대상 Draft PR evidence가 없다.
- Phase 1~7 제품 코드와 GREEN evidence는 아직 없으며 현재 제품 상태는 `NO-GO`다.
- Safety QA가 만든 workspace 밖 임시 복제본 하나는 도구 정책이 recursive cleanup을 차단해 OS temp에 남았으며 credential은 없다.

## 5. 외부 엔진 리뷰 반영

- Reviewer: `agy`, `Gemini 3.1 Pro (High)`; Claude reviewer는 사용하지 않았다.
- Final stage: `phase0-final-v4`, risk level `major`, rounds 2, dry streak 2, confirmed finding 0, `converged-good`.
- Evidence: `_workspace/reviews/phase0-final-v4_agy_round1.md`, `_workspace/reviews/phase0-final-v4_agy_round2.md`, `_workspace/reviews/phase0-final-v4_review_status.json`, `_workspace/reviews/phase0-final-v4_verdicts.json`.
- External sandbox Python 부재는 limitation으로 기록했다. 동일 검증 명령은 로컬에서 실행해 결과와 canonical manifest hash 불변을 확인했다.

## 6. 실패 컨텍스트

- 설계 검토에서 승인 UI preview 미결속, journal UNIQUE 충돌, command hash 불일치, trace target/source 재현성, validator refresh 원자성, inventory/catalog 동시 축소, approval/authorization 용어 혼합을 발견해 수정했다.
- 최종 동결본에는 이 지적을 재현하는 contract/property/E2E oracle과 trace negative control이 남아 있다.

## 7. 다음 단계 참조

- 사용자는 권고된 Phase 0~7 Paper MVP cut과 Release 1.1 이월, 보수적 정책 묶음을 먼저 승인하거나 수정한다.
- 그 결정을 반영하면 전체 Phase 0 artifact를 다시 동결하고 P0-01~12 verdict·SHA manifest와 acceptance digest를 생성한다.
- 별도 Git 권한을 받으면 stage → Conventional Commit → push → `main` 대상 Draft PR evidence를 만든다.
- 위 조건과 digest-bound Phase 1 승인이 모두 충족되기 전에는 Phase 1 제품 골격을 만들지 않는다.
