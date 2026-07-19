# Phase 1 platform foundation

## 1. Context

Phase 0 acceptance digest `9efd487cf4878528df89cc19c2951e7d61f8fad99e704ccfe1dff03fa7c79571`
was explicitly approved for the P0-to-P1 transition. The authoritative state is
now `current_phase=1` in `docs/woozoo-trading-desk/phase-state.json`.

## 2. Scope applied

Implemented only the P1 platform foundation: locked toolchains, secretless
configuration, health-only web/API shells, versioned contract generation,
Postgres/Redis Compose, generic durable platform/outbox tables, redaction,
capability-zero enforcement, and P1 tests. No Phase 2 or later domain module,
route, schema, configuration, or dependency was introduced.

## 3. Key implementation decisions

- `TRADING_MODE` is exactly `paper` or startup fails closed.
- Postgres is durable authority; Redis is optional and explicitly non-authoritative.
- The only control API route is `GET /api/v1/health`; a config HTTP endpoint was
  intentionally not created.
- P1 generic receipt/outbox infrastructure cannot manufacture a business event or
  call an external system.
- Postgres and Redis images are digest pinned and exposed on loopback-only ports.

## 4. Verification performed

The P1 implementation is verified by the canonical aggregate command `pnpm ci`:
bootstrap, env check, lint, strict typecheck, unit, contract, safety, integration,
and build. It produced `CORE-001`, `CONTRACT-001`, `SAFE-001` through `SAFE-005`,
and `PLAT-001` through `PLAT-003` JSON artifacts under ignored `artifacts/`.

The test framework emitted one non-failing Starlette `TestClient` deprecation
warning during integration tests; it does not hide a test failure and is retained
for review rather than suppressed.

## 5. Review and evidence status

Role-separated safety QA passed the executable P1-01 through P1-10 checks. The
same-engine Codex cross-review found no implementation defect but remains
UNVERIFIED for process evidence, and the external route is recorded as
`external-review-unavailable`, not PASS. P1 is not accepted and its acceptance
digest is not yet assigned.

## 6. No-go boundaries

Do not add a Binance client, public market collector, Evidence store, Paper Broker,
finance/risk/approval logic, AI proposal flow, Trading Room controls,
authentication, Testnet/private capability, credentials, or any live trading
surface while this Phase 1 record is active.

## 7. 다음 단계 참조

1. Complete the role-separated safety QA and internal Codex cross-review against
   the concrete P1 diff and resolve any confirmed finding with a test-first fix.
2. Run the project-selected external review route once; if no permitted external
   reviewer is available, retain `external-review-unavailable` as the evidence
   rather than treating an internal review as external PASS.
3. Freeze a P1 acceptance manifest with SHA-256 evidence, commit/push it on the
   existing Draft PR, and request explicit user approval bound to that exact digest.
4. Do not begin Phase 2 until the digest-bound P1-to-P2 transition is recorded in
   `phase-state.json`.
