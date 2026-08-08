# Backstage

Backstage is the internal developer portal for this platform, providing a service catalog, documentation hub, and software templates.

## Files

| File | Description |
|------|-------------|
| `namespace.yaml` | `backstage` namespace definition |
| `deployment.yaml` | Backstage application deployment |
| `service.yaml` | ClusterIP service exposing port 7007 |
| `kustomization.yaml` | Kustomize entrypoint |

## Authentication

Backstage is configured with **Keycloak OIDC** for Single Sign-On:

- **Keycloak Realm:** `digiorg-core-platform`
- **Client ID:** `backstage`
- **Metadata URL:** `https://digiorg.local/keycloak/realms/digiorg-core-platform/.well-known/openid-configuration`

TLS verification remains enabled in local KinD. The public `digiorg.local` CA
is mounted from the required `digiorg-local-ca` Secret and exposed to Node via
`NODE_EXTRA_CA_CERTS`; no global TLS bypass is used.

## Access

| Environment | URL |
|-------------|-----|
| Local (KinD) | https://digiorg.local/backstage |

## Dependencies

### PostgreSQL

Backstage requires a running PostgreSQL instance. The deployment connects to:

- **Host:** `postgresql.platform-db.svc.cluster.local`
- **Port:** `5432`
- **Database user:** `backstage`

An init container (`wait-for-postgres`, based on a digest-pinned `busybox`)
polls `postgresql.platform-db.svc.cluster.local:5432` before the OIDC startup
gate runs. The Backstage Pod remains in `Init` until PostgreSQL is reachable.

### Keycloak

OIDC login requires Keycloak to be running and the `digiorg-core-platform`
realm to exist with the `backstage` client configured. A second init container,
`wait-for-oidc-discovery`, fetches the exact public discovery URL with the
mounted CA and validates that the JSON issuer exactly matches the configured
realm before the Backstage process can start. Requests and retries are bounded;
if discovery is temporarily unavailable, kubelet retries the failed init
container with backoff on the same Pod and startup continues automatically once
the endpoint becomes valid.

## Secrets

Backstage reads sensitive values from the `backstage-secrets` Kubernetes Secret. This secret is **not managed by Kustomize** — it is created by `scripts/local-setup.nu` and must exist in the `backstage` namespace before applying the manifests.

| Key | Description |
|-----|-------------|
| `POSTGRES_PASSWORD` | Password for the `backstage` PostgreSQL user |
| `AUTH_SESSION_SECRET` | Random secret used to sign Backstage session cookies |
| `AUTH_OIDC_CLIENT_SECRET` | Keycloak client secret for the `backstage` OIDC client |
| `GITHUB_TOKEN` | Personal access token for GitHub catalog integration (optional) |

## Resource Limits

| | CPU | Memory |
|-|-----|--------|
| Request | 200m | 512Mi |
| Limit | 1000m | 1Gi |

## Health Checks

| Probe | Endpoint | Initial Delay | Period | Failure Threshold |
|-------|----------|---------------|--------|-------------------|
| Readiness | `GET /.backstage/health/v1/readiness:7007` | 60s | 10s | 6 |
| Liveness | `GET /.backstage/health/v1/liveness:7007` | 120s | 30s | 5 |

Backstage takes a moment to start — the generous initial delays account for plugin initialization and the database migration that runs on first boot.

## Troubleshooting

### Pod stuck in Init state

First inspect which ordered init container is blocking:

```bash
kubectl get pods -n backstage -l app=backstage
kubectl logs -n backstage -l app=backstage -c wait-for-postgres --tail=100
kubectl logs -n backstage -l app=backstage -c wait-for-oidc-discovery --tail=100
```

- `wait-for-postgres`: check the PostgreSQL Pods in `platform-db` and the
  `postgresql.platform-db.svc.cluster.local:5432` Service endpoint.
- `wait-for-oidc-discovery`: check Keycloak readiness, the public ingress route,
  and that `backstage/digiorg-local-ca` contains a non-empty `ca.crt` key. Do
  not disable TLS verification.

### OIDC login fails

1. Check Keycloak is running: `kubectl get pods -n keycloak`
2. Verify the realm and client exist in the Keycloak admin console
3. Check Backstage logs: `kubectl logs -n backstage -l app.kubernetes.io/name=backstage`
4. Confirm the `AUTH_OIDC_CLIENT_SECRET` in `backstage-secrets` matches the Keycloak client secret

### Image pull error

The deployment uses digest-pinned Backstage and startup-gate images. If an
image cannot be pulled:

1. Copy the exact image reference from `deployment.yaml` and verify that digest
   exists in its registry.
2. Check that an image pull secret is configured if the registry is private.
