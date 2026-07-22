from platform_core.config import PlatformSettings

import pytest


@pytest.mark.parametrize("value", [None, "", "live", "testnet", "unknown"])
def test_safe_001_rejects_non_paper_modes_before_startup(value: str | None) -> None:
    values: dict[str, str] = {}
    if value is not None:
        values["TRADING_MODE"] = value

    with pytest.raises(ValueError):
        PlatformSettings.from_mapping(values)
