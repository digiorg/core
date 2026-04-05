# ArgoCD Configuration

ArgoCD is the GitOps engine for this platform, configured with Keycloak SSO.

## Files

| File | Description |
|------|-------------|
| `values.yaml` | Helm values for `argo/argo-cd` chart |
| `kustomization.yaml` | Kustomize entrypoint |
| `applications/` | ArgoCD Application manifests |

## Authentication

ArgoCD is configured with **Keycloak OIDC** for Single Sign-On:

- **Keycloak Realm:** `digiorg-core-platform`
- **Client ID:** `argocd`
- **Login:** Click "Login via Keycloak" on the ArgoCD UI

## Access

| Environment | URL |
|-------------|-----|
| Local (KinD) | http://digiorg.local/argocd |

## Local Development

ArgoCD is installed by `scripts/local-setup.nu` using Helm:

```bash
helm upgrade --install argocd argo/argo-cd \
  --namespace argocd \
  --create-namespace \
  --values platform/base/argocd/values.yaml \
  --set 'server.service.type=ClusterIP' \
  --set 'configs.params.server\.insecure=true' \
  --wait --timeout 10m
```

## RBAC

Two default roles are configured via `values.yaml`:

| Role | Permissions |
|------|-------------|
| `role:admin` | Full access to all resources |
| `role:readonly` | Read-only access |

Group mappings (from Keycloak):
- `platform-admins` → `role:admin`
- `platform-viewers` → `role:readonly`

## Configuration Details

### OIDC Settings (values.yaml)

```yaml
configs:
  cm:
    url: http://digiorg.local/argocd
    oidc.config: |
      name: Keycloak
      issuer: http://digiorg.local/keycloak/realms/digiorg-core-platform
      clientID: argocd
      clientSecret: $oidc.keycloak.clientSecret
      requestedScopes:
        - openid
        - profile
        - email
        - groups
```

### Ingress

ArgoCD is exposed via the unified platform ingress at `/argocd`. The ingress is configured in `platform/base/ingress/`.

## Troubleshooting

### Login fails

1. Check Keycloak is running: `kubectl get pods -n keycloak`
2. Verify realm exists: `curl http://digiorg.local/keycloak/realms/digiorg-core-platform`
3. Check ArgoCD logs: `kubectl logs -n argocd -l app.kubernetes.io/name=argocd-server`

### OIDC redirect issues

Ensure CoreDNS is configured to resolve `digiorg.local` internally. The setup script handles this automatically.
