from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
P7_ACCEPTANCE_MANIFEST = (
    ROOT / "docs/woozoo-trading-desk/phase-7/p7-acceptance-evidence-manifest.json"
)


def _p7_bound_hashes() -> dict[str, str]:
    acceptance = json.loads(P7_ACCEPTANCE_MANIFEST.read_text(encoding="utf-8"))
    paths = [entry["path"] for entry in acceptance["artifacts"]]
    assert acceptance["artifact_count"] == 65
    assert len(paths) == 65
    assert len(set(paths)) == 65
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}


def test_phase_quality_policy_rejects_malformed_phase_values_without_writes() -> None:
    before = _p7_bound_hashes()
    source = """
import { assertEvidenceTargetAllowed, validateActivePhase } from './scripts/phase-quality-policy.mjs';
for (const value of [null, '8', 7.5, -1, 10]) {
  let rejected = false;
  try { validateActivePhase(value); } catch { rejected = true; }
  if (!rejected) throw new Error(`accepted malformed phase: ${value}`);
}
for (const value of [0, 7, 8, 9]) validateActivePhase(value);
for (const target of [
  'test:unit', 'test:contracts', 'test:safety', 'test:integration',
  'test:property', 'test:replay', 'test:failure', 'test:e2e',
  'test:acceptance', 'ci'
]) assertEvidenceTargetAllowed(8, target);
for (const [phase, target] of [[8, 'test:unknown'], [9, 'test:safety']]) {
  let rejected = false;
  try { assertEvidenceTargetAllowed(phase, target); } catch { rejected = true; }
  if (!rejected) throw new Error(`accepted unopened target: ${phase}/${target}`);
}
"""
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", source],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert _p7_bound_hashes() == before


def test_phase8_unknown_evidence_target_fails_before_p7_artifact_writes() -> None:
    before = _p7_bound_hashes()
    result = subprocess.run(
        ["node", "scripts/quality.mjs", "test:unknown"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "fail-closed until its phase-specific manifest" in result.stderr
    assert _p7_bound_hashes() == before
