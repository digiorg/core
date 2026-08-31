# Plan: OpenSearch memory headroom

1. Reproduce the source-contract failure with a focused test against the current `512Mi` request / `1Gi` limit.
2. Keep the measured `512Mi` JVM heap and change only the Kubernetes memory request/limit to `1Gi`/`2Gi`.
3. Extend the exact-chart renderer with a semantic StatefulSet gate for resources and `OPENSEARCH_JAVA_OPTS`.
4. Align the OpenSearch README with the combined Fluentd-log and Jaeger-trace workload.
5. Run focused tests, exact Helm rendering, full platform tests, pin policy, Kustomize/Nushell, secret/scope scans, and diff checks.
6. Obtain independent exact-snapshot review before publishing a PR.
7. Stop for Chris's review and merge decision. Do not mutate the retained cluster from this branch.
8. After merge only, create an immutable runtime freeze and perform one controlled Argo rollout with the existing PVC.
