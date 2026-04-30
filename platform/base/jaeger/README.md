# Jaeger Distributed Tracing

## What is Jaeger?

Jaeger is an open-source, end-to-end distributed tracing platform. It helps monitor and troubleshoot transactions in complex distributed systems by tracking requests as they flow through multiple services. Jaeger is a CNCF graduated project and provides native support for OpenTelemetry (OTLP).

In the DigiOrg Core Platform, Jaeger completes the **three pillars of observability**:
- **Metrics** — Prometheus/Grafana (already deployed)
- **Logs** — planned
- **Traces** — Jaeger (this component)

## Architecture

This deployment uses **Jaeger v2** (chart 3.x), which runs as a single binary based on the OpenTelemetry Collector framework. For local dev, in-memory storage is used.

```
Application (OTLP) ──gRPC:4317 / HTTP:4318──► Jaeger (single binary)
                                                      │
                                          ┌───────────┴───────────┐
                                    In-memory store        Jaeger UI (/jaeger)
                                                                   │
                                                          Prometheus :8888/metrics
```

## Ports

| Port  | Protocol | Purpose                              |
|-------|----------|--------------------------------------|
| 16686 | HTTP     | Jaeger UI and Query API              |
| 4317  | gRPC     | OTLP trace ingestion (gRPC)          |
| 4318  | HTTP     | OTLP trace ingestion (HTTP)          |
| 14269 | HTTP     | Health check endpoint                |
| 8888  | HTTP     | Prometheus metrics (`/metrics`)      |

## Accessing the UI

Jaeger UI is accessible at: **https://digiorg.local/jaeger**

`jaeger_query.base_path: /jaeger` (set in `values.yaml` via `userconfig:`) ensures Jaeger
serves all static assets under the `/jaeger` prefix. No NGINX URL rewriting is required.

## Configuration Overview (values.yaml)

Jaeger v2 uses the OpenTelemetry Collector YAML format for all configuration.
The relevant settings are under `userconfig:`:

| Setting | Value | Notes |
|---------|-------|-------|
| `jaeger_query.base_path` | `/jaeger` | Required for subpath deployment |
| `jaeger_storage.primary_store` | `memory` | In-memory, data lost on restart |
| OTLP gRPC endpoint | `0.0.0.0:4317` | Trace ingestion |
| OTLP HTTP endpoint | `0.0.0.0:4318` | Trace ingestion |
| Prometheus metrics | `0.0.0.0:8888` | Scraped by ServiceMonitor |
| `ingress.enabled` | `false` | Using unified platform ingress |

> **Note:** The old Jaeger v1 Helm schema (`allInOne:`, `collector:`, `query:`, `storage.type:`)
> does not exist in chart 3.x and will be silently ignored. Always use `jaeger:` and `userconfig:`.

## Instrumentation

Services send traces to Jaeger using the OpenTelemetry SDK:

```
OTLP gRPC: jaeger-query.tracing.svc.cluster.local:4317
OTLP HTTP: jaeger-query.tracing.svc.cluster.local:4318
```

## Production Considerations

For production deployments the following changes are recommended:

1. **Storage**: Replace in-memory with OpenSearch via Crossplane. Update `jaeger_storage.backends.primary_store` to use the `elasticsearch` backend with your OpenSearch connection.

2. **Authentication**: Add an OAuth2 proxy (Keycloak) in front of the Jaeger query service. The UI has no built-in authentication.

3. **Sampling**: Configure adaptive sampling strategies in the OTEL Collector pipeline.

4. **Resource limits**: Increase CPU/memory limits and add HPA based on observed usage.

5. **Retention**: Configure index TTL in OpenSearch to match your retention policy (e.g. 7 days).

6. **Multi-tenancy**: Enable Jaeger multi-tenancy for separate trace namespaces per team.
