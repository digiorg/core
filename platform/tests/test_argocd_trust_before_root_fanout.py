#!/usr/bin/env python3
"""Argo trust rollout must finish before root-app fans out.

Fresh bootstrap evidence on Core main 63468dee showed that applying the Argo
OIDC/TLS Helm override after root-app replaced the Argo control plane while its
full Application graph already existed.  The cold controller then produced a
system-wide comparison burst and transient internal DNS timeouts.  Bootstrap
must instead create the CA and finish the Argo rollout before root-app exists.
"""
import os
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SETUP = os.path.join(REPO_ROOT, "scripts", "local-setup.nu")


def function_body(text: str, name: str) -> str:
    start = text.index(f"def {name} ")
    end = text.find("\ndef ", start + 5)
    return text[start:] if end == -1 else text[start:end]


class ArgoTrustBeforeRootFanoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(SETUP, encoding="utf-8") as fh:
            cls.text = fh.read()
        cls.body = function_body(cls.text, "deploy_root_app")

    def test_cert_manager_and_ca_are_ready_before_argocd_trust_patch(self):
        cert_apply = self.body.index("apps/platform/cert-manager.yaml")
        dependency_wait = self.body.index('wait_for_configuration_dependencies "ArgoCD trust bootstrap"')
        ca_name = self.body.index('name: "digiorg-local-ca"')
        trust_patch = self.body.index("patch_argocd_oidc_ca")
        self.assertLess(cert_apply, dependency_wait)
        self.assertLess(dependency_wait, ca_name)
        self.assertLess(ca_name, trust_patch)

    def test_cert_manager_apply_is_bounded_scoped_and_fail_closed(self):
        apply_start = self.body.index("let cert_manager_apply")
        wait_start = self.body.index('wait_for_configuration_dependencies "ArgoCD trust bootstrap"')
        apply_block = self.body[apply_start:wait_start]
        self.assertIn(
            "kubectl --kubeconfig $KUBECONFIG_PATH --request-timeout=30s apply -f apps/platform/cert-manager.yaml",
            apply_block,
        )
        self.assertIn("| complete", apply_block)
        self.assertIn("if $cert_manager_apply.exit_code != 0", apply_block)
        self.assertIn("redact_sync_diagnostic", apply_block)
        self.assertIn("error make", apply_block)

    def test_trust_rollout_is_stable_before_core_data_and_root_app(self):
        first_stability = self.body.index("wait_for_repo_server_stable")
        trust_patch = self.body.index("patch_argocd_oidc_ca")
        second_stability = self.body.index("wait_for_repo_server_stable", first_stability + 1)
        core_data = self.body.index("deploy_core_data_layer")
        root_apply = self.body.index("root-app.yaml")
        gated_sync = self.body.index("sync_gated_apps_for_local_dev")
        self.assertLess(first_stability, trust_patch)
        self.assertLess(trust_patch, second_stability)
        self.assertLess(second_stability, core_data)
        self.assertLess(core_data, root_apply)
        self.assertLess(root_apply, gated_sync)

    def test_argocd_trust_patch_runs_exactly_once_in_deploy_root_app(self):
        self.assertEqual(self.body.count("patch_argocd_oidc_ca"), 1)
        main_up_start = self.text.index('def "main up" []')
        main_up_end = self.text.index('\ndef "main ', main_up_start + 10)
        main_up = self.text[main_up_start:main_up_end]
        self.assertNotIn("patch_argocd_oidc_ca", main_up)


if __name__ == "__main__":
    unittest.main(verbosity=2)
