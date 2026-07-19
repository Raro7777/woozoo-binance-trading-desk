import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const openApiPath = resolve(root, "packages/contracts/spec/openapi.v1.json");
const eventPath = resolve(root, "packages/contracts/spec/event-envelope.v1.json");
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

if (Object.keys(openApi.paths).join(",") !== "/api/v1/health") {
  throw new Error("P1 OpenAPI may expose only /api/v1/health");
}
if (openApi.info.version !== "v1" || eventEnvelope.$id !== "woozoo.event/v1") {
  throw new Error("P1 contract versions must remain v1");
}

const schemas = openApi.components?.schemas;
const healthOperation = openApi.paths["/api/v1/health"]?.get;
if (schemas === undefined || healthOperation === undefined) {
  throw new Error("P1 OpenAPI must define the health operation and components");
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
propertyRef(dependencies, "postgres", "#/components/schemas/PostgresDependencyV1");
propertyRef(dependencies, "redis", "#/components/schemas/RedisDependencyV1");
propertyRef(healthData, "dependencies", "#/components/schemas/HealthDependenciesV1");
propertyRef(healthEnvelope, "data", "#/components/schemas/HealthDataV1");
propertyRef(healthEnvelope, "meta", "#/components/schemas/MetaV1");
propertyRef(errorEnvelope, "error", "#/components/schemas/ErrorV1");
propertyRef(errorEnvelope, "meta", "#/components/schemas/MetaV1");
if (meta.properties?.resource_version?.type !== "null" || meta.properties?.next_cursor?.type !== "null") {
  throw new Error("P1 metadata must not expose resource versions or cursors");
}

const manifest = {
  api_version: openApi.info.version,
  event_spec_version: eventEnvelope.$id,
  health_path: "/api/v1/health",
  sources: {
    "openapi.v1.json": digest(openApi),
    "event-envelope.v1.json": digest(eventEnvelope),
  },
};
const manifestJson = `${JSON.stringify(manifest, null, 2)}\n`;
const tsManifest = `// GENERATED by scripts/generate-contracts.mjs. Do not edit.\nexport const API_VERSION = ${JSON.stringify(manifest.api_version)} as const;\nexport const EVENT_SPEC_VERSION = ${JSON.stringify(manifest.event_spec_version)} as const;\nexport const HEALTH_PATH = ${JSON.stringify(manifest.health_path)} as const;\nexport const CONTRACT_SOURCE_DIGESTS = ${JSON.stringify(manifest.sources, null, 2)} as const;\n`;
const tsBindings = `// GENERATED by scripts/generate-contracts.mjs. Do not edit.\nexport type PostgresDependencyBindingV1 = { required: ${literal(postgres, "required")}; status: ${JSON.stringify(literal(postgres, "status"))} };\nexport type RedisDependencyBindingV1 = { required: ${literal(redis, "required")}; authoritative: ${literal(redis, "authoritative")}; status: ${stringUnion(redis, "status")} };\nexport type PlatformHealthBindingV1 = {\n  api_version: ${JSON.stringify(literal(healthEnvelope, "api_version"))};\n  request_id: string;\n  correlation_id: string;\n  served_at: string;\n  data: {\n    service: ${JSON.stringify(literal(healthData, "service"))};\n    status: ${stringUnion(healthData, "status")};\n    trading_mode: ${JSON.stringify(literal(healthData, "trading_mode"))};\n    dependencies: { postgres: PostgresDependencyBindingV1; redis: RedisDependencyBindingV1 };\n  };\n  meta: { resource_version: null; next_cursor: null };\n};\nexport type DependencyUnavailableBindingV1 = {\n  api_version: ${JSON.stringify(literal(errorEnvelope, "api_version"))};\n  request_id: string;\n  correlation_id: string;\n  served_at: string;\n  error: { code: ${JSON.stringify(literal(error, "code"))}; message: ${JSON.stringify(literal(error, "message"))} };\n  meta: { resource_version: null; next_cursor: null };\n};\nexport const healthPath = "/api/v1/health" as const;\n`;
const pySourceDigests = Object.entries(manifest.sources)
  .map(([name, value]) => `    ${JSON.stringify(name)}: ${JSON.stringify(value)},`)
  .join("\n");
const pyBindings = `# GENERATED by scripts/generate-contracts.mjs. Do not edit.\nAPI_VERSION = "v1"\nEVENT_SPEC_VERSION = "woozoo.event/v1"\nHEALTH_PATH = "/api/v1/health"\nCONTRACT_SOURCE_DIGESTS = {\n${pySourceDigests}\n}\n`;

const outputs = new Map([
  [resolve(root, "packages/contracts/schema-manifest.json"), manifestJson],
  [resolve(root, "packages/contracts/src/generated-schema-manifest.ts"), tsManifest],
  [resolve(root, "packages/typescript/contract-bindings/src/generated.ts"), tsBindings],
  [resolve(root, "packages/python/platform-core/src/platform_core/generated_contracts.py"), pyBindings],
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
