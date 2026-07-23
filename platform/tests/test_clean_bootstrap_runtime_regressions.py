#!/usr/bin/env python3
"""Regression contracts discovered by Issue #285 clean bootstrap run #6."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import time
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SETUP = (ROOT / "scripts/local-setup.nu").read_text(encoding="utf-8")
NATS_VALUES = yaml.safe_load((ROOT / "platform/base/nats/values.yaml").read_text(encoding="utf-8"))
NACK_APP = yaml.safe_load((ROOT / "apps/platform/nats-jetstream-controller.yaml").read_text(encoding="utf-8"))
HARBOR_APP = yaml.safe_load((ROOT / "apps/platform/crossplane-harbor-bootstrap.yaml").read_text(encoding="utf-8"))


def func_body(name: str) -> str:
    start = SETUP.index(f"def {name} [")
    next_def = SETUP.find("\ndef ", start + 5)
    return SETUP[start:] if next_def < 0 else SETUP[start:next_def]


class NatsControllerAuthenticationTest(unittest.TestCase):
    SECRET = "nats-jetstream-controller-nkey"

    def test_nats_server_uses_dedicated_least_privilege_public_nkey(self):
        merge = NATS_VALUES["config"]["merge"]
        users = merge["accounts"]["APP"]["users"]
        controller = next(u for u in users if "nkey" in u)
        self.assertEqual(controller["nkey"], "<< $NATS_JSC_NKEY_PUBLIC >>")
        self.assertEqual(controller["permissions"]["publish"], ["$JS.API.>"])
        self.assertEqual(controller["permissions"]["subscribe"], ["_INBOX.>"])
        item = NATS_VALUES["container"]["env"]["NATS_JSC_NKEY_PUBLIC"]
        ref = item["valueFrom"]["secretKeyRef"]
        self.assertEqual(ref, {"name": self.SECRET, "key": "public.nk"})

    def test_nack_mounts_private_seed_from_matching_secret(self):
        values = yaml.safe_load(NACK_APP["spec"]["source"]["helm"]["values"])
        ref = values["jetstream"]["nats"]["nkey"]["secret"]
        self.assertEqual(ref, {"name": self.SECRET, "key": "seed.nk"})
        self.assertNotIn("password", NACK_APP["spec"]["source"]["helm"]["values"].lower())

    def test_nack_cluster_role_cannot_read_kubernetes_secrets(self):
        values = yaml.safe_load(NACK_APP["spec"]["source"]["helm"]["values"])
        rules = yaml.safe_load(values["rbacRules"])["rules"]
        secret_rules = [
            rule for rule in rules
            if rule.get("apiGroups") == [""] and "secrets" in rule.get("resources", [])
        ]
        self.assertEqual(secret_rules, [])
        jetstream_rules = [rule for rule in rules if rule.get("apiGroups") == ["jetstream.nats.io"]]
        self.assertEqual(len(jetstream_rules), 1)
        self.assertIn("streams", jetstream_rules[0]["resources"])
        self.assertIn("consumers", jetstream_rules[0]["resources"])

    def test_bootstrap_generates_and_validates_nkey_with_pinned_tool_image(self):
        body = func_body("ensure_nats_jetstream_controller_nkey")
        self.assertIn("natsio/nats-box:0.19.2@sha256:8031d190c7ee24081f3f27cc939fb647a1eeb29ebb5c60fef9b5b6c7a846d6a2", body)
        self.assertIn("nk -gen user", body)
        self.assertIn("nk -inkey /dev/stdin -pubout", body)
        self.assertIn('persist_opaque_secret "messaging" "nats-jetstream-controller-nkey" "seed.nk"', body)
        self.assertIn('persist_opaque_secret "messaging" "nats-jetstream-controller-nkey" "public.nk"', body)
        self.assertIn("ensure_nats_jetstream_controller_nkey", func_body("create_platform_namespaces_secrets"))
        self.assertIn("if ($public | is-empty)", body)
        self.assertIn('persist_opaque_secret "messaging" $name "public.nk" $derived', body)


class CrossplaneHarborOrderingTest(unittest.TestCase):
    def test_harbor_bootstrap_is_manual_and_gated_after_provider_configs(self):
        self.assertNotIn("automated", HARBOR_APP["spec"].get("syncPolicy", {}))
        gated = SETUP[SETUP.index("let gated_apps"):]
        self.assertLess(gated.index('"crossplane-provider-configs"'), gated.index('"crossplane-harbor-bootstrap"'))
        self.assertLess(gated.index('"crossplane-harbor-bootstrap"'), gated.index('"crossplane-xrds"'))

    def test_gate_fails_closed_on_provider_revision_and_request_crd(self):
        body = func_body("wait_for_provider_http_ready")
        self.assertIn("provider-http", body)
        self.assertIn("ProviderRevision", body)
        self.assertIn("Healthy", body)
        self.assertIn("requests.http.crossplane.io", body)
        self.assertIn("condition=Established", body)
        loop = func_body("sync_gated_apps_for_local_dev")
        self.assertLess(loop.index("wait_for_provider_http_ready"), loop.index("kubectl patch application $app"))

    def test_gate_fails_fast_and_redacts_deterministic_kubectl_failures(self):
        scenarios = {
            "forbidden": (1, "", "Forbidden transport-secret-sentinel"),
            "credential-helper": (1, "", "error: executable credential-helper not found transport-secret-sentinel"),
            "malformed": (0, "{not-json", ""),
        }
        for name, (code, stdout, stderr) in scenarios.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                fake = Path(tmp) / "kubectl"
                fake.write_text(
                    "#!/usr/bin/env python3\n"
                    "import sys\n"
                    f"sys.stdout.write({stdout!r})\n"
                    f"sys.stderr.write({stderr!r})\n"
                    f"sys.exit({code})\n",
                    encoding="utf-8",
                )
                fake.chmod(0o755)
                env = os.environ.copy()
                env["PATH"] = tmp + os.pathsep + env.get("PATH", "")
                started = time.monotonic()
                result = subprocess.run(
                    ["nu", "-c", f"source {ROOT / 'scripts/local-setup.nu'}; wait_for_provider_http_ready"],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=5,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertLess(time.monotonic() - started, 3)
                self.assertNotIn("transport-secret-sentinel", result.stdout + result.stderr)

        not_found = subprocess.run(
            [
                "nu", "-c",
                f"source {ROOT / 'scripts/local-setup.nu'}; "
                "kubectl_result_is_not_found {stdout: '', stderr: 'Error from server (NotFound): provider-http not found'}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(not_found.returncode, 0, not_found.stderr)
        self.assertEqual(not_found.stdout.strip(), "true")


class GiteaServiceIdentityTest(unittest.TestCase):
    def test_bool_flag_uses_equals_form(self):
        body = func_body("gitea_create_user_random_password")
        self.assertIn("--must-change-password=false", body)
        self.assertNotIn("--must-change-password false", body)

    def test_existing_service_users_have_must_change_unset_on_resume(self):
        helper = func_body("gitea_unset_service_user_must_change_password")
        self.assertIn("admin user must-change-password --unset", helper)
        for function, username in (
            ("configure_crossplane_gitea_credentials", "crossplane-provisioner"),
            ("configure_argocd_gitea_access", "argocd-reader"),
            ("configure_backstage_gitea_publisher", "backstage-appclaim-publisher"),
        ):
            body = func_body(function)
            self.assertIn(f'gitea_unset_service_user_must_change_password $gitea_pod "{username}"', body)


class SecretMetadataTest(unittest.TestCase):
    def test_new_credential_writers_avoid_client_side_apply_and_scrub_annotation(self):
        scrubber = func_body("scrub_secret_last_applied_annotation")
        self.assertIn("kubectl.kubernetes.io/last-applied-configuration-", scrubber)
        self.assertIn("jsonpath='{.metadata.annotations.kubectl", scrubber)

        opaque = func_body("persist_opaque_secret")
        self.assertIn("get secret", opaque)
        self.assertIn("--ignore-not-found", opaque)
        self.assertIn("data: {($key): $encoded}", opaque)
        self.assertIn("apply --server-side", opaque)
        self.assertIn("digiorg-bootstrap-secret-", opaque)
        self.assertIn("--field-manager $field_manager", opaque)
        self.assertNotRegex(opaque, r"(?<!server-side )apply -f -")
        self.assertGreaterEqual(opaque.count("scrub_secret_last_applied_annotation"), 2)
        self.assertLess(
            opaque.index("scrub_secret_last_applied_annotation"),
            opaque.index("apply --server-side"),
            "legacy last-applied annotation must be scrubbed before target-only SSA",
        )

        repo = func_body("persist_argocd_repo_secret")
        self.assertIn("apply --server-side", repo)
        self.assertIn("--field-manager", repo)
        self.assertNotRegex(repo, r"(?<!server-side )apply -f -")
        self.assertIn("scrub_secret_last_applied_annotation", repo)


if __name__ == "__main__":
    unittest.main(verbosity=2)
