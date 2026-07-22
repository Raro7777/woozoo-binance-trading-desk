import { createHash, randomUUID } from "node:crypto";


function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}


function resultSummary(gate) {
  return {
    framework: gate.framework,
    tests: gate.tests,
    errors: gate.errors ?? 0,
    failures: gate.failures ?? 0,
    skipped: gate.skipped ?? 0,
    cancelled: gate.cancelled ?? 0,
    todo: gate.todo ?? 0,
  };
}


function canonicalRunIdentity(invocationId, resultLabel, command, resultDigest, gate) {
  return {
    schema_version: "woozoo.test-execution-run/v1",
    invocation_id: invocationId,
    result_label: resultLabel,
    command,
    result_digest: resultDigest,
    result_summary: resultSummary(gate),
  };
}


export function bindExecutionRun(resultLabel, command, resultBytes, gate) {
  const invocationId = randomUUID();
  const resultDigest = sha256(resultBytes);
  const identity = canonicalRunIdentity(invocationId, resultLabel, command, resultDigest, gate);
  return {
    ...gate,
    invocation_id: invocationId,
    result_label: resultLabel,
    command,
    result_digest: resultDigest,
    run_id: sha256(JSON.stringify(identity)),
  };
}


export function executionGateLeaves(gate) {
  if (Array.isArray(gate?.components)) {
    return gate.components.flatMap((component) => executionGateLeaves(component));
  }
  return [gate];
}


export function assertResultBoundExecution(run, label = "execution run") {
  if (
    run === null
    || typeof run !== "object"
    || typeof run.result_label !== "string"
    || run.result_label.length === 0
    || !Array.isArray(run.command)
    || typeof run.command[0] !== "string"
    || !Array.isArray(run.command[1])
    || !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u.test(run.invocation_id ?? "")
    || !/^[0-9a-f]{64}$/u.test(run.result_digest ?? "")
    || !/^[0-9a-f]{64}$/u.test(run.run_id ?? "")
  ) {
    throw new Error(`${label} is not bound to a valid JUnit execution identity`);
  }
  const identity = canonicalRunIdentity(
    run.invocation_id,
    run.result_label,
    run.command,
    run.result_digest,
    run,
  );
  if (run.run_id !== sha256(JSON.stringify(identity))) {
    throw new Error(`${label} run id does not match its execution evidence`);
  }
}


function runEvidence(run) {
  return {
    run_id: run.run_id,
    invocation_id: run.invocation_id,
    result_digest: run.result_digest,
    result_label: run.result_label,
    command: run.command,
    framework: run.framework,
    tests: run.tests,
    errors: run.errors ?? 0,
    failures: run.failures ?? 0,
    skipped: run.skipped ?? 0,
    cancelled: run.cancelled ?? 0,
    todo: run.todo ?? 0,
  };
}


export function summarizeExecutionGates(gates) {
  const uniqueRuns = new Map();
  for (const gate of gates) {
    for (const leaf of executionGateLeaves(gate)) {
      assertResultBoundExecution(leaf);
      const evidence = runEvidence(leaf);
      const prior = uniqueRuns.get(evidence.run_id);
      if (prior !== undefined && JSON.stringify(prior) !== JSON.stringify(evidence)) {
        throw new Error(`execution run ${evidence.run_id} has conflicting evidence`);
      }
      uniqueRuns.set(evidence.run_id, evidence);
    }
  }
  const runs = [...uniqueRuns.values()].sort((left, right) => left.run_id.localeCompare(right.run_id));
  return {
    artifact_test_coverage_count: gates.reduce((total, gate) => total + gate.tests, 0),
    unique_test_execution_count: runs.reduce((total, run) => total + run.tests, 0),
    unique_execution_run_count: runs.length,
    unique_execution_runs: runs,
  };
}
