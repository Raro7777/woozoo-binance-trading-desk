import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { createServer } from "node:net";
import { resolve } from "node:path";
import test from "node:test";

import { approvalActionIssues, renderedPreviewFields } from "../../apps/trading-room-web/src/lib/approval-preview";
import { approvalActionAvailability } from "../../apps/trading-room-web/src/components/approval-view";
import { localizedNarrative } from "../../apps/trading-room-web/src/components/analysis-view";
import { killSwitchPresentation } from "../../apps/trading-room-web/src/components/operations-console";
import { diagnosticLabel, statusLabel, statusTone } from "../../apps/trading-room-web/src/components/ui";

const root = resolve(import.meta.dirname, "../..");

test("PLAT-UI-STATUS translates Phase 7 failure states and fails unknown values closed", () => {
  const failures = ["ERROR", "UNHEALTHY", "INCOMPLETE", "INVALIDATED"] as const;
  for (const status of failures) {
    assert.doesNotMatch(statusLabel(status), new RegExp(`^${status}$`));
    assert.equal(statusTone(status), "danger");
  }
  assert.equal(statusLabel("PENDING_RISK"), "위험 판단 대기 중");
  assert.equal(statusLabel("PENDING_APPROVAL"), "승인 대기 중");
  assert.equal(statusLabel("AUTHORIZATION_ISSUED"), "실행 권한 발급됨");
  assert.equal(statusLabel("UNRECOGNIZED_STATE"), "알 수 없는 상태");
  assert.equal(statusTone("UNRECOGNIZED_STATE"), "danger");
  assert.equal(diagnosticLabel("UNRECOGNIZED_REASON"), "알 수 없는 진단 정보");
  assert.equal(killSwitchPresentation({}), "UNKNOWN");
  assert.equal(killSwitchPresentation({ status: "ACTIVE" }), "UNKNOWN");
  assert.equal(killSwitchPresentation({ status: "INACTIVE" }), "UNKNOWN");
  assert.equal(killSwitchPresentation({ active: true, status: "INACTIVE" }), "UNKNOWN");
  assert.equal(killSwitchPresentation({ active: false, status: "INACTIVE" }), "INACTIVE");
  assert.equal(killSwitchPresentation({ active: true, status: "ACTIVE" }), "ACTIVE");
  assert.equal(localizedNarrative("Untrusted English narrative"), "분석 서술을 한국어로 표시할 수 없습니다.");
  assert.equal(localizedNarrative("BTC 근거에 결합된 관찰입니다."), "BTC 근거에 결합된 관찰입니다.");
});

const canonicalApprovalView = {
  status: "READY",
  approval_action_allowed: true,
  approve_action_allowed: true,
  reject_action_allowed: true,
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

  const rendered = Object.fromEntries(
    renderedPreviewFields.map(([label, read]) => [label, read(canonicalApprovalView.paper_order_preview)]),
  );
  assert.equal(rendered["매수·매도"], "매수");
  assert.equal(rendered["주문 유형"], "지정가 주문");
  assert.equal(rendered["주문 유효 방식"], "취소할 때까지 유효");
  assert.equal(rendered["예상 슬리피지 방식"], "지정가와 최우선 호가 비교");

  const missingBid = structuredClone(canonicalApprovalView) as Record<string, any>;
  delete missingBid.paper_order_preview.best_bid;
  assert.ok(approvalActionIssues(missingBid).some((issue) => issue.includes("최우선 매수호가")));

  const unknownField = structuredClone(canonicalApprovalView) as Record<string, any>;
  unknownField.paper_order_preview.unrendered_financial_authority = "1";
  assert.ok(approvalActionIssues(unknownField).some((issue) => issue.includes("알 수 없는")));

  const unknownNestedField = structuredClone(canonicalApprovalView) as Record<string, any>;
  unknownNestedField.paper_order_preview.expected_slippage_inputs.hidden_input = "1";
  assert.ok(approvalActionIssues(unknownNestedField).some((issue) => issue.includes("예상 슬리피지 입력")));

  const mismatchedHash = structuredClone(canonicalApprovalView) as Record<string, any>;
  mismatchedHash.paper_order_preview.paper_order_preview_hash = "0".repeat(64);
  assert.ok(approvalActionIssues(mismatchedHash).some((issue) => issue.includes("해시")));
});

