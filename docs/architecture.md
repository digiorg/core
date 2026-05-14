# Platform Architecture

This document provides an overview of the DigiOrg Core Platform architecture.

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Platform Architecture                           │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                       Industry Solutions Layer                          │
│                                                                         │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────┐  │
│  │  AI DevSecOps    │  │  Self-Service    │  │  Compliance          │  │
│  │  Workflows       │  │  Portal          │  │  Automation          │  │
│  └──────────────────┘  └──────────────────┘  └──────────────────────┘  │
├─────────────────────────────────────────────────────────────────────────┤
│                     Business Integration Layer                          │
│                                                                         │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────┐  │
│  │  AI Agent        │  │  Policy          │  │  Tenant              │  │
│  │  Orchestration   │  │  Engine          │  │  Management          │  │
│  └──────────────────┘  └──────────────────┘  └──────────────────────┘  │
├─────────────────────────────────────────────────────────────────────────┤
│                    Digital IT Foundation Layer                          │
│                                                                         │
│  ┌─────────────┬─────────────┬─────────────┬─────────────────────────┐ │
│  │   GitOps    │  Security   │ Observability│   Infrastructure      │ │
│  │             │             │              │                       │ │
│  │  • ArgoCD   │  • Kyverno  │  • Prometheus│  • Crossplane         │ │
│  │  • Backstage│  • Keycloak │  • Grafana   │  • Terraform          │ │
│  │             │             │  • Jaeger    │                       │ │
│  │             │             │  • OpenSearch│                       │ │
│  └─────────────┴─────────────┴─────────────┴─────────────────────────┘ │
├─────────────────────────────────────────────────────────────────────────┤
│                       Kubernetes Runtime Layer                          │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │   AWS EKS  │  Azure AKS  │  GCP GKE  │  IONOS  │  KinD (local) │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

## Local Development Stack

The local development environment (`digiorg-core-dev` cluster) includes:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    Local Development Stack                              │
│                                                                         │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │                     Unified Ingress                             │    │
│  │              https://digiorg.local/<service>                    │    │
│  │  ┌────┬──────────┬──────────┬──────────┬──────────┬──────────┐ │    │
│  │  │ /  │/keycloak │ /argocd  │ /grafana │/backstage│  /gitea  │ │    │
│  │  ├────┴──────────┴──────────┴──────────┴──────────┴──────────┤ │    │
│  │  │         /jaeger                  │       /sonarqube        │ │    │
│  │  └──────────────────────────────────┴─────────────────────────┘ │    │
│  └────────────────────────────────────────────────────────────────┘    │
│          │          │          │          │          │    │    │       │
│          ▼          ▼          ▼          ▼          ▼    ▼    ▼       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐    │
│  │ Landing  │ │ Keycloak │ │  ArgoCD  │ │ Grafana  │ │Backstage │    │
│  │  Page    │◀─┤   IdP    │◀─┤   SSO    │◀─┤  OAuth   │◀─┤  OIDC    │    │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘    │
│                                                                         │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                               │
│  │  Gitea   │ │  Jaeger  │ │SonarQube │                               │
│  │   SCM    │ │  Tracing │ │   SAST   │                               │
│  └──────────┘ └──────────┘ └──────────┘                               │
│       │                                                                 │
│       └──────────────────────────┬──────────────────────────────────── │
│                                  │                                      │
│                                  ▼                                      │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │         Shared PostgreSQL (platform-db namespace)               ││
│  │  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐      ││
│  │  │  keycloak DB   │  │  backstage DB  │  │    gitea DB    │      ││
│  │  └────────────────┘  └────────────────┘  └────────────────┘      ││
│  │  ┌────────────────┐                                               ││
│  │  │  sonarqube DB  │                                               ││
│  │  └────────────────┘                                               ││
│  └──────────────────────────────────────────────────────────────────┘│
│  * Gitea uses Keycloak OIDC, auto-configured by local-setup.nu        │
│    (Phase 3: configure_gitea)                                         │
│  ┌──────────┐ ┌──────────┐                                              │
│  │Crossplane│ │Prometheus│                                              │
│  └──────────┘ └──────────┘                                              │
│                                                                        │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │                        Kyverno                                  │   │
│  │                   (Policy Engine)                               │   │
│  └────────────────────────────────────────────────────────────────┘   │
│                                                                        │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │                    KinD Cluster                                 │   │
│  │                  (digiorg-core-dev)                             │   │
│  └────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

## Authentication Flow

All services authenticate via Keycloak. Most use OIDC directly; SonarQube uses Keycloak SAML; Jaeger is protected via oauth2-proxy (OIDC).

