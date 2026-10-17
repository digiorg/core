# Issue 375 — app observability implementation specification (draft)

Status: local implementation candidate; exact validation/freeze state is recorded in the delivered external evidence manifest. No publication or deployment. Component proof is not live acceptance.
Owner: Scott / platform-engineer. Standard lane, large cross-repository change.
Controlling task: t_278943ce; independent review: t_76da5507 (not yet bound).

## Authorized outcome and boundaries

The task authorizes local implementation and testing of Core issue 375, superseding the issue's older creation-only boundary. No push, PR, merge, deployment, live tests, credentials/trust mutation, new controller, chart/plugin upgrade or datasource UID migration is authorized. Chris retains security-scope and residual-risk decisions. Portal is excluded.

Core base: ca30a538648ef46a55f3f4da511e1ed0fb60e80d.
Catalog base: 2867d1b4d47f472c7bb4097d61226e206e32a5a1.
Both isolated local branches: feat/375-app-dashboards; neither tracks AGENTS.md.

## Confirmed requirements (not reopened)

R1. Every AppClaim app plus explicitly GitOps-registered dedicated-namespace non-Claim app receives one distinct dashboard, not one per service. Reject shared/ambiguous namespace ownership. System workloads are separate.
R2. Catalog structured KCL/JSON is the canonical per-app producer. UID `app-dev-<appName>` is at most 40 characters; app ID is at most 32. Title `App · <appName>`. Stable identity; no foreign adoption, implicit rename or overwrite.
R3. Create the derived ConfigMap once namespace Active, independently of image/workload/SonarQube readiness. Reconciliation updates the same UID; deletion removes only derived dashboard resources and does not delete telemetry history or add namespace deletion behavior.
R4. Core owns inventory, overview and folder integration. Folders are DigiOrg Apps and DigiOrg Platform; existing unannotated dashboard placement is preserved. One file provider; no duplicate imports.
R5. Existing Prometheus UID `prometheus`; logs datasource type `elasticsearch`, exact escaped name `OpenSearch (Logs)`, no guessed UID. Default last hour, 30-second refresh, at most 200 log lines. Missing or ambiguous datasource selection is visible, not success.
R6. Inventory contains pending AppClaims and expected service rows before workloads exist. Fresh inventory and telemetry are prerequisites to Healthy; distinguish provisioning, no requested workloads, scaled-to-zero, missing services, stale/unknown feed and faults. No logs observed is not positive ingestion.
R7. All queries, variables and links have literal app scope and correct datasource escaping. Pod→ReplicaSet→Deployment joins retain namespace and controller ownership; support StatefulSet/DaemonSet owners. Missing resource limits remain N/A. Include CPU/memory/request/limit/replica/restart/OOM/network/log panels, not unsupported request/error/latency instrumentation.
R8. Shared operator visibility is not tenant isolation. Protect reserved dashboard import metadata using an explicitly approved publisher boundary; inherited cluster-admin is not permission to broaden access.

## Bounded data contract

The exact registration shape and build-time generation/validation procedure are in platform/base/app-dashboards/README.md. Non-Claim registration consumes the same real Catalog Composition KCL and emits only GitOps dashboard ConfigMaps plus bounded inventory PrometheusRules; it never applies synthetic Applications or workloads. Removal prunes derived output only. Missing ownership/collision inventory fails closed. Core integration keeps the canonical Catalog pin deferred until the producer is canonically merged.

Claim app ID and dedicated app namespace equal spec.appName, not claim metadata.namespace. Add namespace label platform.digiorg.io/app-id; retain type/team labels. Registration fields: contractVersion, appId, namespace, ownerTeam, dedicatedNamespace, expectedWorkloads (kind/name), dashboardDesired. Reject duplicate IDs, namespace reuse, unmanaged UID/ConfigMap ownership and identity changes. Registration remains declarative; no second manual JSON template. Exact adapter mechanism must consume the canonical Catalog producer without a speculative canonical pin.

KSM custom state exports only app identity, namespace, claim identity, expected service names and bounded conditions: digiorg_app_info and digiorg_app_service_info plus condition metrics. Narrow namespace label allowlist: platform.digiorg.io/type, platform.digiorg.io/app-id, platform.digiorg.io/team. No arbitrary specs, annotations, log text or Secrets become metric labels. Verify pinned KSM 2.19.1 output and CRD-late startup before considering the configuration complete.

Log identities use direct keyword kubernetes.namespace_name/pod_name/container_name, not .keyword aliases. level.keyword is the level aggregation field. Proposed dashboard query normalization groups case/aliases (error/err/fatal/critical, warn/warning) without modifying Fluentd ingestion or historical documents; unknown/missing levels remain visible and are not counted as successful/non-error coverage.

## Security preflight and approval boundary

Read-only source and exact pinned-chart RBAC inventory is in the task workspace's evidence/security-scope-decision.md. That workspace evidence is a preflight aid, not a portable repository dependency. The final delivery must package evidence separately.

S1/S2, S3 canonical-name/cross-publisher reservation and the GET-based actual-controller verification were explicitly approved by Chris for LOCAL implementation/tests on t_278943ce. KSM adds only AppClaim get/list/watch and CRD list/watch. Supplemental Kyverno RBAC grants only exact rendered admission subject get on kubernetes.crossplane.io/objects. That is FULL-resource GET and may expose embedded Secret manifests; it is not metadata-only permission. The policy reads the canonical still-finalizing Object, verifies Application controller UID and desired target/identity, and fails closed on absent/mismatched/unavailable evidence. No additional publishers, namespace-controller bypass, blanket group/admin exception, direct Secret API or live grant is added. A foreign replacement can leave cleanup pending for explicit operator resolution; Chris accepted that bounded local-design consequence. Real pinned Kyverno component tests exercise the actual API context GET against an isolated loopback fixture; they do not prove live effective permissions or a running Crossplane finalizer.

## Delivery, validation and rollback

After security-scope approval: vertical RED/GREEN implementation, real KCL/parsed JSON, pinned chart and provider component fixtures, then full workflow-locked local gates. Freeze base/parent/commit/tree, diffs, logs, portable bundles and hashes; reconstruct in fresh directories. Simon binds existing review round 1. Catalog producer must be reviewed, published and canonically merged before Core pins it; local Core candidate leaves the canonical pin unchanged and explicitly defers integration.

Source rollback is reviewed revert/template correction with stable UIDs. No automatic runtime repair. Authenticated Grafana import/datasource/panel checks, positive synthetic log ingestion, fault/lifecycle testing and capacity measurements require separately authorized runtime acceptance. No measured import SLO or capacity claim exists.
