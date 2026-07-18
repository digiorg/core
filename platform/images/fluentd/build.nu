#!/usr/bin/env nu

# =============================================================================
# Build the DigiOrg pinned Fluentd image — Issue #275 (Tier 1)
# =============================================================================
# The daemonset ran fluent/fluentd-kubernetes-daemonset on a FLOATING minor tag
# and inherited whatever plugin versions the upstream image shipped. This builds
# a pinned image FROM the digest-pinned upstream base into which EXPLICITLY
# versioned plugin gems (opensearch + kubernetes_metadata_filter) are installed.
# See ./README.md for the gem-version sources and compatibility notes.
#
# This script is the single source of truth for HOW the pinned image is built.
# It is written in Nushell so it runs identically on Linux, macOS and Windows
# with Docker Desktop and kind on PATH — no bash or Unix-only utilities.
# It is intentionally credential-free: authenticate to the registry out-of-band
# (`docker login` / `nu scripts/local-setup.nu`) before running with PUSH set.
#
# Reproducibility / provenance:
#   * UPSTREAM_TAG    — the upstream image tag we track.
#   * UPSTREAM_DIGEST — the immutable digest that tag resolves to. The Dockerfile
#                       FROM pins the digest, and this script verifies the tag
#                       still resolves to it before building, so an upstream
#                       re-tag cannot silently change the base. The version +
#                       base digest are baked into OCI labels.
#
# Usage (identical on Linux, macOS and Windows — no shell-specific env syntax):
#   nu build.nu                             # build + `kind load` into local dev cluster
#   with-env {PUSH: "1"} { nu build.nu }    # also push to $REGISTRY (needs prior docker login)
#   with-env {LOAD: "0"} { nu build.nu }    # build only (no kind load, no push)
# =============================================================================

# --- Pinned upstream base --------------------------------------------------- #
const UPSTREAM_IMAGE = "fluent/fluentd-kubernetes-daemonset"
const UPSTREAM_TAG = "v1.19.2-debian-opensearch-1.0"
const UPSTREAM_DIGEST = "sha256:f7a636ae892cab78b0e63fcb4d89f68a84bd602609a7f38b00fcc5c65f487de2"

# --- Image coordinates ------------------------------------------------------ #
# IMAGE/TAG are the local (kind-loaded) coordinates and MUST match the image
# selected in platform/base/fluentd/daemonset.yaml. REGISTRY and
# REGISTRY_REPOSITORY form the separate Harbor target used only on PUSH.
const IMAGE = "digiorg/fluentd"
const TAG = "v1.19.2-debian-opensearch-1.0"
const REGISTRY_REPOSITORY = "fluentd"

def main [] {
    # --- Behaviour toggles / registry (overridable via environment) --------- #
    let registry = ($env.REGISTRY? | default "digiorg.local/library")
    let kind_cluster = ($env.KIND_CLUSTER? | default "digiorg-core-dev")
    let load = ($env.LOAD? | default "1")
    let push = ($env.PUSH? | default "0")

    print $">> Building ($IMAGE):($TAG)"
    print $"   base   : ($UPSTREAM_IMAGE):($UPSTREAM_TAG)@($UPSTREAM_DIGEST)"
    print "   plugins: fluent-plugin-opensearch + fluent-plugin-kubernetes_metadata_filter (pinned)"

    # Verify the pinned tag still resolves to the pinned digest before building,
    # so provenance is guaranteed even though Docker resolves the base by tag.
    # The repo's resolve_digest.py talks to the registry v2 API without Docker
    # and prints "<registry>/<repo>:<tag>@sha256:<digest>"; compare the digest.
    let repo_root = ($env.FILE_PWD | path join ".." ".." "..")
    let resolver = ($repo_root | path join "scripts" "resolve_digest.py")
    if ($resolver | path exists) {
        let resolved = (python3 $resolver $"($UPSTREAM_IMAGE):($UPSTREAM_TAG)" | str trim)
        let resolved_digest = ($resolved | split row "@" | last | str trim)
        if ($resolved_digest != $UPSTREAM_DIGEST) {
            print -e $"ERROR: ($UPSTREAM_IMAGE):($UPSTREAM_TAG) resolves to ($resolved_digest),"
            print -e $"       expected ($UPSTREAM_DIGEST). Upstream re-tagged the release."
            print -e "       Review the change and update UPSTREAM_DIGEST deliberately."
            exit 1
        }
        print $"   verified: tag resolves to ($UPSTREAM_DIGEST)"
    } else {
        print -e "WARN: scripts/resolve_digest.py not found — skipping tag/digest verification"
    }

    # version/base_digest bake provenance into the image OCI labels. The base is
    # pinned by digest in the Dockerfile's FROM line.
    (
        docker build
            --build-arg $"UPSTREAM_IMAGE=($UPSTREAM_IMAGE)"
            --build-arg $"UPSTREAM_TAG=($UPSTREAM_TAG)"
            --build-arg $"UPSTREAM_DIGEST=($UPSTREAM_DIGEST)"
            --build-arg $"version=($UPSTREAM_TAG)"
            --build-arg $"base_digest=($UPSTREAM_DIGEST)"
            --tag $"($IMAGE):($TAG)"
            $env.FILE_PWD
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
