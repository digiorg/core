# Crossplane

This directory contains Crossplane configurations for infrastructure automation via Kubernetes-native APIs.

## Structure

```
crossplane/
├── providers/       # Provider installations and ProviderConfigs
│   ├── provider-kubernetes.yaml
│   ├── provider-helm.yaml
│   ├── provider-http.yaml
│   └── kustomization.yaml
├── xrds/            # Composite Resource Definitions (add here)
└── compositions/    # Compositions live in core-catalog, not here
```

## Installed Providers

### provider-kubernetes

Manages Kubernetes resources (Deployments, Services, ConfigMaps, etc.) in the local or remote clusters.
Used to compose higher-level abstractions that create Kubernetes workloads.

- Package: `xpkg.upbound.io/crossplane-contrib/provider-kubernetes:v0.15.0`
- ProviderConfig: `InjectedIdentity` — uses the `crossplane-provider-kubernetes` ServiceAccount token
- Permissions: `cluster-admin` ClusterRoleBinding on `crossplane-provider-kubernetes` SA

### provider-helm

Installs and manages Helm releases as Crossplane managed resources.
Used to compose platform capabilities that are delivered via Helm charts.

- Package: `xpkg.upbound.io/crossplane-contrib/provider-helm:v0.20.3`
- ProviderConfig: `InjectedIdentity` — uses the `crossplane-provider-helm` ServiceAccount token
- Permissions: `cluster-admin` ClusterRoleBinding on `crossplane-provider-helm` SA

### provider-http

Makes HTTP requests as Crossplane managed resources.
Used to integrate with external REST APIs and webhooks as part of compositions.

- Package: `xpkg.upbound.io/crossplane-contrib/provider-http:v0.5.1`
- ProviderConfig: `None` credentials — no cluster permissions required

## How ProviderConfigs Work

Each provider has a `ProviderConfig` named `default`. Managed resources reference it via:

```yaml
spec:
  providerConfigRef:
    name: default
```

The `InjectedIdentity` source means the provider pod uses its Kubernetes ServiceAccount token,
which is bound to `cluster-admin` to allow full cluster management. The `None` source (provider-http)
means no credentials are injected — the provider makes unauthenticated or externally-configured requests.

## Adding XRDs

Define new Composite Resource Definitions in `xrds/`. These declare the platform API that users interact with:

```yaml
apiVersion: apiextensions.crossplane.io/v1
kind: CompositeResourceDefinition
metadata:
  name: xapps.platform.digiorg.io
spec:
  group: platform.digiorg.io
  names:
    kind: XApp
    plural: xapps
  ...
```

## Adding Compositions

Compositions live in the **core-catalog** repository, not here. This keeps application-level
compositions separate from the platform provider infrastructure. Reference the XRD group and kind
from this repo when authoring compositions there.
