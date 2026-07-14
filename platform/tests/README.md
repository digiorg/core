# Platform manifest regression tests

Deterministic tests that parse/render the platform manifests and the pinned
upstream serving config, and assert their **behaviour** (not just the presence
of a string), so subpath/proxy regressions are caught in review rather than in
the cluster.

## Requirements

- Python 3
- PyYAML (`pip install pyyaml`)

No cluster, pytest, or network access required.

## Running

```sh
python3 platform/tests/test_opencost_ui_subpath.py
```

## `test_opencost_ui_subpath.py` — Issue #272 Option B

Guards the subpath-aware OpenCost UI architecture: a **custom UI image** built
from upstream **v1.120.4** with `vite_basename=/opencost`, which serves the SPA,
static assets and the API natively under `/opencost` — replacing the old
root-only image plus its HTML `sub_filter` proxy and unauthenticated
asset-bypass Ingress.

| Test class | Under test | What it proves |
| --- | --- | --- |
| `BuildContractTest` | `opencost/ui-image/build.nu` | is a cross-platform Nushell builder (no bash / Unix-only utilities) that pins upstream ≥ v1.120.4 **and** an immutable 40-hex commit, compiles `vite_basename=/opencost` and `vite_base_api_url=/opencost/model`, keeps local kind and Harbor image coordinates aligned, supports `REGISTRY`/`KIND_CLUSTER`/`LOAD`/`PUSH` env overrides, passes version/commit provenance args, and embeds no credentials. |
| `NginxServingRenderTest` | `ui-image/reference/default.nginx.conf.template` rendered with the `UI_PATH`/`BASE_URL` from `opencost/values.yaml` | via an nginx longest-prefix location matcher: `/opencost/` serves the SPA, deep links fall back to `index.html`, and `/opencost/model/` is the API proxy (API base `= /opencost/model`) — SPA and API never collide. |
| `HelmWiringTest` | `opencost/values.yaml` | selects the custom (non-1.113.0, ≥ v1.120.4) image whose tag matches `build.nu`, uses `IfNotPresent`/`Never`, sets `BASE_URL=/opencost/model` (not the legacy `BASE_URL_OVERRIDE`) and `UI_PATH=/opencost`. |
| `AuthRouteTest` | `opencost/oauth2-proxy.yaml`, `ingress/opencost-portal-ingress.yaml` | the auth proxy forwards straight to `opencost:9090` (no `9091` ui-proxy) with OIDC intact, and the portal Ingress routes `/opencost` through the auth proxy with **no** `rewrite-target`. |
| `NoObsoleteWorkaroundsTest` | `opencost/`, `ingress/` | `ui-proxy.yaml` and `opencost-assets-ingress.yaml` are gone and unreferenced, no Ingress routes `/opencost*` straight to the `opencost` service (no unauthenticated bypass), and no `sub_filter`/`BASE_URL_OVERRIDE` remnants remain. |
| `RouterBasenameDomTest` | `build.nu` `vite_basename` arg + a react-router basename simulation | the DOM-level guard: a root-only (no-basename) router renders an **empty** `#app` at `/opencost/` (the confirmed root cause), while the configured basename matches the index route and deep links — so `#app` is not empty. |

The vendored `ui-image/reference/default.nginx.conf.template` is a byte-faithful
snapshot of the upstream v1.120.4 template (see `ui-image/README.md`), so the
render test exercises the **real** serving config rather than a hand-written
fixture. Refresh it whenever the pinned upstream tag changes.
