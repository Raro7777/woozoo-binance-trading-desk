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
const environmentPath = resolve(root, ".env.phase8.local");
const temporaryRoot = await mkdtemp(join(tmpdir(), "woozoo-phase8-https-"));
const children = new Set();
let proxy;
const refreshChildren = new Set();
let stopping = false;

function readEnvironment(text) {
  const result = {};
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (line.length === 0 || line.startsWith("#")) continue;
    const separator = line.indexOf("=");
    if (separator <= 0) throw new Error(`.env.phase8.local 형식이 잘못되었습니다: ${rawLine}`);
    const key = line.slice(0, separator);
    if (key in result) throw new Error(`.env.phase8.local에 ${key}가 중복되었습니다.`);
    result[key] = line.slice(separator + 1);
  }
  return result;
}

function required(configuration, key) {
  const value = configuration[key];
  if (typeof value !== "string" || value.length === 0) throw new Error(`${key}가 필요합니다.`);
  return value;
}

function failClosed(configuration) {
  const expected = {
    TRADING_MODE: "paper",
    MARKET_DATA_SOURCE: "recorded",
    LLM_PROVIDER: "mock",
    LOCAL_OPERATOR_ORIGIN: "https://localhost:3443",
    SPOT_TESTNET_GATEWAY_ENABLED: "true",
    SPOT_TESTNET_ENVIRONMENT: "BINANCE_SPOT_TESTNET",
    SPOT_TESTNET_REST_ORIGIN: "https://testnet.binance.vision",
    SPOT_TESTNET_WS_URL: "wss://ws-api.testnet.binance.vision/ws-api/v3",
  };
  for (const [key, value] of Object.entries(expected)) {
    if (configuration[key] !== value) throw new Error(`${key}는 ${value}이어야 합니다.`);
  }
  for (const key of [
    "PHASE8_POSTGRES_PORT",
    "PHASE8_CONTROL_API_PORT",
    "PHASE8_MARKET_WRITER_DATABASE_PASSWORD_FILE",
    "PHASE8_EVIDENCE_WRITER_DATABASE_PASSWORD_FILE",
    "PHASE8_AGENT_ORCHESTRATOR_DATABASE_PASSWORD_FILE",
    "PHASE8_RISK_ENGINE_DATABASE_PASSWORD_FILE",
    "PHASE8_PAPER_ENGINE_DATABASE_PASSWORD_FILE",
  ]) required(configuration, key);
}

function checked(command, args, options = {}) {
  const result = spawnSync(command, args, { cwd: root, stdio: "inherit", ...options });
  if (result.status !== 0) throw new Error(`${command} 실행이 실패했습니다 (${result.status}).`);
  return result;
}

function terminate(child) {
  if (child === undefined || child.exitCode !== null || child.pid === undefined) return;
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/pid", String(child.pid), "/t", "/f"], {
      stdio: "ignore",
      windowsHide: true,
    });
  } else {
    child.kill("SIGTERM");
  }
}

function start(command, args, environment) {
  const child = spawn(command, args, { cwd: root, stdio: "inherit", env: environment });
  children.add(child);
  child.once("exit", () => children.delete(child));
  child.once("error", (error) => {
    console.error(`[Testnet] 하위 서비스 오류: ${error.message}`);
    void stop(1);
  });
  return child;
}

function waitForPort(port, child) {
  return new Promise((resolveReady, reject) => {
    const deadline = Date.now() + 60_000;
    const earlyExit = (code) => reject(new Error(`포트 ${port} 서비스가 시작 중 종료됐습니다 (${code}).`));
    if (child !== undefined) child.once("exit", earlyExit);
    const attempt = () => {
      const socket = connect({ host: "127.0.0.1", port });
      socket.once("connect", () => {
        socket.destroy();
        if (child !== undefined) child.off("exit", earlyExit);
        resolveReady();
      });
      socket.once("error", () => {
        socket.destroy();
        if (Date.now() >= deadline) reject(new Error(`포트 ${port} 대기 시간이 초과됐습니다.`));
        else setTimeout(attempt, 150);
      });
    };
    attempt();
  });
}

function pythonArguments(...args) {
  return ["-m", "uv", "run", "--locked", "python", ...args];
}

