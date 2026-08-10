#!/usr/bin/env python3
"""Resume/upgrade must promote bootstrap-managed ingress through the one allowed wrapper."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETUP = ROOT / "scripts/local-setup.nu"
TEXT = SETUP.read_text()


def function_body(name: str) -> str:
    match = re.search(rf"(?m)^def {re.escape(name)}\b", TEXT)
    if not match:
        raise AssertionError(f"missing function {name}")
    nxt = re.search(r"(?m)^def ", TEXT[match.end():])
    end = match.end() + nxt.start() if nxt else len(TEXT)
    return TEXT[match.start():end]


class BootstrapManagedIngressResumeTest(unittest.TestCase):
    def test_gated_wrapper_promotes_ingress_before_any_gated_application(self):
        body = function_body("sync_gated_apps_for_local_dev")
        call = "apply_bootstrap_managed_ingress_for_local_dev"
        self.assertIn(call, body)
        self.assertLess(body.index(call), body.index("wait_for_configuration_dependencies"))
        self.assertLess(body.index(call), body.index("for app in $gated_apps"))

    def test_ingress_apply_is_kustomize_based_kubeconfig_scoped_and_fail_closed(self):
        body = function_body("apply_bootstrap_managed_ingress_for_local_dev")
        self.assertIn("kubectl --kubeconfig $KUBECONFIG_PATH apply -k platform/base/ingress/", body)
        self.assertIn("| complete", body)
        self.assertIn("if $result.exit_code != 0", body)
        self.assertIn("error make", body)
        self.assertNotIn("--validate=false", body)
        self.assertNotIn("--insecure", body)

    def test_resume_path_does_not_restart_or_delete_workloads(self):
        body = function_body("apply_bootstrap_managed_ingress_for_local_dev")
        for forbidden in ("rollout restart", "delete pod", "delete deployment", "patch deployment"):
            self.assertNotIn(forbidden, body)


if __name__ == "__main__":
    unittest.main()
