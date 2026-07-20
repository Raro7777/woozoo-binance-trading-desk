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
const riskBoundaryCases = [
  "order_notional_below", "order_notional_equal", "order_notional_above",
  "sell_order_notional_below", "sell_order_notional_equal", "sell_order_notional_above",
  "realized_loss_below", "realized_loss_equal", "realized_loss_above",
  "drawdown_below", "drawdown_equal", "drawdown_above",
  "spread_below", "spread_equal", "spread_above",
  "slippage_below", "slippage_equal", "slippage_above",
  "btc_exposure_below", "btc_exposure_equal", "btc_exposure_above",
  "eth_exposure_below", "eth_exposure_equal", "eth_exposure_above",
  "portfolio_exposure_below", "portfolio_exposure_equal", "portfolio_exposure_above",
  "sell_btc_exposure_below", "sell_btc_exposure_equal", "sell_btc_exposure_above",
  "sell_eth_exposure_below", "sell_eth_exposure_equal", "sell_eth_exposure_above",
  "sell_portfolio_exposure_below", "sell_portfolio_exposure_equal", "sell_portfolio_exposure_above",
  "sell_reduces_long", "evidence_missing", "data_stale", "future_contamination",
  "watermark_incomplete", "data_invalid", "expired", "duplicate_proposal",
  "duplicate_order_intent", "equity_invalid", "bid_missing", "ask_missing",
  "bid_zero", "ask_zero", "ledger_mismatch",
  "order_type_not_limit", "side_not_long_cash", "tif_not_allowed",
  "invalid_price_or_qty", "insufficient_available_balance", "fee_reserve_insufficient",
  "sell_exceeds_position", "sell_held_only", "max_precision_exposure_above",
  "kill_snapshot_inconsistent",
  "invalid_open_order_state", "loss_scale_edge_below",
];
const riskGuardReasonCases = {
  ORDER_TYPE_NOT_LIMIT: "order_type_not_limit",
  SIDE_NOT_LONG_CASH: "side_not_long_cash",
  TIF_NOT_ALLOWED: "tif_not_allowed",
  INVALID_PRICE_OR_QTY: "invalid_price_or_qty",
  INSUFFICIENT_AVAILABLE_BALANCE: "insufficient_available_balance",
  FEE_RESERVE_INSUFFICIENT: "fee_reserve_insufficient",
  SELL_EXCEEDS_POSITION: "sell_exceeds_position",
};
const killFaults = [
  "paper_create_lock_before_activation", "activation_lock_before_paper_create",
  "paper_fill_lock_before_activation", "activation_lock_before_paper_fill",
  "one_hundred_and_one_open_orders", "crash_before_batch_commit",
  "crash_after_batch_commit_before_ack", "duplicate_activation_delivery",
];
const killAttempts = ["timer", "ai", "process_restart", "redis_expiry", "unauthenticated_actor"];

function run(command, args, environment = {}) {
  const commandEnvironment = { ...deterministicEnvironment, ...environment };
  const result = process.platform === "win32" && [pnpm, "corepack"].includes(command)
    ? spawnSync(process.env.ComSpec ?? "cmd.exe", ["/d", "/s", "/c", [command, ...args].join(" ")], {
        cwd: root,
        stdio: "inherit",
        env: commandEnvironment,
      })
    : spawnSync(command, args, { cwd: root, stdio: "inherit", env: commandEnvironment });
  if (result.status !== 0) throw new Error(`${command} ${args.join(" ")} failed`);
}

