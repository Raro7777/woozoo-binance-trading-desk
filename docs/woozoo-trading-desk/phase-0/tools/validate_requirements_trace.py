#!/usr/bin/env python3
"""Recompute and validate the Phase 0 atomic requirements evidence manifest.

This is documentation-evidence tooling. It has no product, broker, credential,
network, or order capability and uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
REQ_RE = re.compile(r"^REQ-[A-Z0-9]+-[0-9]{3}$")
TARGET_RES = {
    "mvp": re.compile(r"^MVP-[0-9]{2}$"),
    "p0": re.compile(r"^P0-[0-9]{2}$"),
    "phase": re.compile(r"^PHASE-[0-9]$"),
    "scenario": re.compile(r"^(?:CORE|PLAT|CONTRACT|SAFE|DATA|PTI|FIN|ORD|ATOM|RISK|KILL|AUTH|AI|SEC|E2E)-[0-9]{3}$"),
}
TARGET_FIELDS = {
    "mvp": "target_mvp_ids",
    "p0": "target_p0_ids",
    "phase": "target_phase_ids",
    "scenario": "target_scenario_ids",
}

# Reviewed inventory authority intentionally lives outside the candidate manifest.
# Updating either fingerprint requires a deliberate validator change after re-auditing
# the immutable source projections; candidate-controlled summaries cannot weaken it.
AUTHORITATIVE_REQUIREMENT_COUNT = 123
AUTHORITATIVE_REQUIREMENT_FINGERPRINT = "7667f7a6439f1a246ad1363a501711403e42ef43c666fed7c8893e4af5575cbd"
AUTHORITATIVE_ALIAS_COUNT = 8
AUTHORITATIVE_ALIAS_FINGERPRINT = "03ec0558ad664b6b4e5b6f8d55c62e3886be0f86ffa836263829ead7485954b1"
AUTHORITATIVE_TARGET_COUNT = 77
AUTHORITATIVE_TARGET_FINGERPRINT = "d7860d5479262f22d8fc47feb6f510c862986527e9577ed9b972cec654493f51"

# User-approved policy values are source requirements, so their original wording
# remains immutable. This reviewed mapping records the only approved resolution
# path and prevents a candidate manifest from silently dropping or widening it.
POLICY_APPROVAL_RECORD = "docs/woozoo-trading-desk/phase-0/phase-0-approval-record.md"
POLICY_DECISION_PACKAGE = "docs/woozoo-trading-desk/phase-0-decision-package.md"
# These digests are independently reviewed authority, not candidate-controlled
# bookkeeping. Updating either requires a deliberate policy-document review and
# validator change; changing the manifest alongside a policy document is denied.
AUTHORITATIVE_POLICY_APPROVAL_RECORD_SHA256 = "2c8a1d68d73c8a217db4d0e93aa74c5c7a05e092bc6f290c9f78fb107cb68080"
AUTHORITATIVE_POLICY_DECISION_PACKAGE_SHA256 = "0f0dd9e04b856c39b9ac6e0d778d4529e179eb15c2b6c2ed3240e4610849f4f0"
REVIEWED_POLICY_RESOLUTION_MAP = {
    "REQ-AI-007": "DP-D03",
    "REQ-RISK-001": "DP-D06",
    "REQ-RISK-002": "DP-D06",
    "REQ-RISK-003": "DP-D06",
    "REQ-RISK-004": "DP-D06",
    "REQ-RISK-005": "DP-D06",
    "REQ-RISK-006": "DP-D06",
    "REQ-SCOPE-007": "DP-D02",
    "REQ-SCOPE-008": "DP-D02",
    "REQ-SEC-006": "DP-D02",
}


class TraceValidationError(RuntimeError):
    """Controlled fail-closed refresh error reported without a traceback."""


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value)).strip()


def digest_text(value: str) -> str:
    return hashlib.sha256(normalize(value).encode("utf-8")).hexdigest()


def digest_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def stable_json_digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def requirement_inventory(manifest: dict) -> list[dict]:
    return sorted(({
        "id": item["id"],
        "source_anchor": item["source_anchor"],
        "source_text_sha256": item["source_text_sha256"],
        "canonical_atomic_statement": item["canonical_atomic_statement"],
    } for item in manifest["requirements"]), key=lambda item: item["id"])


def alias_inventory(manifest: dict) -> list[dict]:
    return sorted(({
        "alias_anchor": item["alias_anchor"],
        "canonical_requirement_ids": item["canonical_requirement_ids"],
        "source_text_sha256": item["source_text_sha256"],
    } for item in manifest["aliases"]), key=lambda item: item["alias_anchor"])


def paragraph_text(node: ET.Element) -> str:
    parts: list[str] = []
    for item in node.iter():
        if item.tag == W + "t":
            parts.append(item.text or "")
        elif item.tag == W + "tab":
            parts.append("\t")
        elif item.tag == W + "br":
            parts.append("\n")
    return "".join(parts)


def load_docx(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    paragraphs: dict[str, str] = {}
    cells: dict[str, str] = {}
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    body = root.find(".//" + W + "body")
    if body is None:
        raise ValueError("DOCX has no w:body")
    p_no = t_no = 0
    for child in body:
        if child.tag == W + "p":
            p_no += 1
            paragraphs[f"P{p_no:03d}"] = paragraph_text(child)
        elif child.tag == W + "tbl":
            t_no += 1
            for r_no, row in enumerate(child.findall(W + "tr"), 1):
                for c_no, cell in enumerate(row.findall(W + "tc"), 1):
                    value = " / ".join(paragraph_text(p) for p in cell.findall(".//" + W + "p"))
                    cells[f"T{t_no:02d}.R{r_no:02d}.C{c_no:02d}"] = value
    return paragraphs, cells


def expand_docx_projection(spec: str, paragraphs: dict[str, str], cells: dict[str, str]) -> str:
    if not spec.startswith("DOCX:"):
        raise ValueError(f"not a DOCX projection: {spec}")
    body = spec[5:]
    fragment = None
    if "#fragment=" in body:
        body, fragment = body.split("#fragment=", 1)
    selected: list[str] = []
    for token in body.split("|"):
        token = token.strip()
        match = re.fullmatch(r"P(\d{3})-P(\d{3})", token)
        if match:
            selected.extend(paragraphs[f"P{i:03d}"] for i in range(int(match[1]), int(match[2]) + 1))
            continue
        if re.fullmatch(r"P\d{3}", token):
            selected.append(paragraphs[token])
            continue
        match = re.fullmatch(r"T(\d{2})\.R(\d{2})-R(\d{2})\[C([0-9]{2}(?:,C[0-9]{2})*)\]", token)
        if match:
            table, first, last = int(match[1]), int(match[2]), int(match[3])
            columns = [int(value) for value in re.findall(r"\d{2}", match[4])]
            for row in range(first, last + 1):
                for column in columns:
                    selected.append(cells[f"T{table:02d}.R{row:02d}.C{column:02d}"])
            continue
        if re.fullmatch(r"T\d{2}\.R\d{2}\.C\d{2}", token):
            selected.append(cells[token])
            continue
        raise ValueError(f"unsupported DOCX projection token: {token}")
    value = " / ".join(selected)
    if fragment is not None:
        if value.count(fragment) != 1:
            raise ValueError(f"fragment must occur exactly once in {spec!r}")
        value = fragment
    return value


def select_markdown(lines: list[str], selector: dict) -> str:
    pattern = re.compile(selector["pattern"])
    matches = [(line, pattern.fullmatch(line)) for line in lines]
    matches = [(line, match) for line, match in matches if match]
    if len(matches) != 1:
        raise ValueError(f"selector expected one match, got {len(matches)}: {selector['pattern']}")
    line, match = matches[0]
    group = selector.get("group")
    value = match.group(group) if group is not None else line
    fragment = selector.get("fragment")
    if fragment is not None:
        occurrence = int(selector.get("occurrence", 1))
        starts = [item.start() for item in re.finditer(re.escape(fragment), value)]
        if len(starts) < occurrence:
            raise ValueError(f"fragment occurrence missing for selector: {fragment}")
        value = fragment
    return value.strip()


def source_value(item: dict, sources: dict[str, Path], docx_cache: tuple[dict[str, str], dict[str, str]]) -> str:
    anchor = item.get("source_anchor") or item.get("alias_anchor")
    if anchor.startswith("DOCX:"):
        return expand_docx_projection(anchor, *docx_cache)
    selector = item.get("source_selector")
    if not isinstance(selector, dict):
        raise ValueError(f"missing source_selector for {anchor}")
    path = sources[selector["source_id"]]
    lines = path.read_text(encoding="utf-8").splitlines()
    return select_markdown(lines, selector)


def target_specs(repo: Path) -> list[dict]:
    plan = "docs/woozoo-trading-desk/mvp-plan-and-acceptance.md"
    scenarios = "docs/woozoo-trading-desk/phase-0/verification-strategy.md"
    result: list[dict] = []
    for number in range(1, 13):
        result.append({"id": f"MVP-{number:02d}", "type": "mvp", "path": plan,
                       "selector": {"kind": "markdown_regex_line", "pattern": rf"^\| MVP-{number:02d} \|.*\|$"}})
        result.append({"id": f"P0-{number:02d}", "type": "p0", "path": plan,
                       "selector": {"kind": "markdown_regex_line", "pattern": rf"^\| P0-{number:02d} \|.*\|$"}})
    for number in range(10):
        result.append({"id": f"PHASE-{number}", "type": "phase", "path": plan,
                       "selector": {"kind": "markdown_regex_line", "pattern": rf"^\| \*\*{number} — .*\|$"}})
    text = (repo / scenarios).read_text(encoding="utf-8")
    ids = sorted(set(re.findall(r"(?m)^\| ((?:CORE|PLAT|CONTRACT|SAFE|DATA|PTI|FIN|ORD|ATOM|RISK|KILL|AUTH|AI|SEC|E2E)-[0-9]{3}) \|", text)))
    for item_id in ids:
        result.append({"id": item_id, "type": "scenario", "path": scenarios,
                       "selector": {"kind": "markdown_regex_line", "pattern": rf"^\| {re.escape(item_id)} \|.*\|$"}})
    for entry in result:
        lines = (repo / entry["path"]).read_text(encoding="utf-8").splitlines()
        entry["normalized_anchor_digest"] = digest_text(select_markdown(lines, entry["selector"]))
    return result


def make_source_selectors(manifest: dict) -> None:
    for item in manifest["requirements"]:
        anchor = item["source_anchor"]
        if anchor.startswith("DOCX:"):
            item.pop("source_selector", None)
            continue
        if anchor.startswith("PHASE_CONTRACT:P0-"):
            item_id = anchor.split(":", 1)[1]
            item["source_selector"] = {"source_id": "PHASE_CONTRACT", "kind": "markdown_regex",
                                        "pattern": rf"^\| {item_id} \| (?P<value>.*?) \|$", "group": "value"}
        elif anchor.startswith("PHASE_CONTRACT:git-gate-"):
            gate = anchor.split(":", 1)[1].split("#", 1)[0]
            patterns = {
                "git-gate-01": r"^- 브랜치는 .* 직접 수정이 아니다\.$",
                "git-gate-02": r"^- 애플리케이션 소스, .* delta가 0이다\.$",
                "git-gate-03": r"^- diff 자체 리뷰, .* 증거가 있다\.$",
            }
            item["source_selector"] = {"source_id": "PHASE_CONTRACT", "kind": "markdown_regex",
                                        "pattern": patterns[gate], "fragment": item["source_text"]}
        elif anchor.startswith("ROOT_RULES:root-rule-"):
            number = int(re.search(r"root-rule-(\d+)", anchor).group(1))
            labels = {1: "Mission", 2: "기본 모드", 3: "금융 권위", 4: "AI 경계", 5: "데이터 무결성",
                      6: "Engineering", 7: "Security", 8: "Workflow", 9: "Definition of Done", 10: "품질 명령"}
            selector = {"source_id": "ROOT_RULES", "kind": "markdown_regex",
                        "pattern": rf"^{number}\. \*\*{re.escape(labels[number])}:\*\* (?P<value>.*)$", "group": "value"}
            if "#" in anchor:
                selector["fragment"] = item["source_text"]
                if anchor.endswith("#pnpm-ci"):
                    selector["occurrence"] = 1
            item["source_selector"] = selector
        else:
            raise ValueError(f"cannot derive source selector: {anchor}")


def expected_summary(manifest: dict) -> dict:
    requirements = manifest["requirements"]
    policy_resolutions = manifest.get("policy_resolutions", {})
    resolved_ids = set(policy_resolutions.get("resolved_requirement_ids", []))
    unresolved = sorted(
        item["id"] for item in requirements
        if (item["status"] == "DEFERRED_PENDING_USER_APPROVAL"
            or "pending explicit user policy approval" in item["canonical_atomic_statement"])
        and item["id"] not in resolved_ids
    )
    git_pending = sorted(
        item["id"] for item in requirements
        if item["id"].startswith("REQ-GIT-") and item["status"] == "GATE_PENDING_EVIDENCE"
    )
    return {
        "canonical_requirement_count": len(requirements),
        "alias_record_count": len(manifest["aliases"]),
        "target_index_count": len(manifest["target_index"]),
        "source_projection_count": len(requirements) + len(manifest["aliases"]),
        "disposition_counts": dict(sorted(Counter(item["disposition"] for item in requirements).items())),
        "status_counts": dict(sorted(Counter(item["status"] for item in requirements).items())),
        "duplicate_requirement_ids": [],
        "duplicate_source_anchors": [],
        "invalid_digest_fields": [],
        "invalid_target_ids": [],
        "policy_approved_requirement_ids": sorted(resolved_ids),
        "unresolved_user_decision_requirement_ids": unresolved,
        "git_evidence_pending_requirement_ids": git_pending,
        "acceptance_pass_claimed": False,
    }


def validate_policy_resolutions(manifest: dict, repo: Path) -> list[str]:
    """Check the reviewed delegation binding without treating it as acceptance PASS."""
    errors: list[str] = []
    resolution = manifest.get("policy_resolutions")
    if not isinstance(resolution, dict):
        return ["policy resolution binding is missing"]
    if resolution.get("status") != "POLICY_APPROVED_NOT_ACCEPTANCE_PASS":
        errors.append("policy resolution status is invalid")
    if resolution.get("approval_id") != "P0-POLICY-01":
        errors.append("policy resolution approval ID is invalid")
    if resolution.get("decision_package") != POLICY_DECISION_PACKAGE:
        errors.append("policy resolution decision package path is invalid")
    if resolution.get("approval_record") != POLICY_APPROVAL_RECORD:
        errors.append("policy resolution approval record path is invalid")
    expected_ids = sorted(REVIEWED_POLICY_RESOLUTION_MAP)
    if sorted(resolution.get("resolved_requirement_ids", [])) != expected_ids:
        errors.append("policy resolution requirement set differs from reviewed authority")
    if resolution.get("requirement_to_decision") != REVIEWED_POLICY_RESOLUTION_MAP:
        errors.append("policy resolution decision map differs from reviewed authority")
    for field, relative_path, expected_digest in (
        ("approval_record_sha256", POLICY_APPROVAL_RECORD, AUTHORITATIVE_POLICY_APPROVAL_RECORD_SHA256),
        ("decision_package_sha256", POLICY_DECISION_PACKAGE, AUTHORITATIVE_POLICY_DECISION_PACKAGE_SHA256),
    ):
        value = resolution.get(field)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            errors.append(f"policy resolution digest is invalid: {field}")
            continue
        if value != expected_digest:
            errors.append(f"policy resolution digest differs from reviewed authority: {field}")
        actual = digest_file(repo / relative_path)
        if actual != expected_digest:
            errors.append(f"policy authority artifact differs from reviewed authority: {field}")
        if actual != value:
            errors.append(f"policy resolution digest mismatch: {field}")
    try:
        approval_text = (repo / POLICY_APPROVAL_RECORD).read_text(encoding="utf-8")
        decision_text = (repo / POLICY_DECISION_PACKAGE).read_text(encoding="utf-8")
    except OSError as exc:
        return [*errors, f"policy resolution evidence unreadable: {exc}"]
    if "P0-POLICY-01" not in approval_text or "DP-D01~DP-D10" not in approval_text:
        errors.append("approval record does not contain the approved policy binding")
    if "POLICY_APPROVED" not in decision_text:
        errors.append("decision package is not marked POLICY_APPROVED")
    for decision_id in sorted(set(REVIEWED_POLICY_RESOLUTION_MAP.values())):
        if decision_id not in decision_text:
            errors.append(f"decision package is missing {decision_id}")
    return errors


def refresh(manifest: dict, repo: Path) -> None:
    replacements = {
        "DOCX:T13.R02-T13.R11": "DOCX:T13.R02-R11[C01,C02]",
        "DOCX:T18.R02.C02-T18.R08.C02": "DOCX:T18.R02-R08[C02]",
        "DOCX:P025#derivatives-context": "DOCX:P025#fragment=파생시장 문맥",
        "DOCX:P025#news-macro": "DOCX:P025#fragment=뉴스/거시 분석",
    }
    for item in manifest["requirements"]:
        item["source_anchor"] = replacements.get(item["source_anchor"], item["source_anchor"])
    alias_replacements = {
        "DOCX:P007,P010,T02-T07": "DOCX:P007|P010|T02.R01.C02|T03.R01.C02|T04.R01.C02|T05.R01.C02|T06.R01.C02|T07.R01.C02",
        "DOCX:P119-P130": "DOCX:P119-P130",
        "DOCX:P131-P136": "DOCX:P131-P136",
        "DOCX:T25.R02-T25.R11": "DOCX:T25.R02-R11[C01]",
    }
    for item in manifest["aliases"]:
        item["alias_anchor"] = alias_replacements.get(item["alias_anchor"], item["alias_anchor"])
    make_source_selectors(manifest)
    sources = {item["source_id"]: repo / item["path"] for item in manifest["source_documents"]}
    docx_cache = load_docx(sources["DOCX"])
    inventory = next(item["inventory"] for item in manifest["source_documents"] if item["source_id"] == "DOCX")
    inventory_errors: list[str] = []
    if len(docx_cache[0]) != inventory["body_paragraphs"]:
        inventory_errors.append("DOCX body paragraph inventory count mismatch")
    if len(docx_cache[1]) != inventory["table_cells"]:
        inventory_errors.append("DOCX table cell inventory count mismatch")
    if inventory_errors:
        raise TraceValidationError("; ".join(inventory_errors))
    for item in [*manifest["requirements"], *manifest["aliases"]]:
        value = source_value(item, sources, docx_cache)
        item["source_text"] = value
        item["source_text_sha256"] = digest_text(value)
    manifest["target_index"] = target_specs(repo)
    for item in manifest["target_catalogs"]:
        item["sha256"] = digest_file(repo / item["path"])
    index = {item["id"]: item for item in manifest["target_index"]}
    for requirement in manifest["requirements"]:
        bindings = {}
        for kind, field in TARGET_FIELDS.items():
            bindings[kind] = [{"id": item_id, "normalized_anchor_digest": index[item_id]["normalized_anchor_digest"]}
                              for item_id in requirement[field]]
        requirement["target_bindings"] = bindings
    coverage = manifest["source_coverage"]["classification_counts"]
    coverage["canonical_requirement_records"] = len(manifest["requirements"])
    coverage["canonical_docx_records"] = sum(item["source_anchor"].startswith("DOCX:") for item in manifest["requirements"])
    manifest["validation_summary"] = expected_summary(manifest)


def validate(manifest: dict, repo: Path) -> list[str]:
    errors: list[str] = []
    errors.extend(validate_policy_resolutions(manifest, repo))
    sources = {item["source_id"]: repo / item["path"] for item in manifest["source_documents"]}
    for item in manifest["source_documents"]:
        actual = digest_file(repo / item["path"])
        if actual != item["sha256"]:
            errors.append(f"source hash mismatch {item['source_id']}: {actual}")
    for item in manifest["target_catalogs"]:
        actual = digest_file(repo / item["path"])
        if actual != item["sha256"]:
            errors.append(f"target catalog hash mismatch {item['path']}: {actual}")
    docx_cache = load_docx(sources["DOCX"])
    inventory = next(item["inventory"] for item in manifest["source_documents"] if item["source_id"] == "DOCX")
    if len(docx_cache[0]) != inventory["body_paragraphs"]:
        errors.append("DOCX body paragraph inventory count mismatch")
    if len(docx_cache[1]) != inventory["table_cells"]:
        errors.append("DOCX table cell inventory count mismatch")
    all_source_items = [*manifest["requirements"], *manifest["aliases"]]
    for item in all_source_items:
        anchor = item.get("source_anchor") or item.get("alias_anchor")
        try:
            value = source_value(item, sources, docx_cache)
            if value != item["source_text"]:
                errors.append(f"source projection text mismatch: {anchor}")
            if digest_text(value) != item["source_text_sha256"]:
                errors.append(f"source projection digest mismatch: {anchor}")
        except Exception as exc:  # produce evidence instead of a traceback-only failure
            errors.append(f"source projection invalid {anchor}: {exc}")
    ids = [item["id"] for item in manifest["requirements"]]
    anchors = [item["source_anchor"] for item in manifest["requirements"]]
    if len(ids) != len(set(ids)):
        errors.append("duplicate requirement IDs")
    if len(anchors) != len(set(anchors)):
        errors.append("duplicate canonical source anchors")
    canonical_inventory = requirement_inventory(manifest)
    if (len(canonical_inventory) != AUTHORITATIVE_REQUIREMENT_COUNT
            or stable_json_digest(canonical_inventory) != AUTHORITATIVE_REQUIREMENT_FINGERPRINT):
        errors.append("canonical requirement inventory differs from reviewed external authority")
    reviewed_aliases = alias_inventory(manifest)
    if (len(reviewed_aliases) != AUTHORITATIVE_ALIAS_COUNT
            or stable_json_digest(reviewed_aliases) != AUTHORITATIVE_ALIAS_FINGERPRINT):
        errors.append("alias inventory differs from reviewed external authority")
    allowed_dispositions = set(manifest["allowed_dispositions"])
    allowed_statuses = set(manifest["allowed_statuses"])
    for item in manifest["requirements"]:
        if not REQ_RE.fullmatch(item["id"]):
            errors.append(f"invalid requirement ID: {item['id']}")
        if item["disposition"] not in allowed_dispositions:
            errors.append(f"invalid disposition: {item['id']}")
        if item["status"] not in allowed_statuses:
            errors.append(f"invalid status: {item['id']}")
    coverage = manifest["source_coverage"]["classification_counts"]
    expected_coverage = {
        "canonical_requirement_records": len(manifest["requirements"]),
        "canonical_docx_records": sum(item["source_anchor"].startswith("DOCX:") for item in manifest["requirements"]),
        "canonical_phase_contract_records": sum(item["source_anchor"].startswith("PHASE_CONTRACT:") for item in manifest["requirements"]),
        "canonical_root_rule_records": sum(item["source_anchor"].startswith("ROOT_RULES:") for item in manifest["requirements"]),
        "docx_alias_group_records": sum(item["alias_anchor"].startswith("DOCX:") for item in manifest["aliases"]),
        "uncovered_requirement_anchors": len(manifest["source_coverage"]["uncovered_requirement_anchors"]),
    }
    for key, value in expected_coverage.items():
        if coverage.get(key) != value:
            errors.append(f"source coverage count mismatch: {key}")
    target_index = manifest.get("target_index", [])
    try:
        independently_derived_targets = target_specs(repo)
    except Exception as exc:
        independently_derived_targets = []
        errors.append(f"independent target inventory derivation failed: {exc}")
    if (len(independently_derived_targets) != AUTHORITATIVE_TARGET_COUNT
            or stable_json_digest(independently_derived_targets) != AUTHORITATIVE_TARGET_FINGERPRINT):
        errors.append("independently derived target inventory differs from reviewed external authority")
    if target_index != independently_derived_targets:
        errors.append("target index differs from independently derived complete catalog")
    target_ids = [item["id"] for item in target_index]
    if len(target_ids) != len(set(target_ids)):
        errors.append("duplicate target index IDs")
    index: dict[str, dict] = {}
    for item in target_index:
        try:
            if not TARGET_RES[item["type"]].fullmatch(item["id"]):
                errors.append(f"invalid indexed target ID: {item['id']}")
            lines = (repo / item["path"]).read_text(encoding="utf-8").splitlines()
            value = select_markdown(lines, item["selector"])
            if digest_text(value) != item["normalized_anchor_digest"]:
                errors.append(f"target anchor digest mismatch: {item['id']}")
            index[item["id"]] = item
        except Exception as exc:
            errors.append(f"target index invalid {item.get('id')}: {exc}")
    req_ids = set(ids)
    for alias in manifest["aliases"]:
        for item_id in alias["canonical_requirement_ids"]:
            if item_id not in req_ids:
                errors.append(f"alias target missing: {alias['alias_anchor']} -> {item_id}")
    for requirement in manifest["requirements"]:
        for kind, field in TARGET_FIELDS.items():
            values = requirement[field]
            bindings = requirement.get("target_bindings", {}).get(kind, [])
            if [item["id"] for item in bindings] != values:
                errors.append(f"target binding ID/order mismatch: {requirement['id']}:{kind}")
                continue
            for binding in bindings:
                indexed = index.get(binding["id"])
                if indexed is None:
                    errors.append(f"unresolved target: {requirement['id']} -> {binding['id']}")
                elif binding["normalized_anchor_digest"] != indexed["normalized_anchor_digest"]:
                    errors.append(f"target binding digest mismatch: {requirement['id']} -> {binding['id']}")
    expected = expected_summary(manifest)
    if manifest.get("validation_summary") != expected:
        errors.append("stored validation_summary differs from independent recomputation")
    if manifest["overall_status"] == "PASS" or any(item["status"] == "PASS" for item in manifest["requirements"]):
        errors.append("acceptance PASS is not permitted while evidence remains pending")
    return errors


def target_catalog_co_shrink_test(manifest_path: Path, repo: Path) -> bool:
    """Prove candidate+catalog co-shrink cannot redefine the target authority."""
    original = json.loads(manifest_path.read_text(encoding="utf-8"))
    original_manifest_before = digest_file(manifest_path)
    target_paths = [repo / item["path"] for item in original["target_catalogs"]]
    original_catalogs_before = {path: digest_file(path) for path in target_paths}
    with tempfile.TemporaryDirectory(prefix="woozoo-trace-co-shrink-") as directory:
        temporary_repo = Path(directory)
        relative_paths = {
            item["path"] for item in [*original["source_documents"], *original["target_catalogs"]]
        }
        relative_paths.update({POLICY_APPROVAL_RECORD, POLICY_DECISION_PACKAGE})
        for relative in relative_paths:
            destination = temporary_repo / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(repo / relative, destination)
        temporary_manifest = temporary_repo / "docs/woozoo-trading-desk/phase-0/requirements-trace-manifest.json"
        temporary_manifest.parent.mkdir(parents=True, exist_ok=True)
        mutated = copy.deepcopy(original)
        mutated["target_index"] = [item for item in mutated["target_index"] if item["id"] != "E2E-005"]
        for requirement in mutated["requirements"]:
            requirement["target_scenario_ids"] = [
                item for item in requirement["target_scenario_ids"] if item != "E2E-005"
            ]
            requirement["target_bindings"]["scenario"] = [
                item for item in requirement["target_bindings"]["scenario"] if item["id"] != "E2E-005"
            ]
        mutated["validation_summary"] = expected_summary(mutated)
        temporary_manifest.write_text(json.dumps(mutated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        scenario_relative = next(
            item["path"] for item in original["target_catalogs"] if item["path"].endswith("verification-strategy.md")
        )
        temporary_catalog = temporary_repo / scenario_relative
        lines = temporary_catalog.read_text(encoding="utf-8").splitlines(keepends=True)
        retained = [line for line in lines if not re.match(r"^\| E2E-005 \|", line)]
        if len(retained) != len(lines) - 1:
            raise TraceValidationError("co-shrink setup expected exactly one E2E-005 row")
        temporary_catalog.write_text("".join(retained), encoding="utf-8")

        candidate_before = digest_file(temporary_manifest)
        temporary_catalog_before = digest_file(temporary_catalog)
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--manifest", str(temporary_manifest),
             "--repo-root", str(temporary_repo), "--refresh-derived"],
            text=True, capture_output=True, check=False,
        )
        combined = result.stdout + result.stderr
        candidate_unchanged = digest_file(temporary_manifest) == candidate_before
        temporary_catalog_unchanged = digest_file(temporary_catalog) == temporary_catalog_before

    original_manifest_unchanged = digest_file(manifest_path) == original_manifest_before
    original_catalogs_unchanged = all(digest_file(path) == value for path, value in original_catalogs_before.items())
    passed = (
        result.returncode != 0
        and "Traceback" not in combined
        and candidate_unchanged
        and temporary_catalog_unchanged
        and original_manifest_unchanged
        and original_catalogs_unchanged
    )
    if passed:
        print("MUTATION=TARGET_CATALOG_E2E_005_CO_SHRINK RESULT=FAIL_AS_EXPECTED")
        evidence = next((line for line in combined.splitlines() if "reviewed external authority" in line), "TRACE_REFRESH=FAIL")
        print(
            "MUTATION_EVIDENCE=" + evidence
            + "; candidate_unchanged=true; temp_catalog_unchanged=true"
            + "; original_manifest_unchanged=true; original_catalogs_unchanged=true"
        )
    else:
        print("MUTATION=TARGET_CATALOG_E2E_005_CO_SHRINK RESULT=UNEXPECTED")
    return passed


def policy_decision_package_co_edit_test(manifest_path: Path, repo: Path) -> bool:
    """Prove a candidate cannot authorize a co-edited policy decision package."""
    original = json.loads(manifest_path.read_text(encoding="utf-8"))
    original_manifest_before = digest_file(manifest_path)
    decision_path = repo / POLICY_DECISION_PACKAGE
    original_decision_before = digest_file(decision_path)
    with tempfile.TemporaryDirectory(prefix="woozoo-policy-co-edit-") as directory:
        temporary_repo = Path(directory)
        relative_paths = {item["path"] for item in [*original["source_documents"], *original["target_catalogs"]]}
        relative_paths.update({POLICY_APPROVAL_RECORD, POLICY_DECISION_PACKAGE})
        for relative in relative_paths:
            destination = temporary_repo / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(repo / relative, destination)

        temporary_decision = temporary_repo / POLICY_DECISION_PACKAGE
        original_text = temporary_decision.read_text(encoding="utf-8")
        modified_text = original_text.replace("0.25%", "99%", 1)
        if modified_text == original_text:
            raise TraceValidationError("policy co-edit setup could not find policy value")
        temporary_decision.write_text(modified_text, encoding="utf-8")
        temporary_decision_before = digest_file(temporary_decision)

        mutated = copy.deepcopy(original)
        mutated["policy_resolutions"]["decision_package_sha256"] = digest_file(temporary_decision)
        failures = validate(mutated, temporary_repo)
        temporary_decision_unchanged = digest_file(temporary_decision) == temporary_decision_before

    original_manifest_unchanged = digest_file(manifest_path) == original_manifest_before
    original_decision_unchanged = digest_file(decision_path) == original_decision_before
    passed = (
        any("policy resolution digest differs from reviewed authority" in error for error in failures)
        and original_manifest_unchanged
        and original_decision_unchanged
        and temporary_decision_unchanged
    )
    if passed:
        print("MUTATION=POLICY_DECISION_PACKAGE_CO_EDIT RESULT=FAIL_AS_EXPECTED")
        print("MUTATION_EVIDENCE=policy resolution digest differs from reviewed authority; original artifacts unchanged=true")
    else:
        print("MUTATION=POLICY_DECISION_PACKAGE_CO_EDIT RESULT=UNEXPECTED")
    return passed


def mutation_tests(manifest: dict, repo: Path, manifest_path: Path) -> bool:
    cases: list[tuple[str, dict]] = []
    phase = copy.deepcopy(manifest)
    changed = False
    for requirement in phase["requirements"]:
        if "PHASE-2" in requirement["target_phase_ids"]:
            position = requirement["target_phase_ids"].index("PHASE-2")
            requirement["target_phase_ids"][position] = "PHASE-9"
            changed = True
            break
    if not changed:
        raise ValueError("no PHASE-2 target available for mutation test")
    cases.append(("PHASE-2_TO_PHASE-9", phase))
    for name, replacement in (
        ("T13_C03_INJECTION", "DOCX:T13.R02-R11[C01,C02,C03]"),
        ("T13_C02_OMISSION", "DOCX:T13.R02-R11[C01]"),
    ):
        mutated = copy.deepcopy(manifest)
        target = next(item for item in mutated["requirements"] if item["id"] == "REQ-AI-007")
        target["source_anchor"] = replacement
        cases.append((name, mutated))

    target_shrink = copy.deepcopy(manifest)
    target_shrink["target_index"] = [item for item in target_shrink["target_index"] if item["id"] != "E2E-005"]
    for requirement in target_shrink["requirements"]:
        requirement["target_scenario_ids"] = [item for item in requirement["target_scenario_ids"] if item != "E2E-005"]
        requirement["target_bindings"]["scenario"] = [
            item for item in requirement["target_bindings"]["scenario"] if item["id"] != "E2E-005"
        ]
    target_shrink["validation_summary"] = expected_summary(target_shrink)
    cases.append(("TARGET_E2E_005_OMISSION", target_shrink))

    policy_shrink = copy.deepcopy(manifest)
    policy_shrink["policy_resolutions"]["resolved_requirement_ids"] = [
        item for item in policy_shrink["policy_resolutions"]["resolved_requirement_ids"] if item != "REQ-AI-007"
    ]
    policy_shrink["policy_resolutions"]["requirement_to_decision"].pop("REQ-AI-007")
    policy_shrink["validation_summary"] = expected_summary(policy_shrink)
    cases.append(("POLICY_RESOLUTION_REQ_AI_007_OMISSION", policy_shrink))

    requirement_shrink = copy.deepcopy(manifest)
    requirement_shrink["requirements"] = [
        item for item in requirement_shrink["requirements"] if item["id"] != "REQ-SCOPE-001"
    ]
    coverage = requirement_shrink["source_coverage"]["classification_counts"]
    coverage["canonical_requirement_records"] = len(requirement_shrink["requirements"])
    coverage["canonical_docx_records"] = sum(
        item["source_anchor"].startswith("DOCX:") for item in requirement_shrink["requirements"]
    )
    requirement_shrink["validation_summary"] = expected_summary(requirement_shrink)
    cases.append(("CANONICAL_REQ_SCOPE_001_OMISSION", requirement_shrink))

    alias_shrink = copy.deepcopy(manifest)
    alias_shrink["aliases"] = [
        item for item in alias_shrink["aliases"] if item["alias_anchor"] != "DOCX:T28.R01.C02"
    ]
    coverage = alias_shrink["source_coverage"]["classification_counts"]
    coverage["docx_alias_group_records"] = sum(
        item["alias_anchor"].startswith("DOCX:") for item in alias_shrink["aliases"]
    )
    alias_shrink["validation_summary"] = expected_summary(alias_shrink)
    cases.append(("ALIAS_T28_OMISSION", alias_shrink))
    success = True
    for name, mutated in cases:
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False) as stream:
            json.dump(mutated, stream, ensure_ascii=False, indent=2)
            temporary_path = Path(stream.name)
        try:
            reloaded = json.loads(temporary_path.read_text(encoding="utf-8"))
            failures = validate(reloaded, repo)
        finally:
            temporary_path.unlink(missing_ok=True)
        if failures:
            print(f"MUTATION={name} RESULT=FAIL_AS_EXPECTED")
            print(f"MUTATION_EVIDENCE={failures[0]}")
        else:
            print(f"MUTATION={name} RESULT=UNEXPECTED_PASS")
            success = False
    if not target_catalog_co_shrink_test(manifest_path, repo):
        success = False
    if not policy_decision_package_co_edit_test(manifest_path, repo):
        success = False
    return success


def atomic_write_validated_manifest(candidate: dict, destination: Path, repo: Path) -> list[str]:
    """Serialize, reparse, validate, then atomically replace the destination."""
    payload = json.dumps(candidate, ensure_ascii=False, indent=2) + "\n"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json.tmp", prefix=destination.name + ".", dir=destination.parent,
            encoding="utf-8", delete=False,
        ) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
            temporary_path = Path(stream.name)
        reparsed = json.loads(temporary_path.read_text(encoding="utf-8"))
        errors = validate(reparsed, repo)
        if errors:
            return errors
        os.replace(temporary_path, destination)
        temporary_path = None
        return []
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def refresh_safety_tests(manifest_path: Path, repo: Path) -> bool:
    """Prove failed refreshes are controlled and cannot overwrite any manifest."""
    canonical_before = digest_file(manifest_path)
    original = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases: list[tuple[str, dict]] = []

    inventory = copy.deepcopy(original)
    source = next(item for item in inventory["source_documents"] if item["source_id"] == "DOCX")
    source["inventory"]["body_paragraphs"] += 1
    cases.append(("REFRESH_INVENTORY_MISMATCH", inventory))

    invalid = copy.deepcopy(original)
    invalid["overall_status"] = "PASS"
    cases.append(("REFRESH_VALIDATION_FAILURE", invalid))

    success = True
    for name, mutated in cases:
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False) as stream:
            json.dump(mutated, stream, ensure_ascii=False, indent=2)
            temporary_path = Path(stream.name)
        before = digest_file(temporary_path)
        try:
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--manifest", str(temporary_path),
                 "--repo-root", str(repo), "--refresh-derived"],
                text=True, capture_output=True, check=False,
            )
            combined = result.stdout + result.stderr
            unchanged = digest_file(temporary_path) == before
            canonical_unchanged = digest_file(manifest_path) == canonical_before
        finally:
            temporary_path.unlink(missing_ok=True)
        if result.returncode != 0 and "Traceback" not in combined and unchanged and canonical_unchanged:
            print(f"MUTATION={name} RESULT=FAIL_CLOSED_AS_EXPECTED")
            first_line = next((line for line in combined.splitlines() if line.strip()), "controlled nonzero")
            print(f"MUTATION_EVIDENCE={first_line}; candidate_unchanged=true; canonical_unchanged=true")
        else:
            print(f"MUTATION={name} RESULT=UNEXPECTED")
            success = False
    return success


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--refresh-derived", action="store_true")
    parser.add_argument("--mutation-tests", action="store_true")
    args = parser.parse_args()
    repo = (args.repo_root or Path(__file__).resolve().parents[4]).resolve()
    manifest_path = (args.manifest or repo / "docs/woozoo-trading-desk/phase-0/requirements-trace-manifest.json").resolve()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print("TRACE_VALIDATION=FAIL")
        print(f"- manifest read/parse failed: {exc}")
        return 1
    if args.refresh_derived:
        candidate = copy.deepcopy(manifest)
        try:
            refresh(candidate, repo)
        except Exception as exc:
            print("TRACE_REFRESH=FAIL")
            print(f"- {type(exc).__name__}: {exc}")
            return 1
        errors = validate(candidate, repo)
        if errors:
            print("TRACE_REFRESH=FAIL")
            for error in errors:
                print(f"- {error}")
            return 1
        try:
            errors = atomic_write_validated_manifest(candidate, manifest_path, repo)
        except Exception as exc:
            print("TRACE_REFRESH=FAIL")
            print(f"- atomic candidate write failed: {type(exc).__name__}: {exc}")
            return 1
        if errors:
            print("TRACE_REFRESH=FAIL")
            for error in errors:
                print(f"- serialized candidate validation failed: {error}")
            return 1
        manifest = candidate
    errors = validate(manifest, repo)
    if errors:
        print("TRACE_VALIDATION=FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"TRACE_VALIDATION=PASS requirements={len(manifest['requirements'])} aliases={len(manifest['aliases'])} targets={len(manifest['target_index'])}")
    print("ACCEPTANCE_STATUS=UNVERIFIED")
    if args.mutation_tests:
        if not mutation_tests(manifest, repo, manifest_path):
            return 1
        if not refresh_safety_tests(manifest_path, repo):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
