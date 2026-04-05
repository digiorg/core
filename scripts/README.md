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

Manages the local KinD development cluster.

```bash
# Start local cluster with all components
nu scripts/local-setup.nu up

# Destroy local cluster
nu scripts/local-setup.nu down

# Reset cluster (down + up)
nu scripts/local-setup.nu reset

# Show cluster status
nu scripts/local-setup.nu status

# Install specific components only
nu scripts/local-setup.nu install --components argocd,keycloak
```

#### What `up` installs

1. **KinD Cluster** (`digiorg-core-dev`)
2. **Gateway API CRDs**
3. **NGINX Ingress Controller**
4. **Platform Ingress** (unified routing via `digiorg.local`)
5. **CoreDNS Patch** (internal `digiorg.local` resolution)
6. **Keycloak** (Identity Provider with PostgreSQL)
7. **ArgoCD** (GitOps with Keycloak SSO)
8. **Crossplane** (Infrastructure as Code)
9. **Kyverno** (Policy Engine)
10. **Prometheus + Grafana** (Monitoring with Keycloak OAuth)
11. **Backstage** (Developer Portal with Keycloak OIDC)

#### Service Access

After `up` completes, access services via:

| Service | URL | Credentials |
|---------|-----|-------------|
| Keycloak | http://digiorg.local/keycloak | admin / admin |
| ArgoCD | http://digiorg.local/argocd | Login via Keycloak |
| Grafana | http://digiorg.local/grafana | Login via Keycloak |
| Backstage | http://digiorg.local/backstage | Login via Keycloak or Guest |

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

### Services not accessible

```bash
# Check ingress controller
kubectl get pods -n ingress-nginx

# Check /etc/hosts
cat /etc/hosts | grep digiorg
```

### Component not ready

```bash
# Check specific namespace
kubectl get pods -n <namespace>

# View logs
kubectl logs -n <namespace> -l app=<app-name>
```
