# P0-01·P0-03 저장소·요구사항 감사

- 상태: `P0-01 UNVERIFIED`, `P0-03 POLICY_APPROVED — final evidence pending`
- 감사일: 2026-07-19 (Asia/Seoul)
- 제품 Phase: 0
- 작업 브랜치: `codex/phase-0-design`
- 원본 정본: `Woozoo_Binance_Trading_Desk_Codex_Handoff-5.docx`
- 원본 SHA-256: `2DED3774EE98F4F808853C03A072FBBDEFB3135473711463DA9939DAA188E8A7`
- 원본 구조: body 165 paragraphs, 28 tables, 104 rows, 263 cells; header/footer 각 1 paragraph
- 원자 요구사항 정본: `requirements-trace-manifest.json` (`schema_version=1.0.0`)

이 감사는 원본 요구를 읽고 현재 저장소와 MVP 계약에 매핑한다. 문서 안의 권고값은 사용자 승인이나 Phase 전환을 대신하지 않는다.

## 1. 저장소 현황

| 항목 | 관측값 | 판정 |
|---|---|---|
| Git branch | `codex/phase-0-design` | 요구와 일치 |
| Git history | unborn branch, commit 0 | Phase 0 Git 게이트 미충족 |
| tracked files | 0 | 모든 파일이 아직 untracked |
| 원본 DOCX | 56,162 bytes, 원본 hash 위 참조 | 수정 없음 |
| phase state | `current_phase=0`, approval `not_requested`, evidence digest `null` | 다음 Phase 금지 |
| application roots | `apps/`, `services/`, `packages/`, `infra/`, `migrations/`, `tests/` 없음 | Phase 0 금지 delta 0 |
| harness | `AGENTS.md`, `CLAUDE.md`, `.agents/skills/`, `.codex/agents/` | Codex 중심 하네스 준비됨 |
| product plan | `docs/woozoo-trading-desk/mvp-plan-and-acceptance.md` | 계획안, 구현 증거 아님 |
| atomic trace | `docs/woozoo-trading-desk/phase-0/requirements-trace-manifest.json` | canonical REQ 123건, alias group 8건, target anchor 77건; 전건 non-PASS |
| trace validator | `docs/woozoo-trading-desk/phase-0/tools/validate_requirements_trace.py` | Python stdlib 전용 문서 evidence 검증기; 제품·network·credential·order capability 없음 |

현재 저장소는 제품 코드가 없는 설계 단계다. 문서·하네스 파일이 존재한다는 사실을 실행 가능한 앱, 테스트 또는 MVP 진척으로 계산하지 않는다.

## 2. 원본 요구 추적

| 원본 요구 묶음 | 원본 위치 | MVP/후속 배정 | 상태 |
|---|---|---|---|
| Binance Spot 공개 시세·체결·캔들·필요 호가 | §2, T22 Phase 2 | MVP Phase 2~3, MVP-02·03 | 계획됨 |
| BTCUSDT·ETHUSDT | §2, T07 | MVP 전체 | 결정됨 |
| Long 또는 Cash | T07 | BUY와 보유량 이하 축소 SELL만 | 결정됨 |
| 시장국면·기술·체결 흐름 | §2, T12 | MVP Phase 3·6 | 계획됨 |
| 파생시장 문맥 | §2, T12 | DP-D02: Release 1.1 이월 | 승인됨; 별도 source allowlist 전 MVP 추가 금지 |
| 뉴스·거시 | §2, T12 | DP-D02: Release 1.1 이월 | 승인됨; 별도 provenance/injection 계약 전 MVP 추가 금지 |
| Bull/Bear와 TradeProposal | §2, T12, T14 | MVP Phase 6, MVP-04·05 | 계획됨 |
| 결정론적 Risk와 Kill Switch | §2, §6 | MVP Phase 5, MVP-05·06 | 계획됨 |
| spread·예상 slippage 한도 초과 강제 거절 | 원본 문단 200, §6 | DP-D06, MVP Phase 5, MVP-05와 RISK-002 | 25 bps와 결정론 산식 승인됨 |
| 내부 Paper Broker·원장 | §2, §6, T17 | MVP Phase 4, MVP-07·08 | 계획됨 |
| Next.js GUI | §2, T22 Phase 7 | MVP Phase 7, MVP-09·10 | 계획됨 |
| Spot Testnet lifecycle·reconciliation | §2, T22 Phase 8 | MVP 이후 Testnet Validation | DP-D01 scope split 승인됨; Phase 8 자체는 별도 digest-bound 승인 필요 |
| 감사·승인·model/prompt version | §2, §4, T17 | MVP Phase 1~7, MVP-10 | 계획됨 |
| Mainnet private/live 금지 | §2, §6, 부록 A | 모든 Phase 구조적 차단 | 결정됨 |
| 출금·Futures·Margin·leverage·short 금지 | §2, 부록 A | schema/UI/capability까지 0 | 결정됨 |
| AI 직접 주문·정책 변경 금지 | §2, §8 | 서비스·도구·비밀 경계 분리 | 결정됨 |
| Phase 0 문서 전용·Draft PR | §11, 부록 B·C | Phase 0 Git gate | 아직 미충족 |

