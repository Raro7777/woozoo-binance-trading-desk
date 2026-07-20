import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createServer } from "node:net";
import { resolve } from "node:path";
import test from "node:test";

import { approvalActionIssues } from "../../apps/trading-room-web/src/lib/approval-preview";

const root = resolve(import.meta.dirname, "../..");

const canonicalApprovalView = {
  status: "READY",
  approval_action_allowed: true,
  view_version: 1,
  proposal_id: "a".repeat(64),
  proposal_hash: "b".repeat(64),
  risk_decision_id: "c".repeat(64),
  risk_decision_hash: "d".repeat(64),
  risk_input_digest: "e".repeat(64),
  risk_policy_version: "woozoo.risk-policy/v1",
  risk_verdict: "ALLOWED",
  paper_order_preview_hash: "f".repeat(64),
  paper_order_preview: {
    symbol: "BTCUSDT",
    side: "BUY",
    order_type: "LIMIT",
    time_in_force: "GTC",
    quantity: "0.001",
    limit_price: "60001.00",
    worst_case_fee: "0.060001",
    worst_case_hold: "60.061001",
    worst_case_notional: "60.061001",
    best_bid: "60000.00",
    best_ask: "60001.00",
    expected_slippage_inputs: { method: "limit-vs-book-v1" },
    paper_order_preview_hash: "f".repeat(64),
  },
} as const;

test("PLAT-UI-APPROVAL renders only a complete closed canonical Paper preview", () => {
  assert.deepEqual(approvalActionIssues(canonicalApprovalView), []);

  const missingBid = structuredClone(canonicalApprovalView) as Record<string, any>;
  delete missingBid.paper_order_preview.best_bid;
  assert.ok(approvalActionIssues(missingBid).some((issue) => issue.includes("best_bid")));

  const unknownField = structuredClone(canonicalApprovalView) as Record<string, any>;
  unknownField.paper_order_preview.unrendered_financial_authority = "1";
  assert.ok(approvalActionIssues(unknownField).some((issue) => issue.includes("unknown")));

  const unknownNestedField = structuredClone(canonicalApprovalView) as Record<string, any>;
  unknownNestedField.paper_order_preview.expected_slippage_inputs.hidden_input = "1";
  assert.ok(approvalActionIssues(unknownNestedField).some((issue) => issue.includes("expected_slippage_inputs")));

  const mismatchedHash = structuredClone(canonicalApprovalView) as Record<string, any>;
  mismatchedHash.paper_order_preview.paper_order_preview_hash = "0".repeat(64);
  assert.ok(approvalActionIssues(mismatchedHash).some((issue) => issue.includes("hash")));
});

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

test("PLAT-001 serves the Phase 7 Trading Room routes without configuration disclosure", async () => {
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
    assert.match(body, /Woozoo Trading Room/);
    assert.match(body, /Paper only/i);
    assert.match(body, /Skip to trading room content/i);
    assert.doesNotMatch(body, /postgresql:|redis:|TRADING_MODE|testnet|api[_-]?key/i);

    const routes = [
      "/login",
      "/analysis/run-demo",
      "/proposals/proposal-demo",
      "/paper",
      "/audit",
      "/operations",
    ];
    for (const route of routes) {
      const routeResponse = await fetch(`http://127.0.0.1:${port}${route}`);
      assert.equal(routeResponse.status, 200, `${route} must be a real App Router route`);
      const routeBody = await routeResponse.text();
      assert.match(routeBody, /Woozoo Trading Room/);
      assert.match(routeBody, /HOLD|Sign in|Loading authoritative state/i);
      assert.doesNotMatch(routeBody, /postgresql:|redis:|TRADING_MODE|testnet|api[_-]?key/i);
    }
  } finally {
    await stopWeb(web);
  }
});
