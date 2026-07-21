import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page, type Route } from "@playwright/test";
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

function runLiveControl(...args: string[]) {
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
  const paperUrl = paperDatabaseUrl();
  return execFileSync("python", [
    "-m", "uv", "run", "--locked", "python", "tests/e2e/live_control_api.py",
    ...args,
  ], {
    cwd: root,
    env: {
      ...process.env,
      TRADING_MODE: "paper",
      PAPER_DATABASE_URL: paperUrl,
      AGENT_DATABASE_URL: paperUrl.replace("woozoo_paper_engine", "woozoo_agent_orchestrator"),
      RISK_DATABASE_URL: paperUrl.replace("woozoo_paper_engine", "woozoo_risk_engine"),
      MARKET_DATABASE_URL: paperUrl.replace("woozoo_paper_engine", "woozoo_market_writer"),
      EVIDENCE_DATABASE_URL: paperUrl.replace("woozoo_paper_engine", "woozoo_evidence_writer"),
      PYTHONPATH: pythonPath,
    },
    encoding: "utf8",
  });
}

function createSellAnalysis(
  symbol: "BTCUSDT" | "ETHUSDT",
  nonCrossingBuySymbol?: "BTCUSDT" | "ETHUSDT",
  nonCrossingSellSymbol?: "BTCUSDT" | "ETHUSDT",
): { runId: string; proposalId: string } {
  const protection = nonCrossingBuySymbol === undefined
    ? []
    : ["--non-crossing-buy-book", nonCrossingBuySymbol];
  const sellProtection = nonCrossingSellSymbol === undefined
    ? []
    : ["--non-crossing-sell-book", nonCrossingSellSymbol];
  const output = runLiveControl(
    "--create-sell-analysis",
    symbol,
    ...protection,
    ...sellProtection,
  );
  const runMatch = output.match(/E2E_SELL_RUN_ID:([a-f0-9]{64})/);
  const proposalMatch = output.match(/E2E_SELL_PROPOSAL_ID:([a-f0-9]{64})/);
  if (runMatch === null) throw new Error(`E2E_SELL_RUN_ID_MISSING:${output}`);
  if (proposalMatch === null) throw new Error(`E2E_SELL_PROPOSAL_ID_MISSING:${output}`);
  return { runId: runMatch[1], proposalId: proposalMatch[1] };
}

function refreshEvidence(
  symbol: "BTCUSDT" | "ETHUSDT",
  nonCrossingBuySymbol?: "BTCUSDT" | "ETHUSDT",
  nonCrossingSellSymbol?: "BTCUSDT" | "ETHUSDT",
) {
  const protection = nonCrossingBuySymbol === undefined
    ? []
    : ["--non-crossing-buy-book", nonCrossingBuySymbol];
  const sellProtection = nonCrossingSellSymbol === undefined
    ? []
    : ["--non-crossing-sell-book", nonCrossingSellSymbol];
  runLiveControl("--refresh-evidence", symbol, ...protection, ...sellProtection);
}

function recordPartialBook(symbol: "BTCUSDT" | "ETHUSDT") {
  runLiveControl("--record-partial-book", symbol);
}

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("로컬 운영자 비밀번호").fill("paper-only-password");
  await page.getByRole("button", { name: "안전하게 로그인" }).click();
  await expect(page).toHaveURL(/\/$/);
}

async function focusByKeyboard(
  page: Page,
  target: Locator,
  accessibleName: string,
  direction: "forward" | "backward" = "forward",
) {
  await expect(target).toHaveAccessibleName(accessibleName);
  const key = direction === "forward" ? "Tab" : "Shift+Tab";
  for (let step = 0; step < 80; step += 1) {
    await page.keyboard.press(key);
    if (await target.evaluate((element) => element === document.activeElement)) {
      await expect(target).toBeFocused();
      return;
    }
  }
  throw new Error(`KEYBOARD_FOCUS_UNREACHABLE:${accessibleName}:${direction}`);
}

