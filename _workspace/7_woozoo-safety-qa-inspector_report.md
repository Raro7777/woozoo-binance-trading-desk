# Phase 7 역할 분리 Safety QA 보고서

- 판정: **PASS**
- 심각도: **NONE**
- 차단 여부: **비차단**
- 후보: `c0baf4cedd56818da0cc9d5718950b15eaf48b2b`
- 트리: `6f66d2f761a689ab4ed98924b1485f78f7c25478`
- 기준: `main@03369ceebc84da904bf6cab6818154f96670dc43`
- Phase 상태: Phase 7 `active`, 승인 `not_requested`
- 미해결 안전·금융 지적: **0건**
- 검토 성격: 역할 분리 내부 Safety QA이며 외부 독립 리뷰나 사용자 인수 승인이 아님

## 검증 결과

### 1. Supervisor의 치명적 품질 저장 실패 전파 — PASS

- `MarketDataSupervisor`는 queue overflow 중 `mark_global_failure`가 낸 `QualityPersistenceError`를 일반 disconnect로 강등하지 않는다.
- consumer는 모든 dequeue 전과 종료 직전에 fatal error를 검사하고, 오류 검사와 다음 `queue.take()` 사이에는 await가 없다.
- 오류는 runner 밖으로 전파되어 다음 collector session, reconnect generation, queue drain이 실행되지 않는다.
- 10,001개 메시지 반례에서 오류 전파, `quality_append_failed`, 정규화 이벤트 0건, transport 1회, session `s1`만 발급됨을 확인했다.
- 근거: `services/market-data-worker/src/market_data_worker/supervisor.py:184-233`, `runner.py:37-45`, `pipeline.py:204-223`.

### 2. 공개 데이터·raw·현재 시세 권위 — PASS

- normalized `SHARE` fence가 동시 insert를 직렬화하고, watermark → market → collector 순서로 권위 잠금을 취득한다.
- raw SHA-256, stream, symbol, sequence, payload와 normalized 값을 재계산해 대조한다.
- `event_time DESC, received_at DESC, id DESC` 기준 최신 book ID를 잠금 뒤 재검사한다.
- `as_of`, `knowledge_cutoff`, `event_time`, `received_at` 미래 오염과 freshness 실패는 fail-closed된다.
- 검증자가 이전 B1을 고른 뒤 B2가 커밋되는 경합은 B1을 거절하고 주문·체결·원장 효과 0건으로 끝났다.
- 근거: `db/migrations/versions/20260720_0007_trading_room.py:1386-1565`, Evidence/Paper persistence verifier.

### 3. Proposal → Risk → Approval → Authorization — PASS

- AI provider는 고정 mock만 허용하고 `tool_allowlist=[]`이다. tool call, malformed output, Evidence/hash/time 불일치는 HOLD다.
- Risk는 입력에서 재평가하며 위조 verdict/hash를 거부한다.
- 승인 함수는 최신 `ALLOWED` Risk, Proposal/preview hash, Kill, reconciliation, worker를 같은 transaction에서 재검증한다.
- 모든 잠금 뒤 DB 시간을 한 번 샘플링하여 Risk TTL·worker freshness·승인·authorization 시간을 통일한다.
- 승인 nonce와 authorization nonce가 분리되고 authorization은 일회성이다.
- 최신 Risk를 `DENIED`로 바꾸거나 worker heartbeat를 만료한 반례는 승인·authorization·receipt·outbox 효과 0건이었다.

### 4. Paper 첫 시도·체결·원장·멱등성 — PASS

- 첫 시도 전에 authorization/Risk/Kill/reconciliation/Evidence/book/ledger 권위를 재검증한다.
- 첫 시도는 주문 생성 또는 terminal `BLOCKED`이고, 같은 idempotency key는 기존 receipt를 재생하며 다른 hash는 conflict다.
- 가격·수량·fee·hold는 Decimal이다.
- order, balance, fill, lot, commodity별 복식 journal, outbox는 한 Postgres transaction에서 처리된다.
- BUY/SELL 음수 잔고와 보유량 초과를 차단하고 취소 hold는 동일 commodity로 반환한다.
- 재시도·partial-fill replay·failure injection에서 중복 효과 0건과 commodity별 균형을 확인했다.

### 5. Kill Switch·reconciliation·복구 — PASS

- Kill 활성 중 첫 시도는 terminal `BLOCKED`이며 신규 주문이 없다.
- pending authorization이나 open order가 남으면 completion/recovery가 불가능하다.
- 복구는 exact activation event/version, 인증 actor, fresh books, healthy worker, balanced ledger, 최신 reconciliation authority sequence를 요구한다.
- timer, AI, restart, Redis 자동 복구 경로가 없다.
- TTL 잠금 경합과 completion 이전 checkpoint 반례 모두 Kill을 유지했다.

### 6. 금지 capability와 최소 권한 — PASS

- Testnet/private/signed/live/withdrawal/futures/margin/leverage/short capability가 없다.
- AI에는 Risk·approval·Paper order·계좌 테이블 권한과 주문 도구가 없다.
- browser/control은 projection과 승인 명령 함수로 제한된다.
- Paper 역할은 authorization을 발급하지 못하고 DB 발급 authorization만 소비한다.
- raw·collector·watermark·market authority 직접 접근은 verifier 경계로 제한된다.

## 재실행 결과

- supervisor·pipeline·AI/Risk/Paper/browser safety: **19 passed**
- Postgres authority·latest-book race·quality lock·fill/ledger·Kill: **10 passed**
- capability-zero: **1 passed**
- skip/todo/failure: **0**
- 제공된 정본 `corepack pnpm ci`: exit 0
- Phase 7 증거: 43/43 PASS, validator `failures=[]`

이 판정은 정확한 제품 후보에 대한 역할 분리 내부 Safety QA다. 외부 독립 리뷰, Phase 전환 또는 사용자 인수 승인을 충족하거나 대체하지 않는다.

## CI 지원 revision addendum

- 지원 commit: `dcef304d5043a51f54c7f20435206c9fb1fd171a`
- diff: `.github/workflows/ci.yml`의 `timeout-minutes: 20 → 45` 한 줄
- 판정: **PASS**
- 근거: 테스트 명령, 권한, dependency, artifact 업로드와 제품 capability가 바뀌지 않았다. 기존 20분 job 제한이 Playwright 19 PASS 뒤 build·validator 전에 두 exact-head 실행을 강제 취소한 운영 시간 한계만 교정한다.
- 효과: `c0baf4c` 제품 Safety PASS는 계속 유효하다. 이 addendum도 외부 독립 리뷰나 사용자 승인이 아니다.
