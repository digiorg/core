# DigiOrg pinned Fluentd image

Pinned Fluentd log-forwarder image built for **Issue #275 — Tier 1**. It fixes
the base image by immutable digest and installs **explicitly versioned** plugin
gems, so a clean bootstrap ships the same, reproducible plugin set every time.

## Why this exists

The daemonset previously ran
`fluent/fluentd-kubernetes-daemonset:v1.19-debian-opensearch-1` — a **floating**
minor tag — and inherited whatever plugin versions that image happened to ship.
Two logging-critical plugins are now pinned on top of a digest-pinned base:

| Plugin | Pinned version | Requires | Source |
| --- | --- | --- | --- |
| `fluent-plugin-opensearch` | `1.1.6` | `fluentd >= 0.14.22` | rubygems.org (latest release) |
| `fluent-plugin-kubernetes_metadata_filter` | `3.8.0` | `fluentd >= 0.14.0, < 1.20` | rubygems.org (latest release) |

Both are mutually compatible with the base image's **fluentd 1.19.2** (1.19.2
satisfies `>= 0.14.22` and is `< 1.20`). Versions and their fluentd requirements
were read from the rubygems.org API:

```sh
python3 -c "import urllib.request,json; \
  print(json.load(urllib.request.urlopen('https://rubygems.org/api/v1/gems/fluent-plugin-opensearch.json'))['version'])"
# → 1.1.6
python3 -c "import urllib.request,json; \
  print(json.load(urllib.request.urlopen('https://rubygems.org/api/v1/gems/fluent-plugin-kubernetes_metadata_filter.json'))['version'])"
# → 3.8.0
```

The base image entrypoint, command and bundled config are inherited unchanged —
only the plugin gem set is pinned on top.

## Image coordinates

| | Value |
| --- | --- |
| Local (kind) image | `digiorg/fluentd:v1.19.2-debian-opensearch-1.0` |
| Registry path (Harbor) | `digiorg.local/library/fluentd:v1.19.2-debian-opensearch-1.0` |
| Upstream base | `fluent/fluentd-kubernetes-daemonset:v1.19.2-debian-opensearch-1.0` |
| Upstream base digest (immutable) | `sha256:f7a636ae892cab78b0e63fcb4d89f68a84bd602609a7f38b00fcc5c65f487de2` |
| Pinned plugins | `fluent-plugin-opensearch=1.1.6`, `fluent-plugin-kubernetes_metadata_filter=3.8.0` |

The local image + `IfNotPresent` pull policy are consumed by
`platform/base/fluentd/daemonset.yaml` and asserted by
`platform/tests/test_fluentd_image.py`. The exact ref is allow-listed in
`scripts/pin-policy-allowlist.yaml` (locally built, kind-loaded, never pulled).

The base digest was resolved with
`python3 scripts/resolve_digest.py fluent/fluentd-kubernetes-daemonset:v1.19.2-debian-opensearch-1.0`.

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
`digiorg/fluentd:<tag>` locally and `digiorg.local/library/fluentd:<tag>` in
Harbor.

## Upgrade procedure

1. Pick the new upstream tag; resolve its digest:
   `python3 scripts/resolve_digest.py fluent/fluentd-kubernetes-daemonset:<tag>`
2. Check the newest compatible plugin versions on rubygems.org (the
   `kubernetes_metadata_filter` `< 1.20` fluentd bound in particular).
3. Update `UPSTREAM_TAG` / `UPSTREAM_DIGEST` / `TAG` in `build.nu`, the
   Dockerfile `ARG` defaults + labels, and the plugin version ARGs.
4. Bump the image tag in `daemonset.yaml` and the
   `scripts/pin-policy-allowlist.yaml` entry.
5. Run `python3 platform/tests/test_fluentd_image.py` and
   `python3 scripts/check_pins.py`.
6. `with-env {PUSH: "1"} { nu build.nu }`, scan in Harbor, then roll out.
