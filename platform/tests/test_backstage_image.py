#!/usr/bin/env python3
"""Backstage must run the customized core-portal image, not the scaffold (#279).

``ghcr.io/digiorg/core-portal:48d262e`` is core-portal's **initial commit**
(2026-04-18) — the stock Backstage scaffold, before the DigiOrg theme
(commit ``9077fa3``) and the TeraSky Crossplane plugins (commits ``42bc40e``/
``c74fe7c``/``b77e94a``) were added. Pinning it explained why the deployed UI
looked unstyled/uncustomized.

``e87210b270ab94539db85e21fd6bc8f943fb7bf4`` is core-portal PR #9's
reviewed Issue #285 commit and CI built and pushed it successfully
(workflow run 29983067024, conclusion "success").
The digest below was resolved independently from the GHCR anonymous registry
API (``docker buildx imagetools inspect ghcr.io/digiorg/core-portal:e87210b``), not
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

# Resolved 2026-07-23 via the live GHCR API,
# and independently cross-checked against core-portal's commit history/Actions
# run for that exact commit.
EXPECTED_IMAGE = (
    "ghcr.io/digiorg/core-portal:e87210b"
    "@sha256:a255cf284f333741429ca84d17382d890a2536dc921a8d8ef189e14e2e6fb767"
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
        self.assertIn("e87210b", doc)


if __name__ == "__main__":
    unittest.main(verbosity=2)
