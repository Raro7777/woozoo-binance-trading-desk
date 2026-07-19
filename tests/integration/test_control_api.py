import asyncio
import json
from pathlib import Path

import httpx
import pytest

from control_api.app import create_app
from control_api.dependencies import DependencySnapshot, StaticHealthProbe


ROOT = Path(__file__).resolve().parents[2]
OPENAPI = json.loads((ROOT / "packages/contracts/spec/openapi.v1.json").read_text(encoding="utf-8"))


def get_health_response(app: object) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get("/api/v1/health")

    return asyncio.run(request())


def assert_matches_schema(value: object, schema: dict[str, object]) -> None:
    if "$ref" in schema:
        reference = schema["$ref"]
        assert isinstance(reference, str)
        name = reference.rsplit("/", maxsplit=1)[-1]
        assert_matches_schema(value, OPENAPI["components"]["schemas"][name])
        return
    if "const" in schema:
        assert value == schema["const"]
        return
    if "enum" in schema:
        assert value in schema["enum"]
        return
    if schema.get("type") == "null":
        assert value is None
        return
    if schema.get("type") == "string":
        assert isinstance(value, str)
        return
    assert schema.get("type") == "object"
    assert isinstance(value, dict)
    properties = schema["properties"]
    assert isinstance(properties, dict)
    assert set(schema["required"]) == set(value)
    for key, child_schema in properties.items():
        assert_matches_schema(value[key], child_schema)


def test_plat_001_exposes_only_the_paper_health_shell() -> None:
    secret_database_url = "postgresql://sentinel-api-key@127.0.0.1:5433/woozoo"
    app = create_app(
        environment={
            "TRADING_MODE": "paper",
            "DATABASE_URL": secret_database_url,
            "REDIS_URL": "redis://127.0.0.1:6380/0",
        },
        probe=StaticHealthProbe(DependencySnapshot(postgres="healthy", redis="healthy")),
    )
    response = get_health_response(app)

    assert response.status_code == 200
    assert response.json()["data"]["trading_mode"] == "paper"
    assert "sentinel-api-key" not in response.text
    assert {route.path for route in app.routes} == {
        "/api/v1/health",
        "/api/v1/markets/{symbol}/status",
    }


def test_required_postgres_failure_is_fail_closed() -> None:
    app = create_app(
        environment={
            "TRADING_MODE": "paper",
            "DATABASE_URL": "postgresql://postgres@127.0.0.1:5433/woozoo",
            "REDIS_URL": "redis://127.0.0.1:6380/0",
        },
        probe=StaticHealthProbe(DependencySnapshot(postgres="unavailable", redis="unknown")),
    )

    response = get_health_response(app)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


def test_plat_001_runtime_health_payloads_match_the_checked_contract() -> None:
    environment = {
        "TRADING_MODE": "paper",
        "DATABASE_URL": "postgresql://postgres@127.0.0.1:5433/woozoo",
        "REDIS_URL": "redis://127.0.0.1:6380/0",
    }
    healthy = get_health_response(
        create_app(
            environment=environment,
            probe=StaticHealthProbe(DependencySnapshot(postgres="healthy", redis="healthy")),
        )
    )
    unavailable = get_health_response(
        create_app(
            environment=environment,
            probe=StaticHealthProbe(DependencySnapshot(postgres="unavailable", redis="unknown")),
        )
    )

    assert_matches_schema(
        healthy.json(),
        OPENAPI["paths"]["/api/v1/health"]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"],
    )
    assert_matches_schema(
        unavailable.json(),
        OPENAPI["paths"]["/api/v1/health"]["get"]["responses"]["503"]["content"][
            "application/json"
        ]["schema"],
    )
    with pytest.raises(RuntimeError, match="authoritative"):
        create_app(environment=environment).openapi()


def test_plat_001_uses_the_process_environment_when_not_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/woozoo")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6380/0")
    app = create_app(
        probe=StaticHealthProbe(DependencySnapshot(postgres="healthy", redis="healthy"))
    )

    response = get_health_response(app)

    assert response.status_code == 200
    assert response.json()["data"]["trading_mode"] == "paper"
