# Tasks: OpenSearch memory headroom

## Implementation

- [x] Confirm the latest proven termination was `OOMKilled` / exit `137` at the `1Gi` cgroup limit.
- [x] Confirm no Java heap-pressure signal justified increasing the `512Mi` heap.
- [x] Add and observe a RED source-contract test.
- [x] Set memory request/limit to `1Gi`/`2Gi` while preserving CPU and heap.
- [x] Add a rendered StatefulSet resource/JVM contract gate.
- [x] Add and observe RED then GREEN documentation-parity coverage.
- [x] Run all repository quality gates.
- [ ] Obtain independent exact-snapshot PASS.
- [ ] Open a CI-green PR linked to #350 and stop before merge.

## Post-merge runtime acceptance

- [ ] Verify Chris merged the reviewed PR and resolve the authoritative merge revision.
- [ ] Create and independently review an immutable runtime freeze.
- [ ] Roll out through Argo CD only while preserving the existing PVC and indices.
- [ ] Verify yellow-or-better health, readable current primary, completed recovery, and resumed representative ingestion.
- [ ] Observe no new OOM/restart and explicit cgroup headroom in a bounded window.
- [ ] Resume #348 only after retained recovery passes.
