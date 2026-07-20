from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from platform_core.generated_contracts import (
    EvidenceCommandBindingV1,
    EvidenceCommandReceiptBindingV1,
)


ROOT = Path(__file__).resolve().parents[2]


def test_evid_002_snapshot_contract_is_closed_and_consumer_complete() -> None:
    schema = json.loads(
        (ROOT / "packages/contracts/spec/evidence-snapshot.v1.json").read_text("utf-8")
    )

    assert schema["additionalProperties"] is False
    assert schema["properties"]["quality"] == {"const": "healthy"}
    assert schema["properties"]["quality_reasons"]["maxItems"] == 0
    assert {"items", "candles", "features"} <= set(schema["required"])
    assert schema["$defs"]["EvidenceCandleV1"]["additionalProperties"] is False
    assert schema["$defs"]["EvidenceFeatureV1"]["properties"]["name"]["enum"] == [
        "close_return_1",
        "close_sma_20",
        "close_rsi_14",
    ]
    assert schema["$defs"]["EvidenceCandleV1"]["properties"]["open"] == {
        "$ref": "#/$defs/PositiveDecimalString"
    }
    assert schema["$defs"]["EvidenceFeatureV1"]["properties"]["value"] == {
        "$ref": "#/$defs/FixedScale18SignedDecimalString"
    }
    assert schema["properties"]["candles"]["minItems"] == 84
    assert schema["properties"]["candles"]["maxItems"] == 84
    assert schema["properties"]["features"]["minItems"] == 12
    assert schema["properties"]["features"]["maxItems"] == 12
    assert schema["properties"]["items"]["minItems"] == 256
    assert schema["properties"]["items"]["maxItems"] == 256


def test_evidence_command_generated_bindings_cover_request_and_receipt() -> None:
    command: EvidenceCommandBindingV1 = {
        "symbol": "BTCUSDT",
        "as_of": "2026-07-19T00:00:00Z",
        "knowledge_cutoff": "2026-07-18T23:00:00Z",
    }
    receipt: EvidenceCommandReceiptBindingV1 = {
        "evidence_id": "a" * 64,
        "evidence_digest": "b" * 64,
        "created": True,
    }

    assert command["knowledge_cutoff"] < command["as_of"]
    assert receipt["created"] is True

    openapi = json.loads((ROOT / "packages/contracts/spec/openapi.v1.json").read_text("utf-8"))
    operation = openapi["paths"]["/api/v1/commands/evidence-snapshots"]["post"]
    assert operation["security"] == [{"InternalServiceMutualTLS": []}]
    assert "403" in operation["responses"]


@pytest.mark.parametrize("invalid", ["NaN", "Infinity", "-Infinity", "oops", "1e3", "+1"])
def test_evidence_decimal_contract_rejects_non_finite_or_non_decimal_strings(
    invalid: str,
) -> None:
    schema = json.loads(
        (ROOT / "packages/contracts/spec/evidence-snapshot.v1.json").read_text("utf-8")
    )
    assert re.fullmatch(schema["$defs"]["NonNegativeDecimalString"]["pattern"], invalid) is None
    assert re.fullmatch(schema["$defs"]["PositiveDecimalString"]["pattern"], invalid) is None
    assert (
        re.fullmatch(schema["$defs"]["FixedScale18SignedDecimalString"]["pattern"], invalid) is None
    )


def test_evidence_contract_rejects_zero_prices_and_noncanonical_feature_scale() -> None:
    schema = json.loads(
        (ROOT / "packages/contracts/spec/evidence-snapshot.v1.json").read_text("utf-8")
    )
    positive = schema["$defs"]["PositiveDecimalString"]["pattern"]
    feature = schema["$defs"]["FixedScale18SignedDecimalString"]["pattern"]

    assert re.fullmatch(positive, "0") is None
    assert re.fullmatch(positive, "0.000000") is None
    assert re.fullmatch(positive, "0.000001") is not None
    assert re.fullmatch(feature, "1.2") is None
    assert re.fullmatch(feature, "1.200000000000000000") is not None
    assert re.fullmatch(feature, "-0.500000000000000000") is not None
