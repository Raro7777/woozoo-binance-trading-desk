# Phase 0 작업결과서: myharness 개발 하네스 준비

## 1. 작업 요약

- 원본 `Woozoo_Binance_Trading_Desk_Codex_Handoff-5.docx`를 구조적으로 읽고, 애플리케이션 구현 전에 사용할 프로젝트 로컬 듀얼 런타임 하네스를 준비했다.
- Codex와 Claude Code에 각각 5개 전문 개발 에이전트와 6개 스킬(오케스트레이터, 안전 경계, 금융 불변조건, Binance 공개 데이터, Phase 게이트, 외부 리뷰 루프)을 연결했다.
- `docs/woozoo-trading-desk/phase-state.json`을 단일 단계 상태로 두고 제품 Phase 0, 승인 미요청, 문서·하네스·설계·테스트 설계만 허용하도록 fail-closed 범위를 고정했다.
- Mainnet private/live, 출금, Futures·Margin·leverage·short, AI 직접 주문과 Phase 8 전 Testnet Gateway capability를 구조적으로 금지했다.
- 브랜치는 `codex/phase-0-design`으로 만들었으며 애플리케이션·거래·실행 테스트 코드는 생성하지 않았다. 원본 DOCX는 수정하지 않았다.
- 외부 후보 스킬은 즉시 설치하지 않고 관련 제품 Phase 직전에 전체 감사와 commit SHA 고정을 거치는 라우팅 정책으로만 준비했다.

이 결과서는 **하네스 준비 완료**를 기록한다. P0-01~P0-12의 실제 설계 산출물과 acceptance evidence는 아직 생성하지 않았으므로 제품 Phase 0 완료를 주장하지 않는다.

## 2. 변경 파일

- `AGENTS.md`, `CLAUDE.md`: 런타임별 얇은 진입 포인터, 안전·Phase 규칙, `TRADING_MODE=paper` 기본과 P0-12 추적.
- `.codex/agents/*.toml`, `.claude/agents/*.md`: system architect, market/agent, risk/ledger, web/platform, safety QA의 5개 개발 역할.
- `.agents/skills/**`, `.claude/skills/**`: 6개 듀얼 런타임 스킬과 정책 reference·review script.
- `.agents/skills/woozoo-orchestrator/scripts/update-factory-managed.sh`, Claude 동등 파일: 분리된 reference와 review script를 중앙 myharness updater가 서로 `NEW`로 오인하지 않도록 하는 안전 갱신 어댑터.
- `docs/woozoo-trading-desk/phase-state.json`: 런타임 중립 acceptance contract ID와 Codex/Claude 실제 경로, 승인·허용·금지 범위.
- `docs/woozoo-trading-desk/working_template/working_history_template.md`: T1 영속 결과서 형식.
- `.gitignore`: 임시 `_workspace/`, 백업 작업공간과 staging backup 제외.
- `.harness-manifest.json` 4개: myharness v1 관리 대상 reference 2개와 script 2개의 생성 기준 SHA-256. 에이전트·스킬 본문과 `*.local.*`은 사용자 소유이므로 관리 대상이 아니다.

## 3. 검증 결과

- Phase 상태·계약 경로: PASS — `current_phase=0`, approval=`not_requested`, 런타임 중립 ID와 두 실제 계약 파일 존재.
- 하네스 구조: PASS — `.agents`/`.claude` 스킬 각 6개, Codex/Claude 전문 에이전트 각 5개.
- Codex agent schema: PASS — TOML 5개가 파싱되고 `name`, `description`, `developer_instructions`를 가지며 지원되지 않는 `prompt` 필드가 없다.
- 듀얼 런타임 parity: PASS — 런타임 경로 치환을 제외한 Woozoo 정책 파일과 새 updater adapter가 동등하다.
- 매니페스트: PASS — 4개 JSON 파싱과 관리파일 SHA-256 전건 일치.
- shell: PASS — 외부 리뷰 script 4개와 updater adapter 2개의 `bash -n` 통과.
- updater 통합: PASS — 양 런타임 adapter `plan`이 각각 관련 관리파일만 `SAME 4`, `NEW/UNKNOWN/USER-MODIFIED/UPDATABLE 0`; 잘못된 명령·runtime은 exit 2; 임시 경로 정리 확인.
- Phase 0 코드 게이트: PASS — `apps`, `services`, `packages`, `infra`, `migrations`, `src`, `tests` 아래 실행 가능한 앱·SQL·테스트 코드 0개.
- credential material scan: PASS — private key와 실제 key/secret 형태 0건.
- 읽기 전용 smoke: PASS — Phase 0 설계 요청은 architect + 세 설계 전용 builder → 독립 safety QA로 라우팅; Mainnet BTCUSDT 자동 시장가 주문 요청은 키·네트워크·서명·파일 변경 없이 fail-closed 차단.
- 내부 독립 아키텍처/회귀 QA: CLEAN — 안전·금융·Phase 회귀 없음. updater adapter도 양 런타임에서 독립 검증했다.
- Codex doctor: agent 정의 오류 없음. 전체 명령의 non-zero는 비대화 터미널의 `TERM=dumb` 진단뿐이며 하네스 schema 오류와 무관하다.

