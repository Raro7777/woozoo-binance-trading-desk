import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const openApiPath = resolve(root, "packages/contracts/spec/openapi.v1.json");
const eventPath = resolve(root, "packages/contracts/spec/event-envelope.v1.json");
const marketEventPath = resolve(root, "packages/contracts/spec/market-event.v1.json");
const marketDomainEventsPath = resolve(root, "packages/contracts/spec/market-domain-events.v1.json");
const evidenceSnapshotPath = resolve(root, "packages/contracts/spec/evidence-snapshot.v1.json");
const evidenceDomainEventsPath = resolve(root, "packages/contracts/spec/evidence-domain-events.v1.json");
const paperOrderPath = resolve(root, "packages/contracts/spec/paper-order.v1.json");
const paperDomainEventsPath = resolve(root, "packages/contracts/spec/paper-domain-events.v1.json");
const riskInputPath = resolve(root, "packages/contracts/spec/risk-input.v1.json");
const riskDecisionPath = resolve(root, "packages/contracts/spec/risk-decision.v1.json");
const killSwitchPath = resolve(root, "packages/contracts/spec/kill-switch.v1.json");
const riskDomainEventsPath = resolve(root, "packages/contracts/spec/risk-domain-events.v1.json");
const check = process.argv.includes("--check");

const stable = (value) => {
  if (Array.isArray(value)) return value.map(stable);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, stable(value[key])]));
  }
  return value;
};
const normalized = (value) => `${JSON.stringify(stable(value))}\n`;
const digest = (value) => createHash("sha256").update(normalized(value)).digest("hex");
const openApi = JSON.parse(await readFile(openApiPath, "utf8"));
const eventEnvelope = JSON.parse(await readFile(eventPath, "utf8"));
const marketEvent = JSON.parse(await readFile(marketEventPath, "utf8"));
const marketDomainEvents = JSON.parse(await readFile(marketDomainEventsPath, "utf8"));
const evidenceSnapshot = JSON.parse(await readFile(evidenceSnapshotPath, "utf8"));
const evidenceDomainEvents = JSON.parse(await readFile(evidenceDomainEventsPath, "utf8"));
const paperOrder = JSON.parse(await readFile(paperOrderPath, "utf8"));
const paperDomainEvents = JSON.parse(await readFile(paperDomainEventsPath, "utf8"));
const riskInput = JSON.parse(await readFile(riskInputPath, "utf8"));
const riskDecision = JSON.parse(await readFile(riskDecisionPath, "utf8"));
const killSwitch = JSON.parse(await readFile(killSwitchPath, "utf8"));
const riskDomainEvents = JSON.parse(await readFile(riskDomainEventsPath, "utf8"));

if (
  Object.keys(openApi.paths).join(",") !==
  "/api/v1/health,/api/v1/markets/{symbol}/status,/api/v1/evidence/{evidence_id},/api/v1/commands/evidence-snapshots"
) {
  throw new Error("P3 OpenAPI must expose only the approved query and Evidence command paths");
}
if (
  openApi.info.version !== "v1" ||
  eventEnvelope.$id !== "woozoo.event/v1" ||
  marketEvent.$id !== "woozoo.market-event/v1" ||
  marketDomainEvents.$id !== "woozoo.market-domain-events/v1" ||
  evidenceSnapshot.$id !== "woozoo.evidence-snapshot/v1" ||
  evidenceDomainEvents.$id !== "woozoo.evidence-domain-events/v1"
) {
  throw new Error("approved contract versions must remain v1");
}
if (marketEvent.additionalProperties !== false || marketEvent.properties?.source?.const !== "binance_spot_public") {
  throw new Error("P2 market event must be closed to its approved public source");
}
if (
  !Array.isArray(marketDomainEvents.oneOf) ||
  marketDomainEvents.oneOf.length !== 3 ||
  eventEnvelope.properties?.data?.type !== "object"
) {
  throw new Error("P2 market domain event registry must close exactly three event types");
}
if (
  evidenceSnapshot.additionalProperties !== false ||
  !Array.isArray(evidenceDomainEvents.oneOf) ||
  evidenceDomainEvents.oneOf.length !== 1 ||
  !eventEnvelope.properties?.producer?.enum?.includes("evidence-worker")
) {
  throw new Error("P3 Evidence snapshot and domain event contracts must remain closed");
}
if (
  paperOrder.$id !== "woozoo.paper-order/v1" ||
  paperOrder.additionalProperties !== false ||
  paperOrder["x-creation-phase"] !== 4 ||
  paperOrder["x-activation-phase"] !== 7 ||
  paperDomainEvents.$id !== "woozoo.paper-domain-events/v1" ||
  paperDomainEvents["x-activation-phase"] !== 7 ||
  !Array.isArray(paperDomainEvents.oneOf) ||
  paperDomainEvents.oneOf.length !== 7
) {
  throw new Error("P4 Paper contracts must remain closed and dormant until Phase 7");
}
if (
  riskInput.$id !== "woozoo.risk-input/v1" ||
  riskDecision.$id !== "woozoo.risk-decision/v1" ||
  killSwitch.$id !== "woozoo.kill-switch/v1" ||
  riskDomainEvents.$id !== "woozoo.risk-domain-events/v1" ||
  riskInput.additionalProperties !== false ||
  riskDecision.additionalProperties !== false ||
  killSwitch.additionalProperties !== false ||
  riskInput["x-creation-phase"] !== 5 ||
  riskInput["x-activation-phase"] !== 7 ||
  riskDecision["x-activation-phase"] !== 7 ||
  killSwitch["x-activation-phase"] !== 7 ||
  riskDomainEvents["x-activation-phase"] !== 7 ||
  !Array.isArray(riskDomainEvents.oneOf) ||
  riskDomainEvents.oneOf.length !== 2
) {
  throw new Error("P5 Risk contracts must remain closed and dormant until Phase 7");
}

