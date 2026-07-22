import { spawnSync } from "node:child_process";


export const E2E_COMPOSE_PROJECT = "woozoo-e2e";
export const E2E_POSTGRES_VOLUME = "woozoo-e2e_postgres_data";


function runChecked(spawnSyncImpl, command, args, options, label) {
  const result = spawnSyncImpl(command, args, options);
  if (result.error !== undefined || result.status !== 0) {
    throw new Error(`${label} failed (${result.status ?? "spawn-error"})`, {
      cause: result.error,
    });
  }
  return result;
}


export function removeInfrastructure({
  composeFile,
  composeProject = E2E_COMPOSE_PROJECT,
  environment = process.env,
  root,
  spawnSyncImpl = spawnSync,
  volumeName = E2E_POSTGRES_VOLUME,
}) {
  const composePrefix = ["compose", "-f", composeFile, "-p", composeProject];
  runChecked(
    spawnSyncImpl,
    "docker",
    [...composePrefix, "down", "-v", "--remove-orphans"],
    { cwd: root, env: environment, stdio: "inherit" },
    "E2E infrastructure cleanup",
  );
  const volumeResult = runChecked(
    spawnSyncImpl,
    "docker",
    ["volume", "ls", "--quiet", "--filter", `name=^${volumeName}$`],
    { cwd: root, encoding: "utf8", env: environment },
    "E2E volume absence check",
  );
  const remainingVolumes = (volumeResult.stdout ?? "")
    .split(/\r?\n/u)
    .map((value) => value.trim())
    .filter(Boolean);
  if (remainingVolumes.length !== 0) {
    throw new Error(`E2E PostgreSQL volume still exists after cleanup: ${remainingVolumes.join(",")}`);
  }
  return {
    cleanup: {
      status: "PASS",
      remove_volumes: true,
      remove_orphans: true,
    },
    volume_absence: {
      status: "PASS",
      expected_volume: volumeName,
      observed_volume_names: remainingVolumes,
    },
  };
}


export function startFreshInfrastructure({
  afterStart,
  composeFile,
  composeProject = E2E_COMPOSE_PROJECT,
  environment = process.env,
  root,
  spawnSyncImpl = spawnSync,
  volumeName = E2E_POSTGRES_VOLUME,
}) {
  const removal = removeInfrastructure({
    composeFile,
    composeProject,
    environment,
    root,
    spawnSyncImpl,
    volumeName,
  });
  runChecked(
    spawnSyncImpl,
    "docker",
    [
      "compose",
      "-f",
      composeFile,
      "-p",
      composeProject,
      "up",
      "-d",
      "--wait",
      "postgres",
      "redis",
    ],
    { cwd: root, env: environment, stdio: "inherit" },
    "E2E infrastructure startup",
  );
  const downstream = afterStart();
  return {
    ...removal,
    startup: {
      status: "PASS",
      wait_for_health: true,
      services: ["postgres", "redis"],
    },
    ...downstream,
  };
}
