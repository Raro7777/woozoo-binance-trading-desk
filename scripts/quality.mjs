import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const pnpm = "pnpm";
const target = process.argv[2];
const deterministicEnvironment = { ...process.env, TZ: "UTC", PYTHONHASHSEED: "0" };
const sourceRevision = "binance-spot-api-docs@29c227d84058dd2be3fe3b42ab368d1d1ce910e5";
const policyVersion = "woozoo.market.collector-policy/v1";
const evidenceRecipeVersion = "woozoo.evidence.closed-candles-approved-features/v1";

function run(command, args) {
  const result = process.platform === "win32" && [pnpm, "corepack"].includes(command)
    ? spawnSync(process.env.ComSpec ?? "cmd.exe", ["/d", "/s", "/c", [command, ...args].join(" ")], {
        cwd: root,
        stdio: "inherit",
        env: deterministicEnvironment,
      })
    : spawnSync(command, args, { cwd: root, stdio: "inherit", env: deterministicEnvironment });
  if (result.status !== 0) throw new Error(`${command} ${args.join(" ")} failed`);
}

async function fixtureManifestDigest() {
  const fixtureRoot = resolve(root, "tests", "fixtures", "market-data");
  const names = (await readdir(fixtureRoot)).sort();
  const hash = createHash("sha256");
  for (const name of names) {
    hash.update(name);
    hash.update(await readFile(resolve(fixtureRoot, name)));
  }
  return hash.digest("hex");
}

async function paperMetadata() {
  const fixture = await readFile(resolve(root, "tests", "fixtures", "paper-domain", "p4-oracle-v1.json"));
  return {
    schema_version: "woozoo.paper.replay-manifest/v1",
    financial_policy_version: "woozoo.paper.financial-policy/v1",
    oracle_fixture_sha256: createHash("sha256").update(fixture).digest("hex"),
    network_enabled: false,
  };
}

async function revisionEvidence() {
  const commit = spawnSync("git", ["rev-parse", "HEAD"], {
    cwd: root,
    encoding: "utf8",
    env: deterministicEnvironment,
  });
  const files = spawnSync("git", ["ls-files", "--cached", "--others", "--exclude-standard", "-z"], {
    cwd: root,
    encoding: "utf8",
    env: deterministicEnvironment,
  });
  if (commit.status !== 0 || files.status !== 0) throw new Error("cannot capture revision evidence");
  const excludedPrefixes = [".codex-remote-attachments/", "artifacts/", "_workspace/"];
  const paths = files.stdout
    .split("\0")
    .filter(Boolean)
    .map((path) => path.replaceAll("\\", "/"))
    .filter((path) => !excludedPrefixes.some((prefix) => path.startsWith(prefix)))
    .sort();
  const manifest = createHash("sha256");
  for (const path of paths) {
    manifest.update(path.replaceAll("\\", "/"));
    manifest.update("\0");
    manifest.update(await readFile(resolve(root, path)));
    manifest.update("\0");
  }
  return {
    git_commit: commit.stdout.trim(),
    working_tree_digest: manifest.digest("hex"),
    frozen_file_count: paths.length,
  };
}

function runPnpm(args) {
  run("corepack", [pnpm, ...args]);
}

async function scenarios(
  area,
  ids,
  commands,
  executedTestCount = ids.length,
  commandsAlreadyPassed = false,
  metadata = {},
) {
  if (!commandsAlreadyPassed) {
    for (const [command, args] of commands) run(command, args);
  }
  const fixture_manifest_sha256 = await fixtureManifestDigest();
  const revision = await revisionEvidence();
  for (const id of ids) {
    const path = resolve(root, "artifacts", area, `${id}.json`);
    const result = {
      schema_version: "woozoo.market.replay-manifest/v1",
      id,
      status: "PASS",
      commands,
      source_revision: sourceRevision,
      policy_version: policyVersion,
      fixture_manifest_sha256,
      fixed_seed: 0,
      timezone: "UTC",
      test_count: executedTestCount,
      ...metadata,
      ...revision,
    };
    const output_digest = createHash("sha256").update(JSON.stringify(result)).digest("hex");
    await mkdir(resolve(path, ".."), { recursive: true });
    await writeFile(
      path,
      `${JSON.stringify({ ...result, output_digest, recorded_at: new Date().toISOString() }, null, 2)}\n`,
      "utf8",
    );
  }
}

