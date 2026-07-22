import { mkdir, readFile, rm, unlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, resolve, sep } from "node:path";

const root = resolve(import.meta.dirname, "../..");
const artifactDirectory = process.env.WOOZOO_E2E_ARTIFACT_DIR ?? "artifacts/e2e";
const marker = resolve(root, artifactDirectory, "runtime/https-temp-path.txt");

function validatedTemporaryPath(value) {
  const target = resolve(value.trim());
  if (!(target.startsWith(resolve(tmpdir()) + sep) && basename(target).startsWith("woozoo-e2e-https-"))) {
    throw new Error(`Refusing unexpected E2E certificate path: ${target}`);
  }
  return target;
}

export async function recordTemporaryCertificate(path) {
  await mkdir(resolve(marker, ".."), { recursive: true });
  await writeFile(marker, `${validatedTemporaryPath(path)}\n`, { encoding: "utf8", mode: 0o600 });
}

export async function cleanupRecordedCertificate() {
  let value;
  try {
    value = await readFile(marker, "utf8");
  } catch (error) {
    if (error?.code === "ENOENT") return;
    throw error;
  }
  await rm(validatedTemporaryPath(value), { recursive: true, force: true });
  await unlink(marker).catch((error) => {
    if (error?.code !== "ENOENT") throw error;
  });
}
