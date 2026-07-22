import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");
const p7Path = "artifacts/acceptance/P7-43.json";
const p7Bytes = readFileSync(p7Path);
const p7 = JSON.parse(p7Bytes);
const failures = [];
const uniqueRuns = new Map();

function executionLeaves(gate) {
  return Array.isArray(gate?.components)
    ? gate.components.flatMap((component) => executionLeaves(component))
    : [gate];
}

function executionEvidence(run) {
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

function validateExecutionRun(run, artifactId) {
  const identity = {
    schema_version: "woozoo.test-execution-run/v1",
    invocation_id: run.invocation_id,
    result_label: run.result_label,
    command: run.command,
    result_digest: run.result_digest,
    result_summary: {
      framework: run.framework,
      tests: run.tests,
      errors: run.errors ?? 0,
      failures: run.failures ?? 0,
      skipped: run.skipped ?? 0,
      cancelled: run.cancelled ?? 0,
      todo: run.todo ?? 0,
    },
  };
  if (sha256(JSON.stringify(identity)) !== run.run_id) failures.push(`run-id:${artifactId}`);
  const evidence = executionEvidence(run);
  const prior = uniqueRuns.get(run.run_id);
  if (prior !== undefined && JSON.stringify(prior) !== JSON.stringify(evidence)) {
    failures.push(`run-conflict:${artifactId}`);
  }
  uniqueRuns.set(run.run_id, evidence);
}

for (const artifact of p7.artifacts) {
  const bytes = readFileSync(artifact.path);
  if (sha256(bytes) !== artifact.sha256) failures.push(`sha:${artifact.id}`);
  const parsed = JSON.parse(bytes);
  const outputDigest = parsed.output_digest;
  delete parsed.output_digest;
  delete parsed.recorded_at;
  if (sha256(JSON.stringify(parsed)) !== outputDigest) failures.push(`output:${artifact.id}`);
  if (parsed.status !== "PASS") failures.push(`status:${artifact.id}`);
  for (const run of executionLeaves(parsed.execution_gate)) {
    validateExecutionRun(run, artifact.id);
  }
  if (
    parsed.git_commit !== p7.git_commit ||
    parsed.git_tree !== p7.git_tree ||
    parsed.working_tree_digest !== p7.working_tree_digest ||
    parsed.frozen_file_count !== p7.frozen_file_count
  ) {
    failures.push(`revision:${artifact.id}`);
  }
}

const preflightPath = "artifacts/e2e/E2E-INFRA-001.json";
const preflightBytes = readFileSync(preflightPath);
const preflightSha256 = sha256(preflightBytes);
const preflight = JSON.parse(preflightBytes);
for (const id of ["E2E-001", "E2E-002", "E2E-003", "E2E-004", "E2E-005"]) {
  const result = JSON.parse(readFileSync(`artifacts/e2e/${id}/result.json`));
  const binding = result.infrastructure_preflight;
  if (
    !binding ||
    binding.path !== preflightPath ||
    binding.sha256 !== preflightSha256 ||
    binding.output_digest !== preflight.output_digest
  ) {
    failures.push(`preflight:${id}`);
  }
}

const paths = execFileSync(
  "git",
  ["ls-files", "--cached", "--others", "--exclude-standard", "-z"],
  { encoding: "utf8" },
)
  .split("\0")
  .filter(Boolean)
  .map((path) => path.replaceAll("\\", "/"))
  .filter(
    (path) =>
      ![".codex-remote-attachments/", "artifacts/", "_workspace/"].some((prefix) =>
        path.startsWith(prefix),
      ),
  )
  .sort();
const manifest = createHash("sha256");
for (const path of paths) {
  manifest.update(path);
  manifest.update("\0");
  manifest.update(readFileSync(path));
  manifest.update("\0");
}
const worktreeDigest = manifest.digest("hex");
const p7OutputDigest = p7.output_digest;
delete p7.output_digest;
delete p7.recorded_at;
const uniqueExecutionRuns = [...uniqueRuns.values()].sort((left, right) =>
  left.run_id.localeCompare(right.run_id),
);
const artifactTestCoverageCount = p7.artifacts.reduce(
  (count, artifact) => count + artifact.test_count,
  0,
);
const uniqueTestExecutionCount = uniqueExecutionRuns.reduce(
  (count, run) => count + run.tests,
  0,
);
if (
  p7.artifact_test_coverage_count !== artifactTestCoverageCount
  || p7.unique_test_execution_count !== uniqueTestExecutionCount
  || p7.unique_execution_run_count !== uniqueExecutionRuns.length
  || JSON.stringify(p7.unique_execution_runs) !== JSON.stringify(uniqueExecutionRuns)
) {
  failures.push("execution-summary");
}

const result = {
  failures,
  artifact_count: p7.artifacts.length,
  unique_paths: new Set(p7.artifacts.map((artifact) => artifact.path)).size,
  artifact_test_coverage_count: artifactTestCoverageCount,
  unique_test_execution_count: uniqueTestExecutionCount,
  unique_execution_run_count: uniqueExecutionRuns.length,
  p7_file_sha256: sha256(p7Bytes),
  p7_output_valid: sha256(JSON.stringify(p7)) === p7OutputDigest,
  preflight_sha256: preflightSha256,
  preflight_output_digest: preflight.output_digest,
  git_commit: execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim(),
  git_tree: execFileSync("git", ["rev-parse", "HEAD^{tree}"], { encoding: "utf8" }).trim(),
  worktree_digest: worktreeDigest,
  frozen_file_count: paths.length,
  revision_matches:
    worktreeDigest === p7.working_tree_digest && paths.length === p7.frozen_file_count,
};

console.log(JSON.stringify(result, null, 2));
if (
  failures.length ||
  !result.p7_output_valid ||
  !result.revision_matches ||
  result.artifact_count !== 43 ||
  result.unique_paths !== 43
) {
  process.exitCode = 1;
}