test("PLAT-UI-REJECT keeps the Korean Reject control usable when only worker readiness blocks Approve", () => {
  for (const reason of [
    "PAPER_WORKER_MISSING",
    "PAPER_WORKER_STALE",
    "PAPER_WORKER_FAILED",
    "PAPER_WORKER_STOPPED",
    "PAPER_WORKER_NOT_READY",
    "PAPER_WORKER_STATE_MISSING",
    "PAPER_WORKER_STATE_UNAVAILABLE",
    "PAPER_WORKER_STATE_INVALID",
  ]) {
    const workerBlocked = {
      ...canonicalApprovalView,
      status: "BLOCKED",
      approval_action_allowed: false,
      approve_action_allowed: false,
      reject_action_allowed: true,
      reason_codes: [reason],
    } as const;
    assert.deepEqual(approvalActionAvailability(workerBlocked), {
      approve: false,
      reject: true,
    });
    assert.notEqual(diagnosticLabel(reason), "알 수 없는 진단 정보");
  }
  const source = readFileSync(
    resolve(root, "apps/trading-room-web/src/components/approval-view.tsx"),
    "utf8",
  );
  assert.match(source, />거절<\/button>/);
});

test("PLAT-UI-DIALOGS use localized in-app forms and ship both App Router error boundaries", () => {
  const componentFiles = ["approval-view.tsx", "paper-desk.tsx", "operations-console.tsx"];
  for (const file of componentFiles) {
    const source = readFileSync(resolve(root, "apps/trading-room-web/src/components", file), "utf8");
    assert.doesNotMatch(source, /window\.(?:confirm|prompt)\s*\(/);
  }
  for (const file of ["error.tsx", "global-error.tsx"]) {
    const source = readFileSync(resolve(root, "apps/trading-room-web/src/app", file), "utf8");
    assert.match(source, /오류/);
    assert.match(source, /다시 시도/);
    assert.match(source, /뒤로/);
    assert.doesNotMatch(source, /Something went wrong|Try again|Go back/);
  }
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
    : spawn("corepack", argumentsForPnpm, {
        cwd: root,
        detached: true,
        stdio: ["ignore", "pipe", "pipe"],
      });
}

function processGroupExists(processGroupId: number): boolean {
  try {
    process.kill(-processGroupId, 0);
    return true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ESRCH") return false;
    throw error;
  }
}

async function stopWeb(processToStop: ReturnType<typeof startWeb>): Promise<void> {
  if (processToStop.pid === undefined) return;
  if (process.platform === "win32") {
    if (processToStop.exitCode !== null) return;
    spawnSync("taskkill", ["/PID", String(processToStop.pid), "/T", "/F"], { windowsHide: true });
  } else {
    // `corepack pnpm` launches Next as a descendant. Killing only the wrapper
    // leaves that server holding the stdout pipes open on Linux CI, so the
    // node:test process never exits. The detached process group gives this
    // fixture one precise, disposable target for teardown.
    try {
      process.kill(-processToStop.pid, "SIGTERM");
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ESRCH") throw error;
    }
  }
  for (
    let attempts = 0;
    attempts < 20 &&
    (process.platform === "win32"
      ? processToStop.exitCode === null
      : processGroupExists(processToStop.pid));
    attempts += 1
  ) {
    await delay(100);
  }
  if (process.platform !== "win32" && processGroupExists(processToStop.pid)) {
    try {
      process.kill(-processToStop.pid, "SIGKILL");
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ESRCH") throw error;
    }
    for (
      let attempts = 0;
      attempts < 20 && processGroupExists(processToStop.pid);
      attempts += 1
    ) {
      await delay(100);
    }
    assert.equal(processGroupExists(processToStop.pid), false, "web fixture process group did not terminate");
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
    assert.match(body, /우주 트레이딩룸/);
    assert.match(body, /모의투자 전용/);
    assert.match(body, /트레이딩룸 본문으로 건너뛰기/);
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
      assert.match(routeBody, /우주 트레이딩룸/);
      assert.match(routeBody, /보류|로그인|서버 확정 상태를 불러오는 중/);
      assert.doesNotMatch(routeBody, /postgresql:|redis:|TRADING_MODE|testnet|api[_-]?key/i);
    }
  } finally {
    await stopWeb(web);
  }
});
