import { access, copyFile, readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const example = resolve(root, ".env.example");
const local = resolve(root, ".env.local");
const checkOnly = process.argv.includes("--check");

const validate = (text) => {
  const keys = text
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => line.split("=", 1)[0]);
  const expected = ["TRADING_MODE", "DATABASE_URL", "CONTROL_DATABASE_URL", "MARKET_DATABASE_URL", "EVIDENCE_DATABASE_URL", "AGENT_DATABASE_URL", "RISK_DATABASE_URL", "PAPER_DATABASE_URL", "PAPER_AUTHORIZATION_POLL_INTERVAL_MS", "PAPER_RECONCILIATION_INTERVAL_MS", "REDIS_URL", "MARKET_DATA_SOURCE", "LLM_PROVIDER", "LOCAL_OPERATOR_ORIGIN", "LOCAL_OPERATOR_VERIFIER_FILE"];
  if (keys.length !== expected.length || keys.some((key, index) => key !== expected[index])) {
    throw new Error("environment schema must contain only the approved Phase 7 keys");
  }
  if (!text.startsWith("TRADING_MODE=paper\n")) {
    throw new Error("environment schema must explicitly select paper mode");
  }
  if (!text.includes("\nMARKET_DATA_SOURCE=recorded\n")) {
    throw new Error("environment schema must default public collection to recorded input");
  }
  if (!text.endsWith("LOCAL_OPERATOR_VERIFIER_FILE=.secrets/operator.argon2id\n")) {
    throw new Error("operator bootstrap must use the local verifier file boundary");
  }
};

validate(await readFile(example, "utf8"));
if (checkOnly) {
  console.log("env schema is valid; no file was written");
} else {
  try {
    await access(local);
    console.log(".env.local already exists; it was not changed");
  } catch {
    await copyFile(example, local);
    console.log("created secretless .env.local from .env.example");
  }
}
