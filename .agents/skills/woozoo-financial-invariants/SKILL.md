---
name: woozoo-financial-invariants
description: "Woozoo의 가격·수량·잔고·수수료·PnL·노출·Risk Engine·Paper Broker·주문 상태·복식 원장·멱등성·reconciliation을 설계, 구현, 수정 또는 검토할 때 금융 불변조건을 강제한다. float 계산, 음수 잔고, 중복 체결·중복 원장, 타임아웃 즉시 재주문을 막기 위해 관련 작업이면 반드시 사용한다."
---

# Woozoo Financial Invariants

LLM 출력이나 UI 값을 금융 상태의 권위로 삼지 않는다. 금액과 주문 상태는 결정론적 도메인 코드, 원자적 저장, 재현 가능한 이벤트로 계산한다.

## 시작 절차

1. 현재 Phase를 확인한다. Phase 0에서는 계약과 테스트 시나리오를 문서로만 정의한다.
2. 변경이 건드리는 상태를 order, fill, position, cash, fee, PnL, exposure, ledger, approval, reconciliation으로 분류한다.
3. `references/invariant-catalog.md`에서 관련 불변조건과 실패 테스트를 선택한다.
4. 코드가 허용된 Phase에서는 실패 테스트를 먼저 만들고 최소 구현 후 property·replay·failure-injection으로 검증한다. Phase 0에서는 같은 내용을 실행 코드가 아닌 RED 테스트 명세로 기록한다.

## 계산 규칙

- 가격, 수량, 잔고, 수수료, 원가, PnL, 노출은 Decimal 의미론을 사용하고 API/event에서는 문자열로 직렬화한다.
- float에서 Decimal로 사후 변환해 오차를 숨기지 않는다. 입력 경계에서 문자열 또는 정수 스케일을 사용한다.
- 자산·현금·수수료의 단위와 반올림 정책을 명시하고 거래소 필터와 독립적으로 버전 관리한다.
- Risk Decision은 동일 입력·정책 버전에서 동일 결과를 내며 LLM이 수치나 최종 허용을 확정하지 않는다.

## 상태·멱등성 규칙

- 각 명령은 Idempotency Key, 각 외부 주문은 고유 Client Order ID, 각 이벤트는 중복 판별 키를 가진다.
- 로컬 권위 상태에서는 inbox 중복 기록, 명령 수락, 도메인 상태, 원장 기록, outbox 이벤트를 동일 DB 트랜잭션으로 묶고 unique constraint로 재처리를 차단한다.
- 타임아웃은 결과 불명 상태다. 새 주문을 보내지 말고 동일 Client Order ID로 조회·대조한다.
- 부분 체결 합은 주문 수량을 초과하지 않고, 취소와 후속 체결의 순서를 이벤트 원장으로 재현한다.
- 이벤트 재처리와 replay는 상태·원장·포트폴리오를 중복 반영하지 않는다.

## 원장 규칙

- 모든 원장 거래는 asset/currency commodity별 차변·대변 합계가 0이고 수정 대신 반대 분개를 추가한다. BTC와 USDT 같은 서로 다른 단위를 원시 수량으로 합산하지 않는다.
- 가치통화 보고가 필요하면 명시적 valuation posting·환율 출처·시각·계정을 사용한다. cost-basis와 실현/미실현 PnL 방식은 Phase 0 계약으로 먼저 결정한다.
- 허용되지 않은 음수 현금·자산 잔고를 만들지 않는다.
- 원장은 주문·체결 상태에서 파생 가능하고, 원장과 포트폴리오 snapshot은 대조 가능해야 한다.
- 설명되지 않는 불균형이나 reconciliation failure는 신규 주문을 차단한다.

## 검증 결과

각 불변조건마다 이름, 적용 경계, 최소 반례, RED 테스트, 구현 위치, 재실행 결과를 기록한다. 샘플 예시만 통과시키지 말고 생성형 입력과 이벤트 순서 변형을 사용한다.

## 테스트 시나리오

- 정상: 하나의 지정가 주문이 세 번 부분 체결되고 수수료가 각각 부과되어도 fill 합, cash, asset, 원장이 정확히 일치한다.
- 중복: 동일 fill 이벤트를 두 번 처리해도 주문·포지션·원장 결과가 한 번 처리한 것과 같다.
- 타임아웃: 제출 응답이 끊겨도 같은 Client Order ID를 조회하며 새 ID로 재주문하지 않는다.
