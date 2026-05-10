# External Secrets Operator

Synchronises secrets from external backends (Azure Key Vault, HashiCorp Vault, …) into Kubernetes Secrets, eliminating the need to store sensitive values in Git.

## Overview

ESO introduces two CRDs used across the platform:

| Resource | Scope | Purpose |
|----------|-------|---------|
| `ClusterSecretStore` | Cluster | Configures the secret backend (one per environment) |
| `ExternalSecret` | Namespace | Declares which keys to fetch and which K8s Secret to create |

## Dev vs. Production

| Aspect | Local (KinD) | Production |
|--------|-------------|------------|
| Provider | `Fake` (static values, no backend) | Azure Key Vault / HashiCorp Vault |
| ClusterSecretStore | `digiorg-dev-store` (this directory) | `digiorg-prod-store` (separate overlay) |
| Credentials required | None | Service principal / Vault token |

## Provider-Swap Pattern

All `ExternalSecret` resources across the platform reference `digiorg-dev-store` by name. To switch environments, **only the `ClusterSecretStore` needs to be replaced** — no changes to any `ExternalSecret`:

```
Local dev   → digiorg-dev-store  (Fake provider)
Production  → digiorg-prod-store (Azure KV / Vault)
```

Deploy the correct `ClusterSecretStore` for the target environment. Every `ExternalSecret` that references it will immediately resolve against the new backend.

## Production ClusterSecretStore Examples

### Azure Key Vault

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ClusterSecretStore
metadata:
  name: digiorg-prod-store
spec:
  provider:
    azurekv:
      vaultUrl: https://digiorg-prod.vault.azure.net
      authType: WorkloadIdentity
      serviceAccountRef:
        name: external-secrets
        namespace: external-secrets
```

### HashiCorp Vault

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ClusterSecretStore
metadata:
  name: digiorg-prod-store
spec:
  provider:
    vault:
      server: https://vault.example.com
      path: secret
      version: v2
      auth:
        kubernetes:
          mountPath: kubernetes
          role: external-secrets
```

## Files

| File | Description |
|------|-------------|
| `values.yaml` | Helm chart values (resource-constrained for KinD) |
| `cluster-secret-store-dev.yaml` | Fake provider store for local development |
| `example-external-secret.yaml` | Example showing the ExternalSecret pattern |
| `kustomization.yaml` | Kustomize entrypoint |

## References

- [ESO Documentation](https://external-secrets.io/latest/)
- [Fake Provider](https://external-secrets.io/latest/provider/fake/)
- [Azure Key Vault Provider](https://external-secrets.io/latest/provider/azure-key-vault/)
- [HashiCorp Vault Provider](https://external-secrets.io/latest/provider/hashicorp-vault/)
