# Explicit retained consumer specification

Authority: approved task t_8e98c858, review t_45ac37bb (round 1). Extend
../347-dashboard-delivery/spec.md without reopening #348.

RC-1: Preserve all 31 retained child identities and all 28 unrelated complete
specifications from f6e7d58c0b03ee6a3ec6ed9e1e22e5023f861549. 27 manifests are
byte-identical; only four inherited Gitea whitespace-only lines normalize. Preserve policies,
manual gates, external pins, OpenSearch, Kyverno, writer/schema and destinations.
RC-2: Provide explicit declarative closure under platform/runtime/log-dashboard-v1.
Root consumes its apps/; Argo consumes its argocd/ wrapper, which renders Root.
Both cyclic references use reserved log-dashboard-runtime-v1. Only necessary
path/revision links change; no ownership or ignoreDifferences exception.
RC-3: Grafana values and Fluentd dashboard consume canonical merge
41b77b1b1af726563c7209f6d51f57a5ab1a03e4; writer retains exact
8e6b8908f99ebf76db47c15613eff523644c23f6. Preserve seven resource identities,
six non-dashboard bodies and no schema Job, duplicates or prune gap.
RC-4: No generator, canonical source revert, publication, tag creation, cluster
access, deployment or risk acceptance. Offline tag resolution is simulation.
Future annotated tag targets actual reviewed merge after tree/topology/CI checks;
changes invalidate review; collision stops publication, never move/overwrite.
RC-5: Freeze graph/delta, exact candidate/parent/tree, real RED/GREEN and full
local canonical static gates. Durable bundle/evidence precede phase completion.
Live Argo/authenticated Grafana and hosted CI remain separate gates.

Tests: RC-1 inventory/byte contracts; RC-2 actual Kustomize owner render;
RC-3 exact preparer/Grafana tests and retained separation render; RC-4 scope
report and guide; RC-5 full gate and durable evidence manifest.
Deployment/rollback: docs/guides/log-dashboard-runtime-v1.md.
