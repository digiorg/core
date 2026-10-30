# Grafana self-metrics path — Core #378

## Status and provenance

Medium / standard lane; independent full-candidate review required. This is a NEW reconstructed contract, not the unavailable historical specification bytes. Inputs: current [issue #378](https://github.com/digiorg/core/issues/378), retained preparation t_845ac19a and review t_068ffac9, and Chris-approved continuation authorization (core-378-resume/authorization.md). Historical specification commit b2620b2e620c3126352510419d997bdd6d1ae480 / tree 256482c4b5402bf7fa19ed233b65218908f0d160 / parent 16c2c3f3d696569fc9a1b5690f1b021b1477b96f could not be recovered. Its prior verdict does not apply to this candidate. Chris authorized bounded reconstruction and implementation; the existing separate review t_d306dd50 must inspect the complete new spec and implementation.

## Problem and outcome

The historical October 24 observations found the inherited internal HTTP `/metrics` endpoint redirecting to public HTTPS `/grafana/metrics`, where the scraper failed certificate verification. That is not evidence of dashboard or datasource failure. Align the ServiceMonitor with Grafana's configured subpath without changing transport, trust or application ownership.

## Requirements and verification

| ID | Contract | Verification |
| --- | --- | --- |
| R1 | Exact kube-prometheus-stack 87.17.0 render selects `/grafana/metrics`, derived from configured server root URL, over existing `http` / `http-web`. | `GrafanaSelfMetricsRenderTest.test_monitor_matches_server_subpath`; observed baseline RED, focused GREEN and restore-old-path sabotage RED. |
| R2 | Real Grafana 13.1.0 reproduces root-path 301 to the configured HTTPS URL and subpath 200 Prometheus metrics without redirect; rendered selected path must return 200. | Opt-in `GrafanaSelfMetricsComponentTest`, immutable linux/amd64 manifest and config verification, in-process `grafana_build_info` version and revision checks before accepting differential results. |
| R3 | Production change is only `grafana.serviceMonitor.path`; preserve chart/image, public HTTPS/OAuth, plugins, datasources, dashboards, labels/RBAC, retention and other alerts. | Deterministic inherited-vs-candidate full render comparison permitting exactly one monitor path difference; existing regressions; explicit five-path diff review. Do not copy pre-existing insecure settings into tests or introduce new bypasses. |
| R4 | Preserve issue-275 manual Grafana upgrade gate; do not infer reconciliation from a merge. | Application contract assertion; future deployment requires explicit approval. |
| R5 | Runtime recovery is separate and pending. | Separately authorized exact-deployment validation: at least three scrape evaluations over >=5 minutes, 200/no redirect/no trust error, `up=1`, `grafana_build_info`, target-specific TargetDown resolved under effective rule timing, stable Pod UID/restarts/image and unchanged public HTTPS/authenticated dashboard behavior. |

## Scope and non-goals

Only production edit: `platform/base/grafana/values.yaml`. Test: `platform/tests/test_grafana_self_metrics.py`. Documentation: this directory's `spec.md`, `plan.md`, `tasks.md`. No guide, workflow, lock, pin, secret, trust, RBAC, public routing, dashboard/plugin/datasource, retention or unrelated-alert changes. No cluster actions, remote CI, publication, push, PR or merge in this local phase.

## Alternatives, risks and boundaries

Do nothing retains the known scrape defect. Trusted public HTTPS scraping would require a separate approved transport/security design and is not a fallback. An internal path-only change preserves the existing HTTP listener rather than bypassing public TLS. The isolated component proves handler behavior, not Kubernetes discovery, Prometheus scraping, OAuth, dashboards, rollout or alert recovery. Runtime TargetDown timing must be read from deployed rules; local chart rendering is only desired-rule evidence. No residual risk is accepted here.

## Rollout and rollback

After independent exact-candidate review, any publication/CI/merge requires separate authority. Chris retains approval and merge. Preserve the manual gate; obtain a separate runtime assignment/reservation and non-mutating baseline before any promotion. Roll back through a reviewed source revert of the path change, recognizing that this restores the known scrape defect. No restart, sync, trust repair or runtime workaround is part of rollback authorization.

Delivery: local candidate only. Remote CI: pending/not authorized. Deployment and environment validation: pending/not attempted. Before Delivery Done, link a dedicated validation record or structured issue comment explicitly marked Pending with exact source/deployed identities and the R5 scenarios.
