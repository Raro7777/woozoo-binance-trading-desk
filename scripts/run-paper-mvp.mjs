import { spawn, spawnSync } from "node:child_process";
import { access, mkdtemp, readFile, rm } from "node:fs/promises";
import { createServer as createHttpsServer } from "node:https";
import { request as httpRequest } from "node:http";
import { connect } from "node:net";
import { tmpdir } from "node:os";
import { basename, delimiter, join, resolve, sep } from "node:path";
import { pathToFileURL } from "node:url";

const root = resolve(import.meta.dirname, "..");
const webApp = resolve(root, "apps/trading-room-web");
const localEnvironmentPath = resolve(root, ".env.local");
const temporaryRoot = await mkdtemp(join(tmpdir(), "woozoo-paper-mvp-https-"));
const children = new Set();
let proxy;
let stopping = false;
let refreshChild;

function readEnvironment(text) {
  const result = {};
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (line.length === 0 || line.startsWith("#")) continue;
    const separator = line.indexOf("=");
    if (separator <= 0) throw new Error(`.env.local has an invalid line: ${rawLine}`);
    const key = line.slice(0, separator);
    if (key in result) throw new Error(`.env.local repeats ${key}`);
    result[key] = line.slice(separator + 1);
  }
  return result;
}

function failClosed(configuration) {
  const expected = {
    TRADING_MODE: "paper",
    MARKET_DATA_SOURCE: "recorded",
    LLM_PROVIDER: "mock",
    LOCAL_OPERATOR_ORIGIN: "https://localhost:3443",
  };
  for (const [key, value] of Object.entries(expected)) {
    if (configuration[key] !== value) throw new Error(`${key} must be ${value} for the local Paper MVP`);
  }
  for (const key of [
    "DATABASE_URL", "CONTROL_DATABASE_URL", "MARKET_DATABASE_URL", "EVIDENCE_DATABASE_URL",
    "AGENT_DATABASE_URL", "RISK_DATABASE_URL", "PAPER_DATABASE_URL", "REDIS_URL",
    "LOCAL_OPERATOR_VERIFIER_FILE",
  ]) {
    if (typeof configuration[key] !== "string" || configuration[key].length === 0) {
      throw new Error(`${key} is required by the local Paper MVP`);
    }
  }
}

function checked(command, args, options = {}) {
  const result = spawnSync(command, args, { cwd: root, stdio: "inherit", ...options });
  if (result.status !== 0) throw new Error(`${command} ${args.join(" ")} failed (${result.status})`);
  return result;
}

function terminate(child) {
  if (child === undefined || child.exitCode !== null || child.pid === undefined) return;
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/pid", String(child.pid), "/t", "/f"], { stdio: "ignore", windowsHide: true });
  } else {
    child.kill("SIGTERM");
  }
}

function start(command, args, environment) {
  const child = spawn(command, args, { cwd: root, stdio: "inherit", env: environment });
  children.add(child);
  child.once("exit", () => children.delete(child));
  child.once("error", (error) => {
    console.error(`[Paper MVP] 하위 서비스 오류: ${error.message}`);
    void stop(1);
  });
  return child;
}

function openLocalBrowser() {
  if (process.env.WOOZOO_OPEN_BROWSER !== "1") return;
  const target = "https://localhost:3443";
  const launch = process.platform === "win32"
    ? [process.env.ComSpec ?? "cmd.exe", ["/d", "/s", "/c", "start", "", target]]
    : process.platform === "darwin"
      ? ["open", [target]]
      : ["xdg-open", [target]];
  const browser = spawn(launch[0], launch[1], {
    cwd: root,
    detached: true,
    stdio: "ignore",
    windowsHide: false,
  });
  browser.once("error", (error) => {
    console.error(`[Paper MVP] 브라우저 자동 열기 실패: ${error.message}`);
  });
  browser.unref();
}

function waitForPort(port, child) {
  return new Promise((resolveReady, reject) => {
    const deadline = Date.now() + 60_000;
    const earlyExit = (code) => reject(new Error(`port ${port} service exited during startup (${code})`));
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
        if (Date.now() >= deadline) reject(new Error(`timed out waiting for loopback port ${port}`));
        else setTimeout(attempt, 150);
      });
    };
    attempt();
  });
}

function pythonArguments(...args) {
  return ["-m", "uv", "run", "--locked", "python", ...args];
}

function adminDatabaseUrl(databaseUrl) {
  const parsed = new URL(databaseUrl);
  parsed.username = "postgres";
  parsed.password = "";
  return parsed.toString();
}

async function removeTemporaryRoot() {
  const temporaryBase = resolve(tmpdir()) + sep;
  const target = resolve(temporaryRoot);
  if (!target.startsWith(temporaryBase) || !basename(target).startsWith("woozoo-paper-mvp-https-")) {
    throw new Error(`refusing to remove unexpected temporary path: ${target}`);
  }
  await rm(target, { recursive: true, force: true });
}

async function stop(exitCode = 0) {
  if (stopping) return;
  stopping = true;
  terminate(refreshChild);
  for (const child of children) terminate(child);
  if (proxy !== undefined) await new Promise((resolveClose) => proxy.close(() => resolveClose()));
  await removeTemporaryRoot();
  console.log("\n[Paper MVP] 앱을 종료했습니다. Postgres와 Redis 데이터는 유지됩니다.");
  process.exitCode = exitCode;
}

