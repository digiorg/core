# Issue #348 runtime validation addendum

This document is the stable validation child of the Issue #345 capability specification. It does not restate or broaden that capability. It defines the evidence and authority boundaries for Issue #348 only. Repository verification is not runtime acceptance, and no checked item below implies permission to publish or contact a cluster.

## Stable requirements

- **I348-V01 — Live-contract provenance and raw shape.** The sanitized fixture must identify the read-only capture command, Argo CD/Kubernetes API versions, capture digest, redactions, and source review. It must preserve the observed raw `ApplicationList` shape, including omitted core-resource `group` fields, rather than normalizing the fixture before validation.
- **I348-V02 — Exact CLI/parser gate.** Every source candidate must pass the parser smoke with the checksum-pinned Argo CD v3.4.5 binary and must report zero skipped Issue #348 tests. A substitute binary or skipped parser test is a failure.
- **I348-V03 — Qualification architecture.** Runtime qualification remains `Adapters -> immutable Snapshot -> pure Validator -> MutationPlan -> Mutator`. Only the Mutator has write capability; the Snapshot, Validator, and plan are deterministic values.
- **I348-V04 — Non-mutating preflight.** The standalone preflight must have no mutation dependency, must reject an adapter exposing write capability, and may report success only with `runtime_mutated=false` and an empty mutation trace.
- **I348-V05 — Deterministic release closure.** After merge, the reviewed generator may change only the explicit 32 first-party `targetRevision` fields to the exact 5 candidate / 27 retained / 0 previous / 0 other graph. It must preserve external chart, app-config, and core-catalog identities, emit a deterministic diff inventory, and reject any path or field outside its allowlist.
- **I348-V06 — Source-candidate review.** An independent immutable source-candidate review is required before any pull request, merge, closure generation, tag, publication, or cluster action. The verdict must name the exact source commit and tree.
- **I348-V07 — Normal repository delivery.** Delivery requires a normal pull request, required exact-SHA CI on the reviewed head, human approval, and a human-authorized squash merge. Review, CI, approval, and merge identities must be recorded separately.
- **I348-V08 — Post-merge closure and tag.** Only the canonical merge commit/tree may be supplied to the generator. Closure generation and annotated tagging are separate post-merge actions. The tag object and peeled closure commit must both be recorded; a tag name alone is insufficient.
- **I348-V09 — Publication-closure review.** A separate immutable publication-closure review must bind source commit/tree, closure commit/tree, closure manifest digest, annotated tag object, peeled commit, exact CI run/head SHA, both review references, generator version, and descriptor/fixture digests.
- **I348-V10 — Post-publication preflight.** Publication does not authorize rollout. A separately authorized read-only preflight must run after publication against exact remote identities and must preserve zero mutation evidence.
- **I348-V11 — Convergence authorization.** Retained-cluster convergence requires a new explicit human authorization after successful post-publication preflight. Source review, merge, publication, and preflight are not convergence authorization.
- **I348-V12 — Acceptance authorization.** The 31-sample / 30-minute acceptance observation requires another explicit human authorization after convergence evidence has been reviewed. Convergence is not acceptance.
- **I348-V13 — Validation attempt ledger.** Every attempted preflight, convergence, or acceptance run must append an immutable ledger entry with attempt ID, phase, authority reference, start/end time, exact source/closure/tag identities, result, and supersession relationship. Failed attempts remain visible.
- **I348-V14 — Evidence fields.** Evidence must include exact invocation, tool versions/digests, cluster identity, application/source graph, snapshot digest, plan digest, mutation trace, operation/reconciliation identities, bounded deadlines, rollback attempts, failure classification, and artifact SHA-256 digests. Credentials, raw Secrets, and tokens are forbidden.
- **I348-V15 — Rollback boundary.** Before controller restore, bounded owner rollback and controller restoration follow the reviewed plan. After restore or schema-hook success, there is no automatic data/schema rollback; preserve state and escalate for a human decision.
- **I348-V16 — No retry.** no automatic retry is authorized for failed CI, generation, preflight, convergence, or acceptance. A retry requires evidence that the candidate is unchanged, classification of the failure as an infrastructure flake where applicable, and explicit human authorization.

## Ordered status

| Order | Gate | Status | Evidence boundary |
|---:|---|---|---|
| 1 | Live fixture provenance/raw-shape contract | Complete (offline) | Sanitized v3.4.5 fixture and manifest are committed; no live recapture claimed. |
| 2 | Exact CLI/parser gate | Complete (offline) | Checksum-pinned v3.4.5 parser tests passed in the source workspace. |
| 3 | Snapshot/Validator/MutationPlan/Mutator boundary | Complete (offline) | Repository tests prove the capability separation. |
| 4 | Non-mutating preflight implementation | Complete (offline) | Fake-adapter evidence proves `runtime_mutated=false` and an empty mutation trace; no live preflight claimed. |
| 5 | Deterministic closure generator and attestation contract | Complete (offline) | Temporary repositories only; no real closure, attestation, or tag exists. |
| 6 | source-candidate review | Pending | No immutable review verdict exists for this candidate. |
| 7 | pull request | Pending | No PR has been opened for this candidate. |
| 8 | exact-SHA CI | Pending | Local verification is not hosted required CI. |
| 9 | Human approval and squash merge | Pending | No approval or merge authority has been exercised. |
| 10 | Post-merge closure generation and publication | Pending | The generator has not run against a canonical merge; no tag or publication exists. |
| 11 | Immutable publication-closure review | Pending | Future closure identities are intentionally unknown. |
| 12 | post-publication preflight | Pending | No cluster was contacted. |
| 13 | convergence | Pending | No convergence authorization or mutation occurred. |
| 14 | 30-minute acceptance | Pending | No acceptance authorization or observation occurred. |

## Current limitations

The reserved tag literal is not an existing release identity. Source merge commit/tree, generated closure commit/tree, tag object, peeled commit, CI run, and both review references are future facts and must not be fabricated in the source candidate. The release-attestation template therefore keeps those fields null until their owning gates complete.
