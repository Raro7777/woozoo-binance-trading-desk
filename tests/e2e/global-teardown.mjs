import { cleanupRecordedCertificate } from "./ephemeral-certificate.mjs";
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";

export default async function globalTeardown() {
  const root = resolve(import.meta.dirname, "../..");
  const composeFile = resolve(import.meta.dirname, "compose.yaml");
  spawnSync(
    "docker",
    ["compose", "-f", composeFile, "-p", "woozoo-e2e", "down", "-v"],
    { cwd: root, stdio: "inherit" },
  );
  await cleanupRecordedCertificate();
}
