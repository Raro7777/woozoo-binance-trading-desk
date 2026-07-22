from decimal import Decimal

import pytest

from control_api.trading_room import InMemoryTradingRoom, TradingRoomError, canonical_hash


def test_phase7_preview_hash_is_mutation_sensitive_and_notional_is_policy_capped() -> None:
    room = InMemoryTradingRoom()
    created = room.create_analysis("BTCUSDT", "preview-property")
    view = room.approval_view(str(created.body["proposal_id"]))
    preview = view["paper_order_preview"]
    assert isinstance(preview, dict)

    supplied_hash = preview["paper_order_preview_hash"]
    without_hash = {
        key: value for key, value in preview.items() if key != "paper_order_preview_hash"
    }
    assert canonical_hash(without_hash) == supplied_hash
    mutated = {**without_hash, "quantity": "0.00046"}
    assert canonical_hash(mutated) != supplied_hash

    notional = Decimal(str(preview["worst_case_notional"]))
    # The versioned genesis Paper equity is 10,000 USDT; policy limit is 0.25%.
    assert notional <= Decimal("10000") * Decimal("0.0025")


def test_phase7_idempotency_same_hash_replays_and_changed_hash_conflicts() -> None:
    room = InMemoryTradingRoom()
    first = room.create_analysis("ETHUSDT", "analysis-property")
    replay = room.create_analysis("ETHUSDT", "analysis-property")
    assert replay == first

    with pytest.raises(TradingRoomError, match="idempotency key body changed") as conflict:
        room.create_analysis("BTCUSDT", "analysis-property")
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
