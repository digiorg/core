# Keycloak Configuration

Keycloak 26.0 (`quay.io/keycloak/keycloak:26.0`) is the Identity Provider (IdP) for the platform, providing SSO for all services via the `digiorg-core-platform` realm.

> **Development mode notice**
> This deployment runs Keycloak in `start-dev` mode (`args: [start-dev, --import-realm]`). HTTP is used internally; TLS is terminated at the NGINX ingress. This configuration is **not suitable for production** — there is no clustering, no HA, and no persistent session store. The `--import-realm` flag only imports the realm on first start **when the realm does not yet exist in the database**.

## Files

| File | Description |
|------|-------------|
| `keycloak-deployment.yaml` | Keycloak server deployment (Service + Deployment) |
| `kustomization.yaml` | Kustomize entrypoint; generates realm ConfigMap from JSON files |
| `values.yaml` | Helm/ArgoCD value overrides |
| `digiorg-core-platform-realm.json` | Realm definition (clients, roles, groups) |
| `digiorg-core-platform-users-0.json` | Pre-seeded realm users |

**Note:** Keycloak uses the shared PostgreSQL instance in the `platform-db` namespace. Database credentials are provided via the `keycloak-db-credentials` Secret.

## Access

| Environment | URL | Credentials |
|-------------|-----|-------------|
| Local (KinD) | https://digiorg.local/keycloak | admin / admin |

Admin Console: https://digiorg.local/keycloak/admin

## Ports

| Port | Protocol | Purpose |
|------|----------|---------|
| 8080 | HTTP | Keycloak application (internal) |
| 9000 | HTTP | Health & management API (`KC_HEALTH_ENABLED=true`) |
| 30100 | TCP | NodePort for local KinD cluster access |

Health endpoint: `http://keycloak.keycloak.svc.cluster.local:9000/health/ready`

## Pre-configured Realm

The `digiorg-core-platform` realm is automatically imported on startup with:

### OIDC Clients

| Client ID | Service | Redirect URIs | Notes |
|-----------|---------|---------------|-------|
| `landingpage` | Landing Page | `https://digiorg.local/*` | Public client (no secret), PKCE S256 |
| `argocd` | ArgoCD | `https://digiorg.local/argocd/auth/callback` | Auto-configured |
| `grafana` | Grafana | `https://digiorg.local/grafana/login/generic_oauth` | Auto-configured |
| `backstage` | Backstage | `https://digiorg.local/backstage/api/auth/oidc/handler/frame`, `https://digiorg.local/backstage/*` | Auto-configured |
| `gitea` | Gitea | `https://digiorg.local/gitea/user/oauth2/Keycloak/callback`, `https://digiorg.local/gitea/*` | **Manual config in Gitea Admin UI** |
| `jaeger` | Jaeger | `https://digiorg.local/jaeger/oauth2/callback` | Protected via oauth2-proxy + Keycloak OIDC |
| `sonarqube` | SonarQube | `https://digiorg.local/sonarqube/oauth2/callback/saml`, `https://digiorg.local/sonarqube/*` | SAML/OIDC callback |

### Realm Roles

| Role | Description |
|------|-------------|
| `platform-admin` | Full administrative access to the platform |
| `platform-viewer` | Read-only access to the platform |
| `developer` | Standard developer access |

### Default Users

#### Keycloak Master Admin

| Username | Password | Scope |
|----------|----------|-------|
| `admin` | `admin` | Keycloak master realm (bootstrap only) |

#### Pre-seeded Realm Users

| Username | Password | Email | Realm Role |
|----------|----------|-------|------------|
| `digiorgadmin` | `digiorgadmin` | admin@digiorg.local | `platform-admin` |
| `digiorgdeveloper` | `digiorgdeveloper` | developer@digiorg.local | `developer` |

## Architecture

```
┌─────────────────────────────────────────────────────┐
│            Keycloak 26.0 (start-dev)                │
│                                                     │
│  ┌─────────────────────────────────────────────┐    │
│  │         Realm: digiorg-core-platform        │    │
│  │                                             │    │
│  │  Clients:                                   │    │
│  │  ├── landingpage (OIDC, public)             │    │
│  │  ├── argocd (OIDC)                          │    │
│  │  ├── grafana (OIDC)                         │    │
│  │  ├── backstage (OIDC)                       │    │
│  │  ├── gitea (OIDC)*                          │    │
│  │  ├── jaeger (OIDC, oauth2-proxy)            │    │
│  │  └── sonarqube (SAML)                       │    │
│  │                                             │    │
│  │  * Gitea OIDC configured in Admin UI        │    │
│  │                                             │    │
│  │  Roles:                                     │    │
│  │  ├── platform-admin                         │    │
│  │  ├── platform-viewer                        │    │
│  │  └── developer                              │    │
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

### keycloak-db-credentials Secret

Keycloak requires a `keycloak-db-credentials` Secret in the `keycloak` namespace before the pod can start:

```bash
kubectl create secret generic keycloak-db-credentials \
  --namespace keycloak \
  --from-literal=password=<your-db-password>
```

> **Note:** `local-setup.nu` handles this automatically for local development.

## Adding New OIDC Clients

1. Edit `digiorg-core-platform-realm.json`
2. Add new client under the `clients` array
3. Re-apply: `kubectl apply -k platform/base/keycloak/`

> **Warning:** The `--import-realm` flag only imports the realm when it **does not yet exist** in the database. Modifying the JSON files and restarting Keycloak will **not** re-apply changes to an existing realm.
>
> To apply changes to an existing realm, either:
> - Use the Admin Console directly at https://digiorg.local/keycloak/admin, **or**
> - Delete the realm in the database and restart Keycloak (destructive — all realm data and user sessions are wiped)

Or use the Keycloak Admin Console directly:
1. Go to https://digiorg.local/keycloak/admin
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
kubectl get configmap keycloak-realm-import -n keycloak

# Verify realm import
kubectl logs -n keycloak -l app=keycloak | grep -i realm
```

### OIDC errors in services

1. Verify client secret matches in both Keycloak and service config
2. Check redirect URI is correctly configured
3. Ensure Keycloak is accessible from the service pod (CoreDNS config)
