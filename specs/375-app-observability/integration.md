# Canonical Catalog integration — issue 375

This addendum supersedes only the earlier spec/plan/tasks' deferred consumer-pin
stage. Their implementation snapshots and approval history remain historical;
this document is not a publication, merge, deployment or runtime receipt.

## Exact inputs and scope

- Core base: `52fb3c2bbe2ab24d17cb0e8d32713db23e4e21b5` (merged PR #376),
  tree `300985ef17907c93435b3fd2ebdd91fd4cc114c8`, identical to reviewed R2
  `d6c9ebd49614aa4907a9c3024089f856b756ba64` and containing that commit.
- Canonical Catalog: `14bed077beb3da068454775869f5e5009b5fc21c` (merged PR #31),
  tree `a88a4d58160284196ef9001e683971607824bcb2`, identical to reviewed producer
  `6e2e7d8f8a3670d0b45c5df4236ef28a0b020a0c` and containing that commit.
- Change only the immutable consumer pin/comment, directly coupled current-main
  expectations/allowed-byte digest, and integration/version documentation.
  Manual synchronization, source path, wave and retry options remain unchanged.
- Preserve the prior SonarQube/Harbor/Gitea/scaffold contracts and main-based
  deployment model. No policy, chart, permissions, template or datasource change.

## Verification and handoff

Observe focused RED against the previous pin, then GREEN against the canonical
producer. Run full locked Python 3.12 suites for both repositories, pinned-tool
Core render/pin/security/component checks, Catalog render, and actual canonical
registration/dashboard compatibility. Preserve no-data, scope, ownership and
import negative tests. Exact candidate identities, commands, tool/dependency
hashes, logs and bundle reconstruction results belong to the external evidence
packet; unchecked historical tasks are not current delivery status.

The current-main Application digest is
`41c8041917db6d9e9c391a50625358c310cefb51504141f7bc360adad33cf6eb`.
Only the Catalog pin and explanatory comments changed. The original Issue #348
source fixture, historical pin/digests, production generator/validators and
negative drift guards remain unchanged. Historical seeding still uses original
fixture bytes, not the current consumer. Keep historical writer commit
`8e6b8908f99ebf76db47c15613eff523644c23f6` in the portable bundle under
`refs/heads/evidence/retained-fluentd-writer`; never publish that evidence-only ref.

Scott freezes this local candidate for independent review t_d6154ff6. Simon
binds the exact candidate and routes the already-authorized publication stage
only after review. Chris retains merge and residual-risk authority. Remote
exact-head CI and deployed source identity remain unverified at local freeze.

## Unchanged limitations and runtime gates

Prior R2 findings remain, not waived: N2 registration/system-namespace denylist;
N5 extra-allowed-username negative coverage gap; N6 actual matcher nameless-object
path untested; N7 adapter namespace-list fixture fidelity. S1/S2/S3, full-resource
Object GET exposure and fail-closed foreign/missing-owner cleanup consequences
remain unchanged. The pinned CLI apply wrapper panic is disclosed; the approved
unchanged-upstream component adapter is test-only, not live CLI acceptance.

Installed webhook generation/global filters/outage behavior, effective RBAC,
legacy live Secret-dashboard inventory, actual Crossplane finalization,
ConfigMap-to-sidecar-to-Grafana import, authenticated datasource/panel checks,
positive log ingestion, visual acceptance, capacity and latency require separate
runtime authorization and evidence. Shared operator queries are not tenant
isolation. No source pin establishes deployment, readiness or risk acceptance.
The prior issue 372 account/Secret/effective-policy prerequisites still apply.

Rollback requires a reviewed PR restoring the previous consumer pin
`2867d1b4d47f472c7bb4097d61226e206e32a5a1` and its coupled expectations/digest.
Do not delete dashboards, telemetry, namespaces or external projects as automatic
rollback. A source revert alone does not prove runtime rollback or undo any
previous external enrollment. Preserve failed-environment evidence.