const schemas = openApi.components?.schemas;
const healthOperation = openApi.paths["/api/v1/health"]?.get;
const marketStatusOperation = openApi.paths["/api/v1/markets/{symbol}/status"]?.get;
const evidenceOperation = openApi.paths["/api/v1/evidence/{evidence_id}"]?.get;
const evidenceCommandOperation = openApi.paths["/api/v1/commands/evidence-snapshots"]?.post;
if (
  schemas === undefined ||
  healthOperation === undefined ||
  marketStatusOperation === undefined ||
  evidenceOperation === undefined ||
  evidenceCommandOperation === undefined
) {
  throw new Error("P3 OpenAPI must define health, market status, Evidence query/command, and components");
}

function schema(name) {
  const candidate = schemas[name];
  if (candidate?.type !== "object" || candidate.additionalProperties !== false) {
    throw new Error(`P1 schema ${name} must be a closed object`);
  }
  return candidate;
}

function requiredSchema(name, required) {
  const candidate = schema(name);
  if (JSON.stringify(candidate.required) !== JSON.stringify(required)) {
    throw new Error(`P1 schema ${name} has an unexpected required-field contract`);
  }
  return candidate;
}

function propertyRef(candidate, property, reference) {
  if (candidate.properties?.[property]?.$ref !== reference) {
    throw new Error(`P1 schema property ${property} must reference ${reference}`);
  }
}

function literal(candidate, property) {
  const value = candidate.properties?.[property]?.const;
  if (typeof value !== "string" && typeof value !== "boolean") {
    throw new Error(`P1 schema property ${property} must have a literal value`);
  }
  return value;
}

function stringUnion(candidate, property) {
  const values = candidate.properties?.[property]?.enum;
  if (!Array.isArray(values) || values.length === 0 || values.some((value) => typeof value !== "string")) {
    throw new Error(`P1 schema property ${property} must have a non-empty string enum`);
  }
  return values.map((value) => JSON.stringify(value)).join(" | ");
}

function responseReference(status, reference) {
  const actual = healthOperation.responses?.[status]?.content?.["application/json"]?.schema?.$ref;
  if (actual !== reference) {
    throw new Error(`P1 health ${status} response must reference ${reference}`);
  }
}

