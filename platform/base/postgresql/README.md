# Shared PostgreSQL

A single PostgreSQL StatefulSet (`postgres:16-alpine`) serving multiple platform services.

## Overview

Instead of running separate PostgreSQL instances for each service, the platform uses a shared PostgreSQL deployment in the `platform-db` namespace. This reduces resource usage and simplifies management.

## Databases

| Database | User | Used By |
|----------|------|---------|
| `keycloak` | `keycloak` | Keycloak IdP |
| `backstage` | `backstage` (CREATEDB) | Backstage Developer Portal |
| `gitea` | `gitea` | Gitea (Git + CI/CD Pipelines) |
| `sonarqube` | `sonarqube` | SonarQube (Code Quality & Security) |

Only the `backstage` user is granted the `CREATEDB` privilege; all other users have database-scoped access only.

## Files

| File | Description |
|------|-------------|
| `namespace.yaml` | `platform-db` namespace |
| `statefulset.yaml` | PostgreSQL StatefulSet with init script (image: `postgres:16-alpine`) |
| `service.yaml` | ClusterIP service |
| `kustomization.yaml` | Kustomize entrypoint |

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          platform-db namespace                           │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │                    PostgreSQL StatefulSet                          │  │
│  │                                                                    │  │
│  │  ┌──────────────────┐  ┌─────────────────────────┐                │  │
│  │  │ keycloak DB      │  │ backstage DB             │                │  │
│  │  │ user: keycloak   │  │ user: backstage (CREATEDB│)               │  │
│  │  └──────────────────┘  └─────────────────────────┘                │  │
│  │  ┌──────────────────┐  ┌──────────────────────────┐               │  │
│  │  │ gitea DB         │  │ sonarqube DB             │               │  │
│  │  │ user: gitea      │  │ user: sonarqube          │               │  │
│  │  └──────────────────┘  └──────────────────────────┘               │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                  │                                       │
│                                  ▼                                       │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │        postgresql.platform-db.svc.cluster.local:5432               │  │
│  └────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
          │              │              │              │
          ▼              ▼              ▼              ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│   Keycloak   │ │  Backstage   │ │    Gitea     │ │  SonarQube   │
│ (keycloak ns)│ │(backstage ns)│ │  (gitea ns)  │ │(sonarqube ns)│
└──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘
```

## Secrets

PostgreSQL-related secret keys are created by `scripts/local-setup.nu` **before** ArgoCD syncs:

| Namespace | Secret | PostgreSQL-related keys |
|-----------|--------|------------------------|
| `platform-db` | `postgresql-secrets` | `POSTGRES_PASSWORD`, `KEYCLOAK_DB_PASSWORD`, `BACKSTAGE_DB_PASSWORD`, `GITEA_DB_PASSWORD`, `SONARQUBE_DB_PASSWORD` |
| `keycloak` | `keycloak-db-credentials` | `password` (same as `KEYCLOAK_DB_PASSWORD`) |
| `backstage` | `backstage-secrets` | `POSTGRES_PASSWORD` (same as `BACKSTAGE_DB_PASSWORD`) |
| `gitea` | `gitea-db-credentials` | `password` (same as `GITEA_DB_PASSWORD`) |
| `sonarqube` | `sonarqube-db-credentials` | `password` (same as `SONARQUBE_DB_PASSWORD`) |

The gitea and sonarqube consumer secrets follow the same pattern as `keycloak-db-credentials`; their exact names are defined in the respective application overlays.

## Init Script

On first startup, the init script (`/docker-entrypoint-initdb.d/init.sh`) creates:

1. `keycloak` user and database, then grants all privileges on the `public` schema
2. `backstage` user with `CREATEDB` privilege and database, then grants all privileges on the `public` schema
3. `gitea` user and database, then grants all privileges on the `public` schema
4. `sonarqube` user and database, then grants all privileges on the `public` schema

**Note:** The init script only runs on first database initialization. If passwords change, you must delete the PVC and re-initialize.

## Connection Details

Services connect using:

```
Host: postgresql.platform-db.svc.cluster.local
Port: 5432
Database: keycloak | backstage | gitea | sonarqube
User: keycloak | backstage | gitea | sonarqube
Password: (from respective secrets)
```

## Configuration

### Storage

The StatefulSet requests a **5Gi** `ReadWriteOnce` PersistentVolumeClaim via `volumeClaimTemplates`.

`PGDATA` is set to `/var/lib/postgresql/data/pgdata` — a subdirectory of the PVC mount point (`/var/lib/postgresql/data`). This avoids the `lost+found` directory conflict that PostgreSQL encounters on ext4-formatted PVCs when `PGDATA` points directly at the mount root.

### Resource Limits

| | Request | Limit |
|-|---------|-------|
| CPU | `200m` | `1000m` |
| Memory | `256Mi` | `512Mi` |

### Security Context

The pod runs as UID `70` / GID `70` (the `postgres` user in the Alpine image) with `fsGroup: 70`. This ensures correct ownership on PVC-backed directories without requiring an init container or a `chown` step.

### Health Probes

Both probes use `pg_isready -U postgres`:

| Probe | `initialDelaySeconds` | `periodSeconds` | `failureThreshold` |
|-------|-----------------------|-----------------|--------------------|
| Readiness | 5 | 5 | 6 |
| Liveness | 30 | 10 | 3 |

The higher `initialDelaySeconds` on the liveness probe prevents the kubelet from restarting the pod during slow first-run initialization (schema grants, large restore, etc.).

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

If they don't match, delete the 5Gi PVC and restart to force re-initialization:

```bash
kubectl delete pvc -n platform-db postgres-data-postgresql-0
kubectl delete pod -n platform-db postgresql-0
# Wait for new PVC and pod
```

## Sync Wave

PostgreSQL is deployed in **Wave 0** to ensure it's ready before Keycloak (Wave 1) and Backstage (Wave 2) start.
