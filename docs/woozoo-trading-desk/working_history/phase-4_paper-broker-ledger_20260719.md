# Phase 4 Working History — Paper Broker and Ledger

- P3 digest `a85fa86194...e076c` was explicitly approved and the P3→P4 envelope was committed.
- Branch `codex/phase-4-paper-ledger` was created after the transition commit.
- The system architect and risk/ledger builder returned report-only contracts; the root
  orchestrator persisted their handoffs under `_workspace/`.
- Implementation follows P4 creation/P7 activation separation: no Paper HTTP route,
  scheduler, queue listener, production account seed, Risk/Approval/Kill/Proposal FK or
  external exchange capability exists.
- RED→GREEN coverage was added for FIN-001..004, ORD-001..002 and ATOM-001..002.
- Official keyless Binance Spot exchangeInfo was reverified for BTCUSDT and ETHUSDT and
  the response hash plus adopted filters were frozen.
- Postgres migration adds test-namespace Paper storage, per-commodity deferred balance
  assertion, immutable history triggers, least-privilege writer and dormant outbox identity.
- Final CI, role-separated Safety QA, Codex cross-review, external review status, Git/PR
  evidence and acceptance digest remain required before requesting Phase 5 approval.

## Final freeze

- Corrective review loops closed shared observation ordering, causal FIFO, exact
  NUMERIC(38,18) results, context-free commodity balance and exact participation flooring.
- Frozen implementation commit `19b97256ab4c8e3cb97e8c16e882176d48d497be` passed
  `corepack pnpm ci` in 197.6 seconds with all eleven Phase 4 scenario artifacts bound to
  worktree digest `2f67a9de85adf8f95c35d35e227304b68ab6c1540a0b39b1417cc36d6d35ac91`.
- Role-separated Safety QA returned PASS and the same-engine Codex cross-review returned
  NO_CONFIRMED_FINDINGS. External review is accurately recorded as unavailable, not PASS.
- GitHub Draft PR #5 is open against `main` with the exact frozen implementation head.
- Phase 5 remains blocked until the exact Phase 4 acceptance manifest digest is approved.