위 요약표는 탐색용이며 요구사항 완전성 또는 orphan 0건의 증거가 아니다. 원자 추적의 정본은 `requirements-trace-manifest.json`이다. 정본은 각 canonical requirement에 안정적인 `REQ-*` ID, 정확한 source anchor와 정규화 원문 SHA-256, 원자 문장, disposition, MVP/P0/Phase/scenario target, non-PASS 상태를 기록한다. 부록 B 프롬프트, 부록 C 체크리스트, 온보딩과 반복 상세 지시는 새 요구사항으로 중복 계수하지 않고 canonical REQ의 alias로 분류한다.

manifest의 deterministic classification 결과에서 requirement candidate의 미분류 source anchor는 0건이다. 모든 DOCX canonical/alias anchor는 paragraph 또는 table row/cell projection으로 재구성하며, 복합 agent role anchor는 `DOCX:T13.R02-R11[C01,C02]`처럼 포함 column을 명시한다. MVP/P0/PHASE/scenario target 77건은 target catalog의 unique Markdown selector와 normalized anchor digest로 색인되고 각 requirement의 target binding에 결속된다. validator는 후보가 제공한 target 목록을 신뢰하지 않고 두 target catalog에서 77건 전체를 매번 다시 도출해 type/path/selector/digest를 포함한 exact equality를 검사한다. Catalog 자체와 candidate가 함께 축소되는 공격도 막기 위해 77건 전체 `ID + type + path + selector + normalized anchor digest`를 candidate와 catalog 밖의 reviewed target fingerprint `d7860d5479262f22d8fc47feb6f510c862986527e9577ed9b972cec654493f51`에 추가 결속한다.

Canonical/alias 분류는 자연어 원본만으로 기계적으로 다시 판단하면 의미 분류 규칙 자체가 바뀔 수 있으므로 candidate 밖 validator의 **명시적으로 검토된 고정 inventory authority**에 결속한다. Canonical authority는 123건의 `ID + exact source anchor + source digest + atomic statement` fingerprint `7667f7a6439f1a246ad1363a501711403e42ef43c666fed7c8893e4af5575cbd`, alias authority는 8건의 `alias anchor + canonical mapping + source digest` fingerprint `03ec0558ad664b6b4e5b6f8d55c62e3886be0f86ffa836263829ead7485954b1`이다. 이 값 변경은 원본 재감사와 validator 변경을 함께 요구하므로 candidate가 자체 summary와 inventory를 같이 줄여도 통과할 수 없다. 별도로 reviewed policy-resolution authority는 P0-POLICY-01과 `REQ-AI-007`, `REQ-RISK-001~006`, `REQ-SCOPE-007~008`, `REQ-SEC-006`의 exact DP-D03/DP-D02/DP-D06 mapping 및 validator에 독립 고정된 approval/decision file SHA-256을 검증한다. 따라서 candidate와 policy 문서를 함께 고쳐 hash를 맞추는 공격도 fail-closed다. 이는 **source classification과 policy delegation 결과**일 뿐 P0-01/P0-03 PASS, 구현 완료, Git gate 충족 또는 Phase 전환 근거가 아니다. 동결된 파일 hash, final safety/package review와 Git/PR evidence가 남아 있으므로 모든 acceptance 판정은 계속 `UNVERIFIED`다.

