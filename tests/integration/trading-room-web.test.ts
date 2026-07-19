import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createServer } from "node:net";
import { resolve } from "node:path";
import test from "node:test";

const root = resolve(import.meta.dirname, "../..");

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds));
}

async function availablePort(): Promise<number> {
  return new Promise((resolvePort, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (address === null || typeof address === "string") {
        reject(new Error("could not reserve a loopback port"));
        return;
      }
      server.close((error) => (error === undefined ? resolvePort(address.port) : reject(error)));
    });
  });
}

function startWeb(port: number) {
  const argumentsForPnpm = [
    "pnpm",
    "--filter",
    "@woozoo/trading-room-web",
    "run",
    "start",
    "--hostname",
    "127.0.0.1",
    "--port",
    String(port),
  ];
  return process.platform === "win32"
    ? spawn(process.env.ComSpec ?? "cmd.exe", ["/d", "/s", "/c", ["corepack", ...argumentsForPnpm].join(" ")], {
        cwd: root,
        stdio: ["ignore", "pipe", "pipe"],
        windowsHide: true,
      })
    : spawn("corepack", argumentsForPnpm, { cwd: root, stdio: ["ignore", "pipe", "pipe"] });
}

async function stopWeb(processToStop: ReturnType<typeof startWeb>): Promise<void> {
  if (processToStop.exitCode !== null || processToStop.pid === undefined) return;
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/PID", String(processToStop.pid), "/T", "/F"], { windowsHide: true });
  } else {
    processToStop.kill("SIGTERM");
  }
  for (let attempts = 0; attempts < 20 && processToStop.exitCode === null; attempts += 1) {
    await delay(100);
  }
}

test("PLAT-001 starts the built web shell without configuration disclosure", async () => {
  const port = await availablePort();
  const web = startWeb(port);
  let output = "";
  web.stdout?.on("data", (chunk) => {
    output += String(chunk);
  });
  web.stderr?.on("data", (chunk) => {
    output += String(chunk);
  });
  try {
    let response: Response | undefined;
    for (let attempts = 0; attempts < 80; attempts += 1) {
      if (web.exitCode !== null) throw new Error(`web shell exited early: ${output}`);
      try {
        response = await fetch(`http://127.0.0.1:${port}/`);
        break;
      } catch {
        await delay(100);
      }
    }
    assert.ok(response, `web shell did not start: ${output}`);
    assert.equal(response.status, 200);
    const body = await response.text();
    assert.match(body, /Woozoo platform shell/);
    assert.match(body, /Paper-only platform foundation/);
    assert.doesNotMatch(body, /postgresql:|redis:|TRADING_MODE/i);
  } finally {
    await stopWeb(web);
  }
});