async function dataScenario(area, id, nodeIds, metadata = {}) {
  const resultPath = resolve(root, "artifacts", ".pytest-results", `${id}.xml`);
  await mkdir(resolve(resultPath, ".."), { recursive: true });
  const command = [
    "python",
    [
      "-m", "uv", "run", "--locked", "pytest", ...nodeIds, "-q",
      `--junitxml=${resultPath}`,
    ],
  ];
  run(command[0], command[1]);
  const xml = await readFile(resultPath, "utf8");
  const suite = xml.match(/<testsuite\b([^>]*)>/);
  const counts = suite === null
    ? {}
    : Object.fromEntries([...suite[1].matchAll(/\b(tests|errors|failures|skipped)="(\d+)"/g)].map((match) => [match[1], Number(match[2])]));
  if (
    suite === null ||
    counts.tests !== nodeIds.length ||
    counts.errors !== 0 ||
    counts.failures !== 0 ||
    counts.skipped !== 0
  ) {
    throw new Error(`${id} must execute every declared node with zero failure, error, or skip`);
  }
  await scenarios(area, [id], [command], nodeIds.length, true, metadata);
}

const actions = {
  lint: async () => {
    run("python", ["-m", "uv", "run", "--locked", "ruff", "check", "packages", "services", "tests"]);
    run("python", ["-m", "uv", "run", "--locked", "ruff", "format", "--check", "packages", "services", "tests"]);
    runPnpm(["--filter", "@woozoo/trading-room-web", "run", "lint"]);
  },
  typecheck: async () => {
    run("python", ["-m", "uv", "run", "--locked", "mypy"]);
    runPnpm(["-r", "--if-present", "run", "typecheck"]);
  },
  "test:unit": async () => {
    await scenarios("unit", ["CORE-001"], [["python", ["-m", "uv", "run", "--locked", "pytest", "tests/unit", "-q"]]]);
    await dataScenario("unit", "EVID-001", [
      "tests/unit/test_evidence_features.py::test_derives_approved_decimal_features_with_ordered_provenance",
      "tests/unit/test_evidence_features.py::test_wilder_rsi_has_deterministic_flat_and_all_loss_edges[closes0-50.000000000000000000]",
      "tests/unit/test_evidence_features.py::test_wilder_rsi_has_deterministic_flat_and_all_loss_edges[closes1-0.000000000000000000]",
      "tests/unit/test_evidence_settings.py::test_evidence_settings_require_paper_mode_and_dedicated_database_url",
    ], { schema_version: "woozoo.evidence.replay-manifest/v1", evidence_recipe_version: evidenceRecipeVersion });
    await dataScenario("unit", "FIN-003", [
      "tests/property/test_paper_financial_properties.py::test_fin_003_exact_partial_fill_fifo_and_pnl_oracle",
    ], await paperMetadata());
  },
  "test:contracts": async () => {
    await scenarios("contracts", ["CONTRACT-001", "DATA-CONTRACT-001"], [["node", ["scripts/generate-contracts.mjs", "--check"]], ["corepack", [pnpm, "exec", "tsc", "-p", "tests/contract/tsconfig.json"]], ["corepack", [pnpm, "exec", "tsx", "--test", "tests/contract/contracts.test.ts", "tests/contract/market-data-contracts.test.ts", "tests/contract/evidence-contracts.test.ts", "tests/contract/paper-contracts.test.ts"]], ["python", ["-m", "uv", "run", "--locked", "pytest", "tests/contract", "-q"]]]);
    await dataScenario("contracts", "EVID-002", ["tests/contract/test_evidence_contract.py::test_evid_002_snapshot_contract_is_closed_and_consumer_complete"], { schema_version: "woozoo.evidence.replay-manifest/v1", evidence_recipe_version: evidenceRecipeVersion });
    await scenarios("contracts", ["PAPER-CONTRACT-001"], [["corepack", [pnpm, "exec", "tsx", "--test", "tests/contract/paper-contracts.test.ts"]]], 1, false, await paperMetadata());
  },
  "test:safety": async () => {
    await scenarios("safety", ["SAFE-001", "SAFE-002", "SAFE-003", "SAFE-004", "SAFE-005"], [["python", ["-m", "uv", "run", "--locked", "pytest", "tests/safety", "-q"]], ["node", ["scripts/capability-zero.mjs"]], ["corepack", [pnpm, "exec", "tsx", "--test", "tests/safety/capability-zero.test.ts"]]]);
    await dataScenario("safety", "EVID-007", [
      "tests/safety/test_phase3_evidence_capabilities.py::test_evidence_worker_has_no_network_or_later_phase_capability",
      "tests/safety/test_phase3_evidence_capabilities.py::test_phase_three_registers_only_the_approved_evidence_command_route",
    ], { schema_version: "woozoo.evidence.replay-manifest/v1", evidence_recipe_version: evidenceRecipeVersion });
    await dataScenario("safety", "PAPER-SAFE-001", [
      "tests/safety/test_phase4_paper_boundaries.py::test_phase_four_has_no_active_paper_route_or_network_ingress",
      "tests/safety/test_phase4_paper_boundaries.py::test_phase_four_settings_reject_credential_vocabulary",
    ], await paperMetadata());
  },
  "test:integration": async () => {
    await scenarios("integration", ["PLAT-001", "PLAT-002", "PLAT-003"], [["corepack", [pnpm, "--filter", "@woozoo/trading-room-web", "run", "build"]], ["python", ["-m", "uv", "run", "--locked", "pytest", "tests/integration", "-q"]], ["corepack", [pnpm, "exec", "tsx", "--test", "tests/integration/trading-room-web.test.ts"]]]);
    await dataScenario("integration", "EVID-005", ["tests/integration/test_platform_infrastructure.py::test_phase_three_evidence_is_atomic_idempotent_and_append_only"], { schema_version: "woozoo.evidence.replay-manifest/v1", evidence_recipe_version: evidenceRecipeVersion });
    await dataScenario("integration", "FIN-004", [
      "tests/integration/test_paper_ledger_immutability.py::test_fin_004_posted_journal_is_immutable_and_correction_is_reversal_replacement",
      "tests/integration/test_platform_infrastructure.py::test_phase_four_postgres_enforces_balance_and_immutable_ledger",
      "tests/integration/test_paper_postgres_persistence.py::test_atomic_write_is_durable_idempotent_and_restart_stable",
      "tests/integration/test_paper_postgres_persistence.py::test_writer_commit_succeeds_and_unledgered_balance_update_is_rejected",
      "tests/integration/test_paper_postgres_persistence.py::test_database_rejects_incomplete_financial_state_and_liquidity_overallocation",
      "tests/integration/test_paper_postgres_persistence.py::test_sell_fill_binds_fifo_basis_and_exact_ledger_amounts",
      "tests/integration/test_paper_postgres_persistence.py::test_failed_reconciliation_checkpoint_holds_new_lifecycle_command",
    ], await paperMetadata());
    await dataScenario("integration", "PAPER-MIGRATION-001", [
      "tests/integration/test_paper_migration_contract.py::test_phase_four_migration_closes_financial_and_activation_boundaries",
      "tests/integration/test_paper_postgres_persistence.py::test_downgrade_preserves_a_preexisting_writer_role",
      "tests/integration/test_paper_postgres_persistence.py::test_downgrade_removes_a_migration_created_writer_role",
    ], await paperMetadata());
  },
  "test:replay": async () => {
    await dataScenario("replay", "DATA-001", [
      "tests/replay/test_market_data_replay.py::test_data_001_recorded_replay_is_deterministic_and_deduplicated",
      "tests/unit/test_restart_recovery.py::test_restart_bootstraps_durable_state_and_recovers_pending_raw_once",
      "tests/integration/test_platform_infrastructure.py::test_postgres_restart_recovers_pending_raw_and_preserves_trade_continuity",
      "tests/integration/test_platform_infrastructure.py::test_postgres_restart_replay_preserves_semantic_digest_and_single_effect",
    ]);
    await dataScenario("replay", "DATA-002", [
      "tests/replay/test_market_data_replay.py::test_data_002_gap_is_observable_and_blocks_the_gapped_effect",
      "tests/unit/test_market_data_completeness.py::test_closed_kline_interval_grid_gap_is_rejected",
      "tests/unit/test_market_data_completeness.py::test_symbol_is_healthy_only_after_all_six_expected_streams_are_fresh",
      "tests/integration/test_platform_infrastructure.py::test_postgres_restart_restores_closed_kline_grid_continuity",
    ]);
    run("python", ["-m", "uv", "run", "--locked", "pytest", "tests/replay", "-q"]);
    await dataScenario("replay", "EVID-004", [
      "tests/unit/test_evidence_builder.py::test_builder_is_deterministic_and_prior_snapshot_is_immutable_under_late_arrival",
      "tests/unit/test_evidence_builder.py::test_newer_cutoff_deterministically_replaces_a_same_bucket_late_materialization",
    ], { schema_version: "woozoo.evidence.replay-manifest/v1", evidence_recipe_version: evidenceRecipeVersion });
    await dataScenario("replay", "ORD-002", [
      "tests/replay/test_paper_restart_replay.py::test_ord_002_restart_replay_has_identical_digest_and_single_effect",
      "tests/integration/test_paper_postgres_persistence.py::test_restart_continues_existing_order_with_distinct_cancel_command",
      "tests/integration/test_paper_postgres_persistence.py::test_restart_applies_and_idempotently_replays_observation_fill",
      "tests/integration/test_paper_postgres_persistence.py::test_restart_restores_floor_stepped_observation_budget_and_hash",
      "tests/integration/test_paper_postgres_persistence.py::test_restart_completes_shared_observation_for_second_order_once",
      "tests/integration/test_paper_postgres_persistence.py::test_rejected_command_is_durable_orderless_and_restart_idempotent",
      "tests/integration/test_paper_postgres_persistence.py::test_observation_no_fill_requires_ineligibility_or_exhausted_budget",
      "tests/integration/test_paper_postgres_persistence.py::test_terminal_order_cannot_append_a_new_accepted_version",
    ], await paperMetadata());
    await dataScenario("replay", "ATOM-002", [
      "tests/replay/test_paper_restart_replay.py::test_atom_002_ack_loss_retry_returns_same_order_without_new_effect",
      "tests/integration/test_paper_postgres_persistence.py::test_concurrent_command_and_observation_retries_return_one_stored_effect",
    ], await paperMetadata());
  },
  "test:failure": async () => {
    await dataScenario("failure", "DATA-003", [
      "tests/failure/test_market_data_failures.py::test_data_003_shutdown_is_control_only_and_reconnect_is_bounded",
      "tests/failure/test_market_data_supervisor.py::test_live_supervisor_reconnects_immediately_for_shutdown_then_backs_off",
      "tests/failure/test_market_data_supervisor.py::test_live_supervisor_accepts_the_raw_shutdown_control_form",
    ]);
    await dataScenario("failure", "DATA-004", [
      "tests/failure/test_market_data_failures.py::test_data_004_429_honors_retry_after_and_prevents_followup_call",
      "tests/unit/test_rest_collection.py::test_retry_after_blocks_the_collector_before_a_followup_network_call",
    ]);
    await dataScenario("failure", "DATA-005", [
      "tests/failure/test_market_data_failures.py::test_data_005_malformed_payload_is_quarantined_and_invalid",
      "tests/unit/test_rest_collection.py::test_malformed_rest_json_is_preserved_exactly_and_fails_closed",
      "tests/unit/test_rest_collection.py::test_rest_kline_future_close_is_quarantined_and_never_forced_closed",
      "tests/unit/test_public_market_transports.py::test_rest_transport_rejects_responses_over_the_fixed_limit",
      "tests/integration/test_platform_infrastructure.py::test_future_kline_quarantine_downgrades_durable_symbol_projection",
    ]);
    await dataScenario("failure", "DATA-006", [
      "tests/failure/test_market_data_failures.py::test_data_006_raw_append_failure_has_no_normalized_effect",
      "tests/unit/test_restart_recovery.py::test_restart_bootstraps_durable_state_and_recovers_pending_raw_once",
      "tests/integration/test_platform_infrastructure.py::test_market_raw_and_outbox_are_one_transaction",
      "tests/integration/test_platform_infrastructure.py::test_normalized_and_outbox_are_one_transaction",
      "tests/integration/test_platform_infrastructure.py::test_quality_and_outbox_are_one_transaction",
      "tests/integration/test_platform_infrastructure.py::test_postgres_restart_recovers_pending_raw_and_preserves_trade_continuity",
      "tests/integration/test_platform_infrastructure.py::test_postgres_restart_restores_closed_kline_grid_continuity",
    ]);
    run("python", ["-m", "uv", "run", "--locked", "pytest", "tests/failure", "-q"]);
    await dataScenario("failure", "EVID-006", [
      "tests/unit/test_evidence_builder.py::test_builder_fails_closed_when_an_interval_has_no_complete_window",
      "tests/unit/test_evidence_builder.py::test_builder_rejects_every_non_healthy_quality[degraded]",
      "tests/unit/test_evidence_builder.py::test_builder_rejects_every_non_healthy_quality[stale]",
      "tests/unit/test_evidence_builder.py::test_builder_rejects_every_non_healthy_quality[invalid]",
      "tests/unit/test_evidence_builder.py::test_builder_rejects_every_non_healthy_quality[reconnecting]",
      "tests/unit/test_evidence_builder.py::test_builder_rejects_a_terminal_window_that_is_stale_at_the_cutoffs",
    ], { schema_version: "woozoo.evidence.replay-manifest/v1", evidence_recipe_version: evidenceRecipeVersion });
    await dataScenario("failure", "ATOM-001", [
      "tests/failure/test_paper_atomicity.py::test_atom_001_injected_commit_failure_rolls_back_every_effect",
      "tests/failure/test_paper_postgres_atomicity.py::test_injected_failure_rolls_back_every_authoritative_row[receipt]",
      "tests/failure/test_paper_postgres_atomicity.py::test_injected_failure_rolls_back_every_authoritative_row[domain]",
      "tests/failure/test_paper_postgres_atomicity.py::test_injected_failure_rolls_back_every_authoritative_row[lot]",
      "tests/failure/test_paper_postgres_atomicity.py::test_injected_failure_rolls_back_every_authoritative_row[ledger-header]",
      "tests/failure/test_paper_postgres_atomicity.py::test_injected_failure_rolls_back_every_authoritative_row[ledger-entry]",
      "tests/failure/test_paper_postgres_atomicity.py::test_injected_failure_rolls_back_every_authoritative_row[outbox]",
    ], await paperMetadata());
  },
  "test:property": async () => {
    await dataScenario("property", "DATA-007", [
      "tests/property/test_market_data_backpressure.py::test_data_007_capacity_overflow_is_explicit_and_fail_closed",
      "tests/failure/test_market_data_supervisor.py::test_live_supervisor_uses_bounded_queue_and_persists_overflow_failure",
    ]);
    run("python", ["-m", "uv", "run", "--locked", "pytest", "tests/property", "-q"]);
    await dataScenario("property", "EVID-003", [
      "tests/property/test_evidence_boundaries.py::test_dual_cutoff_is_independently_inclusive[event_delta0-received_delta0-True]",
      "tests/property/test_evidence_boundaries.py::test_dual_cutoff_is_independently_inclusive[event_delta1-received_delta1-True]",
      "tests/property/test_evidence_boundaries.py::test_dual_cutoff_is_independently_inclusive[event_delta2-received_delta2-False]",
      "tests/property/test_evidence_boundaries.py::test_dual_cutoff_is_independently_inclusive[event_delta3-received_delta3-False]",
      "tests/unit/test_evidence_features.py::test_feature_derivation_rejects_incomplete_or_gapped_windows",
    ], { schema_version: "woozoo.evidence.replay-manifest/v1", evidence_recipe_version: evidenceRecipeVersion });
    await dataScenario("property", "FIN-001", [
      "tests/property/test_paper_financial_properties.py::test_fin_001_generated_fill_cancel_sequences_conserve_and_balance",
    ], await paperMetadata());
    await dataScenario("property", "FIN-002", [
      "tests/property/test_paper_financial_properties.py::test_fin_002_insufficient_cash_or_long_inventory_has_zero_order_and_ledger_effect",
    ], await paperMetadata());
    await dataScenario("property", "ORD-001", [
      "tests/unit/test_paper_engine.py::test_ord_001_partial_fill_duplicate_cancel_and_terminal_monotonicity",
    ], await paperMetadata());
  },
  build: async () => {
    run("node", ["scripts/generate-contracts.mjs", "--check"]);
    runPnpm(["--filter", "@woozoo/contracts", "run", "build"]);
    runPnpm(["--filter", "@woozoo/contract-bindings", "run", "build"]);
    runPnpm(["--filter", "@woozoo/trading-room-web", "run", "build"]);
    run("python", ["-m", "compileall", "-q", "packages", "services"]);
  },
  ci: async () => {
    runPnpm(["bootstrap"]);
    runPnpm(["env:init", "--", "--check"]);
    for (const action of ["lint", "typecheck", "test:unit", "test:contracts", "test:safety", "test:integration", "test:property", "test:replay", "test:failure", "build"]) {
      await actions[action]();
    }
  },
};

if (!(target in actions)) throw new Error(`unknown quality target: ${target}`);
await actions[target]();