재현 명령:

```powershell
python docs/woozoo-trading-desk/phase-0/tools/validate_requirements_trace.py
python docs/woozoo-trading-desk/phase-0/tools/validate_requirements_trace.py --mutation-tests
python docs/woozoo-trading-desk/phase-0/tools/validate_requirements_trace.py --refresh-derived
```

검증기는 DOCX ZIP/XML을 Python 표준 라이브러리로 직접 읽고 source projection/text/digest, source·target catalog hash, target selector unique match/digest, requirement ID·anchor·status·disposition, alias binding, target binding과 저장된 validation summary를 독립 재계산한다. `--refresh-derived`는 canonical 파일을 먼저 쓰지 않는다. 메모리 candidate를 완성하고 검증한 뒤 같은 디렉터리의 임시 JSON을 재파싱·재검증하고 `os.replace`로만 원자 교체한다. refresh 또는 validation 실패 시 nonzero와 통제된 오류를 반환하며 기존 manifest는 그대로 둔다.

mutation suite는 임시 JSON 복사본에서 `PHASE-2→PHASE-9`, T13 C03 혼입, T13 C02 누락, `E2E-005` target 전체 삭제, 승인된 `REQ-AI-007` policy-resolution 삭제, alias 미참조 `REQ-SCOPE-001` 삭제, `DOCX:T28.R01.C02` alias 삭제가 모두 실패하는지 확인한다. 여덟 번째 변이는 disposable temp repository에서 `verification-strategy.md`의 `E2E-005` row와 candidate의 모든 관련 ID·binding·summary를 함께 축소한 뒤 refresh가 reviewed target authority로 실패하는지 확인한다. 아홉 번째 변이는 candidate의 decision hash와 policy 문서를 함께 바꿔도 validator의 독립 policy authority가 거절하는지 확인한다. 축소 변이는 candidate 내부 count·coverage·summary도 함께 재계산해 자기일관적으로 만들므로 외부 authority 검사를 직접 검증한다. 추가로 두 refresh negative control은 DOCX inventory 불일치와 refresh candidate validation 실패가 traceback 없이 nonzero로 종료하고 임시 candidate와 canonical manifest digest를 모두 보존하는지 확인한다. 총 negative control은 11건(변이 9 + refresh fail-closed 2)이다.

## 3. 확정된 안전·제품 원칙

1. 기본 실행 모드는 명시적 `TRADING_MODE=paper`다. 누락·알 수 없는 값은 시작 실패다.
2. MVP는 로컬 단일 운영자의 내부 Paper Trading 폐루프다.
3. MVP의 외부 exchange credential, signed/private request, user-data stream, 주문 gateway는 0개다.
4. SELL은 보유량 이하의 Long 축소·청산만 허용한다. 음수 position과 short는 schema와 상태 전이에서 표현할 수 없어야 한다.
5. AI는 Evidence 기반 보고서와 TradeProposal만 만든다. Risk, 승인 유효성, 수량·잔고·원장·체결은 결정론적 코드가 담당한다.
6. 데이터가 stale/invalid이거나 Evidence가 누락·미래오염되면 기본 결과는 HOLD/거절이다.
7. 금융 값은 Decimal string으로 직렬화하고, ledger는 commodity별 균형·immutable entry·reversal+replacement를 사용한다.
8. Kill Switch는 신규 Paper 명령을 막고 열린 Paper 주문을 감사 가능한 취소로 종료하며 자동 해제하지 않는다.
9. Phase 1~7 종료 때 Testnet과 private capability 0을 매번 검사한다.
10. Phase 8은 별도 승인과 별도 package/process/env의 Spot Testnet 확장으로만 시작한다.