const approvalView = {
  status: "READY",
  view_version: 17,
  proposal_id: "a".repeat(64),
  proposal_hash: "b".repeat(64),
  approval_ttl_seconds: 300,
  approval_expires_at: null,
  approval_action_allowed: true,
  approve_action_allowed: true,
  reject_action_allowed: true,
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
    if (path === "/api/v1/analysis-runs/run-001") return json(route, { run_id: "run-001", status: "SUCCEEDED", provider: "mock", evidence: { evidence_id: "evidence-1", digest: "sha256:evidence", as_of: "2026-07-20T12:00:00Z", knowledge_cutoff: "2026-07-20T12:00:00Z" }, report: { claims: [{ claim_id: "claim-1", text: "근거에 결합된 관찰입니다." }, { claim_id: "claim-2", text: "Untrusted English narrative" }] }, proposal: { proposal_id: "proposal-001", proposal_hash: "sha256:proposal-bound", status: "CREATED" } });
    if (path === "/api/v1/proposals/proposal-001/approval-view") return json(route, approvalView);
    if (path === "/api/v1/paper-portfolio") return json(route, portfolio);
    if (path === "/api/v1/audit-events") return json(route, { events: [{ event_id: "event-1", occurred_at: "2026-07-20T12:00:00Z", event_type: "paper.order.partially-filled.v1", aggregate_id: "paper-order-1", actor_id: "system", outcome: "RECORDED" }], next_cursor: "cursor-2" });
    if (path === "/api/v1/kill-switch") return json(route, { status: "INACTIVE", active: false, version: 4, data_status: "HEALTHY", reconciliation_status: "HEALTHY", ledger_status: "BALANCED", cancellation_status: "NOT_ACTIVE", recovery_allowed: false });
    return json(route, { detail: "Not found" }, 404);
  });
}

