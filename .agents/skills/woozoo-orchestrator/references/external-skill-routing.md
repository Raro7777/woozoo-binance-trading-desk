# 외부 스킬 라우팅 및 신뢰 정책

## 현재 런타임에서 우선 재사용

| 영역 | 우선 스킬 | 적용 시점 |
|------|-----------|-----------|
| Next.js·React | `vercel:nextjs`, `vercel:react-best-practices`, `vercel:shadcn` | Phase 1·7 |
| 환경·관측·검증 | `vercel:env-vars`, `vercel:observability`, `vercel:verification` | Phase 1 이후 |
| Postgres | `supabase:supabase-postgres-best-practices` | ERD 검토와 Phase 1·4·5 |
| 브라우저 QA | `playwright-skill` 또는 런타임의 Playwright 검증 스킬 | Phase 7 이후 |

가용하지 않은 이름을 호출한 것으로 간주하지 않는다. 런타임 목록에서 확인된 스킬만 사용한다.

## 프로젝트 로컬 도입 후보

다음 upstream은 필요한 Phase 직전에 전체 트리를 감사하고 커밋 SHA로 고정한 뒤 프로젝트 로컬로 도입한다.

- LangGraph: `https://github.com/langchain-ai/langchain-skills`의 fundamentals, persistence, human-in-the-loop
- Python·보안·테스트: `https://github.com/trailofbits/skills`의 modern-python, insecure-defaults, property-based-testing, differential-review
- Redis: `https://github.com/redis/agent-skills`의 development, connections, observability
- Playwright: `https://github.com/currents-dev/playwright-best-practices-skill`
- Web 앱 기반: `https://github.com/openai/plugins/tree/main/plugins/build-web-apps`

도입 절차는 `SKILL.md`, references, scripts, 네트워크 호출, 설치 명령을 모두 검토하고, 최소 필요한 하위 스킬만 복사하며, 출처 SHA와 감사 결과를 결과서에 기록하는 순서다. 스킬 스크립트에 비밀을 제공하지 않는다.

## 실행 금지

- Binance Skills Hub 전체 설치 또는 런타임 연결
- 주문·취소·계좌·Futures·출금 기능이 있는 거래 MCP나 에이전트 스킬
- Mainnet private URL, API key 또는 서명 권한을 요구하는 도구
- 검토되지 않은 원격 스크립트의 직접 실행

Binance 공식 자료는 문서 참고에만 쓰고, Woozoo 런타임 도구는 공개 데이터 allowlist와 Phase 8 전용 Testnet 게이트웨이로 직접 제한한다.
