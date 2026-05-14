# ADR-001: Platform Architecture Decisions

**Status:** Accepted  
**Date:** 2026-02-22  
**Updated:** 2026-05-14  
**Deciders:** @christian.mueller, @simon-itstudio

---

## Context

The DigiOrg Core Platform needs a consistent, repeatable way to:

1. Provision Kubernetes clusters across multiple cloud providers (AWS, Azure, GCP, IONOS, StackIT)
2. Install and configure platform components (ArgoCD, Keycloak, Crossplane, Kyverno, Backstage)
3. Manage infrastructure lifecycle with GitOps principles
4. Support both initial setup (Day-1) and ongoing operations (Day-2)

This document consolidates all platform-level architectural decisions, covering the bootstrap framework, observability stack, identity management, secret handling, messaging, and code quality tooling.

---

## Section 1: Bootstrap Framework

### Decision

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
# Bootstrap local development cluster
def "main up" [] {
    print "Phase 1: Bootstrap Infrastructure"
    main bootstrap   # KinD cluster, ingress, CoreDNS, secrets, ArgoCD

    print "Phase 2: Deploy ArgoCD Root App (App-of-Apps)"
    deploy_root_app  # Triggers ArgoCD wave-based deployment

    print "Phase 3: Configure Applications"
    configure_gitea              # OIDC auto-config, users, DigiOrg org
    configure_sonarqube          # SAML via Settings API
    restart_oidc_dependent_pods  # DNS refresh for OIDC clients
    patch_argocd_oidc_ca         # Patch ArgoCD with Keycloak CA
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

### Bootstrap Sequence (Local Development)

```
┌──────────────────────────────────────────────────────────────────────┐
│                   Local Bootstrap Sequence                           │
│                    (nu scripts/local-setup.nu up)                    │
└──────────────────────────────────────────────────────────────────────┘

Phase 1: Infrastructure  (main bootstrap)
┌─────────────────────────────────────────────────────────────────────┐
│  1. Create KinD cluster (digiorg-core-dev)                          │
│  2. Set vm.max_map_count=262144 via privileged initContainer        │
│     (required for OpenSearch)                                       │
│  3. Install Gateway API CRDs                                        │
│  4. Install NGINX Ingress Controller                                │
│  5. Apply Platform Ingress rules (digiorg.local/*)                  │
│  6. Patch CoreDNS for digiorg.local resolution                      │
│  7. Create platform namespaces + secrets                            │
│     (10 secrets across 7 namespaces)                                │
│  8. Install ArgoCD                                                  │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
Phase 2: ArgoCD App-of-Apps  (deploy_root_app)
┌─────────────────────────────────────────────────────────────────────┐
│  Wave 0 — Data layer (synced first, no dependencies)                │
│    cert-manager, external-secrets, nats, opensearch, postgresql     │
│                                                                     │
│  Wave 1 — Identity & GitOps                                         │
│    keycloak, argocd                                                 │
│                                                                     │
│  Wave 2 — Platform applications                                     │
│    landingpage, backstage, gitea, grafana, jaeger, sonarqube        │
│                                                                     │
│  Wave 3 — Policy & infrastructure controllers                       │
│    crossplane, kyverno                                              │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
Phase 3: Post-deploy Configuration
┌─────────────────────────────────────────────────────────────────────┐
│  1. configure_gitea — OIDC auto-config, create users, DigiOrg org  │
│  2. configure_sonarqube — SAML settings via Settings API            │
│  3. restart_oidc_dependent_pods — DNS refresh for OIDC clients      │
│  4. patch_argocd_oidc_ca — Inject Keycloak self-signed CA           │
└─────────────────────────────────────────────────────────────────────┘
```

### Service Access

All services are accessible via a unified TLS ingress at `https://digiorg.local`:

| Path | Service | Namespace | Authentication |
|------|---------|-----------|----------------|
| `https://digiorg.local/` | Landing Page | `platform-apps` | Keycloak OIDC |
| `https://digiorg.local/keycloak` | Keycloak Admin Console | `keycloak` | Built-in |
| `https://digiorg.local/argocd` | ArgoCD UI | `argocd` | Keycloak OIDC |
| `https://digiorg.local/grafana` | Grafana Dashboards | `monitoring` | Keycloak OAuth |
| `https://digiorg.local/backstage` | Developer Portal | `backstage` | Keycloak OIDC |
| `https://digiorg.local/gitea` | Gitea Git Service | `gitea` | Keycloak OIDC |
| `https://digiorg.local/jaeger` | Jaeger Tracing UI | `tracing` | Keycloak OIDC via oauth2-proxy |
| `https://digiorg.local/sonarqube` | SonarQube Code Quality | `code-quality` | Keycloak SAML |

### Provider Abstraction Pattern

Each cloud provider implements a common Terraform interface:

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

---

## Section 2: Observability Architecture

The platform implements the **three-pillar observability model**:

| Pillar | Tool | Storage | Status |
|--------|------|---------|--------|
| Metrics | Prometheus + Grafana | Prometheus PVC | ✅ Deployed |
| Traces | Jaeger v2 | OpenSearch (`platform-db`) | ✅ Deployed |
| Logs | planned (Fluent Bit) | OpenSearch (`platform-db`) | 🔲 Planned |

OpenSearch serves as the unified observability storage backend, covering traces now and logs in the future. Grafana provides the primary dashboard layer across all three pillars.

### 2.1: Distributed Tracing — Jaeger

#### Decision

We adopt **Jaeger v2** (Helm chart `jaeger` from `jaegertracing.github.io/helm-charts`) as the distributed tracing backend.

For local development we deploy in **all-in-one** mode with in-memory storage. For production, OpenSearch is used as the storage backend with separate collector and query deployments.

#### Rationale

| Criterion | Jaeger | Grafana Tempo | Zipkin |
|-----------|--------|---------------|--------|
| CNCF status | Graduated | Sandbox | — |
| Native OTLP | Yes (v2) | Yes | Partial |
| Grafana datasource | Yes | Yes (native) | Yes |
| All-in-one mode | Yes | No | Yes |
| Storage options | Memory, OpenSearch, Cassandra | Object storage (S3/GCS) | In-memory, Elasticsearch |
| License | Apache 2.0 | AGPL (Grafana OSS) | Apache 2.0 |
| UI | Built-in | Grafana only | Built-in |
| Kubernetes-native | Yes | Yes | No |

**Jaeger** was chosen because:

1. **CNCF Graduated**: Battle-tested at scale, strong community and long-term support commitment.
2. **Native OTLP support**: Jaeger v2 accepts OTLP natively on ports 4317 (gRPC) and 4318 (HTTP) — no translation layer needed.
3. **Grafana datasource**: First-class Grafana integration for correlating traces with metrics.
4. **All-in-one mode**: Single container for local dev keeps resource usage low and setup simple.
5. **Apache 2.0 license**: No AGPL restrictions; compatible with our licensing requirements.
6. **OpenSearch storage path**: Integrates with OpenSearch (deployed in `platform-db`) for production persistence.

**Grafana Tempo** was considered but rejected:
- Requires object storage (S3/GCS) even for local dev — adds operational complexity.
- No standalone UI; depends entirely on Grafana, making standalone debugging harder.
- Sandbox status at time of decision.

**Zipkin** was considered but rejected:
- Not CNCF; smaller community.
- Incomplete native OTLP support requires an extra OpenTelemetry Collector layer.
- No Kubernetes-native deployment story.

#### Production Migration Path

1. ✅ Provision OpenSearch cluster — deployed in `platform-db` namespace (see Section 2.2 and issue #83).
2. ✅ Switch `jaeger_storage.backends.primary_store` to `elasticsearch` backend pointing to `opensearch-cluster-master.platform-db.svc.cluster.local:9200`.
3. Disable `allInOne`, enable separate `collector` and `query` deployments. _(planned for production scale-out)_
4. Add OAuth2 proxy (Keycloak) in front of the query service. _(tracked in issue #82)_
5. Configure adaptive sampling in the collector. _(planned)_

### 2.2: Observability Storage — OpenSearch

#### Decision

We deploy **OpenSearch** (via the official `opensearch-project/opensearch` Helm chart v3.6.0, repo `https://helm.opensearch.org`) in the `platform-db` namespace as the shared observability storage backend.

- Mode: single-node for local dev, 3-node cluster for production
- ArgoCD Sync Wave: 0 (data layer, before all application services)

#### Namespace Placement

OpenSearch is placed in the existing `platform-db` namespace alongside PostgreSQL. This centralizes all persistent data services in one namespace, making backup, access control, and network policies uniform.

```
platform-db namespace
├── postgresql     ← Keycloak, Backstage, Gitea, SonarQube databases
└── opensearch     ← Jaeger traces, future log aggregation
```

#### Rationale

| Criterion | OpenSearch | Cassandra | PostgreSQL |
|-----------|-----------|-----------|------------|
| Jaeger native support | ✅ Official (1.x–3.x) | ✅ Official (4.x–5.x) | ❌ Community-only |
| Init job required | ❌ No | ✅ Schema script needed | ❌ N/A |
| Log aggregation support | ✅ Yes (future Fluent Bit) | ❌ No | ❌ No |
| Grafana datasource | ✅ Elasticsearch type | ❌ No | ✅ PostgreSQL type |
| Index TTL / rollover | ✅ ISM built-in | ✅ TTL per table | ❌ Manual |
| Full-text search | ✅ Yes | ❌ No | ⚠️ Limited |
| Kubernetes Helm chart | ✅ Official | ✅ Bitnami | ✅ Custom |
| License | ✅ Apache 2.0 | ✅ Apache 2.0 | ✅ PostgreSQL |
| Resource overhead | ⚠️ Medium (512 MB+) | ⚠️ High (1 GB+) | ✅ Low |

**OpenSearch** was chosen because:

1. **Official Jaeger support**: Versions 1.x, 2.x, 3.x are all supported with no additional init steps. Index creation is automatic on first trace write.
2. **Elasticsearch API compatibility**: OpenSearch exposes the ES 7.10.2 REST API — Grafana, Jaeger, Fluent Bit, and other tools integrate natively without additional adapters.
3. **Observability convergence**: OpenSearch is the only option that covers both traces (now) and logs (future). Deploying it once establishes the full observability storage infrastructure.
4. **No schema maintenance**: Unlike Cassandra (CQL schema init) or PostgreSQL, OpenSearch requires no upfront schema work — indices are created dynamically.
5. **ISM for retention**: The built-in Index State Management provides automatic index rollover and deletion policies without external cron jobs.
6. **Apache 2.0 license**: Fully open-source, no AGPL or proprietary restrictions.

**Cassandra** was not chosen:
- Heavier resource requirements (multi-GB RAM for cluster formation).
- No log aggregation support — a second storage backend would still be needed.
- Schema init required via `cqlsh` script.

**PostgreSQL** was ruled out:
- No official Jaeger support (community gRPC adapter only, unmaintained risk).
- Not suitable for full-text search or time-series log retention.

#### Production Migration Path

1. Set `singleNode: false`, `replicas: 3` in `values.yaml`.
2. Remove `DISABLE_SECURITY_PLUGIN`, configure TLS via cert-manager and RBAC.
3. Integrate Keycloak OIDC with OpenSearch Security plugin for SSO.
4. Configure ISM policy for index rollover (e.g. 30-day retention, max 50 GB per index).
5. Deploy OpenSearch Dashboards (separate Helm chart) for direct log/trace UI with Keycloak SSO.
6. Set `vm.max_map_count=262144` via node-level DaemonSet or cloud provider node pool configuration.
7. Use a high-performance StorageClass (SSD-backed) for the PVC.

---

## Section 3: Identity & Access Management

### Decision

We adopt **Keycloak** as the platform Identity Provider (IdP) with realm `digiorg-core-platform`.

### Authentication Patterns

| Protocol | Services |
|----------|---------- |
| OIDC | ArgoCD, Grafana, Backstage, Gitea, Landing Page, Jaeger (via oauth2-proxy) |
| SAML | SonarQube Community Build |

Jaeger uses oauth2-proxy as an authentication front-end because the Jaeger UI has no native OIDC support. All other services integrate directly.

SonarQube uses SAML because native SAML support is available in the Community Build; OIDC requires the paid edition.

### Pre-Configured Keycloak Clients

All clients are imported via the `digiorg-core-platform-realm.json` ConfigMap at install time:

| Client ID | Protocol | Service |
|-----------|----------|---------|
| `landingpage` | OIDC | Platform Landing Page |
| `argocd` | OIDC | ArgoCD GitOps UI |
| `grafana` | OIDC | Grafana Dashboards |
| `backstage` | OIDC | Backstage Developer Portal |
| `gitea` | OIDC | Gitea Git Service |
| `jaeger` | OIDC | Jaeger Tracing UI (via oauth2-proxy) |
| `sonarqube` | SAML | SonarQube Code Quality |

### Rationale

**Keycloak** was chosen over alternatives (Dex, Authentik, Auth0) because:

1. **Centralized SSO**: Single realm manages all platform service identities — no per-service user stores.
2. **CNCF ecosystem fit**: Native OIDC and SAML support aligns with all platform components without adapters.
3. **Realm import via ConfigMap**: The full realm configuration (clients, roles, mappers) is version-controlled and applied deterministically at bootstrap, ensuring reproducibility across environments.
4. **Protocol breadth**: Supports both OIDC (modern apps) and SAML (SonarQube Community Build) from a single IdP.
5. **PostgreSQL-backed**: Keycloak state is persisted in the shared PostgreSQL instance, providing reliability and a clear backup target.

---

## Section 4: Secret Management

### Decision

We adopt the **External Secrets Operator (ESO)** with a **provider-swap pattern**. All `ExternalSecret` resources reference a `ClusterSecretStore` by name only; swapping environments requires replacing the `ClusterSecretStore` definition alone, not touching any `ExternalSecret` manifests.

### Environment Mapping

| Environment | ClusterSecretStore name | Backend |
|-------------|------------------------|---------|
| Development | `digiorg-dev-store` | Fake provider (in-cluster static values) |
| Production | `digiorg-prod-store` | Azure Key Vault / HashiCorp Vault |

### Rationale

- **No secret sprawl**: All secrets are fetched at runtime from a single authoritative source; no credentials in Git.
- **Environment parity**: Dev and prod use identical `ExternalSecret` manifests — only the `ClusterSecretStore` changes.
- **Zero-friction local dev**: The Fake provider removes any cloud credential dependency for local KinD clusters.
- **Production flexibility**: The production store can point to Azure KV or HashiCorp Vault without any application-layer changes.

---

## Section 5: Messaging Architecture

### Decision

We adopt **NATS JetStream** as the platform message broker, deployed in the `messaging` namespace.

### Rationale

1. **Lightweight footprint**: NATS has a minimal resource profile suitable for platform-layer messaging without dedicated infrastructure overhead.
2. **CNCF project**: Active community, well-maintained, Kubernetes-native deployment.
3. **JetStream persistence**: Built-in at-least-once and exactly-once delivery with persistent streams — no separate Kafka cluster needed at this scale.
4. **Prometheus metrics**: NATS Surveyor exposes Prometheus-compatible metrics, integrating with the existing kube-prometheus-stack without additional exporters.

---

## Section 6: Code Quality

### Decision

We adopt **SonarQube Community Build** for static code analysis and SAST, deployed in the `code-quality` namespace backed by the shared PostgreSQL in `platform-db`.

### Authentication

SonarQube is configured for **Keycloak SAML** authentication. SAML is the only SSO protocol supported in the Community Build; OIDC requires the paid Developer Edition or higher.

SAML settings are persisted in the SonarQube database and applied programmatically via the Settings API in Phase 3 of bootstrap (`configure_sonarqube`). This is intentional: the SonarQube Helm chart does not support injecting SAML configuration declaratively at install time.

### Key Implementation Notes

- `sonarSecretProperties` is intentionally not set (tracked in issue #109) — the SAML secret is applied via the Settings API post-deploy rather than via Helm values.
- The shared PostgreSQL instance in `platform-db` hosts the SonarQube database alongside Keycloak, Backstage, and Gitea.
- SonarQube is deployed in ArgoCD sync Wave 2, after PostgreSQL (Wave 0) and Keycloak (Wave 1) are healthy.

---

## Consequences

### Positive

- **Single Sign-On**: Keycloak provides unified authentication for all platform services across OIDC and SAML.
- **Clear separation of concerns**: Terraform for Day-1 bootstrap, Crossplane for Day-2 operations, Nushell for orchestration.
- **GitOps-native**: All Crossplane and ArgoCD resources live in Git and are continuously reconciled.
- **Unified access**: Single domain (`https://digiorg.local`) with path-based routing for all services.
- **Complete observability**: All three pillars (metrics, traces, logs-ready) are in place with OpenSearch as the shared storage layer.
- **No secret sprawl**: External Secrets Operator with provider-swap keeps credentials out of Git and enables environment portability.
- **Developer experience**: Backstage portal, Gitea, Grafana, Jaeger, and SonarQube accessible via SSO from day one.
- **Persistent traces**: Jaeger backed by OpenSearch survives pod restarts and provides production-grade trace retention.
- **Index lifecycle management**: OpenSearch ISM handles trace and log index rollover without external cron jobs.

### Negative

- **Learning curve**: Teams must learn Nushell, ArgoCD wave sequencing, Keycloak realm management, and External Secrets — multiple tools simultaneously.
- **Keycloak dependency**: All services depend on Keycloak availability; a Keycloak outage blocks login across the entire platform.
- **OpenSearch resource overhead**: ~512 MB RAM minimum in local dev; ~3 × 2 GB in production — significant for resource-constrained environments.
- **`vm.max_map_count` requirement**: OpenSearch requires `vm.max_map_count=262144` on all nodes, handled via privileged `initContainer` for KinD. Must be set at node level in production.
- **OpenSearch security disabled in dev**: Security plugin is off in local dev — must be enabled and configured (TLS, RBAC, Keycloak OIDC) before production exposure.
- **In-memory traces in dev**: Without OpenSearch enabled, all Jaeger trace data is lost on pod restart.
- **SonarQube post-deploy config**: SAML configuration cannot be injected at install time; requires a working Settings API call in Phase 3, which depends on SonarQube being fully healthy first.

### Mitigations

- Keycloak is backed by PostgreSQL for reliability; init containers in dependent services wait for Keycloak readiness.
- OpenSearch startup time (~30–60 s) is accounted for in ArgoCD Wave 0 health checks.
- Phase 3 scripts include retry logic and readiness probes before applying post-deploy configuration.
- Comprehensive documentation and fully automated `nu scripts/local-setup.nu up` reduce onboarding friction.

---

## Alternatives Considered

### OAuth2 Proxy Instead of Native OIDC

Use a shared OAuth2 proxy for all services.

**Why not chosen**: Native OIDC integration provides better UX and is supported by all platform services directly. oauth2-proxy is used only for Jaeger, which has no native OIDC support.

### Separate Domains Per Service

Use `argocd.local`, `grafana.local`, etc.

**Why not chosen**: Unified `digiorg.local` domain with path-based routing is simpler to manage and requires a single `/etc/hosts` entry.

### Grafana Tempo Instead of Jaeger

Use Grafana Tempo as the tracing backend.

**Why not chosen**: Requires object storage even for local dev; has no standalone UI; was in Sandbox status at time of decision.

### Cassandra or PostgreSQL for Trace Storage

Use Cassandra or PostgreSQL as the Jaeger storage backend.

**Why not chosen**: Neither supports future log aggregation. Cassandra has higher resource overhead; PostgreSQL has no official Jaeger support. OpenSearch covers both use cases with a single deployment.

---

## References

- [Nushell Documentation](https://www.nushell.sh/book/)
- [Keycloak Documentation](https://www.keycloak.org/documentation)
- [ArgoCD OIDC Configuration](https://argo-cd.readthedocs.io/en/stable/operator-manual/user-management/)
- [Backstage Authentication](https://backstage.io/docs/auth/)
- [Jaeger Documentation](https://www.jaegertracing.io/docs/)
- [Jaeger Helm Chart](https://github.com/jaegertracing/helm-charts)
- [OpenTelemetry OTLP Specification](https://opentelemetry.io/docs/specs/otlp/)
- [Grafana Jaeger Datasource](https://grafana.com/docs/grafana/latest/datasources/jaeger/)
- [CNCF Jaeger Project](https://www.cncf.io/projects/jaeger/)
- [OpenSearch Helm Charts](https://github.com/opensearch-project/helm-charts)
- [Jaeger OpenSearch Storage Docs](https://www.jaegertracing.io/docs/latest/opensearch/)
- [OpenSearch Index State Management](https://docs.opensearch.org/latest/im-plugin/ism/index/)
- [External Secrets Operator](https://external-secrets.io/)
- [NATS JetStream](https://docs.nats.io/nats-concepts/jetstream)
- [Issue #82: Jaeger Refactoring Analysis](https://github.com/digiorg/core/issues/82)
- [Issue #83: OpenSearch Feature](https://github.com/digiorg/core/issues/83)
- [Issue #109: SonarQube SAML sonarSecretProperties](https://github.com/digiorg/core/issues/109)
