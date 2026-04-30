# ADR-005: Tracing Backend — Jaeger

**Status:** Accepted  
**Date:** 2026-04-30  
**Deciders:** @simon-itstudio  

## Context

The DigiOrg Core Platform has two of the three observability pillars in place:
- **Metrics**: Prometheus + Grafana (kube-prometheus-stack)
- **Logs**: planned

To complete the observability story, we need a **distributed tracing** backend that can:
- Receive traces from services instrumented with OpenTelemetry
- Provide a UI to explore traces and identify latency bottlenecks
- Integrate as a data source in Grafana
- Be deployable locally (KinD) with minimal resource overhead
- Be production-ready with a persistent storage backend path

## Decision

We adopt **Jaeger v2** (Helm chart `jaeger` from `jaegertracing.github.io/helm-charts`) as the distributed tracing backend.

For local development we deploy in **all-in-one** mode with in-memory storage. For production, OpenSearch will be used as the storage backend with separate collector and query deployments.

## Rationale

### Why Jaeger over alternatives

| Criterion | Jaeger | Grafana Tempo | Zipkin |
|-----------|--------|---------------|--------|
| CNCF status | Graduated | Sandbox | — |
| Native OTLP | Yes (v2) | Yes | Partial |
| Grafana datasource | Yes | Yes (native) | Yes |
| All-in-one mode | Yes | No | Yes |
| Storage options | Memory, OpenSearch, Cassandra | Object storage (S3/GCS) | In-memory, Elasticsearch |
| License | Apache 2.0 | AGPL (Grafana OSS) | Apache 2.0 |
| UI | Built-in | Grafana only | Built-in |
| Kubernetes-native | Yes | Yes | No |

**Jaeger** was chosen because:

1. **CNCF Graduated**: Battle-tested at scale, strong community and long-term support commitment.
2. **Native OTLP support**: Jaeger v2 accepts OTLP natively on ports 4317 (gRPC) and 4318 (HTTP) — no translation layer needed.
3. **Grafana datasource**: First-class Grafana integration for correlating traces with metrics.
4. **All-in-one mode**: Single container for local dev keeps resource usage low and setup simple.
5. **Apache 2.0 license**: No AGPL restrictions; compatible with our licensing requirements.
6. **OpenSearch storage path**: Integrates with OpenSearch (already on the platform roadmap) for production persistence.

**Grafana Tempo** was considered but rejected:
- Requires object storage (S3/GCS) even for local dev — adds operational complexity.
- No standalone UI; depends entirely on Grafana, making standalone debugging harder.
- Sandbox status at time of decision.

**Zipkin** was considered but rejected:
- Not CNCF; smaller community.
- Incomplete native OTLP support requires an extra OpenTelemetry Collector layer.
- No Kubernetes-native deployment story.

## Consequences

### Positive

- Completes the three observability pillars on the platform.
- OTLP endpoint available to all services at `jaeger-query.tracing.svc.cluster.local:4317` (gRPC) and `:4318` (HTTP).
- Jaeger UI accessible at `https://digiorg.local/jaeger` without additional ingress configuration overhead.
- Prometheus scraping via ServiceMonitor on the admin port (`/metrics`).
- Trace correlation in Grafana via the Jaeger datasource.

### Negative

- In-memory storage means all trace data is lost on pod restart in local dev.
- No authentication on the Jaeger UI — acceptable for local dev, must be addressed before production exposure.
- Additional resource consumption (~128Mi RAM minimum).

### Migration path to production

1. Provision OpenSearch cluster (or reuse existing if available).
2. Update `storage.type: opensearch` and add connection config to values.yaml.
3. Disable `allInOne`, enable separate `collector` and `query` deployments.
4. Add OAuth2 proxy (Keycloak) in front of the query service.
5. Configure adaptive sampling in the collector.

## References

- [Jaeger Documentation](https://www.jaegertracing.io/docs/)
- [Jaeger Helm Chart](https://github.com/jaegertracing/helm-charts)
- [OpenTelemetry OTLP Specification](https://opentelemetry.io/docs/specs/otlp/)
- [Grafana Jaeger Datasource](https://grafana.com/docs/grafana/latest/datasources/jaeger/)
- [CNCF Jaeger Project](https://www.cncf.io/projects/jaeger/)
