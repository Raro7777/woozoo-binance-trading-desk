import assert from "node:assert/strict";
import test from "node:test";

import {
  E2E_POSTGRES_VOLUME,
  startFreshInfrastructure,
} from "../e2e/infrastructure-lifecycle.mjs";


type SpawnResult = {
  error?: Error;
  status: number | null;
  stdout?: string;
};


function commandText(command: string, args: readonly string[]): string {
  return [command, ...args].join(" ");
}


test("cleanup failure aborts before volume inspection, startup, and migration", () => {
  const commands: string[] = [];
  let afterStartCalls = 0;
  const spawnSyncImpl = (command: string, args: readonly string[]): SpawnResult => {
    commands.push(commandText(command, args));
    return { status: 17 };
  };

  assert.throws(
    () => startFreshInfrastructure({
      afterStart: () => {
        afterStartCalls += 1;
        return { migration: { status: "PASS" } };
      },
      composeFile: "tests/e2e/compose.yaml",
      root: ".",
      spawnSyncImpl,
    }),
    /E2E infrastructure cleanup failed \(17\)/u,
  );
  assert.equal(commands.length, 1);
  assert.match(commands[0], / down -v --remove-orphans$/u);
  assert.equal(commands.some((command) => command.includes(" up ")), false);
  assert.equal(afterStartCalls, 0);
});


test("a surviving named volume aborts before startup and migration", () => {
  const commands: string[] = [];
  let afterStartCalls = 0;
  const results: SpawnResult[] = [
    { status: 0 },
    { status: 0, stdout: `${E2E_POSTGRES_VOLUME}\n` },
  ];
  const spawnSyncImpl = (command: string, args: readonly string[]): SpawnResult => {
    commands.push(commandText(command, args));
    return results.shift() ?? { status: 99 };
  };

  assert.throws(
    () => startFreshInfrastructure({
      afterStart: () => {
        afterStartCalls += 1;
        return { migration: { status: "PASS" } };
      },
      composeFile: "tests/e2e/compose.yaml",
      root: ".",
      spawnSyncImpl,
    }),
    /volume still exists after cleanup/u,
  );
  assert.equal(commands.length, 2);
  assert.match(commands[1], /docker volume ls --quiet --filter name=\^woozoo-e2e_postgres_data\$$/u);
  assert.equal(commands.some((command) => command.includes(" up ")), false);
  assert.equal(afterStartCalls, 0);
});


test("successful fresh startup orders cleanup, absence, startup, then migration", () => {
  const steps: string[] = [];
  const results: SpawnResult[] = [
    { status: 0 },
    { status: 0, stdout: "" },
    { status: 0 },
  ];
  const spawnSyncImpl = (command: string, args: readonly string[]): SpawnResult => {
    steps.push(commandText(command, args));
    return results.shift() ?? { status: 99 };
  };

  const evidence = startFreshInfrastructure({
    afterStart: () => {
      steps.push("migration");
      return { migration: { status: "PASS" } };
    },
    composeFile: "tests/e2e/compose.yaml",
    root: ".",
    spawnSyncImpl,
  });

  assert.deepEqual(steps.map((step) => step.includes(" down ")
    ? "down"
    : step.includes("volume ls")
      ? "volume-ls"
      : step.includes(" up ")
        ? "up"
        : step), ["down", "volume-ls", "up", "migration"]);
  assert.deepEqual(evidence.volume_absence.observed_volume_names, []);
  assert.equal(evidence.startup.status, "PASS");
  assert.equal(evidence.migration.status, "PASS");
});
