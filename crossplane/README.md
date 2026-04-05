# Crossplane

This directory contains Crossplane configurations for multi-cloud infrastructure management.

## Structure

```
crossplane/
├── providers/       # Provider configurations
│   ├── aws.yaml
│   ├── azure.yaml
│   ├── gcp.yaml
│   └── ionos.yaml
├── xrds/            # Composite Resource Definitions
│   ├── database.yaml
│   ├── kubernetes.yaml
│   ├── network.yaml
│   └── storage.yaml
└── compositions/    # Compositions per provider
    ├── aws/
    ├── azure/
    ├── gcp/
    └── ionos/
```

## Concepts

### XRDs (Composite Resource Definitions)

XRDs define the **API** that platform users interact with. They are provider-agnostic:

```yaml
apiVersion: platform.digiorg.io/v1alpha1
kind: Database
metadata:
  name: my-database
spec:
  engine: postgres
  size: small
  provider: aws
```

### Compositions

Compositions define **how** the XRD is implemented for each provider:

- `aws/database.yaml` → Creates RDS
- `azure/database.yaml` → Creates Azure Database
- `gcp/database.yaml` → Creates Cloud SQL

### Providers

Providers are the Crossplane plugins that communicate with cloud APIs:

- `provider-aws`
- `provider-azure`
- `provider-gcp`
- `provider-kubernetes`
- `provider-helm`

## Adding a New Resource Type

1. Define the XRD in `xrds/`
2. Create Compositions for each provider in `compositions/<provider>/`
3. Document the new resource in this README
4. Update platform documentation

## Testing

Test compositions locally using the Crossplane CLI:

```bash
crossplane beta validate crossplane/xrds/ crossplane/compositions/
```
