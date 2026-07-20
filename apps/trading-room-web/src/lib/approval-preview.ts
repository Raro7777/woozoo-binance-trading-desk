import { asRecord, type JsonRecord } from "./api";

const HASH = /^[a-f0-9]{64}$/;

const canonicalPreviewKeys = new Set([
  "symbol",
  "side",
  "order_type",
  "time_in_force",
  "quantity",
  "limit_price",
  "worst_case_fee",
  "worst_case_hold",
  "worst_case_notional",
  "best_bid",
  "best_ask",
  "expected_slippage_inputs",
  "paper_order_preview_hash",
]);

export const renderedPreviewFields: ReadonlyArray<
  readonly [label: string, value: (preview: JsonRecord) => string | undefined]
> = [
  ["Symbol", (preview) => stringField(preview, "symbol")],
  ["Side", (preview) => stringField(preview, "side")],
  ["Order type", (preview) => stringField(preview, "order_type")],
  ["Time in force", (preview) => stringField(preview, "time_in_force")],
  ["Quantity", (preview) => stringField(preview, "quantity")],
  ["Limit price", (preview) => stringField(preview, "limit_price")],
  ["Worst-case fee", (preview) => stringField(preview, "worst_case_fee")],
  ["Worst-case hold", (preview) => stringField(preview, "worst_case_hold")],
  ["Worst-case notional", (preview) => stringField(preview, "worst_case_notional")],
  ["Best bid", (preview) => stringField(preview, "best_bid")],
  ["Best ask", (preview) => stringField(preview, "best_ask")],
  ["Expected slippage method", (preview) => stringField(asRecord(preview.expected_slippage_inputs), "method")],
  ["Embedded preview hash", (preview) => stringField(preview, "paper_order_preview_hash")],
];

function stringField(record: JsonRecord | undefined, key: string): string | undefined {
  const value = record?.[key];
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function requireHash(view: JsonRecord, key: string, issues: string[]): void {
  const value = stringField(view, key);
  if (value === undefined || !HASH.test(value)) issues.push(`${key} is missing or invalid`);
}

export function approvalActionIssues(value: unknown): readonly string[] {
  const view = asRecord(value);
  if (view === undefined) return ["approval view is unavailable"];
  const issues: string[] = [];
  const preview = asRecord(view.paper_order_preview);
  if (preview === undefined) return ["paper_order_preview is missing or invalid"];

  const actualKeys = Object.keys(preview);
  for (const key of canonicalPreviewKeys) {
    if (!actualKeys.includes(key)) issues.push(`paper_order_preview.${key} is missing`);
  }
  for (const key of actualKeys) {
    if (!canonicalPreviewKeys.has(key)) issues.push(`paper_order_preview contains unknown field ${key}`);
  }
  for (const [label, read] of renderedPreviewFields) {
    if (read(preview) === undefined) issues.push(`${label} is not renderable`);
  }

  if (!(["BTCUSDT", "ETHUSDT"] as const).includes(stringField(preview, "symbol") as "BTCUSDT" | "ETHUSDT")) {
    issues.push("paper_order_preview.symbol is unknown");
  }
  if (!(["BUY", "SELL"] as const).includes(stringField(preview, "side") as "BUY" | "SELL")) {
    issues.push("paper_order_preview.side is unknown");
  }
  if (preview.order_type !== "LIMIT") issues.push("paper_order_preview.order_type is unknown");
  if (preview.time_in_force !== "GTC") issues.push("paper_order_preview.time_in_force is unknown");

  const slippage = asRecord(preview.expected_slippage_inputs);
  if (
    slippage === undefined
    || Object.keys(slippage).length !== 1
    || slippage.method !== "limit-vs-book-v1"
  ) {
    issues.push("paper_order_preview.expected_slippage_inputs is missing, unknown, or unrendered");
  }

  requireHash(view, "proposal_id", issues);
  requireHash(view, "proposal_hash", issues);
  requireHash(view, "risk_decision_id", issues);
  requireHash(view, "risk_decision_hash", issues);
  requireHash(view, "risk_input_digest", issues);
  requireHash(view, "paper_order_preview_hash", issues);
  if (stringField(view, "risk_policy_version") === undefined) issues.push("risk_policy_version is missing");
  if (preview.paper_order_preview_hash !== view.paper_order_preview_hash) {
    issues.push("paper_order_preview hash binding does not match");
  }
  return [...new Set(issues)];
}
