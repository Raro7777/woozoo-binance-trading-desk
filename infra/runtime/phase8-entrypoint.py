"""Secret-file adapter for the authenticated Phase 8 container boundary."""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import socket
import subprocess
import sys
from urllib.parse import quote

import psycopg
from psycopg import sql


ROLE_PASSWORD_FILES = {
    "woozoo_control_reader": "PHASE8_CONTROL_READER_DATABASE_PASSWORD_FILE",
    "woozoo_control_api": "PHASE8_CONTROL_API_DATABASE_PASSWORD_FILE",
    "woozoo_market_writer": "PHASE8_MARKET_WRITER_DATABASE_PASSWORD_FILE",
    "woozoo_evidence_writer": "PHASE8_EVIDENCE_WRITER_DATABASE_PASSWORD_FILE",
    "woozoo_agent_orchestrator": "PHASE8_AGENT_ORCHESTRATOR_DATABASE_PASSWORD_FILE",
    "woozoo_risk_engine": "PHASE8_RISK_ENGINE_DATABASE_PASSWORD_FILE",
    "woozoo_paper_engine": "PHASE8_PAPER_ENGINE_DATABASE_PASSWORD_FILE",
    "woozoo_testnet_execution": "PHASE8_TESTNET_EXECUTION_DATABASE_PASSWORD_FILE",
    "woozoo_spot_testnet_gateway": "PHASE8_SPOT_TESTNET_GATEWAY_DATABASE_PASSWORD_FILE",
}
RUNTIME_UID = 10001
RUNTIME_GID = 10001
GATEWAY_SECRET_DIRECTORY = Path("/run/woozoo-secrets")


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def _secret(path_variable: str) -> str:
    path = Path(_required(path_variable))
    value = path.read_text(encoding="utf-8").strip()
    if not value or "\n" in value or "\r" in value:
        raise SystemExit(f"{path_variable} must reference one non-empty line")
    return value


def _database_url(role: str, password_variable: str) -> str:
    host = _required("PHASE8_POSTGRES_HOST")
    port = os.environ.get("PHASE8_POSTGRES_PORT", "5432").strip()
    database = os.environ.get("PHASE8_POSTGRES_DATABASE", "woozoo").strip()
    password = quote(_secret(password_variable), safe="")
    return f"postgresql://{role}:{password}@{host}:{port}/{database}?sslmode=disable"


def _trusted_control_proxy_ip() -> str:
    host = _required("PHASE8_CONTROL_API_TRUSTED_PROXY_HOST")
    try:
        resolved = {
            str(address[4][0])
            for address in socket.getaddrinfo(
                host,
                0,
                family=socket.AF_INET,
                type=socket.SOCK_STREAM,
            )
        }
    except socket.gaierror as error:
        raise SystemExit("trusted proxy address resolution failed") from error
    if len(resolved) != 1:
        raise SystemExit("trusted proxy must resolve to exactly one private IPv4 address")
    proxy_ip = ipaddress.ip_address(next(iter(resolved)))
    if (
        not isinstance(proxy_ip, ipaddress.IPv4Address)
        or not proxy_ip.is_private
        or proxy_ip.is_loopback
        or proxy_ip.is_link_local
        or proxy_ip.is_unspecified
    ):
        raise SystemExit("trusted proxy must resolve to exactly one private IPv4 address")
    return str(proxy_ip)


def _stage_gateway_secret(source_variable: str, target_name: str) -> str:
    """Copy a root-owned Docker secret into a strict non-root 0400 mount."""

    source = Path(_required(source_variable))
    if not source.is_absolute() or source.is_symlink() or not source.is_file():
        raise SystemExit(f"{source_variable} must reference a regular absolute file")
    raw = source.read_bytes()
    if not raw or len(raw) > 4096 or b"\x00" in raw:
        raise SystemExit(f"{source_variable} references an invalid secret")
    GATEWAY_SECRET_DIRECTORY.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(GATEWAY_SECRET_DIRECTORY, RUNTIME_UID, RUNTIME_GID)
    GATEWAY_SECRET_DIRECTORY.chmod(0o700)
    target = GATEWAY_SECRET_DIRECTORY / target_name
    temporary = GATEWAY_SECRET_DIRECTORY / f".{target_name}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temporary, RUNTIME_UID, RUNTIME_GID)
        temporary.chmod(0o400)
        os.replace(temporary, target)
    finally:
        raw = b""
        if temporary.exists():
            temporary.unlink()
    return str(target)


