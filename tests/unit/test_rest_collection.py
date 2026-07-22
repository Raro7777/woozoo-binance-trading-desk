from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json

import pytest

from market_data_worker.capabilities import (
    KlineInterval,
    PublicRestRequest,
    RestCapability,
    Symbol,
)
from market_data_worker.pipeline import InMemoryMarketStore
from market_data_worker.rest_collection import PublicRestCollector
from market_data_worker.transport import PublicRestTransport
from market_data_worker.types import QualityStatus


class Response:
    status_code = 200
    headers: dict[str, str] = {}
    content = b'[{"id":1,"isBestMatch":true,"isBuyerMaker":false,"price":"60000.1","qty":"0.01","quoteQty":"600.001","time":1784419200000}]'

    def json(self) -> object:
        return json.loads(self.content)


class RateLimitedResponse:
    status_code = 429
    headers = {"Retry-After": "7"}
    content = b'{"code":-1003,"msg":"rate limited"}'

    def json(self) -> object:
        return json.loads(self.content)


class MalformedResponse:
    status_code = 200
    headers: dict[str, str] = {}
    content = b'{"truncated":'

    def json(self) -> object:
        raise json.JSONDecodeError("truncated", self.content.decode(), len(self.content))


def test_rest_response_and_item_provenance_are_durable_before_normalization() -> None:
    store = InMemoryMarketStore()
    collector = PublicRestCollector(
        PublicRestTransport(get=lambda _uri, _timeout: Response()), store
    )
    result = collector.collect(
        PublicRestRequest(RestCapability.TRADES, Symbol.BTCUSDT),
        "018f7000-0000-7000-8000-000000000001",
        datetime(2026, 7, 19, tzinfo=UTC),
    )

    assert result.raw_count == 3 and result.normalized_count == 1
    assert store.raw_events[0].payload_bytes == Response.content
    assert store.raw_events[0].record_kind == "rest_response"
    assert store.raw_events[1].record_kind == "rest_item"
    assert store.raw_events[1].parent_raw_event_id == store.raw_events[0].raw_event_id
    assert store.raw_events[2].parent_raw_event_id == store.raw_events[1].raw_event_id
    assert (
        store.raw_events[2].payload_hash
        == hashlib.sha256(store.raw_events[2].payload_bytes).hexdigest()
    )
    assert store.normalized_events[0].raw_event_id == store.raw_events[2].raw_event_id
    assert store.normalized_events[0].raw_payload_hash == store.raw_events[2].payload_hash


def test_live_rest_receipt_clock_is_sampled_after_transport_returns() -> None:
    store = InMemoryMarketStore()
    request_started_at = datetime(2026, 7, 19, tzinfo=UTC)
    response_received_at = request_started_at + timedelta(seconds=4)
    transport_finished = False

    def get(_uri: str, _timeout: float) -> Response:
        nonlocal transport_finished
        transport_finished = True
        return Response()

    def observed_clock() -> datetime:
        assert transport_finished is True
        return response_received_at

    collector = PublicRestCollector(
        PublicRestTransport(get=get),
        store,
        observed_clock=observed_clock,
    )
    collector.collect(
        PublicRestRequest(RestCapability.TRADES, Symbol.BTCUSDT),
        "018f7000-0000-7000-8000-000000000001",
        request_started_at,
    )

    assert {raw.received_at for raw in store.raw_events} == {response_received_at}
    assert store.normalized_events[0].received_at == response_received_at


class FutureKlineResponse:
    status_code = 200
    headers: dict[str, str] = {}
    content = b'[[1784419200000,"60000","60001","59999","60000.1","10",1784419259999,"600000",1,"5","300000","0"]]'

    def json(self) -> object:
        return json.loads(self.content)


def test_rest_kline_future_close_is_quarantined_and_never_forced_closed() -> None:
    store = InMemoryMarketStore()
    collector = PublicRestCollector(
        PublicRestTransport(get=lambda _uri, _timeout: FutureKlineResponse()), store
    )
    result = collector.collect(
        PublicRestRequest(
            RestCapability.KLINES,
            Symbol.BTCUSDT,
            interval=KlineInterval.ONE_MINUTE,
        ),
        "018f7000-0000-7000-8000-000000000001",
        datetime(2026, 7, 19, tzinfo=UTC),
    )

    assert result.normalized_count == 0
    assert store.quality_events[-1].reason == "kline_incomplete"
    assert store.quality_events[-1].status is QualityStatus.INVALID


def test_retry_after_blocks_the_collector_before_a_followup_network_call() -> None:
    calls = 0

    def get(_uri: str, _timeout: float) -> RateLimitedResponse:
        nonlocal calls
        calls += 1
        return RateLimitedResponse()

    collector = PublicRestCollector(PublicRestTransport(get=get), InMemoryMarketStore())
    now = datetime(2026, 7, 19, tzinfo=UTC)
    request = PublicRestRequest(RestCapability.TRADES, Symbol.BTCUSDT)

    first = collector.collect(request, "018f7000-0000-7000-8000-000000000001", now)
    with pytest.raises(RuntimeError, match="Retry-After"):
        collector.collect(
            request,
            "018f7000-0000-7000-8000-000000000001",
            now + timedelta(seconds=6),
        )

    assert first.status_code == 429 and first.raw_count == 1
    assert calls == 1


def test_malformed_rest_json_is_preserved_exactly_and_fails_closed() -> None:
    store = InMemoryMarketStore()
    collector = PublicRestCollector(
        PublicRestTransport(get=lambda _uri, _timeout: MalformedResponse()), store
    )

    result = collector.collect(
        PublicRestRequest(RestCapability.TRADES, Symbol.BTCUSDT),
        "018f7000-0000-7000-8000-000000000001",
        datetime(2026, 7, 19, tzinfo=UTC),
    )

    assert result.raw_count == 1 and result.normalized_count == 0
    assert store.raw_events[0].payload_bytes == MalformedResponse.content
    assert store.quality_events[0].status.value == "invalid"