const configuration = readEnvironment(await readFile(localEnvironmentPath, "utf8").catch(() => {
  throw new Error(".env.local이 없습니다. 먼저 `corepack pnpm env:init`을 실행하세요.");
}));
failClosed(configuration);
const verifierPath = resolve(root, configuration.LOCAL_OPERATOR_VERIFIER_FILE);
await access(verifierPath).catch(() => {
  throw new Error("로컬 운영자 verifier가 없습니다. docs/QUICK_START_KO.md의 최초 로그인 준비를 먼저 실행하세요.");
});

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
const serviceEnvironment = { ...process.env, ...configuration, PYTHONPATH: pythonPath };

try {
  checked("docker", ["compose", "up", "-d", "--wait", "postgres", "redis"]);
  checked("python", ["-m", "uv", "run", "--locked", "alembic", "upgrade", "head"], {
    env: { ...process.env, TRADING_MODE: "paper", DATABASE_URL: adminDatabaseUrl(configuration.DATABASE_URL) },
  });

  const stateProbe = spawnSync("python", pythonArguments("-c", [
    "import os,sys,psycopg",
    "with psycopg.connect(os.environ['MARKET_DATABASE_URL']) as connection:",
    "    exists=connection.execute('SELECT EXISTS (SELECT 1 FROM collector_sessions)').fetchone()[0]",
    "sys.exit(0 if exists else 3)",
  ].join("\n")), { cwd: root, stdio: ["ignore", "ignore", "inherit"], env: serviceEnvironment });
  if (stateProbe.status === 3) {
    console.log("[Paper MVP] BTC·ETH 기록 재생 데이터를 처음 구성합니다.");
    checked("python", pythonArguments("tests/e2e/live_control_api.py", "--bootstrap-public-data"), { env: serviceEnvironment });
  } else if (stateProbe.status !== 0) {
    throw new Error("could not inspect the local recorded market state");
  }
  for (const symbol of ["BTCUSDT", "ETHUSDT"]) {
    checked("python", pythonArguments("tests/e2e/live_control_api.py", "--refresh-evidence", symbol), { env: serviceEnvironment });
  }

  const nextCli = resolve(webApp, "node_modules/next/dist/bin/next");
  checked(process.execPath, [nextCli, "build", webApp], { env: process.env });

  const originalDirectory = process.cwd();
  process.chdir(temporaryRoot);
  let certificate;
  try {
    const mkcertModule = pathToFileURL(resolve(webApp, "node_modules/next/dist/lib/mkcert.js")).href;
    const { createSelfSignedCertificate } = await import(mkcertModule);
    certificate = await createSelfSignedCertificate("localhost");
  } finally {
    process.chdir(originalDirectory);
  }
  if (certificate === undefined) throw new Error("local HTTPS certificate generation failed closed");

  const web = start(process.execPath, [nextCli, "start", webApp, "--hostname", "127.0.0.1", "--port", "3001"], process.env);
  const api = start("python", pythonArguments("tests/e2e/live_control_api.py"), serviceEnvironment);
  const paperWorker = start("python", pythonArguments("-m", "paper_engine.authorization_worker"), serviceEnvironment);
  await Promise.all([waitForPort(3001, web), waitForPort(8001, api)]);
  if (paperWorker.exitCode !== null) throw new Error(`Paper worker exited during startup (${paperWorker.exitCode})`);

  proxy = createHttpsServer({ key: await readFile(certificate.key), cert: await readFile(certificate.cert) }, (request, response) => {
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
      if (!response.headersSent) response.writeHead(503, { "content-type": "text/plain; charset=utf-8" });
      response.end("로컬 Paper 서비스를 사용할 수 없습니다");
    });
    request.pipe(upstream);
  });
  await new Promise((resolveListen, reject) => {
    proxy.once("error", reject);
    proxy.listen(3443, "127.0.0.1", resolveListen);
  });

  async function refresh(symbol) {
    await new Promise((resolveRefresh) => {
      refreshChild = spawn("python", pythonArguments("tests/e2e/live_control_api.py", "--refresh-evidence", symbol), {
        cwd: root,
        stdio: "inherit",
        env: serviceEnvironment,
      });
      refreshChild.once("exit", (code) => {
        if (!stopping && code !== 0) console.error(`[Paper MVP] ${symbol} 기록 재생 갱신 실패 (${code})`);
        refreshChild = undefined;
        resolveRefresh();
      });
    });
  }
  void (async () => {
    while (!stopping) {
      await refresh("BTCUSDT");
      if (stopping) break;
      await refresh("ETHUSDT");
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 1_000));
    }
  })();

  console.log("\n[Paper MVP] 실행 준비가 끝났습니다.");
  console.log("[Paper MVP] 화면: https://localhost:3443");
  console.log("[Paper MVP] 데이터: BTC·ETH 기록 재생(Fixture), AI: Mock, 거래 모드: PAPER");
  console.log("[Paper MVP] 종료: 이 창에서 Ctrl+C (DB/Redis 데이터는 유지)\n");
  openLocalBrowser();
} catch (error) {
  console.error(`[Paper MVP] 시작 실패: ${error instanceof Error ? error.message : String(error)}`);
  await stop(1);
}

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.once(signal, () => void stop(0));
}
for (const child of children) {
  child.once("exit", (code) => {
    if (!stopping) {
      console.error(`[Paper MVP] 서비스가 예기치 않게 종료되었습니다 (${code})`);
      void stop(code ?? 1);
    }
  });
}
