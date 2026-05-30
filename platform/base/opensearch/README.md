# OpenSearch — Observability Data Backend

## What is OpenSearch?

OpenSearch is an open-source, Apache 2.0-licensed search and analytics engine forked from Elasticsearch 7.10.2. It serves as the **persistent observability storage backend** for the DigiOrg Core Platform.

## Files

| File | Purpose |
|------|---------|
| `values.yaml` | OpenSearch Helm Chart values (cluster identity, JVM heap, auth, storage, resource limits). Chart: `opensearch-project/opensearch` v3.6.0, Repo: https://helm.opensearch.org |
| `servicemonitor.yaml` | Prometheus Operator ServiceMonitor scraping OpenSearch metrics at `/_prometheus/metrics` (port 9200, interval 30s) |
| `ism-retention-job.yaml` | ArgoCD PostSync Job that bootstraps the `digiorg-logs-retention-7d` ISM policy for 7-day Fluentd log retention. |
| `index-template-job.yaml` | ArgoCD PostSync Job that bootstraps the `digiorg-logs-template` composable index template, mapping `kubernetes.labels` and `kubernetes.namespace_labels` as `flat_object` to prevent dotted-key mapping conflicts. |
| `kustomization.yaml` | Kustomize entrypoint — manages supplementary resources (ISM bootstrap job, index template bootstrap job). OpenSearch itself is deployed via Helm by the ArgoCD Application (`apps/platform/opensearch.yaml`). Running `kubectl apply -k platform/base/opensearch/` deploys **only supplementary resources**, not OpenSearch itself. |

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
| **Logs** | Fluentd → OpenSearch | **OpenSearch** (this component, `digiorg-logs-*`) |

## Architecture

