# P0-11 로컬 개발·CI 명령 계약

- 상태: `POLICY_APPROVED — Phase 1에서 명령을 구현하고 RED/GREEN evidence를 만들기 전에는 실행 완료로 해석하지 않는다.`
- 대상: Phase 1~7 Paper MVP
- 기본 환경: Windows 11 + PowerShell 7 또는 Linux, Docker Engine/Compose
- 비밀 없는 기본 실행: `TRADING_MODE=paper`, Mock LLM, 공개 market data만

DP-D10 승인으로 Chromium 최신 안정판에서 desktop `1440px`·mobile `360px` E2E를 실행한다. serious/critical accessibility violation은 0이어야 하며 keyboard만으로 Kill, Paper approval, cancel 핵심 경로를 완주해야 한다. Python/Node/pnpm/Chromium의 exact version은 Phase 1 lockfile/evidence에 pin하며 현재 문서의 지원 범위는 그 pin을 대신하지 않는다.

## 1. Toolchain과 lock 원칙

| 영역 | 정본 | Phase 1 산출물 |
|---|---|---|
| Python | `uv` workspace, exact Python version | `pyproject.toml`, `.python-version`, `uv.lock` |
| TypeScript | `pnpm` workspace, exact Node/pnpm version | `package.json#packageManager`, `.node-version`, `pnpm-workspace.yaml`, `pnpm-lock.yaml` |
| Services | Docker Compose | `compose.yaml`, image digest 또는 pinned tag |
| API/event | source schema + generated/validated bindings | `packages/contracts/`와 compatibility check |
| DB | reversible migration tool | migration graph와 upgrade/downgrade smoke |

Phase 1 branch 시작 시 공식 지원 상태와 로컬 런타임을 다시 확인해 exact versions를 pin한다. floating `latest`, lockfile 없는 설치, CI와 로컬의 다른 package manager는 금지한다.

## 2. 정본 명령

아래 명령 이름은 Phase 1에서 root script로 구현한다. 운영자는 내부 도구의 긴 조합을 외우지 않고 같은 진입점을 로컬과 CI에서 사용한다.

| 명령 | 계약 | 실패 조건 |
|---|---|---|
| `pnpm bootstrap` | Python/TS locked dependency 설치와 generated contract 준비 | lock drift, network 없이 lock 해석 불가, generated diff |
| `pnpm env:init` | 비밀 없는 `.env.example`을 검증한 뒤 OS 중립적으로 `.env.local` 생성. `--check`는 파일을 쓰지 않고 schema·overwrite·금지 key 규칙만 검증 | 기존 파일 덮어쓰기 필요, 금지 key 포함 |
| `pnpm lint` | Ruff lint+format check와 ESLint | warning/error 또는 파일 변경 필요 |
| `pnpm typecheck` | mypy strict boundary와 TypeScript no-emit | any/error suppression baseline 증가 |
| `pnpm test:unit` | Python/TS unit tests | 실패·skip budget 초과·비결정 결과 |
| `pnpm test:property` | 금융·주문·ledger property tests | seed 반례 또는 invariant 위반 |
| `pnpm test:contracts` | JSON/OpenAPI/event schema와 producer-consumer compatibility | breaking diff, unknown field policy 위반 |
| `pnpm test:replay` | 고정 manifest의 deterministic replay | digest mismatch, orphan raw/evidence ID |
| `pnpm test:failure` | DB/Redis/network/restart/duplicate/gap/time failure injection | fail-open, double effect, recovery budget 초과 |
| `pnpm test:safety` | phase capability, Mainnet/private/live, secret, AI-tool boundary | 금지 token/capability/path/DI/config 1건 이상 |
| `pnpm test:integration` | Postgres/Redis/FastAPI/workers 통합 | health/state/event contract 불일치 |
| `pnpm test:e2e` | Playwright Paper golden+negative journeys | 필수 scenario 누락·접근성 serious/critical |
| `pnpm build` | production-like Python package와 Next.js build | build warning/error, runtime config leak |
| `pnpm ci` | 해당 Phase에 존재하는 모든 필수 명령의 순서 고정 aggregator | 하나라도 실패 또는 manifest 분모 누락 |