responseReference("200", "#/components/schemas/HealthEnvelopeV1");
responseReference("503", "#/components/schemas/ErrorEnvelopeV1");
const postgres = requiredSchema("PostgresDependencyV1", ["required", "status"]);
const redis = requiredSchema("RedisDependencyV1", ["required", "authoritative", "status"]);
const dependencies = requiredSchema("HealthDependenciesV1", ["postgres", "redis"]);
const healthData = requiredSchema("HealthDataV1", ["service", "status", "trading_mode", "dependencies"]);
const meta = requiredSchema("MetaV1", ["resource_version", "next_cursor"]);
const error = requiredSchema("ErrorV1", ["code", "message"]);
const healthEnvelope = requiredSchema("HealthEnvelopeV1", [
  "api_version", "request_id", "correlation_id", "served_at", "data", "meta",
]);
const errorEnvelope = requiredSchema("ErrorEnvelopeV1", [
  "api_version", "request_id", "correlation_id", "served_at", "error", "meta",
]);
const marketWatermark = requiredSchema("MarketWatermarkV1", ["session_id", "stream", "last_sequence", "observed_at"]);
const marketStatusData = requiredSchema("MarketStatusDataV1", ["symbol", "price", "event_time", "received_at", "quality", "quality_reasons", "watermark"]);
const marketStatusEnvelope = requiredSchema("MarketStatusEnvelopeV1", ["api_version", "request_id", "correlation_id", "served_at", "data", "meta"]);
const marketError = requiredSchema("MarketErrorV1", ["code", "message"]);
const marketErrorEnvelope = requiredSchema("MarketErrorEnvelopeV1", ["api_version", "request_id", "correlation_id", "served_at", "error", "meta"]);
const evidenceItem = requiredSchema("EvidenceItemV1", ["item_type", "item_id", "raw_event_id", "raw_payload_hash"]);
const evidenceCandle = requiredSchema("EvidenceCandleV1", ["normalized_event_id", "raw_event_id", "raw_payload_hash", "interval", "event_time", "received_at", "open_time", "close_time", "open", "high", "low", "close", "base_volume"]);
const evidenceFeature = requiredSchema("EvidenceFeatureV1", ["feature_id", "name", "definition_version", "interval", "feature_time", "value", "input_digest"]);
const evidenceData = requiredSchema("EvidenceSnapshotDataV1", [
  "evidence_id", "evidence_digest", "symbol", "as_of", "knowledge_cutoff", "recipe_version",
  "input_digest", "quality", "quality_reasons", "collector_session_id", "watermark_digest", "items", "candles", "features",
]);
const evidenceError = requiredSchema("EvidenceErrorV1", ["code", "message"]);
const evidenceEnvelope = requiredSchema("EvidenceSnapshotEnvelopeV1", ["api_version", "request_id", "correlation_id", "served_at", "data", "meta"]);
const evidenceErrorEnvelope = requiredSchema("EvidenceErrorEnvelopeV1", ["api_version", "request_id", "correlation_id", "served_at", "error", "meta"]);
requiredSchema("EvidenceCommandV1", ["symbol", "as_of", "knowledge_cutoff"]);
requiredSchema("EvidenceCommandReceiptV1", ["evidence_id", "evidence_digest", "created"]);
const evidenceCommandReceiptEnvelope = requiredSchema("EvidenceCommandReceiptEnvelopeV1", ["api_version", "request_id", "correlation_id", "served_at", "data", "meta"]);
const evidenceCommandError = requiredSchema("EvidenceCommandErrorV1", ["code"]);
const evidenceCommandErrorEnvelope = requiredSchema("EvidenceCommandErrorEnvelopeV1", ["api_version", "request_id", "correlation_id", "served_at", "error", "meta"]);
propertyRef(dependencies, "postgres", "#/components/schemas/PostgresDependencyV1");
propertyRef(dependencies, "redis", "#/components/schemas/RedisDependencyV1");
propertyRef(healthData, "dependencies", "#/components/schemas/HealthDependenciesV1");
propertyRef(healthEnvelope, "data", "#/components/schemas/HealthDataV1");
propertyRef(healthEnvelope, "meta", "#/components/schemas/MetaV1");
propertyRef(errorEnvelope, "error", "#/components/schemas/ErrorV1");
propertyRef(errorEnvelope, "meta", "#/components/schemas/MetaV1");
propertyRef(marketStatusData, "watermark", "#/components/schemas/MarketWatermarkV1");
propertyRef(marketStatusEnvelope, "data", "#/components/schemas/MarketStatusDataV1");
propertyRef(marketStatusEnvelope, "meta", "#/components/schemas/MetaV1");
propertyRef(marketErrorEnvelope, "error", "#/components/schemas/MarketErrorV1");
propertyRef(marketErrorEnvelope, "meta", "#/components/schemas/MetaV1");
propertyRef(evidenceEnvelope, "data", "#/components/schemas/EvidenceSnapshotDataV1");
propertyRef(evidenceEnvelope, "meta", "#/components/schemas/MetaV1");
propertyRef(evidenceErrorEnvelope, "error", "#/components/schemas/EvidenceErrorV1");
propertyRef(evidenceErrorEnvelope, "meta", "#/components/schemas/MetaV1");
propertyRef(evidenceCommandReceiptEnvelope, "data", "#/components/schemas/EvidenceCommandReceiptV1");
propertyRef(evidenceCommandReceiptEnvelope, "meta", "#/components/schemas/MetaV1");
propertyRef(evidenceCommandErrorEnvelope, "error", "#/components/schemas/EvidenceCommandErrorV1");
propertyRef(evidenceCommandErrorEnvelope, "meta", "#/components/schemas/MetaV1");
if (
  stringUnion(evidenceItem, "item_type") !==
    '"normalized_market_event" | "feature_observation"' ||
  evidenceData.properties?.items?.items?.$ref !== "#/components/schemas/EvidenceItemV1"
  || evidenceData.properties?.candles?.items?.$ref !== "#/components/schemas/EvidenceCandleV1"
  || evidenceData.properties?.features?.items?.$ref !== "#/components/schemas/EvidenceFeatureV1"
) {
  throw new Error("P3 Evidence API must expose closed candles, features, and provenance without raw payload bytes");
}
if (meta.properties?.resource_version?.type !== "null" || meta.properties?.next_cursor?.type !== "null") {
  throw new Error("P1 metadata must not expose resource versions or cursors");
}

