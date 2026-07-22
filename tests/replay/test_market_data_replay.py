from __future__ import annotations

from pathlib import Path
import asyncio
import hashlib
import json

import pytest

from market_data_worker.replay import ReplayResult, load_recorded_events, replay_recorded_events
from market_data_worker.runner import run_market_data
from market_data_worker.types import QualityStatus


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "market-data" / "recorded-events.jsonl"
MANIFEST = ROOT / "docs" / "woozoo-trading-desk" / "phase-2" / "p2-scenario-manifest.json"


def test_data_001_recorded_replay_is_deterministic_and_deduplicated() -> None:
    events = load_recorded_events(FIXTURE)
    first = replay_recorded_events(events)
    second = replay_recorded_events((*events, events[0]))

    assert first.digest == second.digest
    assert first.normalized_count == second.normalized_count == 5
    assert first.symbols == ("BTCUSDT", "ETHUSDT")

    dispatched = asyncio.run(
        run_market_data(
            {"TRADING_MODE": "paper", "MARKET_DATA_SOURCE": "recorded"},
            recorded_path=FIXTURE,
        )
    )
    assert isinstance(dispatched, ReplayResult)
    assert dispatched.digest == first.digest


def test_data_002_gap_is_observable_and_blocks_the_gapped_effect() -> None:
    events = load_recorded_events(FIXTURE)
    before = replay_recorded_events(events[:1])
    gap_event = events[1].with_payload({**events[1].payload, "t": 103})
    after = replay_recorded_events((events[0], gap_event))

    assert before.normalized_count == 1
    assert after.normalized_count == 1
    assert after.quality is QualityStatus.STALE
    assert "sequence_gap" in after.quality_reasons
    assert before.digest != after.digest


@pytest.mark.legacy_acceptance
def test_phase_two_scenario_manifest_is_closed_and_matches_the_fixture() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    directory_digest = hashlib.sha256()
    for fixture in sorted(FIXTURE.parent.iterdir()):
        directory_digest.update(fixture.name.encode("utf-8"))
        directory_digest.update(fixture.read_bytes())

    assert [scenario["id"] for scenario in manifest["scenarios"]] == [
        "DATA-001",
        "DATA-002",
        "DATA-003",
        "DATA-004",
        "DATA-005",
        "DATA-006",
        "DATA-007",
    ]
    assert manifest["fixture_manifest_sha256"] == directory_digest.hexdigest()
    assert manifest["recorded_events_sha256"] == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert (
        manifest["expected_replay_digest"]
        == replay_recorded_events(load_recorded_events(FIXTURE)).digest
    )
