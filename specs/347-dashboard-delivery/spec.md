# Bounded dashboard delivery specification

Authority: Kanban t_24070940, Chris's approved source-scope extension; related
issues #303 and #347. Standard lane, medium work, independent review required.
No publication, merge, deployment, data change or #348 reactivation is approved.

## Requirements

- LD-1: Offer a dashboard-only Kustomize source with the existing ConfigMap
  identity `logging/fluentd-grafana-dashboards`, labels, dashboard UIDs and
  byte-identical merged #365 payload. Preserve `level.keyword` and all filters.
- LD-2: Prepare a two-source Fluentd Application from an exact immutable writer
  Application snapshot. Keep the Application name, namespace, destination,
  finalizer, policy and writer revision. Suppress only the writer source's
  dashboard ConfigMap and provide it from the independently pinned source.
  No repeated-resource precedence, resource ownership transfer or prune gap.
- LD-3: Render the actual retained writer commit and prove that every resource
  except dashboard content remains identical. No schema Job is introduced,
  writer ConfigMap change, OpenSearch promotion or unrelated Application edit.
- LD-4: Fail closed on floating pins, unexpected source fields, destination,
  already-multi-source inputs and existing outputs. No external client calls.
- LD-5: Keep standalone Fluentd rendering compatible and keep #364 Grafana
  values unchanged. Full authoritative static gate and exact-snapshot review
  precede any publication. Runtime acceptance remains a separate prerequisite.

## Non-goals

No ingestion/schema correction, index mapping, deletion/reindex, new plugin,
chart/image upgrade, credentials, live sync, runtime repair or release runner.
The offline preparer is not a full Root/Argo runtime closure generator.

## Requirement-to-test matrix

LD-1/LD-5: test_grafana_log_explorer_fields.py, test_grafana_plugin_lifecycle.py.
LD-2/LD-3: test_log_dashboard_delivery.py, independent-source preservation and
actual retained-render inventory/body comparison.
LD-4: same test file, rejected input and exclusive-output CLI tests.
LD-5: canonical .github/workflows/platform-validation.yml with full history and
exact Kustomize in the regression job; standalone Fluentd render comparison.

Runtime acceptance must retain #345 -> #303 -> #347 ordering unless Chris
explicitly approves a limited UI-only acceptance boundary. Source separation
does not accept current ingest rejection or establish datasource health.
