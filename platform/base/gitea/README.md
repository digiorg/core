# Gitea

Gitea is a self-hosted Git service providing Git hosting, code review, team collaboration, package registry, and CI/CD capabilities.

## Overview

Gitea is deployed as the central Git-based DevOps component for the DigiOrg Core Platform, replacing external Git providers for platform-internal repositories.

## Files

| File | Description |
|------|-------------|
| `namespace.yaml` | Gitea namespace |
| `values.yaml` | Helm chart values |
| `admin-secret.yaml` | Admin user placeholder secret |
| `kustomization.yaml` | Kustomize entrypoint |

## Access

| Environment | URL | Credentials |
|-------------|-----|-------------|
| Local (KinD) | http://digiorg.local/gitea | Login via Keycloak |

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Gitea                                         │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                     Gitea Server                                 │   │
│  │  • Git Repositories                                              │   │
│  │  • Code Review (Pull Requests)                                   │   │
│  │  • Issue Tracking                                                │   │
│  │  • Package Registry                                              │   │
│  │  • Gitea Actions (CI/CD)                                         │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                            │                                            │
└────────────────────────────┼────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                Shared PostgreSQL (platform-db)                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │
│  │ keycloak DB │  │ backstage DB│  │  gitea DB   │  │    ...      │    │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘    │
└─────────────────────────────────────────────────────────────────────────┘
```

## Features

### Git Hosting
- Unlimited private/public repositories
- Branch protection rules
- Webhooks for CI/CD integration
- SSH and HTTP(S) clone support

### Code Review
- Pull requests with inline comments
- Code owners / reviewers
- Merge options (merge, rebase, squash)

### Gitea Actions
- GitHub Actions-compatible workflow syntax
- act_runner for job execution
- Self-hosted runners

### Package Registry
- Container images (OCI)
- npm, Maven, PyPI, Cargo, and more
- Integrated with Git repositories

## Keycloak OIDC Integration

Gitea authenticates users via Keycloak OIDC:

1. User clicks "Sign in with Keycloak"
2. Redirect to Keycloak login
3. Keycloak validates credentials
4. Redirect back with authorization code
5. Gitea creates/links user account
6. User logged in

### Configuration

OIDC is configured via Gitea Admin UI after first deployment:

1. Go to **Site Administration** → **Authentication Sources**
2. Click **Add Authentication Source**
3. Select **OAuth2** → **OpenID Connect**
4. Configure:
   - Name: `Keycloak`
   - Client ID: `gitea` (from Keycloak)
   - Client Secret: (from Keycloak)
   - Auto Discovery URL: `http://digiorg.local/keycloak/realms/digiorg-core-platform/.well-known/openid-configuration`
   - Enable "Auto Registration"

## Secrets

| Namespace | Secret | Keys | Description |
|-----------|--------|------|-------------|
| `platform-db` | `postgresql-secrets` | `GITEA_DB_PASSWORD` | Database password |
| `gitea` | `gitea-secrets` | `POSTGRES_PASSWORD`, `AUTH_OIDC_CLIENT_SECRET` | Application secrets |
| `gitea` | `gitea-admin-secret` | `username`, `password` | Admin user (placeholder) |

Secrets are created by `scripts/local-setup.nu` before ArgoCD sync.

## Deployment

Gitea is deployed via Helm chart managed by ArgoCD:

```yaml
# apps/platform/gitea.yaml
source:
  repoURL: https://dl.gitea.com/charts/
  chart: gitea
  helm:
    valueFiles:
      - platform/base/gitea/values.yaml
```

## Sync Wave

Gitea is deployed in **Wave 2**:

| Wave | Applications | Dependencies |
|------|--------------|--------------|
| 0 | postgresql | Secrets |
| 1 | keycloak, argocd | PostgreSQL |
| **2** | **gitea**, backstage, monitoring | PostgreSQL, Keycloak |
| 3 | crossplane, kyverno | None |

## Troubleshooting

### Gitea not starting

```bash
# Check pod status
kubectl get pods -n gitea

# Check logs
kubectl logs -n gitea -l app.kubernetes.io/name=gitea

# Check PostgreSQL connection
kubectl exec -n platform-db postgresql-0 -- psql -U postgres -c "\l" | grep gitea
```

### OIDC login fails

```bash
# Check Keycloak client exists
curl -s http://digiorg.local/keycloak/realms/digiorg-core-platform/.well-known/openid-configuration | jq .issuer

# Check Gitea logs for OAuth errors
kubectl logs -n gitea -l app.kubernetes.io/name=gitea | grep -i oauth
```

### Database connection issues

```bash
# Verify secret values match
kubectl get secret postgresql-secrets -n platform-db -o jsonpath='{.data.GITEA_DB_PASSWORD}' | base64 -d && echo
kubectl get secret gitea-secrets -n gitea -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d && echo

# Test connection from Gitea pod
kubectl exec -n gitea -it $(kubectl get pods -n gitea -l app.kubernetes.io/name=gitea -o jsonpath='{.items[0].metadata.name}') -- nc -zv postgresql.platform-db.svc.cluster.local 5432
```

## Future Enhancements

1. **SSH Access**: Configure SSH port forwarding for Git over SSH
2. **Gitea Actions Runner**: Deploy act_runner for CI/CD
3. **Package Registry**: Enable and configure package registries
4. **Repository Mirroring**: Mirror external repos for offline access
5. **High Availability**: Scale replicas with external Redis
