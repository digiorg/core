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

This runs in two phases:

**Phase 1 (Bootstrap):**
1. Create a KinD cluster (`digiorg-core-dev`)
2. Install Gateway API CRDs
3. Install NGINX Ingress Controller
4. Configure CoreDNS for `digiorg.local`
5. Create platform secrets
6. Install ArgoCD (Helm)
7. Deploy root-app

**Phase 2 (App-of-Apps):**
ArgoCD syncs all platform components via sync waves:
- Wave 1: Keycloak, ArgoCD (self-managed)
- Wave 2: Backstage, Monitoring
- Wave 3: Crossplane, Kyverno

### Access Services

All services are accessible via `http://digiorg.local/<service>`:

| Service | URL | Credentials |
|---------|-----|-------------|
| Keycloak | http://digiorg.local/keycloak | admin / admin |
| ArgoCD | http://digiorg.local/argocd | Login via Keycloak |
| Grafana | http://digiorg.local/grafana | Login via Keycloak |
| Backstage | http://digiorg.local/backstage | Login via Keycloak or Guest |

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
│  KinD → Ingress → CoreDNS → Secrets → ArgoCD (Helm) → Root App         │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    ArgoCD (Phase 2)                                     │
│                                                                         │
│  Root App discovers apps/ directory                                     │
│      │                                                                  │
│      ├── apps/platform/keycloak.yaml    → Wave 1                       │
│      ├── apps/platform/argocd.yaml      → Wave 1 (self-managed)        │
│      ├── apps/platform/backstage.yaml   → Wave 2                       │
│      ├── apps/observability/monitoring.yaml → Wave 2                   │
│      ├── apps/platform/crossplane.yaml  → Wave 3                       │
│      └── apps/platform/kyverno.yaml     → Wave 3                       │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### Sync Waves

| Wave | Applications | Dependencies |
|------|--------------|--------------|
| -1 | root-app | Bootstrap (deployed by script) |
| 1 | keycloak, argocd | Ingress, Secrets |
| 2 | backstage, monitoring | Keycloak (OIDC) |
| 3 | crossplane, kyverno | None |

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
open http://digiorg.local/argocd
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
open http://digiorg.local/argocd

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
curl -s http://digiorg.local/keycloak/realms/digiorg-core-platform | jq .realm
```

## Resource Usage

The local cluster uses approximately:

| Component | CPU | Memory |
|-----------|-----|--------|
| KinD Node | 2 cores | 4 GB |
| Keycloak + PostgreSQL | 0.5 cores | 1 GB |
| ArgoCD | 0.5 cores | 512 MB |
| Crossplane | 0.2 cores | 256 MB |
| Kyverno | 0.2 cores | 256 MB |
| Prometheus + Grafana | 0.5 cores | 1 GB |
| Backstage + PostgreSQL | 0.5 cores | 1 GB |

**Recommended:** At least 8 GB RAM allocated to Docker.
