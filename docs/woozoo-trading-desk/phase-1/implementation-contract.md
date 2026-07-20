# Phase 1 platform foundation — implementation contract

## Status and scope

This document implements the active Phase 1 scope in
`docs/woozoo-trading-desk/phase-state.json`. It is not an approval to begin
Phase 2. The source acceptance criteria are P1-01 through P1-12 in
`docs/woozoo-trading-desk/phase-0/phase-1-scope-and-phase-0-report.md`.

Phase 1 creates only a secretless platform foundation:

- a pinned Node/pnpm and Python/uv workspace;
- a FastAPI `GET /api/v1/health` ingress and a Next.js health/config display shell;
- a versioned health/error/event-envelope contract with generated bindings;
- loopback-only local Postgres and Redis Compose services;
- generic durable receipt/outbox delivery infrastructure, reversible migration,
  observability redaction, and Phase 1 verification.

It creates no market-data, Evidence, Paper Broker, Risk, approval, Kill Switch,
AI, Trading Room, authentication/session, Testnet, private-exchange, or credential
capability.

## Deliberate boundary resolutions

The Phase 0 P1 scope uses the phrase “health/config shell,” but the authoritative
P1 API catalog creates only `GET /api/v1/health`. The implementation therefore has
**no** `/api/v1/config` endpoint. “Config shell” means server-side strict
configuration parsing and a static web display of the safe, already-public `paper`
mode fact. It does not serialize environment values, DSNs, headers, or provider
configuration.

P1 also has no identity table, login route, cookie, session, CSRF token, or actor
schema. The migration contains only generic platform receipts and outbox delivery
records. P1's `CONTRACT-001` covers only health/error/event-envelope, canonical
hashing, generated-binding drift, and closed P1 shapes; future approval, risk, and
actor contracts remain absent.

## Runtime and authority model

`TRADING_MODE` accepts exactly the explicit string `paper`. Missing, blank,
unknown, `live`, and `testnet` values fail before the API serves. The control API
requires Postgres for health and returns a v1 `DEPENDENCY_UNAVAILABLE` 503 error
when it is unavailable. Redis is an optional, non-authoritative cache/wake-up hint:
loss produces a degraded health result while durable Postgres records stay unchanged.

`compose.yaml` pins Postgres 17 and Redis 7.4 by content digest, publishes only
`127.0.0.1:5433` and `127.0.0.1:6380`, and declares no exchange, Testnet, model, or
other external service configuration. The bridge network is needed for local
host-to-container integration verification; the capability-zero scanner checks the
actual configuration, dependencies, schemas, and product source for forbidden
exchange capability rather than treating the generic Docker network name as a
trading capability.

## Contract ownership and generated artifacts

`packages/contracts/spec/` is the source of truth for the closed P1 health API and
`woozoo.event/v1` envelope. `node scripts/generate-contracts.mjs --check` verifies
deterministic generated TypeScript and Python bindings plus the checked schema
manifest. No generated binding is hand-maintained as a second source of truth, and
no P1 domain event is emitted.

## Command contract

The canonical P1 gate is:

```text
pnpm ci
```

The repository pins pnpm 7.33.7 because this version resolves `pnpm ci` as the
package's `ci` script, rather than reserving the command for a clean installation.
It runs `pnpm bootstrap`, verifies the non-writing `pnpm env:init -- --check`, then
runs lint, typecheck, unit, contract, safety, integration, and build gates. Those
scenario commands write fixed local evidence paths under ignored `artifacts/`.
The committed pnpm 7 lockfile is the clean-install precondition. The
`onlyBuiltDependencies: [esbuild]` policy allows the one required native build and
blocks unapproved dependency build scripts.

The required individual commands are:

```text
pnpm bootstrap
pnpm env:init -- --check
pnpm lint
pnpm typecheck
pnpm test:unit
pnpm test:contracts
pnpm test:safety
pnpm test:integration
pnpm build
pnpm ci
```

There are no success-only placeholders for later property, replay, failure, or
browser E2E stages.

## Evidence mapping

| Criterion | Executable evidence |
|---|---|
| P1-01 | `pnpm bootstrap`; committed lockfiles, version files, and generator check |
| P1-02 | `scripts/env-init.mjs` and non-destructive check |
| P1-03 | `tests/safety/test_phase_one_boundaries.py` |
| P1-04 | FastAPI/Next shells and `PLAT-001` |
| P1-05 | Postgres/Redis integration and `PLAT-003` |
| P1-06 | source specs, manifest, generated bindings, `CONTRACT-001` |
| P1-07 | Alembic migration cycle in `PLAT-002` |
| P1-08 | redaction tests and capability scan |
| P1-09 | source/dependency/config/schema capability-zero test |
| P1-10 | root quality commands and GitHub Actions workflow |
| P1-11 | this contract, working history, acceptance manifest, scenario artifacts |
| P1-12 | final safety QA, same-engine cross-review, external-review status, Git/PR record, and digest-bound user approval |

P1 acceptance is valid only if the final evidence manifest marks every criterion
PASS, review records contain no unresolved blocking finding, and the user later
approves that manifest's exact digest for the P1-to-P2 transition.