저수준 도구의 기준 명령은 다음과 같다. Phase 1에서 root scripts가 정확히 이 의미를 호출한다.

```text
uv sync --frozen --all-packages
uv run ruff check .
uv run ruff format --check .
uv run mypy services packages tests
uv run pytest
pnpm install --frozen-lockfile
pnpm -r --if-present lint
pnpm -r --if-present typecheck
pnpm -r --if-present test
pnpm exec playwright test
docker compose config --quiet
```

현재 Phase 0에는 `services`, `packages`, `tests`, `package.json`, `pyproject.toml`이 없으므로 위 제품 명령을 실행하지 않는다. 이 목록은 P0-11 설계 증거다.

## 3. 로컬 시작 계약

Phase 1 이후 clean checkout 기준 흐름은 다음으로 고정한다.

```text
pnpm bootstrap
pnpm env:init                      # OS 중립, 비밀 없는 .env.local 생성
pnpm dev:infra
pnpm dev
pnpm health
```

`.env.example`에는 공개·로컬 값만 둔다. 최소 값은 다음과 같다.

```text
TRADING_MODE=paper
MODEL_PROVIDER=mock
DATABASE_URL=<local compose DSN>
REDIS_URL=<local compose DSN>
```

금지 값:

- Binance API key/secret, signature, private key
- Testnet gateway enabled flag 또는 URL(Phase 1~7)
- Mainnet private/order/account URL
- withdrawal, futures, margin, leverage, short capability
- 실제 model-provider credential

`TRADING_MODE` 누락, 빈 값, `live`, `testnet`, 알 수 없는 값은 startup exit non-zero다. Mock provider로 Paper MVP 전체가 실행돼야 하며 외부 model-provider 비밀은 선택적 adapter test에서만 별도 주입한다.

## 4. CI job graph

```text
policy-phase ─┬─ python-quality ─┐
              ├─ web-quality ────┼─ contract-compat ── integration ── e2e
              ├─ secret-scan ────┤
              └─ docs-evidence ──┘
                         └─ property/replay/failure/safety (Phase별 활성)
```

| Job | 항상 검사하는 것 | 외부 서비스 |
|---|---|---|
| `policy-phase` | phase-state, 허용/금지 디렉터리·capability, Testnet/private zero gate | 없음 |
| `python-quality` | lock, lint, format, type, unit | 없음 |
| `web-quality` | lock, lint, type, unit, build | 없음 |
| `contract-compat` | API/event schema, generated bindings, backward compatibility | 없음 |
| `secret-scan` | tracked+generated artifact의 secret/signature/header 패턴 | 없음 |
| `docs-evidence` | scenario manifest, path/hash/verdict, broken link | 없음 |
| `property-replay-failure` | fixed seed+seed corpus, deterministic digest, fault cases | local containers만 |
| `integration` | Postgres/Redis/migrations/API/workers | local containers만 |
| `e2e` | Chromium 1440px/360px Paper flow, keyboard 핵심 경로, serious/critical accessibility 0 | local containers만 |

CI는 Binance credential, Testnet credential, Mainnet private endpoint 또는 real model key를 절대 요구하지 않는다. Phase 2 public-data contract test는 recorded fixture/replay가 기본이며, 선택적 live-public smoke는 merge gate와 분리하고 인증 없는 allowlist만 사용한다.

## 5. Phase→root command→scenario→test path 정본

아래 표가 Phase별 신규 CI 분모의 단일 정본이다. `pnpm ci`는 현재 Phase까지의 모든 행을 누적 실행한다. `env:init`은 CI에서 `--check`로 실행하고 실제 `.env.local`을 쓰지 않는다.

