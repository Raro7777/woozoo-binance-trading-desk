FROM python:3.13-slim@sha256:6771159cd4fa5d9bba1258caf0b82e6b73458c694d178ad97c5e925c2d0e1a91

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/woozoo/.venv/bin:$PATH \
    PYTHONPATH=/opt/woozoo/services/testnet-execution-service/src

WORKDIR /opt/woozoo
COPY pyproject.toml uv.lock ./
RUN python -m pip install --no-cache-dir uv==0.11.29 \
    && uv sync --frozen --no-dev --no-install-project
COPY infra/runtime ./infra/runtime
COPY services/testnet-execution-service ./services/testnet-execution-service
RUN useradd --create-home --uid 10001 woozoo
USER 10001:10001
ENTRYPOINT ["python", "infra/runtime/phase8-entrypoint.py"]
