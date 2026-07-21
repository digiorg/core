# Local Development Guide

This guide explains how to set up and use the local development environment.

## Overview

The local development environment uses [KinD](https://kind.sigs.k8s.io/) (Kubernetes in Docker) to run a fully-functional platform locally with the **App-of-Apps pattern**. This enables:

- Testing changes before pushing to production
- Developing new features without cloud costs
- Running the full platform stack locally
- GitOps-native deployment via ArgoCD

## Prerequisites

### Required

| Tool | Version | Installation |
|------|---------|--------------|
| Docker | >= 20.10 | [Docker Desktop](https://www.docker.com/products/docker-desktop) |
| kubectl | >= 1.28 | `brew install kubectl` |
| Helm | >= 3.12 | `brew install helm` |
| KinD | >= 0.20 | `brew install kind` |
| Nushell | >= 0.90 | `brew install nushell` |

### Optional (Recommended)

| Tool | Purpose | Installation |
|------|---------|--------------|
| k9s | Terminal UI | `brew install k9s` |

### Host Configuration

Add the following to your `/etc/hosts` file (or `C:\Windows\System32\drivers\etc\hosts` on Windows):

```
127.0.0.1 digiorg.local
```

## Quick Start

### Start the Cluster

```bash
nu scripts/local-setup.nu up
```

This runs in three phases:

**Phase 1 (Bootstrap):**
1. Create a KinD cluster (`digiorg-core-dev`)
2. Set `vm.max_map_count=262144` on the KinD node via `docker exec` (required by OpenSearch embedded Elasticsearch)
3. Install Gateway API CRDs
4. Install NGINX Ingress Controller
5. Configure CoreDNS for `digiorg.local`
6. Create platform secrets (including shared PostgreSQL credentials)
7. Install ArgoCD (Helm)
8. Apply PostgreSQL and OpenSearch and wait for their real Service paths to become functionally ready
9. Deploy root-app

**Phase 2 (App-of-Apps):**
ArgoCD creates the child Applications. Core Applications use sync-wave ordering metadata:
- Wave 0: cert-manager, external-secrets, nats, opensearch, postgresql
- Wave 1: keycloak, argocd (self-managed)
- Wave 2: landingpage, backstage, gitea, grafana, harbor, jaeger, opencost, sonarqube
- Wave 3: crossplane, kyverno
- Waves 4–8: provider, policy, monitoring, and catalog extensions
- Waves 9–10: CNPG operator and Cluster Applications (**manual**, not synced by `up`)

Sync waves do not prove cross-Application readiness. The bootstrap's direct PostgreSQL/OpenSearch Service probes provide that guarantee. To install the optional future hosted-application database infrastructure, run `nu scripts/local-setup.nu future-infra` explicitly.

**Phase 3 (Post-Deployment Configuration):**
After all core apps reach their required state, the script runs automated post-deployment steps:
- **configure_gitea**: Registers the self-signed CA in Gitea's trust store, adds Keycloak as an OIDC provider via the `gitea admin auth` CLI, creates `digiorgadmin` and `digiorgdeveloper` users, and creates the `DigiOrg` organisation via the `tea` CLI.
- **configure_sonarqube**: Waits for SonarQube to report `UP`, sets `serverBaseURL`, pushes all `sonar.auth.saml.*` settings via the Settings API, and enables SAML.
- **restart_oidc_dependent_pods**: Restarts ArgoCD Server, Grafana, Backstage, and Landing Page to pick up updated OIDC configuration.
- **patch_argocd_oidc_ca**: Embeds the self-signed CA cert in the ArgoCD Helm release via `helm upgrade --reuse-values`, saves the cert to `./digiorg-local-ca.crt`.

> **Note:** Common `make` shortcuts are available — run `make help` to see them.

### Trust the Self-Signed CA Certificate

The platform uses a self-signed CA certificate for `digiorg.local`. To avoid browser warnings,
import the CA cert into your OS trust store:

> **Note:** `nu scripts/local-setup.nu up` automatically saves the cert to `./digiorg-local-ca.crt`. The `kubectl` command below is an alternative if you need to extract it manually.

```bash
# Extract CA cert from cluster
kubectl get secret digiorg-local-ca-secret -n cert-manager \
  -o jsonpath='{.data.ca\.crt}' | base64 -d > digiorg-local-ca.crt

# macOS
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain digiorg-local-ca.crt

# Linux (Ubuntu/Debian)
sudo cp digiorg-local-ca.crt /usr/local/share/ca-certificates/
sudo update-ca-certificates

# Windows
certutil -addstore -f "ROOT" digiorg-local-ca.crt
```

> **Note:** Restart your browser after importing the CA certificate.

### Access Services

All services are accessible via `https://digiorg.local/<service>`.
HTTP (`http://`) automatically redirects to HTTPS.

| Service | URL | Credentials |
|---------|-----|-------------|
| **Landing Page** | https://digiorg.local/ | Login via Keycloak |
| Keycloak | https://digiorg.local/keycloak | admin / admin |
| ArgoCD | https://digiorg.local/argocd | Login via Keycloak |
| Grafana | https://digiorg.local/grafana | Login via Keycloak |
| Backstage | https://digiorg.local/backstage | Login via Keycloak or Guest |
| Gitea | https://digiorg.local/gitea | `gitea_admin` (see note below) |
| SonarQube | https://digiorg.local/sonarqube | admin / admin — change immediately |
| Jaeger | https://digiorg.local/jaeger | Login via Keycloak |

**Gitea Admin Password:**
```bash
kubectl get secret gitea-admin-secret -n gitea -o jsonpath='{.data.password}' | base64 -d && echo
```

> **Note:** Keycloak OIDC is auto-configured by `local-setup.nu` (Phase 3). Use **Login via Keycloak** or the `gitea_admin` account.

### Set Kubeconfig

```bash
export KUBECONFIG=$(pwd)/kubeconfig-local.yaml
```

### Check Status

```bash
nu scripts/local-setup.nu status
```

### Stop the Cluster

```bash
nu scripts/local-setup.nu down
```

### Reset the Cluster

```bash
nu scripts/local-setup.nu reset
```

## Architecture

### App-of-Apps Pattern

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    Setup Script (Phase 1)                               │
│                                                                         │
│  KinD → Ingress → CoreDNS → Secrets → ArgoCD (Helm)                    │
│       → PostgreSQL/OpenSearch functional gates → Root App              │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    ArgoCD (Phase 2)                                     │
│                                                                         │
│  Root App creates all 27 child Application CRs:                        │
│      Wave -1: namespaces                                                │
│      Wave  0: cert-manager, external-secrets, nats, opensearch,         │
│               postgresql                                               │
│      Wave  1: argocd, keycloak                                         │
│      Wave  2: backstage, gitea, grafana, harbor, jaeger, landingpage,   │
│               opencost, sonarqube                                      │
│      Wave  3: crossplane, kyverno                                      │
│      Waves 4-8: providers, logging, policies, monitoring, catalog       │
│      Waves 9-10: cnpg, cnpg-cluster (manual; `future-infra` only)       │
│                                                                         │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    Post-Deployment (Phase 3)                            │
│                                                                         │
│  configure_gitea → configure_sonarqube → restart_oidc_dependent_pods   │
│  → patch_argocd_oidc_ca                                                 │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### Sync Waves

| Wave | Applications | Dependencies |
|------|--------------|--------------|
| bootstrap | root-app | Deployed by script after core data-layer gates |
| -1 | namespaces | Pre-creates every platform namespace |
| 0 | cert-manager, external-secrets, nats, opensearch, postgresql | Ingress, CoreDNS, Secrets (foundational services) |
| 1 | keycloak, argocd | keycloak: postgresql, cert-manager; argocd: Ingress (self-managed after Helm install) |
| 2 | landingpage, backstage, gitea, grafana, harbor, jaeger, opencost, sonarqube | keycloak (OIDC/SAML); SQL consumers: postgresql; jaeger: opensearch |
| 3 | crossplane, kyverno | All platform services healthy |
| 4 | crossplane-providers, fluentd, kyverno-policies | Provider, log shipping, and policy extensions |
| 5 | monitoring-extras | Monitoring CRDs ready |
| 6 | crossplane-provider-configs | Crossplane providers ready |
| 7 | crossplane-xrds | Provider configurations ready |
| 8 | core-catalog | XRDs registered |
| 9 | cnpg | Manual optional future-app database operator |
| 10 | cnpg-cluster | Manual optional future-app database cluster |

The normal `up` path does not sync waves 9–10. Run `nu scripts/local-setup.nu future-infra` explicitly; it waits for the operator and admission webhook before syncing the Cluster.

### Phase 3: Post-Deployment Configuration

`local-setup.nu up` automatically runs the following after all core ArgoCD apps reach their required state:

| Step | Function | What it does |
|------|----------|--------------|
| 1 | `configure_gitea` | Registers the self-signed CA in Gitea's trust store; adds Keycloak as an OIDC provider via `gitea admin auth add-oauth`; creates `digiorgadmin` + `digiorgdeveloper` users; creates the `DigiOrg` organisation via the `tea` CLI |
| 2 | `configure_sonarqube` | Waits for `status: UP`; sets `serverBaseURL`; pushes all `sonar.auth.saml.*` settings via the Settings API; enables SAML |
| 3 | `restart_oidc_dependent_pods` | Restarts ArgoCD Server, Grafana, Backstage, and Landing Page to pick up the updated OIDC/Keycloak configuration |
| 4 | `patch_argocd_oidc_ca` | Embeds the self-signed CA cert in the ArgoCD Helm release via `helm upgrade --reuse-values`; saves `./digiorg-local-ca.crt` |

## Development Workflow

### 1. Make Changes to Platform Components

Edit files in `platform/base/<component>/`

### 2. Commit and Push

```bash
git add -A
git commit -m "feat(backstage): Update configuration"
git push
```

### 3. ArgoCD Auto-Syncs

ArgoCD detects the change and syncs automatically (selfHeal enabled).

### 4. Monitor Sync Status

```bash
# CLI
kubectl get applications -n argocd

# UI
open https://digiorg.local/argocd
```

### 5. Manual Sync (if needed)

```bash
# Force sync specific app
kubectl patch application backstage -n argocd \
  --type merge \
  -p '{"operation":{"initiatedBy":{"username":"admin"},"sync":{}}}'
```

## Adding New Components

1. Create manifests in `platform/base/<component>/`
2. Create ArgoCD Application in `apps/platform/<component>.yaml`
3. Set appropriate sync wave based on dependencies
4. Commit and push — ArgoCD will sync automatically

Example Application:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: my-component
  namespace: argocd
  annotations:
    argocd.argoproj.io/sync-wave: "2"
spec:
  project: default
  source:
    repoURL: git@github.com:digiorg/core.git
    path: platform/base/my-component
  destination:
    server: https://kubernetes.default.svc
    namespace: my-component
  syncPolicy:
    automated:
      selfHeal: true
      prune: true
```

> **Note:** The example uses an SSH URL (`git@github.com:digiorg/core.git`), which requires SSH key setup. HTTPS alternative: `https://github.com/digiorg/core.git`

## Troubleshooting

### Cluster Won't Start

```bash
# Check Docker
docker info

# Delete and recreate
nu scripts/local-setup.nu reset
```

### ArgoCD Apps Not Syncing

```bash
# Check ArgoCD UI
open https://digiorg.local/argocd

# Check app status
kubectl get applications -n argocd -o wide

# Check specific app
kubectl describe application <app-name> -n argocd

# Check controller logs
kubectl logs -n argocd -l app.kubernetes.io/name=argocd-application-controller
```

### Services Not Accessible

```bash
# Check /etc/hosts
cat /etc/hosts | grep digiorg

# Check ingress controller
kubectl get pods -n ingress-nginx

# Check ingress rules
kubectl get ingress -A
```

### Keycloak Login Fails

```bash
# Check Keycloak is ready
kubectl get pods -n keycloak

# Check realm exists
curl -s https://digiorg.local/keycloak/realms/digiorg-core-platform | jq .realm
```

## Resource Usage

The local cluster uses approximately:

| Component | CPU | Memory |
|-----------|-----|--------|
| KinD Node | 2 cores | 4 GB |
| SonarQube | 200m | 2Gi request (4Gi limit — includes embedded Elasticsearch) |
| Prometheus + Grafana | 0.5 cores | 1 GB |
| Keycloak | 0.4 cores | 768 MB |
| Backstage | 0.4 cores | 768 MB |
| Shared PostgreSQL | 0.3 cores | 512 MB |
| ArgoCD | 0.5 cores | 512 MB |
| OpenSearch | 250m | 512Mi request (1Gi limit) |
| Crossplane | 0.2 cores | 256 MB |
| Kyverno | 0.2 cores | 256 MB |
| Jaeger + oauth2-proxy | ~100m | ~256Mi |
| NATS + Surveyor | ~75m | ~96Mi |
| External Secrets | ~25m | ~64Mi |
| Landing Page | 10m | 32Mi |

**Recommended:** At least 16 GB RAM allocated to Docker (SonarQube alone requests 2Gi with a 4Gi limit for its embedded Elasticsearch).
