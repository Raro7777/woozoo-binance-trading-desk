import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import test from "node:test";

import {
  MARKET_EVENT_SPEC_VERSION,
  MARKET_SOURCE,
} from "../../packages/contracts/src/index.js";
import type {
  MarketDomainEventBindingV1,
  MarketEventBindingV1,
} from "../../packages/typescript/contract-bindings/src/index.js";

test("Phase 2 market event contract is closed and generated", async () => {
  assert.equal(MARKET_EVENT_SPEC_VERSION, "woozoo.market-event/v1");
  assert.equal(MARKET_SOURCE, "binance_spot_public");
  const event: MarketEventBindingV1 = {
    event_id: "a".repeat(64),
    event_type: "trade",
    schema_version: "woozoo.market-event/v1",
    source: "binance_spot_public",
    symbol: "BTCUSDT",
    event_time: "2026-07-19T00:00:00Z",
    received_at: "2026-07-19T00:00:00.100000Z",
    sequence: 100,
    raw_event_id: "b".repeat(64),
    raw_payload_hash: "c".repeat(64),
    correlation_id: "018f7000-0000-7000-8000-000000000001",
    quality_status: "healthy",
    quality_reasons: [],
    stream_watermark: {
      session_id: "018f7000-0000-7000-8000-000000000001",
      stream: "btcusdt@trade",
      last_sequence: 100,
      observed_at: "2026-07-19T00:00:00.100000Z",
    },
    payload: { kind: "trade", price: "60000.10000000", quantity: "0.01000000", buyer_maker: false },
  };
  assert.equal(event.payload.kind, "trade");

  const schema = JSON.parse(await readFile(
    resolve(import.meta.dirname, "../../packages/contracts/spec/market-event.v1.json"),
    "utf8",
  )) as { additionalProperties: boolean; required: string[] };
  assert.equal(schema.additionalProperties, false);
  assert.deepEqual(new Set(schema.required), new Set(Object.keys(event)));

  const eventEnvelope = JSON.parse(await readFile(
    resolve(import.meta.dirname, "../../packages/contracts/spec/event-envelope.v1.json"),
    "utf8",
  )) as { properties: { producer: { enum: string[] }; data: { type: string } } };
  const domainRegistry = JSON.parse(await readFile(
    resolve(import.meta.dirname, "../../packages/contracts/spec/market-domain-events.v1.json"),
    "utf8",
  )) as { $id: string; oneOf: unknown[] };
  const registrySource = JSON.stringify(domainRegistry);

  assert.equal(eventEnvelope.properties.data.type, "object");
  assert.ok(eventEnvelope.properties.producer.enum.includes("market-data-worker"));
  assert.equal(domainRegistry.$id, "woozoo.market-domain-events/v1");
  assert.equal(domainRegistry.oneOf.length, 3);
  assert.match(registrySource, /market\.raw\.appended\.v1/);
  assert.match(registrySource, /market\.normalized\.recorded\.v1/);
  assert.match(registrySource, /market\.quality\.changed\.v1/);

  const rawDomainEvent: MarketDomainEventBindingV1 = {
    spec_version: "woozoo.event/v1",
    event_id: "018f7000-0000-7000-8000-000000000002",
    event_type: "market.raw.appended.v1",
    event_version: 1,
    occurred_at: "2026-07-19T00:00:00Z",
    published_at: null,
    producer: "market-data-worker",
    correlation_id: "018f7000-0000-7000-8000-000000000001",
    causation_id: null,
    aggregate: { type: "raw_market_event", id: "b".repeat(64), version: 1 },
    data: {
      raw_event_id: "b".repeat(64),
      collector_session_id: "018f7000-0000-7000-8000-000000000001",
      source: "binance_spot_public",
      stream: "btcusdt@trade",
      symbol: "BTCUSDT",
      source_dedupe_key: "trade:BTCUSDT:100",
      source_event_time: "2026-07-19T00:00:00Z",
      received_at: "2026-07-19T00:00:00.100000Z",
      raw_payload_hash: "c".repeat(64),
      classification: "market",
    },
    payload_hash: "d".repeat(64),
  };
  assert.equal(rawDomainEvent.event_type, "market.raw.appended.v1");

  const pythonBindings = await readFile(
    resolve(
      import.meta.dirname,
      "../../packages/python/platform-core/src/platform_core/generated_contracts.py",
    ),
    "utf8",
  );
  assert.match(pythonBindings, /class MarketRawAppendedEventBindingV1\(TypedDict\)/);
  assert.match(pythonBindings, /class MarketNormalizedRecordedEventBindingV1\(TypedDict\)/);
  assert.match(pythonBindings, /class MarketQualityChangedEventBindingV1\(TypedDict\)/);
});
