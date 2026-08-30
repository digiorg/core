# Fluentd — Kubernetes Log Collection

Fluentd tails Kubernetes container logs, preserves their raw text and Kubernetes
identity envelope, and writes daily `digiorg-logs-*` indices to OpenSearch.

## Governed record shape

Successful inner-JSON parsing uses Fluentd's `hash_value_field structured`, so
the complete workload-owned payload stays under one `flat_object` field. No
arbitrary application key is merged into the shared document root. The raw
`log`, `stream`, and Kubernetes namespace, pod, container, host, labels, and
namespace labels remain available. Plain text remains only in `log` and does
not invent a structured payload.

The sole governed promotion is `level`: Fluentd copies it to root only when
`structured.level` is a Ruby `String`. Missing, null, numeric, array, and object
values produce root `level: null`, which OpenSearch does not index. The root mapping is `text` with a `keyword`
multi-field, retaining full-text compatibility while enabling exact matching on
newly indexed documents through `level.keyword`.

## Schema prerequisite and migration

`log-schema-job.yaml` is a same-Application Argo CD `PreSync` hook. It is the
safety boundary before the Fluentd ConfigMap and DaemonSet can sync; root
Application waves are only ordering metadata.

Each bounded, idempotent execution:

1. waits for yellow-or-better OpenSearch health;
2. unconditionally upserts the complete `digiorg-logs-template`;
3. verifies the template's `structured` and `level` mappings;
4. enumerates every open `digiorg-logs-*` index, including hidden indices;
5. reads names line by line without shell pattern expansion and percent-encodes
   each API-returned name as one URL path segment;
6. additively applies and verifies those mappings on every enumerated index.

An incompatible existing mapping, failed update, transport/HTTP error, or failed
readback stops the hook and therefore the writer sync. A retry resumes safely;
already-compatible updates are repeated and historical documents are untouched.
The hook never deletes, closes, rolls over, or reindexes an index. OpenSearch's
separate ISM policy continues to own retention.

## Verification

The deterministic contract and strict fake-OpenSearch harness run without a
cluster, network, credentials, or real log data:

```sh
python3 platform/tests/test_fluentd_log_schema.py -v
```

The test file also contains an explicitly gated real-image behavior seam. It
extracts the production filters, supplies synthetic fixture records, and starts
the already-built pinned Fluentd image with `--network none`:

```sh
RUN_FLUENTD_SCHEMA_INTEGRATION=1 \
  python3 platform/tests/test_fluentd_log_schema.py \
  PinnedFluentdImageIntegrationTest -v
```

During Issue #345 design validation on 2026-08-30, the pinned
`digiorg/fluentd:v1.19.2-debian-opensearch-1.0` image was already probed with
synthetic, networkless fixtures. That probe confirmed the isolated shape while
retaining raw log and Kubernetes metadata. The gate remains optional because
standard CI must not depend on a Docker daemon or image availability.

After an authorized deployment, verify the template and current mappings with
read-only OpenSearch calls and inspect only synthetic/new documents. Runtime
validation against the retained cluster and clean bootstrap are separate
post-merge authorization boundaries.

## Forward rollback

Do not delete mappings or historical indices. Roll back with a forward
corrective change that retains `hash_value_field structured` and the PreSync
schema prerequisite. The string-only level promotion may be disabled if needed;
merging arbitrary parsed application keys back into root is not a safe rollback.
