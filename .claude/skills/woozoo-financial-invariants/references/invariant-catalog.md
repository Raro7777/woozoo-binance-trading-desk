# 금융 불변조건 카탈로그

## 산술

- 모든 금융 값은 유한한 Decimal이고 명시된 scale/rounding을 따른다.
- `filled_quantity = sum(unique fills)`이고 `0 <= filled_quantity <= order_quantity`다.
- Long/Cash 모델에서 자산 수량과 현금은 허용된 credit 정책 없이는 음수가 아니다.
- 수수료 자산과 수수료 금액은 체결별로 보존되며 PnL에서 누락되지 않는다.

## 복식 원장

- 각 transaction의 debit 합과 credit 합 차이는 commodity별로 정확히 0이다. 서로 다른 asset 수량을 합쳐 균형을 주장하지 않는다.
- 가치통화 변환은 원시 자산 분개와 분리하고 valuation source, as_of, rate와 전용 계정을 남긴다.
- ledger entry는 immutable이고 correction은 reversal + replacement로 표현한다.
- order/fill/cancel 이벤트와 ledger transaction 사이에 감사 가능한 correlation ID가 있다.
- 동일 business event가 원장 transaction을 두 번 만들지 않는다.
- inbox event key와 command idempotency key는 DB unique constraint로 보호하고 domain state·ledger·outbox와 한 transaction에 커밋한다.

## 주문 상태

- 허용된 상태 전이만 가능하고 terminal 상태를 임의로 되돌리지 않는다.
- 부분 체결 후 취소는 이미 체결된 수량과 수수료를 보존한다.
- 늦게 도착한 이벤트와 중복 이벤트를 결정적 순서 규칙으로 처리한다.
- 제출 timeout은 unknown으로 남겨 조회·대조 후 확정한다.

## 리스크·승인

- stale/missing/future-contaminated data, 만료·hash 불일치 승인, Kill Switch, 대조 실패, 한도 초과는 거절한다.
- 동일 Proposal, 포트폴리오 snapshot, 데이터 상태, 정책 버전은 동일 Risk Decision을 만든다.
- 정책 변경은 버전·변경자·시각을 기록하고 기존 승인과 결정의 재현성을 보존한다.

## 필수 테스트 계층

- Unit: Decimal, fee, PnL, exposure, 상태 전이, 만료와 hash.
- Property: 원장 균형, 자산 보존, 비음수, 중복 무효, fill 상한.
- Replay: 같은 이벤트 로그를 반복·재시작 후 재생해 같은 snapshot 생성.
- Failure injection: DB/Redis 중단, 응답 유실, 지연·중복, 프로세스 재시작.
- Safety: 금지 URL, 승인 없음, stale data, reconciliation failure에서 주문 거절.
