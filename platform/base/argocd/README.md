# ArgoCD Configuration

ArgoCD is the GitOps engine for this platform, configured with Keycloak SSO.

## Files

| File | Description |
|------|-------------|
| `values.yaml` | Helm values for `argo/argo-cd` chart |
| `kustomization.yaml` | Kustomize entrypoint |
| `rbac-cm.yaml` | Kubernetes ConfigMap for ArgoCD RBAC policy (deployed via Kustomize) |
| `applications/` | ArgoCD Application manifests |

## Authentication

ArgoCD is configured with **Keycloak OIDC** for Single Sign-On:

- **Keycloak Realm:** `digiorg-core-platform`
- **Client ID:** `argocd`
- **Login:** Click "Login via Keycloak" on the ArgoCD UI
- **Admin account:** disabled — SSO via Keycloak is the only login method

## Access

| Environment | URL |
|-------------|-----|
| Local (KinD) | https://digiorg.local/argocd |

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

After Helm install, `local-setup.nu` patches the ArgoCD OIDC ConfigMap with the self-signed CA certificate provisioned by cert-manager (`rootCA` field). In production (using Let's Encrypt), the `rootCA` field is not needed and should be omitted.

## RBAC

RBAC policy is split across two sources:

- **`values.yaml` (`configs.rbac`)** — inline policy applied at Helm install time, including an explicit sync deny for `role:readonly`
- **`rbac-cm.yaml`** — standalone ConfigMap deployed via Kustomize; takes effect after Kustomize apply

| Role | Permissions |
|------|-------------|
| `role:admin` | Full access to all resources |
| `role:readonly` | Read-only access; sync explicitly denied |

Group mappings (from Keycloak):
- `platform-admins` → `role:admin`
- `platform-viewers` → `role:readonly`

## Configuration Details

### OIDC Settings (values.yaml)

```yaml
configs:
  cm:
    url: https://digiorg.local/argocd
    oidc.config: |
      name: Keycloak
      issuer: https://digiorg.local/keycloak/realms/digiorg-core-platform
      clientID: argocd
      clientSecret: $oidc.keycloak.clientSecret
      requestedScopes:
        - openid
        - profile
        - email
        - roles
```

### Path-based Routing

ArgoCD is served at `/argocd` via path-based routing. The following `values.yaml` parameters configure this:

```yaml
configs:
  params:
    server.basehref: "/argocd"
    server.rootpath: "/argocd"
```

### Ingress

ArgoCD is exposed via the unified platform ingress at `/argocd`. The ingress is configured in `platform/base/ingress/`.

## Troubleshooting

### Login fails

1. Check Keycloak is running: `kubectl get pods -n keycloak`
2. Verify realm exists: `curl https://digiorg.local/keycloak/realms/digiorg-core-platform`
3. Check ArgoCD logs: `kubectl logs -n argocd -l app.kubernetes.io/name=argocd-server`

### OIDC redirect issues

Ensure CoreDNS is configured to resolve `digiorg.local` internally. The setup script handles this automatically.