## 4. 미해결 / 후속

- P0-01~P0-12는 아직 미실행이다. 공식 Binance Spot·Spot Testnet 문서 검증(P0-02), 공개 파생 telemetry의 Release 1 포함·출처 allowlist 결정(P0-03), cost basis·valuation posting(P0-08), 부록 A 운영 규칙 집약(P0-12)이 실제 Phase 0 산출물에서 필요하다.
- 외부 GitHub 스킬은 설치하지 않았다. 관련 Phase 직전에 전체 소스·권한·network/credential 범위를 감사하고 commit SHA를 고정한 최소 단위만 도입한다. 거래 MCP, Binance Skills Hub의 주문 기능과 live connector는 도입하지 않는다.
- `jq`가 없어 공식 `harness-update.sh manifest` 생성과 review scorecard 계산은 사용할 수 없었다. 현재 기준선은 같은 SHA-256 규칙으로 결정적으로 생성·검증했으며 scorecard는 `eval-unavailable`이다. 향후 `jq`가 준비되면 전용 updater adapter로 manifest를 재생성한다.
- 원본 DOCX는 구조적으로 파싱했으나 LibreOffice가 없어 페이지 렌더 기반 시각 검증은 수행하지 못했다. 원본은 변경하지 않았다.
- agy 리뷰는 첫 실행 실패 후 안전한 headless 재시도에서도 command permission이 자동 거부되어 수집하지 못했다. `--dangerously-skip-permissions`는 사용하지 않았고 Claude 외부 리뷰와 내부 독립 QA로 진행했다.
- 파일을 stage·commit·push하거나 PR을 만들지 않았다.

## 5. 외부 리뷰 반영

- 외부 일반 리뷰어: Claude. 성능·안정성 리뷰어 후보 agy는 권한 문제로 미수집.
- Round 1: P0/P1 0건, P2 2건, P3 5건. 최종 판정은 확인 5건, 부분 확인 2건으로 기록했다.
- 반영: runner 중립 `REVIEWERS:` 규칙, 에이전트 I/O 기반 단일 산출물 이름, P0-03 공개 파생 telemetry 추적, Phase 0 문서 테스트 설계 한정, P0-12 추적, runtime-neutral phase contract 참조를 적용했다.
- Round 2와 외부 Round 3: 수정 범위에서 각각 CLEAN.
- 외부 Round 3 직후 결정적 중앙 updater `plan` 통합 시험이 외부 리뷰가 놓친 split-skill `NEW` 오인 1건을 발견했다. 이를 확인 판정하고 듀얼 런타임 updater adapter로 수정했다.
- 외부 루프는 MAX_ROUNDS에 도달했으므로 종료 라벨은 `max-rounds`이며 `converged-good`으로 과장하지 않는다. 추가 수정은 shell·plan 결정적 게이트와 내부 독립 QA에서 CLEAN이다. 현재 알려진 미해결 안전 결함은 없다.
- 판정 원장: `_workspace/reviews/harness-preparation_verdicts.json`(임시·git 제외). scorecard는 `jq` 부재로 `eval-unavailable`.

## 7. 다음 단계 참조

- 다음 작업은 **여전히 제품 Phase 0 설계**다. Phase 1 구현을 시작하지 않는다.
- `woozoo-orchestrator`로 P0-01~P0-12를 실행하고, 에이전트별 `_workspace/{phase}_woozoo-<agent>_{artifact}.md` 규약으로 설계 산출물을 만든 뒤 safety QA를 독립 후행한다.
- 각 acceptance 항목에 `PASS|FAIL|UNVERIFIED`, 증거 경로와 SHA-256을 기록하고 정렬된 목록의 evidence digest를 만든다.
- 모든 항목 PASS, 금지 delta 0, 안전 QA와 외부 리뷰가 끝난 뒤에만 그 digest에 결속된 명시적 사용자 Phase 전환 승인을 요청한다.
- 그 전까지 앱 코드, 실행 테스트·fixture·scaffold, DB migration, 거래 로직, API key, Testnet Gateway와 주문 capability를 만들지 않는다.
- 하네스 갱신은 중앙 updater를 직접 적용하지 말고 `update-factory-managed.sh plan`을 먼저 사용한다. 현재 환경에서는 Git Bash를 명시하고 `jq` 부재를 보수 모드로 취급한다.
