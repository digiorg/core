# Keycloak Configuration

Keycloak is the Identity Provider (IdP) for the platform, providing SSO for all services.

## Files

| File | Description |
|------|-------------|
| `namespace.yaml` | Keycloak namespace |
| `postgres-deployment.yaml` | PostgreSQL database for Keycloak |
| `keycloak-deployment.yaml` | Keycloak server deployment |
| `realm-configmap.yaml` | Pre-configured realm (`digiorg-core-platform`) |
| `kustomization.yaml` | Kustomize entrypoint |

## Access

| Environment | URL | Credentials |
|-------------|-----|-------------|
| Local (KinD) | http://digiorg.local/keycloak | admin / admin |

## Pre-configured Realm

The `digiorg-core-platform` realm is automatically imported on startup with:

### OIDC Clients

| Client ID | Service | Redirect URIs |
|-----------|---------|---------------|
| `argocd` | ArgoCD | `http://digiorg.local/argocd/auth/callback` |
| `grafana` | Grafana | `http://digiorg.local/grafana/login/generic_oauth` |
| `backstage` | Backstage | `http://digiorg.local/backstage/api/auth/oidc/handler/frame` |

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
│  │  ├── argocd (OIDC)                          │    │
│  │  ├── grafana (OIDC)                         │    │
│  │  └── backstage (OIDC)                       │    │
│  │                                             │    │
│  │  Roles:                                     │    │
│  │  ├── platform-admin                         │    │
│  │  └── platform-viewer                        │    │
│  └─────────────────────────────────────────────┘    │
│                       │                             │
│                       ▼                             │
│              ┌─────────────┐                        │
│              │ PostgreSQL  │                        │
│              └─────────────┘                        │
└─────────────────────────────────────────────────────┘
```

## Local Development

Keycloak is installed by `scripts/local-setup.nu`:

```bash
# Apply manifests
kubectl apply -k platform/base/keycloak/

# Wait for PostgreSQL
kubectl rollout status deployment/postgres -n keycloak

# Wait for Keycloak
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
# Check PostgreSQL
kubectl get pods -n keycloak -l app=postgres
kubectl logs -n keycloak -l app=postgres

# Check Keycloak
kubectl get pods -n keycloak -l app=keycloak
kubectl logs -n keycloak -l app=keycloak
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
