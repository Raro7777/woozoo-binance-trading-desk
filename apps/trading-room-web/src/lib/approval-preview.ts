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

const previewFieldLabels: Readonly<Record<string, string>> = {
  symbol: "종목",
  side: "매수·매도",
  order_type: "주문 유형",
  time_in_force: "주문 유효 방식",
  quantity: "수량",
  limit_price: "지정가",
  worst_case_fee: "최악 조건 수수료",
  worst_case_hold: "최악 조건 보유액",
  worst_case_notional: "최악 조건 명목금액",
  best_bid: "최우선 매수호가",
  best_ask: "최우선 매도호가",
  expected_slippage_inputs: "예상 슬리피지 입력",
  paper_order_preview_hash: "모의주문 미리보기 해시",
};

function closedLabel(value: string | undefined, labels: Readonly<Record<string, string>>, kind: string): string | undefined {
  if (value === undefined) return undefined;
  return labels[value] ?? `알 수 없는 ${kind}`;
}

export const renderedPreviewFields: ReadonlyArray<
  readonly [label: string, value: (preview: JsonRecord) => string | undefined]
> = [
  ["종목", (preview) => stringField(preview, "symbol")],
  ["매수·매도", (preview) => closedLabel(stringField(preview, "side"), { BUY: "매수", SELL: "매도" }, "매수·매도 값")],
  ["주문 유형", (preview) => closedLabel(stringField(preview, "order_type"), { LIMIT: "지정가 주문" }, "주문 유형")],
  ["주문 유효 방식", (preview) => closedLabel(stringField(preview, "time_in_force"), { GTC: "취소할 때까지 유효" }, "주문 유효 방식")],
  ["수량", (preview) => stringField(preview, "quantity")],
  ["지정가", (preview) => stringField(preview, "limit_price")],
  ["최악 조건 수수료", (preview) => stringField(preview, "worst_case_fee")],
  ["최악 조건 보유액", (preview) => stringField(preview, "worst_case_hold")],
  ["최악 조건 명목금액", (preview) => stringField(preview, "worst_case_notional")],
  ["최우선 매수호가", (preview) => stringField(preview, "best_bid")],
  ["최우선 매도호가", (preview) => stringField(preview, "best_ask")],
  ["예상 슬리피지 방식", (preview) => closedLabel(stringField(asRecord(preview.expected_slippage_inputs), "method"), { "limit-vs-book-v1": "지정가와 최우선 호가 비교" }, "예상 슬리피지 방식")],
  ["내장 미리보기 해시", (preview) => stringField(preview, "paper_order_preview_hash")],
];

function stringField(record: JsonRecord | undefined, key: string): string | undefined {
  const value = record?.[key];
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function requireHash(view: JsonRecord, key: string, issues: string[]): void {
  const value = stringField(view, key);
  const labels: Readonly<Record<string, string>> = {
    proposal_id: "제안 ID",
    proposal_hash: "제안 해시",
    risk_decision_id: "위험 판단 ID",
    risk_decision_hash: "위험 판단 해시",
    risk_input_digest: "위험 입력 다이제스트",
    paper_order_preview_hash: "모의주문 미리보기 해시",
  };
  if (value === undefined || !HASH.test(value)) issues.push(`${labels[key] ?? "필수 해시"} 값이 없거나 유효하지 않습니다`);
}

export function approvalActionIssues(value: unknown): readonly string[] {
  const view = asRecord(value);
  if (view === undefined) return ["승인 화면을 사용할 수 없습니다"];
  const issues: string[] = [];
  const preview = asRecord(view.paper_order_preview);
  if (preview === undefined) return ["모의주문 미리보기 값이 없거나 유효하지 않습니다"];

  const actualKeys = Object.keys(preview);
  for (const key of canonicalPreviewKeys) {
    if (!actualKeys.includes(key)) issues.push(`모의주문 미리보기의 ${previewFieldLabels[key] ?? "필수 항목"} 값이 없습니다`);
  }
  for (const key of actualKeys) {
    if (!canonicalPreviewKeys.has(key)) issues.push("모의주문 미리보기에 알 수 없는 항목이 있습니다");
  }
  for (const [label, read] of renderedPreviewFields) {
    if (read(preview) === undefined) issues.push(`${label} 값을 표시할 수 없습니다`);
  }

  if (!(["BTCUSDT", "ETHUSDT"] as const).includes(stringField(preview, "symbol") as "BTCUSDT" | "ETHUSDT")) {
    issues.push("모의주문 미리보기의 종목을 알 수 없습니다");
  }
  if (!(["BUY", "SELL"] as const).includes(stringField(preview, "side") as "BUY" | "SELL")) {
    issues.push("모의주문 미리보기의 매수·매도 값을 알 수 없습니다");
  }
  if (preview.order_type !== "LIMIT") issues.push("모의주문 미리보기의 주문 유형을 알 수 없습니다");
  if (preview.time_in_force !== "GTC") issues.push("모의주문 미리보기의 주문 유효 방식을 알 수 없습니다");

  const slippage = asRecord(preview.expected_slippage_inputs);
  if (
    slippage === undefined
    || Object.keys(slippage).length !== 1
    || slippage.method !== "limit-vs-book-v1"
  ) {
    issues.push("모의주문 미리보기의 예상 슬리피지 입력이 없거나 알 수 없거나 표시되지 않았습니다");
  }

  requireHash(view, "proposal_id", issues);
  requireHash(view, "proposal_hash", issues);
  requireHash(view, "risk_decision_id", issues);
  requireHash(view, "risk_decision_hash", issues);
  requireHash(view, "risk_input_digest", issues);
  requireHash(view, "paper_order_preview_hash", issues);
  if (stringField(view, "risk_policy_version") === undefined) issues.push("위험 정책 버전 값이 없습니다");
  if (preview.paper_order_preview_hash !== view.paper_order_preview_hash) {
    issues.push("모의주문 미리보기 해시 결합이 일치하지 않습니다");
  }
  return [...new Set(issues)];
}
