# Phase 6 implementation contract — AI analysis and TradeProposal

## Status and boundary

- Phase: 6
- Branch: `codex/phase-6-ai-proposal`
- Production activation phase: 7
- Runtime provider: deterministic `mock` only
- Runtime namespace: `test` only
- Active HTTP/UI/approval/order ingress added by this phase: zero

The agent-orchestrator reads immutable healthy Evidence and may create closed analysis reports,
an analytical `TradeProposal`, and append-only audit records. It has no account, balance, Risk
verdict, Kill recovery, approval, authorization, Paper order, exchange, credential, or external
provider capability. Model output is never financial or execution authority.

## Fixed workflow

`MARKET_REGIME → TECHNICAL → TRADE_FLOW → BULL → BEAR → TRADER → PORTFOLIO → AUDIT`

The coordinator fixes role order, timeout, manifests, citations, and canonical hashes. Every stage
receives quoted Evidence plus accepted dependency reports and an empty tool allowlist. The
deterministic audit validates; it does not repair model output.

A valid analytical `side=HOLD` may be a Proposal and is not Risk-eligible. Provider timeout,
provider failure, malformed/schema-invalid output, missing role, unhealthy/future Evidence,
orphan citation, temporal violation, prompt injection, or tool call is a run-level HOLD: no
`trade_proposals` row, no Proposal event, no RiskDecision, and no financial/external effect.

## Version and compatibility decisions

- Preserve the Phase 5 frozen `woozoo.risk-input/v1` and fixture semantics byte-for-byte.
- Add `woozoo.risk-input/v2` for full authoritative Proposal binding in `namespace=test` only.
- Keep all agent domain events dormant with `x-activation-phase=7`.
- Do not register analysis routes in active OpenAPI or FastAPI during Phase 6.
- Do not add a real model provider dependency, secret variable, or egress rule.
- Do not add news, macro, social, derivatives, or private exchange inputs.

## Acceptance items

| ID | Required result |
|---|---|
| P6-01 | Closed analysis/report/prompt/audit/Proposal/event and test-only Risk v2 contracts have generated digest-matched Python/TS bindings; activation is Phase 7. |
| P6-02 | Every accepted report/Proposal binds one immutable healthy Evidence ID/digest, `as_of`, `knowledge_cutoff`, manifests, and only member Evidence citations. |
| P6-03 | Fixed Mock provider and injected clock produce byte-identical report, Proposal, audit, and event hashes across replay. |
| P6-04 | The eight fixed roles execute exactly once in order; missing, duplicate, reordered, or unknown roles yield run-level HOLD. |
| P6-05 | BUY/SELL Proposals pass the full-hash `risk-input/v2` test chain; HOLD has no Risk evaluation and production effects remain zero. |
| P6-06 | Timeout/failure/malformed/schema/prompt/citation/time/tool faults yield stable HOLD codes and no accepted Proposal or external effect. |
| P6-07 | Agent persistence is append-only and atomic with restrictive Evidence/FK integrity, least privilege, retry safety, and fail-closed data-bearing downgrade. |
| P6-08 | Tool registry is exactly empty; agent source/config/dependencies contain no forbidden exchange/account/Risk/approval/order/secret/egress capability; active routes remain zero. |
| P6-09 | `AI-001`, `AI-002`, `SEC-001`, `SEC-002` and supporting unit/replay/property/failure/integration scenarios produce bound artifacts. |
| P6-10 | Prior Phase tests and canonical `corepack pnpm ci` remain green. |
| P6-11 | Role-separated Safety QA and same-engine Codex cross-review evidence are recorded; same-engine review is not external review. |
| P6-12 | External review is completed or `external-review-unavailable` is recorded, Draft PR Git gate passes, and exact digest approval is required for Phase 7. |

## Stable HOLD reasons

`EVIDENCE_NOT_FOUND`, `EVIDENCE_DIGEST_MISMATCH`, `EVIDENCE_UNHEALTHY`,
`EVIDENCE_FUTURE_CONTAMINATION`, `PROVIDER_TIMEOUT`, `PROVIDER_FAILURE`,
`MODEL_OUTPUT_MALFORMED`, `REPORT_SCHEMA_INVALID`, `REQUIRED_REPORT_MISSING`,
`ORPHAN_EVIDENCE_ITEM`, `TEMPORAL_BOUNDARY_VIOLATION`, `PROMPT_INJECTION_DETECTED`,
`TOOL_CALL_FORBIDDEN`, `AUDIT_REJECTED`.

## Required evidence

- `artifacts/contracts/AI-001.json`
- `artifacts/safety/AI-002.json`
- `artifacts/safety/SEC-001.json`
- `artifacts/safety/SEC-002.json`
- deterministic replay, migration, atomicity, property, and test-only Proposal→Risk artifacts
- final CI, review, Git gate, acceptance manifest, and digest-bound user approval
