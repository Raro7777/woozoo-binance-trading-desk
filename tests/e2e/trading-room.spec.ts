import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Route } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { delimiter, resolve } from "node:path";

const root = resolve(import.meta.dirname, "../..");
const composeFile = resolve(root, "tests/e2e/compose.yaml");
const composeProject = "woozoo-e2e";

function paperDatabaseUrl(): string {
  const mapping = execFileSync("docker", [
    "compose", "-f", composeFile, "-p", composeProject, "port", "postgres", "5432",
  ], { cwd: root, encoding: "utf8" }).trim();
  const match = mapping.match(/:(\d+)$/);
  if (match === null) throw new Error("Cannot resolve the disposable E2E Postgres port");
  return `postgresql://woozoo_paper_engine@127.0.0.1:${match[1]}/woozoo`;
}

function refreshEvidence(symbol: "BTCUSDT" | "ETHUSDT") {
  const pythonPath = [
    resolve(root, "packages/python/platform-core/src"),
    resolve(root, "services/control-api/src"),
    resolve(root, "services/evidence-worker/src"),
    resolve(root, "services/paper-engine/src"),
    resolve(root, "services/risk-engine/src"),
    resolve(root, "services/agent-orchestrator/src"),
    resolve(root, "services/market-data-worker/src"),
    process.env.PYTHONPATH,
  ].filter(Boolean).join(delimiter);
  execFileSync("python", [
    "-m", "uv", "run", "--locked", "python", "tests/e2e/live_control_api.py",
    "--refresh-evidence", symbol,
  ], {
    cwd: root,
    env: {
      ...process.env,
      TRADING_MODE: "paper",
      MARKET_DATABASE_URL: paperDatabaseUrl().replace("woozoo_paper_engine", "woozoo_market_writer"),
      EVIDENCE_DATABASE_URL: paperDatabaseUrl().replace("woozoo_paper_engine", "woozoo_evidence_writer"),
      PYTHONPATH: pythonPath,
    },
    stdio: "inherit",
  });
}

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Local operator password").fill("paper-only-password");
  await page.getByRole("button", { name: "Sign in securely" }).click();
  await expect(page).toHaveURL(/\/$/);
}

const approvalView = {
  status: "READY",
  view_version: 17,
  proposal_id: "a".repeat(64),
  proposal_hash: "b".repeat(64),
  approval_ttl_seconds: 300,
  approval_expires_at: null,
  approval_action_allowed: true,
  risk_decision_id: "c".repeat(64),
  risk_decision_hash: "d".repeat(64),
  risk_input_digest: "e".repeat(64),
  risk_policy_version: "woozoo.risk.paper/v7",
  risk_verdict: "ALLOWED",
  reason_codes: [],
  paper_order_preview_hash: "f".repeat(64),
  paper_order_preview: {
    symbol: "BTCUSDT",
    side: "BUY",
    order_type: "LIMIT",
    quantity: "0.00100000",
    limit_price: "68123.45000000",
    time_in_force: "GTC",
    worst_case_fee: "0.068123450000000000",
    worst_case_hold: "68.191573450000000000",
    worst_case_notional: "68.191573450000000000",
    best_bid: "68123.44000000",
    best_ask: "68123.45000000",
    expected_slippage_inputs: { method: "limit-vs-book-v1" },
    paper_order_preview_hash: "f".repeat(64),
  },
  approval_id: null,
  approval_status: null,
  authorization_id: null,
  authorization_status: null,
  served_at: "2026-07-20T12:00:00Z",
};

