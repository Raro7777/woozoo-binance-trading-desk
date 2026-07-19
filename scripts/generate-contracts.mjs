import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const openApiPath = resolve(root, "packages/contracts/spec/openapi.v1.json");
const eventPath = resolve(root, "packages/contracts/spec/event-envelope.v1.json");
const marketEventPath = resolve(root, "packages/contracts/spec/market-event.v1.json");
const marketDomainEventsPath = resolve(root, "packages/contracts/spec/market-domain-events.v1.json");
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

if (Object.keys(openApi.paths).join(",") !== "/api/v1/health,/api/v1/markets/{symbol}/status") {
  throw new Error("P2 OpenAPI must expose only health and market status");
}
if (
  openApi.info.version !== "v1" ||
  eventEnvelope.$id !== "woozoo.event/v1" ||
  marketEvent.$id !== "woozoo.market-event/v1" ||
  marketDomainEvents.$id !== "woozoo.market-domain-events/v1"
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

const schemas = openApi.components?.schemas;
const healthOperation = openApi.paths["/api/v1/health"]?.get;
const marketStatusOperation = openApi.paths["/api/v1/markets/{symbol}/status"]?.get;
if (schemas === undefined || healthOperation === undefined || marketStatusOperation === undefined) {
  throw new Error("P2 OpenAPI must define health, market status, and components");
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
if (meta.properties?.resource_version?.type !== "null" || meta.properties?.next_cursor?.type !== "null") {
  throw new Error("P1 metadata must not expose resource versions or cursors");
}

const manifest = {
  api_version: openApi.info.version,
  event_spec_version: eventEnvelope.$id,
  market_event_spec_version: marketEvent.$id,
  market_domain_event_spec_version: marketDomainEvents.$id,
  market_source: marketEvent.properties.source.const,
  health_path: "/api/v1/health",
  market_status_path_template: "/api/v1/markets/{symbol}/status",
  sources: {
    "openapi.v1.json": digest(openApi),
    "event-envelope.v1.json": digest(eventEnvelope),
    "market-event.v1.json": digest(marketEvent),
    "market-domain-events.v1.json": digest(marketDomainEvents),
  },
};
const manifestJson = `${JSON.stringify(manifest, null, 2)}\n`;
const tsManifest = `// GENERATED by scripts/generate-contracts.mjs. Do not edit.\nexport const API_VERSION = ${JSON.stringify(manifest.api_version)} as const;\nexport const EVENT_SPEC_VERSION = ${JSON.stringify(manifest.event_spec_version)} as const;\nexport const MARKET_EVENT_SPEC_VERSION = ${JSON.stringify(manifest.market_event_spec_version)} as const;\nexport const MARKET_SOURCE = ${JSON.stringify(manifest.market_source)} as const;\nexport const HEALTH_PATH = ${JSON.stringify(manifest.health_path)} as const;\nexport const MARKET_STATUS_PATH_TEMPLATE = ${JSON.stringify(manifest.market_status_path_template)} as const;\nexport const CONTRACT_SOURCE_DIGESTS = ${JSON.stringify(manifest.sources, null, 2)} as const;\n`;
const tsBindings = `// GENERATED by scripts/generate-contracts.mjs. Do not edit.\nexport type PostgresDependencyBindingV1 = { required: ${literal(postgres, "required")}; status: ${JSON.stringify(literal(postgres, "status"))} };\nexport type RedisDependencyBindingV1 = { required: ${literal(redis, "required")}; authoritative: ${literal(redis, "authoritative")}; status: ${stringUnion(redis, "status")} };\nexport type PlatformHealthBindingV1 = {\n  api_version: ${JSON.stringify(literal(healthEnvelope, "api_version"))};\n  request_id: string;\n  correlation_id: string;\n  served_at: string;\n  data: {\n    service: ${JSON.stringify(literal(healthData, "service"))};\n    status: ${stringUnion(healthData, "status")};\n    trading_mode: ${JSON.stringify(literal(healthData, "trading_mode"))};\n    dependencies: { postgres: PostgresDependencyBindingV1; redis: RedisDependencyBindingV1 };\n  };\n  meta: { resource_version: null; next_cursor: null };\n};\nexport type DependencyUnavailableBindingV1 = {\n  api_version: ${JSON.stringify(literal(errorEnvelope, "api_version"))};\n  request_id: string;\n  correlation_id: string;\n  served_at: string;\n  error: { code: ${JSON.stringify(literal(error, "code"))}; message: ${JSON.stringify(literal(error, "message"))} };\n  meta: { resource_version: null; next_cursor: null };\n};\nexport const healthPath = "/api/v1/health" as const;\n`;
const marketTsBindings = `export type MarketEventBindingV1 = {\n  event_id: string;\n  event_type: "trade" | "book_ticker" | "kline";\n  schema_version: "woozoo.market-event/v1";\n  source: "binance_spot_public";\n  symbol: "BTCUSDT" | "ETHUSDT";\n  event_time: string;\n  received_at: string;\n  sequence: number;\n  raw_event_id: string;\n  raw_payload_hash: string;\n  correlation_id: string;\n  quality_status: "healthy" | "degraded" | "stale" | "invalid" | "reconnecting";\n  quality_reasons: string[];\n  stream_watermark: { session_id: string; stream: string; last_sequence: number; observed_at: string };\n  payload:\n    | { kind: "trade"; price: string; quantity: string; buyer_maker: boolean }\n    | { kind: "book_ticker"; bid_price: string; bid_quantity: string; ask_price: string; ask_quantity: string; event_time_source: "received_at" }\n    | { kind: "kline"; interval: "1m" | "5m" | "1h" | "4h"; open_time: string; close_time: string; open: string; close: string; high: string; low: string; base_volume: string; trade_count: number; closed: boolean };\n};\nexport type MarketStatusBindingV1 = { api_version: "v1"; request_id: string; correlation_id: string; served_at: string; data: { symbol: ${stringUnion(marketStatusData, "symbol")}; price: string; event_time: string; received_at: string; quality: ${stringUnion(marketStatusData, "quality")}; quality_reasons: string[]; watermark: { session_id: string; stream: string; last_sequence: number; observed_at: string } }; meta: { resource_version: null; next_cursor: null } };\nexport type MarketStatusErrorBindingV1 = { api_version: "v1"; request_id: string; correlation_id: string; served_at: string; error: { code: ${stringUnion(marketError, "code")}; message: string }; meta: { resource_version: null; next_cursor: null } };\nexport const marketStatusPathTemplate = "/api/v1/markets/{symbol}/status" as const;\n`;
const domainTsBindings = `export type MarketRawAppendedDataBindingV1 = { raw_event_id: string; collector_session_id: string; source: "binance_spot_public"; stream: string; symbol: "BTCUSDT" | "ETHUSDT" | null; source_dedupe_key: string; source_event_time: string | null; received_at: string; raw_payload_hash: string; classification: "market" | "operational_control" | "quarantined" };\nexport type MarketQualityChangedDataBindingV1 = { quality_event_id: string; scope: string; previous_status: "healthy" | "degraded" | "stale" | "invalid" | "reconnecting"; new_status: "healthy" | "degraded" | "stale" | "invalid" | "reconnecting"; reason_codes: string[]; observed_at: string; raw_event_id: string | null; recovery_required: boolean };\nexport type MarketDomainEventEnvelopeBindingV1<TType extends string, TData> = { spec_version: "woozoo.event/v1"; event_id: string; event_type: TType; event_version: 1; occurred_at: string; published_at: string | null; producer: "market-data-worker"; correlation_id: string; causation_id: string | null; aggregate: { type: string; id: string; version: number }; data: TData; payload_hash: string };\nexport type MarketDomainEventBindingV1 =\n  | MarketDomainEventEnvelopeBindingV1<"market.raw.appended.v1", MarketRawAppendedDataBindingV1>\n  | MarketDomainEventEnvelopeBindingV1<"market.normalized.recorded.v1", MarketEventBindingV1>\n  | MarketDomainEventEnvelopeBindingV1<"market.quality.changed.v1", MarketQualityChangedDataBindingV1>;\n`;
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

const outputs = new Map([
  [resolve(root, "packages/contracts/schema-manifest.json"), manifestJson],
  [resolve(root, "packages/contracts/src/generated-schema-manifest.ts"), tsManifest],
  [resolve(root, "packages/typescript/contract-bindings/src/generated.ts"), `${tsBindings}${marketTsBindings}${domainTsBindings}`],
  [
    resolve(root, "packages/python/platform-core/src/platform_core/generated_contracts.py"),
    strictPyBindings.replaceAll("\n\nclass ", "\n\n\nclass "),
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
