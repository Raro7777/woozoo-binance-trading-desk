FROM python:3.13-slim@sha256:6771159cd4fa5d9bba1258caf0b82e6b73458c694d178ad97c5e925c2d0e1a91

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /opt/woozoo
COPY infra/runtime/phase8_loopback_proxy.py ./phase8_loopback_proxy.py
RUN useradd --create-home --uid 10001 woozoo
USER 10001:10001
ENTRYPOINT ["python", "/opt/woozoo/phase8_loopback_proxy.py"]
