# Argo CD Applications

This directory contains the Argo CD `Application` manifests managed by the root App-of-Apps.

## Application inventory

There are **27 child Applications** under `apps/platform/`. Their sync-wave annotations are ordering metadata; they do not prove that a dependency in another Application is functionally ready.

| Wave | Applications | Runtime contract |
|---|---|---|
| -1 | namespaces | Pre-creates all platform namespaces |
| 0 | cert-manager, external-secrets, nats, opensearch, postgresql | Foundation and permanent core data layer |
| 1 | argocd, keycloak | GitOps and identity |
| 2 | backstage, gitea, grafana, harbor, jaeger, landingpage, opencost, sonarqube | Platform services |
| 3 | crossplane, kyverno | Infrastructure and policy engines |
| 4 | crossplane-providers, fluentd, kyverno-policies | Providers, log shipping, and policies |
| 5 | monitoring-extras | Additional monitoring resources |
| 6 | crossplane-provider-configs | Provider configuration |
| 7 | crossplane-xrds | Composite Resource Definitions |
| 8 | core-catalog | Core catalog |
| 9 | cnpg | **Manual** optional future-app database operator |
| 10 | cnpg-cluster | **Manual** optional future-app database Cluster |

Ingress is not an Argo CD Application; the local setup script applies it during bootstrap.

## Bootstrap and readiness contract

1. `nu scripts/local-setup.nu up` creates the KinD cluster, ingress, CoreDNS, bootstrap Secrets, and Argo CD.
2. Before the root App exists, the script directly applies the PostgreSQL and OpenSearch Applications.
3. It proves PostgreSQL over its real Service DNS/TCP path, authenticates every platform role, checks database/schema access, and verifies OpenSearch through its Service.
4. Only then does the script apply the root App, which creates all 27 child Application CRs.
5. The normal `up` path synchronizes and waits for the **25 core Applications**. The two CNPG Applications remain manual and cannot delay or fail core bootstrap.
6. Optional future hosted-application database infrastructure is promoted explicitly with:

```bash
nu scripts/local-setup.nu future-infra
```

That command fails closed and sequences operator sync, operator Deployment availability, admission-webhook endpoint readiness, and finally Cluster sync.

## Adding an Application

1. Add `apps/platform/<name>.yaml` with an immutable source revision and the correct destination namespace.
2. Assign a wave based on ordering only; add an explicit functional gate when another Application must be demonstrably ready.
3. Add the corresponding manifests under `platform/base/<name>/`.
4. Use automated sync only for core Applications that should be reconciled by normal `up`. Optional infrastructure must remain explicit and script-driven.
5. Update the application inventory. The platform regression tests compare the inventory with all Application manifests.

Example:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: my-app
  namespace: argocd
  finalizers:
    - resources-finalizer.argocd.argoproj.io
  annotations:
    argocd.argoproj.io/sync-wave: "2"
spec:
  project: default
  source:
    repoURL: https://github.com/digiorg/core.git
    targetRevision: <immutable-revision>
    path: platform/base/my-app
  destination:
    server: https://kubernetes.default.svc
    namespace: my-namespace
  syncPolicy:
    automated:
      selfHeal: true
      prune: true
    syncOptions:
      - CreateNamespace=true
      - ServerSideApply=true
```

## Core dependencies

### Permanent PostgreSQL

The legacy PostgreSQL StatefulSet in `platform-db` is the permanent database for internal platform components:

- Keycloak (`keycloak` database)
- Backstage (`backstage` database)
- Gitea (`gitea` database)
- SonarQube (`sonarqube` database)
- Harbor (`registry` database, `harbor` role)

CNPG is isolated future hosted-application infrastructure. It is not a migration, cutover target, backup mechanism, or replacement for these databases.

### OpenSearch

OpenSearch in `platform-db` remains the platform search, trace, and log database. Jaeger stores distributed traces there.

### Keycloak

Landing Page, Argo CD, Grafana, Backstage, Gitea, Harbor, OpenCost, and other configured clients depend on Keycloak for authentication.

## Bootstrap Secrets

Bootstrap Secrets are created idempotently before dependent Applications are synchronized. Existing generated values are preserved on resume unless an explicit environment override requests rotation. Relevant examples include:

| Namespace | Secret | Purpose |
|---|---|---|
| platform-db | postgresql-secrets | PostgreSQL superuser and per-role passwords |
| platform-db | opensearch-secrets | OpenSearch administrator credential |
| backstage | backstage-secrets | Database, session, and OIDC values |
| gitea | gitea-secrets | Database and OIDC values |
| code-quality | sonarqube-db-secret | SonarQube database credential |
| code-quality | sonarqube-monitoring-secret | SonarQube monitoring passcode |
| harbor | harbor-admin-secret | Harbor administrator credential |
| harbor | harbor-oidc-secret | Harbor OIDC client credential |

The setup script does not print Secret values. For production, use External Secrets Operator with the selected external secret store.