```
┌─────────────────────────────────────────────────────────────────────────┐
│                       Authentication Flow                               │
└─────────────────────────────────────────────────────────────────────────┘

    User                    Service                  Keycloak
      │                        │                        │
      │  1. Access service     │                        │
      ├───────────────────────▶│                        │
      │                        │                        │
      │  2. Redirect to login  │                        │
      │◀───────────────────────┤                        │
      │                        │                        │
      │  3. Login page         │                        │
      ├────────────────────────┼───────────────────────▶│
      │                        │                        │
      │  4. Authenticate       │                        │
      │◀───────────────────────┼────────────────────────┤
      │                        │                        │
      │  5. Authorization code │                        │
      ├───────────────────────▶│                        │
      │                        │  6. Exchange for token │
      │                        ├───────────────────────▶│
      │                        │                        │
      │                        │  7. JWT token          │
      │                        │◀───────────────────────┤
      │                        │                        │
      │  8. Access granted     │                        │
      │◀───────────────────────┤                        │
      │                        │                        │
```

## Namespace Layout

```
┌─────────────────────────────────────────────────────────────────────────┐
│                       Kubernetes Namespaces                             │
└─────────────────────────────────────────────────────────────────────────┘

 Data Layer                           Infrastructure
 ─────────────────                    ──────────────
 ┌──────────────┐                     ┌──────────────┐
 │  platform-db │                     │ ingress-nginx│
 │  • postgresql│◀───────────────────┤  • controller│
 │  • opensearch│  keycloak DB        └──────────────┘
 │    (shared) │  backstage DB
 └───────┬──────┘  gitea DB           ┌──────────────┐
        │          sonarqube DB       │  crossplane- │
        │          opensearch         │    system    │
        │          (observability)    │  • providers │
        │                            └──────────────┘
        │
        │                            ┌──────────────┐
        │                            │   kyverno    │
        │                            │  • admission │
        │                            │  • background│
        │                            └──────────────┘
        │
        │                            ┌──────────────┐
        │                            │  kube-system │
        │                            │  • coredns   │
        │                            └──────────────┘
        │
 Platform Services
 ─────────────────
 ┌──────────────┐
 │ cert-manager │
 │  • controller│  ← Wave 0: provisions TLS certs for digiorg.local
 │  • webhook   │  ← self-signed CA + Let's Encrypt support
 └──────────────┘

 ┌──────────────┐
 │   keycloak   │
 │  • keycloak  │◀─── uses keycloak DB
 └──────────────┘

 ┌──────────────┐
 │    argocd    │
 │  • server    │
 │  • repo-srv  │
 │  • redis     │
 └──────────────┘

 ┌──────────────┐
 │  monitoring  │
 │  • prometheus│
 │  • grafana   │
 └──────────────┘

 ┌──────────────┐
 │  backstage   │
 │  • backstage │◀─── uses backstage DB
 └──────────────┘

 ┌──────────────┐
 │    gitea     │
 │  • gitea     │◀─── uses gitea DB
 └──────────────┘

 ┌──────────────┐
 │  platform-   │
 │    apps      │
 │  • landingpg │  ← Platform entry point with Keycloak SSO
 └──────────────┘

 ┌──────────────┐
 │  messaging   │
 │  • nats      │  ← Wave 0: JetStream + Surveyor
 │  • surveyor  │
 └──────────────┘

 ┌──────────────┐
 │   tracing    │
 │  • jaeger    │  ← Wave 2: + jaeger-oauth2-proxy
 │  • oauth2-   │
 │    proxy     │
 └──────────────┘

 ┌──────────────┐
 │ code-quality │
 │  • sonarqube │  ← Wave 2
 └──────────────┘

 ┌──────────────┐
 │  external-   │
 │   secrets    │  ← Wave 0: External Secrets Operator
 └──────────────┘
```

## TLS Architecture

All traffic is served over HTTPS. TLS terminates at the NGINX Ingress:

```
Browser ──HTTPS:443──▶ NGINX Ingress ──HTTP──▶ Services (internal)
                          │
                          │ TLS cert managed by cert-manager
                          ▼
                  digiorg-local-tls (Secret)
                          ▲
                          │ issues
                   cert-manager
                  ┌────────────────┐
                  │  Local Dev:    │  Self-signed CA (digiorg-local-ca-issuer)
                  │  Staging/Prod: │  Let's Encrypt ACME (letsencrypt-prod)
                  └────────────────┘
```

HTTP (`:80`) automatically redirects to HTTPS (`:443`).

