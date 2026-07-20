from __future__ import annotations

from copy import deepcopy
import json

from risk_engine import evaluate_risk
from test_risk_engine import risk_input


def test_risk_replay_is_independent_of_json_key_order_and_process_identity() -> None:
    payload = risk_input()
    reordered = json.loads(json.dumps(payload, sort_keys=False))
    reordered = {key: reordered[key] for key in reversed(tuple(reordered))}

    first = evaluate_risk(payload)
    replayed = evaluate_risk(deepcopy(reordered))

    assert replayed == first


def test_reason_message_or_recording_metadata_cannot_enter_decision_hash() -> None:
    payload = risk_input()
    baseline = evaluate_risk(payload)
    replayed = evaluate_risk(payload)
    assert replayed.decision_hash == baseline.decision_hash
    assert replayed.decision_schema_version == "woozoo.risk-decision/v1"
