import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const source = resolve(root, "docs/woozoo-trading-desk/phase-6/p6-scenario-manifest.json");
const target = resolve(root, "docs/woozoo-trading-desk/phase-7/p7-agent-regression-manifest.json");
const manifest = JSON.parse(await readFile(source, "utf8"));
manifest.schema_version = "woozoo.phase-7-agent-regression-manifest/v1";
manifest.phase = 7;
manifest.runtime_namespace = "paper";
manifest.supersedes_runtime_source_bindings =
  "docs/woozoo-trading-desk/phase-6/p6-scenario-manifest.json";
const phase7Nodes = {
  "SEC-002": [
    "tests/safety/test_phase6_agent_boundaries.py::test_sec_002_agent_has_no_execution_or_exchange_capability",
    "tests/safety/test_phase6_agent_boundaries.py::test_sec_002_phase7_api_exposes_analysis_command_without_ai_execution_capability",
  ],
};
for (const scenario of manifest.scenarios) {
  if (phase7Nodes[scenario.id]) scenario.test_nodes = phase7Nodes[scenario.id];
  const sourcePaths = [...new Set(scenario.test_nodes.map((node) => node.split("::", 1)[0]))];
  scenario.source_sha256 = [];
  for (const sourcePath of sourcePaths) {
    scenario.source_sha256.push(
      createHash("sha256").update(await readFile(resolve(root, sourcePath))).digest("hex"),
    );
  }
}
await writeFile(target, `${JSON.stringify(manifest, null, 2)}\n`);
