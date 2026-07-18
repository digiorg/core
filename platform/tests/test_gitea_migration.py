#!/usr/bin/env python3
"""Tests for the Gitea chart migration 10.6.0 -> 12.x (Issue #275).

The 12.0 Helm chart carries a hard breaking change: the bundled in-memory cache
provider changed from Redis to **Valkey**, and the default-enabled subchart is
now ``valkey-cluster`` (``redis``/``redis-cluster`` keys no longer exist). The
platform runs Gitea against the shared platform-db PostgreSQL with in-process
session/cache/queue, so the bundled ``valkey-cluster``/``valkey`` AND
``postgresql``/``postgresql-ha`` subcharts must all be explicitly disabled, and
the old ``redis-cluster`` toggle (now a dead key) must be gone.

Sources (revalidated 2026-07-18):
  * chart index https://dl.gitea.com/charts/index.yaml — 12.6.0 (appVersion 1.26.1)
  * chart values https://gitea.com/gitea/helm-gitea/raw/tag/v12.6.0/values.yaml —
    postgresql-ha and valkey-cluster default to enabled:true.

Pure python3 + PyYAML::

    python3 platform/tests/test_gitea_migration.py
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
APP = os.path.join(REPO_ROOT, "apps", "platform", "gitea.yaml")
VALUES = os.path.join(REPO_ROOT, "platform", "base", "gitea", "values.yaml")


def _docs(path):
    with open(path, encoding="utf-8") as fh:
        return [d for d in yaml.safe_load_all(fh) if d]


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class ChartPinTest(unittest.TestCase):
    def setUp(self):
        app = _docs(APP)[0]
        self.src = next(s for s in app["spec"]["sources"] if s.get("chart") == "gitea")

    def test_chart_pinned_to_12_x_exact(self):
        rev = str(self.src["targetRevision"])
        self.assertRegex(rev, r"^12\.\d+\.\d+$",
                         "gitea chart must pin an exact 12.x SemVer, got %r" % rev)


class ValuesMigrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.values = _load(VALUES)

    def test_bundled_valkey_cluster_disabled(self):
        # valkey-cluster defaults to enabled:true in 12.x — must be turned off.
        self.assertIn("valkey-cluster", self.values,
                      "must explicitly disable the bundled valkey-cluster subchart")
        self.assertFalse(self.values["valkey-cluster"].get("enabled", True))

    def test_bundled_valkey_disabled(self):
        self.assertIn("valkey", self.values)
        self.assertFalse(self.values["valkey"].get("enabled", True))

    def test_legacy_redis_cluster_key_removed(self):
        # redis-cluster is not a valid key in 12.x; leaving it is misleading.
        self.assertNotIn("redis-cluster", self.values,
                         "the pre-12.x redis-cluster key must be removed")
        self.assertNotIn("redis", self.values)

    def test_bundled_postgres_disabled(self):
        self.assertFalse(self.values["postgresql"].get("enabled", True))
        self.assertFalse(self.values["postgresql-ha"].get("enabled", True))

    def test_in_process_session_cache_queue_retained(self):
        cfg = self.values["gitea"]["config"]
        self.assertEqual(cfg["session"]["PROVIDER"], "memory")
        self.assertEqual(cfg["cache"]["ADAPTER"], "memory")
        self.assertEqual(cfg["queue"]["TYPE"], "level")

    def test_shared_db_host_retained(self):
        db = self.values["gitea"]["config"]["database"]
        self.assertEqual(db["HOST"], "postgresql.platform-db.svc.cluster.local:5432")
        self.assertEqual(db["USER"], "gitea")
        self.assertEqual(db["NAME"], "gitea")

    def test_image_matches_chart_12_registry_layout_and_is_digest_pinned(self):
        # Chart 12 defaults to registry=docker.gitea.com and repository=gitea.
        # Carrying the old docker.io-style `gitea/gitea` override renders the
        # nonexistent docker.gitea.com/gitea/gitea:1.26.1 image.
        image = self.values["image"]
        self.assertEqual(image.get("registry"), "docker.gitea.com")
        self.assertEqual(image.get("repository"), "gitea")
        self.assertEqual(str(image.get("tag")), "1.26.1")
        self.assertEqual(
            image.get("digest"),
            "sha256:d8667667b4ccbd1f67b86a376bffcc0a17b16cf71309ed04e3918231776d47dd",
        )

    def test_chart_test_hook_does_not_reintroduce_busybox_latest(self):
        self.assertIn("test", self.values)
        self.assertFalse(
            self.values["test"].get("enabled", True),
            "disable the optional chart test hook unless its image is digest-pinned",
        )

    def test_rootless_pinned_false(self):
        # 12.x auto-transition can break SSH known_hosts; keep the prior behaviour.
        self.assertFalse(self.values["image"].get("rootless", True))

    def test_chart_12_probe_overrides_are_nested_under_gitea(self):
        self.assertNotIn("livenessProbe", self.values)
        self.assertNotIn("readinessProbe", self.values)
        self.assertEqual(self.values["gitea"]["livenessProbe"]["timeoutSeconds"], 5)
        self.assertEqual(self.values["gitea"]["readinessProbe"]["timeoutSeconds"], 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
