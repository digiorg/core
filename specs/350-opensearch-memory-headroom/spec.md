# Specification: OpenSearch memory headroom for combined observability load

**Issue:** #350
**Status:** Implementation candidate

## Problem

The retained single-node OpenSearch container was OOM-killed at its `1Gi` cgroup limit while serving Fluentd logs and Jaeger traces and replaying a persisted log-index translog. The effective JVM heap was `512Mi`; measurements showed cgroup exhaustion rather than Java heap pressure.

## Required behavior

1. Local/KinD OpenSearch continues to use a fixed `512Mi` heap (`Xms == Xmx`).
2. Kubernetes reserves `1Gi` memory and limits the container to `2Gi`.
3. CPU request and limit remain `250m` and `1000m`.
4. Exact Helm rendering must fail if the StatefulSet resources or JVM contract drift.
5. Documentation must describe the combined logs-and-traces role and the selected headroom.
6. No chart, image, security, storage, retention, schema, or unrelated platform behavior changes.

## Runtime acceptance

Runtime acceptance is post-merge and separate from this PR. It requires an immutable runtime revision, Argo CD only, preservation of the existing PVC and index identities, completion at yellow-or-better health, readable current log primary, no new OOM/restart during a bounded observation window, and measurable distance from the cgroup limit. Failure preserves the cluster for a human decision.

## Non-goals

- Increasing the JVM heap without evidence of heap pressure.
- Manual StatefulSet patches, restarts, writer quiescence, or index operations.
- Promoting the Issue #348 schema freeze before Issue #350 retained recovery passes.
