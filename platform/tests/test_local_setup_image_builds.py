#!/usr/bin/env python3
"""The bootstrap script must build+load the Tier-1 DigiOrg images (Issue #275).

Keycloak and Fluentd now run locally built, kind-loaded images with
``pullPolicy: Never`` (allow-listed in scripts/pin-policy-allowlist.yaml),
so — exactly like the OpenCost UI image — ``scripts/local-setup.nu`` must build
and load them during bootstrap, or their pods stay ``ErrImageNeverPull`` /
pending on a clean kind cluster. Missing Docker or a failed build must abort
bootstrap rather than falling back to an untrusted public image.

Pure python3 (text assertions on the Nushell script)::

    python3 platform/tests/test_local_setup_image_builds.py
"""

import os
import re
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SETUP = os.path.join(REPO_ROOT, "scripts", "local-setup.nu")


class BuildWiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(SETUP, encoding="utf-8") as fh:
            cls.text = fh.read()

    def test_invokes_keycloak_build(self):
        self.assertIn("platform/images/keycloak/build.nu", self.text,
                      "bootstrap must build the DigiOrg Keycloak image")

    def test_invokes_fluentd_build(self):
        self.assertIn("platform/images/fluentd/build.nu", self.text,
                      "bootstrap must build the DigiOrg Fluentd image")

    def test_opencost_ui_build_preserved(self):
        # The OpenCost /opencost fix must not regress.
        self.assertIn("platform/base/opencost/ui-image/build.nu", self.text)

    def test_builds_are_docker_guarded(self):
        # Fail with a clear bootstrap error before attempting builds when Docker
        # is unavailable; never allow kubelet fallback to a public namespace.
        self.assertGreaterEqual(
            len(re.findall(r"which docker", self.text)), 2,
            "Tier-1 image builds must guard on Docker availability",
        )

    def test_tier1_build_failures_are_fatal(self):
        start = self.text.index("def build_tier1_images")
        end = self.text.index("# -----------------------------------------------------------------------------\n# Phase 2", start)
        self.assertIn("error make", self.text[start:end])

    def test_prometheus_operator_upgrade_preinstalls_all_crds(self):
        expected = (
            "alertmanagerconfigs", "alertmanagers", "podmonitors", "probes",
            "prometheusagents", "prometheuses", "prometheusrules",
            "scrapeconfigs", "servicemonitors", "thanosrulers",
        )
        for crd in expected:
            self.assertIn(f"monitoring.coreos.com_{crd}.yaml", self.text)

    def test_tier1_builds_invoked_from_bootstrap(self):
        # The build step must actually be called from the bootstrap flow (which
        # `main up` runs), not just defined. Assert it appears in `main bootstrap`
        # alongside the preserved opencost build.
        m = re.search(r'def "main bootstrap" \[\][\s\S]*?(?=\ndef )', self.text)
        self.assertIsNotNone(m, "could not locate the `main bootstrap` function body")
        body = m.group(0)
        self.assertIn("build_opencost_ui_image", body,
                      "the opencost UI build must stay in bootstrap")
        self.assertRegex(
            body, r"build_(tier1_images|keycloak_image|fluentd_image|platform_images)",
            "the Tier-1 image build step must be invoked from `main up`",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
