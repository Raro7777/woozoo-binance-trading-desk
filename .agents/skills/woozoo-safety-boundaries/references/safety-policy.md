# Woozoo Release 1 안전 정책

## Capability 매트릭스

| Capability | Release 1 | 조건 |
|------------|-----------|------|
| Binance Spot 공개 시장 데이터 | 허용 | 무인증 GET/공개 WebSocket allowlist, 데이터 품질 검사 |
| AI 분석·토론·TradeProposal | 허용 | Evidence 전용 입력, 주문 도구와 비밀 없음 |
| 내부 Paper Broker | Phase 4부터 허용 | 결정론적 코드, 불변식·원장·멱등성 테스트 |
| Binance Spot Testnet 주문 | Phase 8부터 조건부 | 별도 gateway, 기본 OFF, 사람 승인, 대조 |
| Mainnet 비공개 주문·계좌 | 금지 | 설계·구성·코드·도구에서 capability 제거 |
| 출금·Futures 주문·계좌·마진·레버리지·숏 | 금지 | 스키마와 UI를 포함해 거래 capability 제거 |
| 공개 파생시장 텔레메트리 | Phase 0 미결정 | 무인증 read-only 별도 allowlist, 거래·계좌 도구와 완전 분리 |
| AI의 직접 주문·정책 변경 | 금지 | Risk·Approval·Gateway와 권한 경계 분리 |

## 구조적 차단 규칙

1. 공개 시장 데이터 클라이언트와 Testnet 주문 클라이언트의 패키지·프로세스·환경 변수를 분리한다.
2. 일반 `exchange_client`처럼 public/private를 함께 제공하는 범용 인터페이스를 만들지 않는다.
3. 거래소 요청은 capability allowlist를 통과해야 하며 문자열 URL 조합으로 우회하지 못하게 한다.
4. Mainnet host 자체는 공개 데이터에 필요할 수 있으므로 host 차단만으로 안전을 주장하지 않는다. method, path, 인증 요구, capability를 함께 검사한다.
5. Phase 8 전에는 Testnet gateway의 코드·주문 schema·DI binding·설정 capability 자체를 만들지 않는다. Phase 8에서 처음 추가해도 `enabled=false`가 기본이다.
6. Testnet 주문 승인 레코드는 proposal hash, risk decision hash, risk policy version, Testnet account/environment, symbol, side, quantity, price/rule, time-in-force의 정확한 preview, 일회성 nonce, 인증된 approver, 승인·만료시각과 revocation 상태를 포함한다. 내용 변경·사용·만료·철회는 기존 승인을 무효화한다.
7. Kill Switch와 대조 실패는 신규 주문을 fail-closed로 막고 운영자가 원인을 확인하기 전 자동 해제하지 않는다.
8. 승인 nonce는 단일 command ID와 Client Order ID에 결속하거나 그 식별자를 결정적으로 파생한다. 하나의 승인으로 둘 이상의 create 명령을 만들 수 없다.

## 네트워크 경계

- market collector egress는 승인된 무인증 public data allowlist만 허용한다. Testnet private와 주문 경로는 차단한다.
- AI service egress는 승인된 model provider와 필요한 read-only 내부 API만 허용한다. Binance private/Testnet gateway에 직접 접근하지 못한다.
- Phase 8 Testnet Gateway egress는 승인된 Spot Testnet capability만 허용하고 Mainnet private·출금·Futures 경로를 차단한다.
- Gateway inbound는 인증된 결정론적 execution service의 승인된 command schema만 받는다. 브라우저와 AI service의 직접 호출을 받지 않는다.

## 비밀 경계

- 웹: 공개 상태와 승인 UI만. Secret, 서명문, 원시 인증 헤더 금지.
- AI: Evidence와 요약 포트폴리오만. 거래소 Secret과 주문·취소·계좌 도구는 금지한다. 모델 제공자 credential은 별도 최소 권한 secret path로만 주입하고 prompt·tool registry·로그에 넣지 않는다.
- Risk Engine: Proposal, 정책, 포트폴리오, 데이터 상태. 거래소 인증 금지.
- Testnet Gateway: Phase 8에서 Testnet trade key만. 출금 권한과 Mainnet private URL 금지.

## Prompt Injection 방어

- 외부 콘텐츠를 인용된 데이터 필드에 넣고 시스템·도구 지시로 해석하지 않는다.
- 에이전트 도구는 명시적 allowlist로 구성한다.
- evidence ID와 신뢰 가능한 provenance가 없는 주장은 최종 Proposal 근거에서 제외한다.
- 모델 응답 실패, 스키마 오류, 필수 보고서 누락의 기본 결정은 HOLD다.
