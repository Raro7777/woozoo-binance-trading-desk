from __future__ import annotations

import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
PHASE_STATE_PATH = ROOT / "docs/woozoo-trading-desk/phase-state.json"


def _split_reference(reference: str) -> tuple[str, str]:
    path, separator, fragment = reference.rpartition("#")
    assert separator == "#", f"fragment가 없는 계약 참조: {reference}"
    assert path, f"경로가 없는 계약 참조: {reference}"
    assert fragment, f"빈 계약 fragment: {reference}"
    return path, fragment


def _markdown_heading_slugs(markdown: str) -> set[str]:
    slugs: set[str] = set()
    for line in markdown.splitlines():
        match = re.fullmatch(r" {0,3}#{1,6}\s+(.+?)\s*#*\s*", line)
        if match is None:
            continue
        heading = match.group(1).strip().lower()
        slug = re.sub(r"[^\w\- ]", "", heading, flags=re.UNICODE).replace(" ", "-")
        slugs.add(slug)
    return slugs


def test_phase_acceptance_contract_runtime_pointers_resolve_to_real_headings() -> None:
    phase_state = json.loads(PHASE_STATE_PATH.read_text(encoding="utf-8"))
    _, canonical_fragment = _split_reference(phase_state["acceptance_contract_ref"])
    runtime_paths = phase_state["acceptance_contract_runtime_paths"]

    assert runtime_paths, "acceptance 계약 런타임 경로가 비어 있습니다."
    for runtime, reference in runtime_paths.items():
        relative_path, fragment = _split_reference(reference)
        assert fragment == canonical_fragment, (
            f"{runtime} 계약 fragment가 정본과 다릅니다: {fragment} != {canonical_fragment}"
        )

        contract_path = (ROOT / relative_path).resolve()
        assert contract_path.is_relative_to(ROOT), f"저장소 밖 계약 경로: {reference}"
        assert contract_path.is_file(), f"존재하지 않는 {runtime} 계약 파일: {relative_path}"

        headings = _markdown_heading_slugs(contract_path.read_text(encoding="utf-8"))
        assert fragment in headings, (
            f"{runtime} 계약 파일에 #{fragment} Markdown heading이 없습니다: {relative_path}"
        )
