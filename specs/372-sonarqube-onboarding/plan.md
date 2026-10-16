# Plan — issue 372

Bases frozen before implementation:
- Core: 451baf4a18a60858dc82fe0fba038dcaa5c11848
- Catalog: d531180b322dc0128477ecb9bb0fc9071b41d631
- Branch in each isolated clone: feat/372-sonarqube-onboarding
- Main/PR read-only inspection reconfirmed no competing open PRs.

Slices: actual KCL Job gate -> shipped Python ConfigMap runner over HTTPS ->
exact UID-bound adoption -> fail-closed API/default checks -> bounded GET-only
retry/readback -> duplicate-identity inventory and completion readiness ->
dedicated bootstrap account/Secret preparation -> complete local gates.
No XRD spec/status schema change is needed: the native Crossplane Conditions
mechanism supplies SonarQubeEnrollmentReady. Portal is untouched.

A required Application inventory rejects duplicate appName ownership. It is
read-only, cluster-scoped and secret-free; absence or ambiguity blocks enrollment.
Deterministic Kubernetes Object/Job names also collide rather than assign two
independent runners per app. SonarQube's unique project key is the final create
collision boundary. A collision/lost POST response never grants ownership.

Runner image is python:3.12.13-alpine3.23 pinned to the registry index digest
601d3d3797e90e2534782e69c85fafb7971b43f24c7b1b079b7e48dd435e458d,
retrieved using docker pull before use. It needs only Python's stdlib.
Kustomize generates a fixed v1 runner ConfigMap. Future runner revision changes
must version that ConfigMap intentionally, receive review and authorize any Job
replacement. Updating ConfigMap bytes does not rerun a completed Job.

Producer/consumer delivery (not executed here):
1. Independently review both frozen local candidates. Obtain Chris publication
   approval, then authoritative exact-head CI and Chris's producer merge.
2. Verify canonical merged Catalog revision; only then create Core pin change in
   apps/platform/core-catalog.yaml. Never pin this local candidate SHA as main.
3. Review/test the Core integration including the real pin; Chris merges it.
4. Separately approve prerequisite account/token preparation and Catalog rollout
   to ALL eligible claims (including backfill). Manual Argo gate is retained.
5. Verify effective live rights/defaults before enrollment, then actual Portal
   PR/merge -> claim/repo/project journey and backfill inventory. Local fixtures
   do not establish live policy, ingress or controller acceptance.

Rollback: restore the preceding reviewed Catalog pin through a new reviewed PR.
Disable/delete composed runner resources only under the normal declarative
lifecycle; never delete SonarQube projects/history. Account/token revocation,
adoption, drift repair and project teardown remain operator actions requiring
Chris's separate approval. Preserve failed Jobs/logs and inventory before retry.

Acceptance layers: local render/API/security/component tests; independent exact
review; authorized publication/exact-head CI; canonical producer/consumer merge;
separate runtime acceptance. These are not interchangeable.
