"""Phase 8 deployment packaging must preserve authenticated process boundaries."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]


def _service_block(compose: str, name: str) -> str:
    marker = f"\n  {name}:\n"
    after = compose.split(marker, 1)[1]
    return re.split(r"\n  (?=[a-z0-9-]+:\n)|\nnetworks:\n", after, maxsplit=1)[0]


def test_phase8_runtime_is_authenticated_secretless_and_network_isolated() -> None:
    compose = (ROOT / "compose.phase8.yaml").read_text(encoding="utf-8")

    assert "POSTGRES_HOST_AUTH_METHOD: trust" not in compose
    assert "--auth-host=scram-sha-256" in compose
    assert "POSTGRES_PASSWORD_FILE:" in compose
    assert "x-woozoo-boundary: phase8-authenticated-runtime" in compose

    for role in (
        "woozoo_control_api",
        "woozoo_testnet_execution",
        "woozoo_spot_testnet_gateway",
    ):
        assert role in compose
        assert f"{role}_password" in compose

    assert "authority-network:" in compose
    assert "operator-network:" in compose
    assert "loopback-ingress-network:" in compose
    assert "exchange-egress-network:" in compose
    assert "internal: true" in compose
    assert 'ports:\n      - "127.0.0.1:' in compose
    assert "SPOT_TESTNET_API_KEY_FILE: /run/secrets/spot_testnet_api_key" in compose
    assert "SPOT_TESTNET_SIGNING_SECRET_FILE: /run/secrets/spot_testnet_signing_secret" in compose
    assert "file: ${SPOT_TESTNET_API_KEY_FILE:?set SPOT_TESTNET_API_KEY_FILE}" in compose
    assert (
        "file: ${SPOT_TESTNET_SIGNING_SECRET_FILE:?set SPOT_TESTNET_SIGNING_SECRET_FILE}" in compose
    )
    assert "spot_testnet_api_key:\n    external: true" not in compose
    assert "spot_testnet_signing_secret:\n    external: true" not in compose
    assert "NEXT_PUBLIC_" not in compose

    control_api = _service_block(compose, "control-api")
    execution = _service_block(compose, "testnet-execution-worker")
    gateway = "\n".join(
        _service_block(compose, service)
        for service in (
            "spot-testnet-gateway-command",
            "spot-testnet-gateway-reconciliation",
            "spot-testnet-gateway-user-data",
        )
    )
    assert "woozoo_testnet_execution_password" not in control_api
    assert "woozoo_spot_testnet_gateway_password" not in control_api
    assert "woozoo_control_api_password" not in execution
    assert "woozoo_spot_testnet_gateway_password" not in execution
    assert "woozoo_control_api_password" not in gateway
    assert "woozoo_testnet_execution_password" not in gateway
    assert "spot_testnet_api_key" not in control_api
    assert "spot_testnet_signing_secret" not in control_api
    assert "spot_testnet_api_key" not in execution
    assert "spot_testnet_signing_secret" not in execution
    assert "PHASE8_CONTROL_API_TRUSTED_PROXY_HOST: control-api-loopback-proxy" in control_api
    assert "control-api-loopback-proxy: { condition: service_started }" in control_api

    postgres = _service_block(compose, "postgres")
    postgres_proxy = _service_block(compose, "postgres-loopback-proxy")
    control_proxy = _service_block(compose, "control-api-loopback-proxy")
    assert "loopback-ingress-network" not in postgres
    assert "loopback-ingress-network" not in control_api
    for proxy in (postgres_proxy, control_proxy):
        assert "loopback-ingress-network" in proxy
        assert "secrets:" not in proxy
        assert "SPOT_TESTNET_" not in proxy
        assert "read_only: true" in proxy
        assert "cap_drop: [ALL]" in proxy
        assert "no-new-privileges:true" in proxy
    assert "depends_on:\n      control-api:" not in control_proxy

    entrypoint = (ROOT / "infra/runtime/phase8-entrypoint.py").read_text(encoding="utf-8")
    assert '"--forwarded-allow-ips"' in entrypoint
    assert '"--forwarded-allow-ips", "*"' not in entrypoint


def test_phase8_workers_are_long_running_and_gateway_modes_are_concurrent() -> None:
    compose = (ROOT / "compose.phase8.yaml").read_text(encoding="utf-8")

    for service in (
        "testnet-execution-worker",
        "spot-testnet-gateway-command",
        "spot-testnet-gateway-reconciliation",
        "spot-testnet-gateway-user-data",
    ):
        assert f"  {service}:" in compose
    assert compose.count('restart: "unless-stopped"') >= 4
    assert "SPOT_TESTNET_GATEWAY_RUN_MODE: command" in compose
    assert "SPOT_TESTNET_GATEWAY_RUN_MODE: reconciliation" in compose
    assert "SPOT_TESTNET_GATEWAY_RUN_MODE: user-data" in compose


def test_phase8_runtime_images_do_not_share_python_import_roots() -> None:
    compose = (ROOT / "compose.phase8.yaml").read_text(encoding="utf-8")
    assert "phase8-control-api.Dockerfile" in compose
    assert "phase8-loopback-proxy.Dockerfile" in compose
    assert "phase8-testnet-execution.Dockerfile" in compose
    assert "phase8-spot-testnet-gateway.Dockerfile" in compose

    execution = (ROOT / "infra/containers/phase8-testnet-execution.Dockerfile").read_text(
        encoding="utf-8"
    )
    gateway = (ROOT / "infra/containers/phase8-spot-testnet-gateway.Dockerfile").read_text(
        encoding="utf-8"
    )
    assert "services/control-api" not in execution
    assert "services/spot-testnet-gateway" not in execution
    assert "services/control-api" not in gateway
    assert "services/testnet-execution-service" not in gateway


def test_gateway_stages_exchange_secrets_as_non_root_mode_0400() -> None:
    entrypoint = (ROOT / "infra/runtime/phase8-entrypoint.py").read_text(encoding="utf-8")
    gateway = (ROOT / "infra/containers/phase8-spot-testnet-gateway.Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "_stage_gateway_secret" in entrypoint
    assert "chmod(0o400)" in entrypoint
    assert "os.chown" in entrypoint
    assert "os.setuid" in entrypoint
    assert "USER 10001:10001" not in gateway


def test_committed_environment_schema_contains_paths_not_secret_values() -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")

    required_empty_paths = (
        "PHASE8_POSTGRES_SUPERUSER_PASSWORD_FILE=",
        "PHASE8_CONTROL_API_DATABASE_PASSWORD_FILE=",
        "PHASE8_TESTNET_EXECUTION_DATABASE_PASSWORD_FILE=",
        "PHASE8_SPOT_TESTNET_GATEWAY_DATABASE_PASSWORD_FILE=",
    )
    for entry in required_empty_paths:
        assert f"\n{entry}\n" in f"\n{example}"
    assert "NEXT_PUBLIC_" not in example
    assert "POSTGRES_HOST_AUTH_METHOD" not in example


def test_local_phase8_secret_and_environment_files_are_git_ignored() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert ".secrets/" in ignore
    assert ".env.phase8.local" in ignore


def test_phase8_launcher_stops_profile_services_without_masking_the_root_error() -> None:
    launcher = (ROOT / "scripts" / "start-phase8-testnet.ps1").read_text(encoding="utf-8")
    command_launcher = (ROOT / "START_TESTNET.cmd").read_text(encoding="utf-8")
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")

    assert "$composeRuntimeArguments" in launcher
    assert "& docker @composeRuntimeArguments down" in launcher
    assert '$ErrorActionPreference = "Continue"' in launcher
    assert "chcp 65001 >nul" in command_launcher
    assert "*.cmd text eol=crlf" in attributes


def test_root_compose_is_unambiguously_a_loopback_test_fixture() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "x-woozoo-boundary: local-test-only" in compose
    assert "POSTGRES_HOST_AUTH_METHOD: trust" in compose
    assert "127.0.0.1:5433:5432" in compose
    assert "phase8-authenticated-runtime" not in compose


def test_fresh_postgres_e2e_requires_scram_secret_file_authentication() -> None:
    compose = (ROOT / "tests/e2e/compose.yaml").read_text(encoding="utf-8")

    assert "POSTGRES_HOST_AUTH_METHOD: trust" not in compose
    assert "POSTGRES_PASSWORD_FILE: /run/secrets/postgres_superuser_password" in compose
    assert "POSTGRES_INITDB_ARGS: --auth-host=scram-sha-256" in compose
    assert "E2E_POSTGRES_SUPERUSER_PASSWORD_FILE" in compose