## 4. 모순·누락·위험한 가정

### 4.1 사용자 결정으로 해소된 범위 모순

| ID | 문제 | 승인된 판정 | 결속 기록 |
|---|---|---|---|
| AUD-SCOPE-01 | 원본은 Testnet을 Release 1에 포함하지만 credential·불명확 주문 상태가 Paper MVP와 다른 위험 등급이다. | Release 1을 `1a=MVP Phase 0~7`, `Testnet Validation=Phase 8`, `Readiness=Phase 9`로 분리 | `phase-0-approval-record.md` P0-POLICY-01 |
| AUD-SCOPE-02 | 원본은 파생시장 문맥을 요구하지만 공식 source, contract, retention, prompt-injection 경계가 없다. | MVP 제외, Release 1.1에서 무인증 read-only allowlist를 별도 승인 | `phase-0-approval-record.md` P0-POLICY-01 |
| AUD-SCOPE-03 | 원본은 뉴스·거시 분석을 요구하지만 source license, 관측시각, 수정 기사, 신뢰도, injection 경계가 없다. | MVP 제외, Release 1.1에서 source·provenance 계약부터 설계 | `phase-0-approval-record.md` P0-POLICY-01 |

### 4.2 설계에서 닫아야 하는 누락

| ID | 원본 누락 | Phase 0에서 고정할 계약 |
|---|---|---|
| G-01 | `as_of`는 있으나 재생 시 지식 컷오프 소유자와 clock이 불명확 | `knowledge_cutoff`, simulation clock, wall-clock fallback 금지 |
| G-02 | market-data source schema·watermark·raw append 실패 의미가 불명확 | raw/normalized schema, quality state, invalid/HOLD 전이 |
| G-03 | idempotency가 상태·원장·outbox까지 원자적인지 불명확 | inbox/domain/ledger/outbox 단일 DB transaction+unique key |
| G-04 | ledger의 commodity와 correction 방식이 불명확 | commodity별 clearing/exchange account, immutable entry, reversal+replacement |
| G-05 | fee asset, cost basis, valuation, PnL oracle이 불명확 | FIFO 권고, fee posting, base/quote valuation, rounding oracle |
| G-06 | Paper fill model이 거래소 matching을 과장할 수 있음 | 보수적 deterministic fill model과 명시적 limitation |
| G-07 | Risk 후보값의 window·분모·우선순위가 불명확 | versioned policy schema와 강제 거절 precedence |
| G-08 | Paper 승인과 Testnet 승인이 혼동될 수 있음 | `PaperExecutionAuthorization`과 Phase 8 approval record 분리 |
| G-09 | provider credential 비밀 경계가 원본에는 일반적 수준 | provider adapter 전용 최소 권한 secret path, UI/prompt/tool/log 금지 |
| G-10 | 완료율 100%의 분모가 바뀔 수 있음 | 고정 scenario manifest와 누락 시 실패 규칙 |
| G-11 | 보존기간·삭제·감사 보존 예외가 없음 | 데이터 등급별 retention과 audit/legal-hold 절차 |
| G-12 | 단일 운영자 actor 인증·승인 revocation과 authorization 소비 상태가 불명확 | actor identity, approval TTL/revocation, authorization first-attempt consumed/blocked state, hash binding |

### 4.3 금지해야 하는 위험한 가정

