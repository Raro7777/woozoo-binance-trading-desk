from copy import deepcopy

from control_api.trading_room import browser_safe_audit_value


def test_browser_audit_projection_recursively_removes_nonce_and_binding_material() -> None:
    raw = {
        "producer": "woozoo.paper-engine",
        "actor_id": None,
        "proposal_hash": "safe-business-hash",
        "approval_nonce": "never-browser-visible",
        "nested": {
            "authorization_nonce_hash": "never-browser-visible",
            "session_binding_hash": "never-browser-visible",
            "items": [
                {"csrf_binding": "never-browser-visible", "outcome": "RECORDED"},
                {"origin_hash": "never-browser-visible", "status": "CONSUMED"},
            ],
        },
    }
    durable_raw = deepcopy(raw)

    projected = browser_safe_audit_value(raw)

    assert raw == durable_raw
    assert projected == {
        "producer": "woozoo.paper-engine",
        "actor_id": None,
        "proposal_hash": "safe-business-hash",
        "nested": {"items": [{"outcome": "RECORDED"}, {"status": "CONSUMED"}]},
    }