test("[live] E2E-001 HTTPS approval, automatic Paper partial fill, and operator cancellation", async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  const symbol = testInfo.project.name === "mobile-chromium" ? "ETHUSDT" : "BTCUSDT";
  const analysisButton = symbol === "BTCUSDT" ? "BTC / USDT 분석" : "ETH / USDT 분석";
  await page.goto("/login");
  expect(page.url()).toMatch(/^https:\/\/localhost:/);
  await page.getByLabel("로컬 운영자 비밀번호").fill("paper-only-password");
  await page.getByRole("button", { name: "안전하게 로그인" }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByLabel("로컬 운영자 비밀번호")).toHaveCount(0);
  const initialPortfolioResponse = await page.request.get("/api/v1/paper-portfolio");
  expect(initialPortfolioResponse.ok()).toBe(true);
  const initialPortfolio = await initialPortfolioResponse.json() as { orders?: Array<{ order_id?: string }> };
  const initialOrders = initialPortfolio.orders ?? [];
  const initialOrderIds = new Set(initialOrders.map((order) => order.order_id));
  refreshEvidence(
    symbol,
    undefined,
    testInfo.project.name === "mobile-chromium" ? "BTCUSDT" : undefined,
  );
  await page.reload();
  await expect(page.getByText("정상", { exact: true }).first()).toBeVisible();
  const expectedBtcPrice = testInfo.project.name === "mobile-chromium"
    ? "59000.000000000000000000"
    : "60000.000000000000000000";
  await expect(page.getByText(expectedBtcPrice, { exact: true }).first()).toBeVisible();
  await expect(page.getByText("3000.000000000000000000", { exact: true }).first()).toBeVisible();
  await expect(page.locator(".field", { hasText: "워터마크" }).first()).toContainText("#");
  await expect(page.locator(".field", { hasText: "연결" }).first()).not.toContainText("정보 없음");
  const analysisResponsePromise = page.waitForResponse((response) =>
    response.url().endsWith("/api/v1/analysis-runs") && response.request().method() === "POST"
  );
  await page.getByRole("button", { name: analysisButton }).click();
  const analysisResponse = await analysisResponsePromise;
  expect([200, 201]).toContain(analysisResponse.status());
  await expect(page).toHaveURL(/\/analysis\//);
  await page.getByRole("link", { name: "승인 화면 열기" }).click();
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
    "종목", "매수·매도", "주문 유형", "주문 유효 방식", "수량", "지정가",
    "최악 조건 수수료", "최악 조건 보유액", "최악 조건 명목금액", "최우선 매수호가", "최우선 매도호가",
    "예상 슬리피지 방식", "내장 미리보기 해시", "결합된 미리보기 해시",
  ]) {
    await expect(page.getByText(label, { exact: true })).toBeVisible();
  }
  await expect(page.getByRole("button", { name: "정확한 미리보기 승인" })).toBeEnabled();
  const approvalCommandPromise = page.waitForResponse((response) =>
    response.url().endsWith("/api/v1/paper-approvals") && response.request().method() === "POST"
  );
  await page.getByRole("button", { name: "정확한 미리보기 승인" }).click();
  await expect(page.getByRole("dialog", { name: "정확한 모의주문 승인" })).toBeVisible();
  await page.getByRole("button", { name: "승인 제출" }).click();
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
  const finalPortfolio = await finalPortfolioResponse.json() as {
    orders?: Array<{
      order_id?: string;
      status?: string;
      symbol?: string;
      quantity?: string;
      filled_quantity?: string;
      version?: number;
    }>;
  };
  const finalOrders = finalPortfolio.orders ?? [];
  expect(finalOrders).toHaveLength(initialOrders.length + 1);
  const createdOrder = finalOrders.find((order) =>
    order.symbol === symbol && order.status === "OPEN" && !initialOrderIds.has(order.order_id)
  );
  expect(createdOrder?.order_id).toMatch(/^[a-f0-9]{64}$/);
  const orderId = createdOrder?.order_id as string;

  recordPartialBook(symbol);
  await expect.poll(async () => {
    const response = await page.request.get(`/api/v1/paper-orders/${orderId}`);
    if (!response.ok()) return `HTTP_${response.status()}`;
    return ((await response.json()) as { status?: string }).status;
  }, { timeout: 20_000 }).toBe("PARTIALLY_FILLED");
  const partialOrder = await page.request.get(`/api/v1/paper-orders/${orderId}`).then((response) => response.json()) as {
    quantity: string;
    filled_quantity: string;
    status: string;
    version: number;
  };
  expect(Number(partialOrder.filled_quantity)).toBeGreaterThan(0);
  expect(Number(partialOrder.filled_quantity)).toBeLessThan(Number(partialOrder.quantity));

  await page.goto("/paper");
  await expect(page.getByRole("table").first().locator("tbody tr")).toHaveCount(finalOrders.length);
  const orderRow = page.getByRole("row").filter({ hasText: orderId });
  await expect(orderRow).toContainText(symbol);
  await expect(orderRow).toContainText("부분 체결");
  await expect(orderRow).toContainText(partialOrder.filled_quantity);
  // Keep the mobile order partially open so the later operations project
  // proves that Kill cancels a real outstanding effect. The desktop journey
  // below remains the live operator-cancellation proof for E2E-001.
  if (testInfo.project.name === "mobile-chromium") {
    await expect(orderRow.getByRole("button", { name: "취소", exact: true })).toBeEnabled();
    await expect.poll(async () => {
      const response = await page.request.get("/api/v1/paper-portfolio");
      if (!response.ok()) return { status: `HTTP_${response.status()}` };
      const state = await response.json() as {
        orders?: Array<{ order_id?: string; status?: string; filled_quantity?: string }>;
        reconciliation_status?: string;
        checkpoint_authority_sequence?: number;
        current_authority_sequence?: number;
      };
      const outstanding = state.orders?.find((order) => order.order_id === orderId);
      return {
        status: outstanding?.status,
        filled_quantity: outstanding?.filled_quantity,
        reconciled: state.reconciliation_status === "HEALTHY"
          && state.checkpoint_authority_sequence === state.current_authority_sequence,
      };
    }, { timeout: 20_000 }).toEqual({
      status: "PARTIALLY_FILLED",
      filled_quantity: partialOrder.filled_quantity,
      reconciled: true,
    });
    return;
  }
  const cancelResponsePromise = page.waitForResponse((response) =>
    response.url().endsWith(`/api/v1/paper-orders/${orderId}/cancel`)
    && response.request().method() === "POST"
  );
  await orderRow.getByRole("button", { name: "취소", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "모의주문 취소" })).toBeVisible();
  await page.getByRole("button", { name: "취소 제출" }).click();
  const cancelResponse = await cancelResponsePromise;
  expect(cancelResponse.ok()).toBe(true);
  expect(await cancelResponse.json()).toMatchObject({ result: "ORDER_CANCELLED", status: "CANCELLED" });
  await expect(orderRow).toContainText("취소됨");
  await expect.poll(async () => {
    const response = await page.request.get("/api/v1/paper-portfolio");
    if (!response.ok()) return { status: `HTTP_${response.status()}` };
    const state = await response.json() as {
      orders?: Array<{ order_id?: string; status?: string; filled_quantity?: string }>;
      reconciliation_status?: string;
      checkpoint_authority_sequence?: number;
      current_authority_sequence?: number;
    };
    const cancelled = state.orders?.find((order) => order.order_id === orderId);
    return {
      status: cancelled?.status,
      filled_quantity: cancelled?.filled_quantity,
      reconciled: state.reconciliation_status === "HEALTHY"
        && state.checkpoint_authority_sequence === state.current_authority_sequence,
    };
  }, { timeout: 20_000 }).toEqual({
    status: "CANCELLED",
    filled_quantity: partialOrder.filled_quantity,
    reconciled: true,
  });
});

