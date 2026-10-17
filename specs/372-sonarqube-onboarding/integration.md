# Canonical Catalog integration — issue 372

This is the consumer-pin stage, not a new implementation or runtime approval.
The earlier spec/plan/tasks describe the original local implementation snapshot;
its approval restrictions and unchecked delivery tasks are historical, not a
current delivery receipt. This addendum records the bounded integration scope.

## Inputs and acceptance

- Core base: `ed8c0d408e5c8a1eba219a4c5464ae15eb0b08f1` (merged PR #373).
- Catalog producer: `2867d1b4d47f472c7bb4097d61226e206e32a5a1` (merged PR #30).
- Producer tree: `b253074b1ff97d6875a92113d24253ae72b58631`, equal to the
  independently reviewed producer `884a4860a834d38aaa954cb900a7a8c582baf7a2`.
- Update only the immutable Catalog pin, its directly related tests/comments
  and version/integration documentation. Preserve canonical main and manual sync.
- Prove the old pin fails the focused regression, then test the new pin, the
  complete Core CI-equivalent matrix and actual canonical Catalog contracts.
- Freeze the local candidate for separate exact-snapshot review. Publication is
  authorized only after that review; Chris alone decides merge and rollout.

## Sequence and verification plan

1. Verify fresh main, merged PR identities, reviewed-tree equality and ancestry.
2. RED/GREEN the expected producer pin; retain manual-gate regression coverage.
3. Run Core pin policy, full platform tests, Helm/Kustomize renders and Nushell
   parsing. Run the actual merged Catalog suite/render as the producer contract.
4. Freeze candidate/bundle/diff and bind reproducible logs in the task manifest.
5. Independent review, authorized publication/exact-head CI, then Chris's merge.
6. Separate authorization for prerequisite bootstrap/credentials and deployment;
   verify live policy, Crossplane completion, Portal journey and existing-app
   backfill/retention. Local tests cannot establish those acceptance states.

## Unchanged operational boundaries

All eligible claims may enroll on rollout, including existing and CI-disabled
apps. Do not sync before the account, token/CA Secrets and approved effective
policy are verified. No automatic adoption, project deletion or runtime repair.
Accepted inherited permissions remain the bounded exceptions in the spec.

Prior non-blocking review limitations remain: Job spec changes need a new name;
300-second deadline can interrupt the GET retry budget or an ambiguous POST;
failed Jobs require manual recovery; concurrent backfill is unexercised; a failed
CA save may leave a public-data temporary directory. No new risk acceptance is
implied. The earlier synthetic fixture's operator cleanup is a separate gate.

Rollback is a new reviewed PR restoring the previous Catalog pin
`d531180b322dc0128477ecb9bb0fc9071b41d631`; no SonarQube project/history deletion.
Preserve failed Job and environment evidence. Pin rollback alone does not undo
external enrollment or prove successful runtime rollback.
