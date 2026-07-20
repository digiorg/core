# Platform component versions, pinning & upgrade/rollback

**Issue:** #275 (update and pin all component versions)
**Revalidated:** 2026-07-18 against upstream chart indexes, registries and
release notes. Chart versions and direct image references below are pinned in
Git; chart-managed transitive images and runtime promotion gates are called out
explicitly in sections 6–7.

This guide is the single reference for *what* is pinned, *why*, and *how* to
upgrade or roll back safely. It is enforced mechanically by
`scripts/check_pins.py` (run in CI, see `.github/workflows/platform-validation.yml`)
and the `platform/tests/` regression suite.

---

## 1. Pinning policy (enforced by CI)

`scripts/check_pins.py` scans source manifests under `apps/` and `platform/base/`
and **fails CI** on:

- direct container `image:` refs without an immutable `@sha256:` digest;
- Argo CD Helm `chart:` sources without an exact SemVer (no `latest`, branch,
  range `^ ~ >= *`, or `x` wildcard);
- non-first-party git sources without an immutable 40-hex commit.

The only allowed exceptions are the DigiOrg-built, kind-loaded images listed in
`scripts/pin-policy-allowlist.yaml` (each with a rationale). They use
`pullPolicy: Never` and a digest-pinned upstream base. OCI labels record build
inputs but are not attestations. Fluentd's two direct plugins are version-pinned;
its live Ruby transitive dependency resolution still requires a lockfile or
vendored/checksummed gems before the build is fully reproducible. Use
`scripts/resolve_digest.py <ref>` to obtain a digest for a tag without Docker.

`scripts/render_platform_charts.py` additionally renders every Argo CD Helm
source with its real inline/repository values and fails CI if a chart introduces
an untagged or floating (`latest`, `main`, `master`, `HEAD`) runtime image. Exact
tag-only transitive chart images are reported as warnings until their charts
expose digest overrides; they remain an explicit residual item in section 7.

---

## 2. Helm charts (Argo CD Applications)

| Component | Chart | Pinned | appVersion | Source |
| --- | --- | --- | --- | --- |
| Argo CD (bootstrap) | argo-cd | **10.1.4** | v3.4.5 | pinned in `scripts/local-setup.nu` (`--version 10.1.4`, both helm calls) |
| kube-prometheus-stack | kube-prometheus-stack | **87.17.0** | prometheus-operator v0.92.1 | `apps/platform/grafana.yaml`; CRDs pre-installed at v0.92.1 by `local-setup.nu` |
| CloudNativePG operator | cloudnative-pg | **0.29.0** | 1.30.0 | `apps/platform/cnpg.yaml` |
| External Secrets | external-secrets | **2.7.0** | v2.7.0 | `apps/platform/external-secrets.yaml` — CRs migrated v1beta1→v1 |
| Kyverno | kyverno | **3.8.1** | v1.18.1 | `apps/platform/kyverno.yaml` (3.8.2 available; 3.8.x patch-compatible) |
| OpenSearch | opensearch | **3.7.0** | 3.7.0 | `apps/platform/opensearch.yaml` |
| NATS | nats | **2.14.2** | 2.14.2 | `apps/platform/nats.yaml` |
| OpenCost | opencost | **2.5.27** | 1.120.4 | `apps/platform/opencost.yaml` — custom subpath UI preserved |
| Gitea | gitea | **12.6.0** | 1.26.1 | `apps/platform/gitea.yaml` — Redis→Valkey subcharts disabled |
| SonarQube | sonarqube | **2026.3.1** | 2026.3.1 | `apps/platform/sonarqube.yaml` — Community Build 26.5.0.122743 |
| Crossplane | crossplane | **2.3.3** | 2.3.3 | `apps/platform/crossplane.yaml` — v2, XRD stays LegacyCluster |
| Harbor | harbor | **1.19.1** | 2.15.x | `apps/platform/harbor.yaml` (retained — already current) |
| Jaeger | jaeger | **4.11.1** | 2.19.0 | `apps/platform/jaeger.yaml` (retained — already current) |

### cert-manager (raw manifest)

Pinned to **v1.20.1** in `platform/base/cert-manager/kustomization.yaml` (remote
release manifest URL). Routine update from v1.17.0.

---

## 3. Container images (digest-pinned)

Third-party images record a human-readable tag **plus** an immutable digest:

