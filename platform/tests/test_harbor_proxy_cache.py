#!/usr/bin/env python3
"""Tests for the Harbor proxy-cache configuration (Issue #275, Tier 2).

Tier 2 requires Harbor to pull upstream images through **proxy-cache projects**
for Docker Hub, Quay, GHCR and registry.k8s.io. Harbor stores registries and
projects in its database, so — exactly like the OIDC config (Issue #262) — they
are provisioned by an ArgoCD PostSync Job that calls the Harbor REST API
(``POST /api/v2.0/registries`` then ``POST /api/v2.0/projects`` with
``registry_id``).

Registry provider ``type`` strings are the exact Harbor constants
(src/pkg/reg/model/registry.go): docker-hub, quay, github-ghcr, docker-registry.

Pure python3 + PyYAML::

    python3 platform/tests/test_harbor_proxy_cache.py
"""

import os
import sys
import unittest

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.stderr.write("PyYAML is required: pip install pyyaml\n")
    raise

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HARBOR_DIR = os.path.join(REPO_ROOT, "platform", "base", "harbor")
JOB = os.path.join(HARBOR_DIR, "harbor-proxy-cache-job.yaml")
KUSTOMIZATION = os.path.join(HARBOR_DIR, "kustomization.yaml")

# name -> (harbor registry type, upstream url) that MUST be configured.
EXPECTED_REGISTRIES = {
    "docker-hub": ("docker-hub", "https://hub.docker.com"),
    "quay": ("quay", "https://quay.io"),
    "ghcr": ("github-ghcr", "https://ghcr.io"),
    "registry-k8s-io": ("docker-registry", "https://registry.k8s.io"),
}


def _docs(path):
    with open(path, encoding="utf-8") as fh:
        return [d for d in yaml.safe_load_all(fh) if d]


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class JobShapeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.job = next(d for d in _docs(JOB) if d.get("kind") == "Job")
        cls.container = cls.job["spec"]["template"]["spec"]["containers"][0]

    def test_is_postsync_hook_in_harbor_namespace(self):
        ann = self.job["metadata"]["annotations"]
        self.assertEqual(ann.get("argocd.argoproj.io/hook"), "PostSync")
        self.assertEqual(self.job["metadata"]["namespace"], "harbor")

    def test_uses_digest_pinned_curl_image(self):
        self.assertRegex(self.container["image"], r"^curlimages/curl:.*@sha256:[0-9a-f]{64}$")

    def test_admin_password_from_secret(self):
        env = {e["name"]: e for e in self.container.get("env", [])}
        self.assertIn("HARBOR_ADMIN_PASSWORD", env)
        self.assertIn("secretKeyRef", env["HARBOR_ADMIN_PASSWORD"]["valueFrom"])


class ProxyCacheContentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.job = next(d for d in _docs(JOB) if d.get("kind") == "Job")
        cls.script = cls.job["spec"]["template"]["spec"]["containers"][0]["command"][-1]

    def test_calls_registries_and_projects_endpoints(self):
        # The base path is composed from a HARBOR_API var (like the OIDC job).
        self.assertIn("/api/v2.0", self.script)
        self.assertIn("/registries", self.script)
        self.assertIn("/projects", self.script)

    def test_waits_for_harbor_ready(self):
        self.assertIn("/ping", self.script)
        self.assertIn("activeDeadlineSeconds", self.job["spec"])
        self.assertIn("--connect-timeout", self.script)
        self.assertIn("--max-time", self.script)
        self.assertIn("readiness timeout", self.script)

    def test_conflicts_are_verified_not_blindly_accepted(self):
        self.assertIn("wrong type", self.script)
        self.assertIn("wrong URL", self.script)
        self.assertIn("not bound to registry", self.script)
        self.assertIn("already exists and matches", self.script)

    def test_every_registry_type_and_url_present(self):
        for name, (rtype, url) in EXPECTED_REGISTRIES.items():
            self.assertIn(rtype, self.script,
                          f"registry type {rtype} ({name}) must be configured")
            self.assertIn(url, self.script,
                          f"upstream url {url} ({name}) must be configured")

    def test_projects_reference_registry_id(self):
        # A project only becomes a proxy cache when created with registry_id.
        self.assertIn("registry_id", self.script,
                      "proxy-cache projects must be created with a registry_id")

    def test_idempotent_on_conflict(self):
        # Re-running on an existing Harbor must not fail (409 already-exists).
        self.assertIn("409", self.script,
                      "the job must treat HTTP 409 (already exists) as success")


class KustomizationTest(unittest.TestCase):
    def test_job_is_included(self):
        k = _docs(KUSTOMIZATION)[0]
        self.assertIn("harbor-proxy-cache-job.yaml", k.get("resources", []))


if __name__ == "__main__":
    unittest.main(verbosity=2)
