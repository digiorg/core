# DigiOrg production-optimized Keycloak image

Pinned, multi-stage Keycloak image built for **Issue #275 — Tier 1**. It runs
`kc.sh build` once at image-build time so the deployment can start with
`start --optimized` (a production start) instead of the unsupported `start-dev`.

## Why this exists

The platform previously ran the stock `quay.io/keycloak/keycloak` image with
`start-dev`. Dev mode:

- re-runs the augmentation/build step on **every** boot (slow, non-reproducible);
- relaxes production safeguards (hostname/HTTP/TLS strictness).

Keycloak's production model is to **fix the build-time options once** with
`kc.sh build`, then boot with `start --optimized` — the server skips
re-augmentation because the optimized distribution is already baked in. This
image does exactly that:

| Stage | Base | Action |
| --- | --- | --- |
| `builder` | `quay.io/keycloak/keycloak:26.7.0` (digest-pinned) | `ENV KC_DB=postgres / KC_HEALTH_ENABLED=true / KC_METRICS_ENABLED=true` then `kc.sh build` |
| final | the **same** digest-pinned base | `COPY --from=builder /opt/keycloak/` |

Because the build-time options are fixed in the image, the deployment keeps
`KC_DB=postgres` (and friends) at runtime and boots with `start --optimized`.
See the Keycloak 26.7 "Configuring Keycloak for production" / "Optimize the
Keycloak startup" docs for the `kc.sh build` + `start --optimized` contract.

### Realm data stays out of the image

Realm/user JSON is **not** baked in. The deployment continues to mount it from
the `keycloak-realm-import` configMap at `/opt/keycloak/data/import` and imports
it at runtime (`--import-realm`). This keeps the image environment-agnostic and
avoids shipping credentials in a layer.

## Image coordinates

| | Value |
| --- | --- |
| Local (kind) image | `digiorg/keycloak:26.7.0-optimized` |
| Registry path (Harbor) | `digiorg.local/library/keycloak:26.7.0-optimized` |
| Upstream base | `quay.io/keycloak/keycloak:26.7.0` |
| Upstream base digest (immutable) | `sha256:2eb3cd316835c990e69e26ade292ffa78f6fb0db7d5fc6377463c162e1979ac0` |
| Build-time options | `KC_DB=postgres`, `KC_HEALTH_ENABLED=true`, `KC_METRICS_ENABLED=true` |

The local image + `IfNotPresent` pull policy are consumed by
`platform/base/keycloak/keycloak-deployment.yaml` and asserted by
`platform/tests/test_keycloak_image.py`. The exact ref is allow-listed in
`scripts/pin-policy-allowlist.yaml` (locally built, kind-loaded, never pulled).

The base digest was resolved with
`python3 scripts/resolve_digest.py quay.io/keycloak/keycloak:26.7.0`.

## Build & publish

`build.nu` is a cross-platform [Nushell](https://www.nushell.sh) script, so the
same commands work on Linux, macOS and Windows (with Docker Desktop and kind on
`PATH`). Run them from a Nushell prompt:

```nu
# Build + load into the local kind cluster (default)
nu build.nu

# Build only
with-env {LOAD: "0"} { nu build.nu }

# Build + push to Harbor (run `docker login digiorg.local` first — no
# credentials are stored in this repo)
with-env {PUSH: "1"} { nu build.nu }
```

Before building, `build.nu` calls `scripts/resolve_digest.py` and **verifies**
the pinned tag still resolves to `UPSTREAM_DIGEST`, so an upstream re-tag cannot
silently change the base. `version`/`base_digest` build args bake the provenance
into OCI labels. The local kind image and optional Harbor target are separate:
`digiorg/keycloak:<tag>` locally and `digiorg.local/library/keycloak:<tag>` in
Harbor.

## Upgrade procedure

1. Pick the new upstream tag; resolve its digest:
   `python3 scripts/resolve_digest.py quay.io/keycloak/keycloak:<tag>`
2. Update `UPSTREAM_TAG` / `UPSTREAM_DIGEST` / `TAG` in `build.nu` **and** the
   Dockerfile `ARG` defaults + `org.opencontainers.image.base.name` label.
3. Bump the image tag in `keycloak-deployment.yaml` and the
   `scripts/pin-policy-allowlist.yaml` entry.
4. Run `python3 platform/tests/test_keycloak_image.py` and
   `python3 scripts/check_pins.py`.
5. `with-env {PUSH: "1"} { nu build.nu }`, scan in Harbor, then roll out.
