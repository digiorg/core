#!/usr/bin/env python3
"""Backstage must run the customized core-portal image, not the scaffold (#279).

``ghcr.io/digiorg/core-portal:48d262e`` is core-portal's **initial commit**
(2026-04-18) — the stock Backstage scaffold, before the DigiOrg theme
(commit ``9077fa3``) and the TeraSky Crossplane plugins (commits ``42bc40e``/
``c74fe7c``/``b77e94a``) were added. Pinning it explained why the deployed UI
looked unstyled/uncustomized.

``9e58baed46482361cfc5038f96611ea8c09097d2`` is core-portal PR #10's
reviewed Issue #290 commit. PR CI run 30529314569 passed that exact SHA, and
workflow_dispatch publish run 30529864513 successfully published SHA tag
``9e58bae``.
On 2026-07-30, the anonymous GHCR OCI header digest and raw manifest sha256
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
# on 2026-07-30, and cross-checked against core-portal PR #10's exact reviewed
# commit, PR CI run 30529314569, and publish run 30529864513.
EXPECTED_IMAGE = (
    "ghcr.io/digiorg/core-portal:9e58bae"
    "@sha256:d5d55426bbb4bc6ca9e9f14fe2ec38656801a31d017810b6ab0bcdbfb53b58cb"
)
INITIAL_SCAFFOLD_COMMIT = "48d262e"
REVIEWED_COMMIT = "9e58baed46482361cfc5038f96611ea8c09097d2"


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
            "Issue #290",
            "PR #10",
            REVIEWED_COMMIT,
            "30529314569",
            "30529864513",
            "2026-07-30",
        ):
            self.assertIn(provenance, doc)


if __name__ == "__main__":
    unittest.main(verbosity=2)