function openBrowser() {
  if (process.env.WOOZOO_OPEN_BROWSER !== "1") return;
  const target = "https://localhost:3443/testnet";
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
  browser.unref();
}

async function oneLineSecret(configuration, key) {
  const path = resolve(root, required(configuration, key));
  const value = (await readFile(path, "utf8")).trim();
  if (value.length === 0 || value.includes("\n") || value.includes("\r")) {
    throw new Error(`${key}가 가리키는 파일은 비어 있지 않은 한 줄이어야 합니다.`);
  }
  return value;
}

function databaseUrl(role, password, port) {
  return `postgresql://${role}:${encodeURIComponent(password)}@127.0.0.1:${port}/woozoo`;
}

async function removeTemporaryRoot() {
  const temporaryBase = resolve(tmpdir()) + sep;
  const target = resolve(temporaryRoot);
  if (!target.startsWith(temporaryBase) || !basename(target).startsWith("woozoo-phase8-https-")) {
    throw new Error(`예상하지 않은 임시 경로 삭제를 거부합니다: ${target}`);
  }
  await rm(target, { recursive: true, force: true });
}

async function stop(exitCode = 0) {
  if (stopping) return;
  stopping = true;
  for (const child of refreshChildren) terminate(child);
  for (const child of children) terminate(child);
  if (proxy !== undefined) await new Promise((resolveClose) => proxy.close(() => resolveClose()));
  await removeTemporaryRoot();
  console.log("\n[Testnet] 화면을 종료했습니다. Docker 데이터는 유지됩니다.");
  process.exitCode = exitCode;
}

const configuration = readEnvironment(await readFile(environmentPath, "utf8").catch(() => {
  throw new Error(".env.phase8.local이 없습니다. setup-phase8-testnet.ps1을 먼저 실행하세요.");
}));
failClosed(configuration);
await access(resolve(root, required(configuration, "LOCAL_OPERATOR_VERIFIER_FILE")));

const rolePasswordVariables = {
  woozoo_control_reader: "PHASE8_CONTROL_READER_DATABASE_PASSWORD_FILE",
  woozoo_control_api: "PHASE8_CONTROL_API_DATABASE_PASSWORD_FILE",
  woozoo_market_writer: "PHASE8_MARKET_WRITER_DATABASE_PASSWORD_FILE",
  woozoo_evidence_writer: "PHASE8_EVIDENCE_WRITER_DATABASE_PASSWORD_FILE",
  woozoo_agent_orchestrator: "PHASE8_AGENT_ORCHESTRATOR_DATABASE_PASSWORD_FILE",
  woozoo_risk_engine: "PHASE8_RISK_ENGINE_DATABASE_PASSWORD_FILE",
  woozoo_paper_engine: "PHASE8_PAPER_ENGINE_DATABASE_PASSWORD_FILE",
};
const passwords = Object.fromEntries(await Promise.all(
  Object.entries(rolePasswordVariables).map(async ([role, variable]) => [role, await oneLineSecret(configuration, variable)]),
));
const postgresPort = required(configuration, "PHASE8_POSTGRES_PORT");
const urls = Object.fromEntries(Object.entries(passwords).map(
  ([role, password]) => [role, databaseUrl(role, password, postgresPort)],
));
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
const fixtureEnvironment = {
  ...process.env,
  TRADING_MODE: "paper",
  MARKET_DATA_SOURCE: "recorded",
  LLM_PROVIDER: "mock",
  DATABASE_URL: urls.woozoo_control_reader,
  CONTROL_DATABASE_URL: urls.woozoo_control_api,
  MARKET_DATABASE_URL: urls.woozoo_market_writer,
  EVIDENCE_DATABASE_URL: urls.woozoo_evidence_writer,
  AGENT_DATABASE_URL: urls.woozoo_agent_orchestrator,
  RISK_DATABASE_URL: urls.woozoo_risk_engine,
  PAPER_DATABASE_URL: urls.woozoo_paper_engine,
  PYTHONPATH: pythonPath,
};

