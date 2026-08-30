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
- [ ] Exact-snapshot independent review passes.

## Delivery

- [ ] Commit only issue-scoped files.
- [ ] Push branch and verify remote SHA.
- [ ] Create PR linking `Closes #345`.
- [ ] Read back PR title/body/files/head/base.
- [ ] Verify CI head SHA and all required checks.
- [ ] Stop before merge.

## Post-Merge, Separate Authorization Boundary

- [ ] Chris merges the reviewed PR.
- [ ] Verify authoritative merge revision.
- [ ] Perform retained-cluster resume acceptance.
- [ ] Preserve failed environment and return decisions if acceptance fails.
- [ ] Perform clean-cluster acceptance only after resume passes.
- [ ] Unblock #303 and #347 only after #345 runtime acceptance.
