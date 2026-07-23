#!/usr/bin/env nu

# =============================================================================
# Build the DigiOrg production-optimized Keycloak image — Issue #275 (Tier 1)
# =============================================================================
# The platform ran the stock image with `start-dev`. Dev mode re-augments the
# server on every boot and disables production safeguards, so it is unsupported
# for real use. This builds a pinned, multi-stage image that runs `kc.sh build`
# with the platform's build-time options (db=postgres, health, metrics) against
# a digest-pinned upstream base; the deployment then runs `start --optimized`.
# Realm/user data is NOT baked in — it stays in the mounted realm-import
# configMap. See ./README.md for the full rationale.
#
# This script is the single source of truth for HOW the pinned image is built.
# It is written in Nushell so it runs identically on Linux, macOS and Windows
# with Docker Desktop and kind on PATH — no bash or Unix-only utilities.
# It is intentionally credential-free: authenticate to the registry out-of-band
# (`docker login` / `nu scripts/local-setup.nu`) before running with PUSH set.
#
# Reproducibility / provenance:
#   * UPSTREAM_TAG    — the upstream release we track.
#   * UPSTREAM_DIGEST — the immutable digest that tag resolves to. Both stages of
#                       the Dockerfile pin the digest, and this script verifies
#                       the tag still resolves to it before building, so an
#                       upstream re-tag cannot silently change the base. The
#                       version + base digest are baked into OCI labels.
#
# Usage (identical on Linux, macOS and Windows — no shell-specific env syntax):
#   nu build.nu                             # build + `kind load` into local dev cluster
#   with-env {PUSH: "1"} { nu build.nu }    # also push to $REGISTRY (needs prior docker login)
#   with-env {LOAD: "0"} { nu build.nu }    # build only (no kind load, no push)
# =============================================================================

# --- Pinned upstream base --------------------------------------------------- #
const UPSTREAM_IMAGE = "quay.io/keycloak/keycloak"
const UPSTREAM_TAG = "26.7.0"
const UPSTREAM_DIGEST = "sha256:0f198be292568439d700cdbfb893e69a6009bb43a94a06a945b1d3d506c76b13"

# --- Image coordinates ------------------------------------------------------ #
# IMAGE/TAG are the local (kind-loaded) coordinates and MUST match the image
# selected in platform/base/keycloak/keycloak-deployment.yaml. REGISTRY and
# REGISTRY_REPOSITORY form the separate Harbor target used only on PUSH.
const IMAGE = "digiorg/keycloak"
const TAG = "26.7.0-optimized"
const REGISTRY_REPOSITORY = "keycloak"

def validate_persisted_result [result: record] {
    if $result.exit_code != 0 {
        error make {msg: $"persisted Keycloak configuration check exited with code ($result.exit_code)"}
    }

    let persisted_config = (
        $result.stdout
        | lines
        | each {|line| $line | str replace -ar '\s+' ' ' | str trim }
    )
    let expected = [
        { name: "db", value: "postgres" }
        { name: "health-enabled", value: "true" }
        { name: "metrics-enabled", value: "true" }
        { name: "http-relative-path", value: "/keycloak" }
        { name: "http-management-relative-path", value: "/" }
    ]
    for option in $expected {
        let persisted_line = ($"kc.($option.name) = ($option.value) " + "(Persisted)")
        if not ($persisted_config | any {|line| $line == $persisted_line }) {
            error make {msg: $"built image does not persist kc.($option.name)=($option.value)"}
        }
    }
}

def validate_persisted_config [image: string] {
    let result = (
        ^docker run
            --rm
            --entrypoint /opt/keycloak/bin/kc.sh
            $image
            show-config
        | complete
    )
    validate_persisted_result $result
}

def main [] {
    # --- Behaviour toggles / registry (overridable via environment) --------- #
    let registry = ($env.REGISTRY? | default "digiorg.local/library")
    let kind_cluster = ($env.KIND_CLUSTER? | default "digiorg-core-dev")
    let load = ($env.LOAD? | default "1")
    let push = ($env.PUSH? | default "0")

    print $">> Building ($IMAGE):($TAG)"
    print $"   base   : ($UPSTREAM_IMAGE):($UPSTREAM_TAG)@($UPSTREAM_DIGEST)"
    print "   mode   : start --optimized (db=postgres, health, metrics baked at build)"

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
    # pinned by digest in the Dockerfile's FROM lines (both stages).
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
    validate_persisted_config $"($IMAGE):($TAG)"
    print "   verified: optimized Keycloak configuration is persisted"

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
