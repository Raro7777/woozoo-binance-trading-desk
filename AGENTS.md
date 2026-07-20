## 하네스: Woozoo Binance Trading Desk

**목표:** Binance Spot 공개 데이터를 이용하는 AI 연구·Paper Trading 플랫폼을 단계별로 설계하고 검증하되, 주문·잔고·원장·위험 판단은 결정론적 코드와 사람 승인 경계 안에 둔다.

**트리거:** Woozoo의 설계, 구현, 테스트, Binance 데이터, 에이전트, Paper Broker, Risk Engine, 원장, 승인 UI, 운영 또는 이전 결과의 재실행·수정·보완 요청에는 `.agents/skills/woozoo-orchestrator/SKILL.md`를 사용하라. 단순 개념 질문은 직접 답할 수 있다.

**안전 포인터:** 모든 작업은 `.agents/skills/woozoo-safety-boundaries/SKILL.md`와 `.agents/skills/woozoo-phase-gates/SKILL.md`를 먼저 적용한다. Mainnet 비공개 주문, 실거래, 출금, 선물, 마진, 레버리지, 숏, 사람 승인 없는 외부 주문과 AI의 주문 도구 접근은 금지한다.

**현재 단계:** `docs/woozoo-trading-desk/phase-state.json`이 유일한 단계 상태다. 명시적 사용자 승인과 단계 게이트 통과 없이 다음 Phase로 넘어가지 않는다.

**기본 실행 모드:** 이후 애플리케이션 설정의 기본은 `TRADING_MODE=paper`다. 값이 없거나 알 수 없으면 fail-closed하고, Mainnet private/live로 fallback하지 않는다.

**P0-12 추적:** 원본 부록 A의 Mission·Engineering·Security·DoD·Workflow·품질 명령을 루트 운영 규칙으로 집약하는 일은 Phase 0 acceptance 항목 P0-12다. 이 포인터가 있다는 사실만으로 P0-12를 PASS 처리하지 않는다.

### 루트 운영 규칙 — 원본 부록 A 집약

1. **Mission:** 공개 Binance Spot 데이터로 근거를 재생할 수 있는 연구·Paper Trading 운영실을 만든다. 빠른 데모보다 자금 안전, 재현성, 감사 가능성을 우선한다.
2. **기본 모드:** `TRADING_MODE=paper`를 명시해야만 시작한다. 누락·미지 값, stale/invalid 데이터, 불완전 Evidence와 정책 누락은 모두 fail-closed/HOLD다.
3. **금융 권위:** 가격·수량·잔고·fee·PnL·노출은 Decimal과 버전 정책으로만 계산한다. 주문·체결·Risk·승인 유효성·원장은 결정론적 코드와 Postgres transaction이 권위다.
4. **AI 경계:** AI는 immutable Evidence를 읽고 구조화 분석과 `TradeProposal`만 만든다. 계정·잔고·Risk verdict·승인·주문 도구, 거래소 credential과 외부 주문 capability를 받지 않는다.
5. **데이터 무결성:** raw append와 provenance를 보존하고 `event_time`, `received_at`, `as_of`, `knowledge_cutoff`, watermark·quality를 구분한다. 미래 오염·gap·schema 오류·raw 저장 실패를 정상으로 숨기지 않는다.
6. **Engineering:** 계약과 불변조건을 먼저 고정하고 작은 typed 변경으로 구현한다. inbox/domain/ledger/outbox 원자성, idempotency, immutable event·ledger와 reversal+replacement를 유지한다.
7. **Security:** 비밀은 브라우저·로그·prompt·tool·fixture에 넣지 않고 최소권한 process에만 둔다. Phase 1~7에는 Testnet/private schema·config·dependency·egress·capability가 0이어야 한다.
8. **Workflow:** 매 작업 전 `phase-state.json`과 현재 acceptance를 확인하고 승인된 Phase 범위만 변경한다. Phase별 `codex/phase-{n}-{slug}` 브랜치, RED→GREEN→refactor, diff self-review, 역할 분리 safety QA와 리뷰 증거를 사용한다.
9. **Definition of Done:** 관련 코드·계약·문서·테스트·운영 증거가 함께 갱신되고 필수 manifest가 전건 PASS여야 한다. `UNVERIFIED`, 금지 capability, 비밀 노출, 원장 불균형, 미추적 요구가 하나라도 있으면 완료가 아니다.
10. **품질 명령:** 해당 Phase에서 생성이 승인된 뒤 루트 `pnpm bootstrap`, `pnpm env:init`, `pnpm lint`, `pnpm typecheck`, `pnpm test:unit`, `pnpm test:contracts`, `pnpm test:safety`, `pnpm test:integration`, `pnpm test:property`, `pnpm test:replay`, `pnpm test:failure`, `pnpm test:e2e`, `pnpm build`, `pnpm ci`를 정본으로 사용한다. `pnpm ci`는 현재 Phase의 필수 명령 전부를 포함한다. 존재하지 않거나 실행하지 않은 명령은 PASS로 기록하지 않는다.

**Codex 어댑터:** `.codex/agents/*.toml`의 전문 에이전트를 병렬 subagent로 사용한다. architect·builder·safety QA는 보고서 텍스트만 반환하고, 오케스트레이터만 `_workspace/{phase}_{agent}_{artifact}` 계약으로 단계별 산출물을 영속화한다. Codex 내부 교차검토는 별도 `_workspace/internal-reviews/` 경로를 쓰되 같은 단일 persistence-owner 원칙을 따른다. 동시 실행은 기본 3개를 넘지 않는다.

**리뷰 정책:** 일반 대조 검토는 Codex 전용 `woozoo-codex-cross-reviewer`가 읽기 전용·분리 컨텍스트에서 수행한다. 이는 동일 엔진 내부 검토이므로 외부 독립 리뷰가 아니며 safety QA를 대체하지 않는다. Claude reviewer는 사용하지 않고, 외부 엔진은 프로젝트 선택기가 허용한 agy/Gemini만 사용한다. 없거나 실패하면 `external-review-unavailable`을 기록한다.

**변경 이력:**

| 날짜 | 변경 내용 | 대상 | 사유 |
|------|-----------|------|------|
| 2026-07-19 | Claude 일반 검토를 제거하고 Codex 전용 내부 교차검토 역할·외부 리뷰 분리 정책 추가 | 하네스 리뷰 경로 | 같은 엔진 한계를 숨기지 않으면서 Codex 중심 검토를 표준화 |
| 2026-07-19 | 외부 대조 리뷰 후 산출물 규약·Phase 0 테스트 경계·P0 추적·분리형 갱신 어댑터 보강 | 하네스 정책 | 재실행 모호성, 단계 월경과 잘못된 factory 파일 주입 방지 |
| 2026-07-19 | 초기 듀얼 런타임 하네스 포인터 등록 | 전체 | Phase 0 안전한 개발 준비 |
