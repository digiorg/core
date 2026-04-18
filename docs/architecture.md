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
│  │              http://digiorg.local/<service>                     │    │
│  │  ┌────┬──────────┬──────────┬──────────┬──────────┬──────────┐ │    │
│  │  │ /  │/keycloak │ /argocd  │ /grafana │/backstage│  /gitea  │ │    │
│  │  └──┬─┴────┬─────┴────┬─────┴────┬─────┴────┬─────┴────┬─────┘ │    │
│  └───────┼──────────┼──────────┼──────────┼───────────────────────┘    │
│          │          │          │          │                            │
│          ▼          ▼          ▼          ▼                            │
│          │          │          │          │          │               │
│          ▼          ▼          ▼          ▼          ▼               │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐    │
│  │ Landing  │ │ Keycloak │ │  ArgoCD  │ │ Grafana  │ │Backstage │    │
│  │  Page    │◀─┤   IdP    │◀─┤   SSO    │◀─┤  OAuth   │◀─┤  OIDC    │    │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘    │
│       │                          │            │            │         │
│       └──────────────────────────┴────────────┴────────────┘         │
│                                  │                                   │
│                                  ▼                                   │
│  ┌──────────────────────────────────────────────────────────────────┐│
│  │         Shared PostgreSQL (platform-db namespace)               ││
│  │  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐      ││
│  │  │  keycloak DB   │  │  backstage DB  │  │    gitea DB    │      ││
│  │  └────────────────┘  └────────────────┘  └────────────────┘      ││
│  └──────────────────────────────────────────────────────────────────┘│
│  * Gitea uses Keycloak OIDC, but it is configured manually via      │
│    the Admin UI post-deployment rather than as part of deployment   │
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

All services authenticate via Keycloak OIDC:

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
 │    (shared) │  keycloak DB        └──────────────┘
 └───────┬──────┘  backstage DB
        │          gitea DB
        │                            ┌──────────────┐
        │                            │  crossplane- │
        │                            │    system    │
        │                            │  • providers │
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

## Related ADRs

- [ADR-001: Bootstrap Framework Architecture](adr/001-bootstrap-framework-architecture.md)