## GitOps Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            GitOps Flow                                  │
└─────────────────────────────────────────────────────────────────────────┘

    Developer                    Git Repository                 Kubernetes
        │                              │                              │
        │  1. Push changes             │                              │
        ├─────────────────────────────▶│                              │
        │                              │                              │
        │                              │  2. ArgoCD detects change    │
        │                              │◀─────────────────────────────┤
        │                              │                              │
        │                              │  3. Sync to cluster          │
        │                              ├─────────────────────────────▶│
        │                              │                              │
        │                              │  4. Crossplane reconciles    │
        │                              │                              │
        │                              │         ┌────────────────────┤
        │                              │         │                    │
        │                              │         ▼                    │
        │                              │    ┌──────────┐              │
        │                              │    │  Cloud   │              │
        │                              │    │Resources │              │
        │                              │    └──────────┘              │
        │                              │                              │
        │  5. Status visible in Git    │                              │
        │◀─────────────────────────────┤                              │
```

## Security Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        Security Architecture                            │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                         Identity Layer                                  │
│                                                                         │
│   ┌─────────────────────────────────────────────────────────────────┐  │
│   │                        Keycloak                                  │  │
│   │  • OIDC Provider         • User Federation                      │  │
│   │  • SSO for all services  • Role-based Access                    │  │
│   │  • Pre-configured realm  • Group mappings                       │  │
│   └─────────────────────────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────────────────────────┤
│                          Policy Layer                                   │
│                                                                         │
│   ┌─────────────────────────────────────────────────────────────────┐  │
│   │                      Kyverno Policies                            │  │
│   │  • Pod Security Standards    • Image policies                   │  │
│   │  • Network policies          • Resource quotas                  │  │
│   │  • Label requirements        • RBAC enforcement                 │  │
│   └─────────────────────────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────────────────────────┤
│                         Network Layer                                   │
│                                                                         │
│   ┌─────────────────────────────────────────────────────────────────┐  │
│   │                   NGINX Ingress Controller                       │  │
│   │  • TLS termination (production)                                 │  │
│   │  • Path-based routing                                           │  │
│   │  • Rate limiting                                                │  │
│   └─────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

## Observability Architecture

The platform implements a two-pillar observability stack (logs are planned):

| Pillar  | Components                | Namespace(s)              |
|---------|---------------------------|---------------------------|
| Metrics | Prometheus + Grafana      | monitoring                |
| Traces  | Jaeger + OpenSearch       | tracing + platform-db     |
| Logs    | Planned                   | —                         |

```
  Services                    tracing ns                 platform-db ns
  ────────                    ──────────                 ──────────────
  App Pods ──OTLP gRPC:4317──▶ Jaeger ──────writes────▶ OpenSearch
           ──OTLP HTTP:4318──▶        ◀──────queries───
                                           │
                                    Jaeger UI ──────────▶ Grafana datasource

  App Pods ──ServiceMonitor──▶ Prometheus ────────────▶ Grafana dashboards
           (all namespaces)    (monitoring ns)
```

## Messaging Architecture

NATS JetStream runs in the `messaging` namespace (Wave 0) and provides persistent messaging for platform services:

```
  Application Pods ──:4222──▶ NATS JetStream ──▶ Surveyor :7777 ──▶ Prometheus
                               (messaging ns)      (metrics exporter)
```

## Secret Management Architecture

External Secrets Operator (ESO) runs in the `external-secrets` namespace (Wave 0) and enables provider-swappable secret management:

```
  ExternalSecret CR ──▶ ClusterSecretStore ──▶ Fake Provider      (local dev)
                                           ├──▶ Azure Key Vault    (staging/prod)
                                           └──▶ HashiCorp Vault    (on-prem)
                              │
                              ▼
                       Kubernetes Secret  (consumed by workloads)
```

## Code Quality Architecture

SonarQube Community Build runs in the `code-quality` namespace (Wave 2):

- **Auth**: Keycloak SAML (native Community Build support — no plugin required)
- **Storage**: Shared PostgreSQL in `platform-db` namespace; 5 Gi PVC for plugins and temporary analysis data

```
  Developer ──push──▶ Gitea ──webhook──▶ SonarQube ──SAML──▶ Keycloak
                                          (code-quality ns)
                                                │
                               ┌────────────────┴────────────────┐
                               │                                 │
                     PostgreSQL (platform-db)             5 Gi PVC
                     sonarqube DB                         plugins/temp
```

## Related ADRs

- [ADR-001: Platform Architecture Decisions](adr/001-bootstrap-framework-architecture.md)