- production public host가 있으므로 private 주문도 나중에 쉽게 켤 수 있다는 가정.
- Testnet이면 credential·주문·대조 실패가 안전하다는 가정.
- WebSocket event time만으로 완전성과 최신성을 증명할 수 있다는 가정.
- LLM confidence가 확률적으로 보정되어 있거나 주문 허용 근거가 된다는 가정.
- Paper PnL이 수익성·실거래 성능을 증명한다는 가정.
- 중복 message를 무시하면 상태·원장·outbox도 자동으로 exactly-once가 된다는 가정.
- ledger balance 0만으로 경제적 정확성까지 증명된다는 가정.
- Mainnet hostname 차단 하나로 private capability가 제거된다는 가정.
- 문서에 테스트 이름이 있으면 실행 증거가 생긴다는 가정.

## 5. 사용자 승인 권고안

P0-03의 세 묶음은 `phase-0-approval-record.md`로 승인됐다.

### A. MVP 컷

- 채택: MVP는 Phase 0~7의 내부 Paper 폐루프까지다.
- Phase 8 Spot Testnet과 Phase 9 장기 운영은 후속 gate로 유지한다.

### B. 데이터 소스 컷

- 채택: 파생시장 telemetry와 뉴스·소셜 collector는 MVP에서 제외하고 Release 1.1로 이월한다.
- placeholder service, 빈 schema, 비활성 credential 입력도 Phase 1~7에는 만들지 않는다.

### C. 초기 결정 묶음

- 채택: Long/Cash, LIMIT only, 보유량 이하 SELL, BTCUSDT/ETHUSDT, 1m/5m/1h/4h, 단일 운영자, Paper approval을 고정한다.
- 세부 수치(freshness, queue, retention, fill, fee, FIFO, Risk window/limit, TTL)는 DP-D04~DP-D10에 결속됐다. SLO는 측정 기준선 뒤 별도 운영 승인이다.
- Risk 값은 수익성 보장이 아니라 versioned fail-closed 정책의 초기 운영값이다.

정책 승인만으로 P0-03 `PASS`를 주장하지 않는다. 최종 package review와 evidence digest가 남아 있다.

## 6. P0-01/P0-03 판정

### P0-01 — 저장소와 원본 전체 감사

현재 `UNVERIFIED`이며 다음 조건을 모두 만족해야 판정할 수 있다.

- 원본 hash·구조·핵심 표와 모든 요구 묶음을 확인했다.
- 저장소 branch, history, tracked/untracked, phase state와 금지 app delta를 확인했다.
- 원본 요구를 canonical REQ와 alias로 원자 분해하고 MVP/후속/승인 변경으로 추적했다.
- `requirements-trace-manifest.json`의 JSON·ID·anchor·digest·target validation이 재현 가능해야 한다.
- 정상본 validator와 11개 negative control(원본·target·catalog·policy-resolution 변이 9건, refresh fail-closed 2건)이 모두 기대 결과를 내야 한다.
- 최종 evidence 목록에 이 파일 SHA-256이 결속된다.
- 동결 package의 safety QA, 교차검토, 외부 review 또는 정당한 unavailable 기록과 Git gate evidence가 결속된다.

### P0-03 — 모순·누락·위험 가정과 scope 결정

현재 `POLICY_APPROVED / EVIDENCE_PENDING`:

- AUD-SCOPE-01~03 및 DP-D04~DP-D10 세부 정책은 `phase-0-approval-record.md`에 결속됐다.
- 이 파일과 MVP 계약을 승인값으로 갱신했고, 새 hash로 final review와 evidence manifest를 재검증해야 한다.

## 7. 다음 단계 참조

- 미해결: 동결 package의 final review, P0 verdict/SHA manifest, acceptance digest와 Git evidence.
- 추적 정본: `requirements-trace-manifest.json`; source classification 미분류 anchor 0건이더라도 acceptance는 `UNVERIFIED`다.
- 핵심 결정: 현재 앱 구현은 0이며, 계획 문서는 실행·테스트 evidence를 대신하지 않는다.
- 다음 단계: 아키텍처·ERD·API/event·금융·테스트·CI 설계가 G-01~G-12를 각각 닫아야 한다.
- Phase 전환: P0-01~12와 Git gate가 모두 PASS이고 정렬된 evidence digest가 생성되기 전에는 요청하지 않는다.