| Image | Tag | Used by |
| --- | --- | --- |
| `postgres` | 16-alpine | legacy PostgreSQL StatefulSet (transition; being replaced by CNPG) |
| `ghcr.io/cloudnative-pg/postgresql` | 16.9 | CNPG Cluster + init/backup jobs |
| `quay.io/oauth2-proxy/oauth2-proxy` | v7.15.3 | OpenCost + Jaeger auth proxies |
| `nginx` | 1.30-alpine | Harbor UI reverse proxy |
| `curlimages/curl` | 8.16.0 | Harbor OIDC/proxy-cache + OpenSearch bootstrap jobs |
| `busybox` | 1.37.0 | init containers |
| `natsio/nats-surveyor` | 0.9.10 | NATS Surveyor |
| `nats` | 2.14.2-alpine | NATS server (chart fullImageName override) |
| `natsio/nats-server-config-reloader` | 0.23.0 | NATS config reloader |
| `opensearchproject/opensearch` | 3.7.0 | OpenSearch chart primary image |
| `docker.gitea.com/gitea` | 1.26.1 | Gitea chart primary/init image |
| `sonarqube` | 26.5.0.122743-community | SonarQube Community Build |
| `ghcr.io/kyverno/readiness-checker` | v1.18.1 | Kyverno chart test/cleanup hooks |
| `ghcr.io/digiorg/core-portal` | b77e94a | Backstage — core-portal `main` HEAD (Issue #279; was initial-scaffold commit `48d262e`) |
| `ghcr.io/digiorg/core-landingpage` | 32a6777 | Landing page (release tag + digest, was `main`) |

### Tier-1 DigiOrg-built images (allow-listed, kind-loaded)

| Image | Upstream base (digest-pinned) | Build |
| --- | --- | --- |
| `opencost-ui:v1.120.4-basename-opencost` | opencost-ui v1.120.4 (commit 6384abbf) | `platform/base/opencost/ui-image/build.nu` |
| `digiorg/keycloak:26.7.0-optimized` | quay.io/keycloak/keycloak:26.7.0 | `platform/images/keycloak/build.nu` (`kc.sh build`, `start --optimized`) |
| `digiorg/fluentd:v1.19.2-debian-opensearch-1.0` | fluent/fluentd-kubernetes-daemonset:v1.19.2-debian-opensearch-1.0 | `platform/images/fluentd/build.nu` (pinned plugin gems) |

All three are built + kind-loaded during `nu scripts/local-setup.nu up`
(`build_opencost_ui_image`, `build_tier1_images`), docker-guarded and non-fatal.

### Crossplane providers (pinned package tags)

`provider-kubernetes:v1.2.1`, `provider-helm:v1.3.0`, `provider-http:v1.0.14`
(`crossplane/providers/packages/`) — latest crossplane-contrib releases, no upper
Crossplane bound, compatible with Crossplane v2.3.x.

---

## 4. Harbor proxy-cache (Tier 2)

`platform/base/harbor/harbor-proxy-cache-job.yaml` (Argo CD PostSync hook)
provisions pull-through proxy-cache projects via the Harbor REST API:

| Project | Upstream | Registry type |
| --- | --- | --- |
| `dockerhub-proxy` | https://hub.docker.com | docker-hub |
| `quay-proxy` | https://quay.io | quay |
| `ghcr-proxy` | https://ghcr.io | github-ghcr |
| `k8s-proxy` | https://registry.k8s.io | docker-registry |

The Job is idempotent (HTTP 409 = already-exists = success) and self-heals on
every sync.

> **Bootstrap exception (documented):** workload manifests still reference
> upstream registries by digest rather than the `harbor.harbor.svc/...proxy/...`
> mirror path. Harbor only becomes Ready in wave 2, so components that schedule
> earlier (and the kind node pulling their images) cannot route through Harbor
> during a cold bootstrap. Rewriting image refs to mirror paths is a follow-up
> that must keep the immutable digests and add a Harbor-independent bootstrap
> path; until then the digest pin (section 3) provides the reproducibility the
> proxy-cache would otherwise add.

---

## 5. Major migrations — breaking changes & validation

Each migration below has a dedicated regression test under `platform/tests/`.
They validate the manifests statically; **runtime data migration is deferred**
where noted (this environment has no cluster — see section 7).

### Gitea 10.6.0 → 12.6.0 (`test_gitea_migration.py`)
- **Breaking:** bundled cache moved Redis → **Valkey**; `valkey-cluster` is
  default-enabled. Disabled `valkey-cluster` + `valkey` (Gitea uses in-process
  session/cache + level queue). Removed the dead `redis-cluster` key.
- Bundled `postgresql`/`postgresql-ha` stay disabled (shared platform-db).
- **Rollback:** revert the chart pin + values change; Gitea 1.26→1.24 downgrade
  is not supported once DB migrations run, so snapshot the `gitea` DB first.

### SonarQube 10.8.1 → 2026.3.1 (`test_sonarqube_migration.py`)
- Calendar versioning; Community Build via `community.enabled` +
  `buildNumber: 26.5.0.122743` (chart default). All value keys unchanged.
- **DB migration:** SonarQube runs schema migrations on first start. Follow the
  supported LTA sequence — do **not** skip across LTAs. Back up the `sonarqube`
  DB before upgrading; downgrade is unsupported.
- **Rollback:** restore the DB snapshot and revert the chart pin.

### Crossplane 1.19.0 → 2.3.3 (`test_crossplane_migration.py`)
- v2 keeps `apiextensions.crossplane.io/v1` XRDs in **LegacyCluster** scope, so
  the existing XRD (`crossplane/xrds/application.yaml`) + Claims are unchanged.
  **Do not** flip the XRD to `scope: Namespaced` — that drops Claims in v2.
- Providers (`pkg.crossplane.io/v1`) + `DeploymentRuntimeConfig` unchanged.
- **CRD ordering:** upgrade the Crossplane chart (CRDs) before providers/XRDs
  (waves 3 → 4 → 6 → 7 already enforce this).
- **core-catalog:** Compositions live in `core-catalog`; v2 removed native
  patch-and-transform — any P&T Composition there must be converted to the
  function pipeline (`crossplane beta convert pipeline-composition`). Out of
  scope for this repo; validate `core-catalog` separately before promoting.
- **core-catalog pin (Issue #281 Phase 4):** `apps/platform/core-catalog.yaml`
  pins the **canonical reviewed merge commit** of core-catalog PR #17,
  `13b7a3b4a0b7a5f5e692dc6d5a3fa416852c4273`. This supersedes the earlier
  implementation SHA `4c30d9c3…`, which the merge commit is one commit ahead of
  with **no changed files** (identical deployed content) — the change is
  traceability only. The Application stays manually gated
  (`platform.digiorg.io/upgrade-gate: issue-275-manual`, no `syncPolicy.automated`);
  do not enable automatic catalog sync. `test_crossplane_migration.py`
  (`CoreCatalogCanonicalPinTest`) locks the canonical pin, the immutable 40-hex
  format, the manual gate, and that the superseded SHA is referenced nowhere.
  When the reviewed revision advances, update the manifest, this line, and that
  test's `CANONICAL_CATALOG_REVISION` together.
- **Rollback:** revert the chart pin (2.3.3 → 1.19.0) and provider pins; v2→v1
  downgrade of a cluster with namespaced XRs is unsupported, but this repo keeps
  LegacyCluster semantics so no XR conversion occurs.

### External Secrets 0.14.4 → 2.7.0 (`test_external_secrets_migration.py`)
- **Breaking:** ESO ≥ 0.17.0 removed `external-secrets.io/v1beta1`. Migrated
  `ClusterSecretStore` + `ExternalSecret` to `external-secrets.io/v1` (field-
  compatible for the fake-provider dev store). Verify every `SecretStore`/
  `ExternalSecret` in downstream repos is on `v1` before upgrading.
- **Rollback:** revert the chart pin; v1 CRs are also accepted by 0.14.x.

### Kyverno 3.8.1 CRD migration hook — clean install vs. upgrade (Issue #279)
- `crds.migration.enabled` (chart default `true`) renders a Helm
  **post-upgrade** hook Job (`kyverno-migrate-resources`) that migrates CRD
  contents from a prior Kyverno release. Helm only runs `post-upgrade` hooks on
  `helm upgrade`, never on a fresh `helm install` — but Argo CD renders every
  chart with `helm template` and maps `post-upgrade`/`post-install` hooks onto
  `PostSync` for **every** sync, with no install-vs-upgrade distinction. On a
  brand-new disposable cluster there is no prior Kyverno CRD state, so the hook
  fired as pure churn on every sync of a clean bootstrap (confirmed in issue
  #279).
- `apps/platform/kyverno.yaml` therefore pins `crds.migration.enabled: false`
  as the clean-install baseline used by `nu scripts/local-setup.nu up` and by
  default on any freshly-provisioned cluster.
- **To perform a real upgrade** of an existing cluster that has prior Kyverno
  CRD data (e.g. hopping across a Kyverno release that changes CRD storage):
  1. In a dedicated commit, set `crds.migration.enabled: true` in
     `apps/platform/kyverno.yaml`.
  2. Sync only the `kyverno` Application and confirm the
     `kyverno-migrate-resources` Job completes successfully.
  3. Revert `crds.migration.enabled` back to `false` in a follow-up commit so
     the next clean bootstrap does not re-run the migration hook.

### kube-prometheus-stack 72.6.2 → 87.17.0
- CRDs are pre-installed at prometheus-operator **v0.92.1** by `local-setup.nu`
  (matches the chart appVersion) and upgraded before dependents. Verify
  Prometheus, Grafana, ServiceMonitors, dashboards, persistence and the OpenCost
  integration after upgrade.

### PostgreSQL StatefulSet → CloudNativePG
See the dedicated runbook: **[postgres-cnpg-migration.md](./postgres-cnpg-migration.md)**.
Coexistence is conflict-free (the `postgresql` alias Service is not synced until
cutover); cutover and rollback are single, symmetric Git commits.

---

## 6. Promotion gates and upgrade procedure

The major migrations are deliberately **not auto-synced**. Their Applications
carry `platform.digiorg.io/upgrade-gate: issue-275-manual` and omit
`syncPolicy.automated`, so merging the version pins cannot launch every database,
CRD and operator migration at once. Promote them one at a time in this order:

1. External Secrets (back up CRs; verify all consumers use `v1`).
2. NATS (back up JetStream; verify clients and Surveyor).
3. kube-prometheus-stack (apply/verify CRDs first).
4. OpenCost (verify exporter and the authenticated `/opencost/` UI contract).
5. Gitea (database backup and restore evidence).
6. SonarQube (follow every required LTA hop; database backup/restore evidence).
7. Crossplane last, as five separately observed syncs: `crossplane`,
   `crossplane-providers`, `crossplane-provider-configs`, `crossplane-xrds`, then
   `core-catalog`. Do not promote the next Application until the previous one is
   Healthy and its CRDs/providers are established.

`nu scripts/local-setup.nu up` is the deliberate exception: it targets only the
named disposable local KinD cluster and explicitly starts these gated Argo syncs
one at a time, waiting for each Application to become Healthy before continuing.
Shared and production clusters must use the manual procedure below.

For each promotion:

1. Revalidate the target against the upstream chart index / release notes /
   security advisories (see the per-app comment for the source URL).
2. Update the pin in Git (chart `targetRevision` or image digest via
   `scripts/resolve_digest.py`). Update the matching `platform/tests/` test.
3. Run `make test`, render the chart with its repository values, and run
   `kustomize build platform/base/<component>`.
4. Take the required backup and manually sync only that gated Application.
5. Confirm `Synced`/`Healthy`, pods Ready without crash loops, and the
   component's ingress/OAuth/persistence/integrations.
6. Record restore/rollback evidence, then restore `syncPolicy.automated` for that
   Application in the promotion commit before moving to the next gate.

## 7. Rollback & residual validation

- **Rollback** is `git revert` of the pin commit for stateless components; for
  stateful ones follow the component-specific note in section 5 (restore the DB
  snapshot first). The CNPG cutover/rollback is documented separately.
- **Residual validation:** no Kubernetes cluster, Docker daemon or kind runtime is
  available here, so cluster-side upgrade/rollback, data migration and workload
  smoke tests were **not executed**. Static verification did include: all 12 Helm
  charts rendered with their repository values using Helm 4.2.3; every render
  passed kubeconform 0.8.0 in strict mode (with missing CRD schemas ignored);
  every Kustomize base rendered and passed the same validation; all direct image
  digests were resolved again from their registries; the pin checker, complete
  regression suite, and Nushell 0.114.1 parse checks pass.
- Exact chart packages transitively select additional operator/sidecar images that
  do not all expose digest fields through their values schema. The direct images
  and mutable chart defaults identified in this change are digest-pinned, but a
  fully Harbor-rewritten render remains a gated follow-up after Harbor is
  available during bootstrap. Do not mark the platform-wide Harbor mirror
  criterion complete until a real-cluster promotion verifies that path.
