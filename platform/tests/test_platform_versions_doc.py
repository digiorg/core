#!/usr/bin/env python3
"""The platform version/upgrade documentation must exist and stay in sync (Issue #275).

Several manifests point operators at ``docs/guides/platform-versions.md`` for the
upgrade/rollback/CRD-ordering notes (crossplane, sonarqube, the Harbor
proxy-cache Job). This guards those references from going dead and asserts the
doc actually covers the pinned versions, the major migrations, rollback, and the
Harbor proxy-cache bootstrap exception.

Pure python3::

    python3 platform/tests/test_platform_versions_doc.py
"""

import os
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DOC = os.path.join(REPO_ROOT, "docs", "guides", "platform-versions.md")
REFERRERS = [
    os.path.join(REPO_ROOT, "apps", "platform", "crossplane.yaml"),
    os.path.join(REPO_ROOT, "apps", "platform", "sonarqube.yaml"),
    os.path.join(REPO_ROOT, "platform", "base", "harbor", "harbor-proxy-cache-job.yaml"),
]


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class DocPresenceTest(unittest.TestCase):
    def test_doc_exists(self):
        self.assertTrue(os.path.exists(DOC), "docs/guides/platform-versions.md must exist")

    def test_referenced_by_manifests_and_reference_is_valid(self):
        for path in REFERRERS:
            self.assertIn("docs/guides/platform-versions.md", _read(path),
                          f"{os.path.relpath(path, REPO_ROOT)} should reference the versions guide")


class DocContentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = _read(DOC)
        cls.lower = cls.text.lower()

    def test_covers_pinned_migration_versions(self):
        # The exact pins that must appear so the doc can't silently drift.
        for token in ("12.6.0", "2026.3.1", "2.3.3", "2.7.0", "0.29.0", "87.17.0"):
            self.assertIn(token, self.text, f"versions doc must list pin {token}")

    def test_covers_upgrade_and_rollback(self):
        self.assertIn("rollback", self.lower)
        self.assertIn("upgrade", self.lower)

    def test_covers_pin_policy_and_ci(self):
        self.assertIn("check_pins.py", self.text)

    def test_covers_harbor_proxy_cache_bootstrap_exception(self):
        self.assertIn("proxy-cache", self.lower)
        self.assertIn("bootstrap exception", self.lower)

    def test_links_cnpg_runbook(self):
        self.assertIn("postgres-cnpg-migration.md", self.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
