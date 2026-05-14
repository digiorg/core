# Platform

This directory contains the platform infrastructure configuration.

## Structure

```
platform/
├── bootstrap/            # Cluster bootstrap configuration
│   └── kind-config.yaml  # KinD cluster configuration
└── base/                 # Base Kustomize configurations
    ├── argocd/           # ArgoCD with Keycloak SSO
    ├── backstage/        # Backstage Developer Portal
    ├── cert-manager/     # TLS certificate management (self-signed + Let's Encrypt)
    ├── crossplane/       # Crossplane setup
    ├── external-secrets/ # External Secrets Operator
    ├── gitea/            # Gitea Git Service
    ├── grafana/          # Prometheus + Grafana (Metrics & Dashboards)
    ├── ingress/          # NGINX Ingress + unified routing + TLS termination
    ├── jaeger/           # Distributed Tracing
    ├── keycloak/         # Keycloak IdP (uses shared PostgreSQL)
    ├── kyverno/          # Policy Engine
    ├── landingpage/      # Platform Landing Page with SSO
    ├── nats/             # Message Broker (JetStream)
    ├── opensearch/       # Observability Data Backend
    ├── postgresql/       # Shared PostgreSQL (Keycloak + Backstage + Gitea + SonarQube)
    └── sonarqube/        # Code Quality & Security
```

## Components

### bootstrap/

Contains the KinD cluster configuration for local development:

- **kind-config.yaml**: Cluster named `digiorg-core-dev` with port mappings for HTTP/HTTPS ingress

### base/

Kustomize bases for all platform components:

| Component | Description | Authentication | Wave | Namespace |
|-----------|-------------|----------------|------|-----------|
| cert-manager | TLS certificate issuance + renewal | — | 0 | cert-manager |
| external-secrets | ESO — syncs secrets from external backends | — | 0 | external-secrets |
| nats | Message Broker (JetStream pub/sub) | System account auth | 0 | messaging |
| opensearch | Observability data backend (Jaeger traces, future logs) | Security plugin disabled (dev) | 0 | platform-db |
| postgresql | Shared PostgreSQL database | — | 0 | platform-db |
| argocd | GitOps Continuous Delivery | Keycloak OIDC | 1 | argocd |
| keycloak | Identity Provider | Built-in | 1 | keycloak |
| backstage | Internal Developer Portal | Keycloak OIDC / Guest | 2 | backstage |
| gitea | Self-hosted Git Service | Keycloak OIDC (auto-configured) | 2 | gitea |
| grafana | Prometheus + Grafana | Keycloak OAuth | 2 | monitoring |
| jaeger | Distributed Tracing UI | Keycloak OIDC via oauth2-proxy | 2 | tracing |
| landingpage | Platform Entry Point | Keycloak OIDC (public client) | 2 | platform-apps |
| sonarqube | Code Quality & Security | Keycloak SAML | 2 | code-quality |
| crossplane | Infrastructure as Code | — | 3 | crossplane-system |
| kyverno | Policy Engine | — | 3 | kyverno |
| ingress | NGINX Ingress + TLS termination | — | bootstrap | ingress-nginx |

**Note:** cert-manager provisions a self-signed CA for `digiorg.local` (local dev) and supports Let's Encrypt for staging/production. See `platform/base/cert-manager/README.md`.

**Note:** PostgreSQL runs as a shared StatefulSet in the `platform-db` namespace, serving Keycloak, Backstage, Gitea, and SonarQube databases.

**Note:** Ingress is not managed as an ArgoCD Application — it is applied during cluster bootstrap by `local-setup.nu`. See `platform/base/ingress/README.md`.

### Observability Stack

The platform implements a three-pillar observability model:

| Pillar | Components | Wave |
|--------|------------|------|
| Metrics | Prometheus + Grafana | 2 |
| Traces | Jaeger + OpenSearch | 0 + 2 |
| Logs | Planned | — |

OpenSearch (wave 0) serves as the storage backend for Jaeger traces. Jaeger (wave 2) is fronted by oauth2-proxy to enforce Keycloak SSO before the UI is accessible. Future log aggregation will use the same OpenSearch backend.

### External Secrets Operator

ESO is deployed at wave 0 and enables a **provider-swap pattern**: all `ExternalSecret` resources across the platform reference a single `ClusterSecretStore` by name. Switching environments requires replacing only the `ClusterSecretStore` — no changes to any `ExternalSecret`:

- **Local dev**: `digiorg-dev-store` (Fake provider — static values, no backend required)
- **Production**: `digiorg-prod-store` (Azure Key Vault or HashiCorp Vault)

See `platform/base/external-secrets/README.md` for production `ClusterSecretStore` examples.

### NATS Messaging

NATS JetStream (wave 0, `messaging` namespace) provides persistent pub/sub messaging for platform services. The deployment includes NATS Surveyor, which exports Prometheus metrics for monitoring message flow and stream health.

## Service Access

All services are accessible via unified ingress at `https://digiorg.local`:

| Path | Service | Namespace |
|------|---------|-----------|
| `/` | Landing Page | platform-apps |
| `/keycloak` | Keycloak | keycloak |
| `/argocd` | ArgoCD | argocd |
| `/grafana` | Grafana | monitoring |
| `/jaeger` | Jaeger UI (via oauth2-proxy) | tracing |
| `/sonarqube` | SonarQube | code-quality |
| `/backstage` | Backstage | backstage |
| `/gitea` | Gitea | gitea |

See `platform/base/ingress/README.md` for routing details and ExternalName service configuration.

## Wave Deployment Order

ArgoCD sync-waves control the deployment sequence. All 15 ArgoCD-managed components are listed below in order; ingress is excluded as it is applied during cluster bootstrap by `local-setup.nu`.

| Wave | Component | Namespace |
|------|-----------|-----------|
| 0 | cert-manager | cert-manager |
| 0 | external-secrets | external-secrets |
| 0 | nats | messaging |
| 0 | opensearch | platform-db |
| 0 | postgresql | platform-db |
| 1 | argocd | argocd |
| 1 | keycloak | keycloak |
| 2 | backstage | backstage |
| 2 | gitea | gitea |
| 2 | grafana | monitoring |
| 2 | jaeger | tracing |
| 2 | landingpage | platform-apps |
| 2 | sonarqube | code-quality |
| 3 | crossplane | crossplane-system |
| 3 | kyverno | kyverno |

Wave 0 establishes foundational services (TLS, secrets, storage, messaging) before any application-layer components start. Wave 1 brings up identity (Keycloak) and GitOps (ArgoCD). Wave 2 deploys all application-layer platform services that depend on Keycloak for SSO. Wave 3 adds infrastructure-management tooling.

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

Production deployments use Helm values overrides or separate ArgoCD ApplicationSets — there is no `platform/overlays/production/` directory. Configure each component individually using the values files in `platform/base/<component>/` and refer to the individual component README files for production-specific settings (e.g. `platform/base/external-secrets/README.md` for secret backend configuration, `platform/base/cert-manager/README.md` for Let's Encrypt issuers).

## Adding New Components

1. Create directory under `base/`
2. Add Kustomize files (kustomization.yaml, resources)
3. Update `scripts/local-setup.nu` if needed
4. Document in this README
