import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import test from "node:test";

import type {
  EvidenceCommandBindingV1,
  EvidenceCommandReceiptBindingV1,
  EvidenceDomainEventBindingV1,
  EvidenceSnapshotBindingV1,
} from "../../packages/typescript/contract-bindings/src/index.js";

test("Phase 3 Evidence API and event contracts are closed", async () => {
  const snapshot: EvidenceSnapshotBindingV1 = {
    evidence_id: "a".repeat(64),
    evidence_digest: "b".repeat(64),
    symbol: "BTCUSDT",
    as_of: "2026-07-19T00:00:00Z",
    knowledge_cutoff: "2026-07-19T00:00:00Z",
    recipe_version: "woozoo.evidence.closed-candles-approved-features/v1",
    input_digest: "c".repeat(64),
    quality: "healthy",
    quality_reasons: [],
    collector_session_id: "018f7000-0000-7000-8000-000000000001",
    watermark_digest: "d".repeat(64),
    items: [{
      item_type: "feature_observation",
      item_id: "e".repeat(64),
      raw_event_id: "f".repeat(64),
      raw_payload_hash: "0".repeat(64),
    }],
    candles: [{
      normalized_event_id: "1".repeat(64),
      raw_event_id: "f".repeat(64),
      raw_payload_hash: "0".repeat(64),
      interval: "1m",
      event_time: "2026-07-19T00:00:00Z",
      received_at: "2026-07-19T00:00:00Z",
      open_time: "2026-07-18T23:59:00Z",
      close_time: "2026-07-19T00:00:00Z",
      open: "100", high: "101", low: "99", close: "100", base_volume: "10",
    }],
    features: [{
      feature_id: "e".repeat(64),
      name: "close_sma_20",
      definition_version: "woozoo.feature.ohlcv-return-sma20-rsi14/v1",
      interval: "1m",
      feature_time: "2026-07-19T00:00:00Z",
      value: "100.000000000000000000",
      input_digest: "2".repeat(64),
    }],
  };
  assert.equal(snapshot.symbol, "BTCUSDT");

  const event: EvidenceDomainEventBindingV1 = {
    spec_version: "woozoo.event/v1",
    event_id: "018f7000-0000-7000-8000-000000000002",
    event_type: "evidence.snapshot.created.v1",
    event_version: 1,
    occurred_at: "2026-07-19T00:00:00Z",
    published_at: null,
    producer: "evidence-worker",
    correlation_id: "018f7000-0000-7000-8000-000000000001",
    causation_id: null,
    aggregate: { type: "evidence_snapshot", id: snapshot.evidence_id, version: 1 },
    data: snapshot,
    payload_hash: "1".repeat(64),
  };
  assert.equal(event.event_type, "evidence.snapshot.created.v1");

  const command: EvidenceCommandBindingV1 = {
    symbol: "BTCUSDT",
    as_of: snapshot.as_of,
    knowledge_cutoff: snapshot.knowledge_cutoff,
  };
  const receipt: EvidenceCommandReceiptBindingV1 = {
    evidence_id: snapshot.evidence_id,
    evidence_digest: snapshot.evidence_digest,
    created: true,
  };
  assert.equal(command.symbol, "BTCUSDT");
  assert.equal(receipt.created, true);

  const schema = JSON.parse(await readFile(
    resolve(import.meta.dirname, "../../packages/contracts/spec/evidence-snapshot.v1.json"),
    "utf8",
  )) as { additionalProperties: boolean; required: string[] };
  const registry = JSON.parse(await readFile(
    resolve(import.meta.dirname, "../../packages/contracts/spec/evidence-domain-events.v1.json"),
    "utf8",
  )) as { oneOf: unknown[] };
  assert.equal(schema.additionalProperties, false);
  assert.deepEqual(new Set(schema.required), new Set(Object.keys(snapshot)));
  assert.equal(registry.oneOf.length, 1);
});
