#!/usr/bin/env python3
"""Tests for the Crossplane 1.19.0 -> 2.x migration (Issue #275).

Crossplane v2 is the highest-blast-radius migration. Key facts revalidated
against upstream on 2026-07-18:

  * Chart: https://charts.crossplane.io/stable/index.yaml — 2.3.3 (appVersion
    2.3.3) is the newest 2.x. The Helm values keys this repo sets (`replicas`,
    `resourcesCrossplane`, `resourcesRBACManager`) are unchanged in the 2.3.x
    chart (verified against cluster/charts/crossplane/values.yaml @ v2.3.3).
  * XRD compatibility: Crossplane v2 keeps `apiextensions.crossplane.io/v1`
    XRDs in **LegacyCluster** scope — cluster-scoped composite resources that
    still support Claims. So the existing XRD (`apiextensions.crossplane.io/v1`
    + `claimNames`) works unchanged; it must NOT be flipped to Namespaced (which
    drops Claims in v2). https://docs.crossplane.io/latest/guides/upgrade-to-crossplane-v2/
  * Providers keep `pkg.crossplane.io/v1` + `DeploymentRuntimeConfig`
    (`pkg.crossplane.io/v1beta1`); crossplane-contrib providers declare no upper
    Crossplane bound, so they install on 2.3.x. Latest compatible releases:
    provider-kubernetes v1.2.1, provider-helm v1.3.0, provider-http v1.0.14.

Pure python3 + PyYAML::

    python3 platform/tests/test_crossplane_migration.py
"""

import os
import re
import sys
import unittest

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.stderr.write("PyYAML is required: pip install pyyaml\n")
    raise

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
APP = os.path.join(REPO_ROOT, "apps", "platform", "crossplane.yaml")
PKG_DIR = os.path.join(REPO_ROOT, "crossplane", "providers", "packages")
XRD = os.path.join(REPO_ROOT, "crossplane", "xrds", "application.yaml")

# Revalidated compatible provider package versions (exact pins).
EXPECTED_PROVIDERS = {
    "provider-kubernetes": "v1.2.1",
    "provider-helm": "v1.3.0",
    "provider-http": "v1.0.14",
}


def _docs(path):
    with open(path, encoding="utf-8") as fh:
        return [d for d in yaml.safe_load_all(fh) if d]


class ChartPinTest(unittest.TestCase):
    def test_chart_pinned_to_2_x_exact(self):
        app = _docs(APP)[0]
        rev = str(app["spec"]["source"]["targetRevision"])
        self.assertRegex(rev, r"^2\.\d+\.\d+$",
                         "crossplane chart must pin an exact 2.x SemVer, got %r" % rev)


class ProviderVersionTest(unittest.TestCase):
    """Every provider package must pin an exact, v2-compatible release tag."""

    def _providers(self):
        found = {}
        for name in os.listdir(PKG_DIR):
            if not name.endswith(".yaml"):
                continue
            for doc in _docs(os.path.join(PKG_DIR, name)):
                if doc.get("kind") == "Provider":
                    pkg = doc["spec"]["package"]
                    found[doc["metadata"]["name"]] = pkg
        return found

    def test_provider_versions_pinned_and_compatible(self):
        providers = self._providers()
        for name, want in EXPECTED_PROVIDERS.items():
            self.assertIn(name, providers, "missing Provider %s" % name)
            pkg = providers[name]
            self.assertTrue(
                pkg.endswith(":" + want) or (":" + want + "@sha256:") in pkg,
                "%s must pin %s, got %r" % (name, want, pkg),
            )

    def test_no_floating_provider_tags(self):
        for name, pkg in self._providers().items():
            tag = pkg.split("@")[0].rsplit(":", 1)[-1]
            self.assertNotIn(tag, ("latest", "main", ""),
                             "%s must not use a floating package tag" % name)
            self.assertRegex(tag, r"^v\d+\.\d+\.\d+",
                             "%s package tag must be an exact version" % name)


class XrdLegacyClusterCompatTest(unittest.TestCase):
    """The XRD must stay v1/LegacyCluster so Claims keep working under v2."""

    def setUp(self):
        self.xrd = next(d for d in _docs(XRD)
                        if d.get("kind") == "CompositeResourceDefinition")

    def test_uses_v1_api(self):
        # v1 XRDs map to LegacyCluster scope in v2 (cluster-scoped XR + Claims).
        self.assertEqual(self.xrd["apiVersion"], "apiextensions.crossplane.io/v1")

    def test_claims_preserved(self):
        # Claims (AppClaim) are only supported by LegacyCluster XRs in v2; the XRD
        # must keep claimNames and must NOT declare scope: Namespaced.
        self.assertIn("claimNames", self.xrd["spec"],
                      "claimNames must be retained for LegacyCluster claim support")
        self.assertNotEqual(self.xrd["spec"].get("scope"), "Namespaced",
                            "Namespaced scope drops Claims support in Crossplane v2")


if __name__ == "__main__":
    unittest.main(verbosity=2)
