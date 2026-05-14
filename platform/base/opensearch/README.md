# OpenSearch — Observability Data Backend

## What is OpenSearch?

OpenSearch is an open-source, Apache 2.0-licensed search and analytics engine forked from Elasticsearch 7.10.2. It serves as the **persistent observability storage backend** for the DigiOrg Core Platform.

## Files

| File | Purpose |
|------|---------|
| `values.yaml` | OpenSearch Helm Chart values (cluster identity, JVM heap, auth, storage, resource limits). Chart: `opensearch-project/opensearch` v3.6.0, Repo: https://helm.opensearch.org |
| `servicemonitor.yaml` | Prometheus Operator ServiceMonitor scraping OpenSearch metrics at `/_prometheus/metrics` (port 9200, interval 30s) |
| `kustomization.yaml` | Kustomize entrypoint — manages only supplementary resources (ServiceMonitor). OpenSearch itself is deployed via Helm by the ArgoCD Application (`apps/platform/opensearch.yaml`). Running `kubectl apply -k platform/base/opensearch/` deploys **only the ServiceMonitor**, not OpenSearch itself. |

## Role in the Platform

```
┌──────────────────────────────────────────────────────────────────────┐
│                   Observability Stack                                │
│                                                                      │
│  Services (OTLP)                                                     │
│      │                                                               │
│      ▼ gRPC:4317 / HTTP:4318                                         │
│  ┌──────────┐   writes traces   ┌─────────────────────────────────┐  │
│  │  Jaeger  │ ────────────────► │  OpenSearch                     │  │
│  │  (tracing│                   │  platform-db namespace          │  │
│  │   ns)    │ ◄──── queries ─── │  opensearch-cluster-master:9200 │  │
│  └──────────┘                   └─────────────────────────────────┘  │
│       │                                      │                       │
│       ▼                                      ▼                       │
│  Jaeger UI (/jaeger)              Grafana Elasticsearch datasource   │
└──────────────────────────────────────────────────────────────────────┘
```

| Observability Pillar | Tool | Storage |
|---------------------|------|---------|
| **Metrics** | Prometheus + Grafana | In-cluster (Prometheus PVC) |
| **Traces** | Jaeger v2 | **OpenSearch** (this component) |
| **Logs** | planned | **OpenSearch** (future) |

## Architecture

