# ArgoCD Applications

This directory contains ArgoCD Application manifests managed by the App-of-Apps pattern.

## Structure

Applications are organized by purpose:

```
apps/
├── README.md
├── platform/          # Platform infrastructure apps
│   ├── argocd.yaml
│   ├── crossplane.yaml
│   ├── kyverno.yaml
│   └── vault.yaml
├── observability/     # Monitoring and logging
│   ├── prometheus.yaml
│   ├── grafana.yaml
│   └── loki.yaml
└── workloads/         # Workload clusters and resources
```

## Adding a New Application

1. Create a new YAML file following this template:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: my-app
  namespace: argocd
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: default
  source:
    repoURL: https://charts.example.com
    chart: my-chart
    targetRevision: 1.0.0
  destination:
    server: https://kubernetes.default.svc
    namespace: my-namespace
  syncPolicy:
    automated:
      selfHeal: true
      prune: true
    syncOptions:
      - CreateNamespace=true
```

2. Commit and push - ArgoCD will automatically sync the new application.

## Sync Waves

Use annotations to control deployment order:

```yaml
metadata:
  annotations:
    argocd.argoproj.io/sync-wave: "-1"  # Deploy before wave 0
```

Lower numbers deploy first.