def _drop_gateway_privileges() -> None:
    if os.geteuid() != 0:
        raise SystemExit("Gateway secret staging requires an initial root entrypoint")
    os.setgroups([])
    os.setgid(RUNTIME_GID)
    os.setuid(RUNTIME_UID)
    if os.geteuid() != RUNTIME_UID or os.getegid() != RUNTIME_GID:
        raise SystemExit("Gateway privilege drop failed")


def bootstrap() -> int:
    admin_url = _database_url("postgres", "PHASE8_POSTGRES_SUPERUSER_PASSWORD_FILE")
    migration_environment = {**os.environ, "TRADING_MODE": "paper", "DATABASE_URL": admin_url}
    subprocess.run(
        ["alembic", "upgrade", "head"],
        check=True,
        env=migration_environment,
    )
    with psycopg.connect(admin_url) as connection:
        for role, variable in ROLE_PASSWORD_FILES.items():
            connection.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(_secret(variable))
                )
            )
    return 0


def run_service(service: str) -> int:
    environment = dict(os.environ)
    environment["TRADING_MODE"] = "paper"
    if service == "control-api":
        environment["DATABASE_URL"] = _database_url(
            "woozoo_control_reader", "PHASE8_CONTROL_READER_DATABASE_PASSWORD_FILE"
        )
        environment["CONTROL_DATABASE_URL"] = _database_url(
            "woozoo_control_api", "PHASE8_CONTROL_API_DATABASE_PASSWORD_FILE"
        )
        environment["AGENT_DATABASE_URL"] = _database_url(
            "woozoo_agent_orchestrator",
            "PHASE8_AGENT_ORCHESTRATOR_DATABASE_PASSWORD_FILE",
        )
        environment["RISK_DATABASE_URL"] = _database_url(
            "woozoo_risk_engine", "PHASE8_RISK_ENGINE_DATABASE_PASSWORD_FILE"
        )
        environment["PAPER_DATABASE_URL"] = _database_url(
            "woozoo_paper_engine", "PHASE8_PAPER_ENGINE_DATABASE_PASSWORD_FILE"
        )
        command = [
            "uvicorn",
            "control_api.app:create_app",
            "--factory",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
            "--proxy-headers",
            "--forwarded-allow-ips",
            _trusted_control_proxy_ip(),
            "--no-access-log",
        ]
    elif service == "testnet-execution":
        environment["TESTNET_EXECUTION_DATABASE_URL"] = _database_url(
            "woozoo_testnet_execution",
            "PHASE8_TESTNET_EXECUTION_DATABASE_PASSWORD_FILE",
        )
        command = ["python", "-m", "testnet_execution.worker"]
    elif service == "spot-testnet-gateway":
        environment["SPOT_TESTNET_GATEWAY_DATABASE_URL"] = _database_url(
            "woozoo_spot_testnet_gateway",
            "PHASE8_SPOT_TESTNET_GATEWAY_DATABASE_PASSWORD_FILE",
        )
        environment["SPOT_TESTNET_API_KEY_FILE"] = _stage_gateway_secret(
            "SPOT_TESTNET_API_KEY_FILE", "spot_testnet_api_key"
        )
        environment["SPOT_TESTNET_SIGNING_SECRET_FILE"] = _stage_gateway_secret(
            "SPOT_TESTNET_SIGNING_SECRET_FILE", "spot_testnet_signing_secret"
        )
        _drop_gateway_privileges()
        command = ["python", "-m", "spot_testnet_gateway.worker"]
    else:
        raise SystemExit("unknown Phase 8 service")
    os.execvpe(command[0], command, environment)
    return 1


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("expected bootstrap or one Phase 8 service name")
    return bootstrap() if sys.argv[1] == "bootstrap" else run_service(sys.argv[1])


if __name__ == "__main__":
    raise SystemExit(main())
