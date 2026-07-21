import { cleanupRecordedCertificate } from "./ephemeral-certificate.mjs";
import { resolve } from "node:path";
import {
  E2E_COMPOSE_PROJECT,
  E2E_POSTGRES_VOLUME,
  removeInfrastructure,
} from "./infrastructure-lifecycle.mjs";

export default async function globalTeardown() {
  const root = resolve(import.meta.dirname, "../..");
  const composeFile = resolve(import.meta.dirname, "compose.yaml");
  try {
    removeInfrastructure({
      composeFile,
      composeProject: E2E_COMPOSE_PROJECT,
      root,
      volumeName: E2E_POSTGRES_VOLUME,
    });
  } finally {
    await cleanupRecordedCertificate();
  }
}
