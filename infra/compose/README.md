# Phase 1 Compose boundary

The root `compose.yaml` is the single local entrypoint. It pins Postgres and Redis by digest and
binds both ports to loopback only. The containers use a dedicated bridge so host-run FastAPI tests
can reach the local services; the P1 network policy admits no configured external service, and no
container has an exchange, model-provider, or browser credential path.

The root entrypoint is marked `x-woozoo-boundary: local-test-only`; its loopback-only `trust`
database is not a Phase 8 deployment boundary.

## Phase 8 authenticated runtime

`compose.phase8.yaml` is the separate Phase 8 runtime profile. Before running it, copy
`.env.example` to a local ignored environment file and set each `PHASE8_*_PASSWORD_FILE` to a
different operator-owned one-line secret file. Set `LOCAL_OPERATOR_VERIFIER_FILE` to the Argon2id
verifier file. Secret values are never placed in Compose environment fields or committed files.

Start the authenticated database, migration/bootstrap job, Redis, and Control API with:

```text
docker compose --env-file .env.local -f compose.phase8.yaml up --build postgres redis phase8-bootstrap control-api
```

The execution worker is available only under the explicit `runtime` profile. The Gateway is a
separate `testnet-gateway` profile, defaults to disabled, receives its exchange credentials only as
Gateway-only Docker file secrets, and is the only service attached to `exchange-egress-network`. Postgres,
Redis, Control API, and execution worker remain on internal networks and the API publishes only a
loopback host port.

The Windows local operator path is `START_TESTNET.cmd`; it prepares ignored per-role SCRAM files,
mounts exchange secret files only into the Gateway, starts the authenticated profiles, and serves
the browser through local HTTPS. See `docs/TESTNET_QUICK_START_KO.md`.
