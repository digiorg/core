# Getting Started

This guide walks you through setting up the DigiOrg Core Platform.

## Prerequisites

### Required Tools

| Tool | Version | Purpose |
|------|---------|---------|
| [Docker](https://www.docker.com/products/docker-desktop) | >= 20.10 | Container runtime |
| [kubectl](https://kubernetes.io/docs/tasks/tools/) | >= 1.28 | Kubernetes CLI |
| [Helm](https://helm.sh/docs/intro/install/) | >= 3.12 | Package manager |
| [KinD](https://kind.sigs.k8s.io/) | >= 0.20 | Local Kubernetes |
| [Nushell](https://www.nushell.sh/book/installation.html) | >= 0.90 | Setup scripts |

### Optional Tools

| Tool | Purpose |
|------|---------|
| [k9s](https://k9scli.io/) | Terminal UI for Kubernetes |
| [Terraform](https://www.terraform.io/downloads) | Cloud infrastructure (for production) |

## Quick Start (Local Development)

### 1. Clone the Repository

```bash
git clone https://github.com/digiorg/core.git
cd core
```

### 2. Configure Host Entry

Add to `/etc/hosts` (Linux/Mac) or `C:\Windows\System32\drivers\etc\hosts` (Windows):

```
127.0.0.1 digiorg.local
```

### 3. Start Local Cluster

```bash
nu scripts/local-setup.nu up
```

This creates a KinD cluster (`digiorg-core-dev`) and installs:
- **Keycloak** — Identity Provider (SSO for all services)
- **ArgoCD** — GitOps Continuous Delivery
- **Crossplane** — Infrastructure as Code
- **Kyverno** — Policy Engine
- **Prometheus + Grafana** — Monitoring
- **Backstage** — Developer Portal

### 4. Access Services

All services are available via `http://digiorg.local/<service>`:

| Service | URL | Login |
|---------|-----|-------|
| Keycloak | http://digiorg.local/keycloak | admin / admin |
| ArgoCD | http://digiorg.local/argocd | via Keycloak |
| Grafana | http://digiorg.local/grafana | via Keycloak |
| Backstage | http://digiorg.local/backstage | via Keycloak or Guest |

### 5. Explore the Platform

```bash
# Set kubeconfig
export KUBECONFIG=$(pwd)/kubeconfig-local.yaml

# Check all components
kubectl get pods -A

# View ArgoCD applications
kubectl get applications -n argocd

# Check cluster status
nu scripts/local-setup.nu status
```

### 6. Clean Up

```bash
nu scripts/local-setup.nu down
```

## Platform Components

### Keycloak (Identity Provider)

Keycloak provides centralized authentication:

- **Realm:** `digiorg-core-platform`
- All services (ArgoCD, Grafana, Backstage) authenticate via OIDC
- Pre-configured clients for each service

### ArgoCD (GitOps)

ArgoCD manages deployments from Git:

- Configured with Keycloak SSO
- App-of-Apps pattern for managing platform components

### Backstage (Developer Portal)

Backstage provides the Internal Developer Portal:

- Service Catalog
- Tech Docs
- Kubernetes plugin for cluster visibility

### Monitoring Stack

Prometheus + Grafana provide observability:

- Pre-configured dashboards
- Grafana authenticated via Keycloak OAuth

## Production Deployment

For production deployment on cloud providers:

### 1. Configure Cloud Credentials

#### AWS

```bash
export AWS_ACCESS_KEY_ID="your-access-key"
export AWS_SECRET_ACCESS_KEY="your-secret-key"
export AWS_REGION="eu-central-1"
```

#### Azure

```bash
az login
az account set --subscription "your-subscription-id"
```

#### GCP

```bash
gcloud auth login
gcloud config set project your-project-id
```

### 2. Configure Terraform Backend

Create `terraform/backend.tf`:

```hcl
terraform {
  backend "s3" {
    bucket         = "your-terraform-state-bucket"
    key            = "digiorg/terraform.tfstate"
    region         = "eu-central-1"
    encrypt        = true
    dynamodb_table = "terraform-locks"
  }
}
```

### 3. Create Management Cluster

```bash
cd terraform/modules/aws
terraform init
terraform apply -var="cluster_name=management" -var="environment=production"
```

### 4. Bootstrap Platform

Follow the same component installation as local setup, but point to your production cluster.

## Next Steps

- [Architecture Overview](../architecture.md)
- [Local Development Guide](./local-development.md)
- [ADR-001: Bootstrap Framework](../adr/001-bootstrap-framework-architecture.md)

## Troubleshooting

### ArgoCD not syncing

```bash
# Check ArgoCD logs
kubectl logs -n argocd -l app.kubernetes.io/name=argocd-application-controller

# Force sync
kubectl -n argocd patch application <app-name> \
  --type merge \
  -p '{"operation":{"initiatedBy":{"username":"admin"},"sync":{}}}'
```

### Keycloak not reachable

```bash
# Check Keycloak pod
kubectl get pods -n keycloak

# Check logs
kubectl logs -n keycloak -l app=keycloak
```

### Backstage won't start

```bash
# Check pod events
kubectl describe pod -n backstage -l app=backstage

# Check logs
kubectl logs -n backstage -l app=backstage --tail=100
```