This deployment uses the **official OpenSearch Helm chart** (`opensearch-project/opensearch` v3.6.0, repo: https://helm.opensearch.org) in single-node mode for local development.

> **Note:** Running `kubectl apply -k platform/base/opensearch/` deploys **only the ServiceMonitor** — not OpenSearch itself. OpenSearch is deployed by the ArgoCD Application at `apps/platform/opensearch.yaml` via Helm.

```
platform-db namespace
├── postgresql (StatefulSet)   ← Keycloak, Backstage, Gitea databases
└── opensearch (StatefulSet)   ← Jaeger traces + Fluentd logs
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

## Fluentd Log Storage and Retention

Fluentd writes daily log indices using the `logstash_format` convention with prefix `digiorg-logs`:

```
Index pattern:  digiorg-logs-YYYY.MM.DD  (daily rotation)
Example:        digiorg-logs-2026.05.27
```

### ISM Retention Policy — `digiorg-logs-retention-7d`

An OpenSearch ISM (Index State Management) policy is bootstrapped automatically by the `opensearch-ism-retention-bootstrap` ArgoCD PostSync Job defined in `ism-retention-job.yaml`.

**Policy behaviour:**

| Setting | Value |
|---------|-------|
| Policy name | `digiorg-logs-retention-7d` |
| Index pattern | `digiorg-logs-*` |
| Retention | 7 days (`min_index_age: 7d`) |
| Action after 7 days | delete index |
| ISM template priority | 100 |

The policy uses an embedded `ism_template` so any new `digiorg-logs-*` index is automatically enrolled at creation time — no manual policy attachment is needed.

**States:**

```
hot  ──(min_index_age: 7d)──►  delete
```

**Bootstrap job behaviour:**

- Runs as an ArgoCD `PostSync` hook after the opensearch application syncs.
- Idempotent: checks whether the policy already exists; skips creation if so.
- Fails fast if OpenSearch is unreachable so ArgoCD reports degraded status rather than silently continuing.
- Delete policy: `BeforeHookCreation,HookSucceeded` — cleans up the Job pod after success; removes any stale failed Job before re-creating on the next sync.

**Local/dev endpoint assumption:**

The job targets:
```
http://opensearch-cluster-master.platform-db.svc.cluster.local:9200
```

This is the ClusterIP Service name for the single-node OpenSearch deployed in `platform-db` with the security plugin **disabled** (`DISABLE_SECURITY_PLUGIN=true`). No TLS or credentials are required.

> **Production note:** In production the security plugin must be enabled. The bootstrap job must be updated to use HTTPS, provide credentials (via a Kubernetes Secret), and follow least-privilege RBAC — see [Production Considerations](#production-considerations).

**Verify the policy was applied:**

```bash
# Check the ISM policy
kubectl exec -n platform-db opensearch-cluster-master-0 -- \
  curl -s 'http://localhost:9200/_plugins/_ism/policies/digiorg-logs-retention-7d' | python3 -m json.tool

# List Fluentd log indices with their ages
kubectl exec -n platform-db opensearch-cluster-master-0 -- \
  curl -s 'http://localhost:9200/_cat/indices/digiorg-logs-*?v&h=index,creation.date.string,store.size'

# Check ISM policy enforcement on a specific index
kubectl exec -n platform-db opensearch-cluster-master-0 -- \
  curl -s 'http://localhost:9200/_plugins/_ism/explain/digiorg-logs-*'
```

### Index Template — `digiorg-logs-template`

An OpenSearch composable index template (`_index_template` API) is bootstrapped automatically by the `opensearch-index-template-bootstrap` ArgoCD PostSync Job defined in `index-template-job.yaml`.

This template solves a mapping conflict caused by Kubernetes label keys containing dots (e.g. `app.kubernetes.io/component`). OpenSearch interprets dotted field names as nested object paths, which conflicts with single-segment keys like `app` that also exist in the labels map.

**Template behaviour:**

| Setting | Value |
|---------|-------|
| Template name | `digiorg-logs-template` |
| Index pattern | `digiorg-logs-*` |
| Priority | 200 (higher than ISM template at 100) |
| `kubernetes.labels` mapping | `flat_object` — stores the entire labels map as a single opaque object; no per-key field mapping conflicts |
| `kubernetes.namespace_labels` mapping | `flat_object` — same rationale |
| Other `kubernetes.*` fields | `keyword` (namespace_name, pod_name, container_name, host) |
| `@timestamp` | `date` |
| `log`, `message` | `text` + `.keyword` sub-field |
| `dynamic` | `true` — other fields are auto-mapped |

> **Why `flat_object`?** OpenSearch 2.x `flat_object` type stores the entire JSON subtree without expanding each key into a dedicated field. This avoids the mapping conflict where both `kubernetes.labels.app` (string) and `kubernetes.labels.app_kubernetes_io/component` (string) would otherwise compete as sibling document fields. See [OpenSearch flat_object docs](https://docs.opensearch.org/latest/field-types/supported-field-types/flat-object/).

> **Note:** Fluentd's `record_transformer` filter (in `configmap.yaml`) also sanitizes label keys by replacing dots and slashes with underscores before ingest. The `flat_object` mapping provides a second layer of defence for any dotted keys that slip through.

**Bootstrap job behaviour:**

- Runs as an ArgoCD `PostSync` hook after the opensearch application syncs.
- Idempotent: checks whether the template already exists; skips creation if so.
- Fails fast if OpenSearch is unreachable so ArgoCD reports degraded status rather than silently continuing.
- Delete policy: `BeforeHookCreation,HookSucceeded` — cleans up the Job pod after success; removes any stale failed Job before re-creating on the next sync.

**Verify the template was applied:**

```bash
# Check the index template
kubectl exec -n platform-db opensearch-cluster-master-0 -- \
  curl -s 'http://localhost:9200/_index_template/digiorg-logs-template' | python3 -m json.tool

# Confirm flat_object mapping on an existing index
kubectl exec -n platform-db opensearch-cluster-master-0 -- \
  curl -s 'http://localhost:9200/digiorg-logs-*/_mapping' | python3 -m json.tool | grep -A2 '"labels"'

# Check bootstrap Job status
kubectl get job -n platform-db opensearch-index-template-bootstrap
kubectl logs -n platform-db -l app.kubernetes.io/name=opensearch-index-template-bootstrap
```

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
4. **Index lifecycle:** ISM is configured for `digiorg-logs-*` (7-day retention, local/dev). For production, increase retention period and add ISM rollover policies for Jaeger trace indices. Secure the bootstrap job with TLS, credentials, and least-privilege RBAC.
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

# List Fluentd log indices
kubectl exec -n platform-db opensearch-cluster-master-0 -- \
  curl -s 'http://localhost:9200/_cat/indices/digiorg-logs-*?v'

# Check ISM retention policy
kubectl exec -n platform-db opensearch-cluster-master-0 -- \
  curl -s 'http://localhost:9200/_plugins/_ism/policies/digiorg-logs-retention-7d'

# Check ISM bootstrap Job status
kubectl get job -n platform-db opensearch-ism-retention-bootstrap
kubectl logs -n platform-db -l app.kubernetes.io/name=opensearch-ism-bootstrap
```
