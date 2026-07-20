from platform_core.config import PlatformSettings
from platform_core.primitives import canonical_json, new_request_id


def test_core_001_is_deterministic_and_paper_only() -> None:
    first = canonical_json({"b": 2, "a": 1})
    second = canonical_json({"a": 1, "b": 2})

    assert first == second == '{"a":1,"b":2}'
    assert new_request_id().version == 7
    assert PlatformSettings.from_mapping({"TRADING_MODE": "paper"}).trading_mode == "paper"
