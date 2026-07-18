#!/usr/bin/env python3
"""Tests for the SonarQube chart migration 10.8.1 -> 2026.x (Issue #275).

SonarSource moved to calendar versioning; the chart is now ``2026.x`` and the
platform deploys the **Community Build** via ``community.enabled: true`` +
``community.buildNumber``. The 2026.3.1 chart keeps every value key this repo
relies on (``jdbcOverwrite``, ``monitoringPasscodeSecretName``,
``sonarWebContext``, ``deploymentType``, ``initSysctl``, ``sonarProperties`` …),
so the migration is a version bump plus a Community-Build number aligned with the
chart default.

Sources (revalidated 2026-07-18):
  * chart index https://SonarSource.github.io/helm-chart-sonarqube/index.yaml —
    2026.3.1 (appVersion 2026.3.1) is newest.
  * chart values (tag sonarqube-2026.3.1-…) — community.buildNumber default
    "26.5.0.122743"; jdbcOverwrite/monitoringPasscodeSecretName/sonarWebContext
    all present.

Pure python3 + PyYAML::

    python3 platform/tests/test_sonarqube_migration.py
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
APP = os.path.join(REPO_ROOT, "apps", "platform", "sonarqube.yaml")
VALUES = os.path.join(REPO_ROOT, "platform", "base", "sonarqube", "values.yaml")

# The Community Build number shipped as the 2026.3.1 chart default.
EXPECTED_BUILD = "26.5.0.122743"


def _docs(path):
    with open(path, encoding="utf-8") as fh:
        return [d for d in yaml.safe_load_all(fh) if d]


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class ChartPinTest(unittest.TestCase):
    def setUp(self):
        app = _docs(APP)[0]
        self.src = next(s for s in app["spec"]["sources"] if s.get("chart") == "sonarqube")

    def test_chart_pinned_to_calendar_2026_exact(self):
        rev = str(self.src["targetRevision"])
        self.assertRegex(rev, r"^2026\.\d+\.\d+$",
                         "sonarqube chart must pin an exact 2026.x SemVer, got %r" % rev)


class ValuesMigrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.values = _load(VALUES)

    def test_community_build_enabled_and_pinned(self):
        self.assertTrue(self.values["community"]["enabled"],
                        "Community Build must stay enabled")
        # buildNumber must be pinned and aligned with the migrated chart default.
        self.assertEqual(self.values["community"]["buildNumber"], EXPECTED_BUILD,
                         "community.buildNumber must align with the 2026.x chart")

    def test_community_image_is_digest_pinned(self):
        image = self.values["image"]
        self.assertEqual(image.get("repository"), "sonarqube")
        self.assertEqual(
            image.get("tag"),
            "26.5.0.122743-community@sha256:223d0090322edce6211a5328298b6f646920f1535025ef8dd880cab6647bb1fa",
        )

    def test_shared_db_retained(self):
        jo = self.values["jdbcOverwrite"]
        self.assertTrue(jo["enabled"])
        self.assertIn("postgresql.platform-db.svc.cluster.local:5432", jo["jdbcUrl"])
        self.assertEqual(jo["jdbcUsername"], "sonarqube")
        self.assertEqual(jo["jdbcSecretName"], "sonarqube-db-secret")
        self.assertFalse(self.values["postgresql"].get("enabled", True),
                         "bundled postgresql subchart must stay disabled")

    def test_monitoring_passcode_secret_retained(self):
        self.assertEqual(self.values["monitoringPasscodeSecretName"],
                         "sonarqube-monitoring-secret")

    def test_web_context_subpath_retained(self):
        self.assertEqual(self.values["sonarWebContext"], "/sonarqube")


if __name__ == "__main__":
    unittest.main(verbosity=2)
