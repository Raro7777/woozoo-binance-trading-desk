# Phase 7 MVP 최종 구현 검토

## 현재 판정

동결 구현 후보 `8bfc8850b26de83d0162f84c2e03b43de827bae4`는 정본 CI와
43행 acceptance denominator를 통과했다. 이 문서는 정확한 후보에 대한 역할 분리 Safety QA와
동일 엔진 Codex 교차검토를 실행하기 위한 사전 동결 기록이며, 두 검토 결과는 아직
`REVIEW_PENDING`이다. 검토가 끝나기 전에는 MVP-01~12 최종 PASS 또는 인수 가능 상태를
선언하지 않는다.

Phase 7은 아직 사용자에게 인수되지 않았다. 정확한 Phase 7 acceptance evidence manifest의
SHA-256이 생성되고 사용자가 그 digest를 명시적으로 승인하기 전에는 Phase 8로 전환할 수 없다.

## 동결 증거

- commit: `8bfc8850b26de83d0162f84c2e03b43de827bae4`
- tree: `9b12ed46236552516361682ac3222a5682b5ff08`
- worktree digest: `4d0b6c320d11f0fb03cb916564b3efe7307b4eae3dfde6a0c935e6cc0bdddcad`
- frozen files: 399
- canonical command: `corepack pnpm ci`, PASS, exit 0
- P7-43: 43/43 PASS, 1,077 frozen test executions
- scenario manifest SHA-256: `eaba0684139d85ab1a1ff73dee7c07286c55f7f232e32046a3c200a8f255a588`
- aggregate output digest: `ce56ddad1caad763368ca0719a84729b63c70e2a0edf360ae63f142f4b93888e`
- P7-43 artifact SHA-256: `e2863c1d06d5c770b1630d559b90cff0d53d3ad13751d3474cd433e263d8e4eb`

## 검토 게이트

| 검토 | 현재 결과 | 의미 |
|---|---|---|
| 역할 분리 Safety QA | `REVIEW_PENDING` | 이 동결 후보를 새 컨텍스트에서 검토해야 한다. Phase 전환 승인이 아님. |
| Codex 내부 교차검토 | `REVIEW_PENDING` | 동일 엔진·분리 컨텍스트 검토 대기. 외부 독립성이 아님. |
| 외부 agy/Gemini | `external-review-unavailable` | 허용 경로의 기존 2회 시도가 사용 가능한 리뷰를 만들지 못했다. 외부 PASS가 아님. |

## 종결 대상으로 포함한 중대 결함

- Kill completion/recovery는 발급된 모든 Paper authorization이 terminal BLOCKED가 되기 전
  completion을 만들 수 없고, 마지막 BLOCKED effect와 completion digest를 같은 account-lock
  transaction에 기록한다.
- rejected receipt hydration은 namespace별 `test:error_code` 또는 `paper:reason_code`를 정확히
  하나만 허용하고, receipt와 first attempt의 `request_hash` 및 terminal `BLOCKED` outcome을
  재검증한다.
- 브라우저 Risk 분석 직전 Evidence freshness를 다시 고정해 오래된 fixture 시간이 Proposal로
  진행되지 않도록 한다.
- command guard와 logout은 동일 session lock/DB row lock 안에서 CSRF 소비, idle touch 또는
  revoke를 원자적으로 직렬화한다. 실패 시 CSRF와 session 변경을 함께 rollback한다.

## 제품 경계

후보는 로컬 단일 운영자·Paper-only다. Binance Spot 공개 데이터만 사용하며 Binance private,
Testnet gateway, live order, 출금, futures, margin, leverage, short, credential, AI/browser 직접
주문 capability가 없다. `TRADING_MODE=paper`가 필수이고 누락·미지 값은 fail-closed다.
