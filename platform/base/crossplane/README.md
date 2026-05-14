# Crossplane

Crossplane is an infrastructure-as-code platform for multi-cloud resource provisioning via Kubernetes.

## Overview

Crossplane is **not installed via Kustomize**. It is installed by ArgoCD directly from the official Helm chart. This directory is an intentional stub — `kustomization.yaml` declares `resources: []` and the directory exists only to preserve the expected layout in Git.

The real configuration lives in [`apps/platform/crossplane.yaml`](../../../apps/platform/crossplane.yaml), which defines an ArgoCD Application that pulls and installs the Helm chart.

XRDs, Compositions, and Providers are managed separately in the [`crossplane/`](../../../crossplane/) directory at the repository root.

## Files

| File | Description |
|------|-------------|
| `kustomization.yaml` | Intentional stub; `resources: []` — no manifests are applied via Kustomize |

## Installation

Crossplane is installed via Helm through ArgoCD:

| Field | Value |
|-------|-------|
| Chart | `crossplane` |
| Repository | `https://charts.crossplane.io/stable` |
| Version | `1.19.0` |
| Release name | `crossplane` |
| Namespace | `crossplane-system` |
| ArgoCD sync wave | `3` |

**Sync policy:** automated with `selfHeal` and `prune` enabled.

**Retry policy:** limit 5, backoff 5s base, factor 2, max duration 3m.

## Resource Limits

| Component | CPU Request | CPU Limit | Memory Request | Memory Limit |
|-----------|-------------|-----------|----------------|--------------|
| `crossplane` | 50m | 200m | 128Mi | 512Mi |
| `rbac-manager` | 50m | 100m | 64Mi | 256Mi |

## XRDs and Compositions

Platform-level Crossplane resources (XRDs, Compositions, Providers) are defined in the [`crossplane/`](../../../crossplane/) directory at the repository root, which has its own README. The Helm install in [`apps/platform/crossplane.yaml`](../../../apps/platform/crossplane.yaml) is the prerequisite for those resources to be applied.

## Troubleshooting

```bash
# Check Crossplane pods
kubectl get pods -n crossplane-system

# Check installed Providers
kubectl get providers

# Check Provider health
kubectl describe provider <provider-name>

# List all CRDs installed by Crossplane
kubectl get crds | grep crossplane.io

# Check Crossplane logs
kubectl logs -n crossplane-system -l app=crossplane
```
