# syntax=docker/dockerfile:1
# =============================================================================
# DigiOrg production-optimized Keycloak image — Issue #275 (Tier 1)
# =============================================================================
# Multi-stage build that produces a Keycloak server pre-augmented for the
# platform's build-time options, so the deployment can run `start --optimized`
# (production start with no per-boot re-augmentation) instead of `start-dev`.
#
# Realm/user data is intentionally NOT baked in — it stays in the realm-import
# configMap the deployment mounts at /opt/keycloak/data/import at runtime.
#
# Both stages pin the SAME immutable base digest, so an upstream re-tag can
# never silently change the source. build.nu verifies the tag still resolves to
# UPSTREAM_DIGEST before invoking this Dockerfile.
# =============================================================================

ARG UPSTREAM_IMAGE=quay.io/keycloak/keycloak
ARG UPSTREAM_TAG=26.7.0
ARG UPSTREAM_DIGEST=sha256:2eb3cd316835c990e69e26ade292ffa78f6fb0db7d5fc6377463c162e1979ac0

# --- Stage 1: build the optimized server ------------------------------------ #
FROM ${UPSTREAM_IMAGE}:${UPSTREAM_TAG}@${UPSTREAM_DIGEST} AS builder

# Build-time options that `start --optimized` then relies on being fixed. These
# MUST match the runtime intent (KC_DB=postgres etc.); changing them requires a
# rebuild, which is exactly the production contract we want.
ENV KC_DB=postgres
ENV KC_HEALTH_ENABLED=true
ENV KC_METRICS_ENABLED=true

# Produce the augmented/optimized distribution under /opt/keycloak.
RUN /opt/keycloak/bin/kc.sh build

# --- Stage 2: final runtime image (same pinned base) ------------------------ #
FROM ${UPSTREAM_IMAGE}:${UPSTREAM_TAG}@${UPSTREAM_DIGEST}

# Copy ONLY the optimized build — no realm/user JSON is ever added here.
COPY --from=builder /opt/keycloak/ /opt/keycloak/

# Provenance (baked as OCI labels). base_digest records the immutable source.
ARG version=26.7.0
ARG base_digest=sha256:2eb3cd316835c990e69e26ade292ffa78f6fb0db7d5fc6377463c162e1979ac0
LABEL org.opencontainers.image.title="digiorg/keycloak" \
      org.opencontainers.image.description="DigiOrg production-optimized Keycloak (kc.sh build --optimized)" \
      org.opencontainers.image.source="https://github.com/digiorg/core" \
      org.opencontainers.image.version="${version}" \
      org.opencontainers.image.revision="${base_digest}" \
      org.opencontainers.image.base.name="quay.io/keycloak/keycloak:26.7.0"

ENTRYPOINT ["/opt/keycloak/bin/kc.sh"]
