#!/usr/bin/env bash
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
# It is intentionally credential-free: authenticate to the registry out-of-band
# (`docker login` / `nu scripts/local-setup.nu`) before running with PUSH=1.
#
# Reproducibility / provenance:
#   * UPSTREAM_TAG    — the upstream release we track.
#   * UPSTREAM_COMMIT — the immutable commit that tag resolves to. The build
#                       context is fetched by tag and then verified against this
#                       commit, so a re-tag upstream cannot silently change the
#                       source. Both are baked into the image (version/commit
#                       build args -> footer + OCI labels).
#
# Usage:
#   ./build.sh              # build + `kind load` into the local dev cluster
#   PUSH=1 ./build.sh       # also push to $REGISTRY (requires prior docker login)
#   LOAD=0 ./build.sh       # build only (no kind load, no push)
# =============================================================================
set -euo pipefail

# --- Pinned upstream source ------------------------------------------------- #
UPSTREAM_REPO="https://github.com/opencost/opencost-ui.git"
UPSTREAM_TAG="v1.120.4"
UPSTREAM_COMMIT="6384abbfd9fa66090573178257b937244a72213b"

# --- Image coordinates ------------------------------------------------------ #
# IMAGE/TAG are the local (kind-loaded) coordinates and MUST match the image
# selected in platform/base/opencost/values.yaml. REGISTRY and
# REGISTRY_REPOSITORY form the separate Harbor target used only on PUSH.
IMAGE="digiorg/opencost-ui"
TAG="v1.120.4-basename-opencost"
REGISTRY="${REGISTRY:-digiorg.local/library}"
REGISTRY_REPOSITORY="opencost-ui"

# --- Behaviour toggles ------------------------------------------------------ #
KIND_CLUSTER="${KIND_CLUSTER:-digiorg-core-dev}"
LOAD="${LOAD:-1}"
PUSH="${PUSH:-0}"

# The subpath the DigiOrg platform mounts OpenCost under. `vite_basename` must
# start with a slash and NOT end with one (upstream convention: it becomes the
# router basename and the nginx UI_PATH verbatim, while Vite appends the slash).
# `vite_base_api_url` is also build-time: the standard v1.120.4 JavaScript client
# reads VITE_BASE_API_URL during compilation and cannot be corrected by a
# runtime-only BASE_URL. Both are literals so the build contract is testable.

echo ">> Building ${IMAGE}:${TAG}"
echo "   source : ${UPSTREAM_REPO}#${UPSTREAM_TAG} (${UPSTREAM_COMMIT})"
echo "   basename: /opencost"

# Verify the pinned tag still resolves to the pinned commit before building, so
# provenance is guaranteed even though Docker fetches the build context by tag.
if command -v git >/dev/null 2>&1; then
  resolved="$(git ls-remote "${UPSTREAM_REPO}" "refs/tags/${UPSTREAM_TAG}^{}" | awk '{print $1}')"
  if [ -n "${resolved}" ] && [ "${resolved}" != "${UPSTREAM_COMMIT}" ]; then
    echo "ERROR: ${UPSTREAM_TAG} resolves to ${resolved}, expected ${UPSTREAM_COMMIT}" >&2
    echo "       Upstream re-tagged the release. Review the change and update" >&2
    echo "       UPSTREAM_COMMIT deliberately before building." >&2
    exit 1
  fi
fi

# Build from the upstream Dockerfile at the pinned tag. `vite_basename` drives
# the Vite asset base, react-router basename and nginx UI_PATH. The separate
# `vite_base_api_url` compiles browser API calls to the authenticated
# /opencost/model route. version/commit bake provenance into the image.
docker build \
  --build-arg vite_basename=/opencost \
  --build-arg vite_base_api_url=/opencost/model \
  --build-arg version="${UPSTREAM_TAG}" \
  --build-arg commit="${UPSTREAM_COMMIT}" \
  --tag "${IMAGE}:${TAG}" \
  "${UPSTREAM_REPO}#${UPSTREAM_TAG}"

echo ">> Built ${IMAGE}:${TAG}"

if [ "${LOAD}" = "1" ]; then
  echo ">> Loading ${IMAGE}:${TAG} into kind cluster '${KIND_CLUSTER}'"
  kind load docker-image "${IMAGE}:${TAG}" --name "${KIND_CLUSTER}"
fi

if [ "${PUSH}" = "1" ]; then
  echo ">> Pushing to ${REGISTRY}/${REGISTRY_REPOSITORY}:${TAG}"
  docker tag  "${IMAGE}:${TAG}" "${REGISTRY}/${REGISTRY_REPOSITORY}:${TAG}"
  docker push "${REGISTRY}/${REGISTRY_REPOSITORY}:${TAG}"
fi

echo ">> Done."