async function runPlaywrightWithResultGate(expectedTestCount) {
  const resultPath = resolve(root, "artifacts", ".playwright-results", "e2e.xml");
  await mkdir(resolve(resultPath, ".."), { recursive: true });
  const command = [process.execPath, [resolve(root, "node_modules", "@playwright", "test", "cli.js"), "test", "--reporter=junit"]];
  run(command[0], command[1], { PLAYWRIGHT_JUNIT_OUTPUT_NAME: resultPath });
  const xml = await readFile(resultPath, "utf8");
  const suites = xml.match(/<testsuites\b([^>]*)>/);
  const counts = suites === null
    ? {}
    : Object.fromEntries([...suites[1].matchAll(/\b(tests|errors|failures|skipped)="(\d+)"/g)].map((match) => [match[1], Number(match[2])]));
  if (
    suites === null
    || counts.tests !== expectedTestCount
    || counts.errors !== 0
    || counts.failures !== 0
    || counts.skipped !== 0
  ) {
    throw new Error("Playwright must execute every declared browser scenario with zero failure, error, or skip");
  }
  for (const id of ["E2E-001", "E2E-002", "E2E-003", "E2E-004", "E2E-005"]) {
    if (!xml.includes(`[live] ${id}`)) throw new Error(`${id} must be reported as a live non-mock browser scenario`);
  }
  for (const id of ["UI-001", "UI-002", "UI-003", "UI-004"]) {
    if (!xml.includes(`[ui-only] ${id}`)) throw new Error(`${id} must remain explicitly isolated UI-only coverage`);
  }
  return { command, testCount: counts.tests };
}

