# Core #378 implementation and validation plan

## Baseline, scope and recovery

Base: Core `16c2c3f3d696569fc9a1b5690f1b021b1477b96f`; isolated `fix/378-grafana-self-metrics`. Current upstream main must equal this baseline. Historical spec bytes/commit are unavailable; reconstruct only the approved contract in `spec.md`, and review all new bytes through t_d306dd50. No old exact-snapshot verdict is inherited.

Allowed diff: the Grafana values path, one regression file, and this directory's three Markdown files. No pipeline or guide edits. Existing insecure OAuth development settings are out of scope and must not be copied into the local fixture or treated as accepted risk.

## Vertical slice: R1–R4

1. Add exact-package Helm regression. Resolve chart 87.17.0 from the Application; verify package SHA256 `e9d625daece8804bfa82959296e59cf146756d9aa638f207ebe20e01d6d75514` before rendering. Render as `prometheus` in `monitoring` with current values. A fixed synthetic adminPassword is only a render determinism input, not an actual account credential.
2. Observe baseline `/metrics` mismatch against server-derived `/grafana/metrics`. Inspect generated ServiceMonitor and preserve HTTP scheme and named port.
3. Opt in to the real component via `GRAFANA_SELF_METRICS_COMPONENT=1`. Verify manifest raw bytes digest `6ea068891652aa6a65ca9065c26b89de939653803c836426970305c11fd00534`, config digest `e76fd1761e3cc1dd6071a53484b72762f8b358bb1ecd89c9e21d57090956998e`, linux/amd64, local diff_ids, rendered 13.1.0 tag, and process build_info version 13.1.0/revision `b309c9bb3b81a748c3a75289236a27309ed2566a`. Mismatch fails closed before accepting response evidence.
4. Run only the cached digest image (`--pull=never`) in a uniquely named, task-labelled nonprivileged container, all capabilities dropped, no mounts, no host networking, loopback-only random port, bounded memory/CPU. Fixture reproduces only rendered server root URL/subpath; it omits OAuth/datasources/dashboard sidecars and disables background plugin installation/analytics. Therefore it proves handler compatibility, not public authentication or complete deployment behavior. Confirm root 301 and exact HTTPS Location without following; subpath 200/text metrics/build_info; rendered selected path must independently succeed. Remove only the container created by this test in `finally`.
5. Change only `grafana.serviceMonitor.path` to `/grafana/metrics`; run focused GREEN. Compare complete inherited-vs-candidate deterministic rendered resources, normalizing only that single path; compare all other documents exactly. Assert manual upgrade gate unchanged.
6. Restore old path, run render AND real component sabotage RED, restore candidate and rerun GREEN. Preserve logs, including harness failures and their fixes rather than presenting them as product RED.

## Full local matrix and freeze

Use task-local exact tools/checksum-verified downloads from the immutable baseline workflow; hash-locked Python 3.12 dependencies; task-only Go build/module caches and TMPDIR. Verify Go modules, run `go test -mod=readonly -p 4 -v ./...` and build the repository component with `-mod=readonly -p 4`. Expose real `KYVERNO_COMPONENT` and `ARGOCD_V345_BINARY` to the test subprocesses.

On the frozen candidate run pin policy; complete `unittest discover`; every Helm source/image validation; all base Kustomize builds; all relevant Nushell IDE checks including diagnostic/error JSON detection; exact-base diff hygiene; focused component tests; bounded added-line security scan and five-path inventory. Do not substitute global tool versions or relax failures. Full local gate results are recorded in the durable evidence packet bound to exact Git commit/tree/parent, not retroactively attributed to the historical spec. Remote Actions have not run and are prohibited in this task.

Inspect the effective rendered TargetDown rule from the pinned chart, including `for` and presence/absence of `keep_firing_for`. This is desired-state evidence only; read the deployed effective rule separately before timing future alert recovery. Do not claim default timings establish runtime resolution.

Freeze full diff, reviewed source Git bundle/archive, command argv/results, tool/chart/image identities, reconstruction provenance and SHA256 manifest/archive under the durable task evidence root. Simon binds the existing full-candidate independent review card; no same-card review or reassignment.

## Future R5 runtime validation (not authorized here)

After externally approved publication, exact-head CI, Chris approval/merge and manual-gate reconciliation decision: reserve the shared environment, capture non-mutating baseline and deployed source/image identities, record stable Pod UID/restarts, then observe >=3 successful scrapes spanning >=5 minutes with no redirect/trust error, `up=1`, build_info and target-specific alert recovery under effective rule timing. Check public trusted HTTPS/OAuth/authenticated dashboards separately without TLS bypass. Publish a dedicated validation record before Delivery Done with pending status where unproven. Stop/preserve evidence on failure; no automatic sync/restart/repair. Rollback is a reviewed source revert and restores the old known scrape defect.
