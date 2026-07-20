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
| [Argo CD CLI](https://argo-cd.readthedocs.io/en/stable/cli_installation/) | v3.4.5 (matches server) | Required — final readiness verifies a stale `Healthy/OutOfSync` Application via `argocd app diff --core`; a missing or mismatched CLI fails the preflight instead of silently stalling bootstrap readiness (Issue #281) |

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

This bootstraps the cluster in three phases:

**Phase 1 — Bootstrap (this script):**

| Step | Component | Notes |
|------|-----------|-------|
| 1.1 | KinD cluster (`digiorg-core-dev`) | Single-node local cluster |
| 1.2 | `vm.max_map_count=262144` | Required by OpenSearch |
| 1.3 | Gateway API CRDs | Kubernetes Gateway API |
| 1.4 | NGINX Ingress Controller | Ingress for all services |
| 1.5 | Platform Ingress rules | Routes for `digiorg.local/*` |
| 1.6 | CoreDNS | Configured for `digiorg.local` |
| 1.7 | Platform Secrets | 10 secrets across 7 namespaces |
| 1.8 | ArgoCD | Deployed via Helm |

**Phase 2 — ArgoCD App-of-Apps (ArgoCD manages these):**

| Wave | Components |
|------|-----------|
| Wave 0 | cert-manager, external-secrets, nats, opensearch, postgresql |
| Wave 1 | keycloak, argocd |
| Wave 2 | backstage, gitea, grafana, jaeger, landingpage, sonarqube |
| Wave 3 | crossplane, kyverno |

**Phase 3 — Post-Deployment (automated by setup script):**

- `configure_gitea` — OIDC auto-config, initial users, DigiOrg organisation
- `configure_sonarqube` — SAML integration via Settings API
- Restart OIDC-dependent pods

> **Makefile shortcuts:** `make up` / `make down` / `make reset` / `make status` wrap the `nu scripts/local-setup.nu` commands above.

### 3.5. Import CA Certificate

After `nu scripts/local-setup.nu up` completes, a self-signed CA certificate is saved to `./digiorg-local-ca.crt` in the repository root. Import it into your OS trust store so the browser accepts `https://digiorg.local`:

**macOS:**
```bash
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain ./digiorg-local-ca.crt
```

**Linux:**
```bash
sudo cp ./digiorg-local-ca.crt /usr/local/share/ca-certificates/digiorg-local-ca.crt
sudo update-ca-certificates
```

**Windows (PowerShell as Administrator):**
```powershell
certutil -addstore -f "ROOT" .\digiorg-local-ca.crt
```

Restart your browser after importing.

### 4. Access Services

All services are available via `https://digiorg.local/<service>` (TLS via self-signed CA — see Step 3.5):

| Service | URL | Login |
|---------|-----|-------|
| Landing Page | https://digiorg.local/ | — |
| Keycloak | https://digiorg.local/keycloak | admin / admin |
| ArgoCD | https://digiorg.local/argocd | Login via Keycloak |
| Grafana | https://digiorg.local/grafana | Login via Keycloak |
| Backstage | https://digiorg.local/backstage | Login via Keycloak |
| Gitea | https://digiorg.local/gitea | Login via Keycloak (see note) |
| SonarQube | https://digiorg.local/sonarqube | admin / admin — **change immediately** |
| Jaeger | https://digiorg.local/jaeger | Login via Keycloak |

> **Gitea admin password:** The initial admin password is stored in a cluster secret:
> ```bash
> kubectl get secret gitea-admin-secret -n gitea \
>   -o jsonpath='{.data.password}' | base64 -d && echo
> ```

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

### Gitea (Source Control)

Gitea provides self-hosted Git hosting:

- OIDC integration auto-configured by the setup script
- DigiOrg organisation and initial users created during Phase 3
- Admin password retrievable via `kubectl get secret gitea-admin-secret -n gitea`

### SonarQube (Code Quality)

SonarQube provides static code analysis and SAST:

- Keycloak SAML configured automatically during Phase 3
- First login: admin / admin — **change the password immediately**

### Jaeger (Distributed Tracing)

Jaeger provides end-to-end distributed tracing:

- Backed by OpenSearch for trace storage
- Keycloak OIDC via oauth2-proxy

### OpenSearch

OpenSearch serves as the trace and log storage backend:

- Deployed in the `platform-db` namespace
- `vm.max_map_count=262144` set on the KinD node during bootstrap (required)

### cert-manager

cert-manager handles TLS certificate issuance:

- Self-signed CA for `digiorg.local` (certificate saved to `./digiorg-local-ca.crt`)
- Manages certificates for all platform ingress routes

### External Secrets Operator

External Secrets Operator bridges Kubernetes secrets with external vaults:

- Provider-swap pattern: Fake provider for local development, Azure Key Vault or HashiCorp Vault for production

### NATS JetStream

NATS provides persistent pub/sub messaging:

- Deployed in the `messaging` namespace
- JetStream persistence enabled

### Shared PostgreSQL

A single PostgreSQL instance serves multiple platform components:

- Databases for Keycloak, Backstage, Gitea, and SonarQube

### Kyverno (Policy Engine)

Kyverno enforces Policy-as-Code across the cluster:

- Admission controller for policy validation and mutation

### Crossplane (Infrastructure as Code)

Crossplane manages cloud infrastructure from Kubernetes:

- Enables declarative provisioning of external resources

### Observability Stack

Prometheus + Grafana + Jaeger + OpenSearch provide the three-pillar observability model:

- **Metrics** — Prometheus scrapes cluster and application metrics; Grafana provides pre-configured dashboards with Keycloak OAuth
- **Traces** — Jaeger collects distributed traces, backed by OpenSearch
- **Logs** — OpenSearch indexes platform logs

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
