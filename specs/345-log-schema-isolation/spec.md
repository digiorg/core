# Specification: Fluentd Log Schema Isolation

- **Issue:** [#345](https://github.com/digiorg/core/issues/345)
- **Status:** Approved for implementation
- **Base:** `88d1fadefab199333d68bdc3b123f0b9d4427d5a`
- **Branch:** `fix/issue-345-log-schema-isolation`
- **Decision date:** 2026-08-30

## Problem

Fluentd parses arbitrary inner application JSON directly into the shared root of every `digiorg-logs-*` document. OpenSearch assigns one dynamic type per root field across all workloads. Current live collisions include:

- `request`: mapped as `text`, emitted by Crossplane as an object;
- `ts`: mapped as `date`, emitted by External Secrets as a float.

OpenSearch rejects those records with HTTP 400. The defect class is unbounded: any workload-owned field can collide with another workload or a platform-owned envelope field.

## Goals

1. Stop cross-workload root mapping collisions without discarding structured application payloads.
2. Keep raw log text and stable Kubernetes identity metadata.
3. Define one governed top-level application field: `level` as a string.
4. Guarantee schema compatibility before Fluentd can emit the new record shape.
5. Converge clean and resumed environments without deleting or reindexing historical logs.
6. Fail closed when an existing mapping is incompatible.

## Non-goals

- Grafana datasource/plugin repair (#303).
- Log Explorer variable or aggregation-field repair (#347).
- etcd metrics repair (#346).
- Application-specific exceptions for `request`, `ts`, or named workloads.
- Historical index deletion, close, rollover, or reindex.
- Production OpenSearch TLS/RBAC enablement.

## Canonical Record Contract

For successfully parsed JSON:

```json
{
  "@timestamp": "...",
  "stream": "stdout",
  "log": "{...raw JSON...}",
  "kubernetes": {
    "namespace_name": "...",
    "pod_name": "...",
    "container_name": "...",
    "host": "...",
    "labels": {},
    "namespace_labels": {}
  },
  "level": "info",
  "structured": {
    "level": "info",
    "request": {},
    "ts": 1725037200.25
  }
}
```

Rules:

- `log` remains unchanged and canonical for display/full-text search.
- Parsed application JSON is stored only under `structured`.
- `structured` is explicitly mapped as `flat_object`.
- No arbitrary parsed key may appear at the document root.
- Only a string-valued `structured.level` is promoted as an indexable root `level`.
- Non-string, null, missing, or plain-text levels produce root `level: null`; OpenSearch does not index that sentinel, so it cannot conflict with the governed string mapping.
- Existing Kubernetes metadata and label sanitization remain unchanged.
- Plain text remains available in `log`; no structured application schema is invented.

## OpenSearch Mapping Contract

The `digiorg-logs-template` must include:

- `structured`: `flat_object`;
- `level`: `text` with `keyword` multi-field;
- existing `@timestamp`, `stream`, `log`, `message`, Kubernetes identity, label, and namespace-label mappings unchanged.

The migration must add the same compatible definitions to every currently open `digiorg-logs-*` index, including hidden indices. Existing documents are preserved. The new `level.keyword` multi-field applies to documents indexed after the mapping update; no claim is made that historical documents are retroactively indexed.

## Ownership and Ordering

The log-index schema is owned by the Fluentd Application because it is a writer precondition.

A Fluentd-base Argo CD `PreSync` Job must:

1. wait boundedly for acceptable OpenSearch cluster health;
2. unconditionally upsert the complete desired index template;
3. enumerate all open `digiorg-logs-*` indices, including hidden indices;
4. process each API-returned name literally without shell pattern expansion and encode it as a single RFC 3986 URL path segment without narrowing the set of valid matching OpenSearch names;
5. add the compatible `structured` and `level` mappings to each index;
6. read back/verify the expected mapping contract;
7. fail on transport, HTTP, mapping, or verification errors.

Only after the hook succeeds may Argo sync the Fluentd ConfigMap and DaemonSet. Cross-Application root waves are documentation/ordering metadata, not the safety boundary.

The previous OpenSearch-owned create-only PostSync template hook is removed. OpenSearch retains ownership of its unrelated ISM retention hook.

## Idempotency

- No index exists: template upsert succeeds; the index loop is empty.
- Stale template exists: it is replaced by the complete desired template.
- Compatible indices exist: additive same-type mapping updates succeed repeatedly.
- A prior run stopped after updating some indices: the next run converges all open indices.
- `structured` or `level` has an incompatible type: the hook fails before writer rollout.

## Security and Operational Constraints

- Use the repository's digest-pinned `curlimages/curl` image.
- Disable ServiceAccount-token automount.
- Run non-root with read-only root filesystem, dropped capabilities, no privilege escalation, and RuntimeDefault seccomp.
- Define CPU/memory requests and limits.
- Bound Job deadline, connection timeout, request timeout, and retry count.
- Use exact in-cluster OpenSearch Service DNS, template name, and `digiorg-logs-*` scope.
- Never log credentials, request payload records, response bodies containing real data, or Secret values.
- Do not add a public listener or firewall rule.

## Acceptance Criteria

- [ ] Crossplane-shaped object `request` remains under `structured` and never appears at root.
- [ ] External-Secrets-shaped float `ts` remains under `structured` and never appears at root.
- [ ] Raw `log` and Kubernetes namespace/pod/container fields survive unchanged.
- [ ] Only string `level` is promoted as an indexable value; all other cases emit unindexed `null`.
- [ ] Template and existing-index mappings define `structured: flat_object`.
- [ ] `level` remains `text` and adds `level.keyword` for newly indexed documents.
- [ ] Template update occurs before existing-index update; both precede writer sync.
- [ ] Clean, stale-template, compatible-resume, partial-retry, and incompatible-mapping paths are tested.
- [ ] Valid matching names with reserved URL characters are percent-encoded and migrated.
- [ ] No delete, close, rollover, reindex, unrestricted index wildcard, or app-specific exception exists.
- [ ] Focused RED is observed before production changes; focused and full GREEN gates pass afterward.
- [ ] Kustomize, YAML, pin, rendered-artifact, security, and whitespace gates pass.
- [ ] Exact-snapshot independent review passes before push.
- [ ] PR remains unmerged for Chris's approval.
- [ ] Post-merge retained-cluster resume validation is performed separately.
- [ ] Authoritative clean-bootstrap validation is performed separately after resume acceptance.

## Rollback

Schema additions remain in place. Do not delete mappings or historical indices. A rollback is a forward corrective commit that preserves `hash_value_field structured` and the schema precondition; it may disable optional `level` promotion if necessary. Reverting to arbitrary root-field merging is prohibited because it reintroduces active data loss.
