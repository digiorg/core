# Platform

This directory contains the platform infrastructure configuration.

## Structure

```
platform/
├── bootstrap/           # Cluster bootstrap configuration
│   └── kind-config.yaml # KinD cluster configuration
└── base/                # Base Kustomize configurations
    ├── argocd/          # ArgoCD with Keycloak SSO
    ├── backstage/       # Backstage Developer Portal
    ├── cert-manager/    # TLS certificate management (self-signed + Let's Encrypt)
    ├── crossplane/      # Crossplane setup
    ├── gitea/           # Gitea Git Service
    ├── ingress/         # NGINX Ingress + unified routing + TLS termination
    ├── keycloak/        # Keycloak IdP (uses shared PostgreSQL)
    ├── kyverno/         # Policy Engine
    ├── landingpage/     # Platform Landing Page with SSO
    ├── monitoring/      # Prometheus + Grafana
    └── postgresql/      # Shared PostgreSQL (Keycloak + Backstage + Gitea)
```

## Components

### bootstrap/

Contains the KinD cluster configuration for local development:

- **kind-config.yaml**: Cluster named `digiorg-core-dev` with port mappings for HTTP/HTTPS ingress

### base/

Kustomize bases for all platform components:

| Component | Description | Authentication | Wave |
|-----------|-------------|----------------|------|
| cert-manager | TLS certificate issuance + renewal | - | 0 |
| postgresql | Shared PostgreSQL database | - | 0 |
| argocd | GitOps Continuous Delivery | Keycloak OIDC | 1 |
| keycloak | Identity Provider | Built-in | 1 |
| landingpage | Platform Entry Point | Keycloak OIDC (public client) | 2 |
| backstage | Internal Developer Portal | Keycloak OIDC / Guest | 2 |
| gitea | Self-hosted Git Service | Local admin; Keycloak OIDC (manual config) | 2 |
| monitoring | Prometheus + Grafana | Keycloak OAuth | 2 |
| crossplane | Infrastructure as Code | - | 3 |
| ingress | NGINX Ingress + TLS termination | - | - |
| kyverno | Policy Engine | - | 3 |

**Note:** cert-manager provisions a self-signed CA for `digiorg.local` (local dev) and supports Let's Encrypt for staging/production. See `platform/base/cert-manager/README.md`.

**Note:** PostgreSQL runs as a shared StatefulSet in the `platform-db` namespace, serving Keycloak, Backstage, and Gitea databases.

## Service Access

All services are accessible via unified ingress at `https://digiorg.local`:

| Path | Service | Namespace |
|------|---------|-----------|
| `/` | Landing Page | platform-apps |
| `/keycloak` | Keycloak | keycloak |
| `/argocd` | ArgoCD | argocd |
| `/grafana` | Grafana | monitoring |
| `/backstage` | Backstage | backstage |
| `/gitea` | Gitea | gitea |

## Usage

### Local Development

```bash
# Start everything
nu scripts/local-setup.nu up

# Apply changes to a specific component
kubectl apply -k platform/base/backstage/

# Check status
kubectl get pods -n backstage
```

### Production

For production, use overlays or Helm values to customize:

```bash
# Example: Apply with custom values
kubectl apply -k platform/overlays/production/
```

## Adding New Components

1. Create directory under `base/`
2. Add Kustomize files (kustomization.yaml, resources)
3. Update `scripts/local-setup.nu` if needed
4. Document in this README
