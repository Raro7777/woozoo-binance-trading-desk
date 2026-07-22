## 하네스: Woozoo Binance Trading Desk

**목표:** Binance Spot 공개 데이터를 이용하는 AI 연구·Paper Trading 플랫폼을 단계별로 설계하고 검증하되, 주문·잔고·원장·위험 판단은 결정론적 코드와 사람 승인 경계 안에 둔다.

**트리거:** Woozoo의 설계, 구현, 테스트, Binance 데이터, 에이전트, Paper Broker, Risk Engine, 원장, 승인 UI, 운영 또는 이전 결과의 재실행·수정·보완 요청에는 `.agents/skills/woozoo-orchestrator/SKILL.md`를 사용하라. 단순 개념 질문은 직접 답할 수 있다.

**안전 포인터:** 모든 작업은 `.agents/skills/woozoo-safety-boundaries/SKILL.md`와 `.agents/skills/woozoo-phase-gates/SKILL.md`를 먼저 적용한다. Mainnet 비공개 주문, 실거래, 출금, 선물, 마진, 레버리지, 숏, 사람 승인 없는 외부 주문과 AI의 주문 도구 접근은 금지한다.

**현재 단계:** `docs/woozoo-trading-desk/phase-state.json`이 유일한 단계 상태다. Phase 8은 일시 중단되었고 PR #8의 Phase 7 Paper MVP 정리 모드가 활성화되어 있다. 중단 중에는 Testnet 구현을 진행하지 않는다.

**Paper MVP 정리 모드:** 일반 기능·버그·문서·UI 변경은 `구현 → 관련 테스트 → 자체 검토 → Draft PR 보고`로 완료한다. 파일별 SHA-256 승인, 테스트별 JSON artifact, 반복 acceptance digest, 동일 엔진 다중 재검토, 매 작업의 외부 리뷰, 사소한 UI 변경의 Phase 승인은 요구하지 않는다. 과거 Manifest와 Evidence는 삭제하지 않고 감사 이력으로만 보존한다. 금융 원장, Risk Engine, 주문 멱등성, Kill Switch, Testnet 주문, Mainnet 전환은 강화 검증 대상으로 유지한다. 세부 정본은 `docs/DEVELOPMENT_WORKFLOW_KO.md`다.

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
8. **Workflow:** 작업 전 `phase-state.json`을 확인하고 별도 브랜치에서 작은 typed 변경, 관련 테스트, diff self-review, Draft PR 보고를 사용한다. 강화 검증 대상은 역할 분리 safety QA를 추가한다.
9. **Definition of Done:** 일반 변경은 구현·관련 테스트·자체 검토·Draft PR 보고가 완료 조건이다. 금지 capability, 비밀 노출, 원장 불균형, 미검증 안전 변경은 언제나 완료가 아니다.
10. **품질 명령:** 루트 `pnpm test:core`, `pnpm test:safety`, `pnpm test:integration`, `pnpm test:e2e`, `pnpm ci`를 정본으로 사용한다. Property·Replay·Failure 테스트는 Core 또는 Safety에 포함한다. 일반 CI는 Phase Manifest나 acceptance artifact digest가 오래되었다는 이유만으로 실패시키지 않는다.

**Codex 어댑터:** 전문 에이전트와 `_workspace/` 리뷰 증거는 강화 검증 대상 또는 사용자가 명시적으로 요청한 작업에만 사용한다. 일반 Paper MVP 유지보수에는 필수가 아니다. 사용할 때는 오케스트레이터만 산출물을 영속화하며 동시 실행은 기본 3개를 넘지 않는다.

**리뷰 정책:** 일반 변경은 diff 자체 검토로 충분하다. 강화 검증 대상은 역할 분리 safety QA를 수행하고 필요할 때 Codex 내부 교차검토를 보조로 사용한다. 동일 엔진 검토는 외부 독립 리뷰가 아니며 safety QA를 대체하지 않는다. 외부 리뷰는 Phase 전환, 거래소 주문 경계, 보안·금융 중대 변경 또는 사용자의 명시 요청에만 사용한다.

**변경 이력:**

| 날짜 | 변경 내용 | 대상 | 사유 |
|------|-----------|------|------|
| 2026-07-22 | Phase 8 일시 중단, Paper MVP 일반 개발·테스트·리뷰 절차 단순화 | 하네스·CI | 실제 제품 확인과 개발 속도를 높이되 금융·거래 안전 게이트는 유지 |
| 2026-07-19 | Claude 일반 검토를 제거하고 Codex 전용 내부 교차검토 역할·외부 리뷰 분리 정책 추가 | 하네스 리뷰 경로 | 같은 엔진 한계를 숨기지 않으면서 Codex 중심 검토를 표준화 |
| 2026-07-19 | 외부 대조 리뷰 후 산출물 규약·Phase 0 테스트 경계·P0 추적·분리형 갱신 어댑터 보강 | 하네스 정책 | 재실행 모호성, 단계 월경과 잘못된 factory 파일 주입 방지 |
| 2026-07-19 | 초기 듀얼 런타임 하네스 포인터 등록 | 전체 | Phase 0 안전한 개발 준비 |
