import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import test from "node:test";

import {
  API_VERSION,
  assertClosedHealthEnvelope,
  canonicalRequestHash,
} from "../../packages/contracts/src/index.js";
import type {
  DependencyUnavailableBindingV1,
  PlatformHealthBindingV1,
} from "../../packages/typescript/contract-bindings/src/index.js";

test("CONTRACT-001 keeps the P1 v1 surface closed and deterministic", () => {
  assert.equal(API_VERSION, "v1");
  const health: PlatformHealthBindingV1 = {
    api_version: "v1",
    request_id: "018f6f5d-1111-7aaa-8111-111111111111",
    correlation_id: "018f6f5d-1111-7aaa-8111-111111111111",
    served_at: "2026-07-19T00:00:00Z",
    data: {
      service: "control-api",
      status: "healthy",
      trading_mode: "paper",
      dependencies: {
        postgres: { required: true, status: "healthy" },
        redis: { required: false, authoritative: false, status: "healthy" },
      },
    },
    meta: { resource_version: null, next_cursor: null },
  };
  assert.doesNotThrow(() => assertClosedHealthEnvelope(health));
  const unavailable: DependencyUnavailableBindingV1 = {
    api_version: "v1",
    request_id: "018f6f5d-1111-7aaa-8111-111111111111",
    correlation_id: "018f6f5d-1111-7aaa-8111-111111111111",
    served_at: "2026-07-19T00:00:00Z",
    error: {
      code: "DEPENDENCY_UNAVAILABLE",
      message: "a required platform dependency is unavailable",
    },
    meta: { resource_version: null, next_cursor: null },
  };
  assert.equal(unavailable.error.code, "DEPENDENCY_UNAVAILABLE");
  assert.throws(() =>
    assertClosedHealthEnvelope({
      api_version: "v1",
      request_id: "018f6f5d-1111-7aaa-8111-111111111111",
      correlation_id: "018f6f5d-1111-7aaa-8111-111111111111",
      served_at: "2026-07-19T00:00:00Z",
      data: {
        service: "control-api",
        status: "healthy",
        trading_mode: "paper",
        dependencies: {
          postgres: { required: true, status: "healthy" },
          redis: { required: false, authoritative: false, status: "healthy" },
        },
      },
      meta: { resource_version: null, next_cursor: null },
      forbidden: true,
    }),
  );
  assert.notEqual(
    canonicalRequestHash({
      contract_version: "command-request-hash.v1",
      method: "GET",
      path: "/api/v1/health",
      principal: "platform-service",
      authorization_scope: "health:read",
      body: {},
      referenced_hashes: [],
    }),
    canonicalRequestHash({
      contract_version: "command-request-hash.v1",
      method: "GET",
      path: "/api/v1/health",
      principal: "another-platform-service",
      authorization_scope: "health:read",
      body: {},
      referenced_hashes: [],
    }),
  );
});

test("CONTRACT-001 links complete health responses to generated bindings", async () => {
  const root = resolve(import.meta.dirname, "../..");
  const openApi = JSON.parse(
    await readFile(resolve(root, "packages/contracts/spec/openapi.v1.json"), "utf8"),
  ) as {
    paths: Record<string, { get: { responses: Record<string, unknown> } }>;
  };
  const bindingSource = await readFile(
    resolve(root, "packages/typescript/contract-bindings/src/generated.ts"),
    "utf8",
  );

  assert.deepEqual(openApi.paths["/api/v1/health"].get.responses["200"], {
    description: "platform health",
    content: {
      "application/json": { schema: { $ref: "#/components/schemas/HealthEnvelopeV1" } },
    },
  });
  assert.deepEqual(openApi.paths["/api/v1/health"].get.responses["503"], {
    description: "required dependency unavailable",
    content: {
      "application/json": { schema: { $ref: "#/components/schemas/ErrorEnvelopeV1" } },
    },
  });
  assert.match(bindingSource, /dependencies:/);
  assert.match(bindingSource, /meta:/);
});