test("[live] E2E-002 stale Evidence blocks analysis before a Proposal is actionable", async ({ page }) => {
  await login(page);
  const responsePromise = page.waitForResponse((response) =>
    response.url().endsWith("/api/v1/analysis-runs") && response.request().method() === "POST"
  );
  await page.getByRole("button", { name: "ETH / USDT 분석" }).click();
  const response = await responsePromise;
  try {
    expect(response.status()).toBe(409);
    const body = await response.json() as { detail?: { code?: string } | string };
    expect(JSON.stringify(body)).toMatch(/EVIDENCE_UNAVAILABLE|healthy Evidence is unavailable/i);
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByText(/정상적인 근거 데이터를 사용할 수 없습니다/)).toBeVisible();
  } finally {
    refreshEvidence("ETHUSDT");
  }
});

test("[live] E2E-003 retry is idempotent and mismatched approval has zero authoritative effects", async ({ page }) => {
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
  await page.getByRole("link", { name: "승인 화면 열기" }).click();
  await expect(page).toHaveURL(/\/proposals\//);
  const proposalApi = page.url().replace("/proposals/", "/api/v1/proposals/");
  const beforeApproval = await page.request.get(`${proposalApi}/approval-view`).then((response) => response.json()) as {
    proposal_id: string;
    view_version: number;
    paper_order_preview_hash: string;
    approval_id?: string | null;
    authorization_id?: string | null;
  };
  expect(beforeApproval.approval_id ?? null).toBeNull();
  expect(beforeApproval.authorization_id ?? null).toBeNull();
  const beforePortfolio = await page.request.get("/api/v1/paper-portfolio").then((response) => response.json()) as {
    orders?: unknown[];
    ledger_checkpoint_id?: string;
    ledger_version?: number;
  };
  const mismatch = await page.evaluate(async ({ proposalId, version }) => {
    const session = await fetch("/api/v1/session").then((response) => response.json()) as { csrf_token: string };
    const response = await fetch("/api/v1/paper-approvals", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "idempotency-key": crypto.randomUUID(),
        "if-match": String(version),
        "x-csrf-token": session.csrf_token,
      },
      body: JSON.stringify({
        decision: "APPROVE",
        proposal_id: proposalId,
        paper_order_preview_hash: "0".repeat(64),
        expected_version: version,
        reason: "OPERATOR_APPROVED_EXACT_PREVIEW",
      }),
    });
    return { status: response.status, body: await response.json() };
  }, { proposalId: beforeApproval.proposal_id, version: beforeApproval.view_version });
  expect(mismatch.status).toBe(409);
  // The Postgres approval authority classifies a preview-binding drift as
  // APPROVAL_NOT_READY at the public boundary; either code is fail-closed.
  expect(JSON.stringify(mismatch.body)).toMatch(/PREVIEW_HASH_MISMATCH|APPROVAL_NOT_READY/);

  const afterApproval = await page.request.get(`${proposalApi}/approval-view`).then((response) => response.json()) as {
    approval_id?: string | null;
    approval_status?: string | null;
    authorization_id?: string | null;
    authorization_status?: string | null;
  };
  expect(afterApproval).toMatchObject({
    approval_id: null,
    approval_status: null,
    authorization_id: null,
    authorization_status: null,
  });
  const afterPortfolio = await page.request.get("/api/v1/paper-portfolio").then((response) => response.json()) as {
    orders?: unknown[];
    ledger_checkpoint_id?: string;
    ledger_version?: number;
  };
  expect(afterPortfolio.orders?.length ?? 0).toBe(beforePortfolio.orders?.length ?? 0);
  expect(afterPortfolio.ledger_checkpoint_id).toBe(beforePortfolio.ledger_checkpoint_id);
  expect(afterPortfolio.ledger_version).toBe(beforePortfolio.ledger_version);
});

