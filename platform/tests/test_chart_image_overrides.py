#!/usr/bin/env python3
"""Guard chart value overrides that would otherwise render mutable images."""

import os
import re
import unittest
import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DIGEST_TAG = re.compile(r"^[^@]+@sha256:[0-9a-f]{64}$")


def load(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class OpenSearchImagePinsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.values = load("platform/base/opensearch/values.yaml")

    def test_main_image_and_volume_init_are_immutable(self):
        # The chart tries to semver-parse image.tag unless majorVersion is set;
        # digest-qualified tags therefore require this explicit major override.
        self.assertEqual(str(self.values["majorVersion"]), "3")
        self.assertRegex(self.values["image"]["tag"], DIGEST_TAG)
        self.assertEqual(self.values["persistence"]["image"], "busybox")
        self.assertRegex(self.values["persistence"]["imageTag"], DIGEST_TAG)


class NatsImagePinsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.values = load("platform/base/nats/values.yaml")

    def test_server_and_reloader_have_real_digests(self):
        server = self.values["container"]["image"]
        reloader = self.values["reloader"]["image"]
        self.assertEqual(
            server["fullImageName"],
            "nats:2.14.2-alpine@sha256:952d157e28d5394a211229bd57a7b37ff9f184e58e2c8486a08fa909fd254e32",
        )
        self.assertEqual(
            reloader["fullImageName"],
            "natsio/nats-server-config-reloader:0.23.0@sha256:64cb6c858e794906d3167378e02b7e0feb83c4e14c07b371eb8d921ef8c4a60b",
        )

    def test_chart_2_jetstream_storage_schema_is_used(self):
        js = self.values["config"]["jetstream"]
        self.assertNotIn("fileStorage", js)
        self.assertEqual(js["fileStore"]["dir"], "/data")
        self.assertTrue(js["fileStore"]["pvc"]["enabled"])
        self.assertEqual(js["fileStore"]["pvc"]["size"], "1Gi")

    def test_removed_service_port_schema_is_not_present(self):
        self.assertNotIn("service", self.values)


class KyvernoHookImagePinsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = load("apps/platform/kyverno.yaml")
        cls.values = yaml.safe_load(app["spec"]["source"]["helm"]["values"])

    def test_test_and_cleanup_hooks_do_not_use_latest(self):
        self.assertRegex(self.values["test"]["image"]["tag"], DIGEST_TAG)
        self.assertRegex(self.values["webhooksCleanup"]["image"]["tag"], DIGEST_TAG)

    def test_current_crd_migration_key_is_explicit(self):
        # Issue #279 supersedes #275's blanket "keep it explicit and on": a
        # clean-install cluster has no prior Kyverno CRD state to migrate, and
        # Argo CD's helm-template rendering runs this post-upgrade hook on
        # every sync (no install-vs-upgrade distinction), so it churned on a
        # fresh bootstrap. The key must still be explicit — just off by
        # default. See test_kyverno_migration.py and
        # docs/guides/platform-versions.md for the upgrade-path override.
        self.assertIs(self.values["crds"]["migration"]["enabled"], False)
        self.assertNotIn("replicaCount", self.values)
        self.assertNotIn("policyReportsCleanup", self.values)


if __name__ == "__main__":
    unittest.main(verbosity=2)
