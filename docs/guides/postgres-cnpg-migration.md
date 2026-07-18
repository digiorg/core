# PostgreSQL -> CloudNativePG (CNPG) Migration Runbook

**Issue:** #275 (Tier-3 migration of the shared PostgreSQL StatefulSet to a
CloudNativePG `Cluster`)
**Namespace:** `platform-db`
**Status:** Manifests landed; **data migration and legacy removal are DEFERRED**
until verified against a real cluster (see [Limitations](#limitations)).

---

## 1. What changes

| Aspect | Legacy (retained) | CNPG (new) |
| --- | --- | --- |
| Workload | `StatefulSet/postgresql` (`platform/base/postgresql`) | `Cluster/postgresql-cnpg` (`platform/base/cnpg`) |
| Image | `postgres:16-alpine@sha256:57c72f…` | `ghcr.io/cloudnative-pg/postgresql:16.9@sha256:cca50a…` |
| Primary service | `postgresql` (selector `app: postgresql`) | `postgresql-cnpg-rw` (CNPG-managed) |
| Consumer DNS | `postgresql.platform-db.svc.cluster.local:5432` | **unchanged after cutover** — `ExternalName` alias `postgresql` -> `postgresql-cnpg-rw` (enabled at cutover only) |
| Databases/users | 6 via `init.sh` ConfigMap | 6 via idempotent init `Job` |
| Per-service passwords | `postgresql-secrets` (Opaque) | **same** `postgresql-secrets` |
| Superuser secret | env from `postgresql-secrets` | `postgresql-cnpg-superuser` (`kubernetes.io/basic-auth`) |
| Storage | 5Gi PVC | 5Gi PVC |
| Backup | none | scheduled `pg_dump` CronJob + runbook |

Both PG majors are **16**, so the on-disk data format is compatible and no
`pg_upgrade` is required — migration is a logical dump/restore (or a PVC data
copy in a real cluster).

### The six databases/users (must all keep working)

| Database | Owner/user | Secret key (password) | Consumer |
| --- | --- | --- | --- |
| `keycloak` | `keycloak` | `KEYCLOAK_DB_PASSWORD` | keycloak |
| `backstage` | `backstage` (CREATEDB) | `BACKSTAGE_DB_PASSWORD` | backstage |
| `gitea` | `gitea` | `GITEA_DB_PASSWORD` | gitea |
| `sonarqube` | `sonarqube` | `SONARQUBE_DB_PASSWORD` | sonarqube |
| `registry` | `harbor` | `HARBOR_DB_PASSWORD` | harbor |
| (superuser) | `postgres` | `POSTGRES_PASSWORD` | init/backup jobs |

### Secret format

`postgresql-secrets` (Opaque) already carries the six `*_DB_PASSWORD` /
`POSTGRES_PASSWORD` keys used by the init `Job` and the legacy StatefulSet — the
CNPG init `Job` reuses it unchanged.

CNPG's `enableSuperuserAccess` + `superuserSecret`, however, **requires a
`kubernetes.io/basic-auth` Secret** with keys `username` (=`postgres`) and
`password`. The Opaque `postgresql-secrets` cannot be reused for this: a Secret's
`type` is immutable, and it has no `username`/`password` keys. The setup script
therefore provisions a **dedicated** secret `postgresql-cnpg-superuser`:

```bash
kubectl create secret generic postgresql-cnpg-superuser -n platform-db \
  --type=kubernetes.io/basic-auth \
  --from-literal=username=postgres \
  --from-literal=password="$POSTGRES_PASSWORD"      # same value as postgresql-secrets
```

`password` **MUST equal** `postgresql-secrets.POSTGRES_PASSWORD` so the init /
backup jobs (which authenticate with `PGPASSWORD=$POSTGRES_PASSWORD`) still
connect. `scripts/local-setup.nu` creates both secrets from the same generated
`postgres_password`, so they always match on a clean bootstrap.

---

## 2. Prerequisites / baseline capture

Run against the **legacy** primary before touching anything.

```bash
# Confirm consumers are healthy and the six databases exist.
kubectl -n platform-db exec statefulset/postgresql -- \
  psql -U postgres -c '\l' | grep -E 'keycloak|backstage|gitea|sonarqube|registry'

# Record row counts / a schema fingerprint per db for later comparison.
for db in keycloak backstage gitea sonarqube registry; do
  echo "== $db =="
  kubectl -n platform-db exec statefulset/postgresql -- \
    psql -U postgres -d "$db" -c \
    "SELECT count(*) AS tables FROM information_schema.tables WHERE table_schema='public';"
done
```

Save this output as the migration baseline.

---

## 3. Backup (logical dump of all six databases)

No object store is available in the local/dev environment, so backups are
**logical** (`pg_dump`), not `barmanObjectStore`. Take a full dump from the
**legacy** primary:

```bash
POD=$(kubectl -n platform-db get pod -l app=postgresql -o jsonpath='{.items[0].metadata.name}')
mkdir -p ./pgbackup
for db in keycloak backstage gitea sonarqube registry; do
  kubectl -n platform-db exec "$POD" -- \
    pg_dump -U postgres -Fc "$db" > "./pgbackup/${db}.dump"
done
# Roles/globals (passwords + grants).
kubectl -n platform-db exec "$POD" -- \
  pg_dumpall -U postgres --globals-only > ./pgbackup/globals.sql
```

The CNPG stack keeps this going automatically: `CronJob/postgresql-cnpg-pgdump`
runs nightly (02:00) and writes `pg_dump -Fc` of all six databases plus
`pg_dumpall --globals-only` to PVC `postgresql-cnpg-backups`.

---

## 4. Deploy the CNPG operator + Cluster

The operator and the Cluster are two separate ArgoCD Applications with ordered
sync waves:

| App | File | Wave | Source |
| --- | --- | --- | --- |
| `cnpg` (operator) | `apps/platform/cnpg.yaml` | 0 | Helm chart `cloudnative-pg` **0.29.0** |
| `cnpg-cluster` | `apps/platform/cnpg-cluster.yaml` | 1 | git `platform/base/cnpg` |

During coexistence the `cnpg-cluster` Application deploys **only** the Cluster +
init `Job` (see `platform/base/cnpg/kustomization.yaml`). The `postgresql`
`ExternalName` alias (`service.yaml`) is deliberately **not** synced yet — if it
were, both the legacy `postgresql` Application and `cnpg-cluster` would own a
Service named `postgresql` in `platform-db` and fight over it. The CNPG database
is reachable directly at `postgresql-cnpg-rw` until cutover.

```bash
kubectl apply -f apps/platform/cnpg.yaml           # operator (wave 0)
kubectl apply -f apps/platform/cnpg-cluster.yaml   # Cluster + init Job (wave 1)

# Wait for the Cluster to reach a healthy primary.
kubectl -n platform-db wait --for=condition=Ready cluster/postgresql-cnpg --timeout=600s
kubectl -n platform-db get pods -l cnpg.io/cluster=postgresql-cnpg
```

`Job/postgresql-cnpg-init` (sync-wave 3, a Sync hook) then creates the six
databases/users idempotently, sourcing every password from `postgresql-secrets`.
Re-running it is safe — it guards on `pg_roles` / `pg_database`.

```bash
kubectl -n platform-db logs job/postgresql-cnpg-init
CNPG=$(kubectl -n platform-db get pod -l cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name}')
kubectl -n platform-db exec "$CNPG" -- psql -U postgres -c '\l' # sanity
```

---

## 5. Restore / migrate the data

With the empty databases created by the init `Job`, load the dumps taken in
step 3 into the CNPG primary (reach it directly at `postgresql-cnpg-rw` — the
`postgresql` alias is not yet authoritative during coexistence):

```bash
CNPG=$(kubectl -n platform-db get pod -l cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name}')
BACKUP_DIR=${BACKUP_DIR:-./pgbackup}

# Roles already exist with the same names from the bootstrap Job. Preserve the
# ownership metadata recorded by pg_dump. Restoring without ownership metadata or
# as an application role would break owner-only schema migrations.
for db in keycloak backstage gitea sonarqube registry; do
  kubectl -n platform-db exec -i "$CNPG" -- \
    pg_restore -U postgres -d "$db" --clean --if-exists --exit-on-error < "${BACKUP_DIR}/${db}.dump"
done

# Verify that no application objects were silently reassigned to postgres.
for spec in "keycloak:keycloak" "backstage:backstage" "gitea:gitea" \
            "sonarqube:sonarqube" "registry:harbor"; do
  db=${spec%%:*}; owner=${spec##*:}
  kubectl -n platform-db exec "$CNPG" -- psql -U postgres -d "$db" -v ON_ERROR_STOP=1 -c \
    "SELECT n.nspname, c.relname, pg_get_userbyid(c.relowner) AS owner
       FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relkind IN ('r','S','v','m','f','p')
        AND pg_get_userbyid(c.relowner) <> '$owner';"
done
```

Verify against the baseline from step 2 (row/table counts must match).

---

## 6. Cut consumers over

Consumers reference `postgresql.platform-db.svc.cluster.local:5432` and need no
manifest change. Argo CD Applications reconcile independently, so a Git commit
alone is **not atomic**. Perform this observed handoff while writes stay frozen:

1. Scale every database-writing consumer to zero. Do not stop the legacy
   PostgreSQL pod yet.
2. Take a **mandatory final full dump** (there is no `pg_dump` delta mode) using
   the commands in section 3, writing to a new `pgbackup-final/` directory.
3. Restore that final dump into clean CNPG target databases by repeating section
   5 with `BACKUP_DIR=./pgbackup-final`; repeat the ownership and baseline
   counts/checksums. Keep consumers stopped until every check matches.
4. Commit both cutover manifest changes, but do not let either Application sync
   automatically:
   - remove the legacy `service.yaml` from
     `platform/base/postgresql/kustomization.yaml` while retaining its
     StatefulSet and PVC;
   - enable `service.yaml` in `platform/base/cnpg/kustomization.yaml`;
   - suspend automated sync/self-heal for both `postgresql` and `cnpg-cluster`.
5. Manually sync **only** the legacy `postgresql` Application with prune, then
   observe that `Service/postgresql` is gone:
   ```bash
   kubectl -n argocd patch application postgresql --type merge \
     -p '{"spec":{"syncPolicy":{"automated":null}}}'
   kubectl -n argocd patch application cnpg-cluster --type merge \
     -p '{"spec":{"syncPolicy":{"automated":null}}}'
   # Trigger/sync postgresql using the Argo CD UI/CLI, then:
   kubectl -n platform-db wait --for=delete service/postgresql --timeout=120s
   ```
6. Manually sync `cnpg-cluster`; verify that the new ExternalName Service exists
   and resolves to `postgresql-cnpg-rw` before restarting consumers.
7. Scale consumers up one at a time and run section 7. Re-enable automation only
   after all checks pass.

The legacy StatefulSet and PVC remain managed and available for rollback; only
its selector Service is handed off during this phase.

---

## 7. Validate each consumer

```bash
# DNS alias resolves to the CNPG primary
kubectl -n platform-db run dns --rm -it --image=busybox --restart=Never -- \
  nslookup postgresql.platform-db.svc.cluster.local

# Per-consumer smoke checks
kubectl -n keycloak   rollout status deploy/keycloak
kubectl -n backstage  rollout status deploy/backstage
kubectl -n gitea      get pods
kubectl -n sonarqube  get pods
kubectl -n harbor     get pods   # connects to db "registry" as user "harbor"
```

Confirm each app logs a successful DB connection and serves traffic.

---

## 8. Rollback procedure

Keep consumers stopped. Reverse the same ordered handoff; do not rely on a Git
revert to sequence two Applications. If CNPG accepted any writes after cutover,
first take a full dump from the frozen CNPG primary, restore it into clean legacy
databases with the ownership-preserving section-5 procedure, and verify counts
and ownership. The declared RPO is therefore zero only while this write freeze
and reverse restore succeed; otherwise stop and escalate rather than repointing
to stale legacy data.

1. Suspend automated sync for both Applications. Revert the cutover manifest
   commit so the legacy kustomization contains its selector Service and the CNPG
   kustomization no longer contains the alias.
2. Manually sync `cnpg-cluster` first and wait until the alias is deleted:
   ```bash
   kubectl -n platform-db wait --for=delete service/postgresql --timeout=120s
   ```
3. Manually sync the retained legacy `postgresql` Application, verify its
   StatefulSet is Ready, and confirm its selector Service endpoints point at the
   legacy pod.
4. Restart consumers one at a time, validate database connections, then restore
   automation. Any writes accepted by CNPG after cutover must be reconciled before
   rollback; this is why rollback is performed while writes remain frozen.

Both clusters use PG16 and the same application credentials, so connection
strings do not change. The rollback is not considered tested until this ordered
procedure has been exercised on a real cluster and evidence recorded.

---

## 9. Deferred: removing the legacy StatefulSet

**Do NOT delete `platform/base/postgresql` yet.** Issue #275 requires legacy
removal only *after* data is verified in a real cluster. This environment has no
Kubernetes cluster/kubectl/kind runtime, so the cutover/verification in steps
5–7 cannot be executed here. Once a real run confirms every consumer on CNPG and
the baseline matches:

1. Delete `apps/platform/postgresql.yaml` and `platform/base/postgresql/**` only
   after rollback evidence is accepted; let Argo CD prune the legacy workload.
2. Reclaim the legacy PVC after the agreed retention window.
3. Simplify the coexistence note in `platform/base/cnpg/service.yaml` because the
   alias is then the sole owner of the `postgresql` name.

---

## Limitations

- **No cluster / kubectl / kind / Docker runtime** in this environment: the data
  migration, consumer cutover and rollback were **not executed**. The CNPG chart
  and repository resources were Helm/Kustomize-rendered and passed strict
  kubeconform validation (missing CRD schemas ignored), plus
  `python3 platform/tests/test_cnpg_migration.py` and
  `python3 scripts/check_pins.py`.
- **Operator chart 0.29.0** was revalidated against the chart index
  `https://cloudnative-pg.github.io/charts/index.yaml` on 2026-07-18: `0.29.0` is the
  newest release (appVersion 1.30.0). The Cluster image
  `ghcr.io/cloudnative-pg/postgresql:16.9@sha256:cca50a…` was verified against
  GHCR via `scripts/resolve_digest.py`.
- **Superuser secret**: CNPG requires a **`kubernetes.io/basic-auth`** Secret
  (`username=postgres` + `password`). The Opaque `postgresql-secrets` cannot be
  reused (immutable type), so `scripts/local-setup.nu` provisions a dedicated
  `postgresql-cnpg-superuser` with the same password value (see
  [Secret format](#secret-format)).
- **Backups are logical (`pg_dump`)**, not `barmanObjectStore`, because no object
  store is available in dev. Add a `backup.barmanObjectStore` block + a
  `ScheduledBackup` when an S3/MinIO target exists.
- **Legacy removal is deferred** (section 9) until real-cluster verification.