test("[live] E2E-004 Kill activation cancels open orders and guarded recovery completes", async ({ page }) => {
  await login(page);
  // Do not append a new book before Kill: the outstanding mobile E2E-001
  // order is the cancellation precondition, and any fresh executable book
  // would correctly be consumed by the background Paper worker first.
  const beforePortfolio = await page.request.get("/api/v1/paper-portfolio").then((response) => response.json()) as {
    ledger_checkpoint_id?: string;
    orders?: Array<{ status?: string }>;
  };
  expect(beforePortfolio.orders?.some((order) =>
    order.status === "OPEN" || order.status === "PARTIALLY_FILLED"
  )).toBe(true);
  await page.goto("/operations");
  const activationResponsePromise = page.waitForResponse((response) =>
    response.url().endsWith("/api/v1/kill-switch/activate") && response.request().method() === "POST"
  );
  await page.getByRole("button", { name: "킬 스위치 활성화" }).click();
  const activationDialog = page.getByRole("dialog", { name: "킬 스위치 활성화" });
  await activationDialog.getByLabel("사고 사유").fill("검증된 브라우저 시험 사고");
  await activationDialog.getByRole("button", { name: "활성화 제출" }).click();
  const activationResponse = await activationResponsePromise;
  if (!activationResponse.ok()) throw new Error(`KILL_ACTIVATION_${activationResponse.status()}:${await activationResponse.text()}`);
  await expect(page.getByText("활성", { exact: true }).first()).toBeVisible();
  await page.goto("/paper");
  await expect(page.getByText("취소됨", { exact: true }).first()).toBeVisible();
  await expect.poll(async () => {
    const response = await page.request.get("/api/v1/paper-portfolio");
    const portfolioState = await response.json() as { ledger_checkpoint_id?: string };
    return portfolioState.ledger_checkpoint_id;
  }, { timeout: 20_000 }).not.toBe(beforePortfolio.ledger_checkpoint_id);
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
  for (const status of ["완료", "정상", "균형"]) {
    await expect(page.getByText(status, { exact: true }).first()).toBeVisible();
  }
  await expect(page.getByRole("button", { name: "해결 검증 후 복구" })).toBeEnabled();
  await page.getByRole("button", { name: "해결 검증 후 복구" }).click();
  const recoveryDialog = page.getByRole("dialog", { name: "킬 스위치 복구" });
  await recoveryDialog.getByLabel("검증된 사고 해결 내용").fill("공개 자료와 대사 및 원장 상태 검증 완료");
  await recoveryDialog.getByRole("button", { name: "복구 제출" }).click();
  await expect(page.getByText("비활성", { exact: true }).first()).toBeVisible();
});

