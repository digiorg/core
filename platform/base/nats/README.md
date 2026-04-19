# NATS JetStream Messaging

NATS JetStream is the platform message broker, providing pub/sub messaging with persistence, consumer groups, and dead-letter queues.

## Components

| Component | Description | Port |
|-----------|-------------|------|
| NATS Server | Core broker with JetStream | 4222 (client), 8222 (monitor) |
| NATS Surveyor | Read-only observability UI | 7777 (internal only) |
| oauth2-proxy | Keycloak SSO gateway for Surveyor | 4180 (internal only) |

## Access

| Environment | URL | Auth |
|-------------|-----|------|
| Local Dev | `https://digiorg.local/nats` | Keycloak SSO |
| Internal (cluster) | `nats://nats.messaging.svc.cluster.local:4222` | None (cluster-internal) |

## Secrets

The following secrets must be created before ArgoCD sync (handled by `scripts/local-setup.nu`):

```bash
# oauth2-proxy secrets
kubectl create secret generic nats-oauth2-proxy-secret \
  -n messaging \
  --from-literal=client-secret=<keycloak-client-secret> \
  --from-literal=cookie-secret=$(openssl rand -base64 32)
```

## Deployment

NATS is deployed via Helm (ArgoCD Application `nats.yaml`, Wave 0):

```bash
# Manually install/upgrade
helm repo add nats https://nats-io.github.io/k8s/helm/charts
helm upgrade --install nats nats/nats \
  --namespace messaging \
  --create-namespace \
  --values platform/base/nats/values.yaml
```

## Keycloak Client

The `nats-surveyor` OIDC client is configured in the `digiorg-core-platform` realm:
- **Client ID**: `nats-surveyor`
- **Redirect URIs**: `https://digiorg.local/nats/*`
- **Type**: Confidential (uses oauth2-proxy)

## NATS CLI

```bash
# Install nats CLI
curl -sf https://binaries.nats.dev/nats-io/natscli/nats@latest | sh

# Connect to local NATS
nats --server nats://localhost:4222 server info

# Port-forward for local access
kubectl port-forward -n messaging svc/nats 4222:4222

# JetStream status
nats stream ls
nats consumer ls DIGIORG_EVENTS
```

## Production

For production environments, replace NATS with cloud-native backends via Dapr:
- **Azure**: Azure Service Bus (`pubsub.azure.servicebus`)
- **AWS**: SNS + SQS (`pubsub.snssqs`)
- **GCP**: Google Pub/Sub (`pubsub.gcp.pubsub`)
