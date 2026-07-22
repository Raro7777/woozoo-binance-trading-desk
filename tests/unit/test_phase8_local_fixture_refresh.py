from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "scripts" / "run-phase8-testnet-local.mjs"
LIVE_CONTROL = ROOT / "tests" / "e2e" / "live_control_api.py"
WEB_API = ROOT / "apps" / "trading-room-web" / "src" / "lib" / "api.ts"
ANALYSIS_LAUNCHER = (
    ROOT / "apps" / "trading-room-web" / "src" / "components" / "analysis-launcher.tsx"
)


def test_local_fixture_refresh_primes_both_markets_before_reporting_ready() -> None:
    source = LAUNCHER.read_text("utf-8")

    assert "const refreshChildren = new Set();" in source
    assert "refreshChildren.add(child);" in source
    assert "refreshChildren.delete(child);" in source
    assert "async function refreshBoth(required)" in source
    assert "await refreshBoth(true);" in source
    assert "await refreshBoth(false);" in source
    assert "if (required)" in source
    assert 'await refresh("BTCUSDT", required);' in source
    assert 'await refresh("ETHUSDT", required);' in source
    assert 'Promise.all(["BTCUSDT", "ETHUSDT"].map(refresh))' not in source


def test_fixture_refresh_recovers_the_recorded_collector_session() -> None:
    source = LIVE_CONTROL.read_text("utf-8")

    assert '"e2e_refresh_complete"' in source
    assert "QualityStatus.HEALTHY" in source
    assert "def refresh_public_market(*, include_trade: bool)" in source
    assert "refresh_public_market(include_trade=True)" in source
    assert "refresh_public_market(include_trade=False)" in source


def test_missing_current_book_has_a_clear_korean_operator_message() -> None:
    source = WEB_API.read_text("utf-8")

    assert (
        'RISK_BOOK_NOT_FOUND: "BTC·ETH 현재 호가가 아직 준비되지 않았습니다. 잠시 뒤 다시 시도하세요."'
        in source
    )


def test_analysis_retries_only_the_transient_book_gap_with_one_idempotency_key() -> None:
    api_source = WEB_API.read_text("utf-8")
    launcher_source = ANALYSIS_LAUNCHER.read_text("utf-8")

    assert "readonly code?: string" in api_source
    assert 'reason.code === "RISK_BOOK_NOT_FOUND"' in launcher_source
    assert "const idempotencyKey = crypto.randomUUID();" in launcher_source
    assert "idempotencyKey," in launcher_source
    assert "attempt < 2" in launcher_source