test("[live] E2E-005 desktop and mobile journey is keyboard accessible, Axe-clean, and audit-safe", async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  // E2E-001 acquires a real partial base-asset position. The accessibility
  // journey uses the opposite SELL side, so each viewport exercises a fresh
  // approval without weakening the 15-minute same-side duplicate guard.
  const symbol = testInfo.project.name === "mobile-chromium" ? "ETHUSDT" : "BTCUSDT";
  const assertAccessible = async (path: string) => {
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    const results = await new AxeBuilder({ page }).analyze();
    const material = results.violations.filter((violation) => violation.impact === "serious" || violation.impact === "critical");
    expect(material, `${testInfo.project.name} ${path} serious/critical accessibility violations`).toEqual([]);
    expect(await page.locator("body").evaluate((body) => body.scrollWidth <= window.innerWidth)).toBe(true);
  };

  await page.goto("/login");
  const password = page.getByLabel("로컬 운영자 비밀번호");
  const loginButton = page.getByRole("button", { name: "안전하게 로그인" });
  await focusByKeyboard(page, password, "로컬 운영자 비밀번호");
  await page.keyboard.type("paper-only-password");
  await focusByKeyboard(page, loginButton, "안전하게 로그인");
  await page.keyboard.press("Shift+Tab");
  await expect(password).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(loginButton).toBeFocused();
  await Promise.all([
    page.waitForURL(/\/$/),
    page.keyboard.press("Enter"),
  ]);

  await assertAccessible("/");
  const skipLink = page.getByRole("link", { name: "트레이딩룸 본문으로 건너뛰기" });
  await focusByKeyboard(page, skipLink, "트레이딩룸 본문으로 건너뛰기");
  await page.keyboard.press("Enter");
  await expect(page.locator("#trading-room-content")).toBeFocused();

  let protectedEthOrder: { order_id?: string; filled_quantity?: string } | undefined;
  if (testInfo.project.name === "mobile-chromium") {
    const beforeRefresh = await page.request.get("/api/v1/paper-portfolio");
    expect(beforeRefresh.ok()).toBe(true);
    const beforeState = await beforeRefresh.json() as {
      orders?: Array<{ order_id?: string; symbol?: string; status?: string; filled_quantity?: string }>;
    };
    protectedEthOrder = beforeState.orders?.find((order) =>
      order.symbol === "ETHUSDT" && order.status === "PARTIALLY_FILLED"
    );
    expect(protectedEthOrder?.order_id).toMatch(/^[a-f0-9]{64}$/);
  }
  refreshEvidence(
    symbol,
    testInfo.project.name === "mobile-chromium" ? "ETHUSDT" : undefined,
    testInfo.project.name === "mobile-chromium" ? "BTCUSDT" : undefined,
  );
  if (protectedEthOrder?.order_id !== undefined) {
    await expect.poll(async () => {
      const response = await page.request.get("/api/v1/paper-portfolio");
      if (!response.ok()) return { status: `HTTP_${response.status()}` };
      const state = await response.json() as {
        orders?: Array<{ order_id?: string; status?: string; filled_quantity?: string }>;
        reconciliation_status?: string;
        checkpoint_authority_sequence?: number;
        current_authority_sequence?: number;
      };
      const order = state.orders?.find((candidate) =>
        candidate.order_id === protectedEthOrder?.order_id
      );
      return {
        status: order?.status,
        filled_quantity: order?.filled_quantity,
        reconciled: state.reconciliation_status === "HEALTHY"
          && state.checkpoint_authority_sequence === state.current_authority_sequence,
      };
    }, { timeout: 20_000 }).toEqual({
      status: "PARTIALLY_FILLED",
      filled_quantity: protectedEthOrder.filled_quantity,
      reconciled: true,
    });
  }

  const sellAnalysis = createSellAnalysis(
    symbol,
    testInfo.project.name === "mobile-chromium" ? "ETHUSDT" : undefined,
    testInfo.project.name === "mobile-chromium" ? "BTCUSDT" : undefined,
  );
  await page.goto(`/analysis/${sellAnalysis.runId}`);
  await expect(page).toHaveURL(/\/analysis\//);
  await expect(page.getByText("저장된 모의 언어 모델의 모의투자 분석", { exact: true })).toBeVisible();
  await expect(page.getByText("생성됨", { exact: true })).toBeVisible();
  await expect(page.getByText("모의 제공자", { exact: true })).toBeVisible();
  await assertAccessible(page.url());
  const approvalLink = page.getByRole("link", { name: "승인 화면 열기" });
  await focusByKeyboard(page, approvalLink, "승인 화면 열기");
  await Promise.all([
    page.waitForURL(/\/proposals\//),
    page.keyboard.press("Enter"),
  ]);
  await assertAccessible(page.url());

  // Axe and keyboard traversal intentionally take longer than the production
  // five-second public-book freshness window. Build a second immutable
  // proposal and Risk snapshot from freshly drained authority immediately
  // before approval instead of weakening that fail-closed boundary.
  refreshEvidence(
    symbol,
    testInfo.project.name === "mobile-chromium" ? "ETHUSDT" : undefined,
    testInfo.project.name === "mobile-chromium" ? "BTCUSDT" : undefined,
  );
  const freshSellAnalysis = createSellAnalysis(
    symbol,
    testInfo.project.name === "mobile-chromium" ? "ETHUSDT" : undefined,
    testInfo.project.name === "mobile-chromium" ? "BTCUSDT" : undefined,
  );
  await page.goto(`/proposals/${freshSellAnalysis.proposalId}`);
  await expect(page).toHaveURL(/\/proposals\//);

  const approveButton = page.getByRole("button", { name: "정확한 미리보기 승인" });
  const approvalViewResponse = await page.request.get(
    page.url().replace("/proposals/", "/api/v1/proposals/") + "/approval-view",
  );
  expect(approvalViewResponse.ok()).toBe(true);
  const approvalState = await approvalViewResponse.json() as {
    approval_action_allowed?: boolean;
    approve_action_allowed?: boolean;
    reject_action_allowed?: boolean;
    reason_codes?: string[];
    risk_decision_id?: string;
    status?: string;
  };
  expect(approvalState).toMatchObject({
    status: "READY",
    approval_action_allowed: true,
    approve_action_allowed: true,
    reject_action_allowed: true,
  });
  await expect(approveButton).toBeEnabled();
  await focusByKeyboard(page, approveButton, "정확한 미리보기 승인");
  await page.keyboard.press("Space");
  const approvalDialog = page.getByRole("dialog", { name: "정확한 모의주문 승인" });
  await expect(approvalDialog).toBeVisible();
  const submitApproval = approvalDialog.getByRole("button", { name: "승인 제출" });
  await focusByKeyboard(page, submitApproval, "승인 제출");
  const approvalResponsePromise = page.waitForResponse((response) =>
    response.url().endsWith("/api/v1/paper-approvals") && response.request().method() === "POST"
  );
  await page.keyboard.press("Enter");
  expect((await approvalResponsePromise).ok()).toBe(true);
  const approvalApi = page.url().replace("/proposals/", "/api/v1/proposals/") + "/approval-view";
  await expect.poll(async () => {
    const response = await page.request.get(approvalApi);
    if (!response.ok()) return `HTTP_${response.status()}`;
    return ((await response.json()) as { authorization_status?: string }).authorization_status;
  }, { timeout: 20_000 }).toBe("CONSUMED");
  await expect.poll(async () => {
    const response = await page.request.get("/api/v1/paper-portfolio");
    if (!response.ok()) return false;
    const state = await response.json() as {
      reconciliation_status?: string;
      checkpoint_authority_sequence?: number;
      current_authority_sequence?: number;
    };
    return state.reconciliation_status === "HEALTHY"
      && state.checkpoint_authority_sequence === state.current_authority_sequence;
  }, { timeout: 20_000 }).toBe(true);

  const followNavigation = async (
    accessibleName: string,
    path: RegExp,
    direction: "forward" | "backward" = "forward",
  ) => {
    const link = page.getByRole("link", { name: accessibleName, exact: true });
    await focusByKeyboard(page, link, accessibleName, direction);
    await Promise.all([
      page.waitForURL(path),
      page.keyboard.press("Enter"),
    ]);
    await assertAccessible(page.url());
  };

  await followNavigation("모의투자 데스크", /\/paper$/);
  await followNavigation("운영", /\/operations$/, "backward");
  await followNavigation("트레이딩룸", /\/$/);
  await followNavigation("감사 기록", /\/audit$/);

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
  await expect(page.getByRole("columnheader", { name: "생성 주체" })).toBeVisible();
});

test("[ui-only] UI-001 renders an explicit HOLD when authoritative API state is unavailable", async ({ page }) => {
  await page.route("**/api/v1/**", (route) => json(route, { detail: "Authoritative projection unavailable" }, 503));
  await page.goto("/");
  await expect(page.getByText("보류", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/어떤 작업도 수행할 수 없습니다/).first()).toBeVisible();
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
  await page.goto("/proposals/proposal-001");
  await expect(page.locator(".field", { hasText: "최악 조건 보유액" }).getByText("68.191573450000000000", { exact: true })).toBeVisible();
  await expect(page.getByText("b".repeat(64), { exact: true })).toBeVisible();
  await expect(page.getByText("d".repeat(64), { exact: true })).toBeVisible();
  await expect(page.getByText("300", { exact: true })).toBeVisible();
  await expect(page.getByText("매수", { exact: true })).toBeVisible();
  await expect(page.getByText("지정가 주문", { exact: true })).toBeVisible();
  await expect(page.getByText("취소할 때까지 유효", { exact: true })).toBeVisible();
  await expect(page.getByText("지정가와 최우선 호가 비교", { exact: true })).toBeVisible();
  await expect(page.getByText("BUY", { exact: true })).toHaveCount(0);
  await expect(page.getByText("LIMIT", { exact: true })).toHaveCount(0);
  await expect(page.getByText("GTC", { exact: true })).toHaveCount(0);
  await expect(page.getByText("limit-vs-book-v1", { exact: true })).toHaveCount(0);
  await expect(page.locator("[data-canonical-preview]")).toHaveAttribute("data-canonical-preview", /"side":"BUY"/);
  await page.getByRole("button", { name: "정확한 미리보기 승인" }).click();
  await page.getByRole("dialog", { name: "정확한 모의주문 승인" }).getByRole("button", { name: "승인 제출" }).click();
  await expect(page.getByText("서버 확정 명령 처리 결과를 기다리는 중…")).toBeVisible();
  await expect(page.getByText(/결정이 접수되었습니다/)).toBeVisible();
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
  await page.goto("/paper");
  await expect(page.getByText("0.00040000", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "취소" }).click();
  await page.getByRole("dialog", { name: "모의주문 취소" }).getByRole("button", { name: "취소 제출" }).click();
  await expect(page.getByText("취소 중…")).toBeVisible();
  await expect(page.getByText(/취소가 접수되었습니다/)).toBeVisible();
  await page.goto("/operations");
  await page.getByRole("button", { name: "킬 스위치 활성화" }).click();
  const activationDialog = page.getByRole("dialog", { name: "킬 스위치 활성화" });
  await activationDialog.getByRole("button", { name: "활성화 제출" }).click();
  await expect(activationDialog.getByRole("alert")).toHaveText("사유를 입력하세요.");
  await activationDialog.getByLabel("사고 사유").fill("운영자 검증 사고");
  await activationDialog.getByRole("button", { name: "활성화 제출" }).click();
  await expect(page.getByText("서버 확정 명령 처리 결과를 기다리는 중…")).toBeVisible();
  await expect(page.getByText(/명령이 접수되었습니다/)).toBeVisible();
});

test("[ui-only] UI-004 has keyboard-reachable navigation and zero serious or critical Axe findings", async ({ page }, testInfo) => {
  await mockControlApi(page);
  for (const path of ["/", "/login", "/analysis/run-001", "/proposals/proposal-001", "/paper", "/audit", "/operations"]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    if (path === "/analysis/run-001") {
      await expect(page.getByText("근거에 결합된 관찰입니다.", { exact: true })).toBeVisible();
      await expect(page.getByText("분석 서술을 한국어로 표시할 수 없습니다.", { exact: true })).toBeVisible();
      await expect(page.getByText("Untrusted English narrative", { exact: true })).toHaveCount(0);
      await expect(page.getByText("생성됨", { exact: true })).toBeVisible();
      await expect(page.getByText("모의 제공자", { exact: true })).toBeVisible();
    }
    if (path === "/audit") {
      await expect(page.getByText("모의주문 부분 체결", { exact: true })).toBeVisible();
    }
    const results = await new AxeBuilder({ page }).analyze();
    const material = results.violations.filter((violation) => violation.impact === "serious" || violation.impact === "critical");
    expect(material, `${testInfo.project.name} ${path} serious/critical accessibility violations`).toEqual([]);
    await expect(page.locator("body")).not.toContainText(/Secure|HttpOnly|SameSite|CSRF|Origin|Decimal|\bAI\b/);
  }
  await page.goto("/");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "트레이딩룸 본문으로 건너뛰기" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#trading-room-content")).toBeFocused();
  expect(await page.locator("body").evaluate((body) => body.scrollWidth <= window.innerWidth)).toBe(true);
});

test("[ui-only] UI-005 localizes transport failures for reads and commands", async ({ page }) => {
  await page.goto("/login");
  await page.getByRole("button", { name: "안전하게 로그인" }).click();
  await expect(page.getByRole("status")).toHaveText("비밀번호를 입력하세요.");
  await expect(page.locator("body")).not.toContainText("Please fill out this field");

  await page.route("**/api/v1/**", (route) => route.abort("failed"));
  await page.goto("/");
  await expect(page.getByText(/제어 API에 연결할 수 없습니다/).first()).toBeVisible();
  await expect(page.getByText(/Failed to fetch/)).toHaveCount(0);

  await page.unroute("**/api/v1/**");
  await mockControlApi(page);
  await page.route("**/api/v1/analysis-runs", (route) => route.abort("failed"));
  await page.reload();
  await page.getByRole("button", { name: "BTC / USDT 분석" }).click();
  await expect(page.getByText(/제어 API에 연결할 수 없습니다/).first()).toBeVisible();
  await expect(page.getByText(/Failed to fetch/)).toHaveCount(0);
});

test("[ui-only] UI-006 renders Korean route and root error recovery screens", async ({ page }, testInfo) => {
  await page.goto("/analysis/e2e-render-error");
  await expect(page.getByRole("heading", { name: "화면을 표시하는 중 오류가 발생했습니다" })).toBeVisible();
  await expect(page.getByRole("button", { name: "다시 시도" })).toBeVisible();
  await expect(page.getByRole("button", { name: "뒤로" })).toBeVisible();
  let results = await new AxeBuilder({ page }).analyze();
  expect(
    results.violations.filter((violation) => violation.impact === "serious" || violation.impact === "critical"),
    `${testInfo.project.name} route error accessibility violations`,
  ).toEqual([]);

  await page.setExtraHTTPHeaders({ "x-woozoo-e2e-global-error": "enabled" });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "서비스 화면에 오류가 발생했습니다" })).toBeVisible();
  await expect(page.getByRole("button", { name: "다시 시도" })).toBeVisible();
  await expect(page.getByRole("button", { name: "뒤로" })).toBeVisible();
  results = await new AxeBuilder({ page }).analyze();
  expect(
    results.violations.filter((violation) => violation.impact === "serious" || violation.impact === "critical"),
    `${testInfo.project.name} root error accessibility violations`,
  ).toEqual([]);
  expect(await page.locator("body").evaluate((body) => body.scrollWidth <= window.innerWidth)).toBe(true);
});