const manifest = {
  api_version: openApi.info.version,
  event_spec_version: eventEnvelope.$id,
  market_event_spec_version: marketEvent.$id,
  market_domain_event_spec_version: marketDomainEvents.$id,
  evidence_snapshot_spec_version: evidenceSnapshot.$id,
  evidence_domain_event_spec_version: evidenceDomainEvents.$id,
  paper_order_spec_version: paperOrder.$id,
  paper_domain_event_spec_version: paperDomainEvents.$id,
  paper_activation_phase: 7,
  risk_input_spec_version: riskInput.$id,
  risk_decision_spec_version: riskDecision.$id,
  kill_switch_spec_version: killSwitch.$id,
  risk_domain_event_spec_version: riskDomainEvents.$id,
  risk_activation_phase: 7,
  market_source: marketEvent.properties.source.const,
  health_path: "/api/v1/health",
  market_status_path_template: "/api/v1/markets/{symbol}/status",
  evidence_path_template: "/api/v1/evidence/{evidence_id}",
  evidence_command_path: "/api/v1/commands/evidence-snapshots",
  sources: {
    "openapi.v1.json": digest(openApi),
    "event-envelope.v1.json": digest(eventEnvelope),
    "market-event.v1.json": digest(marketEvent),
    "market-domain-events.v1.json": digest(marketDomainEvents),
    "evidence-snapshot.v1.json": digest(evidenceSnapshot),
    "evidence-domain-events.v1.json": digest(evidenceDomainEvents),
    "paper-order.v1.json": digest(paperOrder),
    "paper-domain-events.v1.json": digest(paperDomainEvents),
    "risk-input.v1.json": digest(riskInput),
    "risk-decision.v1.json": digest(riskDecision),
    "kill-switch.v1.json": digest(killSwitch),
    "risk-domain-events.v1.json": digest(riskDomainEvents),
  },
};
const manifestJson = `${JSON.stringify(manifest, null, 2)}\n`;
const tsManifest = `// GENERATED by scripts/generate-contracts.mjs. Do not edit.\nexport const API_VERSION = ${JSON.stringify(manifest.api_version)} as const;\nexport const EVENT_SPEC_VERSION = ${JSON.stringify(manifest.event_spec_version)} as const;\nexport const MARKET_EVENT_SPEC_VERSION = ${JSON.stringify(manifest.market_event_spec_version)} as const;\nexport const EVIDENCE_SNAPSHOT_SPEC_VERSION = ${JSON.stringify(manifest.evidence_snapshot_spec_version)} as const;\nexport const EVIDENCE_DOMAIN_EVENT_SPEC_VERSION = ${JSON.stringify(manifest.evidence_domain_event_spec_version)} as const;\nexport const PAPER_ORDER_SPEC_VERSION = ${JSON.stringify(manifest.paper_order_spec_version)} as const;\nexport const PAPER_DOMAIN_EVENT_SPEC_VERSION = ${JSON.stringify(manifest.paper_domain_event_spec_version)} as const;\nexport const PAPER_ACTIVATION_PHASE = ${JSON.stringify(manifest.paper_activation_phase)} as const;\nexport const MARKET_SOURCE = ${JSON.stringify(manifest.market_source)} as const;\nexport const HEALTH_PATH = ${JSON.stringify(manifest.health_path)} as const;\nexport const MARKET_STATUS_PATH_TEMPLATE = ${JSON.stringify(manifest.market_status_path_template)} as const;\nexport const EVIDENCE_PATH_TEMPLATE = ${JSON.stringify(manifest.evidence_path_template)} as const;\nexport const EVIDENCE_COMMAND_PATH = ${JSON.stringify(manifest.evidence_command_path)} as const;\nexport const CONTRACT_SOURCE_DIGESTS = ${JSON.stringify(manifest.sources, null, 2)} as const;\n`;
const tsBindings = `// GENERATED by scripts/generate-contracts.mjs. Do not edit.\nexport type PostgresDependencyBindingV1 = { required: ${literal(postgres, "required")}; status: ${JSON.stringify(literal(postgres, "status"))} };\nexport type RedisDependencyBindingV1 = { required: ${literal(redis, "required")}; authoritative: ${literal(redis, "authoritative")}; status: ${stringUnion(redis, "status")} };\nexport type PlatformHealthBindingV1 = {\n  api_version: ${JSON.stringify(literal(healthEnvelope, "api_version"))};\n  request_id: string;\n  correlation_id: string;\n  served_at: string;\n  data: {\n    service: ${JSON.stringify(literal(healthData, "service"))};\n    status: ${stringUnion(healthData, "status")};\n    trading_mode: ${JSON.stringify(literal(healthData, "trading_mode"))};\n    dependencies: { postgres: PostgresDependencyBindingV1; redis: RedisDependencyBindingV1 };\n  };\n  meta: { resource_version: null; next_cursor: null };\n};\nexport type DependencyUnavailableBindingV1 = {\n  api_version: ${JSON.stringify(literal(errorEnvelope, "api_version"))};\n  request_id: string;\n  correlation_id: string;\n  served_at: string;\n  error: { code: ${JSON.stringify(literal(error, "code"))}; message: ${JSON.stringify(literal(error, "message"))} };\n  meta: { resource_version: null; next_cursor: null };\n};\nexport const healthPath = "/api/v1/health" as const;\n`;
const marketTsBindings = `export type MarketEventBindingV1 = {\n  event_id: string;\n  event_type: "trade" | "book_ticker" | "kline";\n  schema_version: "woozoo.market-event/v1";\n  source: "binance_spot_public";\n  symbol: "BTCUSDT" | "ETHUSDT";\n  event_time: string;\n  received_at: string;\n  sequence: number;\n  raw_event_id: string;\n  raw_payload_hash: string;\n  correlation_id: string;\n  quality_status: "healthy" | "degraded" | "stale" | "invalid" | "reconnecting";\n  quality_reasons: string[];\n  stream_watermark: { session_id: string; stream: string; last_sequence: number; observed_at: string };\n  payload:\n    | { kind: "trade"; price: string; quantity: string; buyer_maker: boolean }\n    | { kind: "book_ticker"; bid_price: string; bid_quantity: string; ask_price: string; ask_quantity: string; event_time_source: "received_at" }\n    | { kind: "kline"; interval: "1m" | "5m" | "1h" | "4h"; open_time: string; close_time: string; open: string; close: string; high: string; low: string; base_volume: string; trade_count: number; closed: boolean };\n};\nexport type MarketStatusBindingV1 = { api_version: "v1"; request_id: string; correlation_id: string; served_at: string; data: { symbol: ${stringUnion(marketStatusData, "symbol")}; price: string; event_time: string; received_at: string; quality: ${stringUnion(marketStatusData, "quality")}; quality_reasons: string[]; watermark: { session_id: string; stream: string; last_sequence: number; observed_at: string } }; meta: { resource_version: null; next_cursor: null } };\nexport type MarketStatusErrorBindingV1 = { api_version: "v1"; request_id: string; correlation_id: string; served_at: string; error: { code: ${stringUnion(marketError, "code")}; message: string }; meta: { resource_version: null; next_cursor: null } };\nexport const marketStatusPathTemplate = "/api/v1/markets/{symbol}/status" as const;\n`;
const domainTsBindings = `export type MarketRawAppendedDataBindingV1 = { raw_event_id: string; collector_session_id: string; source: "binance_spot_public"; stream: string; symbol: "BTCUSDT" | "ETHUSDT" | null; source_dedupe_key: string; source_event_time: string | null; received_at: string; raw_payload_hash: string; classification: "market" | "operational_control" | "quarantined" };\nexport type MarketQualityChangedDataBindingV1 = { quality_event_id: string; scope: string; previous_status: "healthy" | "degraded" | "stale" | "invalid" | "reconnecting"; new_status: "healthy" | "degraded" | "stale" | "invalid" | "reconnecting"; reason_codes: string[]; observed_at: string; raw_event_id: string | null; recovery_required: boolean };\nexport type MarketDomainEventEnvelopeBindingV1<TType extends string, TData> = { spec_version: "woozoo.event/v1"; event_id: string; event_type: TType; event_version: 1; occurred_at: string; published_at: string | null; producer: "market-data-worker"; correlation_id: string; causation_id: string | null; aggregate: { type: string; id: string; version: number }; data: TData; payload_hash: string };\nexport type MarketDomainEventBindingV1 =\n  | MarketDomainEventEnvelopeBindingV1<"market.raw.appended.v1", MarketRawAppendedDataBindingV1>\n  | MarketDomainEventEnvelopeBindingV1<"market.normalized.recorded.v1", MarketEventBindingV1>\n  | MarketDomainEventEnvelopeBindingV1<"market.quality.changed.v1", MarketQualityChangedDataBindingV1>;\n`;
const evidenceTsBindings = `export type EvidenceItemBindingV1 = { item_type: ${stringUnion(evidenceItem, "item_type")}; item_id: string; raw_event_id: string; raw_payload_hash: string };\nexport type EvidenceCandleBindingV1 = { normalized_event_id: string; raw_event_id: string; raw_payload_hash: string; interval: ${stringUnion(evidenceCandle, "interval")}; event_time: string; received_at: string; open_time: string; close_time: string; open: string; high: string; low: string; close: string; base_volume: string };\nexport type EvidenceFeatureBindingV1 = { feature_id: string; name: ${stringUnion(evidenceFeature, "name")}; definition_version: ${JSON.stringify(literal(evidenceFeature, "definition_version"))}; interval: ${stringUnion(evidenceFeature, "interval")}; feature_time: string; value: string; input_digest: string };\nexport type EvidenceSnapshotBindingV1 = { evidence_id: string; evidence_digest: string; symbol: ${stringUnion(evidenceData, "symbol")}; as_of: string; knowledge_cutoff: string; recipe_version: ${JSON.stringify(literal(evidenceData, "recipe_version"))}; input_digest: string; quality: ${JSON.stringify(literal(evidenceData, "quality"))}; quality_reasons: []; collector_session_id: string; watermark_digest: string; items: EvidenceItemBindingV1[]; candles: EvidenceCandleBindingV1[]; features: EvidenceFeatureBindingV1[] };\nexport type EvidenceSnapshotEnvelopeBindingV1 = { api_version: "v1"; request_id: string; correlation_id: string; served_at: string; data: EvidenceSnapshotBindingV1; meta: { resource_version: null; next_cursor: null } };\nexport type EvidenceErrorBindingV1 = { api_version: "v1"; request_id: string; correlation_id: string; served_at: string; error: { code: ${stringUnion(evidenceError, "code")}; message: string }; meta: { resource_version: null; next_cursor: null } };\nexport type EvidenceDomainEventBindingV1 = { spec_version: "woozoo.event/v1"; event_id: string; event_type: "evidence.snapshot.created.v1"; event_version: 1; occurred_at: string; published_at: string | null; producer: "evidence-worker"; correlation_id: string; causation_id: string | null; aggregate: { type: "evidence_snapshot"; id: string; version: number }; data: EvidenceSnapshotBindingV1; payload_hash: string };\nexport type EvidenceCommandBindingV1 = { symbol: "BTCUSDT" | "ETHUSDT"; as_of: string; knowledge_cutoff: string };\nexport type EvidenceCommandReceiptBindingV1 = { evidence_id: string; evidence_digest: string; created: boolean };\nexport type EvidenceCommandReceiptEnvelopeBindingV1 = { api_version: "v1"; request_id: string; correlation_id: string; served_at: string; data: EvidenceCommandReceiptBindingV1; meta: { resource_version: null; next_cursor: null } };\nexport type EvidenceCommandErrorEnvelopeBindingV1 = { api_version: "v1"; request_id: string; correlation_id: string; served_at: string; error: { code: "SCHEMA_INVALID" | "IDEMPOTENCY_CONFLICT" }; meta: { resource_version: null; next_cursor: null } };\nexport const evidencePathTemplate = "/api/v1/evidence/{evidence_id}" as const;\nexport const evidenceCommandPath = "/api/v1/commands/evidence-snapshots" as const;\n`;
const strictEvidenceTsBindings = evidenceTsBindings.replace(
  '"SCHEMA_INVALID" | "IDEMPOTENCY_CONFLICT"',
  stringUnion(evidenceCommandError, "code"),
);
const pySourceDigests = Object.entries(manifest.sources)
  .map(([name, value]) => `    ${JSON.stringify(name)}: ${JSON.stringify(value)},`)
  .join("\n");
