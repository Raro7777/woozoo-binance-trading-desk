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
const promptManifestPath = resolve(root, "packages/contracts/spec/prompt-manifest.v1.json");
const agentReportPath = resolve(root, "packages/contracts/spec/agent-report.v1.json");
const tradeProposalPath = resolve(root, "packages/contracts/spec/trade-proposal.v1.json");
const analysisRunPath = resolve(root, "packages/contracts/spec/analysis-run.v1.json");
const analysisAuditPath = resolve(root, "packages/contracts/spec/analysis-audit.v1.json");
const agentDomainEventsPath = resolve(root, "packages/contracts/spec/agent-domain-events.v1.json");
const riskInputV2Path = resolve(root, "packages/contracts/spec/risk-input.v2.json");
const phase7SpecNames = [
  "analysis-run.v2.json",
  "analysis-run-view.v1.json",
  "risk-input.v3.json",
  "paper-order.v2.json",
  "paper-approval.v1.json",
  "paper-approval-revocation.v1.json",
  "paper-execution-authorization.v1.json",
  "approval-view.v1.json",
  "local-session.v1.json",
  "risk-domain-events.v2.json",
  "paper-domain-events.v2.json",
];
const phase8SpecNames = [
  "testnet-order-preview.v1.json",
  "testnet-risk-input.v1.json",
  "testnet-risk-decision.v1.json",
  "testnet-approval.v1.json",
  "testnet-approval-revocation.v1.json",
  "testnet-execution-authorization.v1.json",
  "testnet-gateway-command.v1.json",
  "testnet-gateway-receipt.v1.json",
  "testnet-order.v1.json",
  "testnet-reconciliation.v1.json",
  "testnet-account-generation.v1.json",
  "testnet-gateway-status.v1.json",
  "testnet-domain-events.v1.json",
];
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
const promptManifest = JSON.parse(await readFile(promptManifestPath, "utf8"));
const agentReport = JSON.parse(await readFile(agentReportPath, "utf8"));
const tradeProposal = JSON.parse(await readFile(tradeProposalPath, "utf8"));
const analysisRun = JSON.parse(await readFile(analysisRunPath, "utf8"));
const analysisAudit = JSON.parse(await readFile(analysisAuditPath, "utf8"));
const agentDomainEvents = JSON.parse(await readFile(agentDomainEventsPath, "utf8"));
const riskInputV2 = JSON.parse(await readFile(riskInputV2Path, "utf8"));
const phase7Specs = Object.fromEntries(await Promise.all(phase7SpecNames.map(async (name) => [
  name,
  JSON.parse(await readFile(resolve(root, "packages/contracts/spec", name), "utf8")),
])));
const phase8Specs = Object.fromEntries(await Promise.all(phase8SpecNames.map(async (name) => [
  name,
  JSON.parse(await readFile(resolve(root, "packages/contracts/spec", name), "utf8")),
])));

