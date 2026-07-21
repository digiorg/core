# CloudNativePG (CNPG): Future Hosted-Application Database Infrastructure

**Issue:** #283 (corrects the migration/cutover framing originally introduced
in #275 and #281 — see [History](#history))
**Namespace:** `platform-db` (Cluster) / `cnpg-system` (operator)
**Status:** Optional infrastructure, not yet consumed by any application.

---

## 1. Authoritative architecture

CNPG is **not** a migration or replacement target for the internal platform
components' database. It never has been the intended design, and no manifest
in this repository may describe it that way.

| Statement | Status |
| --- | --- |
| Legacy PostgreSQL (`platform/base/postgresql`, Service `postgresql.platform-db.svc.cluster.local:5432`) is the permanent database for Keycloak, Backstage, Gitea, SonarQube and Harbor. | **Permanent.** Not scheduled for removal. |
| CNPG (`platform/base/cnpg`) is optional database infrastructure for **future applications hosted on the platform**. | Not yet consumed by anything. |
| Nothing on the core platform's dependency path requires CNPG to be healthy. | A failed, absent, or degraded CNPG must never block core-platform bootstrap or the core "Platform Ready" banner. |

Legacy PostgreSQL and OpenSearch are the platform's **core data layer** — see
`nu scripts/local-setup.nu up`, which brings both to *functional* readiness
(not just Kubernetes health) before their consumers start
(`wait_for_postgresql_ready`, `wait_for_opensearch_ready`).

## 2. What's deployed

| App | File | Wave | Source |
| --- | --- | --- | --- |
| `cnpg` (operator) | `apps/platform/cnpg.yaml` | 9 | Helm chart `cloudnative-pg` **0.29.0** |
| `cnpg-cluster` | `apps/platform/cnpg-cluster.yaml` | 10 | git `platform/base/cnpg` |

Both Applications are **script-driven** (not automated). Their waves order only
the Application CRs in the app-of-apps tree; they are not treated as a workload
readiness mechanism. The explicit command promotes the operator Application
first, observes a fresh or resumed Synced+Healthy operation, waits for its
Deployment and admission-webhook endpoint, and only then promotes the Cluster
Application. This preserves the webhook ordering fixed in Issue #281 without
starting any CNPG workload during core bootstrap.

**`nu scripts/local-setup.nu up` promotes neither CNPG Application — not even
wrapped in a try/catch.** An earlier "best-effort" design still executed
CNPG's bounded waits (operator availability, webhook readiness, up to 90
polls of the Cluster's sync operation) before catching the resulting error,
which delayed the core "Platform Ready" banner by however long CNPG took to
fail — exactly what Issue #283 prohibits. CNPG is instead provisioned only by
running the separate, explicit command below, whenever you actually need it,
after core bootstrap has already succeeded. Unlike that earlier design, this
command is intentionally **fail-closed**: a real CNPG problem surfaces as a
real, non-zero-exit error.

```bash
nu scripts/local-setup.nu future-infra
```

`platform/base/cnpg/cluster.yaml` defines a minimal, valid single-instance
`Cluster` (`postgresql-cnpg`) with:

- a digest-pinned PG16 image (`ghcr.io/cloudnative-pg/postgresql:16.9@sha256:…`);
- a single `bootstrap.initdb` database named `app`, owned by `app`;
- **no** internal-platform database or user (no `keycloak`, `backstage`,
  `gitea`, `sonarqube` database, no `registry`/`harbor`);
- **no** coupling to `postgresql-secrets` or any other legacy-platform
  credential — CNPG creates and owns its own `postgresql-cnpg-app`
  credentials Secret for the `app` database automatically. `scripts/local-setup.nu`
  does not provision any Secret for this Cluster.
- **no** backup configuration (`barmanObjectStore`) and **no** `pg_dump`
  CronJob — there is nothing to back up yet. Add real backup configuration
  when an application is actually provisioned onto this cluster.

## 3. Provisioning a future application

When a real application is ready to use this Cluster:

1. Create (or reuse) a dedicated `Database`/role for that application via
   CNPG's declarative `Database` CRD, or `bootstrap.initdb` on a
   **new, dedicated** Cluster if isolation is preferred — do not repurpose the
   `app` bootstrap database for a real workload.
2. Point the application's own connection Secret at CNPG's auto-generated
   owner credentials for that database (`kubectl get secret
   postgresql-cnpg-app -n platform-db`) — never at `postgresql-secrets`.
3. Add a `backup.barmanObjectStore` block and a `ScheduledBackup` once an
   object store (S3/MinIO) is available; there is no logical (`pg_dump`)
   backup path for this Cluster today.
4. Consider whether the application warrants its own dedicated `Cluster`
   rather than sharing `postgresql-cnpg`.

## 4. Local KinD reproduction

```bash
# Requires the core platform from `nu scripts/local-setup.nu up`.
# This promotes operator -> Available webhook -> Cluster, fail-closed.
nu scripts/local-setup.nu future-infra

kubectl -n platform-db wait --for=condition=Ready cluster/postgresql-cnpg --timeout=600s
kubectl -n platform-db get pods -l cnpg.io/cluster=postgresql-cnpg
kubectl -n platform-db get secret postgresql-cnpg-app -o jsonpath='{.data.dbname}' | base64 -d && echo
```

## 5. History

- **#275** introduced CNPG as a *Tier-3 migration target* intended to replace
  the legacy StatefulSet, reproducing all six internal-platform databases/
  users and a `pg_dump` backup of them, plus a dormant cutover alias Service.
- **#281** fixed a clean-bootstrap webhook race (the Cluster apply hit
  `connection refused` before the operator's admission webhook was ready) and
  added the webhook-readiness gate (`wait_for_cnpg_webhook_ready`) and
  fresh-operation promotion (`promote_cnpg_cluster`) still used today, plus a
  bootstrap Job to force-bind the (now-removed) backup PVC.
- **#283** corrected the domain model: CNPG is not, and never should have
  been, a migration/cutover target. All internal-platform database/user
  initialization, the backup PVC/bind hook/`pg_dump` CronJob, and the cutover
  alias Service were removed; the Cluster now uses only CNPG-owned Secrets;
  CNPG's Applications were moved to a late, decoupled sync wave; and its
  promotion moved out of `up` entirely into the separate, explicit,
  fail-closed `nu scripts/local-setup.nu future-infra` command — an earlier "best-effort" design
  still executed CNPG's bounded waits before catching the error, which
  delayed core bootstrap rather than truly decoupling from it.

## Limitations

- The #283 workflow was validated on a fresh Linux KinD cluster: the explicit
  command promoted the operator, observed a ready admission-webhook endpoint,
  and reconciled `postgresql-cnpg` to `Ready`. Production backup, restore, and
  disaster-recovery behavior remain outside that local validation scope.
- **Operator chart 0.29.0**, revalidated against
  `https://cloudnative-pg.github.io/charts/index.yaml`: newest release as of
  the #275 approval (appVersion 1.30.0). The Cluster image digest was
  verified against GHCR via `scripts/resolve_digest.py`.
