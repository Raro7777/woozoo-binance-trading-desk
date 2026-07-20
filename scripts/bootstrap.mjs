import { spawnSync } from "node:child_process";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const pnpm = "pnpm";
const skipPnpmInstall = process.argv.includes("--skip-pnpm-install");

function run(command, args) {
  const result = process.platform === "win32" && [pnpm, "corepack"].includes(command)
    ? spawnSync(process.env.ComSpec ?? "cmd.exe", ["/d", "/s", "/c", [command, ...args].join(" ")], {
        cwd: root,
        stdio: "inherit",
      })
    : spawnSync(command, args, { cwd: root, stdio: "inherit" });
  if (result.status !== 0) process.exit(result.status ?? 1);
}

function runPnpm(args) {
  run("corepack", [pnpm, ...args]);
}

let uvAvailable = spawnSync("python", ["-m", "uv", "--version"], { cwd: root, stdio: "ignore" });
if (uvAvailable.status !== 0) {
  run("python", ["-m", "pip", "install", "--user", "uv==0.11.29"]);
  uvAvailable = spawnSync("python", ["-m", "uv", "--version"], { cwd: root, stdio: "ignore" });
  if (uvAvailable.status !== 0) process.exit(1);
}

if (!skipPnpmInstall) runPnpm(["install", "--frozen-lockfile"]);
run("python", ["-m", "uv", "sync", "--locked", "--group", "dev"]);
run("node", ["scripts/generate-contracts.mjs", "--check"]);
