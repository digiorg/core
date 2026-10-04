# Log Dashboard runtime-v1 declarative consumer plan

Authority: preparation t_8e98c858 and independent review t_45ac37bb, round 1.
Chris approved bounded source preparation and the reserved proposed annotated
tag `log-dashboard-runtime-v1`, not publication, merge or deployment.

## Exact source and ownership boundary

Canonical source merge: `41b77b1b1af726563c7209f6d51f57a5ab1a03e4` (#369).
Retained repository Root/Argo: `f6e7d58c0b03ee6a3ec6ed9e1e22e5023f861549`,
tag `issue350-352-runtime-v3-20260904T195619Z`.
Retained writer: `8e6b8908f99ebf76db47c15613eff523644c23f6`, tag
`issue301-runtime-v16-20260817T130820Z`. These are historical inputs, not a
fresh live baseline. The merged [source guide](log-dashboard-delivery.md)
continues to govern dashboard isolation and authenticated acceptance.

The new explicit source directory is `platform/runtime/log-dashboard-v1`.
No release generator is added. Canonical `apps/`, bootstrap and platform base
resources remain unchanged. This avoids broad main promotion and avoids
reverting unrelated canonical improvements merely to prepare a retained graph.

Proposed owner graph (the tag-to-candidate mapping in offline tests is a
SIMULATION; it does not create a Git tag):

- Root: Core / `log-dashboard-runtime-v1` /
  `platform/runtime/log-dashboard-v1/apps`, recursive `*.yaml` directory.
- Root renders the original 31 child identities in namespace `argocd`.
- The `argocd` child: Core / `log-dashboard-runtime-v1` /
  `platform/runtime/log-dashboard-v1/argocd`.
- That Kustomize wrapper consumes `../../../base/argocd` and changes only Root's
  source path and revision. Every non-Root Argo base file equals the retained
  revision byte-for-byte. It renders exactly one original Root identity.
- Grafana keeps chart `87.17.0`, destination, policies, ignoreDifferences and
  manual `issue-275-manual` gate. Only Core values revision becomes #369 merge.
  Its values diff from the retained source is solely #364's plugins block with
  `preinstall_auto_update=false`; no datasource, image or chart change.
- Fluentd keeps its owner, destination, finalizer and sync policies. Source 1
  retains the writer commit and the exact reviewed dashboard delete patch.
  Source 2 selects `platform/base/log-dashboards` at #369 merge. Combined
  resources include the same seven identities, six unchanged non-dashboard
  bodies, one replaced dashboard body, no duplicates/pruned identities and
  no schema Job. Never reconcile source 1 alone.
- The other 28 children preserve every field, including OpenSearch, Kyverno and
  every unrelated source pin and inline configuration. 27 are byte-identical;
  Gitea only normalizes four inherited whitespace-only lines for diff hygiene.

## Publication contract — separate Chris gate

Freeze and independently review the complete candidate, tests and evidence.
After separately authorized normal source publication, run authoritative hosted
CI on the exact PR head, obtain Chris's approval and merge through normal PR
controls. No source change may be smuggled through a generated/tag-only commit.

Read back the ACTUAL final merge commit, ordered parents and tree. Do not assume
squash or two-parent topology. Require the reviewed tree and owner graph to
match exactly and verify ancestry/topology and hosted CI for the actual merge
identity before tagging. A changed tree or graph invalidates the verdict and
requires new exact-snapshot review. If topology or CI cannot be verified, stop.

Recheck both `refs/tags/log-dashboard-runtime-v1` and its peeled ref are absent.
Only after separate Chris tag-publication approval may an annotated tag of that
literal name be created targeting the actual final reviewed merge commit.
Read back tag type/object and peeled commit. Never move, overwrite or reuse the
tag; collision or wrong target stops publication. Local candidate commit,
offline resolver mapping and clean local tests are not a published closure.

## Bounded deployment ordering — no runtime argv authorized here

1. After authorized publication, Nadia obtains separate environment reservation
   and non-mutating preflight authority. Capture fresh Root/Argo specs, source
   revisions, all 31 child identities/specs, tracking/UIDs, workload identity,
   data/history and authenticated Grafana/TLS route. Compare with the full prior
   graph. Drift or missing access is a stop, not implicit permission to repair.
2. Verify actual Argo multi-source rendering at the published immutable identity
   and exact supported controller/CLI. No repeated-resource warning, SSA/prune
   identity loss, writer/schema change or unrelated child/config drift is allowed.
   Verify the old retained Argo source would restore old Root: the returning
   owner must transition coherently with Root, not a Root-only blind patch.
3. Chris must approve the exact supported full-owner transition mechanism and
   argv after preflight; this plan does not implement a live runner. Transition
   Root's source path/revision and the self-managed Argo owner through the normal
   declarative owner route as one bounded transaction. Establish both desired
   owner links and actual synced revisions before claiming convergence. No
   direct child patch, selected-resource sync, reset or #348 runner is permitted.
4. Root may change Fluentd's desired multi-source spec under existing automatic
   policy. Grafana remains manually gated: setting its values pin alone does
   not prove Grafana workloads consumed #364. Separately authorize its complete
   Application reconciliation only after owner/preflight checks. Establish
   logs/traces plugin registration and datasource health before dashboard query
   acceptance. Preserve #345 -> #303 -> #347 order unless Chris explicitly
   approves a limited UI-only boundary. Ingest rejection remains unresolved.
5. Stop at first failed boundary. Record status/counts/schema/hashes only, no raw
   logs/secrets. No retries, repair, credential rotation or risk acceptance is
   implied. Live Argo and authenticated Grafana remain external acceptance gates.

## Coherent rollback — not executed

Preserve both full graphs before mutation. The prior Root and Argo link both
use `issue350-352-runtime-v3-20260904T195619Z` at the original paths `apps` and
`platform/base/argocd`. Restore the complete prior owner graph through a
separately approved supported full-owner transaction. The old Root then renders
all 31 original child specs, including the WHOLE prior single-source Fluentd
Application (writer tag resolving to the retained SHA). This restores the
original dashboard in the same seven-identity inventory; never remove source 2
while leaving source 1's delete patch in place. No tag is moved or deleted.

Grafana's old values source must be restored through its manual gate. The old
plugin baseline is known defective; rollback is not a claim of healthy recovery
and Chris decides that residual risk. No mapping reversal, reindex, historical
index deletion (including 1970 data), schema rollback or writer change is in
scope. A content-only rollback could instead use a newly reviewed immutable
standalone dashboard source, but no such revision is invented by this plan.

## Verification and pending boundaries

`test_log_dashboard_runtime_candidate.py` covers 31-child inventory/28 byte
preservations, exact three child deltas, actual Argo wrapper rendering and
closed owner paths. Existing separation contracts render actual retained
writer resources. Full canonical static gates remain required at exact candidate
and hosted PR/merge revisions. The consumer wrapper must also be explicitly
rendered: the existing base-directory CI discovery does not include runtime/.
The focused regression test executes its real Kustomize build in hosted CI.

Source review is not live multi-source/controller acceptance. Authenticated
Grafana UID `digiorg-log-explorer`, five variables/six panels, exact filter/query
comparison in an absolute window, expected workload UID/imageID/restart state
and at least ten minutes of stability remain pending. Windows clean/resume and
full issue closure are not established. No runtime access is part of preparation.
