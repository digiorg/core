# ADR-006: Observability Storage Backend — OpenSearch

**Status:** Accepted  
**Date:** 2026-05-01  
**Deciders:** @simon-itstudio  
**Related:** [ADR-005](005-tracing-backend-jaeger.md), [Issue #82](https://github.com/digiorg/core/issues/82), [Issue #83](https://github.com/digiorg/core/issues/83)

## Context

ADR-005 introduced Jaeger v2 as the distributed tracing backend with in-memory storage, explicitly deferring persistent storage to a later decision. The migration path in ADR-005 identified OpenSearch as the preferred production storage backend.

Additionally, the platform has only two of the three observability pillars complete:

| Pillar | Tool | Storage | Status |
|--------|------|---------|--------|
| Metrics | Prometheus + Grafana | Prometheus PVC | ✅ Deployed |
| Traces | Jaeger v2 | In-memory → **OpenSearch** | ✅ Migrated |
| Logs | planned | **OpenSearch** | 🔲 Planned |

A dedicated, persistent, scalable storage backend is needed for traces (immediately) and logs (future). Issue #82 analysed options (PostgreSQL, Cassandra, OpenSearch) and recommended OpenSearch. This ADR records that decision.

## Decision

We deploy **OpenSearch** (via the official `opensearch-project/opensearch` Helm chart) in the `platform-db` namespace as the shared observability storage backend.

- Helm Chart: `opensearch-project/opensearch` v3.6.0
- Repo: `https://helm.opensearch.org`
- Mode: single-node for local dev, 3-node cluster for production
- ArgoCD Sync Wave: 0 (data layer, before all application services)

## Rationale

### Why OpenSearch over alternatives

| Criterion | OpenSearch | Cassandra | PostgreSQL |
|-----------|-----------|-----------|------------|
| Jaeger native support | ✅ Official (1.x–3.x) | ✅ Official (4.x–5.x) | ❌ Community-only |
| Init job required | ❌ No | ✅ Schema script needed | ❌ N/A |
| Log aggregation support | ✅ Yes (future Fluent Bit) | ❌ No | ❌ No |
| Grafana datasource | ✅ Elasticsearch type | ❌ No | ✅ PostgreSQL type |
| Index TTL / rollover | ✅ ISM built-in | ✅ TTL per table | ❌ Manual |
| Full-text search | ✅ Yes | ❌ No | ⚠️ Limited |
| Kubernetes Helm chart | ✅ Official | ✅ Bitnami | ✅ Custom |
| License | ✅ Apache 2.0 | ✅ Apache 2.0 | ✅ PostgreSQL |
| Resource overhead | ⚠️ Medium (512 MB+) | ⚠️ High (1 GB+) | ✅ Low |
| ADR-005 recommendation | ✅ Explicitly preferred | ⚠️ Fallback | ❌ Rejected |

**OpenSearch** was chosen because:

1. **Official Jaeger support:** Versions 1.x, 2.x, 3.x are all supported with no additional init steps. Index creation is automatic on first trace write.
2. **Elasticsearch API compatibility:** OpenSearch exposes the ES 7.10.2 REST API — Grafana, Jaeger, Fluent Bit and other tools integrate natively without additional adapters.
3. **Observability convergence:** OpenSearch is the only option that covers both traces (now) and logs (future). Deploying it once establishes the full observability storage infrastructure.
4. **No schema maintenance:** Unlike Cassandra (CQL schema init) or PostgreSQL, OpenSearch requires no upfront schema work — indices are created dynamically.
5. **ISM for retention:** The built-in Index State Management provides automatic index rollover and deletion policies without external cron jobs.
6. **Apache 2.0 license:** Fully open-source, no AGPL or proprietary restrictions.
7. **ADR-005 alignment:** Explicitly called out as the preferred production backend in the Jaeger ADR migration path.

**Cassandra** was considered but not chosen:
- No benefit over OpenSearch for trace storage at this scale.
- Heavier resource requirements (multi-GB RAM for cluster formation).
- No log aggregation support — a second storage backend would still be needed.
- Schema init required via `cqlsh` script.

**PostgreSQL** was ruled out:
- No official Jaeger support (community gRPC adapter only, unmaintained risk).
- No suitable for full-text search or time-series log retention.

## Namespace Placement

OpenSearch is placed in the existing `platform-db` namespace alongside PostgreSQL. This is consistent with the platform principle of centralizing all persistent data services in one namespace, making backup, access control, and network policies uniform.

```
platform-db namespace
├── postgresql     ← Keycloak, Backstage, Gitea databases
└── opensearch     ← Jaeger traces, future log aggregation
```

## Consequences

### Positive

- Jaeger traces are now **persistent across pod restarts**.
- Grafana gains an Elasticsearch-compatible datasource for direct trace searching alongside the Jaeger datasource.
- Foundation is laid for the third observability pillar (logs via Fluent Bit → OpenSearch).
- Index TTL/rollover is configurable via ISM — no external cleanup jobs needed.

### Negative

- Additional resource consumption: ~512 MB RAM minimum (single-node dev), ~3 × 2 GB in production.
- `vm.max_map_count=262144` must be set on each Kubernetes node — handled via privileged `initContainer` for KinD.
- Security plugin is disabled in local dev — must be enabled and configured for production.
- KinD clusters may be slower to initialize due to the OpenSearch startup time (~30–60 s).

### Production Migration Path

1. Set `singleNode: false`, `replicas: 3` in `values.yaml`.
2. Remove `DISABLE_SECURITY_PLUGIN`, configure TLS via cert-manager and RBAC.
3. Integrate Keycloak OIDC with OpenSearch Security plugin for SSO.
4. Configure ISM policy for index rollover (e.g. 30-day retention, max 50 GB per index).
5. Deploy OpenSearch Dashboards (separate Helm chart) for direct log/trace UI with Keycloak SSO.
6. Set `vm.max_map_count=262144` via node-level DaemonSet or cloud provider node pool configuration.
7. Use a high-performance StorageClass (SSD-backed) for the PVC.

## References

- [OpenSearch Helm Charts](https://github.com/opensearch-project/helm-charts)
- [Jaeger OpenSearch Storage Docs](https://www.jaegertracing.io/docs/latest/opensearch/)
- [OpenSearch Index State Management](https://docs.opensearch.org/latest/im-plugin/ism/index/)
- [ADR-005: Tracing Backend Jaeger](005-tracing-backend-jaeger.md)
- [Issue #82: Jaeger Refactoring Analysis](https://github.com/digiorg/core/issues/82)
- [Issue #83: OpenSearch Feature](https://github.com/digiorg/core/issues/83)
