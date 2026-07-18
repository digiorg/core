#!/usr/bin/env python3
"""Backstage must run the customized core-portal image, not the scaffold (#279).

``ghcr.io/digiorg/core-portal:48d262e`` is core-portal's **initial commit**
(2026-04-18) — the stock Backstage scaffold, before the DigiOrg theme
(commit ``9077fa3``) and the TeraSky Crossplane plugins (commits ``42bc40e``/
``c74fe7c``/``b77e94a``) were added. Pinning it explained why the deployed UI
looked unstyled/uncustomized.

``b77e94a1a0e50a834f1844918c4e7287b0764c0b`` is core-portal's current `main`
HEAD (confirmed via ``gh api repos/digiorg/core-portal/commits``) and CI built
and pushed it successfully (workflow run 26395858961, conclusion "success").
The digest below was resolved independently from the GHCR anonymous registry
API (``scripts/resolve_digest.py ghcr.io/digiorg/core-portal:b77e94a``), not
invented.

Pure python3 + PyYAML, no cluster/network access::

    python3 platform/tests/test_backstage_image.py
"""
import os
import re
import unittest

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEPLOYMENT = os.path.join(REPO_ROOT, "platform", "base", "backstage", "deployment.yaml")
VERSIONS_DOC = os.path.join(REPO_ROOT, "docs", "guides", "platform-versions.md")

# Resolved 2026-07-18 via scripts/resolve_digest.py against the live GHCR API,
# and independently cross-checked against core-portal's commit history/Actions
# run for that exact commit.
EXPECTED_IMAGE = (
    "ghcr.io/digiorg/core-portal:b77e94a"
    "@sha256:44d00aba125e1e66d712222ec7f64cbc7cce02f05e5f05146af5af996bfe19dd"
)
INITIAL_SCAFFOLD_COMMIT = "48d262e"


def _backstage_container():
    with open(DEPLOYMENT, encoding="utf-8") as fh:
        deployment = yaml.safe_load(fh)
    containers = deployment["spec"]["template"]["spec"]["containers"]
    return next(c for c in containers if c["name"] == "backstage")


class BackstageImageProvenanceTest(unittest.TestCase):
    def test_pinned_to_current_customized_release_with_digest(self):
        image = _backstage_container()["image"]
        self.assertEqual(image, EXPECTED_IMAGE)

    def test_not_pinned_to_initial_scaffold_commit(self):
        image = _backstage_container()["image"]
        self.assertNotIn(INITIAL_SCAFFOLD_COMMIT, image)

    def test_not_floating(self):
        image = _backstage_container()["image"]
        tag = image.split("@")[0].split(":")[-1]
        self.assertNotIn(tag, ("latest", "main", "master"))
        self.assertRegex(image, r"@sha256:[0-9a-f]{64}$")

    def test_versions_doc_updated(self):
        with open(VERSIONS_DOC, encoding="utf-8") as fh:
            doc = fh.read()
        self.assertIn("core-portal", doc)
        self.assertNotIn("core-portal` | 48d262e", doc)
        self.assertIn("b77e94a", doc)


if __name__ == "__main__":
    unittest.main(verbosity=2)
