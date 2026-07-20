import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import test from "node:test";

import type {
  PaperDomainEventBindingV1,
  PaperOrderBindingV1,
} from "../../packages/typescript/contract-bindings/src/index.js";

test("Phase 4 Paper contracts are closed and dormant until Phase 7", async () => {
  const order: PaperOrderBindingV1 = {
    order_id: "a".repeat(64), client_order_id: "client-1", authorization_id: "fixture-1",
    authorization_namespace: "test", symbol: "BTCUSDT", side: "BUY",
    order_type: "LIMIT", time_in_force: "GTC", quantity: "1.000000000000000000",
    limit_price: "100.000000000000000000", filled_quantity: "0.000000000000000000",
    status: "OPEN", version: 1,
  };
  const event: PaperDomainEventBindingV1 = {
    spec_version: "woozoo.event/v1", event_id: "b".repeat(64),
    event_type: "paper.order.accepted.v1", event_version: 1,
    occurred_at: "2026-07-19T00:00:00Z", producer: "paper-engine",
    activation_phase: 7, aggregate_id: order.order_id, aggregate_version: 1,
    payload_hash: "c".repeat(64), data: { order_id: order.order_id },
  };
  assert.equal(event.activation_phase, 7);
  const eventTypes: PaperDomainEventBindingV1["event_type"][] = [
    "paper.order.accepted.v1", "paper.order.partially-filled.v1",
    "paper.order.filled.v1", "paper.order.cancelled.v1",
    "paper.order.rejected.v1", "paper.authorization.attempted.v1",
    "ledger.transaction.posted.v1",
  ];
  assert.equal(eventTypes.length, 7);
  const schema = JSON.parse(await readFile(resolve(import.meta.dirname, "../../packages/contracts/spec/paper-order.v1.json"), "utf8")) as Record<string, any>;
  const registry = JSON.parse(await readFile(resolve(import.meta.dirname, "../../packages/contracts/spec/paper-domain-events.v1.json"), "utf8")) as {
    oneOf: unknown[];
    "x-activation-phase": number;
    $defs: { ledgerData: { properties: { transaction_id: { pattern: string } } } };
  };
  const openapi = JSON.parse(await readFile(resolve(import.meta.dirname, "../../packages/contracts/spec/openapi.v1.json"), "utf8")) as { paths: Record<string, unknown> };
  assert.equal(schema.additionalProperties, false);
  assert.equal(schema["x-activation-phase"], 7);
  assert.equal(registry.oneOf.length, 7);
  assert.equal(registry["x-activation-phase"], 7);
  assert.equal(Object.keys(openapi.paths).some((path) => path.includes("paper")), true);
  assert.equal(Object.keys(openapi.paths).some((path) => path.includes("authorizations")), false);
  assert.equal(Object.keys(openapi.paths).some((path) => path.includes("/internal/")), false);
  const positiveDecimal = new RegExp(schema.$defs.positiveDecimal.pattern);
  assert.equal(positiveDecimal.test("9".repeat(20) + "." + "1".repeat(18)), true);
  assert.equal(positiveDecimal.test("1".repeat(21)), false);
  assert.equal(positiveDecimal.test("1." + "1".repeat(19)), false);
  const clientOrderId = new RegExp(schema.properties.client_order_id.pattern);
  const authorizationId = new RegExp(schema.properties.authorization_id.pattern);
  assert.equal(clientOrderId.test("client-1"), true);
  assert.equal(clientOrderId.test("주문-1"), false);
  assert.equal(authorizationId.test("fixture-1"), true);
  const transactionId = new RegExp(registry.$defs.ledgerData.properties.transaction_id.pattern);
  assert.equal(transactionId.test("d".repeat(64)), true);
  assert.equal(transactionId.test("ledger-\uC6D0\uC7A5"), false);
  assert.equal(authorizationId.test("승인"), false);
});
