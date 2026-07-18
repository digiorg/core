#!/usr/bin/env python3
"""A post-root-app stability boundary must exist before gated sync (#279).

Confirmed root cause: the root Application fans out into concurrent
Git/Helm/Kustomize renders immediately, and the very first gated sync started
while argocd-repo-server was still absorbing that burst (and, on the observed
runs, mid-restart). Promoting gated Applications only after repo-server is
Ready *and* has stopped restarting closes that race without masking a
genuinely broken repo-server (the wait itself is bounded and fails closed).

Pure python3, text/structural assertions on the Nushell script (no cluster)::

    python3 platform/tests/test_repo_server_stability_gate.py
"""
import os
import re
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SETUP = os.path.join(REPO_ROOT, "scripts", "local-setup.nu")


class RepoServerStabilityGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(SETUP, encoding="utf-8") as fh:
            cls.text = fh.read()

    def test_stability_wait_function_defined(self):
        self.assertRegex(self.text, r"def wait_for_repo_server_stable")

    def test_checks_readiness_and_restart_stability(self):
        start = self.text.index("def wait_for_repo_server_stable")
        end = self.text.index("\ndef ", start + 10)
        body = self.text[start:end]
        self.assertIn("argocd-repo-server", body)
        self.assertIn("restartCount", body)
        self.assertRegex(body, r"error make", "must fail closed if repo-server never stabilizes")

    def test_status_output_does_not_parse_label_as_a_subcommand(self):
        # In Nushell interpolation, `(restarts: ($restarts))` is parsed as an
        # attempted external command named `restarts:` at runtime even though
        # `nu --ide-check` accepts the file. Keep labels outside interpolation.
        start = self.text.index("def wait_for_repo_server_stable")
        end = self.text.index("\ndef ", start + 10)
        body = self.text[start:end]
        self.assertNotIn("(restarts:", body)

    def test_called_between_root_app_deploy_and_gated_sync(self):
        deploy_start = self.text.index("def deploy_root_app")
        deploy_end = self.text.index("\ndef ", deploy_start + 10)
        body = self.text[deploy_start:deploy_end]
        root_apply_pos = body.index("root-app.yaml")
        stability_pos = body.index("wait_for_repo_server_stable")
        gated_sync_pos = body.index("sync_gated_apps_for_local_dev")
        self.assertTrue(
            root_apply_pos < stability_pos < gated_sync_pos,
            "must apply root-app, THEN wait for repo-server stability, THEN start gated sync",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
