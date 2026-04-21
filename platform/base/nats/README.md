# NATS JetStream Messaging

NATS JetStream is the platform message broker, providing pub/sub messaging with persistence, consumer groups, and dead-letter queues.

## Components

| Component | Description | Namespace |
|-----------|-------------|-----------|
| NATS Server | Core broker with JetStream (Helm) | messaging |
| NATS Surveyor | Prometheus metrics exporter | messaging |
| ServiceMonitor | Prometheus Operator scrape config | messaging |
| Grafana Dashboards | NATS Overview + JetStream dashboards | monitoring |

## Monitoring

NATS metrics are collected by Prometheus via the NATS Surveyor exporter and visualized in Grafana.

### Dashboards

| Dashboard | Description |
|-----------|-------------|
| NATS Overview | Server health, connections, messages/sec |
| JetStream State | Streams, consumers, storage |

Dashboards are automatically provisioned in Grafana via the `nats-grafana-dashboards` ConfigMap (label `grafana_dashboard: "1"`).

**Access:** `https://digiorg.local/grafana` → search for "NATS"

### Prometheus

Surveyor metrics are scraped via a ServiceMonitor CRD:
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

```bash
# Install nats CLI
curl -sf https://binaries.nats.dev/nats-io/natscli/nats@latest | sh

# Port-forward for local access
kubectl port-forward -n messaging svc/nats 4222:4222

# Server info
nats --server nats://localhost:4222 server info

# JetStream status
nats stream ls
nats consumer ls DIGIORG_EVENTS
```

## Production

For production environments, replace NATS with cloud-native backends via Dapr:
- **Azure**: Azure Service Bus (`pubsub.azure.servicebus`)
- **AWS**: SNS + SQS (`pubsub.snssqs`)
- **GCP**: Google Pub/Sub (`pubsub.gcp.pubsub`)