const portfolio = {
  status: "HEALTHY",
  reconciliation_status: "HEALTHY",
  paper_account_id: "paper-local-1",
  as_of: "2026-07-20T12:00:00Z",
  ledger_checkpoint_id: "checkpoint-19",
  balances: [
    { asset: "USDT", available: "10000.000000000000000000", held: "68.191573450000000000", total: "10068.191573450000000000" },
    { asset: "BTC", available: "0.12500000", held: "0.00100000", total: "0.12600000" },
  ],
  orders: [{ order_id: "paper-order-1", symbol: "BTCUSDT", side: "BUY", quantity: "0.00100000", limit_price: "68123.45000000", filled_quantity: "0.00040000", status: "PARTIALLY_FILLED", version: 3 }],
  command_receipts: [{ command_id: "command-1", status: "ACCEPTED", reason: "ORDER_CREATED" }],
};

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function mockControlApi(page: Page, commandDelay = 0) {
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (route.request().method() === "POST") {
      if (commandDelay > 0) await new Promise((resolve) => setTimeout(resolve, commandDelay));
      await json(route, { status: "ACCEPTED", message: "Authoritative command receipt recorded." });
      return;
    }
    if (path === "/api/v1/session") return json(route, { status: "AUTHENTICATED", csrf_token: "one-time-test-token" });
    if (path.includes("/markets/")) return json(route, {
      api_version: "v1",
      request_id: "ui-request",
      correlation_id: "ui-correlation",
      served_at: "2026-07-20T12:00:00Z",
      data: {
        symbol: path.includes("ETHUSDT") ? "ETHUSDT" : "BTCUSDT",
        price: path.includes("ETHUSDT") ? "3000.00" : "60000.00",
        event_time: "2026-07-20T12:00:00Z",
        received_at: "2026-07-20T12:00:00Z",
        quality: "healthy",
        quality_reasons: [],
        watermark: { session_id: "market-session-1", stream: "bookTicker", last_sequence: 19, observed_at: "2026-07-20T12:00:00Z" },
      },
      meta: { resource_version: null, next_cursor: null },
    });
    if (path === "/api/v1/analysis-runs/run-001") return json(route, { run_id: "run-001", status: "SUCCEEDED", provider: "mock", evidence: { evidence_id: "evidence-1", digest: "sha256:evidence", as_of: "2026-07-20T12:00:00Z", knowledge_cutoff: "2026-07-20T12:00:00Z" }, report: { claims: [{ claim_id: "claim-1", text: "Evidence-bound observation." }] }, proposal: { proposal_id: "proposal-001", proposal_hash: "sha256:proposal-bound", status: "CREATED" } });
    if (path === "/api/v1/proposals/proposal-001/approval-view") return json(route, approvalView);
    if (path === "/api/v1/paper-portfolio") return json(route, portfolio);
    if (path === "/api/v1/audit-events") return json(route, { events: [{ event_id: "event-1", occurred_at: "2026-07-20T12:00:00Z", event_type: "paper.order.partially-filled.v1", aggregate_id: "paper-order-1", actor_id: "system", outcome: "RECORDED" }], next_cursor: "cursor-2" });
    if (path === "/api/v1/kill-switch") return json(route, { status: "INACTIVE", active: false, version: 4, data_status: "HEALTHY", reconciliation_status: "HEALTHY", ledger_status: "BALANCED", cancellation_status: "NOT_ACTIVE", recovery_allowed: false });
    return json(route, { detail: "Not found" }, 404);
  });
}

