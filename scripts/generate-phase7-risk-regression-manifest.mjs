import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const source = resolve(root, "docs/woozoo-trading-desk/phase-5/p5-scenario-manifest.json");
const target = resolve(root, "docs/woozoo-trading-desk/phase-7/p7-risk-regression-manifest.json");
const manifest = JSON.parse(await readFile(source, "utf8"));
manifest.schema_version = "woozoo.phase-7-risk-regression-manifest/v1";
manifest.phase = 7;
manifest.supersedes_runtime_source_bindings =
  "docs/woozoo-trading-desk/phase-5/p5-scenario-manifest.json";
const phase7Nodes = {
  "RISK-SAFE-001": [
    "tests/safety/test_phase5_risk_boundaries.py::test_phase_five_dormancy_is_activated_only_at_the_phase_seven_browser_boundary",
    "tests/safety/test_phase5_risk_boundaries.py::test_risk_engine_has_no_network_exchange_secret_or_ai_capability",
    "tests/safety/test_phase5_risk_boundaries.py::test_phase_seven_contracts_preserve_the_phase_five_dormant_boundary",
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
