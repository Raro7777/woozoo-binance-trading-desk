import assert from "node:assert/strict";
import test from "node:test";

import {
  productRoots,
  scanRootPackageConfiguration,
  scanPaths,
  verifyCanaryFailure,
} from "../../scripts/capability-zero.mjs";

test("SAFE-002 through SAFE-004 keep Phase 1 capability-zero", async () => {
  assert.deepEqual(new Set(productRoots), new Set([
    ".github",
    "apps",
    "scripts",
    "services",
    "packages/contracts",
    "packages/python",
    "packages/typescript",
    "db/migrations",
    "infra",
    "alembic.ini",
    "compose.yaml",
    "compose.phase8.yaml",
    ".env.example",
    "pnpm-workspace.yaml",
    "pyproject.toml",
    "package.json",
  ]));
  assert.throws(() => scanRootPackageConfiguration({ config: { live_mode: true } }));
  assert.throws(() => scanRootPackageConfiguration({ config: { exchange_client: "forbidden" } }));
  assert.throws(() => scanRootPackageConfiguration({ config: { private_account_client: true } }));
  assert.throws(() => scanRootPackageConfiguration({ config: { account_client: "forbidden" } }));
  await scanPaths();
  await verifyCanaryFailure();
});