const pyBindings = `# GENERATED by scripts/generate-contracts.mjs. Do not edit.\nfrom typing import Literal, TypedDict\n\nAPI_VERSION = "v1"\nEVENT_SPEC_VERSION = "woozoo.event/v1"\nMARKET_EVENT_SPEC_VERSION = "woozoo.market-event/v1"\nMARKET_DOMAIN_EVENT_SPEC_VERSION = "woozoo.market-domain-events/v1"\nMARKET_SOURCE = "binance_spot_public"\nHEALTH_PATH = "/api/v1/health"\nMARKET_STATUS_PATH_TEMPLATE = "/api/v1/markets/{symbol}/status"\nCONTRACT_SOURCE_DIGESTS = {\n${pySourceDigests}\n}\n\nQualityStatusV1 = Literal["healthy", "degraded", "stale", "invalid", "reconnecting"]\n\nclass StreamWatermarkBindingV1(TypedDict):\n    session_id: str\n    stream: str\n    last_sequence: int\n    observed_at: str\n\nclass MarketEventBindingV1(TypedDict):\n    event_id: str\n    event_type: Literal["trade", "book_ticker", "kline"]\n    schema_version: Literal["woozoo.market-event/v1"]\n    source: Literal["binance_spot_public"]\n    symbol: Literal["BTCUSDT", "ETHUSDT"]\n    event_time: str\n    received_at: str\n    sequence: int\n    raw_event_id: str\n    raw_payload_hash: str\n    correlation_id: str\n    quality_status: QualityStatusV1\n    quality_reasons: list[str]\n    stream_watermark: StreamWatermarkBindingV1\n    payload: dict[str, object]\n\nclass MarketRawAppendedDataBindingV1(TypedDict):\n    raw_event_id: str\n    collector_session_id: str\n    source: Literal["binance_spot_public"]\n    stream: str\n    symbol: Literal["BTCUSDT", "ETHUSDT"] | None\n    source_dedupe_key: str\n    source_event_time: str | None\n    received_at: str\n    raw_payload_hash: str\n    classification: Literal["market", "operational_control", "quarantined"]\n\nclass MarketQualityChangedDataBindingV1(TypedDict):\n    quality_event_id: str\n    scope: str\n    previous_status: QualityStatusV1\n    new_status: QualityStatusV1\n    reason_codes: list[str]\n    observed_at: str\n    raw_event_id: str | None\n    recovery_required: bool\n\nclass MarketDomainAggregateBindingV1(TypedDict):\n    type: str\n    id: str\n    version: int\n\nclass MarketRawAppendedEventBindingV1(TypedDict):\n    spec_version: Literal["woozoo.event/v1"]\n    event_id: str\n    event_type: Literal["market.raw.appended.v1"]\n    event_version: Literal[1]\n    occurred_at: str\n    published_at: str | None\n    producer: Literal["market-data-worker"]\n    correlation_id: str\n    causation_id: str | None\n    aggregate: MarketDomainAggregateBindingV1\n    data: MarketRawAppendedDataBindingV1\n    payload_hash: str\n\nclass MarketNormalizedRecordedEventBindingV1(TypedDict):\n    spec_version: Literal["woozoo.event/v1"]\n    event_id: str\n    event_type: Literal["market.normalized.recorded.v1"]\n    event_version: Literal[1]\n    occurred_at: str\n    published_at: str | None\n    producer: Literal["market-data-worker"]\n    correlation_id: str\n    causation_id: str | None\n    aggregate: MarketDomainAggregateBindingV1\n    data: MarketEventBindingV1\n    payload_hash: str\n\nclass MarketQualityChangedEventBindingV1(TypedDict):\n    spec_version: Literal["woozoo.event/v1"]\n    event_id: str\n    event_type: Literal["market.quality.changed.v1"]\n    event_version: Literal[1]\n    occurred_at: str\n    published_at: str | None\n    producer: Literal["market-data-worker"]\n    correlation_id: str\n    causation_id: str | None\n    aggregate: MarketDomainAggregateBindingV1\n    data: MarketQualityChangedDataBindingV1\n    payload_hash: str\n`;

