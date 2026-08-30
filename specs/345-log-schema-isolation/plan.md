# Implementation Plan: Issue #345

## Delivery Boundary

Implement one producer-side PR in `digiorg/core`. Do not change Grafana or etcd resources and do not mutate the retained cluster before merge.

## Vertical TDD Slices

### Slice 1 — Record-shape isolation

**RED**

Add `platform/tests/test_fluentd_log_schema.py` contracts that fail because the production parser lacks `hash_value_field structured`, arbitrary parsed keys can merge into root, and no governed level promotion exists.

Include synthetic Crossplane, External Secrets, invalid/non-string level, and plain-text fixtures. Add an optional real-image harness that extracts/uses the production filter fragment and captures records before OpenSearch. The pinned runtime-image proof must remain networkless and fixture-only.

**GREEN**

- add `hash_value_field structured`;
- promote only string-valued `structured.level`;
- preserve raw `log`, Kubernetes metadata, and label sanitization.

**REFACTOR**

Keep Ruby expressions narrow and comments explicit. Do not add per-application branches.

### Slice 2 — Schema template contract

**RED**

Assert that the desired template lacks `structured: flat_object`, that `level.keyword` is absent, and that create-if-absent behavior cannot update a stale template.

**GREEN**

Define the complete template payload with `structured` and compatible `level` mappings. Make template reconciliation unconditional and bounded.

### Slice 3 — Existing-index resume migration

**RED**

Execute the actual rendered hook script against a strict fake `curl` and assert current production behavior fails to:

- enumerate existing open log indices;
- add mappings to each index;
- order template update before index updates;
- stop on an incompatible mapping/update failure;
- converge after a partial run.

**GREEN**

Implement additive mapping updates and exact readback checks for every open `digiorg-logs-*` index, including hidden indices.
Process API-returned names line by line without shell pattern expansion, encode each as one URL path segment, and do not impose an application-side subset of valid OpenSearch index names.

### Slice 4 — Argo atomicity and ownership

**RED**

Assert the schema hook is currently OpenSearch-owned PostSync and therefore cannot gate the separate Fluentd writer Application.

**GREEN**

- move/replace the hook under `platform/base/fluentd/`;
- mark it `PreSync` with retry-safe lifecycle;
- register it in the Fluentd Kustomization;
- remove it from the OpenSearch Kustomization;
- preserve the OpenSearch ISM hook;
- document that same-Application PreSync is the schema-before-writer boundary.

### Slice 5 — Security and operational bounds

**RED**

Assert missing Job deadline, curl bounds, non-root/read-only/drop-capability controls, token automount disablement, and resource bounds.

**GREEN**

Add the required controls without adding credentials or external listeners.

## Documentation

- Add a Fluentd README describing record shape, migration, verification, and forward rollback.
- Update the OpenSearch README to point log-schema ownership to Fluentd while retaining ISM ownership.
- Clarify Application comments; do not claim sync waves prove workload readiness.

## Verification Matrix

Focused:

```bash
python3 platform/tests/test_fluentd_log_schema.py -v
```

Repository:

```bash
python3 -m unittest discover -s platform/tests -p 'test_*.py' -v
python3 scripts/check_pins.py
python3 scripts/render_platform_charts.py
```

Render and syntax:

```bash
kustomize build platform/base/fluentd
kustomize build platform/base/opensearch
# plus the repository's every-base Kustomize loop
# plus Nushell parser checks from platform-validation.yml
git diff --check
```

Behavioral:

- real pinned Fluentd image, synthetic fixtures, `--network none`;
- strict fake-OpenSearch hook execution for clean/resume/failure/retry paths;
- optional disposable OpenSearch integration only if it does not touch the retained cluster.

Security:

- parse rendered Job security context/resources/deadlines;
- scan changed/rendered artifacts for credentials and unsafe transport expansion;
- verify only expected Service DNS and narrow log-index/template endpoints;
- prove no delete, close, rollover, or reindex call.

## Review and Delivery

1. Create a local exact-snapshot review commit after all gates pass.
2. Record full base and head SHA and verify a clean worktree.
3. Obtain an independent read-only review of that exact two-dot diff.
4. Fix blockers, rerun affected/full gates, and obtain a fresh review if the tree changes.
5. Push only after review; create a ready-to-review PR with RED/GREEN evidence and explicit unexecuted runtime gates.
6. Verify remote head SHA and all CI checks.
7. Stop before merge.

## Post-Merge Runtime Gates

1. Retained-cluster resume: observe PreSync migration, verify template/current mappings, verify new record shape and zero representative HTTP 400 mapping rejections in a bounded window.
2. Preserve the retained environment if validation fails; no automatic repair.
3. After resume acceptance, schedule an authoritative clean-cluster run from an immutable merged revision.
4. Keep #303 and #347 blocked until #345 runtime acceptance passes.
