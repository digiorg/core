# DigiOrg subpath-aware OpenCost UI image

Custom OpenCost UI image that renders natively under
`https://digiorg.local/opencost`. Built for **Issue #272 — Option B**.

## Why this exists

The stock OpenCost UI is compiled to serve at the site root (`/`). Under
DigiOrg's unified NGINX ingress the UI is mounted at `/opencost`, so the
browser's `window.location.pathname` is `/opencost/…`.

- **v1.113.0** used `BrowserRouter` **without a `basename`**. At `/opencost/`
  none of its exact routes (`/`, `/allocation`, `/cloud`, `/external-costs`)
  match, `<Switch>` renders nothing, and `<div id="app">` stays empty — a white
  page with no console error. No HTML `sub_filter` or asset-path rewrite can fix
  this, because the router runs in the browser and cannot be told the prefix was
  stripped upstream.
- **v1.120.4** adds finalized `VITE_BASENAME` support. The **build-time** arg
  `vite_basename` configures all three layers at once:

  | Layer | Effect of `vite_basename=/opencost` |
  | --- | --- |
  | Vite `base` (`vite.config.ts`) | `/opencost/` → assets emit as `/opencost/assets/*` |
  | React Router `basename` (`react-router.config.ts`) | `/opencost` → routes match under the subpath |
  | nginx `UI_PATH` (`Dockerfile` → `ENV UI_PATH=${vite_basename}`) | container serves the SPA at `location /opencost` with an `index.html` SPA fallback |

  Because it is a **build-time** setting, adding `VITE_BASENAME` as a runtime env
  var to a stock image does **not** work — the router and asset base are already
  compiled. The image must be rebuilt, which is what `build.sh` does.

## Image coordinates

| | Value |
| --- | --- |
| Local (kind) image | `digiorg/opencost-ui:v1.120.4-basename-opencost` |
| Registry path (Harbor) | `digiorg.local/library/opencost-ui:v1.120.4-basename-opencost` |
| Upstream source | `github.com/opencost/opencost-ui` |
| Upstream tag | `v1.120.4` |
| Upstream commit (immutable) | `6384abbfd9fa66090573178257b937244a72213b` |
| Build args | `vite_basename=/opencost`, `vite_base_api_url=/opencost/model` |

The tag and env alignment are consumed by `platform/base/opencost/values.yaml`
(`opencost.ui.image` + `extraEnv`) and asserted by
`platform/tests/test_opencost_ui_subpath.py`.

## Build & publish

```sh
# Build + load into the local kind cluster (default)
./build.sh

# Build only
LOAD=0 ./build.sh

# Build + push to Harbor (run `docker login digiorg.local` first — no
# credentials are stored in this repo)
PUSH=1 ./build.sh
```

`build.sh` builds directly from the upstream Dockerfile at the pinned tag and
**verifies** the tag still resolves to `UPSTREAM_COMMIT` before building, so an
upstream re-tag cannot silently change the source. `version`/`commit` build args
bake the provenance into the image footer and OCI labels. Building therefore
requires network access to GitHub. The local kind image and optional Harbor
target are deliberately separate: `digiorg/opencost-ui:<tag>` locally and
`digiorg.local/library/opencost-ui:<tag>` in Harbor.

## Runtime alignment (set in `values.yaml`)

| Setting | Value | Purpose |
| --- | --- | --- |
| `UI_PATH` | `/opencost` | nginx serves the SPA under the subpath (baked from the build arg; set explicitly for clarity) |
| Build arg `vite_base_api_url` | `/opencost/model` | compiles the standard UI JavaScript client to call the authenticated API subpath |
| Runtime `BASE_URL` | `/opencost/model` | renders nginx's matching `location /opencost/model/` model-proxy block |

> The two API settings are intentionally separate but equal: Vite statically
> inlines `VITE_BASE_API_URL` during the image build, while nginx renders its
> proxy location from runtime `BASE_URL`. A runtime variable cannot repair a
> stock image compiled with `/model`. Do **not** use the legacy
> `BASE_URL_OVERRIDE` mechanism for this standard v1.120.4 build.

## `reference/default.nginx.conf.template`

A byte-faithful copy of the upstream v1.120.4 nginx serving template
(`sha256:92428843b58dc262ccce415d8253f785d82ca5a5035421441a95fb3f7fe085ee`).
It is **reference/test data only** — it is not mounted into the image (the image
ships its own copy). The regression tests render it with the `UI_PATH`/`BASE_URL`
taken from `values.yaml` and prove the native subpath serving contract
(SPA at `/opencost/`, deep-link fallback to `index.html`, API at
`/opencost/model/`). Refresh it whenever `UPSTREAM_TAG` changes:

```sh
gh api "repos/opencost/opencost-ui/contents/default.nginx.conf.template?ref=$UPSTREAM_TAG" \
  --jq '.content' | base64 -d > reference/default.nginx.conf.template
```

## Upgrade procedure

1. Pick the new upstream tag; resolve its commit:
   `git ls-remote https://github.com/opencost/opencost-ui.git refs/tags/<tag>^{}`
2. Update `UPSTREAM_TAG` / `UPSTREAM_COMMIT` / `TAG` in `build.sh`.
3. Refresh `reference/default.nginx.conf.template` (command above) — check the
   `UI_PATH`/`BASE_URL` semantics did not change.
4. Bump `opencost.ui.image.tag` in `values.yaml` to the new `TAG`.
5. Run `python3 platform/tests/test_opencost_ui_subpath.py`.
6. `PUSH=1 ./build.sh`, scan in Harbor, then roll out.