const strictPyBindings = pyBindings
  .replace(
    "\n\nclass MarketEventBindingV1(TypedDict):",
    `

class TradePayloadBindingV1(TypedDict):
    kind: Literal["trade"]
    price: str
    quantity: str
    buyer_maker: bool

class BookTickerPayloadBindingV1(TypedDict):
    kind: Literal["book_ticker"]
    bid_price: str
    bid_quantity: str
    ask_price: str
    ask_quantity: str
    event_time_source: Literal["received_at"]

class KlinePayloadBindingV1(TypedDict):
    kind: Literal["kline"]
    interval: Literal["1m", "5m", "1h", "4h"]
    open_time: str
    close_time: str
    open: str
    close: str
    high: str
    low: str
    base_volume: str
    trade_count: int
    closed: bool


MarketPayloadBindingV1 = TradePayloadBindingV1 | BookTickerPayloadBindingV1 | KlinePayloadBindingV1

class MarketEventBindingV1(TypedDict):`,
  )
  .replace("    payload: dict[str, object]", "    payload: MarketPayloadBindingV1");

const evidencePyBindings = `

EVIDENCE_SNAPSHOT_SPEC_VERSION = "woozoo.evidence-snapshot/v1"
EVIDENCE_DOMAIN_EVENT_SPEC_VERSION = "woozoo.evidence-domain-events/v1"
EVIDENCE_PATH_TEMPLATE = "/api/v1/evidence/{evidence_id}"
EVIDENCE_COMMAND_PATH = "/api/v1/commands/evidence-snapshots"

class EvidenceItemBindingV1(TypedDict):
    item_type: Literal["normalized_market_event", "feature_observation"]
    item_id: str
    raw_event_id: str
    raw_payload_hash: str

class EvidenceCandleBindingV1(TypedDict):
    normalized_event_id: str
    raw_event_id: str
    raw_payload_hash: str
    interval: Literal["1m", "5m", "1h", "4h"]
    event_time: str
    received_at: str
    open_time: str
    close_time: str
    open: str
    high: str
    low: str
    close: str
    base_volume: str

class EvidenceFeatureBindingV1(TypedDict):
    feature_id: str
    name: Literal["close_return_1", "close_sma_20", "close_rsi_14"]
    definition_version: Literal["woozoo.feature.ohlcv-return-sma20-rsi14/v1"]
    interval: Literal["1m", "5m", "1h", "4h"]
    feature_time: str
    value: str
    input_digest: str

class EvidenceSnapshotBindingV1(TypedDict):
    evidence_id: str
    evidence_digest: str
    symbol: Literal["BTCUSDT", "ETHUSDT"]
    as_of: str
    knowledge_cutoff: str
    recipe_version: Literal["woozoo.evidence.closed-candles-approved-features/v1"]
    input_digest: str
    quality: Literal["healthy"]
    quality_reasons: list[str]
    collector_session_id: str
    watermark_digest: str
    items: list[EvidenceItemBindingV1]
    candles: list[EvidenceCandleBindingV1]
    features: list[EvidenceFeatureBindingV1]

class EvidenceAggregateBindingV1(TypedDict):
    type: Literal["evidence_snapshot"]
    id: str
    version: int

class EvidenceSnapshotCreatedEventBindingV1(TypedDict):
    spec_version: Literal["woozoo.event/v1"]
    event_id: str
    event_type: Literal["evidence.snapshot.created.v1"]
    event_version: Literal[1]
    occurred_at: str
    published_at: str | None
    producer: Literal["evidence-worker"]
    correlation_id: str
    causation_id: str | None
    aggregate: EvidenceAggregateBindingV1
    data: EvidenceSnapshotBindingV1
    payload_hash: str

class EvidenceCommandBindingV1(TypedDict):
    symbol: Literal["BTCUSDT", "ETHUSDT"]
    as_of: str
    knowledge_cutoff: str

class EvidenceCommandReceiptBindingV1(TypedDict):
    evidence_id: str
    evidence_digest: str
    created: bool

class EvidenceCommandReceiptEnvelopeBindingV1(TypedDict):
    api_version: Literal["v1"]
    request_id: str
    correlation_id: str
    served_at: str
    data: EvidenceCommandReceiptBindingV1
    meta: dict[str, None]

class EvidenceCommandErrorBindingV1(TypedDict):
    code: Literal["SCHEMA_INVALID", "IDEMPOTENCY_CONFLICT"]

class EvidenceCommandErrorEnvelopeBindingV1(TypedDict):
    api_version: Literal["v1"]
    request_id: str
    correlation_id: str
    served_at: str
    error: EvidenceCommandErrorBindingV1
    meta: dict[str, None]
`;

