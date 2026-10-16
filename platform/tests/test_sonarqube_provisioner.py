"""Issue 372: dedicated account provisioning never broadens shared policy."""
import importlib.util
import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/sonarqube_provisioner.py"


class ProvisionerTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), "dedicated provisioning/bootstrap implementation missing")
        spec = importlib.util.spec_from_file_location("sonarqube_provisioner", SCRIPT)
        assert spec is not None and spec.loader is not None
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.calls = []
        self.user = None

    def api(self, path, data=None, token=None) -> dict[str, Any]:
        self.calls.append((path, data, token))
        if path.startswith("/api/users/search"):
            return {"paging": {"total": int(self.user is not None)}, "users": [self.user] if self.user else []}
        if path == "/api/users/create":
            self.user = {"login": "digiorg-provisioner", "local": True, "active": True}
            return self.user
        if path.startswith("/api/users/groups"):
            return {"paging": {"total": 1}, "groups": [{"name": "sonar-users", "selected": True, "default": True}]}
        if path.startswith("/api/permissions/users"):
            return {"paging": {"total": 1}, "users": [{"login": "digiorg-provisioner", "permissions": ["provisioning"]}]}
        if path.startswith("/api/permissions/groups"):
            return {"paging": {"total": 1}, "groups": [{"name": "sonar-users", "permissions": ["provisioning", "scan"]}]}
        if path.startswith("/api/permissions/search_templates"):
            return {"permissionTemplates": [{"id": "default", "name": "Default template"}], "defaultTemplates": [{"qualifier": "TRK", "templateId": "default"}]}
        if path.startswith("/api/permissions/template_groups"):
            return {"paging": {"total": 3}, "groups": [
                {"name": "sonar-users", "permissions": ["user", "codeviewer", "issueadmin", "securityhotspotadmin"]},
                {"name": "sonar-administrators", "permissions": ["admin"]}, {"name": "Anyone", "permissions": []}]}
        if path.startswith("/api/permissions/template_users"):
            return {"paging": {"total": 0}, "users": []}
        if path.startswith("/api/permissions/template_project_creators"):
            return {"permissions": []}
        if path == "/api/permissions/add_user":
            return {}
        if path == "/api/user_tokens/generate":
            return {"token": "synthetic-new-token"}
        if path == "/api/authentication/validate":
            return {"valid": True}
        if path == "/api/users/current":
            return {"login": "digiorg-provisioner"}
        raise AssertionError(path)

    def test_transport_rejects_plaintext_before_credentials(self):
        with self.assertRaises(self.module.PolicyError):
            self.module.Client("http://localhost/sonarqube", "unused", "synthetic")

    def test_bootstrap_calls_helper_with_stdin_and_existing_secret_persistence(self):
        source = (ROOT / "scripts/local-setup.nu").read_text()
        self.assertIn("configure_crossplane_sonarqube_credentials $password", source)
        self.assertIn('persist_opaque_secret "crossplane-system" "crossplane-sonarqube-credentials" "token"', source)
        self.assertIn('python3 scripts/sonarqube_provisioner.py', source)

    def test_unapproved_privileges_fail_without_permission_or_token_writes(self):
        self.user = {"login": "digiorg-provisioner", "local": True, "active": True}
        original = self.api
        for route, field in [("/api/permissions/users", "users"), ("/api/permissions/groups", "groups"), ("/api/permissions/template_groups", "groups")]:
            def api(path, data=None, token=None):
                result = original(path, data, token)
                if path.startswith(route):
                    result[field][0]["permissions"].append("admin")
                return result
            with self.subTest(route=route):
                self.calls.clear()
                with self.assertRaises(self.module.PolicyError):
                    self.module.prepare(api, "existing-synthetic")
                self.assertFalse(any(data is not None for _, data, _ in self.calls))

    def test_prepares_dedicated_identity_and_preserves_existing_token(self):
        token = self.module.prepare(self.api, "")
        self.assertEqual(token, "synthetic-new-token")
        self.assertEqual(sum(p == "/api/users/create" for p, _, _ in self.calls), 1)
        self.assertEqual(sum(p == "/api/user_tokens/generate" for p, _, _ in self.calls), 1)
        self.calls.clear()
        self.assertEqual(self.module.prepare(self.api, token), token)
        self.assertFalse(any(p in ("/api/users/create", "/api/user_tokens/generate") for p, _, _ in self.calls))
        self.assertFalse(any("remove" in p or "delete" in p or "apply_template" in p for p, _, _ in self.calls))


