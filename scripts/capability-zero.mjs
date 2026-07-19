import { readFile, readdir, rm, writeFile } from "node:fs/promises";
import { join, relative, resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
export const productRoots = [
  ".github",
  "apps",
  "scripts",
  "services",
  "packages/contracts",
  "packages/python",
  "packages/typescript",
  "db/migrations",
  "infra",
  "alembic.ini",
  "compose.yaml",
  ".env.example",
  "pnpm-workspace.yaml",
  "pyproject.toml",
  "package.json",
];
const dependencyMetadata = ["pnpm-lock.yaml", "uv.lock"];
const insensitive = (...parts) => new RegExp(parts.join(""), "i");
const forbidden = [
  insensitive("test", "net"),
  insensitive("main", "net"),
  insensitive("exchange[_-]?", "client"),
  insensitive("broker[_-]?", "adapter"),
  insensitive("(?:create|cancel|submit)[_-]?", "order"),
  insensitive("user[_-]?", "data"),
  insensitive("with", "drawal"),
  insensitive("fut", "ures"),
  insensitive("mar", "gin"),
  insensitive("lev", "erage"),
  insensitive("api[_-]?", "key"),
  insensitive("sign", "ature"),
  insensitive("listen[_-]?", "key"),
  insensitive("live[_-]?", "mode"),
  insensitive("priv", "ate[_-]?acc", "ount"),
  insensitive("acc", "ount"),
];
const phaseFourPatternA = insensitive("acc", "ount");
const phaseFourPatternB = insensitive("test", "net");
const phaseFourPatternC = insensitive("api[_-]?", "key");

function isApprovedPhaseFourVocabulary(projectPath, pattern) {
  const paperOwned =
    projectPath.startsWith("services/paper-engine/") ||
    projectPath === "db/migrations/versions/20260719_0004_paper_broker_ledger.py" ||
    projectPath.startsWith("packages/contracts/spec/paper-");
  if (paperOwned && pattern.source === phaseFourPatternA.source) return true;
  if (
    projectPath === "services/paper-engine/src/paper_engine/settings.py" &&
    [phaseFourPatternB.source, phaseFourPatternC.source].includes(pattern.source)
  ) return true;
  return false;
}
const publicOrigins = new Set([
  "https://data-" + "api.bin" + "ance.vision",
  "wss://data-" + "stream.bin" + "ance.vision",
]);
const approvedRestPaths = new Set([
  "/api/v3/" + "ping",
  "/api/v3/" + "time",
  "/api/v3/" + "exchangeInfo",
  "/api/v3/" + "trades",
  "/api/v3/" + "klines",
  "/api/v3/" + "ticker/" + "bookTicker",
]);
const urlPattern = /(?:https|wss):\/\/[a-z0-9.-]+(?::\d+)?/gi;
const restPathPattern = /\/api\/v3\/[A-Za-z][A-Za-z0-9/]*/g;
const forbiddenPublicPath = insensitive("/api/v3/(?:or", "der|acc", "ount|user", "DataStream)");
const dependencyPattern = insensitive(
  "bin",
  "ance|ccxt|exchange[_-]?client|broker[_-]?adapter|priv",
  "ate[_-]?acc",
  "ount|acc",
  "ount",
);

async function filesUnder(path) {
  const entries = await readdir(path, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const entryPath = join(path, entry.name);
    if (entry.isDirectory()) {
      if (!["node_modules", ".next", "dist", "__pycache__"].includes(entry.name)) {
        files.push(...(await filesUnder(entryPath)));
      }
    } else if (entry.isFile() && !entry.name.endsWith(".pyc") && !entry.name.endsWith(".tsbuildinfo")) {
      files.push(entryPath);
    }
  }
  return files;
}

function metadataInspection(content) {
  try {
    const parsed = JSON.parse(content);
    if (parsed !== null && typeof parsed === "object") {
      return JSON.stringify({
        scripts: parsed.scripts ?? {},
        dependencies: Object.keys(parsed.dependencies ?? {}).sort(),
        devDependencies: Object.keys(parsed.devDependencies ?? {}).sort(),
        optionalDependencies: Object.keys(parsed.optionalDependencies ?? {}).sort(),
        pnpm: parsed.pnpm ?? {},
      });
    }
  } catch {
    // Lockfiles are intentionally inspected as raw text below.
  }
  return content;
}

function productConfigurationInspection(projectPath, content) {
  if (projectPath !== "package.json") return content;
  const parsed = JSON.parse(content);
  const { name: _projectName, ...configuration } = parsed;
  return JSON.stringify(configuration);
}

export function scanRootPackageConfiguration(configuration) {
  const findings = [];
  const content = productConfigurationInspection("package.json", JSON.stringify(configuration));
  for (const pattern of forbidden) {
    if (pattern.test(content)) findings.push(`package.json:${pattern}`);
  }
  if (findings.length > 0) {
    throw new Error(`Phase 1 capability-zero violation: ${findings.join(", ")}`);
  }
}

export async function scanDependencyMetadata(paths = dependencyMetadata.map((path) => resolve(root, path))) {
  for (const metadataPath of paths) {
    const content = await readFile(metadataPath, "utf8");
    if (dependencyPattern.test(metadataInspection(content))) {
      throw new Error(`Phase 1 dependency capability violation: ${relative(root, metadataPath)}`);
    }
  }
}

export async function scanPaths(paths = productRoots.map((path) => resolve(root, path))) {
  const files = [];
  for (const path of paths) {
    const entries = await filesUnder(path).catch((error) => {
      if (error.code === "ENOTDIR") return [path];
      throw error;
    });
    files.push(...entries);
  }

  const findings = [];
  for (const path of files) {
    const projectPath = relative(root, path).replaceAll("\\", "/");
    const content = await readFile(path, "utf8");
    const inspected = productConfigurationInspection(projectPath, content);
    for (const pattern of forbidden) {
      if (pattern.test(inspected) && !isApprovedPhaseFourVocabulary(projectPath, pattern)) {
        findings.push(`${projectPath}:${pattern}`);
      }
    }
    for (const candidate of inspected.match(urlPattern) ?? []) {
      if (candidate.toLowerCase().includes("bin" + "ance") && !publicOrigins.has(candidate)) {
        findings.push(`${projectPath}:unapproved-public-origin`);
      }
    }
    for (const candidate of inspected.match(restPathPattern) ?? []) {
      if (!approvedRestPaths.has(candidate)) findings.push(`${projectPath}:unapproved-public-path`);
    }
    if (forbiddenPublicPath.test(inspected)) findings.push(`${projectPath}:forbidden-public-path`);
  }
  if (findings.length > 0) {
    throw new Error(`Phase 2 public-only capability violation: ${findings.join(", ")}`);
  }

  if (paths.length === productRoots.length) await scanDependencyMetadata();
}

if (import.meta.main) {
  await scanPaths();
}

export async function verifyCanaryFailure() {
  const directoryCanaries = [
    ".github/.capability-zero-canary.yml",
    "apps/trading-room-web/.capability-zero-canary.ts",
    "scripts/.capability-zero-canary.mjs",
    "services/control-api/.capability-zero-canary.py",
    "packages/contracts/.capability-zero-canary.json",
    "packages/python/platform-core/.capability-zero-canary.py",
    "packages/typescript/.capability-zero-canary.ts",
    "db/migrations/.capability-zero-canary.py",
    "infra/observability/.capability-zero-canary.json",
  ].map((path) => resolve(root, path));
  const metadataCanary = resolve(root, ".capability-zero-package.json");
  const privateCanary = resolve(root, "scripts/.private-" + "acc" + "ount-capability-canary.mjs");
  const hostCanary = resolve(root, "scripts/.public-host-capability-canary.mjs");
  const pathCanary = resolve(root, "scripts/.public-path-capability-canary.mjs");
  try {
    for (const candidate of directoryCanaries) {
      await writeFile(candidate, "exchange" + "_client = object()\n", "utf8");
      try {
        await scanPaths();
      } catch (error) {
        if (String(error.message).includes("capability violation")) continue;
        throw error;
      } finally {
        await rm(candidate, { force: true });
      }
      throw new Error(`capability scanner accepted its canary: ${candidate}`);
    }
    await writeFile(privateCanary, "private" + "_acc" + "ount_client = object()\n", "utf8");
    let privateCapabilityDetected = false;
    try {
      await scanPaths();
    } catch (error) {
      if (!String(error.message).includes("capability violation")) throw error;
      privateCapabilityDetected = true;
    }
    if (!privateCapabilityDetected) {
      throw new Error("capability scanner accepted its private-capability canary");
    }
    await writeFile(hostCanary, "export const origin = 'https://api.bin" + "ance.com';\n", "utf8");
    let hostDetected = false;
    try {
      await scanPaths();
    } catch (error) {
      if (!String(error.message).includes("public-only capability violation")) throw error;
      hostDetected = true;
    }
    if (!hostDetected) throw new Error("capability scanner accepted an unapproved public host");
    await writeFile(
      pathCanary,
      "export const endpoint = 'https://data-api.bin" + "ance.vision/api/" + "v3/de" + "pth';\n",
      "utf8",
    );
    let pathDetected = false;
    try {
      await scanPaths();
    } catch (error) {
      if (!String(error.message).includes("public-only capability violation")) throw error;
      pathDetected = true;
    }
    if (!pathDetected) throw new Error("capability scanner accepted an unapproved public path");
    await writeFile(
      metadataCanary,
      JSON.stringify({ scripts: { unsafe: "curl https://api.bin" + "ance.com/api/" + "v3/or" + "der" } }),
      "utf8",
    );
    try {
      await scanDependencyMetadata([metadataCanary]);
    } catch (error) {
      if (String(error.message).includes("dependency capability violation")) return;
      throw error;
    }
    throw new Error("capability scanner accepted its package metadata canary");
  } finally {
    await Promise.all([
      ...directoryCanaries,
      metadataCanary,
      privateCanary,
      hostCanary,
      pathCanary,
    ].map((candidate) => rm(candidate, { force: true })));
  }
}
