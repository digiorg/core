# Jaeger Distributed Tracing

## What is Jaeger?

Jaeger is an open-source, end-to-end distributed tracing platform. It helps monitor and troubleshoot transactions in complex distributed systems by tracking requests as they flow through multiple services. Jaeger is a CNCF graduated project and provides native support for OpenTelemetry (OTLP).

In the DigiOrg Core Platform, Jaeger completes the **three pillars of observability**:
- **Metrics** — Prometheus/Grafana (already deployed)
- **Logs** — planned
- **Traces** — Jaeger (this component)

## Architecture

This deployment uses the **all-in-one** mode, which runs the collector, query, and UI in a single container backed by in-memory storage. This is suitable for local development.

```
Application (OTLP) ──► Jaeger all-in-one ──► In-memory storage
                              │
                              └──► Jaeger UI (/jaeger)
```

## Ports

| Port  | Protocol | Purpose                        |
|-------|----------|--------------------------------|
| 16686 | HTTP     | Jaeger UI and Query API        |
| 4317  | gRPC     | OTLP trace ingestion (gRPC)    |
| 4318  | HTTP     | OTLP trace ingestion (HTTP)    |
| 14269 | HTTP     | Admin endpoint + /metrics      |

## Accessing the UI

Jaeger UI is accessible at: **https://digiorg.local/jaeger**

The `--query.base-path=/jaeger` flag configures Jaeger to serve all assets and API calls under the `/jaeger` prefix, so no nginx URL rewriting is required.

## Configuration Overview (values.yaml)

| Setting | Value | Notes |
|---------|-------|-------|
| `allInOne.enabled` | `true` | Single-binary deployment |
| `collector.enabled` | `false` | Disabled (all-in-one handles it) |
| `query.enabled` | `false` | Disabled (all-in-one handles it) |
| `storage.type` | `memory` | In-memory, data lost on restart |
| `service.type` | `ClusterIP` | Accessed via ingress |
| `ingress.enabled` | `false` | We use the platform ingress |

## Instrumentation

Services send traces to Jaeger using the OpenTelemetry SDK:

```
OTLP gRPC: jaeger-query.tracing.svc.cluster.local:4317
OTLP HTTP: jaeger-query.tracing.svc.cluster.local:4318
```

## Production Considerations

For production deployments the following changes are recommended:

1. **Storage**: Replace in-memory with OpenSearch (or Elasticsearch/Cassandra). Set `storage.type: opensearch` and configure the connection.

2. **Separate components**: Disable `allInOne` and enable `collector` and `query` separately for independent scaling.

3. **Authentication**: Add an OAuth2 proxy (Keycloak) in front of the Jaeger query service. The UI has no built-in authentication.

4. **Sampling**: Configure adaptive sampling in the collector to control trace volume at scale.

5. **Resource limits**: Increase CPU/memory limits and add HPA based on observed usage.

6. **Retention**: Configure index TTL in OpenSearch to match your retention policy.
