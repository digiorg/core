# Keycloak Configuration

Keycloak is the Identity Provider (IdP) for the platform, providing SSO for all services.

## Files

| File | Description |
|------|-------------|
| `namespace.yaml` | Keycloak namespace |
| `keycloak-deployment.yaml` | Keycloak server deployment |
| `realm-configmap.yaml` | Pre-configured realm (`digiorg-core-platform`) |
| `kustomization.yaml` | Kustomize entrypoint |

**Note:** Keycloak uses the shared PostgreSQL instance in the `platform-db` namespace. Database credentials are provided via the `keycloak-db-credentials` Secret.

## Access

| Environment | URL | Credentials |
|-------------|-----|-------------|
| Local (KinD) | http://digiorg.local/keycloak | admin / admin |

## Pre-configured Realm

The `digiorg-core-platform` realm is automatically imported on startup with:

### OIDC Clients

| Client ID | Service | Redirect URIs | Notes |
|-----------|---------|---------------|-------|
| `landingpage` | Landing Page | `http://digiorg.local/*` | Public client (no secret) |
| `argocd` | ArgoCD | `http://digiorg.local/argocd/auth/callback` | Auto-configured |
| `grafana` | Grafana | `http://digiorg.local/grafana/login/generic_oauth` | Auto-configured |
| `backstage` | Backstage | `http://digiorg.local/backstage/api/auth/oidc/handler/frame` | Auto-configured |
| `gitea` | Gitea | `http://digiorg.local/gitea/user/oauth2/Keycloak/callback` | **Manual config in Gitea Admin UI** |

### Default Users

| Username | Password | Realm Role |
|----------|----------|------------|
| admin | admin | Admin (Keycloak master realm) |

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Keycloak                          │
│                                                     │
│  ┌─────────────────────────────────────────────┐    │
│  │         Realm: digiorg-core-platform        │    │
│  │                                             │    │
│  │  Clients:                                   │    │
│  │  ├── landingpage (OIDC, public)             │    │
│  │  ├── argocd (OIDC)                          │    │
│  │  ├── grafana (OIDC)                         │    │
│  │  ├── backstage (OIDC)                       │    │
│  │  └── gitea (OIDC)*                          │    │
│  │                                             │    │
│  │  * Gitea OIDC configured in Admin UI        │    │
│  │                                             │    │
│  │  Roles:                                     │    │
│  │  ├── platform-admin                         │    │
│  │  └── platform-viewer                        │    │
│  └─────────────────────────────────────────────┘    │
│                       │                             │
└───────────────────────┬─────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────────────┐
│         Shared PostgreSQL (platform-db)                          │
│                                                                  │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐      │
│  │  keycloak DB   │  │  backstage DB  │  │    gitea DB    │      │
│  └────────────────┘  └────────────────┘  └────────────────┘      │
└──────────────────────────────────────────────────────────────────┘
```

## Local Development

Keycloak is deployed via ArgoCD after the shared PostgreSQL is ready:

```bash
# Wait for PostgreSQL (deployed in Wave 0)
kubectl rollout status statefulset/postgresql -n platform-db

# Wait for Keycloak (deployed in Wave 1)
kubectl rollout status deployment/keycloak -n keycloak
```

## Adding New OIDC Clients

1. Edit `realm-configmap.yaml`
2. Add new client under `clients` array
3. Re-apply: `kubectl apply -k platform/base/keycloak/`
4. Restart Keycloak to re-import realm

Or use the Keycloak Admin Console:
1. Go to http://digiorg.local/keycloak/admin
2. Select realm `digiorg-core-platform`
3. Navigate to Clients → Create

## Troubleshooting

### Keycloak not starting

```bash
# Check shared PostgreSQL
kubectl get pods -n platform-db
kubectl logs -n platform-db -l app=postgresql

# Check Keycloak
kubectl get pods -n keycloak -l app=keycloak
kubectl logs -n keycloak -l app=keycloak

# Verify database connection
kubectl exec -n platform-db postgresql-0 -- psql -U postgres -c "\l" | grep keycloak
```

### Realm not loading

```bash
# Check ConfigMap exists
kubectl get configmap keycloak-realm -n keycloak

# Verify realm import
kubectl logs -n keycloak -l app=keycloak | grep -i realm
```

### OIDC errors in services

1. Verify client secret matches in both Keycloak and service config
2. Check redirect URI is correctly configured
3. Ensure Keycloak is accessible from the service pod (CoreDNS config)
