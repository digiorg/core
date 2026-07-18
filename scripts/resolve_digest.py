#!/usr/bin/env python3
"""Resolve an immutable ``@sha256:`` digest for a container image tag.

Companion to ``scripts/check_pins.py``: the pin policy requires a human-readable
tag *plus* an immutable digest, and this tool produces the digest without Docker
by talking to the registry v2 API directly (anonymous pull tokens). It backs the
"record a human-readable version tag plus an immutable digest" workflow for
Docker Hub, Quay, GHCR and ``registry.k8s.io``.

Usage::

    python3 scripts/resolve_digest.py quay.io/keycloak/keycloak:26.7.0
    python3 scripts/resolve_digest.py ghcr.io/cloudnative-pg/postgresql:16.9
    python3 scripts/resolve_digest.py postgres:16-alpine     # Docker Hub library

Prints ``<registry>/<repo>:<tag>@sha256:<digest>`` on success. Network access to
the registry is required; on failure it exits non-zero with a clear message so a
human can pin the digest by hand from a documented source.
"""

from __future__ import annotations

import json
import sys
import urllib.request

_UA = {"User-Agent": "digiorg-resolve-digest"}
_ACCEPT = ",".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)


def _get(url: str, headers: dict):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.headers, resp.read()


def parse_ref(ref: str):
    """Split an image reference into (registry, repository, tag)."""
    if "@" in ref:
        ref = ref.split("@", 1)[0]
    tag = "latest"
    # Only treat the last ':' as a tag separator if it is not part of a host:port.
    if ":" in ref.rsplit("/", 1)[-1]:
        ref, tag = ref.rsplit(":", 1)
    if "/" not in ref:  # Docker Hub official image, e.g. "postgres"
        registry, repo = "registry-1.docker.io", f"library/{ref}"
    else:
        head, rest = ref.split("/", 1)
        if "." in head or ":" in head or head == "localhost":
            registry, repo = head, rest
        else:  # Docker Hub namespaced, e.g. "fluent/fluentd-..."
            registry, repo = "registry-1.docker.io", ref
    if registry == "docker.io":
        registry = "registry-1.docker.io"
    return registry, repo, tag


def _token(registry: str, repo: str):
    if registry == "registry-1.docker.io":
        url = f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull"
    elif registry == "ghcr.io":
        url = f"https://ghcr.io/token?scope=repository:{repo}:pull"
    else:
        return None  # quay.io and registry.k8s.io allow anonymous manifest reads
    return json.loads(_get(url, _UA)[1]).get("token")


def resolve(ref: str) -> str:
    registry, repo, tag = parse_ref(ref)
    headers = dict(_UA)
    headers["Accept"] = _ACCEPT
    token = _token(registry, repo)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    hdr, _ = _get(f"https://{registry}/v2/{repo}/manifests/{tag}", headers)
    digest = hdr.get("Docker-Content-Digest")
    if not digest:
        raise RuntimeError(f"registry did not return a digest for {ref}")
    display_repo = repo[len("library/"):] if repo.startswith("library/") and registry == "registry-1.docker.io" else repo
    prefix = "" if registry == "registry-1.docker.io" and "/" not in display_repo else f"{registry}/"
    if registry != "registry-1.docker.io":
        prefix = f"{registry}/"
    return f"{prefix}{display_repo}:{tag}@{digest}"


def main(argv: list[str]) -> int:
    if not argv:
        sys.stderr.write(__doc__ or "")
        return 2
    rc = 0
    for ref in argv:
        try:
            print(resolve(ref))
        except Exception as exc:  # noqa: BLE001 - CLI boundary
            sys.stderr.write(f"ERROR resolving {ref}: {exc}\n")
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
