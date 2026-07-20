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
