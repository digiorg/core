# Jaeger Distributed Tracing

## What is Jaeger?

Jaeger is an open-source, end-to-end distributed tracing platform. It helps monitor and troubleshoot transactions in complex distributed systems by tracking requests as they flow through multiple services. Jaeger is a CNCF graduated project and provides native support for OpenTelemetry (OTLP).

In the DigiOrg Core Platform, Jaeger completes the **three pillars of observability**:
- **Metrics** — Prometheus/Grafana (already deployed)
- **Logs** — planned
- **Traces** — Jaeger (this component)

## Architecture

This deployment uses **Jaeger v2** (chart 3.x) with **OpenSearch** as persistent trace storage and **oauth2-proxy** for Keycloak SSO.

```
Application (OTLP) ──gRPC:4317 / HTTP:4318──► Jaeger (single binary)
                                                      │                  │
                                               OpenSearch           Prometheus
                                              (platform-db)       :8888/metrics

Browser ──► NGINX /jaeger ──► oauth2-proxy:4180 ──► Keycloak OIDC
                                      │ (authenticated)
                                      ► Jaeger UI :16686/jaeger
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
| `jaeger_storage.primary_store` | `elasticsearch` | OpenSearch backend (platform-db) |
| OTLP gRPC endpoint | `0.0.0.0:4317` | Trace ingestion |
| OTLP HTTP endpoint | `0.0.0.0:4318` | Trace ingestion |
| Prometheus metrics | `0.0.0.0:8888` | Scraped by ServiceMonitor |
| `ingress.enabled` | `false` | Using unified platform ingress |
| `provisionDataStore.cassandra` | `false` | Sub-chart disabled — OpenSearch used instead |

> **Note:** The old Jaeger v1 Helm schema (`allInOne:`, `collector:`, `query:`, `storage.type:`)
> does not exist in chart 3.x and will be silently ignored. Always use `jaeger:` and `userconfig:`.

## Instrumentation

Services send traces to Jaeger using the OpenTelemetry SDK:

```
OTLP gRPC: jaeger-query.tracing.svc.cluster.local:4317
OTLP HTTP: jaeger-query.tracing.svc.cluster.local:4318
```

## Keycloak SSO

Access to the Jaeger UI is protected by [oauth2-proxy](https://oauth2-proxy.github.io/oauth2-proxy/) and Keycloak OIDC:

| Component | Detail |
|-----------|--------|
| oauth2-proxy Deployment | `jaeger-oauth2-proxy` in `tracing` namespace |
| Keycloak Client | `jaeger` in realm `digiorg-core-platform` |
| Callback URL | `https://digiorg.local/jaeger/oauth2/callback` |
| Cookie scope | `/jaeger` |
| Secret | `jaeger-oauth2-proxy-secrets` in `tracing` namespace |

The secret is created by `scripts/local-setup.nu` and contains:
- `client-secret`: Keycloak OIDC client secret (default: `jaeger-client-secret`)
- `cookie-secret`: 32-byte base64 cookie encryption key

## Production Considerations

1. **Authentication**: Rotate the `cookie-secret` and use a strong `client-secret` from a secrets manager.
2. **TLS**: Remove `--ssl-insecure-skip-verify` from oauth2-proxy and configure proper CA trust.
3. **Sampling**: Configure adaptive sampling strategies in the OTEL Collector pipeline.
4. **Resource limits**: Increase CPU/memory limits and add HPA based on observed usage.
5. **Retention**: Configure index TTL in OpenSearch to match your retention policy (e.g. 7 days).
6. **Multi-tenancy**: Enable Jaeger multi-tenancy for separate trace namespaces per team.
