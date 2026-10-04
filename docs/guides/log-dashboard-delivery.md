# Log dashboard delivery and rollback

Source scope: #303/#347; preparation t_24070940. Publication, merge, shared
runtime use and deployment each require Chris's separate authority. This guide
is not a runnable runtime transition and does not reactivate closed #348.

## Ownership and independent pins

The canonical dashboard payload is now in `platform/base/log-dashboards`.
Standalone `platform/base/fluentd` references that base and therefore keeps
exactly the same rendered resources as before this source change.

For bounded retained delivery use TWO sources in the SAME `argocd/fluentd`
Application, not a new Application:

1. Original Core `platform/base/fluentd`, exact retained writer commit, with one
   targeted Kustomize `$patch: delete` for `fluentd-grafana-dashboards`.
2. Core `platform/base/log-dashboards`, exact independently reviewed/merged
   dashboard commit, rendering only that ConfigMap in namespace `logging`.

The delete patch acts only during manifest generation. No live delete command
is issued. The combined desired inventory still includes exactly one dashboard
ConfigMap under its original Application owner. It never relies on Argo CD's
last-source-wins repeated-resource behavior. Same Application sync/prune sees
no resource removal or ownership transfer. Do not sync the writer source alone.
The whole multi-source Application must be considered atomically in preflight.

At diagnosis, writer was `8e6b8908f99ebf76db47c15613eff523644c23f6`.
Revalidate it before an authorized deployment; this dated identity is not a
live-state claim. Retaining it means no schema PreSync Job and no writer change.
After separately approved schema work, a writer pin containing the hook can be
selected independently; a schema-inclusive sync may execute that hook and is
NOT a dashboard-only rollout. Keep its separate migration and risk contract.

## Offline source preparation

With an approved, immutable-pinned local Application snapshot:

    python3 scripts/log_dashboard_delivery.py \
      --application retained-fluentd.yaml \
      --dashboard-commit <exact-reviewed-merged-40-hex-commit> \
      --output proposed-fluentd.yaml

The tool has no network, Git, cluster or Argo client capability. It creates a
new file, rejects floating revisions/unknown overrides/multi-source inputs and
preserves all fields except `spec.source` -> `spec.sources`. Syntax-valid SHAs
are NOT provenance verification: verify repository membership, content, merge,
review and exact-SHA CI separately. Dashboard SHA must contain the new base.
The existing Grafana manual-upgrade policy remains untouched.

## Publication and immutable Root/Argo consumer plan

1. Freeze/review this source candidate, then obtain Chris's source publication
   approval. A normal source PR must pass exact-head authoritative CI and be
   approved/merged by Chris. Read back canonical merge commit/tree.
2. In a NEW approved consumer preparation scope, capture the complete fresh
   retained Root/Argo and child graph. Prepare a normal reviewed source PR or a
   separately approved allowlisted deterministic closure; never tag unmerged
   repairs. Preserve every unrelated Application specification and revision,
   all OpenSearch sources, external chart/artifact pins, and writer pin.
3. Use the preparer on the exact retained Fluentd Application. Pin dashboard
   source to the canonical source merge SHA. Grafana keeps chart `87.17.0`;
   its Core values source may use that same canonical merge SHA ONLY after a
   byte diff proves the sole retained-to-new values change is merged #364's
   `[plugins] preinstall_auto_update=false`. No datasource/type/image change.
4. Root and self-managed Argo owners must point to the same immutable consumer
   revision. Avoid self-SHA cycles with a reviewed reserved immutable annotated
   tag, generated only under a newly approved deterministic allowlist contract,
   or a separately reviewed two-commit consumer graph. This preparer does NOT
   generate that graph. Do not promote Root to moving main or reuse the #348
   generator/runner. Before publication, freeze the actual closure graph and
   prove that only Root/Argo ownership pins, Grafana values pin and Fluentd's
   two-source representation differ; unrelated resource/spec/revision drift
   is a hard stop, not residual risk accepted by this guide.
5. Review exact consumer closure, obtain exact-SHA CI and Chris's publication
   approval, publish immutable identity and read back annotated tag object and
   peeled commit. Do not confuse a local source candidate with deployable closure.

This work provides the dashboard source and safe Application transformation;
the existing #348-specific delivery mechanism is intentionally not generalized.
No runtime argv can be authorized until the fresh Root/Argo closure and normal
supported rollout mechanism are separately settled and reviewed.

## Migration ordering and acceptance

After authorized publication, reserve `digiorg-core-dev`, preserve historical
logs, capture a fresh immutable Application/workload/data baseline and run a
separate non-mutating preflight. Verify the existing Grafana authenticated route
with TLS, approved CA, correct hostname/SNI, and an existing protected session.
Missing authentication blocks runtime acceptance, not offline implementation.

Maintain issue acceptance order #345 -> #303 -> #347 unless Chris explicitly
approves a limited UI-only boundary. Dashboard separation is not ingest repair
and does not accept partial record rejection. Establish both logs/traces
plugin registration and datasource health after #364 values reconciliation
before attributing #347 queries. Retain the manual Grafana gate; no chart update,
automatic retry, selected-resource sync or livepatch is supplied here.

After a separately approved supported full-Application reconciliation:

- Read actual target/synced source arrays. The writer pin and non-dashboard
  rendered bodies must be unchanged; no new schema Job, OpenSearch revision
  or unrelated Application change. There must be no repeated-resource warning.
- Read the live ConfigMap AND authenticated dashboard API for UID
  `digiorg-log-explorer`; sidecar import is not proven by a ConfigMap alone.
- Discover actual datasource UIDs; verify Elasticsearch registration and BOTH
  datasource health checks before recent queries. Do not guess UID or credentials.
- Verify six identity query/definition sites and two panel terms use direct
  Kubernetes keyword fields, while `level.keyword` stays. Five variables/six
  panels and existing filters/layout/raw logs remain. Container selection was
  not consumed by panels and is not newly promised.
- Use one absolute recent window and populated namespace/pod. Verify variables,
  default/selected filters and all six panel results against read-only OpenSearch
  aggregates. Record statuses, counts and timestamp ranges only, never raw logs.
- Observe at least a bounded ten-minute stability window: expected Pod/imageID,
  stable restart count, repeat health/query success and no plugin updater error.
  Windows clean/resume validation and full issue closure remain separate.

## Rollback

No rollback was executed. Before deployment freeze both complete prior and
proposed Root/Argo graphs, exact App UIDs/specs/revisions, dashboard data digest
and Grafana values hash; establish a supported manually approved restoration.

For a dashboard content rollback, keep the two-source shape and writer pin;
select an immutable dashboard commit containing the separate base and previous
payload. Do not pin the second source to the old retained SHA: it lacks that
path. Alternatively restore the WHOLE prior single-source Fluentd Application
through its declarative Root owner in one reviewed graph change. The original
source includes the dashboard so combined inventory remains stable. Never
remove the second source without simultaneously restoring first-source content.

Grafana values rollback restores the prior exact values pin with the manual
gate; the old plugin baseline is known defective, not a healthy recovery claim.
Restore Root/Argo ownership consistently, not via child-only live patches.
Any schema-inclusive writer rollout is separately controlled and forward-only
where mappings changed: no mapping reversal, reindex or historical deletion.
Stop at the first failed boundary and preserve evidence; Chris decides residual
risk, recovery and any next attempt.
