import assert from "node:assert/strict";
import test from "node:test";

import {
  bindExecutionRun,
  summarizeExecutionGates,
} from "../../scripts/quality-evidence.mjs";


function passingGate(tests) {
  return {
    framework: "pytest-junit",
    tests,
    errors: 0,
    failures: 0,
    skipped: 0,
  };
}


test("shared result-bound runs are counted once while artifact coverage remains explicit", () => {
  const shared = bindExecutionRun(
    "shared-suite",
    ["python", ["-m", "pytest"]],
    Buffer.from("<testsuite tests=\"3\"/>", "utf8"),
    passingGate(3),
  );
  const supplemental = bindExecutionRun(
    "supplemental-suite",
    ["python", ["-m", "pytest", "supplemental"]],
    Buffer.from("<testsuite tests=\"2\"/>", "utf8"),
    passingGate(2),
  );
  const combined = {
    framework: "combined-junit",
    tests: 5,
    errors: 0,
    failures: 0,
    skipped: 0,
    components: [shared, supplemental],
  };

  const summary = summarizeExecutionGates([combined, shared, shared]);

  assert.equal(summary.artifact_test_coverage_count, 11);
  assert.equal(summary.unique_test_execution_count, 5);
  assert.equal(summary.unique_execution_run_count, 2);
  assert.deepEqual(
    summary.unique_execution_runs.map((run) => run.run_id),
    [...new Set(summary.unique_execution_runs.map((run) => run.run_id))].sort(),
  );
});


test("a run id cannot be reused with conflicting execution evidence", () => {
  const original = bindExecutionRun(
    "suite",
    ["python", ["-m", "pytest"]],
    Buffer.from("<testsuite tests=\"1\"/>", "utf8"),
    passingGate(1),
  );
  const conflicting = { ...original, tests: 2 };

  assert.throws(
    () => summarizeExecutionGates([original, conflicting]),
    /run id does not match its execution evidence/u,
  );
});


test("separate invocations remain distinct even when command and JUnit bytes match", () => {
  const command = ["python", ["-m", "pytest"]];
  const resultBytes = Buffer.from("<testsuite tests=\"1\"/>", "utf8");
  const first = bindExecutionRun("suite", command, resultBytes, passingGate(1));
  const second = bindExecutionRun("suite", command, resultBytes, passingGate(1));

  const summary = summarizeExecutionGates([first, second]);

  assert.equal(first.result_digest, second.result_digest);
  assert.notEqual(first.invocation_id, second.invocation_id);
  assert.notEqual(first.run_id, second.run_id);
  assert.equal(summary.artifact_test_coverage_count, 2);
  assert.equal(summary.unique_test_execution_count, 2);
  assert.equal(summary.unique_execution_run_count, 2);
});
