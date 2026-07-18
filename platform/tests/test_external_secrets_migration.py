#!/usr/bin/env python3
"""Tests for the External Secrets Operator 0.14.4 -> 2.x migration (Issue #275).

ESO stopped serving ``external-secrets.io/v1beta1`` at 0.17.0; the 2.x operator
(chart 2.7.0, appVersion v2.7.0) only serves the stable ``external-secrets.io/v1``
API. Every ExternalSecret / (Cluster)SecretStore manifest in the repo must
therefore be on ``external-secrets.io/v1`` or it will be rejected by the API
server after the upgrade.

Sources (revalidated 2026-07-18):
  * chart index https://charts.external-secrets.io/index.yaml — 2.7.0 newest.
  * https://external-secrets.io/latest/guides/v1beta1/ — v1beta1 removed.

Pure python3 + PyYAML::

    python3 platform/tests/test_external_secrets_migration.py
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
APP = os.path.join(REPO_ROOT, "apps", "platform", "external-secrets.yaml")
ESO_DIR = os.path.join(REPO_ROOT, "platform", "base", "external-secrets")
ESO_KINDS = {"ExternalSecret", "SecretStore", "ClusterSecretStore", "PushSecret"}


def _docs(path):
    with open(path, encoding="utf-8") as fh:
        return [d for d in yaml.safe_load_all(fh) if d]


class ChartPinTest(unittest.TestCase):
    def test_chart_pinned_to_2_x_exact(self):
        app = _docs(APP)[0]
        src = next(s for s in app["spec"]["sources"] if s.get("chart") == "external-secrets")
        rev = str(src["targetRevision"])
        self.assertRegex(rev, r"^2\.\d+\.\d+$",
                         "external-secrets chart must pin an exact 2.x SemVer, got %r" % rev)


class ApiVersionMigrationTest(unittest.TestCase):
    """No ESO CR may remain on the removed v1beta1/v1alpha1 API."""

    def _eso_docs(self):
        out = []
        for name in os.listdir(ESO_DIR):
            if not name.endswith((".yaml", ".yml")):
                continue
            for doc in _docs(os.path.join(ESO_DIR, name)):
                if doc.get("kind") in ESO_KINDS:
                    out.append((name, doc))
        return out

    def test_at_least_one_cr_present(self):
        self.assertTrue(self._eso_docs(), "expected ESO CR manifests to validate")

    def test_all_crs_on_stable_v1_api(self):
        for name, doc in self._eso_docs():
            api = doc["apiVersion"]
            self.assertEqual(
                api, "external-secrets.io/v1",
                f"{name}: {doc['kind']} must be external-secrets.io/v1 "
                f"(v1beta1 is removed in ESO 2.x), got {api}",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
