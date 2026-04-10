# Shared PostgreSQL

A single PostgreSQL StatefulSet serving multiple platform services.

## Overview

Instead of running separate PostgreSQL instances for each service, the platform uses a shared PostgreSQL deployment in the `platform-db` namespace. This reduces resource usage and simplifies management.

## Databases

| Database | User | Used By |
|----------|------|---------|
| `keycloak` | `keycloak` | Keycloak IdP |
| `backstage` | `backstage` | Backstage Developer Portal |

## Files

| File | Description |
|------|-------------|
| `namespace.yaml` | `platform-db` namespace |
| `statefulset.yaml` | PostgreSQL StatefulSet with init script |
| `service.yaml` | ClusterIP service |
| `kustomization.yaml` | Kustomize entrypoint |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    platform-db namespace                    │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │              PostgreSQL StatefulSet                   │  │
│  │                                                       │  │
│  │  ┌─────────────────┐  ┌─────────────────────────────┐ │  │
│  │  │ keycloak DB     │  │ backstage DB                │ │  │
│  │  │ user: keycloak  │  │ user: backstage (CREATEDB)  │ │  │
│  │  └─────────────────┘  └─────────────────────────────┘ │  │
│  └───────────────────────────────────────────────────────┘  │
│                            │                                │
│                            ▼                                │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  postgresql.platform-db.svc.cluster.local:5432       │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
            │                              │
            ▼                              ▼
┌───────────────────┐          ┌───────────────────┐
│     Keycloak      │          │    Backstage      │
│   (keycloak ns)   │          │  (backstage ns)   │
└───────────────────┘          └───────────────────┘
```

## Secrets

Secrets are created by `scripts/local-setup.nu` **before** ArgoCD syncs:

| Namespace | Secret | Keys |
|-----------|--------|------|
| `platform-db` | `postgresql-secrets` | `POSTGRES_PASSWORD`, `KEYCLOAK_DB_PASSWORD`, `BACKSTAGE_DB_PASSWORD` |
| `keycloak` | `keycloak-db-credentials` | `password` (same as `KEYCLOAK_DB_PASSWORD`) |
| `backstage` | `backstage-secrets` | `POSTGRES_PASSWORD` (same as `BACKSTAGE_DB_PASSWORD`) |

## Init Script

On first startup, the init script (`/docker-entrypoint-initdb.d/init.sh`) creates:

1. `keycloak` user and database
2. `backstage` user with `CREATEDB` privilege and database
3. Grants schema permissions

**Note:** The init script only runs on first database initialization. If passwords change, you must delete the PVC and re-initialize.

## Connection Details

Services connect using:

```
Host: postgresql.platform-db.svc.cluster.local
Port: 5432
Database: keycloak | backstage
User: keycloak | backstage
Password: (from respective secrets)
```

## Troubleshooting

### Check PostgreSQL Status

```bash
# Pod status
kubectl get pods -n platform-db

# Logs
kubectl logs -n platform-db postgresql-0

# Interactive psql
kubectl exec -it -n platform-db postgresql-0 -- psql -U postgres
```

### Verify Databases

```bash
# List databases
kubectl exec -n platform-db postgresql-0 -- psql -U postgres -c "\l"

# List users
kubectl exec -n platform-db postgresql-0 -- psql -U postgres -c "\du"
```

### Password Mismatch Issues

If services can't authenticate after a reset:

```bash
# Verify secrets match
kubectl get secret postgresql-secrets -n platform-db -o jsonpath='{.data.BACKSTAGE_DB_PASSWORD}' | base64 -d && echo
kubectl get secret backstage-secrets -n backstage -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d && echo
```

If they don't match, delete the PVC and restart:

```bash
kubectl delete pvc -n platform-db postgres-data-postgresql-0
kubectl delete pod -n platform-db postgresql-0
# Wait for new PVC and pod
```

## Sync Wave

PostgreSQL is deployed in **Wave 0** to ensure it's ready before Keycloak (Wave 1) and Backstage (Wave 2) start.
