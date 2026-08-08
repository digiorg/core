#!/usr/bin/env python3
"""Backstage must run the customized core-portal image, not the scaffold (#279).

``ghcr.io/digiorg/core-portal:48d262e`` is core-portal's **initial commit**
(2026-04-18) — the stock Backstage scaffold, before the DigiOrg theme
(commit ``9077fa3``) and the TeraSky Crossplane plugins (commits ``42bc40e``/
``c74fe7c``/``b77e94a``) were added. Pinning it explained why the deployed UI
looked unstyled/uncustomized.

``5fc1357107dd888c1fddab0cff31b51291ffcef9`` is core-portal PR #12's
reviewed Issue #301 product commit. Merge commit
``a21f3e787250c9199e7a4ab7e9495fe1a1f424ac`` was built and published by
push run 31272571743 as SHA tag ``a21f3e7``.
On 2026-08-08, the anonymous GHCR OCI header and raw manifest sha256
independently resolved to the digest below. The OCI index includes linux/amd64
and linux/arm64 images plus unknown/unknown attestations.

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

# Independently resolved from the anonymous GHCR OCI header and raw manifest
# on 2026-08-08, and cross-checked against core-portal PR #12's reviewed
# product commit, authoritative merge commit, and exact-head publish run.
EXPECTED_IMAGE = (
    "ghcr.io/digiorg/core-portal:a21f3e7"
    "@sha256:57312d346e27e87d72d17ebcf478827bb24d1c0a45ce9a49f5aa63fddcbb5593"
)
INITIAL_SCAFFOLD_COMMIT = "48d262e"
PRODUCT_COMMIT = "5fc1357107dd888c1fddab0cff31b51291ffcef9"
MERGE_COMMIT = "a21f3e787250c9199e7a4ab7e9495fe1a1f424ac"
PUBLISH_RUN = "31272571743"


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
        for provenance in (
            EXPECTED_IMAGE,
            "Issue #301",
            "PR #12",
            PRODUCT_COMMIT,
            MERGE_COMMIT,
            PUBLISH_RUN,
            "2026-08-08",
        ):
            self.assertIn(provenance, doc)


if __name__ == "__main__":
    unittest.main(verbosity=2)
