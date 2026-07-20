import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import test from "node:test";

import type {
  KillSwitchBindingV1,
  RiskDecisionBindingV1,
  RiskDomainEventBindingV1,
} from "../../packages/typescript/contract-bindings/src/index.js";

test("Phase 5 Risk contracts are closed and dormant until Phase 7", async () => {
  const decision: RiskDecisionBindingV1 = {
    decision_schema_version: "woozoo.risk-decision/v1",
    decision_id: "a".repeat(64), risk_input_digest: "b".repeat(64),
    decision_hash: "c".repeat(64), verdict: "DENIED",
    primary_reason: "DATA_STALE", ordered_reason_codes: ["DATA_STALE"],
    policy_version: "woozoo.risk-policy/v1", proposal_hash: "d".repeat(64),
    portfolio_snapshot_hash: "e".repeat(64), data_state_hash: "f".repeat(64),
    paper_order_preview_hash: "1".repeat(64),
    reconciliation_checkpoint_hash: "2".repeat(64), kill_switch_version: 0,
    decision_as_of: "2026-07-19T00:00:00Z",
  };
  const kill: KillSwitchBindingV1 = {
    scope: "paper-global", active: true, prior_version: 0, version: 1,
    activation_event_id: "3".repeat(64), trigger_kind: "INVARIANT",
    actor_id: "safety-service", reason_code: "LEDGER_IMBALANCE",
    reason: "Commodity journal imbalance", observed_at: "2026-07-19T00:00:00Z",
    context_digest: "6".repeat(64),
  };
  const event: RiskDomainEventBindingV1 = {
    spec_version: "woozoo.event/v1", event_id: "4".repeat(64),
    event_type: "risk.decision.recorded.v1", event_version: 1,
    occurred_at: decision.decision_as_of, producer: "risk-engine", activation_phase: 7,
    aggregate_id: decision.decision_id, aggregate_version: 1,
    payload_hash: "5".repeat(64), data: decision,
  };
  assert.equal(event.activation_phase, 7);
  assert.equal(kill.active, true);

  const names = ["risk-input", "risk-decision", "kill-switch"];
  for (const name of names) {
    const schema = JSON.parse(await readFile(resolve(import.meta.dirname, `../../packages/contracts/spec/${name}.v1.json`), "utf8")) as Record<string, unknown>;
    assert.equal(schema.additionalProperties, false);
    assert.equal(schema["x-creation-phase"], 5);
    assert.equal(schema["x-activation-phase"], 7);
  }
  const input = JSON.parse(await readFile(resolve(import.meta.dirname, "../../packages/contracts/spec/risk-input.v1.json"), "utf8")) as { $defs: Record<string, { type?: string; additionalProperties?: boolean }> };
  for (const [name, nested] of Object.entries(input.$defs)) {
    if (nested.type === "object") assert.equal(nested.additionalProperties, false, name);
  }
  const decisionSchema = JSON.parse(await readFile(resolve(import.meta.dirname, "../../packages/contracts/spec/risk-decision.v1.json"), "utf8")) as { $defs: { reasonCode: { enum: string[] } } };
  assert.equal(decisionSchema.$defs.reasonCode.enum.includes("RISK_ALLOWED"), true);
  assert.equal(decisionSchema.$defs.reasonCode.enum.includes("ALLOW_UNSAFE"), false);
  const killSchema = JSON.parse(await readFile(resolve(import.meta.dirname, "../../packages/contracts/spec/kill-switch.v1.json"), "utf8")) as { required: string[]; properties: { reason_code: { enum: string[] } } };
  for (const field of ["actor_id", "trigger_kind", "reason", "observed_at", "context_digest", "prior_version", "version"]) {
    assert.equal(killSchema.required.includes(field), true, field);
  }
  assert.equal(killSchema.properties.reason_code.enum.includes("DRAWDOWN_LIMIT_EXCEEDED"), false);
  const registry = JSON.parse(await readFile(resolve(import.meta.dirname, "../../packages/contracts/spec/risk-domain-events.v1.json"), "utf8")) as { oneOf: unknown[]; "x-activation-phase": number };
  assert.equal(registry.oneOf.length, 2);
  assert.equal(registry["x-activation-phase"], 7);
  const openapi = JSON.parse(await readFile(resolve(import.meta.dirname, "../../packages/contracts/spec/openapi.v1.json"), "utf8")) as { paths: Record<string, unknown> };
  assert.equal(Object.keys(openapi.paths).some((path) => /risk|kill|approval|authorization/.test(path)), false);
});
