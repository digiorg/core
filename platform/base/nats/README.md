# NATS JetStream Messaging

NATS JetStream is the platform message broker, providing pub/sub messaging with persistence, consumer groups, and dead-letter queues.

## Files

| File | Purpose |
|------|---------|
| `namespace.yaml` | Namespace `messaging` |
| `values.yaml` | NATS Helm Chart values (JetStream, auth, resource limits) |
| `surveyor.yaml` | NATS Surveyor Deployment + ClusterIP Service |
| `servicemonitor.yaml` | Prometheus Operator ServiceMonitor for Surveyor scraping |
| `grafana-dashboards.yaml` | Grafana Dashboard ConfigMap (deployed to `monitoring` namespace) |
| `kustomization.yaml` | Kustomize entrypoint — note: NATS server itself is deployed via Helm by ArgoCD (`apps/platform/nats.yaml`), not Kustomize |

## Components

| Component | Description | Namespace |
|-----------|-------------|-----------|
| NATS Server | Core broker with JetStream, single-node (`cluster.enabled: false`) | messaging |
| NATS Surveyor | Prometheus metrics exporter (`natsio/nats-surveyor:latest`) | messaging |
| ServiceMonitor | Prometheus Operator scrape config (`release: prometheus`) | messaging |
| Grafana Dashboards | NATS Overview + JetStream State and Metrics dashboards | monitoring |

### Configuration notes

- **Single-node**: `cluster.enabled: false` — NATS runs as a single instance for local development. Not suitable for production HA.
- **JetStream storage**: Persistent file storage backed by a 1Gi PersistentVolumeClaim, mounted at `/data` in the `messaging` namespace.
- **nats-box disabled**: `natsBox.enabled: false` — the nats-box debug sidecar is not deployed. Use `kubectl exec` into the NATS pod or the port-forward approach described in the NATS CLI section instead.
- **Surveyor image**: `natsio/nats-surveyor:latest` — `:latest` is acceptable for local development; pin to a specific version for production.

## Authentication

NATS authentication is active. Two accounts are defined in `values.yaml`:

| Account | Username | Password | Purpose |
|---------|----------|----------|---------|
| `$SYS` | `sys` | `sys_password` | System account — used by NATS Surveyor for `$SYS` subject access and server monitoring |
| `APP` | `app` | _(none)_ | Application account — JetStream-enabled, used by application pods for pub/sub |

> **WARNING — DEV-ONLY DEFAULTS**: The credentials above (`sys_password`) are hardcoded in plain text and are intended for local development only. Rotate all credentials before deploying to any non-local environment.

> **NOTE — APP account has no password**: The `APP` account has no password set in `values.yaml`. Any pod in the cluster can connect to NATS JetStream on port 4222 without credentials (connecting as `--user app` with no password). This is intentional for local development convenience but must be addressed before deploying to a shared or production environment.

## Architecture

```
Application Pods (--user app, no password)
        │
        ▼
NATS Server :4222      (JetStream enabled, 1Gi PVC file storage at /data, messaging ns)
        │
   $SYS account
   (sys / sys_password)
        │
        ▼
NATS Surveyor :7777/metrics    (natsio/nats-surveyor:latest, messaging ns)
        │
        ▼
ServiceMonitor                 (label: release: prometheus, messaging ns)
        │
        ▼
Prometheus
        │
        ▼
Grafana                        (nats-grafana-dashboards ConfigMap, monitoring ns)
```

## Monitoring

NATS metrics are collected by Prometheus via the NATS Surveyor exporter and visualized in Grafana.

### Dashboards

| Dashboard | Description |
|-----------|-------------|
| NATS Overview | Server health, connections, messages/sec |
| JetStream State and Metrics | Streams, consumers, storage |

Dashboards are automatically provisioned in Grafana via the `nats-grafana-dashboards` ConfigMap (label `grafana_dashboard: "1"`).

**Access:** `https://digiorg.local/grafana` → search for "NATS"

### Prometheus

Surveyor metrics are scraped via a ServiceMonitor CRD. The ServiceMonitor carries the label `release: prometheus`, which is required for the Prometheus Operator to discover it — this value must match the Helm release name of `kube-prometheus-stack`.

```bash
# Check ServiceMonitor
kubectl get servicemonitor -n messaging

# Check Prometheus targets (look for nats-surveyor)
# Open https://digiorg.local/grafana → Explore → Prometheus data source
# Query: nats_varz_connections
```

## Internal Access

| Endpoint | URL |
|----------|-----|
| NATS Client | `nats://nats.messaging.svc.cluster.local:4222` |
| NATS Monitoring | `http://nats-headless.messaging.svc.cluster.local:8222` |
| Surveyor Metrics | `http://nats-surveyor.messaging.svc.cluster.local:7777/metrics` |

## NATS CLI

Two accounts are in use. Use `$SYS` credentials (`sys` / `sys_password`) for server-level info and admin commands. Use the `APP` account (`app`, no password) for JetStream operations.

```bash
# Install nats CLI
curl -sf https://binaries.nats.dev/nats-io/natscli/nats@latest | sh

# Port-forward for local access
kubectl port-forward -n messaging svc/nats 4222:4222

# Server info — requires $SYS credentials (monitoring account used by Surveyor)
nats --server nats://localhost:4222 --user sys --password sys_password server info

# JetStream status — use APP account (JetStream-enabled; no password configured)
nats --server nats://localhost:4222 --user app stream ls
nats --server nats://localhost:4222 --user app consumer ls DIGIORG_EVENTS
```

## Troubleshooting

```bash
# Check pod status in the messaging namespace
kubectl get pods -n messaging

# NATS server logs
kubectl logs -n messaging -l app.kubernetes.io/name=nats

# Surveyor logs (check for auth or connection errors to NATS)
kubectl logs -n messaging -l app.kubernetes.io/name=nats-surveyor

# Verify ServiceMonitor is registered
kubectl get servicemonitor -n messaging

# Check JetStream PVC (1Gi, storageDirectory: /data)
kubectl get pvc -n messaging

# Port-forward then query server info (requires $SYS credentials)
kubectl port-forward -n messaging svc/nats 4222:4222 &
nats --server nats://localhost:4222 --user sys --password sys_password server info
```

## Production

For production environments, replace NATS with cloud-native backends via Dapr:
- **Azure**: Azure Service Bus (`pubsub.azure.servicebus`)
- **AWS**: SNS + SQS (`pubsub.snssqs`)
- **GCP**: Google Pub/Sub (`pubsub.gcp.pubsub`)
