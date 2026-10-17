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

## Historical Issue #348 test-contract separation

Chris approved a bounded test-only migration after the new pin exposed an
inherited coupling: the historical closure harness required every current
Application to be byte-identical to its September source, including Catalog
`d531180b322dc0128477ecb9bb0fc9071b41d631`.

- The retained `release-closure-source.json`, its original commit/tree/digests,
  live parser fixtures, runtime descriptors/tags, production transition/closure
  scripts and strict historical `_validate_checkout_source` remain unchanged.
- The current-main test contract permits exactly the reviewed Catalog
  Application bytes (pin plus related comment), SHA-256
  `36acfe6923aa04bfda7918afecda034df8e432de470397699e6c8c21e618ee03`.
  Every other retained Application remains byte-exact. This is not an arbitrary
  new-pin allowlist and does not accept generated runtime closure bytes as main.
- Historical generator tests accept strictly validated historical source/closure
  or current-main input, but seed Applications only from the original historical
  fixture. Current Catalog bytes never silently replace historical evidence.
- Source-graph tests assert both the current canonical consumer and the separate
  original historical Catalog pin. Negative tests reject old/mutable/wrong pins,
  comment, gate, path, sync-option/automation and unrelated Application drift;
  the historical validator and production generator still reject the new pin.
- Old-base controls, focused RED/GREEN, negative/sabotage checks and the full
  exact-candidate local matrix are retained in the task evidence packet.

This separation is not a new Issue #348 runtime candidate, closure generation
approval, live compatibility proof, or operational completion. A future runtime
transition still requires its own source compatibility, exact review, immutable
closure, authorization and live acceptance gates. No historical fixture cleanup,
production release-model change, test skip, or relaxed closure guard is included.

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