const expectedOpenApiPaths = [
  "/api/v1/health",
  "/api/v1/markets/{symbol}/status",
  "/api/v1/evidence/{evidence_id}",
  "/api/v1/commands/evidence-snapshots",
  "/api/v1/session/login",
  "/api/v1/session",
  "/api/v1/session/logout",
  "/api/v1/trading-room",
  "/api/v1/analysis-runs",
  "/api/v1/analysis-runs/{run_id}",
  "/api/v1/risk-decisions/{risk_id}",
  "/api/v1/proposals/{proposal_id}/approval-view",
  "/api/v1/paper-approvals",
  "/api/v1/paper-approvals/{approval_id}/revocations",
  "/api/v1/paper-orders/{order_id}",
  "/api/v1/paper-orders/{order_id}/cancel",
  "/api/v1/paper-portfolio",
  "/api/v1/audit-events",
  "/api/v1/kill-switch",
  "/api/v1/kill-switch/activate",
  "/api/v1/kill-switch/recover",
  "/api/v1/testnet/operator-state",
  "/api/v1/testnet-activations",
  "/api/v1/testnet-activations/{activation_id}/deactivations",
  "/api/v1/proposals/{proposal_id}/testnet-approval-view",
  "/api/v1/testnet-approvals",
  "/api/v1/testnet-approvals/{approval_id}/revocations",
  "/api/v1/testnet-executions/{execution_id}",
  "/api/v1/testnet-reconciliation/{checkpoint_id}/confirmations",
];
if (JSON.stringify(Object.keys(openApi.paths)) !== JSON.stringify(expectedOpenApiPaths)) {
  throw new Error("OpenAPI paths must exactly match the approved Phase 3, Phase 7, and Phase 8 browser API");
}
if (expectedOpenApiPaths.some((path) => path.includes("/internal/"))) {
  throw new Error("Browser OpenAPI must not expose internal service routes");
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
if (
  promptManifest.$id !== "woozoo.prompt-manifest/v1" ||
  agentReport.$id !== "woozoo.agent-report/v1" ||
  tradeProposal.$id !== "woozoo.trade-proposal/v1" ||
  analysisRun.$id !== "woozoo.analysis-run/v1" ||
  analysisAudit.$id !== "woozoo.analysis-audit/v1" ||
  agentDomainEvents.$id !== "woozoo.agent-domain-events/v1" ||
  riskInputV2.$id !== "woozoo.risk-input/v2" ||
  [promptManifest, agentReport, tradeProposal, analysisRun, analysisAudit, riskInputV2]
    .some((contract) => contract.additionalProperties !== false || contract["x-creation-phase"] !== 6 || contract["x-activation-phase"] !== 7) ||
  agentDomainEvents["x-activation-phase"] !== 7 ||
  !Array.isArray(agentDomainEvents.oneOf) || agentDomainEvents.oneOf.length !== 3 ||
  promptManifest.properties?.tool_allowlist?.const?.length !== 0 ||
  riskInputV2.properties?.namespace?.const !== "test"
) {
  throw new Error("P6 Agent contracts must remain closed, tool-free, test-only, and dormant until Phase 7");
}
const expectedPhase7Ids = {
  "analysis-run.v2.json": "woozoo.analysis-run/v2",
  "analysis-run-view.v1.json": "woozoo.analysis-run-view/v1",
  "risk-input.v3.json": "woozoo.risk-input/v3",
  "paper-order.v2.json": "woozoo.paper-order/v2",
  "paper-approval.v1.json": "woozoo.paper-approval/v1",
  "paper-approval-revocation.v1.json": "woozoo.paper-approval-revocation/v1",
  "paper-execution-authorization.v1.json": "woozoo.paper-execution-authorization/v1",
  "approval-view.v1.json": "woozoo.approval-view/v1",
  "local-session.v1.json": "woozoo.local-session/v1",
  "risk-domain-events.v2.json": "woozoo.risk-domain-events/v2",
  "paper-domain-events.v2.json": "woozoo.paper-domain-events/v2",
};
for (const [name, id] of Object.entries(expectedPhase7Ids)) {
  const contract = phase7Specs[name];
  if (
    contract?.$id !== id ||
    contract?.["x-creation-phase"] !== 7 ||
    contract?.["x-activation-phase"] !== 7 ||
    (contract.type === "object" && contract.additionalProperties !== false)
  ) {
    throw new Error(`Phase 7 contract ${name} must be closed and active only in Phase 7`);
  }
}
if (
  phase7Specs["analysis-run.v2.json"].properties?.namespace?.const !== "paper" ||
  phase7Specs["risk-input.v3.json"].properties?.namespace?.const !== "paper" ||
  phase7Specs["risk-input.v3.json"].properties?.preview_policy_version?.const !== "woozoo.paper-order-preview-policy/v1" ||
  phase7Specs["paper-order.v2.json"].properties?.authorization_namespace?.const !== "paper" ||
  phase7Specs["paper-approval.v1.json"].properties?.actor_id?.const !== "operator-local-1" ||
  phase7Specs["paper-approval-revocation.v1.json"].properties?.actor_id?.const !== "operator-local-1"
) {
  throw new Error("Phase 7 production contracts must bind paper namespace and the server actor");
}
const expectedPhase8Ids = Object.fromEntries(phase8SpecNames.map((name) => [
  name,
  `woozoo.${name.replace(".v1.json", "").replaceAll("-", "-")}/v1`,
]));
for (const [name, id] of Object.entries(expectedPhase8Ids)) {
  const contract = phase8Specs[name];
  if (
    contract?.$id !== id ||
    contract?.["x-creation-phase"] !== 8 ||
    contract?.["x-activation-phase"] !== 8 ||
    (contract.type === "object" && contract.additionalProperties !== false)
  ) {
    throw new Error(`Phase 8 contract ${name} must be closed and active only in Phase 8`);
  }
}
if (
  phase8Specs["testnet-risk-input.v1.json"].properties?.namespace?.const !== "testnet" ||
  phase8Specs["testnet-approval.v1.json"].properties?.actor_id?.const !== "operator-local-1" ||
  phase8Specs["testnet-execution-authorization.v1.json"].properties?.namespace?.const !== "testnet" ||
  phase8Specs["testnet-gateway-command.v1.json"].properties?.producer?.const !== "testnet-execution-service" ||
  phase8Specs["testnet-gateway-command.v1.json"].properties?.environment?.const !== "BINANCE_SPOT_TESTNET"
) {
  throw new Error("Phase 8 contracts must bind the separate Testnet namespace, operator, producer, and environment");
}

const cookieScheme = openApi.components?.securitySchemes?.LocalOperatorSession;
if (
  cookieScheme?.type !== "apiKey" || cookieScheme?.in !== "cookie" ||
  cookieScheme?.name !== "__Host-woozoo_session"
) {
  throw new Error("Phase 7 browser auth must use only the hardened host cookie contract");
}
const phase7Mutations = [
  ["/api/v1/session/logout", false],
  ["/api/v1/analysis-runs", false],
  ["/api/v1/paper-approvals", true],
  ["/api/v1/paper-approvals/{approval_id}/revocations", true],
  ["/api/v1/paper-orders/{order_id}/cancel", true],
  ["/api/v1/kill-switch/activate", true],
  ["/api/v1/kill-switch/recover", true],
];
const phase8Mutations = [
  "/api/v1/testnet-activations",
  "/api/v1/testnet-activations/{activation_id}/deactivations",
  "/api/v1/testnet-approvals",
  "/api/v1/testnet-approvals/{approval_id}/revocations",
  "/api/v1/testnet-reconciliation/{checkpoint_id}/confirmations",
];
const loginOperation = openApi.paths["/api/v1/session/login"]?.post;
if (
  JSON.stringify(loginOperation?.security) !== "[]" ||
  JSON.stringify(loginOperation?.parameters) !==
    JSON.stringify([{ $ref: "#/components/parameters/OriginHeader" }])
) {
  throw new Error("Local login must be explicitly unauthenticated and Origin-bound");
}
for (const [path, requiresVersion] of phase7Mutations) {
  const operation = openApi.paths[path]?.post;
  const refs = operation?.parameters?.map((parameter) => parameter.$ref) ?? [];
  const required = [
    "#/components/parameters/OriginHeader",
    "#/components/parameters/CsrfHeader",
    "#/components/parameters/IdempotencyHeader",
  ];
  if (
    operation === undefined || JSON.stringify(operation.security) !== JSON.stringify([{ LocalOperatorSession: [] }]) ||
    required.some((reference) => !refs.includes(reference)) ||
    (requiresVersion && !refs.includes("#/components/parameters/IfMatchHeader"))
  ) {
    throw new Error(`Phase 7 mutation ${path} must bind session, Origin, CSRF, idempotency, and version where mutable`);
  }
}
for (const path of phase8Mutations) {
  const operation = openApi.paths[path]?.post;
  const refs = operation?.parameters?.map((parameter) => parameter.$ref).filter(Boolean) ?? [];
  const required = [
    "#/components/parameters/OriginHeader",
    "#/components/parameters/CsrfHeader",
    "#/components/parameters/IdempotencyHeader",
    "#/components/parameters/IfMatchHeader",
  ];
  if (
    operation === undefined ||
    JSON.stringify(operation.security) !== JSON.stringify([{ LocalOperatorSession: [] }]) ||
    required.some((reference) => !refs.includes(reference))
  ) {
    throw new Error(`Phase 8 mutation ${path} must bind session, Origin, CSRF, idempotency, and version`);
  }
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
  prompt_manifest_spec_version: promptManifest.$id,
  agent_report_spec_version: agentReport.$id,
  trade_proposal_spec_version: tradeProposal.$id,
  analysis_run_spec_version: analysisRun.$id,
  analysis_audit_spec_version: analysisAudit.$id,
  agent_domain_event_spec_version: agentDomainEvents.$id,
  risk_input_v2_spec_version: riskInputV2.$id,
  agent_activation_phase: 7,
  analysis_run_v2_spec_version: phase7Specs["analysis-run.v2.json"].$id,
  analysis_run_view_spec_version: phase7Specs["analysis-run-view.v1.json"].$id,
  risk_input_v3_spec_version: phase7Specs["risk-input.v3.json"].$id,
  paper_order_v2_spec_version: phase7Specs["paper-order.v2.json"].$id,
  paper_approval_spec_version: phase7Specs["paper-approval.v1.json"].$id,
  paper_approval_revocation_spec_version: phase7Specs["paper-approval-revocation.v1.json"].$id,
  paper_execution_authorization_spec_version: phase7Specs["paper-execution-authorization.v1.json"].$id,
  approval_view_spec_version: phase7Specs["approval-view.v1.json"].$id,
  local_session_spec_version: phase7Specs["local-session.v1.json"].$id,
  risk_domain_event_v2_spec_version: phase7Specs["risk-domain-events.v2.json"].$id,
  paper_domain_event_v2_spec_version: phase7Specs["paper-domain-events.v2.json"].$id,
  trading_room_activation_phase: 7,
  testnet_activation_phase: 8,
  testnet_contract_spec_versions: Object.fromEntries(
    phase8SpecNames.map((name) => [name, phase8Specs[name].$id]),
  ),
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
    "prompt-manifest.v1.json": digest(promptManifest),
    "agent-report.v1.json": digest(agentReport),
    "trade-proposal.v1.json": digest(tradeProposal),
    "analysis-run.v1.json": digest(analysisRun),
    "analysis-audit.v1.json": digest(analysisAudit),
    "agent-domain-events.v1.json": digest(agentDomainEvents),
    "risk-input.v2.json": digest(riskInputV2),
    ...Object.fromEntries(phase7SpecNames.map((name) => [name, digest(phase7Specs[name])])),
    ...Object.fromEntries(phase8SpecNames.map((name) => [name, digest(phase8Specs[name])])),
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

const agentTsBindings = `export type AgentRoleBindingV1 = "MARKET_REGIME" | "TECHNICAL" | "TRADE_FLOW" | "BULL" | "BEAR" | "TRADER" | "PORTFOLIO" | "AUDIT";
export type AgentReportBindingV1 = { schema_version: "woozoo.agent-report/v1"; report_id: string; run_id: string; role: AgentRoleBindingV1; evidence_id: string; evidence_digest: string; as_of: string; knowledge_cutoff: string; symbol: "BTCUSDT" | "ETHUSDT"; evidence_item_ids: string[]; dependency_report_ids: string[]; claim_times: string[]; findings: string[]; uncertainty: string[]; invalidation_conditions: string[]; confidence: string; stance: "BUY" | "SELL" | "HOLD" | "NEUTRAL"; provider: "mock"; model: "woozoo-deterministic-mock/v1"; prompt_manifest_hash: string; workflow_hash: string; report_hash: string };
export type TradeProposalBindingV1 = { schema_version: "woozoo.trade-proposal/v1"; proposal_id: string; proposal_version: "v1"; analysis_run_id: string; evidence_id: string; evidence_digest: string; as_of: string; knowledge_cutoff: string; symbol: "BTCUSDT" | "ETHUSDT"; side: "BUY" | "SELL" | "HOLD"; risk_eligible: boolean; report_ids: string[]; report_hashes: string[]; evidence_item_ids: string[]; thesis: string; uncertainty: string[]; invalidation_conditions: string[]; confidence: string; provider: "mock"; model: "woozoo-deterministic-mock/v1"; prompt_manifest_hash: string; workflow_version: "woozoo.agent-workflow/v1"; workflow_hash: string; proposal_hash: string };
export type AnalysisRunBindingV1 = { schema_version: "woozoo.analysis-run/v1"; run_id: string; namespace: "test"; evidence_id: string; evidence_digest: string; symbol: "BTCUSDT" | "ETHUSDT"; as_of: string; knowledge_cutoff: string; workflow_version: "woozoo.agent-workflow/v1"; workflow_hash: string; prompt_manifest_hash: string; provider: "mock"; model: "woozoo-deterministic-mock/v1"; outcome: "COMPLETED" | "HOLD"; hold_reason: string | null; report_ids: string[]; proposal_id: string | null; audit_hash: string };
`;

const agentSchemaPyBindings = `
AGENT_REPORT_SCHEMA: dict[str, object] = json.loads(${JSON.stringify(JSON.stringify(agentReport))})
TRADE_PROPOSAL_SCHEMA: dict[str, object] = json.loads(${JSON.stringify(JSON.stringify(tradeProposal))})
ANALYSIS_RUN_SCHEMA: dict[str, object] = json.loads(${JSON.stringify(JSON.stringify(analysisRun))})
ANALYSIS_AUDIT_SCHEMA: dict[str, object] = json.loads(${JSON.stringify(JSON.stringify(analysisAudit))})
RISK_INPUT_V2_SCHEMA: dict[str, object] = json.loads(${JSON.stringify(JSON.stringify(riskInputV2))})
`;

const phase7TsBindings = `export type PaperApprovalDecisionBindingV1 = "APPROVED" | "REJECTED";
export type ApprovalViewStatusBindingV1 = "PENDING_RISK" | "PENDING_APPROVAL" | "READY" | "APPROVED" | "AUTHORIZATION_ISSUED" | "BLOCKED" | "INVALID";
export type AnalysisRunBindingV2 = { schema_version: "woozoo.analysis-run/v2"; run_id: string; namespace: "paper"; evidence_id: string; evidence_digest: string; symbol: "BTCUSDT" | "ETHUSDT"; as_of: string; knowledge_cutoff: string; workflow_version: "woozoo.agent-workflow/v1"; workflow_hash: string; prompt_manifest_hash: string; provider: "mock"; model: "woozoo-deterministic-mock/v1"; tool_count: 0; outcome: "COMPLETED" | "HOLD"; hold_reason: string | null; report_ids: string[]; proposal_id: string | null; risk_decision_id: string | null; audit_hash: string };
export type AnalysisRunViewBindingV1 = { schema_version: "woozoo.analysis-run-view/v1"; namespace: "paper"; symbol: "BTCUSDT" | "ETHUSDT"; evidence_id: string; provider: "mock"; tool_count: 0; run_id: string; status: "COMPLETED"; report: { summary: string; confidence: string; hold_reasons: string[] }; proposal_id: string; risk_decision_id: string };
export type PaperOrderBindingV2 = { order_id: string; client_order_id: string; authorization_id: string; authorization_namespace: "paper"; authorization_nonce: string; approval_id: string; proposal_hash: string; risk_decision_hash: string; paper_order_preview_hash: string; symbol: "BTCUSDT" | "ETHUSDT"; side: "BUY" | "SELL"; order_type: "LIMIT"; time_in_force: "GTC"; quantity: string; limit_price: string; filled_quantity: string; status: "OPEN" | "PARTIALLY_FILLED" | "FILLED" | "CANCELLED"; version: number };
export type PaperApprovalBindingV1 = { approval_id: string; proposal_id: string; proposal_hash: string; risk_decision_id: string; risk_decision_hash: string; risk_input_digest: string; risk_policy_version: string; paper_order_preview: Record<string, unknown>; paper_order_preview_hash: string; actor_id: "operator-local-1"; session_binding_hash: string; csrf_binding_hash: string; origin_hash: string; decision: PaperApprovalDecisionBindingV1; approval_nonce: string; expected_kill_switch_version: number; expected_portfolio_version: number; expected_ledger_version: number; decided_at: string; expires_at: string; payload_hash: string };
export type PaperApprovalRevocationBindingV1 = { revocation_id: string; approval_id: string; approval_hash: string; actor_id: "operator-local-1"; session_binding_hash: string; csrf_binding_hash: string; origin_hash: string; revocation_nonce: string; reason: string; expected_version: number; revoked_at: string; payload_hash: string };
export type PaperExecutionAuthorizationBindingV1 = { authorization_id: string; namespace: "paper"; approval_id: string; approval_hash: string; approval_nonce_hash: string; authorization_nonce: string; proposal_id: string; proposal_hash: string; risk_decision_id: string; risk_decision_hash: string; risk_input_digest: string; risk_policy_version: string; paper_order_preview_hash: string; authorization_input_digest: string; current_data_state_hash: string; current_data_as_of: string; current_knowledge_cutoff: string; kill_switch_version: number; reconciliation_checkpoint_hash: string; ledger_snapshot_hash: string; paper_account_id: string; issued_at: string; expires_at: string };
export type LocalSessionBindingV1 = { actor_id: "operator-local-1"; issued_at: string; idle_expires_at: string; absolute_expires_at: string; csrf_token: string; csrf_expires_at: string };
export type KillRecoveryDataBindingV2 = { recovery_event_id: string; scope: "paper-global"; active: false; prior_version: number; version: number; actor_id: "operator-local-1"; session_binding_hash: string; csrf_binding_hash: string; origin_hash: string; incident_reference: string; reason: string; observed_at: string; context_digest: string; data_status: "HEALTHY"; data_state_hash: string; reconciliation_status: "PASS"; reconciliation_checkpoint_hash: string; ledger_status: "BALANCED"; ledger_snapshot_hash: string };
`;

const phase8TsBindings = `export type TestnetEnvironmentBindingV1 = "BINANCE_SPOT_TESTNET";
export type TestnetGatewayHealthBindingV1 = "DISABLED" | "ACTIVATING" | "READY" | "DEGRADED" | "UNKNOWN" | "RESET_HOLD" | "KILLED";
export type TestnetReconciliationStatusBindingV1 = "HEALTHY" | "RUNNING" | "FAILED" | "RESET_SUSPECTED" | "AWAITING_OPERATOR_CONFIRMATION" | "UNKNOWN";
export type TestnetOrderPreviewBindingV1 = { schema_version: "woozoo.testnet-order-preview/v1"; environment: TestnetEnvironmentBindingV1; account_binding_id: string; account_generation: number; proposal_id: string; proposal_hash: string; evidence_id: string; evidence_digest: string; data_state_digest: string; as_of: string; knowledge_cutoff: string; symbol: "BTCUSDT" | "ETHUSDT"; side: "BUY" | "SELL"; order_type: "LIMIT"; time_in_force: "GTC"; quantity: string; limit_price: string; worst_case_notional: string; fee_reserve: string; preview_policy_version: "woozoo.testnet-order-preview-policy/v1"; calculator_version: "woozoo.decimal-calculator/v1"; symbol_rules_digest: string; ledger_snapshot_digest: string; reconciliation_checkpoint_digest: string; paper_kill_version: number; testnet_barrier_version: number; client_order_id: string; created_at: string; expires_at: string; testnet_order_preview_digest: string };
export type TestnetApprovalBindingV1 = { schema_version: "woozoo.testnet-approval/v1"; approval_id: string; proposal_id: string; proposal_hash: string; risk_decision_id: string; risk_decision_hash: string; risk_input_digest: string; risk_policy_version: "woozoo.testnet-risk-policy/v1"; preview_policy_version: "woozoo.testnet-order-preview-policy/v1"; environment: TestnetEnvironmentBindingV1; account_binding_id: string; account_generation: number; testnet_order_preview: TestnetOrderPreviewBindingV1; testnet_order_preview_digest: string; approval_input_digest: string; actor_id: "operator-local-1"; session_binding_hash: string; csrf_binding_hash: string; origin_hash: string; decision: "APPROVED" | "REJECTED"; validity: "ACTIVE" | "EXPIRED" | "REVOKED" | "INVALIDATED"; approval_nonce: string; expected_activation_version: number; expected_paper_kill_version: number; expected_testnet_barrier_version: number; expected_ledger_version: number; expected_reconciliation_version: number; approved_at: string; expires_at: string; revocation_state: "NOT_REVOKED" | "REVOKED"; payload_hash: string };
export type TestnetExecutionAuthorizationBindingV1 = { schema_version: "woozoo.testnet-execution-authorization/v1"; authorization_id: string; namespace: "testnet"; environment: TestnetEnvironmentBindingV1; account_binding_id: string; account_generation: number; approval_id: string; approval_hash: string; approval_nonce_hash: string; authorization_nonce: string; proposal_id: string; proposal_hash: string; risk_decision_id: string; risk_decision_hash: string; risk_input_digest: string; risk_policy_version: "woozoo.testnet-risk-policy/v1"; preview_policy_version: "woozoo.testnet-order-preview-policy/v1"; testnet_order_preview_digest: string; authorization_input_digest: string; data_state_digest: string; activation_version: number; paper_kill_version: number; testnet_barrier_version: number; gateway_configuration_digest: string; gateway_allowlist_digest: string; reconciliation_checkpoint_digest: string; ledger_snapshot_digest: string; client_order_id: string; issued_at: string; expires_at: string };
export type TestnetGatewayCommandBindingV1 = { schema_version: "woozoo.testnet-gateway-command/v1"; command_id: string; command_type: "SUBMIT_LIMIT_ORDER" | "CANCEL_EXISTING_ORDER" | "QUERY_EXISTING_ORDER" | "RECONCILIATION_OBSERVATION"; effect_class: "CREATE_ORDER" | "REDUCE_OR_CANCEL" | "OBSERVE_ONLY"; producer: "testnet-execution-service"; environment: TestnetEnvironmentBindingV1; account_binding_id: string; account_generation: number; idempotency_key: string; request_digest: string; correlation_id: string; causation_id: string; capability_id: "SPOT_TESTNET_SUBMIT_LIMIT_GTC" | "SPOT_TESTNET_CANCEL_BY_CLIENT_ID" | "SPOT_TESTNET_QUERY_BY_CLIENT_ID" | "SPOT_TESTNET_RECONCILIATION"; gateway_allowlist_digest: string; gateway_configuration_digest: string; activation_version: number; testnet_barrier_version: number; original_authorization_id: string; original_approval_id: string; original_proposal_hash: string; original_risk_decision_hash: string; client_order_id: string; symbol: "BTCUSDT" | "ETHUSDT"; side: "BUY" | "SELL"; quantity: string; limit_price: string; query_reason: "INITIAL_SUBMIT" | "OPERATOR_CANCEL" | "SAFETY_CANCEL" | "UNKNOWN_OUTCOME" | "PERIODIC_RECONCILIATION"; issued_at: string; expires_at: string };
export type TestnetGatewayReceiptBindingV1 = { schema_version: "woozoo.testnet-gateway-receipt/v1"; receipt_id: string; command_id: string; request_digest: string; environment: TestnetEnvironmentBindingV1; account_binding_id: string; account_generation: number; client_order_id: string; status: "ACCEPTED" | "REJECTED_LOCAL" | "DISPATCH_RECORDED" | "SUBMISSION_UNKNOWN" | "EXCHANGE_ACKNOWLEDGED" | "RESOLUTION_REQUIRED" | "RESOLVED_FOUND" | "RESOLVED_REJECTED" | "RESOLVED_NOT_FOUND_CONFIRMED"; external_effect_count: number; same_client_order_id_query_count: number; observed_at: string; payload_hash: string };
export type TestnetOperatorStateBindingV1 = { environment: TestnetEnvironmentBindingV1; account_binding_id: string; account_label: string; account_generation: number; activation_id: string | null; activation_status: "DISABLED" | "PENDING" | "ACTIVE" | "EXPIRED" | "REVOKED"; activation_version: number; activation_view_digest: string; gateway_health: TestnetGatewayHealthBindingV1; capability_allowlist_digest: string; configuration_digest: string; testnet_barrier_status: "ACTIVE" | "INACTIVE"; paper_kill_active: boolean; reconciliation_status: TestnetReconciliationStatusBindingV1; reconciliation_checkpoint_id: string | null; reconciliation_checkpoint_digest: string | null; reset_state: "NONE" | "SUSPECTED" | "AWAITING_CONFIRMATION" | "CONFIRMED"; new_commands_allowed: boolean; reason_codes: string[]; activate_action_allowed: boolean; deactivate_action_allowed: boolean; reset_confirmation_allowed: boolean; served_at: string };
export type TestnetApprovalCommandBindingV1 = { proposal_id: string; decision: "APPROVE" | "REJECT"; expected_version: number; testnet_order_preview_digest: string; approval_input_digest: string; reason: string };
`;

const phase8PyBindings = `
TestnetEnvironmentBindingV1 = Literal["BINANCE_SPOT_TESTNET"]
TestnetGatewayHealthBindingV1 = Literal["DISABLED", "ACTIVATING", "READY", "DEGRADED", "UNKNOWN", "RESET_HOLD", "KILLED"]
TestnetReconciliationStatusBindingV1 = Literal["HEALTHY", "RUNNING", "FAILED", "RESET_SUSPECTED", "AWAITING_OPERATOR_CONFIRMATION", "UNKNOWN"]

class TestnetGatewayCommandBindingV1(TypedDict):
    schema_version: Literal["woozoo.testnet-gateway-command/v1"]
    command_id: str
    command_type: Literal["SUBMIT_LIMIT_ORDER", "CANCEL_EXISTING_ORDER", "QUERY_EXISTING_ORDER", "RECONCILIATION_OBSERVATION"]
    effect_class: Literal["CREATE_ORDER", "REDUCE_OR_CANCEL", "OBSERVE_ONLY"]
    producer: Literal["testnet-execution-service"]
    environment: TestnetEnvironmentBindingV1
    account_binding_id: str
    account_generation: int
    idempotency_key: str
    request_digest: str
    correlation_id: str
    causation_id: str
    capability_id: str
    gateway_allowlist_digest: str
    gateway_configuration_digest: str
    activation_version: int
    testnet_barrier_version: int
    original_authorization_id: str
    original_approval_id: str
    original_proposal_hash: str
    original_risk_decision_hash: str
    client_order_id: str
    symbol: Literal["BTCUSDT", "ETHUSDT"]
    side: Literal["BUY", "SELL"]
    quantity: str
    limit_price: str
    query_reason: str
    issued_at: str
    expires_at: str

TESTNET_ORDER_PREVIEW_SCHEMA: dict[str, object] = json.loads(${JSON.stringify(JSON.stringify(phase8Specs["testnet-order-preview.v1.json"]))})
TESTNET_RISK_INPUT_SCHEMA: dict[str, object] = json.loads(${JSON.stringify(JSON.stringify(phase8Specs["testnet-risk-input.v1.json"]))})
TESTNET_RISK_DECISION_SCHEMA: dict[str, object] = json.loads(${JSON.stringify(JSON.stringify(phase8Specs["testnet-risk-decision.v1.json"]))})
TESTNET_APPROVAL_SCHEMA: dict[str, object] = json.loads(${JSON.stringify(JSON.stringify(phase8Specs["testnet-approval.v1.json"]))})
TESTNET_APPROVAL_REVOCATION_SCHEMA: dict[str, object] = json.loads(${JSON.stringify(JSON.stringify(phase8Specs["testnet-approval-revocation.v1.json"]))})
TESTNET_EXECUTION_AUTHORIZATION_SCHEMA: dict[str, object] = json.loads(${JSON.stringify(JSON.stringify(phase8Specs["testnet-execution-authorization.v1.json"]))})
TESTNET_GATEWAY_COMMAND_SCHEMA: dict[str, object] = json.loads(${JSON.stringify(JSON.stringify(phase8Specs["testnet-gateway-command.v1.json"]))})
TESTNET_GATEWAY_RECEIPT_SCHEMA: dict[str, object] = json.loads(${JSON.stringify(JSON.stringify(phase8Specs["testnet-gateway-receipt.v1.json"]))})
TESTNET_ORDER_SCHEMA: dict[str, object] = json.loads(${JSON.stringify(JSON.stringify(phase8Specs["testnet-order.v1.json"]))})
TESTNET_RECONCILIATION_SCHEMA: dict[str, object] = json.loads(${JSON.stringify(JSON.stringify(phase8Specs["testnet-reconciliation.v1.json"]))})
TESTNET_ACCOUNT_GENERATION_SCHEMA: dict[str, object] = json.loads(${JSON.stringify(JSON.stringify(phase8Specs["testnet-account-generation.v1.json"]))})
TESTNET_GATEWAY_STATUS_SCHEMA: dict[str, object] = json.loads(${JSON.stringify(JSON.stringify(phase8Specs["testnet-gateway-status.v1.json"]))})
TESTNET_DOMAIN_EVENTS_SCHEMA: dict[str, object] = json.loads(${JSON.stringify(JSON.stringify(phase8Specs["testnet-domain-events.v1.json"]))})
`;

const phase7PyBindings = `
PaperApprovalDecisionBindingV1 = Literal["APPROVED", "REJECTED"]
ApprovalViewStatusBindingV1 = Literal["PENDING_RISK", "PENDING_APPROVAL", "READY", "APPROVED", "AUTHORIZATION_ISSUED", "BLOCKED", "INVALID"]

class AnalysisRunBindingV2(TypedDict):
    schema_version: Literal["woozoo.analysis-run/v2"]
    run_id: str
    namespace: Literal["paper"]
    evidence_id: str
    evidence_digest: str
    symbol: Literal["BTCUSDT", "ETHUSDT"]
    as_of: str
    knowledge_cutoff: str
    workflow_version: Literal["woozoo.agent-workflow/v1"]
    workflow_hash: str
    prompt_manifest_hash: str
    provider: Literal["mock"]
    model: Literal["woozoo-deterministic-mock/v1"]
    tool_count: Literal[0]
    outcome: Literal["COMPLETED", "HOLD"]
    hold_reason: str | None
    report_ids: list[str]
    proposal_id: str | None
    risk_decision_id: str | None
    audit_hash: str

class AnalysisReportViewBindingV1(TypedDict):
    summary: str
    confidence: str
    hold_reasons: list[str]

class AnalysisRunViewBindingV1(TypedDict):
    schema_version: Literal["woozoo.analysis-run-view/v1"]
    namespace: Literal["paper"]
    symbol: Literal["BTCUSDT", "ETHUSDT"]
    evidence_id: str
    provider: Literal["mock"]
    tool_count: Literal[0]
    run_id: str
    status: Literal["COMPLETED"]
    report: AnalysisReportViewBindingV1
    proposal_id: str
    risk_decision_id: str

class PaperOrderBindingV2(TypedDict):
    order_id: str
    client_order_id: str
    authorization_id: str
    authorization_namespace: Literal["paper"]
    authorization_nonce: str
    approval_id: str
    proposal_hash: str
    risk_decision_hash: str
    paper_order_preview_hash: str
    symbol: Literal["BTCUSDT", "ETHUSDT"]
    side: Literal["BUY", "SELL"]
    order_type: Literal["LIMIT"]
    time_in_force: Literal["GTC"]
    quantity: str
    limit_price: str
    filled_quantity: str
    status: Literal["OPEN", "PARTIALLY_FILLED", "FILLED", "CANCELLED"]
    version: int

class PaperApprovalBindingV1(TypedDict):
    approval_id: str
    proposal_id: str
    proposal_hash: str
    risk_decision_id: str
    risk_decision_hash: str
    risk_input_digest: str
    risk_policy_version: str
    paper_order_preview: dict[str, object]
    paper_order_preview_hash: str
    actor_id: Literal["operator-local-1"]
    session_binding_hash: str
    csrf_binding_hash: str
    origin_hash: str
    decision: PaperApprovalDecisionBindingV1
    approval_nonce: str
    expected_kill_switch_version: int
    expected_portfolio_version: int
    expected_ledger_version: int
    decided_at: str
    expires_at: str
    payload_hash: str

class PaperApprovalRevocationBindingV1(TypedDict):
    revocation_id: str
    approval_id: str
    approval_hash: str
    actor_id: Literal["operator-local-1"]
    session_binding_hash: str
    csrf_binding_hash: str
    origin_hash: str
    revocation_nonce: str
    reason: str
    expected_version: int
    revoked_at: str
    payload_hash: str

class PaperExecutionAuthorizationBindingV1(TypedDict):
    authorization_id: str
    namespace: Literal["paper"]
    approval_id: str
    approval_hash: str
    approval_nonce_hash: str
    authorization_nonce: str
    proposal_id: str
    proposal_hash: str
    risk_decision_id: str
    risk_decision_hash: str
    risk_input_digest: str
    risk_policy_version: str
    paper_order_preview_hash: str
    authorization_input_digest: str
    current_data_state_hash: str
    current_data_as_of: str
    current_knowledge_cutoff: str
    kill_switch_version: int
    reconciliation_checkpoint_hash: str
    ledger_snapshot_hash: str
    paper_account_id: str
    issued_at: str
    expires_at: str

class LocalSessionBindingV1(TypedDict):
    actor_id: Literal["operator-local-1"]
    issued_at: str
    idle_expires_at: str
    absolute_expires_at: str
    csrf_token: str
    csrf_expires_at: str

class KillRecoveryDataBindingV2(TypedDict):
    recovery_event_id: str
    scope: Literal["paper-global"]
    active: Literal[False]
    prior_version: int
    version: int
    actor_id: Literal["operator-local-1"]
    session_binding_hash: str
    csrf_binding_hash: str
    origin_hash: str
    incident_reference: str
    reason: str
    observed_at: str
    context_digest: str
    data_status: Literal["HEALTHY"]
    data_state_hash: str
    reconciliation_status: Literal["PASS"]
    reconciliation_checkpoint_hash: str
    ledger_status: Literal["BALANCED"]
    ledger_snapshot_hash: str

RISK_INPUT_V3_SCHEMA: dict[str, object] = json.loads(${JSON.stringify(JSON.stringify(phase7Specs["risk-input.v3.json"]))})
ANALYSIS_RUN_V2_SCHEMA: dict[str, object] = json.loads(${JSON.stringify(JSON.stringify(phase7Specs["analysis-run.v2.json"]))})
ANALYSIS_RUN_VIEW_SCHEMA: dict[str, object] = json.loads(${JSON.stringify(JSON.stringify(phase7Specs["analysis-run-view.v1.json"]))})
PAPER_APPROVAL_SCHEMA: dict[str, object] = json.loads(${JSON.stringify(JSON.stringify(phase7Specs["paper-approval.v1.json"]))})
PAPER_APPROVAL_REVOCATION_SCHEMA: dict[str, object] = json.loads(${JSON.stringify(JSON.stringify(phase7Specs["paper-approval-revocation.v1.json"]))})
PAPER_EXECUTION_AUTHORIZATION_SCHEMA: dict[str, object] = json.loads(${JSON.stringify(JSON.stringify(phase7Specs["paper-execution-authorization.v1.json"]))})
PAPER_ORDER_V2_SCHEMA: dict[str, object] = json.loads(${JSON.stringify(JSON.stringify(phase7Specs["paper-order.v2.json"]))})
APPROVAL_VIEW_SCHEMA: dict[str, object] = json.loads(${JSON.stringify(JSON.stringify(phase7Specs["approval-view.v1.json"]))})
LOCAL_SESSION_SCHEMA: dict[str, object] = json.loads(${JSON.stringify(JSON.stringify(phase7Specs["local-session.v1.json"]))})
RISK_DOMAIN_EVENTS_V2_SCHEMA: dict[str, object] = json.loads(${JSON.stringify(JSON.stringify(phase7Specs["risk-domain-events.v2.json"]))})
PAPER_DOMAIN_EVENTS_V2_SCHEMA: dict[str, object] = json.loads(${JSON.stringify(JSON.stringify(phase7Specs["paper-domain-events.v2.json"]))})
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
export const PROMPT_MANIFEST_SPEC_VERSION = ${JSON.stringify(manifest.prompt_manifest_spec_version)} as const;
export const AGENT_REPORT_SPEC_VERSION = ${JSON.stringify(manifest.agent_report_spec_version)} as const;
export const TRADE_PROPOSAL_SPEC_VERSION = ${JSON.stringify(manifest.trade_proposal_spec_version)} as const;
export const ANALYSIS_RUN_SPEC_VERSION = ${JSON.stringify(manifest.analysis_run_spec_version)} as const;
export const ANALYSIS_AUDIT_SPEC_VERSION = ${JSON.stringify(manifest.analysis_audit_spec_version)} as const;
export const AGENT_DOMAIN_EVENT_SPEC_VERSION = ${JSON.stringify(manifest.agent_domain_event_spec_version)} as const;
export const RISK_INPUT_V2_SPEC_VERSION = ${JSON.stringify(manifest.risk_input_v2_spec_version)} as const;
export const AGENT_ACTIVATION_PHASE = ${JSON.stringify(manifest.agent_activation_phase)} as const;
export const ANALYSIS_RUN_V2_SPEC_VERSION = ${JSON.stringify(manifest.analysis_run_v2_spec_version)} as const;
export const RISK_INPUT_V3_SPEC_VERSION = ${JSON.stringify(manifest.risk_input_v3_spec_version)} as const;
export const PAPER_ORDER_V2_SPEC_VERSION = ${JSON.stringify(manifest.paper_order_v2_spec_version)} as const;
export const PAPER_APPROVAL_SPEC_VERSION = ${JSON.stringify(manifest.paper_approval_spec_version)} as const;
export const PAPER_APPROVAL_REVOCATION_SPEC_VERSION = ${JSON.stringify(manifest.paper_approval_revocation_spec_version)} as const;
export const PAPER_EXECUTION_AUTHORIZATION_SPEC_VERSION = ${JSON.stringify(manifest.paper_execution_authorization_spec_version)} as const;
export const APPROVAL_VIEW_SPEC_VERSION = ${JSON.stringify(manifest.approval_view_spec_version)} as const;
export const LOCAL_SESSION_SPEC_VERSION = ${JSON.stringify(manifest.local_session_spec_version)} as const;
export const RISK_DOMAIN_EVENT_V2_SPEC_VERSION = ${JSON.stringify(manifest.risk_domain_event_v2_spec_version)} as const;
export const PAPER_DOMAIN_EVENT_V2_SPEC_VERSION = ${JSON.stringify(manifest.paper_domain_event_v2_spec_version)} as const;
export const TRADING_ROOM_ACTIVATION_PHASE = ${JSON.stringify(manifest.trading_room_activation_phase)} as const;
export const TESTNET_ACTIVATION_PHASE = ${JSON.stringify(manifest.testnet_activation_phase)} as const;
export const TESTNET_CONTRACT_SPEC_VERSIONS = ${JSON.stringify(manifest.testnet_contract_spec_versions, null, 2)} as const;
`;

const outputs = new Map([
  [resolve(root, "packages/contracts/schema-manifest.json"), manifestJson],
  [resolve(root, "packages/contracts/src/generated-schema-manifest.ts"), `${tsManifest}${riskTsManifest}`],
  [resolve(root, "packages/typescript/contract-bindings/src/generated.ts"), `${tsBindings}${marketTsBindings}${domainTsBindings}${strictEvidenceTsBindings}${paperTsBindings}${riskTsBindings}${agentTsBindings}${phase7TsBindings}${phase8TsBindings}`],
  [
    resolve(root, "packages/python/platform-core/src/platform_core/generated_contracts.py"),
    `${strictPyBindings}${riskSchemaPyBindings}${agentSchemaPyBindings}${evidencePyBindings}${paperPyBindings}${paperPyEventBindings}${riskPyBindings}${phase7PyBindings}${phase8PyBindings}`
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
