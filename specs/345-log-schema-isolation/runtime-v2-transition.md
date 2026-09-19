# Issue #348 Runtime v2 Retained-Convergence Contract

## Authority and non-acceptance boundary

This candidate does not authorize publication and does not authorize rollout. It is prepared offline; review must not contact or mutate a cluster. `scripts/issue348_runtime_v2_transition.py` is the sole transition executable and supports only `retained-convergence`. Successful convergence is not acceptance: it does not start the 31-sample / 30-minute retained-cluster validation and it does not decide acceptance eligibility. A separate acceptance eligibility decision and explicit runtime approval are required.

The executable may mutate only the Argo CD application-controller replica count and the Root/Argo source revisions. It never patches Fluentd, OpenSearch, a workload, Pod, PVC, index, document, template, mapping, or shard. Reconciliation performs child changes. There is no automatic rollback after controller restore; evidence is preserved for operator review.

## Capability architecture and offline preflight

The reviewed boundary is `Adapters -> immutable Snapshot -> pure Validator -> MutationPlan -> Mutator`. Read-only adapters canonicalize remote-tag, cluster-identity, typed ApplicationList, controller, Pod, HPA, and Argo CD diff observations into one immutable Snapshot. The pure Validator has no filesystem, subprocess, clock, or mutation capability and consumes only that Snapshot plus the committed `issue348-runtime-v2-contract.json`. MutationPlan rendering is declarative and limits normal and rollback operations to the controller and Root/Argo owners with UID, resourceVersion, and current-value CAS preconditions. Only the dedicated Mutator module can turn an approved plan operation into a patch invocation.

`scripts/issue348_runtime_v2_preflight.py` is a standalone non-mutating entrypoint. Its dependency graph does not import or reference the Mutator. It requires the same explicit runtime identities, uses an enumerated read-only adapter, and writes only an exclusively-created local mode-`0600` evidence file. For Argo CD core diff it requires the supplied kubeconfig already to have the selected context current and namespace `argocd`; it does not edit or copy the kubeconfig. Successful evidence contains the canonical snapshot digest, exact identities and invocation, rendered proposed plan, `runtime_mutated=false`, and an empty mutation trace.

This repository change does not claim preflight success, publication, merge, convergence, runtime acceptance, or cluster validation. Running either entrypoint against live state remains separately authorized work.

## Immutable identities and source freeze

- product parent: `ff25a5083059412f82525ace73e7c20b322fddbf`
- correction base (published v4 candidate): `b32d1c18eb0d1048d8e38743f5fdd1c68a72936d`
- candidate tag literal: `issue348-runtime-v6-20260919T100440Z`
- immutable predecessor tag (not moved): `issue348-runtime-v4-20260918T162845Z`
- previous runtime tag: `issue350-352-runtime-v3-20260904T195619Z`
- previous peeled commit: `f6e7d58c0b03ee6a3ec6ed9e1e22e5023f861549`
- retained sibling tag: `issue301-runtime-v16-20260817T130820Z`
- retained sibling commit: `8e6b8908f99ebf76db47c15613eff523644c23f6`

The final graph contains exactly 32 first-party Core source entries: **5 candidate / 27 old / 0 previous / 0 other**. Candidate entries are Root `apps`, Argo CD `platform/base/argocd`, Fluentd `platform/base/fluentd`, OpenSearch `ref: values`, and OpenSearch `path: platform/base/opensearch`. Every unrelated Core source stays on the retained sibling tag. External chart, app-config, and core-catalog identities stay unchanged.

This graph promotes PR #349's Fluentd PreSync `fluentd-log-schema` hook and removes the superseded OpenSearch schema Job while retaining the #350 512 MiB heap and 1 GiB request/2 GiB limit. The schema hook is additive, waits fail-closed for yellow-or-better health, and neither deletes nor closes nor rolls over nor reindexes existing data. Existing PVC and index identity are outside this transition's mutation authority.

Current main also contains the reviewed Kyverno empty-annotation correction required to converge the previous runtime. The only non-source Application spec change permitted during convergence is that exact Kyverno Helm-values annotation addition. No unrelated spec change is accepted.

## Publication boundary

The runtime commit cannot contain its own commit or tree identity. After exact-snapshot approval, publication requires a separately recorded annotated tag-object, peeled commit, candidate commit, and tree SHA. This card does not publish them. A separate post-publication launcher and runtime approval are required before rollout.

## Invocation contract

The executable requires explicit `--kubeconfig`, `--context`, `--expected-server`, `--expected-kube-system-uid`, `--remote-url`, `--runtime-tag`, `--runtime-commit`, `--previous-tag`, `--previous-commit`, `--old-tag`, `--old-commit`, and a nonexistent external `--evidence` path. Kubeconfig permissions must be `0600` or stricter; evidence is exclusively created at mode `0600`.

