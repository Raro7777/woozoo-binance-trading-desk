import { asRecord, type JsonRecord } from "./api";

const hashPattern = /^[a-f0-9]{64}$/;
const clientOrderPattern = /^wz8-[a-f0-9]{32}$/;
const decimalPattern = /^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/;

export function stringList(record: JsonRecord, key: string): readonly string[] {
  const value = record[key];
  return Array.isArray(value) && value.every((item) => typeof item === "string")
    ? value
    : [];
}

export function testnetOperatorState(value: unknown): JsonRecord | undefined {
  const record = asRecord(value);
  if (
    record?.environment !== "BINANCE_SPOT_TESTNET"
    || typeof record.account_generation !== "number"
    || !hashPattern.test(String(record.account_binding_id ?? ""))
    || !hashPattern.test(String(record.activation_view_digest ?? ""))
    || typeof record.new_commands_allowed !== "boolean"
    || !["DISABLED", "PENDING", "ACTIVE", "EXPIRED", "REVOKED"].includes(String(record.activation_status))
    || !["ACTIVE", "INACTIVE"].includes(String(record.testnet_barrier_status))
  ) return undefined;
  return record;
}

export function testnetApprovalView(value: unknown): JsonRecord | undefined {
  const record = asRecord(value);
  const preview = asRecord(record?.testnet_order_preview);
  if (
    record?.environment !== "BINANCE_SPOT_TESTNET"
    || !hashPattern.test(String(record.proposal_id ?? ""))
    || !hashPattern.test(String(record.testnet_order_preview_digest ?? ""))
    || !hashPattern.test(String(record.approval_input_digest ?? ""))
    || !["ALLOWED", "DENIED", "ERROR"].includes(String(record.risk_verdict))
    || preview?.schema_version !== "woozoo.testnet-order-preview/v1"
    || preview.environment !== "BINANCE_SPOT_TESTNET"
    || !["BTCUSDT", "ETHUSDT"].includes(String(preview.symbol))
    || !["BUY", "SELL"].includes(String(preview.side))
    || preview.order_type !== "LIMIT"
    || preview.time_in_force !== "GTC"
    || !decimalPattern.test(String(preview.quantity ?? ""))
    || !decimalPattern.test(String(preview.limit_price ?? ""))
    || !clientOrderPattern.test(String(preview.client_order_id ?? ""))
  ) return undefined;
  return record;
}

export function newIntentKey(current: string | undefined): string {
  return current ?? crypto.randomUUID();
}