test("[live] E2E-001 HTTPS login, analysis, approval, automatic authorization worker, and one Paper order", async ({ page }, testInfo) => {
  test.setTimeout(60_000);
  const symbol = testInfo.project.name === "mobile-chromium" ? "ETHUSDT" : "BTCUSDT";
  const analysisButton = symbol === "BTCUSDT" ? "Analyze BTC / USDT" : "Analyze ETH / USDT";
  page.on("dialog", (dialog) => dialog.accept());
  await page.goto("/login");
  expect(page.url()).toMatch(/^https:\/\/localhost:/);
  await page.getByLabel("Local operator password").fill("paper-only-password");
  await page.getByRole("button", { name: "Sign in securely" }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByLabel("Local operator password")).toHaveCount(0);
  const initialPortfolioResponse = await page.request.get("/api/v1/paper-portfolio");
  expect(initialPortfolioResponse.ok()).toBe(true);
  const initialPortfolio = await initialPortfolioResponse.json() as { orders?: Array<Record<string, unknown>> };
  const initialOrderCount = initialPortfolio.orders?.length ?? 0;
  refreshEvidence(symbol);
  await page.reload();
  await expect(page.getByText("healthy", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("60000.000000000000000000", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("3000.000000000000000000", { exact: true }).first()).toBeVisible();
  await expect(page.locator(".field", { hasText: "Watermark" }).first()).toContainText("#");
  await expect(page.locator(".field", { hasText: "Connection" }).first()).not.toContainText("Not available");
  const analysisResponsePromise = page.waitForResponse((response) =>
    response.url().endsWith("/api/v1/analysis-runs") && response.request().method() === "POST"
  );
  await page.getByRole("button", { name: analysisButton }).click();
  const analysisResponse = await analysisResponsePromise;
  expect([200, 201]).toContain(analysisResponse.status());
  await expect(page).toHaveURL(/\/analysis\//);
  await page.getByRole("link", { name: "Open approval view" }).click();
  await expect(page).toHaveURL(/\/proposals\//);
  const approvalResponse = await page.request.get(
    page.url().replace("/proposals/", "/api/v1/proposals/") + "/approval-view",
  );
  expect(approvalResponse.ok()).toBe(true);
  const approval = await approvalResponse.json() as {
    risk_decision_id: string;
    risk_verdict: string;
    paper_order_preview: Record<string, unknown>;
  };
  if (approval.risk_verdict !== "ALLOWED") {
    const riskResponse = await page.request.get(`/api/v1/risk-decisions/${approval.risk_decision_id}`);
    expect(riskResponse.ok()).toBe(true);
    const risk = await riskResponse.json() as { ordered_reason_codes?: string[] };
    throw new Error(`E2E_RISK_NOT_ALLOWED:${(risk.ordered_reason_codes ?? []).join(",")}`);
  }
  const expectedPreviewKeys = [
    "symbol", "side", "order_type", "time_in_force", "quantity", "limit_price",
    "worst_case_fee", "worst_case_hold", "worst_case_notional", "best_bid", "best_ask",
    "expected_slippage_inputs", "paper_order_preview_hash",
  ];
  expect(Object.keys(approval.paper_order_preview).sort()).toEqual([...expectedPreviewKeys].sort());
  for (const label of [
    "Symbol", "Side", "Order type", "Time in force", "Quantity", "Limit price",
    "Worst-case fee", "Worst-case hold", "Worst-case notional", "Best bid", "Best ask",
    "Expected slippage method", "Embedded preview hash", "Bound preview hash",
  ]) {
    await expect(page.getByText(label, { exact: true })).toBeVisible();
  }
  await expect(page.getByRole("button", { name: "Approve exact preview" })).toBeEnabled();
  const approvalCommandPromise = page.waitForResponse((response) =>
    response.url().endsWith("/api/v1/paper-approvals") && response.request().method() === "POST"
  );
  await page.getByRole("button", { name: "Approve exact preview" }).click();
  const approvalCommand = await approvalCommandPromise;
  expect(approvalCommand.ok()).toBe(true);
  expect(await approvalCommand.json()).toMatchObject({ result: "AUTHORIZATION_ISSUED" });
  await expect.poll(async () => {
    const response = await page.request.get(page.url().replace("/proposals/", "/api/v1/proposals/") + "/approval-view");
    if (!response.ok()) return `HTTP_${response.status()}`;
    const view = await response.json() as { authorization_status?: string };
    return view.authorization_status;
  }).toBe("CONSUMED");
  const finalPortfolioResponse = await page.request.get("/api/v1/paper-portfolio");
  expect(finalPortfolioResponse.ok()).toBe(true);
  const finalPortfolio = await finalPortfolioResponse.json() as { orders?: Array<{ status?: string; symbol?: string }> };
  const finalOrders = finalPortfolio.orders ?? [];
  expect(finalOrders).toHaveLength(initialOrderCount + 1);
  expect(finalOrders.some((order) => order.symbol === symbol && order.status === "OPEN")).toBe(true);
  await page.goto("/paper");
  await expect(page.getByRole("table").first().locator("tbody tr")).toHaveCount(finalOrders.length);
  await expect(page.getByText(symbol, { exact: true })).toBeVisible();
  await expect(page.getByText("OPEN", { exact: true })).toHaveCount(
    finalOrders.filter((order) => order.status === "OPEN").length,
  );
});

test("[live] E2E-002 stale Evidence blocks analysis before a Proposal is actionable", async ({ page }) => {
  await login(page);
  const responsePromise = page.waitForResponse((response) =>
    response.url().endsWith("/api/v1/analysis-runs") && response.request().method() === "POST"
  );
  await page.getByRole("button", { name: "Analyze ETH / USDT" }).click();
  const response = await responsePromise;
  try {
    expect(response.status()).toBe(409);
    const body = await response.json() as { detail?: { code?: string } | string };
    expect(JSON.stringify(body)).toMatch(/EVIDENCE_UNAVAILABLE|healthy Evidence is unavailable/i);
    await expect(page).toHaveURL(/\/$/);
  } finally {
    refreshEvidence("ETHUSDT");
  }
});

test("[live] E2E-003 idempotent analysis retry and reload preserve one authoritative run", async ({ page }) => {
  await login(page);
  refreshEvidence("ETHUSDT");
  const idempotencyKey = `e2e-retry-${test.info().project.name}`;
  const create = async () => page.evaluate(async (key) => {
    const session = await fetch("/api/v1/session").then((response) => response.json()) as { csrf_token: string };
    const response = await fetch("/api/v1/analysis-runs", {
      method: "POST",
      headers: { "content-type": "application/json", "idempotency-key": key, "x-csrf-token": session.csrf_token },
      body: JSON.stringify({ symbol: "ETHUSDT" }),
    });
    return { status: response.status, body: await response.json() };
  }, idempotencyKey);
  const first = await create();
  const retry = await create();
  expect([200, 201]).toContain(first.status);
  expect(retry.status).toBe(200);
  expect(retry.body.run_id).toBe(first.body.run_id);
  await page.goto(`/analysis/${first.body.run_id}`);
  await page.reload();
  await expect(page.getByText(first.body.run_id, { exact: true })).toBeVisible();
});

test("[live] E2E-004 Kill activation cancels open orders and guarded recovery completes", async ({ page }) => {
  await login(page);
  refreshEvidence("BTCUSDT");
  page.on("dialog", (dialog) => dialog.accept(dialog.type() === "prompt" ? "verified-e2e-incident" : undefined));
  const beforePortfolio = await page.request.get("/api/v1/paper-portfolio").then((response) => response.json()) as { ledger_checkpoint_id?: string };
  await page.goto("/operations");
  const activationResponsePromise = page.waitForResponse((response) =>
    response.url().endsWith("/api/v1/kill-switch/activate") && response.request().method() === "POST"
  );
  await page.getByRole("button", { name: "Activate Kill Switch" }).click();
  const activationResponse = await activationResponsePromise;
  if (!activationResponse.ok()) throw new Error(`KILL_ACTIVATION_${activationResponse.status()}:${await activationResponse.text()}`);
  await expect(page.getByText("ACTIVE", { exact: true }).first()).toBeVisible();
  await page.goto("/paper");
  await expect(page.getByText("CANCELLED", { exact: true }).first()).toBeVisible();
  await expect.poll(async () => {
    const response = await page.request.get("/api/v1/paper-portfolio");
    const portfolioState = await response.json() as { ledger_checkpoint_id?: string };
    return portfolioState.ledger_checkpoint_id;
  }).not.toBe(beforePortfolio.ledger_checkpoint_id);
  // Recovery authority requires both public books to be healthy and no more
  // than five seconds old at the command boundary.
  refreshEvidence("BTCUSDT");
  await expect.poll(async () => {
    const response = await page.request.get("/api/v1/kill-switch");
    return await response.json() as Record<string, unknown>;
  }).toMatchObject({
    active: true,
    cancellation_status: "COMPLETE",
    open_order_count: 0,
    data_status: "HEALTHY",
    reconciliation_status: "HEALTHY",
    ledger_status: "BALANCED",
    recovery_allowed: true,
  });
  await page.goto("/operations");
  for (const status of ["COMPLETE", "HEALTHY", "BALANCED"]) {
    await expect(page.getByText(status, { exact: true }).first()).toBeVisible();
  }
  await expect(page.getByRole("button", { name: "Recover after verified resolution" })).toBeEnabled();
  await page.getByRole("button", { name: "Recover after verified resolution" }).click();
  await expect(page.getByText("INACTIVE", { exact: true }).first()).toBeVisible();
});

test("[live] E2E-005 browser audit preserves provenance and recursively excludes secret material", async ({ page }) => {
  await login(page);
  const response = await page.request.get("/api/v1/audit-events");
  expect(response.ok()).toBe(true);
  const body = await response.json() as { events: Array<Record<string, unknown>> };
  expect(body.events.length).toBeGreaterThan(0);
  const forbidden: string[] = [];
  const visit = (value: unknown, path = "event") => {
    if (Array.isArray(value)) return value.forEach((item, index) => visit(item, `${path}[${index}]`));
    if (value === null || typeof value !== "object") return;
    for (const [key, nested] of Object.entries(value as Record<string, unknown>)) {
      if (/(nonce|binding|session|csrf|origin|credential|password|secret)/i.test(key)) forbidden.push(`${path}.${key}`);
      visit(nested, `${path}.${key}`);
    }
  };
  body.events.forEach((event) => {
    expect(typeof event.producer).toBe("string");
    expect(event.actor_id === null || typeof event.actor_id === "string").toBe(true);
    visit(event);
  });
  expect(forbidden).toEqual([]);
  await page.goto("/audit");
  await expect(page.getByRole("columnheader", { name: "Producer" })).toBeVisible();
});

test("[ui-only] UI-001 renders an explicit HOLD when authoritative API state is unavailable", async ({ page }) => {
  await page.route("**/api/v1/**", (route) => json(route, { detail: "Authoritative projection unavailable" }, 503));
  await page.goto("/");
  await expect(page.getByText("HOLD", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/No action is available/).first()).toBeVisible();
});

test("[ui-only] UI-002 displays verbatim Decimal bindings and waits for approval receipt", async ({ page }) => {
  let approvalBody = "";
  let csrf = "";
  let ifMatch = "";
  await mockControlApi(page, 300);
  await page.route("**/api/v1/paper-approvals", async (route) => {
    approvalBody = route.request().postData() ?? "";
    csrf = route.request().headers()["x-csrf-token"] ?? "";
    ifMatch = route.request().headers()["if-match"] ?? "";
    await new Promise((resolve) => setTimeout(resolve, 300));
    await json(route, { status: "ACCEPTED", message: "Approval recorded." });
  });
  page.on("dialog", (dialog) => dialog.accept());
  await page.goto("/proposals/proposal-001");
  await expect(page.locator(".field", { hasText: "Worst-case hold" }).getByText("68.191573450000000000", { exact: true })).toBeVisible();
  await expect(page.getByText("b".repeat(64), { exact: true })).toBeVisible();
  await expect(page.getByText("d".repeat(64), { exact: true })).toBeVisible();
  await expect(page.getByText("300", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Approve exact preview" }).click();
  await expect(page.getByText("Awaiting authoritative command receipt…")).toBeVisible();
  await expect(page.getByText("Approval recorded.")).toBeVisible();
  expect(csrf).toBe("one-time-test-token");
  expect(ifMatch).toBe("17");
  const posted = JSON.parse(approvalBody) as Record<string, unknown>;
  expect(posted.actor_id).toBeUndefined();
  expect(posted.expires_at).toBeUndefined();
  expect(posted.proposal_hash).toBeUndefined();
  expect(posted.paper_order_preview_hash).toBe("f".repeat(64));
  expect(posted.expected_version).toBe(17);
  expect(posted.command_id).toBeUndefined();
  expect(posted.risk_decision_hash).toBeUndefined();
});

test("[ui-only] UI-003 keeps Paper cancellation and Kill controls receipt-driven", async ({ page }) => {
  await mockControlApi(page, 200);
  page.on("dialog", (dialog) => dialog.accept("Operator verified incident"));
  await page.goto("/paper");
  await expect(page.getByText("0.00040000", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByText("Cancelling…")).toBeVisible();
  await expect(page.getByText("Authoritative command receipt recorded.")).toBeVisible();
  await page.goto("/operations");
  await page.getByRole("button", { name: "Activate Kill Switch" }).click();
  await expect(page.getByText("Awaiting authoritative command receipt…")).toBeVisible();
  await expect(page.getByText("Authoritative command receipt recorded.")).toBeVisible();
});

test("[ui-only] UI-004 has keyboard-reachable navigation and zero serious or critical Axe findings", async ({ page }, testInfo) => {
  await mockControlApi(page);
  for (const path of ["/", "/analysis/run-001", "/proposals/proposal-001", "/paper", "/audit", "/operations"]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    const results = await new AxeBuilder({ page }).analyze();
    const material = results.violations.filter((violation) => violation.impact === "serious" || violation.impact === "critical");
    expect(material, `${testInfo.project.name} ${path} serious/critical accessibility violations`).toEqual([]);
  }
  await page.goto("/");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to trading room content" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#trading-room-content")).toBeFocused();
  expect(await page.locator("body").evaluate((body) => body.scrollWidth <= window.innerWidth)).toBe(true);
});
