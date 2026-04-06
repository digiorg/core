# ArgoCD Applications

This directory contains ArgoCD Application manifests managed by the App-of-Apps pattern.

## Structure

```
apps/
├── README.md
├── platform/              # Platform infrastructure apps
│   ├── argocd.yaml        # Self-managed ArgoCD (Wave 1)
│   ├── keycloak.yaml      # Identity Provider (Wave 1)
│   ├── backstage.yaml     # Developer Portal (Wave 2)
│   ├── crossplane.yaml    # Infrastructure as Code (Wave 3)
│   └── kyverno.yaml       # Policy Engine (Wave 3)
└── observability/         # Monitoring and logging
    └── monitoring.yaml    # Prometheus + Grafana (Wave 2)
```

## Sync Waves

Applications are deployed in order using ArgoCD sync waves:

| Wave | Applications | Description |
|------|--------------|-------------|
| -1 | root-app | Bootstrap (deployed by setup script) |
| 0 | platform-apps | ApplicationSet generator |
| 1 | keycloak, argocd | Core infrastructure (IdP, GitOps) |
| 2 | backstage, monitoring | Platform services (depend on Keycloak) |
| 3 | crossplane, kyverno | Extensions (no Keycloak dependency) |

## How It Works

1. **Setup Script** bootstraps: KinD cluster, Ingress, CoreDNS, Secrets, ArgoCD (Helm)
2. **Setup Script** deploys `root-app.yaml`
3. **Root App** discovers `apps/` directory
4. **ApplicationSet** generates Applications from `apps/platform/*` and `apps/observability/*`
5. **Sync Waves** ensure correct deployment order

## Adding a New Application

1. Create a new YAML file in the appropriate directory:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: my-app
  namespace: argocd
  finalizers:
    - resources-finalizer.argocd.argoproj.io
  annotations:
    argocd.argoproj.io/sync-wave: "2"  # Adjust based on dependencies
spec:
  project: default
  source:
    repoURL: git@github.com:digiorg/core.git
    targetRevision: HEAD
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

2. Create the corresponding manifests in `platform/base/my-app/`
3. Commit and push — ArgoCD will automatically sync

## Dependencies

### Keycloak Dependencies (Wave 2+)

These services require Keycloak for authentication:
- **ArgoCD** — OIDC login (works after Keycloak is ready)
- **Grafana** — OAuth login
- **Backstage** — OIDC login

### No Dependencies (Wave 3)

These services don't require other platform services:
- **Crossplane** — Infrastructure provisioning
- **Kyverno** — Policy enforcement

## Secrets

Secrets are created by the setup script **before** ArgoCD is installed:

| Namespace | Secret | Notes |
|-----------|--------|-------|
| keycloak | keycloak-db-credentials | Bootstrap database credentials secret created by the setup script |
| backstage | backstage-secrets | Bootstrap application secret created by the setup script |

The setup script does **not** create a bootstrap Grafana secret in the `monitoring` namespace. Refer to the setup script for the exact keys present in each bootstrap secret.
For production, use External Secrets Operator with Azure KeyVault / AWS Secrets Manager.
