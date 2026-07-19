# Phase 1 Compose boundary

The root `compose.yaml` is the single local entrypoint. It pins Postgres and Redis by digest and
binds both ports to loopback only. The containers use a dedicated bridge so host-run FastAPI tests
can reach the local services; the P1 network policy admits no configured external service, and no
container has an exchange, model-provider, or browser credential path.
