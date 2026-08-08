#!/usr/bin/env python3
"""Argo CD must ignore only the Crossplane-generated Application XR.

Issue #301 proved that Crossplane copies Argo's AppClaim tracking annotation to
its cluster-scoped Application XR. The duplicate tracking ID makes app-config
remain OutOfSync. The XR must be excluded from Argo discovery and pruning,
while the namespaced AppClaim remains fully Git-tracked and pruneable.

Pure python3 + PyYAML::

    python3 platform/tests/test_argocd_appclaim_xr_tracking.py
"""

from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
ARGO_VALUES = ROOT / "platform/base/argocd/values.yaml"
APP_CONFIG = ROOT / "apps/platform/app-config.yaml"
APPLICATION_XRD = ROOT / "crossplane/xrds/application.yaml"

EXPECTED_XR_EXCLUSION = {
    "apiGroups": ["platform.digiorg.io"],
    "kinds": ["Application"],
    "clusters": ["*"],
}


def _document(path: Path, kind: str | None = None):
    with path.open(encoding="utf-8") as stream:
        documents = [doc for doc in yaml.safe_load_all(stream) if doc]
    if kind is None:
        return documents[0]
    return next(doc for doc in documents if doc.get("kind") == kind)


class AppClaimXrTrackingContractTest(unittest.TestCase):
    def setUp(self):
        self.values = _document(ARGO_VALUES)
        self.cm = self.values["configs"]["cm"]
        raw_exclusions = self.cm.get("resource.exclusions", "")
        self.exclusions = yaml.safe_load(raw_exclusions) or []
        self.app_config = _document(APP_CONFIG, "Application")
        self.xrd = _document(APPLICATION_XRD, "CompositeResourceDefinition")

    def test_excludes_only_the_controller_generated_application_xr(self):
        self.assertIn(
            EXPECTED_XR_EXCLUSION,
            self.exclusions,
            "Argo CD must exclude the Crossplane-generated Application XR on every cluster",
        )

        for exclusion in self.exclusions:
            groups = exclusion.get("apiGroups", [])
            kinds = exclusion.get("kinds", [])
            if "Application" in kinds or "*" in kinds:
                self.assertNotIn(
                    "*",
                    groups,
                    "Application exclusions must always name their exact API group",
                )
            if "platform.digiorg.io" in groups or "*" in groups:
                self.assertNotIn("AppClaim", kinds)
                self.assertNotIn(
                    "*",
                    kinds,
                    "platform.digiorg.io exclusions must never hide Git-managed AppClaims",
                )

    def test_appclaim_stays_annotation_tracked_and_automatically_pruneable(self):
        self.assertEqual(self.cm["application.resourceTrackingMethod"], "annotation")
        automated = self.app_config["spec"]["syncPolicy"]["automated"]
        self.assertIs(automated["selfHeal"], True)
        self.assertIs(automated["prune"], True)

    def test_excluded_kind_is_the_claim_backed_legacy_cluster_xr(self):
        spec = self.xrd["spec"]
        self.assertEqual(spec["group"], "platform.digiorg.io")
        self.assertEqual(spec["names"]["kind"], "Application")
        self.assertEqual(spec["claimNames"]["kind"], "AppClaim")
        self.assertEqual(self.xrd["apiVersion"], "apiextensions.crossplane.io/v1")
        self.assertNotEqual(spec.get("scope"), "Namespaced")


if __name__ == "__main__":
    unittest.main(verbosity=2)
