# ADR-001: Bootstrap Framework Architecture

**Status:** Accepted  
**Date:** 2026-02-22  
**Updated:** 2026-04-05  
**Deciders:** @christian.mueller, @simon-itstudio  

## Context

The DigiOrg Core Platform needs a consistent, repeatable way to:

1. Provision Kubernetes clusters across multiple cloud providers (AWS, Azure, GCP, IONOS, StackIT)
2. Install and configure platform components (ArgoCD, Keycloak, Crossplane, Kyverno, Backstage)
3. Manage infrastructure lifecycle with GitOps principles
4. Support both initial setup (Day-1) and ongoing operations (Day-2)

We need to decide on the tooling and architecture for this bootstrap process.

## Decision

We adopt a **three-layer architecture** combining Terraform, Crossplane, and Nushell:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Nushell Orchestration                           │
│                    (platform.nu / local-setup.nu)                       │
│         Unified CLI interface for all bootstrap operations              │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────────────────────┐  ┌─────────────────────────────────┐  │
│  │      Terraform (Day-1)      │  │      Crossplane (Day-2)         │  │
│  │                             │  │                                 │  │
│  │  • Management cluster       │  │  • Workload clusters            │  │
│  │  • Initial VPC/Network      │  │  • Databases                    │  │
│  │  • IAM/Service Accounts     │  │  • Storage                      │  │
│  │  • Bootstrap resources      │  │  • Additional infrastructure    │  │
│  │                             │  │  • Self-service resources       │  │
│  │  State: Remote Backend      │  │  State: Kubernetes etcd         │  │
│  │  Reconcile: Manual          │  │  Reconcile: Continuous          │  │
│  └─────────────────────────────┘  └─────────────────────────────────┘  │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### Layer 1: Nushell Orchestration

Nushell serves as the orchestration layer providing:

- **Unified CLI**: Single entry point for all operations
- **Provider Abstraction**: Common interface across cloud providers
- **Workflow Automation**: Sequencing of Helm, Kubectl, and Terraform commands
- **Configuration Management**: Environment-specific settings

```nu
# Example: Bootstrap local development cluster
def "main up" [] {
    # 1. Create KinD cluster (digiorg-core-dev)
    kind create cluster --config $KIND_CONFIG
    
    # 2. Install ingress controller
    install_ingress
    
    # 3. Configure CoreDNS for digiorg.local
    configure_coredns_digiorg_local
    
    # 4. Install Keycloak (IdP)
    install_keycloak
    
    # 5. Install ArgoCD with Keycloak SSO
    install_argocd
    
    # 6. Install remaining components
    install_crossplane
    install_kyverno
    install_monitoring
    install_backstage
}
```

### Layer 2: Terraform (Day-1 Operations)

Terraform handles **initial infrastructure provisioning** for production:

| Resource | Description |
|----------|-------------|
| Management Cluster | The primary Kubernetes cluster hosting the platform |
| VPC/Network | Cloud networking (VPCs, subnets, security groups) |
| IAM | Service accounts, roles, policies for platform components |
| State Backend | S3/GCS bucket for Terraform state |

### Layer 3: Crossplane (Day-2 Operations)

Crossplane handles **ongoing infrastructure management**:

| Resource | Description |
|----------|-------------|
| Workload Clusters | Additional Kubernetes clusters |
| Databases | RDS, Cloud SQL, Azure Database |
| Storage | S3 buckets, GCS, Azure Blob |
| Custom Resources | Platform-specific infrastructure |

## Bootstrap Sequence (Local Development)

```
┌──────────────────────────────────────────────────────────────────────┐
│                   Local Bootstrap Sequence                           │
│                    (nu scripts/local-setup.nu up)                    │
└──────────────────────────────────────────────────────────────────────┘

Phase 1: Infrastructure
┌─────────────────────────────────────────────────────────────────────┐
│  1. Create KinD cluster (digiorg-core-dev)                          │
│  2. Install Gateway API CRDs                                        │
│  3. Install NGINX Ingress Controller                                │
│  4. Configure unified Ingress (digiorg.local/*)                     │
│  5. Patch CoreDNS for digiorg.local resolution                      │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
Phase 2: Identity & Security
┌─────────────────────────────────────────────────────────────────────┐
│  1. Install Keycloak + PostgreSQL                                   │
│  2. Configure digiorg-core-platform realm                           │
│  3. Pre-provision OIDC clients (argocd, grafana, backstage)         │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
Phase 3: Platform Components
┌─────────────────────────────────────────────────────────────────────┐
│  1. Install ArgoCD (with Keycloak SSO)                              │
│  2. Install Crossplane                                              │
│  3. Install Kyverno                                                 │
│  4. Install Prometheus + Grafana (with Keycloak OAuth)              │
│  5. Install Backstage (with Keycloak OIDC)                          │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
Phase 4: Finalization
┌─────────────────────────────────────────────────────────────────────┐
│  1. Restart OIDC-dependent pods (DNS refresh)                       │
│  2. Platform ready for use                                          │
└─────────────────────────────────────────────────────────────────────┘
```

## Service Access Pattern

All services are accessible via a unified ingress at `http://digiorg.local`:

| Path | Service | Authentication |
|------|---------|----------------|
| `/keycloak` | Keycloak Admin Console | Built-in |
| `/argocd` | ArgoCD UI | Keycloak OIDC |
| `/grafana` | Grafana Dashboards | Keycloak OAuth |
| `/backstage` | Developer Portal | Keycloak OIDC / Guest |

## Provider Abstraction Pattern

Each cloud provider implements a common interface:

```
terraform/modules/
├── aws/
│   ├── main.tf
│   ├── variables.tf      # Standard interface
│   └── outputs.tf        # Standard outputs
├── azure/
│   └── ...
└── gcp/
    └── ...
```

## Consequences

### Positive

- **Single Sign-On**: Keycloak provides unified authentication for all services
- **Clear separation of concerns**: Terraform for bootstrap, Crossplane for Day-2
- **GitOps-native**: All Crossplane resources in Git, synced by ArgoCD
- **Unified access**: Single domain (digiorg.local) for all services
- **Developer experience**: Backstage portal for self-service

### Negative

- **Learning curve**: Teams need to learn multiple tools
- **Complexity**: More moving parts than a single-tool solution
- **Keycloak dependency**: All services depend on Keycloak availability

### Mitigations

- Keycloak configured with PostgreSQL for reliability
- Init containers ensure dependent services wait for prerequisites
- Comprehensive documentation and setup automation

## Alternatives Considered

### Alternative 1: OAuth2 Proxy Instead of Native OIDC

Use a shared OAuth2 proxy for all services.

**Why not chosen:** Native OIDC integration provides better UX and is supported by all our services.

### Alternative 2: Separate Domains Per Service

Use `argocd.local`, `grafana.local`, etc.

**Why not chosen:** Unified `digiorg.local` domain is simpler to manage and requires fewer /etc/hosts entries.

## References

- [Keycloak Documentation](https://www.keycloak.org/documentation)
- [ArgoCD OIDC Configuration](https://argo-cd.readthedocs.io/en/stable/operator-manual/user-management/)
- [Backstage Authentication](https://backstage.io/docs/auth/)
- [Nushell Documentation](https://www.nushell.sh/book/)