```text
python3 scripts/issue348_runtime_v2_transition.py \
  --mode retained-convergence \
  --kubeconfig /secure/issue348-kubeconfig \
  --context issue348-retained \
  --expected-server https://api.retained.example:6443 \
  --expected-kube-system-uid <exact-uid> \
  --remote-url https://github.com/digiorg/core.git \
  --runtime-tag issue348-runtime-v6-20260919T100440Z \
  --runtime-commit <exact-published-runtime-commit> \
  --previous-tag issue350-352-runtime-v3-20260904T195619Z \
  --previous-commit f6e7d58c0b03ee6a3ec6ed9e1e22e5023f861549 \
  --old-tag issue301-runtime-v16-20260817T130820Z \
  --old-commit 8e6b8908f99ebf76db47c15613eff523644c23f6 \
  --evidence /secure/issue348-runtime-v2-convergence.jsonl
```

## Exact retained preflight

Before mutation, the typed Application endpoint must return exact `argoproj.io/v1alpha1 ApplicationList`, the reviewed 32 unique Applications, immutable UIDs/specs/operation identities, no pending top-level operation, and no active operation. All must be Healthy. Exactly 31 must be Synced. Kyverno alone may be OutOfSync. Its complete status inventory must equal the reviewed 69 canonical identities and statuses: 58 Synced and exactly 11 OutOfSync `CustomResourceDefinition` resources. Missing, null, and empty `group` values normalize only to the core group; duplicates, omissions, additions, malformed API identities, wrong statuses, namespace drift, and identity drift fail closed. For the pinned Argo CD CLI v3.4.5, the executable copies the explicit kubeconfig to an external mode-`0600` temporary file, switches that copy's current context to the selected `--context`, sets the current context namespace to `argocd`, and supplies the copy only through `KUBECONFIG`. It then runs exactly `argocd app diff kyverno --core --refresh`, without unsupported Argo CD `--kubeconfig`, `--kube-context`, or `--namespace` flags. The command must succeed with stdout and stderr both exactly zero bytes, and the isolated copy is removed before preflight continues or fails.

The candidate, previous, and old annotated remote tags must each peel to their exact supplied commit before Kubernetes is contacted. The preflight Core graph is exactly 3 previous / 29 old: Root, Argo CD, and OpenSearch values use the previous runtime; Fluentd and OpenSearch supplementary resources use the old tag. Their Argo status revisions must resolve to the exact previous and old commits, not merely to matching tag names. Root, Argo CD, OpenSearch, Fluentd, and Kyverno require structurally valid terminal operation identities. Controller identity/readiness, HPA absence, app-config revision, complete Application specs, and operation identities are captured and revalidated.

## Barrier, CAS, and rollback

The protocol CAS-scales only `StatefulSet/argocd/argocd-application-controller` to zero after testing UID, resourceVersion, and replicas. It proves zero desired/observed/ready replicas and no owned Pod, then repeats the complete preflight closure. While stopped, it CAS-patches only Root and Argo from the exact previous tag to the candidate tag and performs complete readback before restore.

Before controller restore, any failure triggers one bounded Root/Argo rollback to the previous tag and controller restoration. Rollback attempts and exact readback checks run independently for both owners before controller restore, so one owner failure cannot skip the other owner. Any incomplete closure remains a hard failure in the evidence. A third target, changed UID/spec, HPA, operation race, or resourceVersion race fails closed. Once restore is accepted, reconciliation may run and no automatic rollback occurs. The operational rollback boundary is forward-only after the schema hook has succeeded: preserve cluster state and escalate rather than reverting schema or data.

## Final convergence

Final closure requires Root, Argo CD, Fluentd, both first-party OpenSearch sources, and Kyverno to match the exact reviewed candidate graph/specs. Root fresh and Fluentd fresh successful operations must request and report the exact candidate commit; the Fluentd operation is the proof that its PreSync hook passed. Kyverno fresh must succeed at chart `3.8.1`. Argo CD and OpenSearch may be zero-render-diff reconciliations but require exact candidate status revisions and fresh RFC3339 reconciliation timestamps. The completed hook Job may be absent under `HookSucceeded`; exact NotFound is accepted only together with the fresh successful Fluentd operation.

All 32 Applications must be literally Synced and Healthy with no active operation. Exit zero requires three consecutive identical typed inventory/spec/UID/source/status samples separated by a positive bounded interval. Evidence ends with `convergence-only-not-acceptance`; it never records acceptance.

Any malformed response, source/spec/UID drift, hidden operation, stale operation/comparison, controller race, wrong graph, failed Fluentd operation, or deadline breach exits nonzero. No automatic retry is authorized.
