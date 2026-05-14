# Jaeger Distributed Tracing

## What is Jaeger?

Jaeger is an open-source, end-to-end distributed tracing platform. It helps monitor and troubleshoot transactions in complex distributed systems by tracking requests as they flow through multiple services. Jaeger is a CNCF graduated project and provides native support for OpenTelemetry (OTLP).

In the DigiOrg Core Platform, Jaeger completes the **three pillars of observability**:
- **Metrics** — Prometheus/Grafana (already deployed)
- **Logs** — planned _(See issue #TBD for tracking progress on the logging component.)_
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

## Data Retention

Jaeger v2 (chart 3.x) ships an `esIndexCleaner` CronJob that deletes OpenSearch indices older than a configurable number of days. Enable and tune it in `values.yaml`:

```yaml
esIndexCleaner:
  enabled: true
  numberOfDays: 7
  schedule: "55 23 * * *"
```

The job runs daily at 23:55 and removes any Jaeger index whose age exceeds `numberOfDays`. Adjust the schedule and retention window to match your SLA and storage budget.

For more advanced rules (e.g. per-index size caps, rollover policies) you can instead define an [OpenSearch ISM policy](https://opensearch.org/docs/latest/im-plugin/ism/index/) targeting the `jaeger-*` index pattern. ISM policies and `esIndexCleaner` can coexist — use ISM for rollover and `esIndexCleaner` for final deletion.

## Instrumentation

Services send traces to Jaeger using the OpenTelemetry SDK:

```
OTLP gRPC: jaeger-query.tracing.svc.cluster.local:4317
OTLP HTTP: jaeger-query.tracing.svc.cluster.local:4318
```

### Kubernetes Deployment Example

Add the following environment variables to any Kubernetes `Deployment` to enable OTLP trace export over gRPC:

```yaml
env:
  - name: OTEL_EXPORTER_OTLP_ENDPOINT
    value: "http://jaeger-query.tracing.svc.cluster.local:4317"
  - name: OTEL_SERVICE_NAME
    value: "my-service"
  - name: OTEL_TRACES_EXPORTER
    value: "otlp"
  - name: OTEL_PROPAGATORS
    value: "tracecontext,baggage"
```

Replace `my-service` with the logical name of your application. The SDK auto-detects the gRPC protocol from the `http://` scheme on port `4317`; use port `4318` with `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf` if you prefer HTTP.

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

### Secret Management

Generate a cryptographically secure `cookie-secret`:

```bash
openssl rand -base64 32
```

To update or rotate the secret in-place without downtime, use the `--dry-run=client` pattern to preview the change before applying it:

```bash
kubectl create secret generic jaeger-oauth2-proxy-secrets \
  --namespace tracing \
  --from-literal=client-secret='<new-client-secret>' \
  --from-literal=cookie-secret="$(openssl rand -base64 32)" \
  --dry-run=client -o yaml | kubectl apply -f -
```

After applying, restart the oauth2-proxy deployment to pick up the new values:

```bash
kubectl rollout restart deployment/jaeger-oauth2-proxy -n tracing
```

> **Production note:** Do not store secrets in `values.yaml` or Git. Inject them at deploy time from a secrets manager such as [HashiCorp Vault](https://developer.hashicorp.com/vault) (via the Vault Agent Injector or the Secrets Store CSI driver) or [Sealed Secrets](https://github.com/bitnami-labs/sealed-secrets).

## Production Considerations

1. **Authentication**: Rotate the `cookie-secret` and use a strong `client-secret` from a secrets manager.
2. **TLS**: Remove `--ssl-insecure-skip-verify` from oauth2-proxy and configure proper CA trust.
3. **Sampling**: Configure adaptive sampling strategies in the OTEL Collector pipeline.
4. **Resource limits**: Increase CPU/memory limits and add HPA based on observed usage.
5. **Retention**: Configure index TTL in OpenSearch to match your retention policy (e.g. 7 days).
6. **Multi-tenancy**: Jaeger multi-tenancy allows a single Jaeger instance to serve multiple isolated tenants, each with their own trace namespace and access controls. This is useful when multiple teams or environments share the same Jaeger deployment. See the [Jaeger multi-tenancy documentation](https://www.jaegertracing.io/docs/latest/multi-tenancy/) for configuration details.
