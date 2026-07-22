import { spawn, spawnSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { readFile, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { createServer as createHttpsServer } from "node:https";
import { request as httpRequest } from "node:http";
import { connect } from "node:net";
import { tmpdir } from "node:os";
import { basename, delimiter, dirname, join, resolve, sep } from "node:path";
import { pathToFileURL } from "node:url";
import { cleanupRecordedCertificate, recordTemporaryCertificate } from "./ephemeral-certificate.mjs";
import {
  E2E_COMPOSE_PROJECT,
  E2E_POSTGRES_VOLUME,
  removeInfrastructure,
  startFreshInfrastructure,
} from "./infrastructure-lifecycle.mjs";

const root = resolve(import.meta.dirname, "../..");
const app = resolve(root, "apps/trading-room-web");
await cleanupRecordedCertificate();
const temporaryRoot = await mkdtemp(join(tmpdir(), "woozoo-e2e-https-"));
await recordTemporaryCertificate(temporaryRoot);
const originalDirectory = process.cwd();
const composeProject = E2E_COMPOSE_PROJECT;
const composeFile = resolve(root, "tests/e2e/compose.yaml");
const artifactDirectory = process.env.WOOZOO_E2E_ARTIFACT_DIR ?? "artifacts/e2e";
const infrastructureEvidencePath = resolve(root, artifactDirectory, "E2E-INFRA-001.json");
const verifierFile = resolve(temporaryRoot, "operator.argon2id");
const secretFile = resolve(temporaryRoot, "operator.secret");
const runtimeHandlePath = resolve(root, ".tmp/phase8-e2e-runtime.json");
const postgresSuperuserPasswordFile = resolve(temporaryRoot, "postgres-superuser.secret");
const rolePasswordFiles = {
  woozoo_control_reader: resolve(temporaryRoot, "control-reader.secret"),
  woozoo_control_api: resolve(temporaryRoot, "control-api.secret"),
  woozoo_market_writer: resolve(temporaryRoot, "market-writer.secret"),
  woozoo_evidence_writer: resolve(temporaryRoot, "evidence-writer.secret"),
  woozoo_agent_orchestrator: resolve(temporaryRoot, "agent-orchestrator.secret"),
  woozoo_risk_engine: resolve(temporaryRoot, "risk-engine.secret"),
  woozoo_paper_engine: resolve(temporaryRoot, "paper-engine.secret"),
  woozoo_testnet_execution: resolve(temporaryRoot, "testnet-execution.secret"),
  woozoo_spot_testnet_gateway: resolve(temporaryRoot, "spot-testnet-gateway.secret"),
};
const postgresSuperuserPassword = randomBytes(32).toString("base64url");
const rolePasswords = Object.fromEntries(
  Object.keys(rolePasswordFiles).map((role) => [role, randomBytes(32).toString("base64url")]),
);
const infrastructureEnvironment = {
  ...process.env,
  E2E_POSTGRES_SUPERUSER_PASSWORD_FILE: postgresSuperuserPasswordFile,
};
const pythonPath = [
  resolve(root, "packages/python/platform-core/src"),
  resolve(root, "services/control-api/src"),
  resolve(root, "services/paper-engine/src"),
  resolve(root, "services/risk-engine/src"),
  resolve(root, "services/agent-orchestrator/src"),
  resolve(root, "services/market-data-worker/src"),
  resolve(root, "services/evidence-worker/src"),
  resolve(root, "services/testnet-execution-service/src"),
  resolve(root, "services/spot-testnet-gateway/src"),
  process.env.PYTHONPATH,
].filter(Boolean).join(delimiter);
const serviceEnvironment = {
  ...process.env,
  TRADING_MODE: "paper",
  MARKET_DATA_SOURCE: "recorded",
  LLM_PROVIDER: "mock",
  PAPER_AUTHORIZATION_POLL_INTERVAL_MS: "50",
  PAPER_RECONCILIATION_INTERVAL_MS: "250",
  LOCAL_OPERATOR_ORIGIN: "https://localhost:3443",
  LOCAL_OPERATOR_VERIFIER_FILE: verifierFile,
  SPOT_TESTNET_GATEWAY_INSTANCE_ID: "8".repeat(64),
  SPOT_TESTNET_GATEWAY_BUILD_DIGEST: "9".repeat(64),
  SPOT_TESTNET_GATEWAY_CONFIGURATION_DIGEST: "5291ea27d80daaec1dd0669feb4295e25853c5d60a8c6f7b6945829daad5fa75",
  SPOT_TESTNET_ALLOWLIST_DIGEST: "a56608938d3d7f7cb94472a1354e8b73c7a0d409dc62463dc13f4612dd06e33a",
  P8_GATEWAY_E2E_EVIDENCE_FILE: resolve(root, "artifacts/phase-8/e2e/P8-RUNTIME-001-gateway.json"),
  PYTHONPATH: pythonPath,
};

function checked(command, args, options = {}) {
  const result = spawnSync(command, args, { cwd: root, stdio: "inherit", ...options });
  if (result.status !== 0) throw new Error(`${command} ${args.join(" ")} failed (${result.status})`);
}

function stopInfrastructure() {
  return removeInfrastructure({
    composeFile,
    composeProject,
    environment: infrastructureEnvironment,
    root,
    volumeName: E2E_POSTGRES_VOLUME,
  });
}

function publishedPort(service, containerPort) {
  const result = spawnSync(
    "docker",
    ["compose", "-f", composeFile, "-p", composeProject, "port", service, String(containerPort)],
    { cwd: root, encoding: "utf8", env: infrastructureEnvironment },
  );
  if (result.status !== 0) throw new Error(`Cannot resolve ${service} E2E port`);
  const match = result.stdout.trim().match(/:(\d+)$/);
  if (match === null) throw new Error(`Invalid ${service} E2E port mapping`);
  return match[1];
}

function terminateChild(child, signal = "SIGTERM") {
  if (child.exitCode !== null || child.pid === undefined) return;
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/pid", String(child.pid), "/t", "/f"], { stdio: "ignore" });
  } else {
    child.kill(signal);
  }
}

process.chdir(temporaryRoot);
const mkcertModule = pathToFileURL(resolve(app, "node_modules/next/dist/lib/mkcert.js")).href;
const { createSelfSignedCertificate } = await import(mkcertModule);
const certificate = await createSelfSignedCertificate("localhost");
process.chdir(originalDirectory);
if (certificate === undefined) throw new Error("E2E HTTPS certificate generation failed closed");

try {
  // The plaintext fixture exists only in the validated disposable directory and
  // is removed with the certificate after the run. It is never an environment value.
  await writeFile(secretFile, "paper-only-password\n", { encoding: "utf8", mode: 0o600 });
  await writeFile(
    postgresSuperuserPasswordFile,
    `${postgresSuperuserPassword}\n`,
    { encoding: "utf8", mode: 0o600 },
  );
  for (const [role, path] of Object.entries(rolePasswordFiles)) {
    await writeFile(path, `${rolePasswords[role]}\n`, { encoding: "utf8", mode: 0o600 });
  }
  checked("python", [
    "-m", "uv", "run", "--locked", "python", "scripts/bootstrap-local-operator.py",
    "--secret-file", secretFile,
    "--verifier-file", verifierFile,
  ]);
  await rm(infrastructureEvidencePath, { force: true });
  await rm(serviceEnvironment.P8_GATEWAY_E2E_EVIDENCE_FILE, { force: true });
  const lifecycleEvidence = startFreshInfrastructure({
    afterStart: () => {
      const postgresPort = publishedPort("postgres", 5432);
      const redisPort = publishedPort("redis", 6379);
      const databaseUrl = (role) =>
        `postgresql://${role}:${encodeURIComponent(rolePasswords[role])}@127.0.0.1:${postgresPort}/woozoo`;
      Object.assign(serviceEnvironment, {
        DATABASE_URL: databaseUrl("woozoo_control_reader"),
        CONTROL_DATABASE_URL: databaseUrl("woozoo_control_api"),
        AGENT_DATABASE_URL: databaseUrl("woozoo_agent_orchestrator"),
        RISK_DATABASE_URL: databaseUrl("woozoo_risk_engine"),
        PAPER_DATABASE_URL: databaseUrl("woozoo_paper_engine"),
        MARKET_DATABASE_URL: databaseUrl("woozoo_market_writer"),
        EVIDENCE_DATABASE_URL: databaseUrl("woozoo_evidence_writer"),
        TESTNET_EXECUTION_DATABASE_URL: databaseUrl("woozoo_testnet_execution"),
        SPOT_TESTNET_GATEWAY_DATABASE_URL: databaseUrl("woozoo_spot_testnet_gateway"),
        REDIS_URL: `redis://127.0.0.1:${redisPort}/0`,
      });
      const adminDatabaseUrl = `postgresql://postgres:${encodeURIComponent(postgresSuperuserPassword)}@127.0.0.1:${postgresPort}/woozoo`;
      checked("python", ["-m", "uv", "run", "--locked", "alembic", "upgrade", "head"], {
        env: { ...process.env, TRADING_MODE: "paper", DATABASE_URL: adminDatabaseUrl },
      });
      checked("python", [
        "-m", "uv", "run", "--locked", "python", "tests/e2e/configure-postgres-roles.py",
      ], {
        env: {
          ...process.env,
          E2E_POSTGRES_ADMIN_URL: adminDatabaseUrl,
          E2E_CONTROL_READER_PASSWORD_FILE: rolePasswordFiles.woozoo_control_reader,
          E2E_CONTROL_API_PASSWORD_FILE: rolePasswordFiles.woozoo_control_api,
          E2E_MARKET_WRITER_PASSWORD_FILE: rolePasswordFiles.woozoo_market_writer,
          E2E_EVIDENCE_WRITER_PASSWORD_FILE: rolePasswordFiles.woozoo_evidence_writer,
          E2E_AGENT_ORCHESTRATOR_PASSWORD_FILE: rolePasswordFiles.woozoo_agent_orchestrator,
          E2E_RISK_ENGINE_PASSWORD_FILE: rolePasswordFiles.woozoo_risk_engine,
          E2E_PAPER_ENGINE_PASSWORD_FILE: rolePasswordFiles.woozoo_paper_engine,
          E2E_TESTNET_EXECUTION_PASSWORD_FILE: rolePasswordFiles.woozoo_testnet_execution,
          E2E_SPOT_TESTNET_GATEWAY_PASSWORD_FILE: rolePasswordFiles.woozoo_spot_testnet_gateway,
        },
      });
      mkdirSync(resolve(runtimeHandlePath, ".."), { recursive: true });
      writeFileSync(
        runtimeHandlePath,
        `${JSON.stringify({ database_urls: Object.fromEntries(
          Object.keys(rolePasswordFiles).map((role) => [role, databaseUrl(role)]),
        ) })}\n`,
        { encoding: "utf8", mode: 0o600 },
      );
      checked("python", [
        "-m", "uv", "run", "--locked", "python", "tests/e2e/live_control_api.py",
        "--bootstrap-public-data",
      ], { env: serviceEnvironment });
      return {
        migration: { status: "PASS", target: "head" },
        postgres_authentication: {
          status: "PASS",
          host_method: "scram-sha-256",
          distinct_application_roles: [
            "woozoo_control_api",
            "woozoo_testnet_execution",
            "woozoo_spot_testnet_gateway",
          ],
          committed_secret_values: false,
        },
        public_data_bootstrap: { status: "PASS", source: "recorded" },
      };
    },
    composeFile,
    composeProject,
    environment: infrastructureEnvironment,
    root,
    volumeName: E2E_POSTGRES_VOLUME,
  });
  const evidence = {
    schema_version: "woozoo.e2e-infrastructure-preflight/v1",
    id: "E2E-INFRA-001",
    status: "PASS",
    compose_project: composeProject,
    postgres_volume: E2E_POSTGRES_VOLUME,
    compose_sha256: createHash("sha256").update(await readFile(composeFile)).digest("hex"),
    postgres_health_transport: "tcp://127.0.0.1",
    ...lifecycleEvidence,
  };
  const outputDigest = createHash("sha256").update(JSON.stringify(evidence)).digest("hex");
  await mkdir(dirname(infrastructureEvidencePath), { recursive: true });
  await writeFile(
    infrastructureEvidencePath,
    `${JSON.stringify({ ...evidence, output_digest: outputDigest }, null, 2)}\n`,
    "utf8",
  );
} catch (error) {
  try {
    stopInfrastructure();
  } finally {
    await removeTemporaryCertificate();
  }
  throw error;
}

const nextCli = resolve(app, "node_modules/next/dist/bin/next");
const build = spawnSync(process.execPath, [nextCli, "build", app], {
  cwd: root,
  stdio: "inherit",
  env: { ...process.env, WOOZOO_E2E_UI_ERRORS: "enabled" },
});
if (build.status !== 0) {
  stopInfrastructure();
  await removeTemporaryCertificate();
  throw new Error("Trading Room production build failed before E2E startup");
}
const next = spawn(process.execPath, [nextCli, "start", app, "--hostname", "127.0.0.1", "--port", "3001"], {
  cwd: root,
  stdio: "inherit",
  env: { ...process.env, WOOZOO_E2E_UI_ERRORS: "enabled" },
});
const api = spawn("python", ["-m", "uv", "run", "--locked", "python", "tests/e2e/live_control_api.py"], {
  cwd: root,
  stdio: "inherit",
  env: {
    ...serviceEnvironment,
  },
});
const paperAuthorizationWorker = spawn(
  "python",
  ["-m", "uv", "run", "--locked", "python", "-m", "paper_engine.authorization_worker"],
  { cwd: root, stdio: "inherit", env: serviceEnvironment },
);

function waitForPort(port, child) {
  return new Promise((resolveReady, reject) => {
    const deadline = Date.now() + 60_000;
    const earlyExit = (code) => reject(new Error(`Loopback service for port ${port} exited during startup (${code})`));
    child.once("exit", earlyExit);
    const attempt = () => {
      const socket = connect({ host: "127.0.0.1", port });
      socket.once("connect", () => {
        socket.destroy();
        child.off("exit", earlyExit);
        resolveReady();
      });
      socket.once("error", () => {
        socket.destroy();
        if (Date.now() >= deadline) reject(new Error(`Timed out waiting for loopback port ${port}`));
        else setTimeout(attempt, 100);
      });
    };
    attempt();
  });
}

try {
  await Promise.all([waitForPort(3001, next), waitForPort(8001, api)]);
  if (paperAuthorizationWorker.exitCode !== null) {
    throw new Error(`Paper authorization worker exited during startup (${paperAuthorizationWorker.exitCode})`);
  }
} catch (error) {
  terminateChild(next);
  terminateChild(api);
  terminateChild(paperAuthorizationWorker);
  stopInfrastructure();
  await removeTemporaryCertificate();
  throw error;
}

const proxy = createHttpsServer({
  key: await readFile(certificate.key),
  cert: await readFile(certificate.cert),
}, (request, response) => {
  const apiRequest = request.url?.startsWith("/api/v1/") === true;
  const upstream = httpRequest({
    hostname: "127.0.0.1",
    port: apiRequest ? 8001 : 3001,
    path: request.url,
    method: request.method,
    headers: {
      ...request.headers,
      host: "localhost:3443",
      "x-forwarded-host": "localhost:3443",
      "x-forwarded-proto": "https",
      "x-forwarded-for": request.socket.remoteAddress ?? "127.0.0.1",
    },
  }, (upstreamResponse) => {
    response.writeHead(upstreamResponse.statusCode ?? 502, upstreamResponse.headers);
    upstreamResponse.pipe(response);
  });
  upstream.once("error", () => {
    if (!response.headersSent) response.writeHead(503, { "content-type": "text/plain" });
    response.end("서버 확정 로컬 서비스를 사용할 수 없습니다");
  });
  request.pipe(upstream);
});
proxy.listen(3443, "127.0.0.1");

let stopping = false;
async function removeTemporaryCertificate() {
  const temporaryBase = resolve(tmpdir()) + sep;
  const resolvedTarget = resolve(temporaryRoot);
  if (!resolvedTarget.startsWith(temporaryBase) || !basename(resolvedTarget).startsWith("woozoo-e2e-https-")) {
    throw new Error(`Refusing to remove unexpected E2E path: ${resolvedTarget}`);
  }
  await rm(resolvedTarget, { recursive: true, force: true });
  await rm(runtimeHandlePath, { force: true });
  await cleanupRecordedCertificate();
}
async function stop(signal) {
  if (stopping) return;
  stopping = true;
  proxy.close();
  terminateChild(next, signal);
  terminateChild(api, signal);
  terminateChild(paperAuthorizationWorker, signal);
  stopInfrastructure();
  await removeTemporaryCertificate();
}
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.once(signal, () => void stop(signal).finally(() => process.exit(0)));
}
for (const child of [next, api, paperAuthorizationWorker]) {
  child.once("error", (error) => void stop("SIGTERM").finally(() => { throw error; }));
  child.once("exit", (code) => {
    if (!stopping && code !== 0) void stop("SIGTERM").finally(() => process.exit(code ?? 1));
  });
}
