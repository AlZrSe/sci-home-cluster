# Observability stack

Separate Compose project for the OpenTelemetry Collector and Grafana Cloud
export. It is **not** part of the application stack in the repository-root
`docker-compose.yaml`.

## Start

```bash
cd observability
cp .env.example .env
# fill in GRAFANA_OTLP_ENDPOINT and GRAFANA_OTLP_AUTH
docker compose up -d
```

## Wire the backend to it

From the application stack, set:

```
OTEL_EXPORTER_OTLP_ENDPOINT=http://host.docker.internal:4318
```

(Docker Desktop) or the host's LAN IP on Linux. The backend's service name,
environment and deployed version are attached to every span by
`backend/core/telemetry.py`.