const paperTsBindings = `export type PaperOrderBindingV1 = { order_id: string; client_order_id: string; authorization_id: string; authorization_namespace: "test"; symbol: "BTCUSDT" | "ETHUSDT"; side: "BUY" | "SELL"; order_type: "LIMIT"; time_in_force: "GTC"; quantity: string; limit_price: string; filled_quantity: string; status: "OPEN" | "PARTIALLY_FILLED" | "FILLED" | "CANCELLED"; version: number };
export type PaperDomainEventEnvelopeBindingV1<TType extends string, TData> = { spec_version: "woozoo.event/v1"; event_id: string; event_type: TType; event_version: 1; occurred_at: string; producer: "paper-engine"; activation_phase: 7; aggregate_id: string; aggregate_version: number; payload_hash: string; data: TData };
export type PaperDomainEventBindingV1 =
  | PaperDomainEventEnvelopeBindingV1<"paper.order.accepted.v1", { order_id: string }>
  | PaperDomainEventEnvelopeBindingV1<"paper.order.partially-filled.v1" | "paper.order.filled.v1", { order_id: string; fill_id: string }>
  | PaperDomainEventEnvelopeBindingV1<"paper.order.cancelled.v1", { order_id: string; cancel_id: string }>
  | PaperDomainEventEnvelopeBindingV1<"paper.order.rejected.v1", { request_hash: string; reason: "INSUFFICIENT_FUNDS" }>
  | PaperDomainEventEnvelopeBindingV1<"paper.authorization.attempted.v1", { authorization_id: string; outcome: "BLOCKED" | "CONSUMED" }>
  | PaperDomainEventEnvelopeBindingV1<"ledger.transaction.posted.v1", { transaction_id: string }>;
`;
const paperPyBindings = `\nclass PaperOrderBindingV1(TypedDict):\n    order_id: str\n    client_order_id: str\n    authorization_id: str\n    authorization_namespace: Literal["test"]\n    symbol: Literal["BTCUSDT", "ETHUSDT"]\n    side: Literal["BUY", "SELL"]\n    order_type: Literal["LIMIT"]\n    time_in_force: Literal["GTC"]\n    quantity: str\n    limit_price: str\n    filled_quantity: str\n    status: Literal["OPEN", "PARTIALLY_FILLED", "FILLED", "CANCELLED"]\n    version: int\n`;
const paperPyEventBindings = `\nclass PaperDomainEventBindingV1(TypedDict):\n    spec_version: Literal["woozoo.event/v1"]\n    event_id: str\n    event_type: Literal[\n        "paper.order.accepted.v1",\n        "paper.order.partially-filled.v1",\n        "paper.order.filled.v1",\n        "paper.order.cancelled.v1",\n        "paper.order.rejected.v1",\n        "paper.authorization.attempted.v1",\n        "ledger.transaction.posted.v1",\n    ]\n    event_version: Literal[1]\n    occurred_at: str\n    producer: Literal["paper-engine"]\n    activation_phase: Literal[7]\n    aggregate_id: str\n    aggregate_version: int\n    payload_hash: str\n    data: dict[str, str]\n`;