try {
  await waitForPort(Number(required(configuration, "PHASE8_CONTROL_API_PORT")));
  const stateProbe = spawnSync("python", pythonArguments("-c", [
    "import os,sys,psycopg",
    "with psycopg.connect(os.environ['MARKET_DATABASE_URL']) as connection:",
    "    exists=connection.execute('SELECT EXISTS (SELECT 1 FROM collector_sessions)').fetchone()[0]",
    "sys.exit(0 if exists else 3)",
  ].join("\n")), { cwd: root, stdio: ["ignore", "ignore", "inherit"], env: fixtureEnvironment });
  if (stateProbe.status === 3) {
    console.log("[Testnet] BTC·ETH 기록 재생 데이터와 초기 원장을 구성합니다.");
    checked("python", pythonArguments("tests/e2e/live_control_api.py", "--bootstrap-public-data"), {
      env: fixtureEnvironment,
    });
  } else if (stateProbe.status !== 0) {
    throw new Error("기록 재생 데이터 상태를 확인할 수 없습니다.");
  }
  for (const symbol of ["BTCUSDT", "ETHUSDT"]) {
    checked("python", pythonArguments("tests/e2e/live_control_api.py", "--refresh-evidence", symbol), {
      env: fixtureEnvironment,
    });
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
  if (certificate === undefined) throw new Error("로컬 HTTPS 인증서 생성에 실패했습니다.");

  const web = start(process.execPath, [nextCli, "start", webApp, "--hostname", "127.0.0.1", "--port", "3001"], process.env);
  await waitForPort(3001, web);
  const apiPort = Number(required(configuration, "PHASE8_CONTROL_API_PORT"));
  proxy = createHttpsServer({ key: await readFile(certificate.key), cert: await readFile(certificate.cert) }, (request, response) => {
    const apiRequest = request.url?.startsWith("/api/v1/") === true;
    const upstream = httpRequest({
      hostname: "127.0.0.1",
      port: apiRequest ? apiPort : 3001,
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
      response.end("로컬 Testnet 서비스를 사용할 수 없습니다.");
    });
    request.pipe(upstream);
  });
  await new Promise((resolveListen, reject) => {
    proxy.once("error", reject);
    proxy.listen(3443, "127.0.0.1", resolveListen);
  });

  async function refresh(symbol, required) {
    await new Promise((resolveRefresh, rejectRefresh) => {
      const child = spawn("python", pythonArguments("tests/e2e/live_control_api.py", "--refresh-evidence", symbol), {
        cwd: root,
        stdio: "inherit",
        env: fixtureEnvironment,
      });
      refreshChildren.add(child);
      let settled = false;
      function finish(code, error) {
        if (settled) return;
        settled = true;
        refreshChildren.delete(child);
        if (!stopping && (error !== undefined || code !== 0)) {
          const detail = error instanceof Error ? error.message : String(code);
          console.error(`[Testnet] ${symbol} 기록 재생 갱신 실패 (${detail})`);
          if (required) {
            rejectRefresh(new Error(`${symbol} 기록 재생을 준비하지 못했습니다.`));
            return;
          }
        }
        resolveRefresh();
      }
      child.once("error", (error) => finish(undefined, error));
      child.once("exit", (code) => {
        finish(code, undefined);
      });
    });
  }
  async function refreshBoth(required) {
    await refresh("BTCUSDT", required);
    if (stopping) return;
    await refresh("ETHUSDT", required);
  }
  await refreshBoth(true);
  void (async () => {
    while (!stopping) {
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 1_000));
      if (stopping) break;
      await refreshBoth(false);
    }
  })();

  console.log("\n[Testnet] 실행 준비가 끝났습니다.");
  console.log("[Testnet] 화면: https://localhost:3443/testnet");
  console.log("[Testnet] 외부 주문: Binance Spot Testnet만 사용");
  console.log("[Testnet] 분석 입력: BTC·ETH 기록 재생(Fixture), AI: Mock");
  console.log("[Testnet] 화면에서 별도 활성화와 주문별 승인을 해야만 주문이 전송됩니다.");
  console.log("[Testnet] 종료: 이 창에서 Ctrl+C\n");
  openBrowser();
} catch (error) {
  console.error(`[Testnet] 시작 실패: ${error instanceof Error ? error.message : String(error)}`);
  await stop(1);
}

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.once(signal, () => void stop(0));
}
for (const child of children) {
  child.once("exit", (code) => {
    if (!stopping) {
      console.error(`[Testnet] 화면 서비스가 예기치 않게 종료되었습니다 (${code}).`);
      void stop(code ?? 1);
    }
  });
}
