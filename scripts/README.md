# Scripts

This directory contains automation scripts for the DigiOrg Core Platform.

## Prerequisites

- [Nushell](https://www.nushell.sh/) >= 0.90
- [Docker](https://www.docker.com/) >= 20.10
- [kubectl](https://kubernetes.io/docs/tasks/tools/) >= 1.28
- [Helm](https://helm.sh/) >= 3.12
- [KinD](https://kind.sigs.k8s.io/) >= 0.20

## Scripts

### local-setup.nu

Manages the local KinD development cluster using the **App-of-Apps pattern**.

```bash
# Bootstrap cluster and deploy ArgoCD root app
nu scripts/local-setup.nu up

# Destroy local cluster
nu scripts/local-setup.nu down

# Reset cluster (down + up)
nu scripts/local-setup.nu reset

# Show cluster and ArgoCD app status
nu scripts/local-setup.nu status

# Run only Phase 1 bootstrap (no root app)
nu scripts/local-setup.nu bootstrap
```

## Architecture

The setup follows a two-phase approach:

### Phase 1: Bootstrap (Setup Script)

The script installs only the minimal infrastructure needed to run ArgoCD:

1. **KinD Cluster** (`digiorg-core-dev`)
2. **Gateway API CRDs**
3. **NGINX Ingress Controller**
4. **Platform Ingress** (unified routing via `digiorg.local`)
5. **CoreDNS Patch** (internal `digiorg.local` resolution)
6. **Platform Secrets** (shared PostgreSQL credentials + per-service secrets)
   - `platform-db/postgresql-secrets`: Shared PostgreSQL superuser and per-database passwords
   - `backstage/backstage-secrets`: Bootstrap application secret
   - `keycloak/keycloak-db-credentials`: Keycloak PostgreSQL database credentials
   - `gitea/gitea-secrets`: PostgreSQL password, OIDC client secret
   - `gitea/gitea-admin-secret`: Admin username and randomly generated password
7. **ArgoCD** (Helm install)
8. **Root App** (triggers App-of-Apps)

### Phase 2: App-of-Apps (ArgoCD)

ArgoCD takes over and deploys all platform components via sync waves:

| Wave | Applications | Description |
|------|--------------|-------------|
| -1 | root-app | Bootstrap (deployed by script) |
| 0 | postgresql | Shared database (namespace: `platform-db`) |
| 1 | keycloak, argocd | Core infrastructure (Keycloak depends on PostgreSQL; ArgoCD is also synced in this wave) |
| 2 | gitea, backstage, monitoring | Platform services (depend on PostgreSQL + Keycloak) |
| 3 | crossplane, kyverno | Extensions |

## Service Access

After `up` completes, access services via:

| Service | URL | Credentials |
|---------|-----|-------------|
| Keycloak | http://digiorg.local/keycloak | admin / admin |
| ArgoCD | http://digiorg.local/argocd | Login via Keycloak |
| Grafana | http://digiorg.local/grafana | Login via Keycloak |
| Backstage | http://digiorg.local/backstage | Login via Keycloak or Guest |
| Gitea | http://digiorg.local/gitea | `gitea_admin` (password from secret) |

**Note:** Requires `/etc/hosts` entry: `127.0.0.1 digiorg.local`

## Cluster Name

The local cluster is named `digiorg-core-dev`.

```bash
# Check if cluster exists
kind get clusters | grep digiorg-core-dev

# Get kubeconfig
export KUBECONFIG=$(pwd)/kubeconfig-local.yaml
```

## Troubleshooting

### Cluster won't start

```bash
# Check Docker
docker info

# Clean up old clusters
kind delete cluster --name digiorg-core-dev

# Try again
nu scripts/local-setup.nu up
```

### ArgoCD apps not syncing

```bash
# Check ArgoCD UI
open http://digiorg.local/argocd

# Check app status
kubectl get applications -n argocd

# Check app logs
kubectl logs -n argocd -l app.kubernetes.io/name=argocd-application-controller
```

### Component not ready

```bash
# Check specific namespace
kubectl get pods -n <namespace>

# View logs
kubectl logs -n <namespace> -l app=<app-name>

# Check ArgoCD app details
kubectl describe application <app-name> -n argocd
```