async function runPytestWithResultGate(testPath, expectedTestCount) {
  const resultPath = resolve(root, "artifacts", ".pytest-results", "phase7-api.xml");
  await mkdir(resolve(resultPath, ".."), { recursive: true });
  const command = [
    "python",
    ["-m", "uv", "run", "--locked", "pytest", "-q", testPath, `--junitxml=${resultPath}`],
  ];
  run(command[0], command[1]);
  const xml = await readFile(resultPath, "utf8");
  const suite = xml.match(/<testsuite\b([^>]*)>/);
  const counts = suite === null
    ? {}
    : Object.fromEntries([...suite[1].matchAll(/\b(tests|errors|failures|skipped)="(\d+)"/g)].map((match) => [match[1], Number(match[2])]));
  if (
    suite === null
    || counts.tests !== expectedTestCount
    || counts.errors !== 0
    || counts.failures !== 0
    || counts.skipped !== 0
  ) {
    throw new Error(`pytest ${testPath} must execute exactly ${expectedTestCount} tests with zero failure, error, or skip`);
  }
  return { command, testCount: counts.tests };
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

async function riskMetadata() {
  const manifest = await readFile(resolve(root, "docs", "woozoo-trading-desk", "phase-7", "p7-risk-regression-manifest.json"));
  return {
    schema_version: "woozoo.risk.replay-manifest/v1",
    risk_policy_version: "woozoo.risk-policy/v1",
    scenario_manifest_sha256: createHash("sha256").update(manifest).digest("hex"),
    fixed_seed: 0,
    timezone: "UTC",
    network_enabled: false,
  };
}

async function riskDataScenario(area, id, nodeIds) {
  const manifestPath = resolve(root, "docs", "woozoo-trading-desk", "phase-7", "p7-risk-regression-manifest.json");
  const manifestBytes = await readFile(manifestPath);
  const manifest = JSON.parse(manifestBytes.toString("utf8"));
  const scenario = manifest.scenarios?.find((candidate) => candidate.id === id);
  if (
    scenario === undefined ||
    scenario.command !== `corepack pnpm test:${area === "contracts" ? "contracts" : area}` ||
    scenario.artifact !== `artifacts/${area}/${id}.json` ||
    JSON.stringify(scenario.test_nodes) !== JSON.stringify(nodeIds) ||
    typeof scenario.oracle !== "string" ||
    scenario.oracle.length === 0
  ) {
    throw new Error(`${id} scenario contract does not match its exact harness nodes`);
  }
  const sourcePaths = [...new Set(nodeIds.map((node) => node.split("::", 1)[0]))];
  const sourceDigests = [];
  for (const sourcePath of sourcePaths) {
    sourceDigests.push(createHash("sha256").update(await readFile(resolve(root, sourcePath))).digest("hex"));
  }
  if (JSON.stringify(scenario.source_sha256) !== JSON.stringify(sourceDigests)) {
    throw new Error(`${id} source digest contract is stale`);
  }
  const expectedRiskCases = Object.fromEntries(riskBoundaryCases.map((name) => [
    name, `tests/unit/test_risk_engine.py::test_risk_002_complete_boundary_matrix[${name}]`,
  ]));
  const expectedKillFaults = Object.fromEntries(killFaults.map((name) => [
    name, `tests/failure/test_kill_fault_matrix.py::test_kill_001_fault_matrix[${name}]`,
  ]));
  const expectedKillAttempts = Object.fromEntries(killAttempts.map((name) => [
    name, `tests/safety/test_kill_switch_recovery_boundary.py::test_kill_002_attempt_keeps_postgres_active[${name}]`,
  ]));
  if (id === "RISK-002" && JSON.stringify(scenario.boundary_cases) !== JSON.stringify(expectedRiskCases)) {
    throw new Error("RISK-002 must bind the complete frozen boundary matrix to exact cases");
  }
  if (id === "RISK-002") {
    const expectedReasons = Object.fromEntries(Object.entries(riskGuardReasonCases).map(([reason, name]) => [
      reason, `tests/unit/test_risk_engine.py::test_risk_002_complete_boundary_matrix[${name}]`,
    ]));
    if (JSON.stringify(scenario.guard_reason_cases) !== JSON.stringify(expectedReasons)) {
      throw new Error("RISK-002 must bind every semantic product guard reason to an exact case");
    }
  }
  if (
    id === "KILL-001" || id === "KILL-002"
  ) {
    const [actual, expected, label] = id === "KILL-001"
      ? [scenario.fault_cases, expectedKillFaults, "fault"]
      : [scenario.attempt_cases, expectedKillAttempts, "attempt"];
    if (JSON.stringify(actual) !== JSON.stringify(expected)) {
      throw new Error(`${id} must bind every frozen ${label} id to its exact executed case`);
    }
  }
  await dataScenario(area, id, nodeIds, {
    ...(await riskMetadata()),
    scenario_input_digest: createHash("sha256").update(JSON.stringify(scenario)).digest("hex"),
  });
}

async function agentMetadata() {
  const manifest = await readFile(resolve(root, "docs", "woozoo-trading-desk", "phase-7", "p7-agent-regression-manifest.json"));
  return {
    schema_version: "woozoo.agent.replay-manifest/v1",
    workflow_version: "woozoo.agent-workflow/v1",
    provider: "mock",
    model: "woozoo-deterministic-mock/v1",
    scenario_manifest_sha256: createHash("sha256").update(manifest).digest("hex"),
    fixed_seed: 0,
    timezone: "UTC",
    network_enabled: false,
    tool_allowlist: [],
    runtime_namespace: "paper",
  };
}

async function agentDataScenario(area, id, nodeIds) {
  const manifestPath = resolve(root, "docs", "woozoo-trading-desk", "phase-7", "p7-agent-regression-manifest.json");
  const manifestBytes = await readFile(manifestPath);
  const manifest = JSON.parse(manifestBytes.toString("utf8"));
  const scenario = manifest.scenarios?.find((candidate) => candidate.id === id);
  if (
    scenario === undefined ||
    scenario.command !== `corepack pnpm test:${area === "contracts" ? "contracts" : area}` ||
    scenario.artifact !== `artifacts/${area}/${id}.json` ||
    JSON.stringify(scenario.test_nodes) !== JSON.stringify(nodeIds) ||
    typeof scenario.oracle !== "string" ||
    scenario.oracle.length === 0
  ) {
    throw new Error(`${id} Phase 7 agent regression contract does not match its exact harness nodes`);
  }
  const sourcePaths = [...new Set(nodeIds.map((node) => node.split("::", 1)[0]))];
  const sourceDigests = [];
  for (const sourcePath of sourcePaths) {
    sourceDigests.push(createHash("sha256").update(await readFile(resolve(root, sourcePath))).digest("hex"));
  }
  if (JSON.stringify(scenario.source_sha256) !== JSON.stringify(sourceDigests)) {
    throw new Error(`${id} Phase 7 agent regression source digest contract is stale`);
  }
  await dataScenario(area, id, nodeIds, {
    ...(await agentMetadata()),
    scenario_input_digest: createHash("sha256").update(JSON.stringify(scenario)).digest("hex"),
  });
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
    await riskDataScenario("unit", "RISK-002", [
      "tests/unit/test_risk_engine.py::test_risk_002_closed_reason_precedence_collects_all_applicable_reasons",
      "tests/unit/test_risk_engine.py::test_invalid_or_non_fixture_input_fails_closed_without_approval_dependency",
      "tests/unit/test_risk_engine.py::test_nested_unknowns_policy_rebinding_and_cross_snapshot_mismatch_fail_closed",
      "tests/unit/test_risk_engine.py::test_complete_input_schema_rejects_nested_type_and_range_counterexamples[numeric_proposal_id]",
      "tests/unit/test_risk_engine.py::test_complete_input_schema_rejects_nested_type_and_range_counterexamples[negative_kill_version]",
      "tests/unit/test_risk_engine.py::test_decimal_thresholds_use_unrounded_values_and_documented_comparators",
      "tests/unit/test_risk_engine.py::test_unbounded_commitment_sum_is_exact_beyond_fixed_decimal_precision",
      "tests/property/test_risk_properties.py::test_spread_and_directional_slippage_boundaries",
      ...riskBoundaryCases.map((name) => `tests/unit/test_risk_engine.py::test_risk_002_complete_boundary_matrix[${name}]`),
    ]);
  },
  "test:contracts": async () => {
    await scenarios("contracts", ["CONTRACT-001", "DATA-CONTRACT-001"], [["node", ["scripts/generate-contracts.mjs", "--check"]], ["corepack", [pnpm, "exec", "tsc", "-p", "tests/contract/tsconfig.json"]], ["corepack", [pnpm, "exec", "tsx", "--test", "tests/contract/contracts.test.ts", "tests/contract/market-data-contracts.test.ts", "tests/contract/evidence-contracts.test.ts", "tests/contract/paper-contracts.test.ts", "tests/contract/risk-contracts.test.ts"]], ["python", ["-m", "uv", "run", "--locked", "pytest", "tests/contract", "-q"]]]);
    await dataScenario("contracts", "EVID-002", ["tests/contract/test_evidence_contract.py::test_evid_002_snapshot_contract_is_closed_and_consumer_complete"], { schema_version: "woozoo.evidence.replay-manifest/v1", evidence_recipe_version: evidenceRecipeVersion });
    await scenarios("contracts", ["PAPER-CONTRACT-001"], [["corepack", [pnpm, "exec", "tsx", "--test", "tests/contract/paper-contracts.test.ts"]]], 1, false, await paperMetadata());
    await riskDataScenario("contracts", "RISK-CONTRACT-001", [
      "tests/contract/test_risk_contract.py::test_risk_contract_001_is_closed_typed_and_dormant",
    ]);
    await agentDataScenario("contracts", "AI-001", [
      "tests/contract/test_agent_contract.py::test_ai_001_failure_matrix",
    ]);
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
    await riskDataScenario("safety", "RISK-SAFE-001", [
      "tests/safety/test_phase5_risk_boundaries.py::test_phase_five_dormancy_is_activated_only_at_the_phase_seven_browser_boundary",
      "tests/safety/test_phase5_risk_boundaries.py::test_risk_engine_has_no_network_exchange_secret_or_ai_capability",
      "tests/safety/test_phase5_risk_boundaries.py::test_phase_seven_contracts_preserve_the_phase_five_dormant_boundary",
    ]);
    await riskDataScenario("safety", "KILL-002", [
      "tests/safety/test_kill_switch_recovery_boundary.py::test_kill_002_has_no_automatic_ai_or_unauthenticated_recovery",
      "tests/safety/test_kill_switch_recovery_boundary.py::test_kill_002_scans_every_executable_config_and_tool_registry_for_recovery_writer",
      ...killAttempts.map((name) => `tests/safety/test_kill_switch_recovery_boundary.py::test_kill_002_attempt_keeps_postgres_active[${name}]`),
    ]);
    await agentDataScenario("safety", "AI-002", [
      "tests/safety/test_phase6_agent_boundaries.py::test_ai_002_orphan_and_future_claims_hold",
    ]);
    await agentDataScenario("safety", "SEC-001", [
      "tests/safety/test_phase6_agent_boundaries.py::test_sec_001_has_no_real_provider_or_secret_configuration",
    ]);
    await agentDataScenario("safety", "SEC-002", [
      "tests/safety/test_phase6_agent_boundaries.py::test_sec_002_agent_has_no_execution_or_exchange_capability",
      "tests/safety/test_phase6_agent_boundaries.py::test_sec_002_phase7_api_exposes_analysis_command_without_ai_execution_capability",
    ]);
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
      "tests/integration/test_paper_postgres_persistence.py::test_deferred_fifo_rejects_younger_first_even_if_later_sale_exhausts_older",
      "tests/integration/test_paper_postgres_persistence.py::test_shared_sell_observation_uses_canonical_sale_order_for_fifo_basis",
      "tests/integration/test_paper_postgres_persistence.py::test_cancel_outbox_binds_exact_receipt_and_rejects_non_ascii_identity",
      "tests/integration/test_paper_postgres_persistence.py::test_database_rejects_noncanonical_ledger_transaction_id",
      "tests/unit/test_paper_engine.py::test_ledger_transaction_id_rejects_unicode_and_non_hash_ids",
      "tests/integration/test_paper_postgres_persistence.py::test_failed_reconciliation_checkpoint_holds_new_lifecycle_command",
    ], await paperMetadata());
    await dataScenario("integration", "PAPER-MIGRATION-001", [
      "tests/integration/test_paper_migration_contract.py::test_phase_four_migration_closes_financial_and_activation_boundaries",
      "tests/integration/test_paper_postgres_persistence.py::test_downgrade_preserves_a_preexisting_writer_role",
      "tests/integration/test_paper_postgres_persistence.py::test_downgrade_removes_a_migration_created_writer_role",
    ], await paperMetadata());
    await riskDataScenario("integration", "RISK-MIGRATION-001", [
      "tests/integration/test_risk_postgres_persistence.py::test_risk_decision_persistence_is_atomic_unique_and_replay_stable",
      "tests/integration/test_risk_postgres_persistence.py::test_risk_writer_cannot_reset_or_decrease_the_kill_barrier",
      "tests/integration/test_risk_migration_contract.py::test_risk_migration_001_closes_authority_and_barrier_boundaries",
      "tests/integration/test_risk_migration_contract.py::test_phase_four_to_five_to_four_to_five_migration_cycle_is_recoverable",
      "tests/integration/test_risk_migration_contract.py::test_preexisting_risk_login_and_direct_grants_survive_empty_downgrade",
      "tests/integration/test_risk_migration_contract.py::test_phase_five_downgrade_fails_closed_when_immutable_history_exists",
      "tests/unit/test_docker_infrastructure_lock.py::test_shared_docker_lock_serializes_two_spawned_processes",
      "tests/integration/test_reconciliation_kill_handler.py::test_critical_reconciliation_mismatch_activates_kill_once_and_retries_idempotently",
    ]);
    await agentDataScenario("integration", "AGENT-INTEGRATION-001", [
      "tests/integration/test_agent_migration_contract.py::test_phase6_migration_is_append_only_least_privilege_and_test_only",
      "tests/integration/test_agent_migration_contract.py::test_preexisting_agent_role_and_direct_grants_survive_empty_downgrade",
      "tests/integration/test_agent_migration_contract.py::test_empty_downgrade_removes_migration_created_agent_role",
      "tests/integration/test_agent_postgres_persistence.py::test_agent_persistence_is_atomic_idempotent_and_append_only",
      "tests/integration/test_agent_postgres_persistence.py::test_agent_persistence_rejects_hold_with_proposal",
      "tests/integration/test_agent_postgres_persistence.py::test_agent_persistence_rejects_rehashed_event_authority_mutation",
      "tests/integration/test_agent_postgres_persistence.py::test_agent_database_rejects_hold_proposal_child",
      "tests/integration/test_proposal_risk_fixture_chain.py::test_p6_authoritative_proposal_binds_full_hash_to_test_risk_v2",
      "tests/integration/test_proposal_risk_fixture_chain.py::test_p6_hold_is_not_risk_eligible",
      "tests/integration/test_proposal_risk_fixture_chain.py::test_p6_risk_rejects_proposal_data_evidence_mismatch",
    ]);
  },
  "test:e2e": async () => {
    const api = await runPytestWithResultGate("tests/integration/test_trading_room_api.py", 7);
    const playwright = await runPlaywrightWithResultGate(14);
    await scenarios(
      "e2e",
      ["E2E-001", "E2E-002", "E2E-003", "E2E-004", "E2E-005"],
      [
        api.command,
        playwright.command,
      ],
      api.testCount + playwright.testCount,
      true,
    );
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
      "tests/unit/test_paper_engine.py::test_shared_observation_uses_one_canonical_broker_sequence_and_budget",
      "tests/integration/test_paper_postgres_persistence.py::test_hydrated_engine_ignores_observation_older_than_order_acceptance",
      "tests/unit/test_paper_engine.py::test_pre_acceptance_observation_is_ignored_without_recording_an_effect",
    ], await paperMetadata());
    await dataScenario("replay", "ATOM-002", [
      "tests/replay/test_paper_restart_replay.py::test_atom_002_ack_loss_retry_returns_same_order_without_new_effect",
      "tests/integration/test_paper_postgres_persistence.py::test_concurrent_command_and_observation_retries_return_one_stored_effect",
    ], await paperMetadata());
    await riskDataScenario("replay", "RISK-001", [
      "tests/unit/test_risk_engine.py::test_risk_001_same_complete_input_has_same_allowed_decision",
      "tests/unit/test_risk_engine.py::test_risk_001_every_bound_section_mutation_changes_digest",
      "tests/replay/test_risk_replay.py::test_risk_replay_is_independent_of_json_key_order_and_process_identity",
      "tests/replay/test_risk_replay.py::test_reason_message_or_recording_metadata_cannot_enter_decision_hash",
    ]);
    await agentDataScenario("replay", "AGENT-REPLAY-001", [
      "tests/replay/test_agent_replay.py::test_agent_replay_is_stable_across_provider_json_key_order",
    ]);
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
    await riskDataScenario("failure", "KILL-001", [
      ...killFaults.map((name) => `tests/failure/test_kill_fault_matrix.py::test_kill_001_fault_matrix[${name}]`),
      "tests/failure/test_kill_cancel_identity.py::test_kill_cancel_id_binds_full_activation_and_order_ids",
    ]);
    await agentDataScenario("failure", "AGENT-FAILURE-001", [
      "tests/failure/test_agent_provider_failures.py::test_provider_failure_at_every_role_has_no_proposal",
    ]);
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
    await agentDataScenario("property", "AGENT-PROPERTY-001", [
      "tests/property/test_agent_evidence_properties.py::test_every_orphan_citation_fails_closed",
    ]);
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
    // Avoid an extra pnpm lifecycle nesting level on Windows. The bootstrap
    // script itself runs the frozen pnpm install; invoking it through another
    // `pnpm run` can terminate the outer CI lifecycle before later gates run.
    // `pnpm ci` already starts with the lockfile-installed Node runtime. Avoid
    // replacing its node_modules from a nested pnpm install on Windows, while
    // still executing the Python sync and generated-contract bootstrap gates.
    run("node", ["scripts/bootstrap.mjs", "--skip-pnpm-install"]);
    run("node", ["scripts/env-init.mjs", "--check"]);
    for (const action of ["lint", "typecheck", "test:unit", "test:contracts", "test:safety", "test:integration", "test:property", "test:replay", "test:failure", "test:e2e", "build"]) {
      await actions[action]();
    }
  },
};

if (!(target in actions)) throw new Error(`unknown quality target: ${target}`);
await actions[target]();
