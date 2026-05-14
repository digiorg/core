# Ingress

Unified path-based routing for all platform services on a single domain (`digiorg.local`) using NGINX Ingress Controller.

## Overview

All services are exposed via a single domain using three Ingress objects and the ExternalName service pattern:

- **`digiorg-platform`** — main ingress handling most services with regex path matching
- **`backstage-ingress`** — separate ingress for Backstage due to URL rewrite requirement
- **`gitea-ingress`** — separate ingress for Gitea due to URL rewrite and large upload support

Services in other namespaces are reached via ExternalName services in `ingress-nginx`, since NGINX cannot route cross-namespace directly.

## Files

| File | Description |
|------|-------------|
| `digiorg-ingress.yaml` | All Ingress objects and ExternalName services |
| `kustomization.yaml` | Kustomize entrypoint |

## Prerequisites

1. NGINX Ingress Controller installed and running in `ingress-nginx`
2. cert-manager deployed with the `digiorg-local-ca-issuer` ClusterIssuer ready (see `platform/base/cert-manager/README.md`)
3. `/etc/hosts` entry: `127.0.0.1 digiorg.local`
4. Local CA certificate imported into OS trust store to avoid browser warnings (see `platform/base/cert-manager/README.md`)
5. KinD cluster with ports 80 and 443 mapped to the host

## Service Routes

| Path | Backend Service | Port | Notes |
|------|----------------|------|-------|
| `/keycloak(/\|$)(.*)` | `keycloak` | 8080 | |
| `/argocd(/\|$)(.*)` | `argocd-server` | 80 | |
| `/grafana(/\|$)(.*)` | `prometheus-grafana` | 80 | |
| `/jaeger(/\|$)(.*)` | `jaeger-oauth2-proxy` | 4180 | oauth2-proxy; Keycloak SSO required |
| `/sonarqube(/\|$)(.*)` | `sonarqube` | 9000 | native subpath via `sonarWebContext=/sonarqube` |
| `/backstage(/\|$)(.*)` | `backstage` | 7007 | URL rewrite to `/` (separate ingress) |
| `/gitea(/\|$)(.*)` | `gitea-http` | 3000 | URL rewrite to `/`; 512m body limit (separate ingress) |
| `/` | `landingpage` | 8080 | catch-all; must remain last |

## Ingress Objects

### digiorg-platform

The main ingress uses `nginx.ingress.kubernetes.io/use-regex: "true"` with `ImplementationSpecific` path type for regex matching. Proxy buffer settings (`proxy-buffer-size: 128k`, `proxy-buffers-number: "4"`) handle services that return large response headers (e.g. Keycloak). The catch-all `/` route for the landing page uses `Prefix` type and must remain the last rule.

### backstage-ingress and gitea-ingress

Backstage and Gitea require a `rewrite-target: /` annotation to strip the path prefix before forwarding (e.g. `/backstage/foo` → `/foo`). This annotation conflicts with the regex routing in the main ingress, so these services get their own Ingress objects.

Gitea additionally sets `proxy-body-size: 512m` to allow large Git push payloads.

## ExternalName Services

NGINX Ingress can only route to services in its own namespace (`ingress-nginx`). ExternalName services act as DNS aliases, forwarding traffic to services in other namespaces via their cluster-internal DNS name.

| Service | ExternalName | Port | Target Namespace |
|---------|-------------|------|-----------------|
| `keycloak` | `keycloak.keycloak.svc.cluster.local` | 8080 | `keycloak` |
| `argocd-server` | `argocd-server.argocd.svc.cluster.local` | 80 | `argocd` |
| `prometheus-grafana` | `prometheus-grafana.monitoring.svc.cluster.local` | 80 | `monitoring` |
| `backstage` | `backstage.backstage.svc.cluster.local` | 7007 | `backstage` |
| `gitea-http` | `gitea-http.gitea.svc.cluster.local` | 3000 | `gitea` |
| `jaeger-oauth2-proxy` | `jaeger-oauth2-proxy.tracing.svc.cluster.local` | 4180 | `tracing` |
| `sonarqube` | `sonarqube-sonarqube.code-quality.svc.cluster.local` | 9000 | `code-quality` |
| `landingpage` | `landingpage.platform-apps.svc.cluster.local` | 8080 | `platform-apps` |

> **Note:** The SonarQube ExternalName is `sonarqube-sonarqube` — this is the Helm-generated fullname (chart name + release name) with no deduplication.

## TLS

All traffic is served over HTTPS. The ingress is annotated with:

- `cert-manager.io/cluster-issuer: digiorg-local-ca-issuer` — cert-manager issues and renews the certificate automatically
- `nginx.ingress.kubernetes.io/ssl-redirect: "true"` and `force-ssl-redirect: "true"` — HTTP requests are permanently redirected to HTTPS

The TLS certificate is stored in secret `digiorg-local-tls` in namespace `ingress-nginx`, covering host `digiorg.local`.

## Switching Issuers

To switch from the local dev CA to Let's Encrypt, update the annotation in `digiorg-ingress.yaml`:

```yaml
# Local dev (default)
cert-manager.io/cluster-issuer: "digiorg-local-ca-issuer"

# Let's Encrypt staging (test)
cert-manager.io/cluster-issuer: "letsencrypt-staging"

# Let's Encrypt production
cert-manager.io/cluster-issuer: "letsencrypt-prod"
```

> **Prerequisite:** Before enabling `letsencrypt-staging` or `letsencrypt-prod`, replace the placeholder email in `platform/base/cert-manager/cluster-issuers.yaml` with a real address.

## Adding a New Service

1. Create an ExternalName service in `ingress-nginx` pointing to the backend's cluster DNS name:

   ```yaml
   apiVersion: v1
   kind: Service
   metadata:
     name: my-service
     namespace: ingress-nginx
   spec:
     type: ExternalName
     externalName: my-service.my-namespace.svc.cluster.local
     ports:
       - port: 8080
   ```

2. Add a path rule to `digiorg-platform` in `digiorg-ingress.yaml`. If the service has no native subpath support and requires a URL rewrite, create a separate Ingress object with `rewrite-target: /` instead.

3. If adding a catch-all or low-specificity prefix route, ensure it is placed **after** all more-specific paths. The `/` catch-all for the landing page must always remain last.

## Troubleshooting

### Ingress not routing

```bash
# Check the ingress rules are applied
kubectl describe ingress digiorg-platform -n ingress-nginx

# Verify the ExternalName service resolves
kubectl run -it --rm debug --image=busybox --restart=Never -- \
  nslookup keycloak.ingress-nginx.svc.cluster.local

# Check the backend pod is running
kubectl get pods -n <target-namespace>
```

### 502 / 503 errors

The backend service or pod is not ready. Check the pod status and logs in the target namespace:

```bash
kubectl get pods -n <target-namespace>
kubectl logs -n <target-namespace> <pod-name>
```

### TLS errors

Check that cert-manager has issued the certificate successfully:

```bash
kubectl get certificate digiorg-local-tls -n ingress-nginx
kubectl describe certificate digiorg-local-tls -n ingress-nginx
kubectl get certificaterequest -n ingress-nginx
```

If the certificate is pending, check cert-manager logs:

```bash
kubectl logs -n cert-manager deploy/cert-manager
```
