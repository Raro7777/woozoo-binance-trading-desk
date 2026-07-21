# Woozoo 개발 Phase 계약

## Phase 0

각 항목은 `PASS|FAIL|UNVERIFIED`, 증거 파일 경로와 SHA-256을 가진다. 다음 12개가 모두 PASS여야 한다.

Phase 0의 `테스트`는 테스트 매트릭스·최소 반례·property/replay/failure-injection 시나리오를 문서로 설계하는 뜻이다. 실행 가능한 테스트 코드, fixture와 scaffold는 Phase 1 이후 승인된 제품 Phase에서 RED로 만든다.

| ID | Acceptance item |
|----|-----------------|
| P0-01 | 저장소와 원본 핸드오프 전체 감사 |
| P0-02 | 공식 Binance Spot·Spot Testnet 문서로 변동 가능한 전제 검증 |
| P0-03 | 모순·누락·위험 가정과 미결정 질문 목록. 공개 파생시장 텔레메트리의 Release 1 포함 여부, 무인증 출처 allowlist와 거래 capability 완전 분리를 명시한다. |
| P0-04 | 최종 모노레포 구조 |
| P0-05 | 아키텍처, 서비스 책임, 신뢰·비밀 경계 |
| P0-06 | ERD와 데이터 보존 계획 |
| P0-07 | 버전이 있는 API·이벤트 계약 |
| P0-08 | 회계·cost basis·멱등성·주문 불변조건 |
| P0-09 | replay·property-based·failure-injection·Mainnet 차단 테스트 전략 |
| P0-10 | Mainnet private 주문 접근이 구조적으로 불가능한 설계 |
| P0-11 | 로컬 개발·CI 명령 설계 |
| P0-12 | 관련 문서, 루트 운영 규칙(원본 부록 A), 정확한 Phase 1 범위와 완료 보고서 갱신 |

추가 Git 게이트:

- 브랜치는 `codex/phase-0-design`이고 `main` 직접 수정이 아니다.
- 애플리케이션 소스, 실행 가능한 테스트 코드·fixture·scaffold, DB migration, 거래 로직, Paper Broker 또는 주문 gateway delta가 0이다.
- diff 자체 리뷰, 승인된 Conventional Commit과 main 대상 Draft PR 증거가 있다.
- 완료 보고서에 변경 파일, 구조, 기술·안전 결정, 승인 질문, Phase 1 범위, 실행 명령, 알려진 한계가 있다.

검증된 artifacts의 정렬된 `path + SHA-256 + verdict` 목록을 acceptance evidence digest로 묶는다. 항목 누락, `UNVERIFIED`, 금지 delta 또는 Git 게이트 실패가 있으면 Phase 0은 `awaiting_evidence`로 남고 전환 승인을 요청하지 않는다.

## Phase 7

Phase 7은 로컬 단일 운영자를 위한 Paper 전용 Trading Room MVP를 완성한다. 아래 기준은 모두 `PASS|FAIL|UNVERIFIED`와 증거 파일 경로·SHA-256을 가지며, `UNVERIFIED`는 PASS로 취급하지 않는다.

### 허용 범위

- 불변 Evidence → 구조화 AI 분석·TradeProposal → 결정론적 Risk → 사람 승인·거절 → 일회성 Paper authorization → Paper 주문·원장·감사로 이어지는 운영 경로를 production Paper namespace에서 활성화한다.
- 단일 운영자 인증, session·Origin·CSRF 방어, 승인·거절·철회, 수동 Kill Switch·복구, 포트폴리오·원장·감사·운영 화면과 이에 필요한 계약·migration·테스트·CI·문서를 구현한다.
- 브라우저에 표시되는 제목·상태·오류·검증·명령 문구는 한국어로 제공하고 desktop·mobile viewport, keyboard 조작, overflow·route/global error와 Axe serious/critical 0건을 실제 브라우저 E2E로 검증한다.

### 금지 범위

- Phase 8 전 Testnet gateway의 schema·config·dependency·credential·private exchange capability와 모든 외부 주문·취소·계좌 접근을 만들거나 연결하지 않는다.
- AI에는 계좌·잔고·Risk verdict·승인·주문·거래소 도구를 제공하지 않는다. 브라우저는 인증된 사람 승인·거절 API만 호출하며 주문·취소·계좌·잔고·거래소·비밀 도구에 직접 접근하지 않고, 비구조화·미검증 모델 출력이나 UI 값을 금융 권위로 사용하지 않는다.
- Mainnet private/live, 출금, Futures, 마진, 레버리지, 숏과 공개 파생시장 텔레메트리는 schema·UI·fixture·예제까지 금지한다.

### 결정론적 승인·Paper 권위

