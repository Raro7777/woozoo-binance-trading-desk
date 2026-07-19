import assert from "node:assert/strict";
import test from "node:test";

import {
  productRoots,
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
    "packages/python/platform-core",
    "packages/typescript",
    "db/migrations",
    "infra",
    "alembic.ini",
    "compose.yaml",
    ".env.example",
    "pnpm-workspace.yaml",
    "pyproject.toml",
  ]));
  await scanPaths();
  await verifyCanaryFailure();
});
