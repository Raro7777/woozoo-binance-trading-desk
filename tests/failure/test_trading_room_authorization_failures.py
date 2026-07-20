from decimal import Decimal

from control_api.trading_room import InMemoryTradingRoom


def test_auth_002_stale_state_before_human_decision_has_zero_order_effect() -> None:
    room = InMemoryTradingRoom()
    created = room.create_analysis("BTCUSDT", "stale-before-approval")
    proposal_id = str(created.body["proposal_id"])
    ready = room.approval_view(proposal_id)
    assert ready["status"] == "READY"

    room.set_guard_state(data_quality="STALE")
    blocked = room.approval_view(proposal_id)
    assert blocked["status"] == "BLOCKED"
    assert blocked["reason_codes"] == ["DATA_STALE"]
    assert room.portfolio()["orders"] == []


def test_kill_001_activation_cancels_all_open_orders_in_deterministic_order() -> None:
    room = InMemoryTradingRoom()
    order_ids: list[str] = []
    for index, symbol in enumerate(("BTCUSDT", "ETHUSDT"), start=1):
        created = room.create_analysis(symbol, f"kill-run-{index}")
        proposal_id = str(created.body["proposal_id"])
        view = room.approval_view(proposal_id)
        approved = room.decide_approval(
            proposal_id=proposal_id,
            decision="APPROVE",
            expected_version=1,
            preview_hash=str(view["paper_order_preview_hash"]),
            idempotency_key=f"kill-approval-{index}",
            reason="deterministic Kill race test",
        )
        order = approved.body["order"]
        assert isinstance(order, dict)
        order_ids.append(str(order["order_id"]))

    activated = room.activate_kill(0, "operator safety drill")
    assert activated.body["cancelled_order_ids"] == sorted(order_ids)
    portfolio = room.portfolio()
    assert all(order["status"] == "CANCELLED" for order in portfolio["orders"])
    assert Decimal(portfolio["available_quote"]) == Decimal("10000")
    assert Decimal(portfolio["held_quote"]) == Decimal("0")
    entries = portfolio["ledger_entries"]
    for asset in {entry["asset"] for entry in entries}:
        debits = sum(
            Decimal(entry["amount"])
            for entry in entries
            if entry["asset"] == asset and entry["side"] == "DEBIT"
        )
        credits = sum(
            Decimal(entry["amount"])
            for entry in entries
            if entry["asset"] == asset and entry["side"] == "CREDIT"
        )
        assert debits == credits