| Phase | 새 root command | 필수 scenario ID | 최초 test path |
|---:|---|---|---|
| 1 | `bootstrap`, `env:init --check`, `lint`, `typecheck`, `test:unit`, `test:contracts`, `test:safety`, `test:integration`, `build`, compose health | CORE-001, PLAT-001~003, CONTRACT-001, SAFE-001~004 | `tests/unit`, `tests/contract`, `tests/safety`, `tests/integration` |
| 2 | `test:replay`, `test:failure`, `test:property`; live-public smoke는 optional/non-gate | DATA-001~007 | `tests/replay`, `tests/failure`, `tests/property` |
| 3 | 기존 `test:replay`, `test:safety`에 PIT corpus 추가 | PTI-001~004 | 기존 replay/safety path |
| 4 | 기존 `test:unit`, `test:integration`, `test:property`, `test:replay`, `test:failure`에 금융 corpus 추가 | FIN-001~004, ORD-001~002, ATOM-001~002 | 기존 unit/integration/property/replay/failure path |
| 5 | 기존 `test:unit`, `test:replay`, `test:failure`, `test:safety`에 Risk/Auth/Kill corpus 추가 | RISK-001~002, KILL-001~002, AUTH-001~002 | 기존 unit/replay/failure/safety path |
| 6 | 기존 `test:contracts`, `test:safety`에 AI/provider corpus 추가 | AI-001~002, SEC-001~002 | 기존 contract/safety path |
| 7 | `test:e2e` | E2E-001~005 | `tests/e2e` |

아직 존재하지 않는 Phase 명령을 빈 성공 script로 만들지 않는다. Phase가 열릴 때 RED test와 함께 구현하고 그 Phase부터 `pnpm ci` 분모에 추가한다.

## 6. Determinism과 evidence

- 테스트 시간은 injectable clock을 사용한다. wall clock fallback을 허용하지 않는 scenario를 포함한다.
- random/property tests는 실패 seed를 출력하고 corpus로 보존한다.
- locale/timezone은 UTC와 고정 locale로 설정한다.
- Decimal context, rounding, fee/cost-basis policy version을 test metadata에 기록한다.
- replay 결과는 input manifest hash, code revision, policy/schema versions, output digest를 남긴다.
- 각 CI job은 JUnit/JSON 또는 표준 로그 artifact 경로를 scenario manifest와 결속한다.
- 필수 scenario가 실행되지 않았거나 artifact가 없으면 pass rate 계산 자체를 실패시킨다.

## 7. Branch·PR gate

- 각 제품 Phase는 `codex/phase-{n}-{slug}` 별도 branch와 `main` 대상 독립 Draft PR이다.
- Phase 0은 정확히 `codex/phase-0-design`이며 문서·하네스·설계 외 delta가 0이어야 한다.
- Phase 0은 제품 manifest를 만들지 않는다. 대신 정본 branch, 필수 문서 링크·SHA-256·verdict, 금지 제품 경로 0, 하네스 validator, diff review, safety QA와 필요한 외부 review를 결정론적으로 검사한다. `package.json`이나 빈 성공 script를 만들어 P0를 닫지 않는다.
- clean `pnpm ci`는 root 명령이 처음 생기는 Phase 1부터 Conventional Commit 전 필수다. 모든 Phase는 해당 Phase 전용 evidence digest와 사용자 승인 순서를 유지한다.
- push와 PR 생성은 별도 외부 변경이므로 사용자 권한 또는 명시된 자동화 gate 없이 수행하지 않는다.

## 8. P0-11 Acceptance

P0-11은 다음이 모두 참일 때 `PASS`다.

- supported environment와 exact-version pin 위치가 정해졌다.
- 로컬과 CI가 같은 root command를 사용한다.
- Phase별 명령 활성화와 빈 성공 script 금지가 명시됐다.
- secret 없는 Mock/Paper clean-checkout 흐름이 정의됐다.
- Mainnet/Testnet/private capability zero와 fail-closed mode가 merge gate다.
- scenario manifest와 test artifact 경로가 연결된다.
- 이 파일의 SHA-256과 verdict가 최종 evidence manifest에 결속된다.

## 9. 다음 단계 참조

- Phase 1에서 toolchain exact versions를 공식 지원 상태에 맞춰 pin하고 lockfile을 생성한다.
- 첫 RED는 unknown/missing `TRADING_MODE`, 금지 capability scan, health/contract boundary다.
- `pnpm ci`는 현재 Phase에 승인된 테스트만 포함하되 필수 scenario 누락을 성공으로 취급하지 않는다.