class BootstrapExecutionTests(unittest.TestCase):
    def test_real_nushell_secret_transport_and_failure_boundaries(self):
        source = (ROOT / "scripts/local-setup.nu").read_text()
        function = source.split("def configure_crossplane_sonarqube_credentials [", 1)[1].split("# Restart one existing OIDC-dependent", 1)[0]
        function = "def configure_crossplane_sonarqube_credentials [" + function
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            token = "fixture-existing-token"
            password = "fixture-admin-password"
            secret = json.dumps({"data": {"token": base64.b64encode(token.encode()).decode()}})
            ca = json.dumps({"data": {"ca.crt": base64.b64encode(b"fixture-public-ca").decode()}})
            kubectl = root / "kubectl"
            kubectl.write_text("#!/bin/sh\ncase \"$*\" in\n*crossplane-sonarqube-credentials*)\n case \"$MOCK_MODE\" in\n  forbidden) printf 'forbidden' >&2; exit 1;;\n  malformed) printf '{}';;\n  missing) printf 'notfound' >&2; exit 1;;\n  *) printf '%s' \"$FIXTURE_SECRET\";;\n esac;;\n*) printf '%s' \"$FIXTURE_CA\";;\nesac\n")
            helper = root / "python3"
            helper.write_text("#!/bin/sh\ncat > \"$CAPTURE\"\nif [ \"$MOCK_MODE\" = helperfail ]; then printf 'fixture-existing-token' >&2; exit 1; fi\nprintf 'fixture-issued-token'\n")
            kubectl.chmod(0o755)
            helper.chmod(0o755)
            capture, saved = root / "stdin.json", root / "persisted.json"
            test = root / "test.nu"
            test.write_text('let KUBECONFIG_PATH = "fixture-unused"\n' +
                'def kubectl_error_is_exact_not_found [stderr: string, resource: string, name: string] { $stderr == "notfound" }\n' +
                'def persist_opaque_secret [namespace: string, name: string, key: string, value: string] { {namespace: $namespace, name: $name, key: $key, value: $value} | to json | save --force ' + json.dumps(str(saved)) + ' }\n' +
                function + '\nconfigure_crossplane_sonarqube_credentials ' + json.dumps(password) + '\n')
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ["PATH"], FIXTURE_SECRET=secret, FIXTURE_CA=ca, CAPTURE=str(capture))
            for mode in ("present", "missing", "forbidden", "malformed", "helperfail"):
                with self.subTest(mode=mode):
                    capture.unlink(missing_ok=True)
                    saved.unlink(missing_ok=True)
                    result = subprocess.run(["nu", "--no-config-file", str(test)], env=dict(env, MOCK_MODE=mode), capture_output=True, text=True, timeout=30)
                    self.assertNotIn(token, result.stdout + result.stderr)
                    self.assertNotIn(password, result.stdout + result.stderr)
                    if mode in ("present", "missing"):
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertEqual(json.loads(capture.read_text())["existing_token"], token if mode == "present" else "")
                        self.assertEqual(json.loads(capture.read_text())["password"], password)
                        self.assertEqual(json.loads(saved.read_text()), {"namespace": "crossplane-system", "name": "crossplane-sonarqube-credentials", "key": "token", "value": "fixture-issued-token"})
                    else:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertFalse(saved.exists())
                        if mode != "helperfail":
                            self.assertFalse(capture.exists())


if __name__ == "__main__":
    unittest.main()
