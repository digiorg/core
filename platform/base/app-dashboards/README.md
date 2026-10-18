# App dashboard and inventory contract (issue 375)

Local implementation candidate only. No deployment or effective-permission claim.
Catalog is the per-app dashboard producer. Core owns the inventory/overview and
existing Grafana file-provider integration. This is a shared operator view, NOT
namespace/tenant authorization. Datasource access remains shared.

## Identity and lifecycle

App ID = dedicated namespace = AppClaim spec.appName, lower-case DNS label,
1–32 characters. UID `app-dev-<appId>` (at most 40 characters), ConfigMap
`<namespace>/grafana-app-dev-<appId>`, title `App · <appId>`, one dashboard for all
services. App dashboards use `DigiOrg Apps`; overview uses `DigiOrg Platform`.
Existing imports without a folder annotation keep their root placement.

The Composition requests all Application identities to reject duplicate owners,
and creates the dashboard after the exact namespace is Active. No image,
workload, database or SonarQube readiness is required. The dashboard Object is
asynchronous/ready to the composition: import failure must not gate workloads.
Stable UID/name survive template changes. Deleting the owning Application
removes only its derived dashboard through the Object finalizer. This feature
adds no namespace, metric-store or log-store deletion. Renames are migrations.

## Explicit non-Claim registration

`registrations.yaml` is a build-time GitOps source, not a CRD/controller. Start
with no entries; do NOT register system components or shared namespaces.
Example shape (not an active registration):

    apps:
    - contractVersion: 1
      appId: sample
      namespace: sample
      ownerTeam: platform-team
      dedicatedNamespace: true
      dashboardDesired: true
      expectedWorkloads:
      - kind: Deployment
        name: web

Supported expected kinds: Deployment, StatefulSet, DaemonSet. Empty expected
list explicitly means no workload requested. A dashboard can be disabled while
inventory remains visible. Duplicate IDs, Claim overlap, namespace mismatch,
foreign ConfigMaps and Grafana UID collisions fail before rendering/output.
Pending registrations stay in inventory before their namespace/workloads exist.

After the Catalog producer has been reviewed, published and canonically merged,
use its verified checkout and verified KCL CLI 0.12.7 (same language as the pinned
function-kcl). Do not run a speculative candidate as the canonical producer.
For offline component tests synthetic snapshots are allowed and labelled as such.
For an authorized delivery, capture a fresh read-only, secret-free snapshot with
arrays `appClaims`, `namespaces`, `configMaps`, `grafanaDashboards`; preserve
actual namespace status, reserved CM labels/annotations and dashboard UID/owner
provenance. Do not manufacture empty inventories when a read is unavailable.
A Grafana entry's `owner` is `gitops:<appId>` only when its existing ConfigMap
provenance has been verified; manual/unproven UID entries must remain foreign.

    python3 <verified-catalog>/scripts/render_app_registrations.py \
      --registrations platform/base/app-dashboards/registrations.yaml \
      --snapshot <secret-free-snapshot.json> --kcl <verified-kcl> \
      > <temporary-output.yaml>

The adapter evaluates the actual Composition KCL, filters only dashboard
ConfigMaps, and emits bounded constant inventory PrometheusRules. It does NOT
apply synthetic Applications, namespaces, workloads, privileges or telemetry
stores. Review the temporary output, then replace
`registered-apps.generated.yaml` and commit it with the registration source.
Never overwrite the output on a failed generator command. Rebuild when the
canonical template/registration changes. Argo owns these imports; removing an
entry prunes its derived CM/inventory only, preserving telemetry history.
An offline snapshot is not an atomic runtime lock: recheck collisions at the
separate approved delivery/acceptance gate; admission remains fail closed.

## Security and failure modes

Chris approved local S1/S2/S3 plus exact GET-based owner verification on the task.
KSM: AppClaim get/list/watch cluster-wide; CRD list/watch discovery; no new writes
or Secret access. Metrics export only bounded identity/service/condition fields.
Kyverno admission subject from pinned chart 3.8.1:
`system:serviceaccount:kyverno:kyverno-admission-controller`. Its supplemental
role is GET only on kubernetes.crossplane.io/objects. GET returns FULL Objects
and can expose embedded Secret manifests; projection is not RBAC restriction.
No list/watch/write, ProviderConfig or direct Secret API grant is added.

Reserved import label/folder metadata on OLD or NEW and exact canonical app CM
names are protected on CREATE/UPDATE/DELETE. Only the exact provider-kubernetes
and Argo application-controller subjects publish. No admin/group/namespace or
namespace-controller bypass exists. Foreign/absent ownership and cross-publisher
deletion are denied. Provider imports additionally GET `<appId>-grafana-dashboard`
and compare actual Application controller UID and desired CM target/identity.
The still-finalizing Object remains available for cleanup. Missing/unavailable
lookup fails closed; a foreign replacement can leave cleanup pending for explicit
operator resolution. Chris accepted that bounded design consequence, not any
additional residual risk or permission. No automatic cleanup repair is provided.
Existing provider cluster-admin and sidecar cluster-wide reads are limitations,
not tenant isolation or permission to add subjects/resources.

## Health, queries and remaining acceptance

AppClaim inventory plus registered inventory anchors rows, including pending
apps. Expected workload completeness, fresh feed, namespace state and replica
readiness precede green. Unknown/stale, provisioning, no requested workload,
scaled-zero and degraded are distinct. Expected-service Pod faults use controller
Pod→ReplicaSet→Deployment joins and direct StatefulSet/DaemonSet ownership;
completed/provisioning Jobs do not create service faults. Claim conditions are
shown separately from Kubernetes workload readiness; neither is business health.

Queries have literal namespace scope, escaped Pod/container refinements, exact
Prometheus UID `prometheus` and an exact escaped `OpenSearch (Logs)` datasource
selector (no guessed UID). Log identity fields are direct keywords; severity is
`level.keyword`, query-time case/alias grouping only. Missing levels are visible;
zero hits are NOT positive ingestion proof. CPU/memory/requests/limits, replicas,
restarts/OOM, pending/waiting, network and logs are shown. Missing limits are N/A,
never an invented denominator. No unsupported request/error-rate/latency panel.
Default last hour, 30s refresh, 200 raw log lines.

Local component tests are not live Grafana/sidecar, Crossplane/API-server webhook,
OpenSearch ingestion, effective-permission or cache-freshness acceptance. Exact
canonical Catalog pin integration is DEFERRED: Core still pins the prior canonical
Catalog. After producer merge, update that pin and its regression expectation in
a separately reviewed integration change. Then verify real import/deletion,
backfill, datasource uniqueness/mapping, rendered panels and scoped links, positive
synthetic log ingestion, fault/recreation/CRD-late behavior and bounded capacity.
Import time, query latency, reconciliation overhead and scale budgets remain
UNMEASURED here. Do not infer green live acceptance from component fixtures.
Rollback is reviewed source revert/template correction, never an automatic reset.
