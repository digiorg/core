# Local Development Guide

This guide explains how to set up and use the local development environment.

## Overview

The local development environment uses [KinD](https://kind.sigs.k8s.io/) (Kubernetes in Docker) to run a fully-functional platform locally. This enables:

- Testing changes before pushing to production
- Developing new features without cloud costs
- Running the full platform stack locally

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

This will:
1. Create a KinD cluster named `digiorg-core-dev`
2. Install NGINX Ingress Controller
3. Configure CoreDNS for internal `digiorg.local` resolution
4. Install Keycloak (IdP with pre-configured realm)
5. Install ArgoCD (with Keycloak SSO)
6. Install Crossplane
7. Install Kyverno
8. Install Prometheus + Grafana (with Keycloak OAuth)
9. Install Backstage Developer Portal (with Keycloak OIDC)

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

## Cluster Configuration

The KinD cluster is configured in `platform/bootstrap/kind-config.yaml`:

```yaml
name: digiorg-core-dev
nodes:
  - role: control-plane
    extraPortMappings:
      - containerPort: 80
        hostPort: 80        # HTTP ingress (digiorg.local)
      - containerPort: 443
        hostPort: 443       # HTTPS ingress
```

## Installing Individual Components

```bash
# Install specific components
nu scripts/local-setup.nu install --components ingress
nu scripts/local-setup.nu install --components keycloak
nu scripts/local-setup.nu install --components argocd
nu scripts/local-setup.nu install --components crossplane
nu scripts/local-setup.nu install --components kyverno
nu scripts/local-setup.nu install --components monitoring
nu scripts/local-setup.nu install --components backstage
```

## Platform Services

### Keycloak (Identity Provider)

Keycloak provides SSO for all platform components:

- **Realm:** `digiorg-core-platform`
- **Admin Console:** http://digiorg.local/keycloak/admin
- **Realm Settings:** Pre-configured with clients for ArgoCD, Grafana, Backstage

**Pre-configured Users:**
| Username | Password | Role |
|----------|----------|------|
| admin | admin | Keycloak Admin |

### ArgoCD (GitOps)

ArgoCD is configured with Keycloak OIDC:

- **URL:** http://digiorg.local/argocd
- **Login:** Click "Login via Keycloak"

### Grafana (Monitoring)

Grafana is configured with Keycloak OAuth:

- **URL:** http://digiorg.local/grafana
- **Login:** Click "Sign in with Keycloak"

### Backstage (Developer Portal)

Backstage supports both Guest and Keycloak login:

- **URL:** http://digiorg.local/backstage
- **Login:** Choose "Guest" or "Keycloak SSO"

## Development Workflow

### 1. Make Changes

Edit files in the repository (platform/base/, policies/, crossplane/, etc.)

### 2. Apply Changes

```bash
# Apply Kubernetes manifests
kubectl apply -k platform/base/backstage/

# Or let ArgoCD sync (if configured)
```

### 3. Test Changes

```bash
# Check resources
kubectl get all -n backstage

# Check ArgoCD sync status
kubectl get applications -n argocd

# Check pod logs
kubectl logs -n backstage -l app=backstage
```

## Troubleshooting

### Cluster Won't Start

```bash
# Check Docker is running
docker info

# Check for existing clusters
kind get clusters

# Delete and recreate
nu scripts/local-setup.nu reset
```

### Services Not Accessible

```bash
# Check /etc/hosts has digiorg.local entry
cat /etc/hosts | grep digiorg

# Check ingress controller
kubectl get pods -n ingress-nginx

# Check service endpoints
kubectl get svc -A | grep -E "keycloak|argocd|grafana|backstage"
```

### Keycloak Login Fails

```bash
# Check Keycloak is ready
kubectl get pods -n keycloak

# Check Keycloak logs
kubectl logs -n keycloak -l app=keycloak

# Verify realm exists
curl -s http://digiorg.local/keycloak/realms/digiorg-core-platform | jq .realm
```

### Backstage Won't Start

```bash
# Check pod status
kubectl get pods -n backstage

# Check logs
kubectl logs -n backstage -l app=backstage

# Common issues:
# - PostgreSQL not ready (wait for init container)
# - OIDC metadata not reachable (check CoreDNS)
```

### Reset Everything

```bash
# Nuclear option: delete everything
nu scripts/local-setup.nu down
docker system prune -f
nu scripts/local-setup.nu up
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

## Tips & Tricks

### Use k9s for Terminal UI

```bash
export KUBECONFIG=$(pwd)/kubeconfig-local.yaml
k9s
```

### Watch Resources

```bash
# Watch pods in all namespaces
kubectl get pods -A -w

# Watch specific namespace
kubectl get pods -n backstage -w
```

### Quick Service Restart

```bash
# Restart a deployment
kubectl rollout restart deployment backstage -n backstage

# Wait for rollout
kubectl rollout status deployment backstage -n backstage
```
