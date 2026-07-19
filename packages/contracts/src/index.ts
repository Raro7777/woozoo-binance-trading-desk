import { createHash } from "node:crypto";

import {
  API_VERSION,
  EVIDENCE_DOMAIN_EVENT_SPEC_VERSION,
  EVIDENCE_PATH_TEMPLATE,
  EVIDENCE_SNAPSHOT_SPEC_VERSION,
  EVENT_SPEC_VERSION,
  HEALTH_PATH,
  MARKET_EVENT_SPEC_VERSION,
  MARKET_SOURCE,
  MARKET_STATUS_PATH_TEMPLATE,
} from "./generated-schema-manifest.js";

export {
  API_VERSION,
  EVENT_SPEC_VERSION,
  EVIDENCE_DOMAIN_EVENT_SPEC_VERSION,
  EVIDENCE_PATH_TEMPLATE,
  EVIDENCE_SNAPSHOT_SPEC_VERSION,
  HEALTH_PATH,
  MARKET_EVENT_SPEC_VERSION,
  MARKET_SOURCE,
  MARKET_STATUS_PATH_TEMPLATE,
};

export type HealthDataV1 = {
  service: "control-api";
  status: "healthy" | "degraded";
  trading_mode: "paper";
  dependencies: Record<string, unknown>;
};

export type HealthEnvelopeV1 = {
  api_version: "v1";
  request_id: string;
  correlation_id: string;
  served_at: string;
  data: HealthDataV1;
  meta: { resource_version: null; next_cursor: null };
};

export type CanonicalRequestHashInputV1 = {
  contract_version: "command-request-hash.v1";
  method: string;
  path: string;
  principal: string;
  authorization_scope: string;
  body: Record<string, unknown>;
  referenced_hashes: readonly string[];
};

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function assertExactKeys(value: Record<string, unknown>, keys: readonly string[], label: string): void {
  const allowed = new Set(keys);
  if (Object.keys(value).some((key) => !allowed.has(key)) || Object.keys(value).length !== keys.length) {
    throw new TypeError(`${label} has unknown or missing fields`);
  }
}

function canonicalize(value: unknown): unknown {
  if (value === null || typeof value === "string" || typeof value === "boolean") {
    return value;
  }
  if (typeof value === "number") {
    if (!Number.isInteger(value)) {
      throw new TypeError("canonical request hashes do not accept floating point values");
    }
    return value;
  }
  if (Array.isArray(value)) {
    return value.map(canonicalize);
  }
  if (isPlainObject(value)) {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonicalize(value[key])]),
    );
  }
  throw new TypeError("canonical request hash received an unsupported value");
}

export function canonicalRequestHash(input: CanonicalRequestHashInputV1): string {
  return createHash("sha256").update(JSON.stringify(canonicalize(input))).digest("hex");
}

export function assertClosedHealthEnvelope(value: unknown): asserts value is HealthEnvelopeV1 {
  if (!isPlainObject(value)) {
    throw new TypeError("health envelope must be an object");
  }
  assertExactKeys(value, ["api_version", "request_id", "correlation_id", "served_at", "data", "meta"], "health envelope");
  if (
    value.api_version !== API_VERSION ||
    typeof value.request_id !== "string" ||
    typeof value.correlation_id !== "string" ||
    typeof value.served_at !== "string" ||
    !isPlainObject(value.data) ||
    !isPlainObject(value.meta)
  ) {
    throw new TypeError("health envelope does not satisfy v1");
  }
  assertExactKeys(value.data, ["service", "status", "trading_mode", "dependencies"], "health data");
  if (
    value.data.service !== "control-api" ||
    (value.data.status !== "healthy" && value.data.status !== "degraded") ||
    value.data.trading_mode !== "paper" ||
    !isPlainObject(value.data.dependencies)
  ) {
    throw new TypeError("health data does not satisfy v1");
  }
  assertExactKeys(value.data.dependencies, ["postgres", "redis"], "health dependencies");
  const postgres = value.data.dependencies.postgres;
  const redis = value.data.dependencies.redis;
  if (!isPlainObject(postgres) || !isPlainObject(redis)) {
    throw new TypeError("health dependencies do not satisfy v1");
  }
  assertExactKeys(postgres, ["required", "status"], "postgres dependency");
  assertExactKeys(redis, ["required", "authoritative", "status"], "redis dependency");
  if (
    postgres.required !== true ||
    postgres.status !== "healthy" ||
    redis.required !== false ||
    redis.authoritative !== false ||
    (redis.status !== "healthy" && redis.status !== "unavailable")
  ) {
    throw new TypeError("health dependencies do not satisfy v1");
  }
  assertExactKeys(value.meta, ["resource_version", "next_cursor"], "health metadata");
  if (value.meta.resource_version !== null || value.meta.next_cursor !== null) {
    throw new TypeError("health metadata does not satisfy v1");
  }
}
