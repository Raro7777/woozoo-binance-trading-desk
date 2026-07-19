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
  const expected = ["TRADING_MODE", "DATABASE_URL", "REDIS_URL"];
  if (keys.length !== expected.length || keys.some((key, index) => key !== expected[index])) {
    throw new Error("environment schema must contain only the approved Phase 1 keys");
  }
  if (!text.startsWith("TRADING_MODE=paper\n")) {
    throw new Error("environment schema must explicitly select paper mode");
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
