import { resolve } from "node:path";

const PHASE_8_OPEN_EVIDENCE_TARGETS = new Set([
  "test:unit",
  "test:contracts",
  "test:safety",
  "test:integration",
  "test:property",
  "test:replay",
  "test:failure",
  "test:e2e",
  "test:acceptance",
  "ci",
]);

export function validateActivePhase(value) {
  if (!Number.isInteger(value) || value < 0 || value > 9) {
    throw new Error("phase-state current_phase must be an integer in the approved 0..9 roadmap");
  }
  return value;
}

export function phaseArtifactsRoot(root, activePhase) {
  validateActivePhase(activePhase);
  return activePhase >= 8
    ? resolve(root, "artifacts", `phase-${activePhase}`)
    : resolve(root, "artifacts");
}

export function assertEvidenceTargetAllowed(activePhase, target) {
  validateActivePhase(activePhase);
  const openEvidenceTargets = activePhase === 8
    ? PHASE_8_OPEN_EVIDENCE_TARGETS
    : new Set();
  if (
    activePhase >= 8
    && (target === "ci" || target?.startsWith("test:"))
    && !openEvidenceTargets.has(target)
  ) {
    throw new Error(
      `Phase ${activePhase} evidence target ${target} is fail-closed until its phase-specific manifest and artifact namespace are defined`,
    );
  }
}
