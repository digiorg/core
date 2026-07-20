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
CATALOG_APP = os.path.join(REPO_ROOT, "apps", "platform", "core-catalog.yaml")
PKG_DIR = os.path.join(REPO_ROOT, "crossplane", "providers", "packages")
XRD = os.path.join(REPO_ROOT, "crossplane", "xrds", "application.yaml")
FUNCTION = os.path.join(PKG_DIR, "function-patch-and-transform.yaml")
PROVIDER_KUSTOMIZATION = os.path.join(PKG_DIR, "kustomization.yaml")
VERSIONS_DOC = os.path.join(REPO_ROOT, "docs", "guides", "platform-versions.md")

# core-catalog PR #17 merged as this commit; a GitHub comparison shows it one
# commit ahead of the old implementation SHA with NO changed files, and
# core-catalog/main points at it. Pin the canonical *reviewed merge* commit for
# traceability (Issue #281 Phase 4). Update this constant, the manifest, and the
# versions doc together when the reviewed revision advances.
CANONICAL_CATALOG_REVISION = "13b7a3b4a0b7a5f5e692dc6d5a3fa416852c4273"
# The prior (non-canonical) implementation SHA must no longer be referenced.
SUPERSEDED_CATALOG_REVISION = "4c30d9c3fa5c3c401c19f26b66a025854c65ee02"

# Revalidated compatible provider package versions (exact pins).
EXPECTED_PROVIDERS = {
    "provider-kubernetes": "v1.2.1",
    "provider-helm": "v1.3.0",
    "provider-http": "v1.0.14",
}


def _docs(path):
    with open(path, encoding="utf-8") as fh:
        return [d for d in yaml.safe_load_all(fh) if d]


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


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


class CompositionFunctionTest(unittest.TestCase):
    def test_patch_and_transform_function_is_exactly_pinned_and_installed(self):
        function = _docs(FUNCTION)[0]
        self.assertEqual(function["kind"], "Function")
        self.assertEqual(function["metadata"]["name"], "function-patch-and-transform")
        self.assertEqual(
            function["spec"]["package"],
            "xpkg.crossplane.io/crossplane-contrib/function-patch-and-transform:v0.10.7",
        )
        kustomization = _docs(PROVIDER_KUSTOMIZATION)[0]
        self.assertIn("function-patch-and-transform.yaml", kustomization["resources"])


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


class CoreCatalogCanonicalPinTest(unittest.TestCase):
    """core-catalog must pin the canonical reviewed merge commit, stay immutable,
    and remain manually gated (Issue #281 Phase 4)."""

    def setUp(self):
        self.app = _docs(CATALOG_APP)[0]

    def test_pins_canonical_merge_commit(self):
        rev = str(self.app["spec"]["source"]["targetRevision"])
        self.assertEqual(
            rev, CANONICAL_CATALOG_REVISION,
            "core-catalog must pin the canonical reviewed merge commit",
        )

    def test_superseded_revision_not_referenced_anywhere(self):
        # The old implementation SHA must be gone from the manifest and the doc.
        self.assertNotIn(SUPERSEDED_CATALOG_REVISION, _read(CATALOG_APP))
        self.assertNotIn(SUPERSEDED_CATALOG_REVISION, _read(VERSIONS_DOC))

    def test_revision_is_immutable_40_hex(self):
        rev = str(self.app["spec"]["source"]["targetRevision"])
        self.assertRegex(rev, r"^[0-9a-f]{40}$",
                         "catalog revision must be an immutable 40-hex commit")

    def test_remains_manually_gated(self):
        # Manual promotion must be preserved: no automated sync, gate annotation kept.
        policy = self.app["spec"].get("syncPolicy", {})
        self.assertNotIn("automated", policy,
                         "core-catalog must not auto-sync (manual promotion gate)")
        self.assertEqual(
            self.app["metadata"].get("annotations", {}).get("platform.digiorg.io/upgrade-gate"),
            "issue-275-manual",
        )

    def test_versions_doc_records_canonical_revision(self):
        # The reviewed revision must be documented so the pin is traceable.
        self.assertIn(CANONICAL_CATALOG_REVISION,
                      _read(VERSIONS_DOC),
                      "platform-versions.md must record the canonical catalog revision")


if __name__ == "__main__":
    unittest.main(verbosity=2)