- 승인·거절을 기록하려면 해당 Proposal의 최신 Risk decision이 완전하고 `ALLOWED`여야 한다. 누락·불완전·`DENIED`·`ERROR`·오래된 결정은 효과 없이 차단한다.
- 이미 커밋된 동일 idempotency key·동일 요청의 durable receipt 재생은 worker·Kill Switch·reconciliation 등 변경 가능한 현재 상태 검사보다 먼저 처리한다. 응답 유실이나 재시작 뒤에도 원래 receipt를 반환하고 효과를 추가하지 않으며, 같은 키의 다른 요청 본문은 conflict로 거절한다.
- 새 `APPROVE`는 고정된 Paper account → proposal/Risk authority → Kill Switch·reconciliation → worker 순서의 잠금과 검사를 DB transaction 안에서 수행한다. 승인 발급의 approval·authorization·receipt·outbox와 별도 첫 Paper 시도의 authorization attempt·domain state·ledger·outbox는 각각 원자적으로 커밋하거나 모두 롤백하고, unique constraint와 idempotency key로 중복 효과를 차단한다.
- worker가 누락·중단·실패·stale·future-dated 상태이거나 Kill Switch가 활성이고, 데이터·원장·reconciliation이 누락·stale·불일치이면 새 승인을 fail-closed로 차단한다. 다만 그 밖의 조건을 모두 만족한 `ALLOWED` Proposal의 사람 `REJECT`는 worker 장애와 무관하게 정확히 한 번 기록되고 authorization을 만들지 않는다.
- authorization은 승인 nonce와 구별되는 최대 5분 TTL의 일회성 nonce에 정확한 Proposal·Risk·preview hash를 결속한다. 첫 Paper 시도는 주문 생성 또는 `BLOCKED`로 terminal이며 재시도·재생은 주문·체결·원장 효과를 중복 생성하지 않는다.
- 가격·수량·잔고·수수료·PnL·노출은 Decimal 의미론과 버전 정책을 사용한다. Paper 주문·체결·복식 원장·outbox·reconciliation은 Postgres transaction이 권위이며 불균형·음수 잔고·중복 분개는 허용하지 않는다.

### 인수·검토·Git 게이트

- 동결된 Phase 0 매니페스트에서 요구 Phase가 7 이하인 항목과 승인된 일정 수정으로 Phase 7에 배정된 항목을 합친 정확한 43개 분모가 43/43 PASS여야 한다. 누락·skip·빈 테스트 결과·`UNVERIFIED`가 없어야 하고 모든 증거는 동일한 commit·tree·worktree digest와 선언된 source/configuration SHA-256에 결속한다.
- 루트 정본 `corepack pnpm ci`(`pnpm ci`)가 exit 0이며 현재 Phase의 lint, typecheck, unit, contract, safety, integration, property, replay, failure, E2E와 build 필수 명령을 모두 실제 실행한다.
- 역할 분리된 Safety QA는 PASS여야 하고 Codex 내부 교차검토의 확인된 지적은 해소되어야 한다. Codex 검토는 동일 엔진·분리 컨텍스트의 내부 보조 증거일 뿐 외부 독립 리뷰로 계산하지 않는다.
- 중대 변경 외부 리뷰는 허용된 외부 엔진에서 완료하거나, 시도·재시도 실패와 영향을 `external-review-unavailable`로 정직하게 기록한다. 부재를 외부 PASS로 승격하지 않고 Claude reviewer를 사용하지 않는다.
- 브랜치는 `codex/phase-7-trading-room`이고 `main` 대상 Draft PR이 존재해야 한다. 인수 후보의 정확한 revision이 원격에 push되고 그 revision의 push·PR 필수 검사가 성공하며, 검토·Git 증거가 인수 manifest에 결속되어야 한다.
- 정렬된 인수 산출물의 정확한 SHA-256 digest를 사용자에게 제시하고 사용자가 그 digest를 명시적으로 승인한 뒤에만 Phase 7을 accepted로 기록한다. 승인 전에는 `phase-state.json`을 올리거나 Phase 8 구현을 시작하지 않는다.

## 전체 로드맵

| Phase | 목표 | 허용 핵심 범위 | 완료 게이트 |
|------:|------|----------------|-------------|
| 0 | 설계·검증 | 구조, 경계, ERD, 계약, 불변식, 테스트, CI 설계 | 문서·안전 QA·사용자 승인 |
| 1 | 실행 골격 | Python/TS workspace, FastAPI, Next.js, Postgres, Redis, Docker, CI | health와 기본 품질 명령 |
| 2 | 공개 데이터 | BTC/ETH public REST/WS, 재연결, dedupe, replay | 안정적 수집·gap 테스트 |
| 3 | Feature·Evidence | 캔들, 지표, 불변 snapshot, `as_of` | 미래 오염 차단 |
| 4 | Paper Broker·원장 | 지정가, 부분 체결, fee, cancel, position, PnL, ledger | 자산 보존·원장 property |
| 5 | Risk Engine | exposure, loss, drawdown, stale, duplicate, Kill Switch | 결정론적 정책 테스트 |
| 6 | AI 조직 | Mock LLM, 분석가, 토론, Proposal, Audit | 구조화 schema·HOLD fallback |
| 7 | GUI Trading Room | 시장, Agent, Proposal, Risk, 승인, 포트폴리오, 감사 UI | 브라우저 E2E·접근성 |
| 8 | Spot Testnet | 주문·취소·조회·user data·timeout·reconciliation | 기본 OFF·사람 승인·대조 |
| 9 | 장기 모의운영 | 24/7, benchmark, fee/slippage, 장애 훈련, 보안 검토 | 운영 증거와 한계 보고 |

## 공통 게이트

- 각 Phase는 별도 `codex/phase-{n}-{slug}` 브랜치와 독립 Draft PR로 진행한다.
- 테스트, 문서, diff 리뷰와 안전 QA가 끝나기 전 다음 Phase를 시작하지 않는다.
- Phase 8까지 외부 거래소 주문 capability를 만들지 않는다.
- Release 1 전체에서 Mainnet private, 실거래, 출금, Futures, 마진, 레버리지, 숏은 금지다.
- 승인된 Phase 범위를 넓히는 변경은 새 사용자 승인 없이는 적용하지 않는다.
