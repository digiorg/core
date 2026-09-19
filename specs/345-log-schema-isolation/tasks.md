# Tasks: Issue #345

## Discovery and Decisions

- [x] Reproduce active Fluentd HTTP 400 record loss read-only.
- [x] Prove `request` and `ts` cross-workload type collisions.
- [x] Rule out total-field limit and Fluentd image availability.
- [x] Separate #303, #347, and #346 workstreams.
- [x] Approve `structured: flat_object` schema direction.
- [x] Prove the proposed record shape in the pinned runtime image with synthetic, networkless fixtures.
- [x] Establish branch `fix/issue-345-log-schema-isolation` from `88d1fadefab199333d68bdc3b123f0b9d4427d5a`.

## Specification

- [x] Define record, mapping, ownership, migration, idempotency, security, and rollback contracts.
- [x] Define vertical RED-GREEN-REFACTOR slices.
- [x] Cross-link the repository spec from Issue #345.

## Implementation

- [x] Slice 1 RED: record-shape contract fails on current manifests.
- [x] Slice 1 GREEN: parsed payload isolation and governed level promotion.
- [x] Slice 2 RED: template upsert/schema contract fails.
- [x] Slice 2 GREEN: complete template upsert contract.
- [x] Slice 3 RED: current-index resume migration contract fails.
- [x] Slice 3 GREEN: additive, bounded, fail-closed migration.
- [x] Slice 4 RED: writer lacks same-Application schema prerequisite.
- [x] Slice 4 GREEN: Fluentd-owned PreSync hook and ownership transfer.
- [x] Slice 5 RED: Job security/timeout/resource contract fails.
- [x] Slice 5 GREEN: hardened bounded Job.
- [x] Refactor comments/docs without changing behavior.

## Verification

- [x] Focused test passes.
- [x] Sabotage run proves the regression test fails without the fix.
- [x] Full platform unittest suite passes.
- [x] Pin policy passes.
- [x] Chart-render contract passes.
- [x] Fluentd and OpenSearch Kustomize bases render and parse.
- [x] Every platform base renders.
- [x] Nushell parse gates pass.
- [x] Real pinned Fluentd synthetic behavior harness passes.
- [x] Fake-OpenSearch clean/resume/failure/retry harness passes.
- [x] Secret/transport/destructive-operation scans pass.
- [x] `git diff --check` passes.
- [ ] A durable exact-snapshot independent-review verdict is recorded. PR #349's
  GitHub record has no formal review decision; do not infer review from merge.

## Historical Delivery: PR #349

- [x] Commit issue-scoped capability source at
  `4fa6dbb76f906a3c5727c87adfa10a588221e447`.
- [x] Push `fix/issue-345-log-schema-isolation` and deliver PR #349 against
  `main`: `https://github.com/digiorg/core/pull/349`.
- [x] Read back PR #349 title, base, branch, and exact head SHA.
- [x] Verify both `pin-policy-and-tests` and `render-and-lint` succeeded on the
  exact PR head (workflow runs `33329044341` and `33329042253`).
- [x] PR #349 was merged by a human on 2026-08-30 as merge commit
  `d8f93d64811f54a5f3ddb0a4f193b5bc08d3b894`; its tree equals the reviewed
  capability commit tree `241dd75bf39b12a73444fea3ff7707070e9c155a`.
- [ ] Record a formal immutable review reference if one exists outside the
  GitHub review record. The GitHub API currently reports an empty review decision.

## Runtime Validation: Separate Authorization Boundary

- [x] Verify the historical PR #349 merge revision and capability tree.
- [ ] Review and deliver the separate Issue #348 source candidate.
- [ ] Generate and review the deterministic post-merge runtime closure.
- [ ] Publish the reviewed annotated runtime tag and immutable attestation.
- [ ] Run the separately authorized post-publication read-only preflight.
- [ ] Obtain separate retained-convergence authorization.
- [ ] Perform retained-cluster resume acceptance.
- [ ] Preserve failed environment and return decisions if acceptance fails.
- [ ] Perform clean-cluster acceptance only after resume passes.
- [ ] Unblock #303 and #347 only after #345 runtime acceptance.