const riskTsBindings = `export type RiskVerdictBindingV1 = "ALLOWED" | "DENIED" | "ERROR";
export type RiskDecisionBindingV1 = { decision_schema_version: "woozoo.risk-decision/v1"; decision_id: string; risk_input_digest: string; decision_hash: string; verdict: RiskVerdictBindingV1; primary_reason: string; ordered_reason_codes: string[]; policy_version: string; proposal_hash: string; portfolio_snapshot_hash: string; data_state_hash: string; paper_order_preview_hash: string; reconciliation_checkpoint_hash: string; kill_switch_version: number; decision_as_of: string };
export type KillSwitchBindingV1 = { scope: "paper-global"; active: true; prior_version: number; version: number; activation_event_id: string; trigger_kind: "MANUAL" | "INVARIANT"; actor_id: string; reason_code: "MANUAL_SAFETY_STOP" | "LEDGER_IMBALANCE" | "PHYSICAL_LEDGER_MISMATCH" | "AUTHORIZATION_RECEIPT_MISMATCH"; reason: string; observed_at: string; context_digest: string };
export type RiskDomainEventBindingV1 = { spec_version: "woozoo.event/v1"; event_id: string; event_type: "risk.decision.recorded.v1" | "kill-switch.activated.v1"; event_version: 1; occurred_at: string; producer: "risk-engine"; activation_phase: 7; aggregate_id: string; aggregate_version: number; payload_hash: string; data: RiskDecisionBindingV1 | KillSwitchBindingV1 };
`;
const riskSchemaPyBindings = `
import json

RISK_INPUT_SCHEMA: dict[str, object] = json.loads(${JSON.stringify(JSON.stringify(riskInput))})
`;

const riskPyBindings = `
RiskVerdictBindingV1 = Literal["ALLOWED", "DENIED", "ERROR"]

class RiskDecisionBindingV1(TypedDict):
    decision_schema_version: Literal["woozoo.risk-decision/v1"]
    decision_id: str
    risk_input_digest: str
    decision_hash: str
    verdict: RiskVerdictBindingV1
    primary_reason: str
    ordered_reason_codes: list[str]
    policy_version: str
    proposal_hash: str
    portfolio_snapshot_hash: str
    data_state_hash: str
    paper_order_preview_hash: str
    reconciliation_checkpoint_hash: str
    kill_switch_version: int
    decision_as_of: str

class KillSwitchBindingV1(TypedDict):
    scope: Literal["paper-global"]
    active: Literal[True]
    prior_version: int
    version: int
    activation_event_id: str
    trigger_kind: Literal["MANUAL", "INVARIANT"]
    actor_id: str
    reason_code: Literal["MANUAL_SAFETY_STOP", "LEDGER_IMBALANCE", "PHYSICAL_LEDGER_MISMATCH", "AUTHORIZATION_RECEIPT_MISMATCH"]
    reason: str
    observed_at: str
    context_digest: str
`;

const riskTsManifest = `export const RISK_INPUT_SPEC_VERSION = ${JSON.stringify(manifest.risk_input_spec_version)} as const;
export const RISK_DECISION_SPEC_VERSION = ${JSON.stringify(manifest.risk_decision_spec_version)} as const;
export const KILL_SWITCH_SPEC_VERSION = ${JSON.stringify(manifest.kill_switch_spec_version)} as const;
export const RISK_DOMAIN_EVENT_SPEC_VERSION = ${JSON.stringify(manifest.risk_domain_event_spec_version)} as const;
export const RISK_ACTIVATION_PHASE = ${JSON.stringify(manifest.risk_activation_phase)} as const;
`;

const outputs = new Map([
  [resolve(root, "packages/contracts/schema-manifest.json"), manifestJson],
  [resolve(root, "packages/contracts/src/generated-schema-manifest.ts"), `${tsManifest}${riskTsManifest}`],
  [resolve(root, "packages/typescript/contract-bindings/src/generated.ts"), `${tsBindings}${marketTsBindings}${domainTsBindings}${strictEvidenceTsBindings}${paperTsBindings}${riskTsBindings}`],
  [
    resolve(root, "packages/python/platform-core/src/platform_core/generated_contracts.py"),
    `${strictPyBindings}${riskSchemaPyBindings}${evidencePyBindings}${paperPyBindings}${paperPyEventBindings}${riskPyBindings}`
      .replace(
        'Literal["SCHEMA_INVALID", "IDEMPOTENCY_CONFLICT"]',
        'Literal["SCHEMA_INVALID", "IDEMPOTENCY_CONFLICT", "CALLER_UNAUTHORIZED"]',
      )
      .replaceAll("\n\nclass ", "\n\n\nclass "),
  ],
]);

for (const [path, content] of outputs) {
  await mkdir(dirname(path), { recursive: true });
  let current = null;
  try {
    current = await readFile(path, "utf8");
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  if (check) {
    if (current !== content) {
      throw new Error(`generated contract artifact is stale: ${path}`);
    }
  } else if (current !== content) {
    await writeFile(path, content, "utf8");
  }
}
