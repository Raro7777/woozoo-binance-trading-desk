import { spawnSync } from "node:child_process";
import { readdirSync } from "node:fs";
import { join, resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const target = process.argv[2];
const deterministicEnvironment = { ...process.env, PYTHONHASHSEED: "0", TZ: "UTC" };

function run(command, args, environment = {}) {
  const env = { ...deterministicEnvironment, ...environment };
  const result = process.platform === "win32" && ["corepack", "pnpm"].includes(command)
    ? spawnSync(process.env.ComSpec ?? "cmd.exe", ["/d", "/s", "/c", [command, ...args].join(" ")], {
        cwd: root,
        stdio: "inherit",
        env,
      })
    : spawnSync(command, args, { cwd: root, stdio: "inherit", env });
  if (result.status !== 0) throw new Error(`${command} ${args.join(" ")} failed`);
}

function pnpm(args) {
  run("corepack", ["pnpm", ...args]);
}

function filesUnder(directory, suffixes) {
  const discovered = [];
  for (const entry of readdirSync(resolve(root, directory), { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) discovered.push(...filesUnder(path, suffixes));
    else if (suffixes.some((suffix) => entry.name.endsWith(suffix))) discovered.push(path.replaceAll("\\", "/"));
  }
  return discovered.sort();
}

function pytest(paths) {
  run("python", ["-m", "uv", "run", "--locked", "pytest", "-q", "-m", "not legacy_acceptance", ...paths]);
}

function nodeTests(paths) {
  if (paths.length > 0) pnpm(["exec", "tsx", "--test", ...paths]);
}

const actions = {
  contracts() {
    run("node", ["scripts/generate-contracts.mjs", "--check"]);
    pnpm(["exec", "tsc", "-p", "tests/contract/tsconfig.json"]);
    pytest(["tests/contract"]);
    nodeTests(filesUnder("tests/contract", [".test.ts", ".test.mjs"]));
  },
  core() {
    pytest(["tests/unit", "tests/contract", "tests/property", "tests/replay"]);
    nodeTests([
      ...filesUnder("tests/unit", [".test.ts", ".test.mjs"]),
      ...filesUnder("tests/contract", [".test.ts", ".test.mjs"]),
    ]);
    run("node", ["scripts/generate-contracts.mjs", "--check"]);
    pnpm(["exec", "tsc", "-p", "tests/contract/tsconfig.json"]);
  },
  safety() {
    pytest(["tests/safety", "tests/failure"]);
    nodeTests(filesUnder("tests/safety", [".test.ts", ".test.mjs"]));
    run("node", ["scripts/capability-zero.mjs"]);
  },
  integration() {
    pytest(["tests/integration"]);
    pnpm(["--filter", "@woozoo/trading-room-web", "run", "build"]);
    nodeTests(filesUnder("tests/integration", [".test.ts", ".test.mjs"]));
  },
  e2e() {
    pnpm(["exec", "playwright", "test"]);
  },
  property() {
    pytest(["tests/property"]);
  },
  replay() {
    pytest(["tests/replay"]);
  },
  failure() {
    pytest(["tests/failure"]);
  },
  lint() {
    run("python", ["-m", "uv", "run", "--locked", "ruff", "check", "packages", "services", "tests"]);
    run("python", ["-m", "uv", "run", "--locked", "ruff", "format", "--check", "packages", "services", "tests"]);
    pnpm(["--filter", "@woozoo/trading-room-web", "run", "lint"]);
  },
  typecheck() {
    run("python", ["-m", "uv", "run", "--locked", "mypy"]);
    pnpm(["-r", "--if-present", "run", "typecheck"]);
  },
  build() {
    run("node", ["scripts/generate-contracts.mjs", "--check"]);
    pnpm(["--filter", "@woozoo/contracts", "run", "build"]);
    pnpm(["--filter", "@woozoo/contract-bindings", "run", "build"]);
    pnpm(["--filter", "@woozoo/trading-room-web", "run", "build"]);
    run("python", ["-m", "compileall", "-q", "packages", "services"]);
  },
  async ci() {
    run("node", ["scripts/bootstrap.mjs"]);
    run("node", ["scripts/env-init.mjs", "--check"]);
    for (const action of ["lint", "typecheck", "core", "safety", "integration", "e2e", "build"]) {
      console.log(`\n[paper-mvp] ${action}`);
      await actions[action]();
    }
  },
};

if (!(target in actions)) {
  throw new Error(`unknown maintenance quality target: ${target}; expected ${Object.keys(actions).sort().join(", ")}`);
}
await actions[target]();
