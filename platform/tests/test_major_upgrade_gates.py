#!/usr/bin/env python3
"""Major platform upgrades must be promoted one at a time (Issue #275)."""
import os
import unittest
import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MAJOR_APPS = (
    "external-secrets",
    "nats",
    "grafana",
    "opencost",
    "gitea",
    "sonarqube",
    "crossplane",
    "crossplane-providers",
    "crossplane-provider-configs",
    "crossplane-xrds",
    "core-catalog",
)


class MajorUpgradeGateTest(unittest.TestCase):
    def test_major_upgrades_require_explicit_manual_sync(self):
        for name in MAJOR_APPS:
            with self.subTest(app=name):
                path = os.path.join(ROOT, "apps", "platform", f"{name}.yaml")
                with open(path, encoding="utf-8") as fh:
                    app = yaml.safe_load(fh)
                annotations = app["metadata"].get("annotations", {})
                self.assertEqual(
                    annotations.get("platform.digiorg.io/upgrade-gate"),
                    "issue-275-manual",
                )
                policy = app["spec"].get("syncPolicy", {})
                self.assertNotIn(
                    "automated", policy,
                    f"{name} is a major migration and must not auto-sync with the other upgrades",
                )
    def test_local_kind_bootstrap_explicitly_promotes_gates_in_order(self):
        path = os.path.join(ROOT, "scripts", "local-setup.nu")
        with open(path, encoding="utf-8") as fh:
            setup = fh.read()
        self.assertIn("sync_gated_apps_for_local_dev", setup)
        self.assertIn("patch application $app", setup)
        self.assertIn("status.operationState.finishedAt", setup)
        self.assertIn("status.operationState.phase", setup)
        self.assertIn("status.sync.status", setup)
        self.assertIn('phase in ["Failed" "Error"]', setup)
        self.assertIn("Synced+Healthy", setup)
        positions = [setup.index(f'\"{name}\"', setup.index("def gated_apps_for_local_dev")) for name in MAJOR_APPS]
        self.assertEqual(positions, sorted(positions))


if __name__ == "__main__":
    unittest.main(verbosity=2)
