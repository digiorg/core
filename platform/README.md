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
    ├── crossplane/      # Crossplane setup
    ├── ingress/         # NGINX Ingress + unified routing
    ├── keycloak/        # Keycloak IdP (uses shared PostgreSQL)
    ├── kyverno/         # Policy Engine
    ├── monitoring/      # Prometheus + Grafana
    └── postgresql/      # Shared PostgreSQL (Keycloak + Backstage)
```

## Components

### bootstrap/

Contains the KinD cluster configuration for local development:

- **kind-config.yaml**: Cluster named `digiorg-core-dev` with port mappings for HTTP/HTTPS ingress

### base/

Kustomize bases for all platform components:

| Component | Description | Authentication |
|-----------|-------------|----------------|
| argocd | GitOps Continuous Delivery | Keycloak OIDC |
| backstage | Internal Developer Portal | Keycloak OIDC / Guest |
| crossplane | Infrastructure as Code | - |
| ingress | NGINX Ingress + routing rules | - |
| keycloak | Identity Provider | Built-in |
| kyverno | Policy Engine | - |
| monitoring | Prometheus + Grafana | Keycloak OAuth |
| postgresql | Shared PostgreSQL database | - |

**Note:** PostgreSQL runs as a shared StatefulSet in the `platform-db` namespace, serving both Keycloak and Backstage databases.

## Service Access

All services are accessible via unified ingress at `http://digiorg.local`:

| Path | Service | Namespace |
|------|---------|-----------|
| `/keycloak` | Keycloak | keycloak |
| `/argocd` | ArgoCD | argocd |
| `/grafana` | Grafana | monitoring |
| `/backstage` | Backstage | backstage |

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
