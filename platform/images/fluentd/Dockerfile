# syntax=docker/dockerfile:1
# =============================================================================
# DigiOrg pinned Fluentd log-forwarder image — Issue #275 (Tier 1)
# =============================================================================
# The platform ran fluent/fluentd-kubernetes-daemonset on a FLOATING minor tag
# (v1.19-debian-opensearch-1) and inherited whatever plugin versions the image
# happened to ship. This pins the base by immutable digest and installs
# EXPLICITLY versioned plugin gems, so a clean bootstrap forwards logs with the
# same, reproducible plugin set every time.
#
# The base image entrypoint/cmd and its bundled config are inherited unchanged
# (no COPY, no ENTRYPOINT override) — only the plugin gem set is pinned on top.
# =============================================================================

ARG UPSTREAM_IMAGE=fluent/fluentd-kubernetes-daemonset
ARG UPSTREAM_TAG=v1.19.2-debian-opensearch-1.0
ARG UPSTREAM_DIGEST=sha256:f7a636ae892cab78b0e63fcb4d89f68a84bd602609a7f38b00fcc5c65f487de2

FROM ${UPSTREAM_IMAGE}:${UPSTREAM_TAG}@${UPSTREAM_DIGEST}

# --- Explicitly pinned plugin gem versions ---------------------------------- #
# Sourced from rubygems.org (latest releases, verified mutually compatible with
# the base fluentd 1.19.2 — see ./README.md):
#   * fluent-plugin-opensearch 1.1.6            (requires fluentd >= 0.14.22)
#   * fluent-plugin-kubernetes_metadata_filter 3.8.0 (requires fluentd >= 0.14.0, < 1.20)
USER root
RUN gem install fluent-plugin-opensearch -v 1.1.6 --no-document \
 && gem install fluent-plugin-kubernetes_metadata_filter -v 3.8.0 --no-document \
 && gem sources --clear-all \
 && rm -rf /var/lib/gems/*/cache/*.gem

# --- Provenance (baked as OCI labels) --------------------------------------- #
ARG version=v1.19.2-debian-opensearch-1.0
ARG base_digest=sha256:f7a636ae892cab78b0e63fcb4d89f68a84bd602609a7f38b00fcc5c65f487de2
LABEL org.opencontainers.image.title="digiorg/fluentd" \
      org.opencontainers.image.description="DigiOrg Fluentd with pinned opensearch + kubernetes_metadata_filter plugins" \
      org.opencontainers.image.source="https://github.com/digiorg/core" \
      org.opencontainers.image.version="${version}" \
      org.opencontainers.image.revision="${base_digest}" \
      org.opencontainers.image.base.name="fluent/fluentd-kubernetes-daemonset:v1.19.2-debian-opensearch-1.0"
