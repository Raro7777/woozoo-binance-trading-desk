import { spawn, spawnSync } from "node:child_process";
import { readFile, mkdtemp, rm, writeFile } from "node:fs/promises";
import { createServer as createHttpsServer } from "node:https";
import { request as httpRequest } from "node:http";
import { connect } from "node:net";
import { tmpdir } from "node:os";
import { basename, delimiter, join, resolve, sep } from "node:path";
import { pathToFileURL } from "node:url";
import { cleanupRecordedCertificate, recordTemporaryCertificate } from "./ephemeral-certificate.mjs";

const root = resolve(import.meta.dirname, "../..");
const app = resolve(root, "apps/trading-room-web");
await cleanupRecordedCertificate();
const temporaryRoot = await mkdtemp(join(tmpdir(), "woozoo-e2e-https-"));
await recordTemporaryCertificate(temporaryRoot);
const originalDirectory = process.cwd();
const composeProject = "woozoo-e2e";
const composeFile = resolve(root, "tests/e2e/compose.yaml");
const verifierFile = resolve(temporaryRoot, "operator.argon2id");
const secretFile = resolve(temporaryRoot, "operator.secret");
const pythonPath = [
  resolve(root, "packages/python/platform-core/src"),
  resolve(root, "services/control-api/src"),
  resolve(root, "services/paper-engine/src"),
  resolve(root, "services/risk-engine/src"),
  resolve(root, "services/agent-orchestrator/src"),
  resolve(root, "services/market-data-worker/src"),
  resolve(root, "services/evidence-worker/src"),
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
  PYTHONPATH: pythonPath,
};

function checked(command, args, options = {}) {
  const result = spawnSync(command, args, { cwd: root, stdio: "inherit", ...options });
  if (result.status !== 0) throw new Error(`${command} ${args.join(" ")} failed (${result.status})`);
}

function stopInfrastructure() {
  spawnSync("docker", ["compose", "-f", composeFile, "-p", composeProject, "down", "-v"], {
    cwd: root,
    stdio: "inherit",
  });
}

function publishedPort(service, containerPort) {
  const result = spawnSync(
    "docker",
    ["compose", "-f", composeFile, "-p", composeProject, "port", service, String(containerPort)],
    { cwd: root, encoding: "utf8" },
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
  checked("python", [
    "-m", "uv", "run", "--locked", "python", "scripts/bootstrap-local-operator.py",
    "--secret-file", secretFile,
    "--verifier-file", verifierFile,
  ]);
  stopInfrastructure();
  checked("docker", [
    "compose", "-f", composeFile, "-p", composeProject,
    "up", "-d", "--wait", "postgres", "redis",
  ]);
  const postgresPort = publishedPort("postgres", 5432);
  const redisPort = publishedPort("redis", 6379);
  Object.assign(serviceEnvironment, {
    DATABASE_URL: `postgresql://woozoo_control_reader@127.0.0.1:${postgresPort}/woozoo`,
    CONTROL_DATABASE_URL: `postgresql://woozoo_control_api@127.0.0.1:${postgresPort}/woozoo`,
    AGENT_DATABASE_URL: `postgresql://woozoo_agent_orchestrator@127.0.0.1:${postgresPort}/woozoo`,
    RISK_DATABASE_URL: `postgresql://woozoo_risk_engine@127.0.0.1:${postgresPort}/woozoo`,
    PAPER_DATABASE_URL: `postgresql://woozoo_paper_engine@127.0.0.1:${postgresPort}/woozoo`,
    MARKET_DATABASE_URL: `postgresql://woozoo_market_writer@127.0.0.1:${postgresPort}/woozoo`,
    EVIDENCE_DATABASE_URL: `postgresql://woozoo_evidence_writer@127.0.0.1:${postgresPort}/woozoo`,
    REDIS_URL: `redis://127.0.0.1:${redisPort}/0`,
  });
  const adminDatabaseUrl = `postgresql://postgres@127.0.0.1:${postgresPort}/woozoo`;
  checked("python", ["-m", "uv", "run", "--locked", "alembic", "upgrade", "head"], {
    env: { ...process.env, TRADING_MODE: "paper", DATABASE_URL: adminDatabaseUrl },
  });
  checked("python", [
    "-m", "uv", "run", "--locked", "python", "tests/e2e/live_control_api.py",
    "--bootstrap-public-data",
  ], { env: serviceEnvironment });
} catch (error) {
  stopInfrastructure();
  await removeTemporaryCertificate();
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
