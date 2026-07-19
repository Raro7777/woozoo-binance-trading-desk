import { spawnSync } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const pnpm = "pnpm";
const target = process.argv[2];

function run(command, args) {
  const result = process.platform === "win32" && [pnpm, "corepack"].includes(command)
    ? spawnSync(process.env.ComSpec ?? "cmd.exe", ["/d", "/s", "/c", [command, ...args].join(" ")], {
        cwd: root,
        stdio: "inherit",
      })
    : spawnSync(command, args, { cwd: root, stdio: "inherit" });
  if (result.status !== 0) throw new Error(`${command} ${args.join(" ")} failed`);
}

function runPnpm(args) {
  run("corepack", [pnpm, ...args]);
}

async function scenarios(area, ids, commands) {
  for (const [command, args] of commands) run(command, args);
  for (const id of ids) {
    const path = resolve(root, "artifacts", area, `${id}.json`);
    await mkdir(resolve(path, ".."), { recursive: true });
    await writeFile(
      path,
      `${JSON.stringify({ id, status: "PASS", commands, recorded_at: new Date().toISOString() }, null, 2)}\n`,
      "utf8",
    );
  }
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
  "test:unit": () => scenarios("unit", ["CORE-001"], [["python", ["-m", "uv", "run", "--locked", "pytest", "tests/unit", "-q"]]]),
  "test:contracts": () => scenarios("contracts", ["CONTRACT-001"], [["node", ["scripts/generate-contracts.mjs", "--check"]], ["corepack", [pnpm, "exec", "tsc", "-p", "tests/contract/tsconfig.json"]], ["corepack", [pnpm, "exec", "tsx", "--test", "tests/contract/contracts.test.ts"]]]),
  "test:safety": () => scenarios("safety", ["SAFE-001", "SAFE-002", "SAFE-003", "SAFE-004", "SAFE-005"], [["python", ["-m", "uv", "run", "--locked", "pytest", "tests/safety", "-q"]], ["node", ["scripts/capability-zero.mjs"]], ["corepack", [pnpm, "exec", "tsx", "--test", "tests/safety/capability-zero.test.ts"]]]),
  "test:integration": () => scenarios("integration", ["PLAT-001", "PLAT-002", "PLAT-003"], [["corepack", [pnpm, "--filter", "@woozoo/trading-room-web", "run", "build"]], ["python", ["-m", "uv", "run", "--locked", "pytest", "tests/integration", "-q"]], ["corepack", [pnpm, "exec", "tsx", "--test", "tests/integration/trading-room-web.test.ts"]]]),
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
    for (const action of ["lint", "typecheck", "test:unit", "test:contracts", "test:safety", "test:integration", "build"]) {
      await actions[action]();
    }
  },
};

if (!(target in actions)) throw new Error(`unknown quality target: ${target}`);
await actions[target]();
