#!/usr/bin/env nu

# =============================================================================
# Build the DigiOrg subpath-aware OpenCost UI image  (Issue #272 — Option B)
# =============================================================================
# The stock OpenCost UI is compiled to serve at "/". Under DigiOrg's unified
# ingress it lives at https://digiorg.local/opencost, where a root-based
# react-router matches nothing and renders an empty <div id="app"> (a white
# page). Upstream v1.120.4 fixes this properly: the build-time arguments
# `vite_basename` and `vite_base_api_url` compile the router/asset base and the
# browser API base respectively; the Dockerfile also derives nginx UI_PATH from
# `vite_basename`. See ./README.md for the full rationale.
#
# This script is the single source of truth for HOW the pinned image is built.
# It is written in Nushell so it runs identically on Linux, macOS and Windows
# with Docker Desktop, Git and kind on PATH — no bash or Unix-only utilities.
# It is intentionally credential-free: authenticate to the registry out-of-band
# (`docker login` / `nu scripts/local-setup.nu`) before running with PUSH set.
#
# Reproducibility / provenance:
#   * UPSTREAM_TAG    — the upstream release we track.
#   * UPSTREAM_COMMIT — the immutable commit that tag resolves to. The build
#                       context is fetched by tag and then verified against this
#                       commit, so a re-tag upstream cannot silently change the
#                       source. Both are baked into the image (version/commit
#                       build args -> footer + OCI labels).
#
# Usage (identical on Linux, macOS and Windows — no shell-specific env syntax;
# `with-env` sets the toggle for the spawned build in any Nushell):
#   nu build.nu                             # build + `kind load` into local dev cluster
#   with-env {PUSH: "1"} { nu build.nu }    # also push to $REGISTRY (needs prior docker login)
#   with-env {LOAD: "0"} { nu build.nu }    # build only (no kind load, no push)
# =============================================================================

# --- Pinned upstream source ------------------------------------------------- #
const UPSTREAM_REPO = "https://github.com/opencost/opencost-ui.git"
const UPSTREAM_TAG = "v1.120.4"
const UPSTREAM_COMMIT = "6384abbfd9fa66090573178257b937244a72213b"

# --- Image coordinates ------------------------------------------------------ #
# IMAGE/TAG are the local (kind-loaded) coordinates and MUST match the image
# selected in platform/base/opencost/values.yaml. REGISTRY and
# REGISTRY_REPOSITORY form the separate Harbor target used only on PUSH.
const IMAGE = "digiorg/opencost-ui"
const TAG = "v1.120.4-basename-opencost"
const REGISTRY_REPOSITORY = "opencost-ui"

def main [] {
    # --- Behaviour toggles / registry (overridable via environment) --------- #
    let registry = ($env.REGISTRY? | default "digiorg.local/library")
    let kind_cluster = ($env.KIND_CLUSTER? | default "digiorg-core-dev")
    let load = ($env.LOAD? | default "1")
    let push = ($env.PUSH? | default "0")

    # The subpath the DigiOrg platform mounts OpenCost under. `vite_basename`
    # must start with a slash and NOT end with one (upstream convention: it
    # becomes the router basename and the nginx UI_PATH verbatim, while Vite
    # appends the slash). `vite_base_api_url` is also build-time: the standard
    # v1.120.4 JavaScript client reads VITE_BASE_API_URL during compilation and
    # cannot be corrected by a runtime-only BASE_URL. Both are literals so the
    # build contract is testable.

    print $">> Building ($IMAGE):($TAG)"
    print $"   source : ($UPSTREAM_REPO)#($UPSTREAM_TAG) \(($UPSTREAM_COMMIT)\)"
    print "   basename: /opencost"

    # Verify the pinned tag still resolves to the pinned commit before building,
    # so provenance is guaranteed even though Docker fetches the context by tag.
    # `git ls-remote` prints "<sha>\t<ref>" lines; parse the first column with
    # Nushell builtins (no awk/grep) so this works on Windows too.
    if (which git | is-not-empty) {
        let resolved = (
            git ls-remote $UPSTREAM_REPO $"refs/tags/($UPSTREAM_TAG)^{}"
            | lines
            | where {|line| ($line | str trim) != "" }
            | each {|line| $line | split row -r '\s+' | first }
        )
        let sha = ($resolved | get -o 0 | default "")
        if ($sha != "" and $sha != $UPSTREAM_COMMIT) {
            print -e $"ERROR: ($UPSTREAM_TAG) resolves to ($sha), expected ($UPSTREAM_COMMIT)"
            print -e "       Upstream re-tagged the release. Review the change and update"
            print -e "       UPSTREAM_COMMIT deliberately before building."
            exit 1
        }
    }

    # Build from the upstream Dockerfile at the pinned tag. `vite_basename`
    # drives the Vite asset base, react-router basename and nginx UI_PATH. The
    # separate `vite_base_api_url` compiles browser API calls to the
    # authenticated /opencost/model route. version/commit bake provenance in.
    (
        docker build
            --build-arg vite_basename=/opencost
            --build-arg vite_base_api_url=/opencost/model
            --build-arg $"version=($UPSTREAM_TAG)"
            --build-arg $"commit=($UPSTREAM_COMMIT)"
            --tag $"($IMAGE):($TAG)"
            $"($UPSTREAM_REPO)#($UPSTREAM_TAG)"
    )

    print $">> Built ($IMAGE):($TAG)"

    if $load == "1" {
        print $">> Loading ($IMAGE):($TAG) into kind cluster '($kind_cluster)'"
        kind load docker-image $"($IMAGE):($TAG)" --name $kind_cluster
    }

    if $push == "1" {
        print $">> Pushing to ($registry)/($REGISTRY_REPOSITORY):($TAG)"
        docker tag $"($IMAGE):($TAG)" $"($registry)/($REGISTRY_REPOSITORY):($TAG)"
        docker push $"($registry)/($REGISTRY_REPOSITORY):($TAG)"
    }

    print ">> Done."
}
