# Phase 5 amendment to the Phase 0 creation schedule

Amendment version: `woozoo.phase-schedule-amendment/2026-07-20-p5`

The monotonic `phase-state.json` gate approved for Phase 5 supersedes only the creation
schedule statements that previously placed Approval and `PaperExecutionAuthorization` in
Phase 5. Their financial semantics, ordering and future acceptance requirements remain
unchanged.

- Phase 5 creates RiskDecision and Kill Switch only.
- Phase 5 keeps the P4 opaque test-namespace authorization reference without a foreign key.
- Approval aggregate, login/session, approval ingress, PaperExecutionAuthorization table,
  state projection, issuance/consumption handlers and FK are reassigned to Phase 7.
- `AUTH-001` and `AUTH-002` remain RED specifications and move to the Phase 7 mandatory
  denominator. They are not omitted or counted PASS in Phase 5.
- P6 may validate Proposal→Risk only through test fixtures and cannot activate approval,
  authorization or Paper production ingress.
- No Testnet/private/live capability is authorized by this amendment.

Authority chain: approved `phase-state.json` → this versioned amendment → the original
Phase 0 creation/activation tables. Historical Phase 0 text is not silently rewritten.
