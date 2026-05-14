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

> **Warning:** `NODE_TLS_REJECT_UNAUTHORIZED=0` is set in the deployment to allow Backstage to reach Keycloak over a self-signed certificate in the local KinD environment. Remove this variable in production.

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

An `initContainer` (`wait-for-postgres`, based on `busybox`) polls `postgresql.platform-db.svc.cluster.local:5432` before the main container starts. The Backstage pod will remain in `Init` state until PostgreSQL is reachable.

### Keycloak

OIDC login requires Keycloak to be running and the `digiorg-core-platform` realm to exist with the `backstage` client configured.

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

The `wait-for-postgres` initContainer is blocking. PostgreSQL is not reachable yet.

1. Check PostgreSQL is running: `kubectl get pods -n platform-db`
2. Verify the service resolves: `kubectl run -it --rm debug --image=busybox --restart=Never -- nc -zv postgresql.platform-db.svc.cluster.local 5432`

### OIDC login fails

1. Check Keycloak is running: `kubectl get pods -n keycloak`
2. Verify the realm and client exist in the Keycloak admin console
3. Check Backstage logs: `kubectl logs -n backstage -l app.kubernetes.io/name=backstage`
4. Confirm the `AUTH_OIDC_CLIENT_SECRET` in `backstage-secrets` matches the Keycloak client secret

### Image pull error

The deployment uses `ghcr.io/digiorg/core-portal:latest`. If the image cannot be pulled:

1. Verify the image exists in the registry: `docker pull ghcr.io/digiorg/core-portal:latest`
2. Check that an image pull secret is configured if the registry is private