This deployment uses the **official OpenSearch Helm chart** (`opensearch-project/opensearch` v3.6.0, repo: https://helm.opensearch.org) in single-node mode for local development.

> **Note:** Running `kubectl apply -k platform/base/opensearch/` deploys **only the ServiceMonitor** — not OpenSearch itself. OpenSearch is deployed by the ArgoCD Application at `apps/platform/opensearch.yaml` via Helm.

```
platform-db namespace
├── postgresql (StatefulSet)   ← Keycloak, Backstage, Gitea databases
└── opensearch (StatefulSet)   ← Jaeger traces, future logs
    └── Service: opensearch-cluster-master:9200 (ClusterIP)
```

## Deployment Parameters

| Parameter | Local Dev | Production |
|-----------|-----------|------------|
| `singleNode` | `true` | `false` |
| Replicas | 1 | 3 |
| Heap | `-Xmx512M` | `-Xmx2G` or higher |
| Storage | 8Gi | 100Gi+ |
| Security Plugin | disabled | enabled (TLS + RBAC) |

## Ports

| Port | Protocol | Purpose |
|------|----------|---------|
| 9200 | HTTP | REST API (Jaeger, Grafana, admin) |
| 9300 | TCP | Cluster transport (inter-node) |
| 9600 | HTTP | Performance Analyzer metrics |

## Accessing OpenSearch

From within the cluster:

```bash
# REST API
http://opensearch-cluster-master.platform-db.svc.cluster.local:9200

# Health check
curl http://opensearch-cluster-master.platform-db.svc.cluster.local:9200/_cluster/health

# List indices (Jaeger creates these automatically)
curl http://opensearch-cluster-master.platform-db.svc.cluster.local:9200/_cat/indices?v
```

## Configuration Overview (values.yaml)

| Setting | Value | Notes |
|---------|-------|-------|
| `clusterName` | `opensearch-cluster` | Cluster identity |
| `singleNode` | `true` | Local dev; set `false` in production |
| `opensearchJavaOpts` | `-Xmx512M -Xms512M` | JVM heap |
| `DISABLE_SECURITY_PLUGIN` | `true` | Local dev only — remove in production |
| Storage | 8Gi PVC | Default provisioner (standard on KinD) |
| `vm.max_map_count` | 262144 | Set at KinD node level via `docker exec` in `local-setup.nu` |
| CPU | 250m request / 1000m limit | Kubernetes resource limits |
| Memory | 512Mi request / 1Gi limit | Kubernetes resource limits |
| `rbac.create` | `false` | No ServiceAccount or RBAC resources created; required if enabling the Security Plugin in production |
| `discovery.type` | `single-node` | Set in `opensearch.yml`; suppresses cluster bootstrap checks — the actual mechanism behind `singleNode: true` |

## Jaeger Integration

Jaeger connects to OpenSearch via the `elasticsearch` backend type (OpenSearch is API-compatible with ES 7.10.2):

```
OTLP endpoint:  jaeger-query.tracing.svc.cluster.local:4317 (gRPC)
Storage write:  opensearch-cluster-master.platform-db.svc.cluster.local:9200
Index pattern:  jaeger-span-YYYY-MM-DD  (daily rotation)
```

Jaeger creates indices automatically on first trace ingest — no schema initialization needed.

## Grafana Integration

OpenSearch is available as a Grafana datasource using the built-in Elasticsearch datasource type:

- **Name:** OpenSearch (Traces)
- **URL:** `http://opensearch-cluster-master.platform-db.svc.cluster.local:9200`
- **Index:** `jaeger-span-*`
- **Time field:** `startTimeMillis`

## Secrets

| Namespace | Secret | Key | Used By |
|-----------|--------|-----|---------|
| `platform-db` | `opensearch-secrets` | `OPENSEARCH_ADMIN_PASSWORD` | OpenSearch admin bootstrap |

Secret is created by `scripts/local-setup.nu` before ArgoCD sync.

## ArgoCD Sync Wave

OpenSearch is deployed in **Wave 0** — same wave as PostgreSQL — to ensure it is available before Jaeger (Wave 2) starts.

| Wave | Services |
|------|---------|
| 0 | cert-manager, postgresql, nats, **opensearch** |
| 2 | jaeger (connects to opensearch), grafana, backstage, landingpage |

## Monitoring

### Prometheus Monitoring

A Prometheus Operator **ServiceMonitor** (`opensearch`, namespace `platform-db`) is deployed alongside OpenSearch to expose cluster metrics to Prometheus.

| Property | Value |
|----------|-------|
| ServiceMonitor name | `opensearch` |
| Namespace | `platform-db` |
| Scrape endpoint | `/_prometheus/metrics` on port `9200` (HTTP) |
| Scrape interval | `30s` |
| Required label | `release: prometheus` |

The `release: prometheus` label on the ServiceMonitor must match the kube-prometheus-stack Helm release name for the Prometheus Operator to discover it — this follows the same pattern used by all other ServiceMonitors in the platform.

**Verify the ServiceMonitor is deployed:**

```bash
kubectl get servicemonitor -n platform-db
```

**Verify Prometheus has picked up the target** (port-forward Prometheus and open in browser):

```bash
kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-prometheus 9090:9090
# Then open http://localhost:9090/targets and search for opensearch
```

**Quick scrape test from within the cluster:**

```bash
kubectl exec -n platform-db opensearch-cluster-master-0 -- \
  curl -s http://localhost:9200/_prometheus/metrics | head -20
```

**Example PromQL query to confirm scraping is active:**

```
opensearch_fs_total_total_in_bytes
```

## Production Considerations

1. **Enable Security Plugin:** Remove `DISABLE_SECURITY_PLUGIN`, configure TLS certificates and RBAC.
2. **Scale out:** Set `singleNode: false`, `replicas: 3` for HA.
3. **Heap sizing:** Increase to `-Xmx2G` or higher based on trace ingest volume.
4. **Index lifecycle:** Configure ISM (Index State Management) for automatic index rollover and deletion (e.g. 30-day retention).
5. **Keycloak OIDC:** Enable OpenSearch Dashboards with Keycloak SSO for direct log/trace search UI.
6. **Persistent volume:** Use a high-performance storage class (SSD-backed).
7. **vm.max_map_count:** On non-KinD deployments, ensure `vm.max_map_count >= 262144` is set at the host level. Options:
   - **DaemonSet:** Run a privileged init DaemonSet that sets the sysctl on each node.
   - **Node tuning operator:** Use the OpenShift Node Tuning Operator or equivalent.
   - **sysctl.d:** Add `vm.max_map_count=262144` to `/etc/sysctl.d/99-opensearch.conf` on each node.
   - See: [OpenSearch Important Settings](https://docs.opensearch.org/latest/install-and-configure/install-opensearch/index/#important-settings)

## Troubleshooting

```bash
# Pod status
kubectl get pods -n platform-db -l app.kubernetes.io/name=opensearch

# Logs
kubectl logs -n platform-db opensearch-cluster-master-0

# Cluster health
kubectl exec -n platform-db opensearch-cluster-master-0 -- \
  curl -s http://localhost:9200/_cluster/health | jq .

# List Jaeger indices
kubectl exec -n platform-db opensearch-cluster-master-0 -- \
  curl -s 'http://localhost:9200/_cat/indices/jaeger-*?v'
```
