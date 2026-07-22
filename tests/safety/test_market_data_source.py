from __future__ import annotations

import pytest

from market_data_worker.settings import MarketDataSettings, MarketDataSource


def test_market_data_source_is_exact_and_fail_closed() -> None:
    assert (
        MarketDataSettings.from_mapping({"MARKET_DATA_SOURCE": "recorded"}).source
        is MarketDataSource.RECORDED
    )
    assert (
        MarketDataSettings.from_mapping({"MARKET_DATA_SOURCE": "spot_public"}).source
        is MarketDataSource.SPOT_PUBLIC
    )
    for value in (None, "", "public", "live", "unknown"):
        values = {} if value is None else {"MARKET_DATA_SOURCE": value}
        with pytest.raises(ValueError):
            MarketDataSettings.from_mapping(values)
