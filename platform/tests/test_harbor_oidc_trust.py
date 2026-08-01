#!/usr/bin/env python3
"""Regression contracts for Harbor's public CA and OIDC configuration."""

from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
VALUES_PATH = ROOT / "platform/base/harbor/values.yaml"
HOOK_PATH = ROOT / "platform/base/harbor/harbor-oidc-config-job.yaml"
APP_PATH = ROOT / "apps/platform/harbor.yaml"
WORKFLOW_PATH = ROOT / ".github/workflows/platform-validation.yml"


class HarborCoreCaTrustRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        helm = shutil.which("helm")
        if helm is None:
            raise RuntimeError("helm is required to render the pinned Harbor chart")

        cls.values_text = VALUES_PATH.read_text(encoding="utf-8")
        cls.values = yaml.safe_load(cls.values_text)
        cls.hook_text = HOOK_PATH.read_text(encoding="utf-8")
        cls.hook = yaml.safe_load(cls.hook_text)
        app = yaml.safe_load(APP_PATH.read_text(encoding="utf-8"))
        source = app["spec"]["sources"][0]
        cls.assert_chart_pin(source)

        command = [
            helm,
            "template",
            "harbor",
            source["chart"],
            "--repo",
            source["repoURL"],
            "--version",
            str(source["targetRevision"]),
            "--namespace",
            "harbor",
            "-f",
            str(VALUES_PATH),
        ]
        rendered = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=180,
            check=False,
        )
        if rendered.returncode != 0:
            raise AssertionError(
                "pinned Harbor chart render failed\n"
                f"command: {command!r}\nstdout:\n{rendered.stdout}\nstderr:\n{rendered.stderr}"
            )
        cls.documents = [
            document
            for document in yaml.safe_load_all(rendered.stdout)
            if isinstance(document, dict)
        ]

    @staticmethod
    def assert_chart_pin(source):
        if source.get("chart") != "harbor":
            raise AssertionError("Harbor Argo source must render the harbor chart")
        if source.get("repoURL") != "https://helm.goharbor.io":
            raise AssertionError("Harbor chart repository changed unexpectedly")
        if str(source.get("targetRevision")) != "1.19.1":
            raise AssertionError("this regression must exercise Harbor chart 1.19.1")

    def core_pod_spec(self):
        cores = [
            document
            for document in self.documents
            if document.get("kind") == "Deployment"
            and document.get("metadata", {}).get("name") == "harbor-core"
        ]
        self.assertEqual(len(cores), 1, "render must contain exactly one harbor-core Deployment")
        return cores[0]["spec"]["template"]["spec"]

    def test_values_select_the_chart_native_public_ca_bundle(self):
        self.assertEqual(self.values.get("caBundleSecretName"), "digiorg-local-ca")

    def test_render_mounts_exactly_one_public_ca_secret_in_harbor_core(self):
        pod = self.core_pod_spec()
        matching_volumes = [
            volume
            for volume in pod.get("volumes", [])
            if volume.get("secret", {}).get("secretName") == "digiorg-local-ca"
        ]
        self.assertEqual(matching_volumes, [{
            "name": "ca-bundle-certs",
            "secret": {"secretName": "digiorg-local-ca"},
        }])

        core = next(container for container in pod["containers"] if container["name"] == "core")
        mounts = [
            mount for mount in core.get("volumeMounts", [])
            if mount.get("name") == "ca-bundle-certs"
        ]
        self.assertEqual(len(mounts), 1)
        self.assertEqual(mounts[0].get("mountPath"), "/harbor_cust_cert/custom-ca.crt")
        self.assertEqual(mounts[0].get("subPath"), "ca.crt")

    def test_hook_and_core_share_the_public_only_secret_key_contract(self):
        pod = self.hook["spec"]["template"]["spec"]
        container = pod["containers"][0]
        volume = next(volume for volume in pod["volumes"] if volume["name"] == "digiorg-local-ca")
        mount = next(mount for mount in container["volumeMounts"] if mount["name"] == volume["name"])

        self.assertEqual(volume["secret"]["secretName"], self.values.get("caBundleSecretName"))
        self.assertEqual(Path(mount["mountPath"]) / "ca.crt", Path("/etc/digiorg-ca/ca.crt"))
        self.assertIn('CA_CERT="/etc/digiorg-ca/ca.crt"', container["command"][2])

    def test_oidc_verification_stays_enabled_without_tls_bypasses(self):
        script = self.hook["spec"]["template"]["spec"]["containers"][0]["command"][2]
        normalized_script = script.replace(r'\"', '"')
        self.assertRegex(normalized_script, r'oidc_verify_cert(?:"|):\s*true')
        boundary = self.values_text + "\n" + self.hook_text
        self.assertNotRegex(boundary, r"(?i)insecureSkipVerify")
        self.assertNotRegex(boundary, r"(?m)\bcurl\b[^\n]*(?:\s-k(?:\s|$)|--insecure)")
        self.assertNotRegex(boundary, r"(?i)oidc_verify_cert[^\n]*false")

    def test_ci_installs_helm_before_material_render_regression(self):
        workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        steps = workflow["jobs"]["pin-policy-and-tests"]["steps"]
        names = [step.get("name", "") for step in steps]
        self.assertLess(
            names.index("Install Helm"),
            names.index("Platform regression tests"),
            "the fail-closed Harbor regression invokes Helm and CI must install it first",
        )


FAKE_CURL = r'''#!/usr/bin/env python3
import base64
import json
import os
from pathlib import Path
import re
import stat
import sys

state_path = Path(os.environ["FAKE_CURL_STATE"])
state = json.loads(state_path.read_text(encoding="utf-8"))
args = sys.argv[1:]
state.setdefault("argv", []).append(args)

configs = []
output = None
write_out = None
method = "GET"
data = None
data_bytes = None
url = None
i = 0
while i < len(args):
    arg = args[i]
    if arg in ("--config", "-K"):
        config_path = Path(args[i + 1])
        configs.append(config_path.read_text(encoding="utf-8"))
        state.setdefault("config_modes", []).append(stat.S_IMODE(config_path.stat().st_mode))
        state.setdefault("config_contents", []).append(configs[-1])
        i += 2
        continue
    if arg in ("--output", "-o"):
        output = args[i + 1]
        i += 2
        continue
    if arg in ("--write-out", "-w"):
        write_out = args[i + 1]
        i += 2
        continue
    if arg in ("--request", "-X"):
        method = args[i + 1]
        i += 2
        continue
    if arg in ("--data-binary", "-d", "--data"):
        value = args[i + 1]
        data_bytes = Path(value[1:]).read_bytes() if value.startswith("@") else value.encode("utf-8")
        data = data_bytes.decode("utf-8")
        if method == "GET":
            method = "POST"
        i += 2
        continue
    if arg.startswith("http://") or arg.startswith("https://"):
        url = arg
    i += 1

for config in configs:
    match = re.search(r'^output\s*=\s*"([^"]+)"', config, re.MULTILINE)
    if match:
        output = match.group(1)
    match = re.search(r'^write-out\s*=\s*"([^"]+)"', config, re.MULTILINE)
    if match:
        write_out = match.group(1)

scenario = state["scenario"]
status = 200
body = ""
transport_failure = False
config_text = "\n".join(configs)
keycloak_admin_request = bool(url and "/admin/realms/" in url)
harbor_configuration_request = bool(url and url.endswith("/api/v2.0/configurations"))
expected_bearer = "Authorization: Bearer " + state["expected_bearer"]
expected_basic = "Authorization: Basic " + base64.b64encode(
    ("admin:" + state["expected_admin"]).encode("utf-8")
).decode("ascii")

if keycloak_admin_request and expected_bearer not in config_text:
    status = 401
elif harbor_configuration_request and expected_basic not in config_text:
    status = 401
elif url and url.endswith("/api/v2.0/ping"):
    body = "Pong"
elif url and url.endswith("/protocol/openid-connect/token"):
    state["token_calls"] = state.get("token_calls", 0) + 1
    expected_form = (
        b"grant_type=password&client_id=admin-cli&username=admin&password=admin"
    )
    state["token_form_exact"] = data_bytes == expected_form
    state["token_form_has_terminal_control"] = bool(
        data_bytes and data_bytes.endswith((b"\n", b"\r", b"\x00"))
    )
    state["token_content_type_exact"] = bool(re.search(
        r'^header\s*=\s*"Content-Type: application/x-www-form-urlencoded"$',
        config_text,
        re.MULTILINE,
    ))
    if not state["token_form_exact"] or not state["token_content_type_exact"]:
        status = 401
        body = json.dumps({"error": "invalid_grant"})
    elif scenario == "auth_transport_failure":
        transport_failure = True
    elif scenario in ("auth_http_failure", "retry_exhaustion"):
        status = 503
    else:
        body = json.dumps({"access_token": "opaque-test-token"})
elif url and "clients?clientId=harbor" in url:
    state["bearer_auth_validated"] = True
    state["list_calls"] = state.get("list_calls", 0) + 1
    if scenario == "list_http_failure":
        status = 503
    elif scenario == "missing_client":
        body = "[]"
    elif scenario == "ambiguous_client":
        body = json.dumps([
            {"id": "client-one", "clientId": "harbor"},
            {"id": "client-two", "clientId": "harbor"},
        ])
    else:
        body = json.dumps([{"id": "harbor-uuid", "clientId": "harbor"}])
elif url and url.endswith("/clients/harbor-uuid/client-secret"):
    state["secret_reads"] = state.get("secret_reads", 0) + 1
    if scenario == "readback_http_failure" and state["secret_reads"] > 1:
        status = 503
    else:
        value = state["current_secret"]
        if scenario == "readback_mismatch" and state["secret_reads"] > 1:
            value = "server-refused-sentinel"
        body = json.dumps({"type": "secret", "value": value})
elif url and url.endswith("/clients/harbor-uuid") and method == "GET":
    body = json.dumps({
        "id": "harbor-uuid", "clientId": "harbor", "enabled": True,
        "secret": state["current_secret"],
    })
elif url and url.endswith("/clients/harbor-uuid") and method == "PUT":
    state["update_calls"] = state.get("update_calls", 0) + 1
    if scenario == "update_http_failure":
        status = 503
    else:
        requested = json.loads(data)["secret"]
        if scenario != "readback_mismatch":
            state["current_secret"] = requested
        status = 204
elif url and url.endswith("/api/v2.0/configurations"):
    state["basic_auth_validated"] = True
    state["harbor_calls"] = state.get("harbor_calls", 0) + 1
    state["harbor_body"] = json.loads(data)
    status = 200
else:
    status = 404

state_path.write_text(json.dumps(state), encoding="utf-8")
if output and output != "/dev/null":
    Path(output).write_text(body, encoding="utf-8")
if transport_failure:
    sys.exit(7)
if write_out:
    sys.stdout.write(str(status))
elif body and output is None:
    sys.stdout.write(body)
'''


class HarborOidcHookBehaviourTest(unittest.TestCase):
    DESIRED = "desired-client-secret-SENTINEL"
    OLD = "old-client-secret-SENTINEL"
    ADMIN = "admin-password-SENTINEL"

    def setUp(self):
        self.doc = yaml.safe_load(HOOK_PATH.read_text(encoding="utf-8"))
        self.pod = self.doc["spec"]["template"]["spec"]
        self.container = self.pod["containers"][0]
        self.script = self.container["command"][2]

    def run_hook(self, scenario, current_secret=None, desired_secret=None):
        desired_secret = self.DESIRED if desired_secret is None else desired_secret
        with tempfile.TemporaryDirectory(prefix="issue289 oidc harness ") as temp:
            root = Path(temp)
            bin_dir = root / "fake bin"
            workspace = root / "memory workspace"
            oidc_dir = root / "oidc secret"
            admin_dir = root / "admin secret"
            ca_dir = root / "public ca"
            for directory in (bin_dir, workspace, oidc_dir, admin_dir, ca_dir):
                directory.mkdir()

            curl_path = bin_dir / "curl"
            curl_path.write_text(FAKE_CURL, encoding="utf-8")
            curl_path.chmod(0o700)
            (oidc_dir / "OIDC_CLIENT_SECRET").write_text(desired_secret, encoding="utf-8")
            (admin_dir / "HARBOR_ADMIN_PASSWORD").write_text(self.ADMIN, encoding="utf-8")
            (ca_dir / "ca.crt").write_text("public test CA", encoding="utf-8")

            state_path = root / "state.json"
            state_path.write_text(json.dumps({
                "scenario": scenario,
                "current_secret": self.DESIRED if current_secret is None else current_secret,
                "expected_bearer": "opaque-test-token",
                "expected_admin": self.ADMIN,
                "token_calls": 0,
                "list_calls": 0,
                "secret_reads": 0,
                "update_calls": 0,
                "harbor_calls": 0,
                "argv": [],
            }), encoding="utf-8")

            # The production defaults are container paths. Repoint only those
            # filesystem locations so the exact shipped script can run as an
            # unprivileged host process; no command or control flow is changed.
            script = self.script.replace(
                "/etc/digiorg-ca/ca.crt", str(ca_dir / "ca.crt")
            )
            env = os.environ.copy()
            env.update({
                "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
                "FAKE_CURL_STATE": str(state_path),
                "WORKSPACE": str(workspace),
                "OIDC_SECRET_FILE": str(oidc_dir / "OIDC_CLIENT_SECRET"),
                "HARBOR_ADMIN_FILE": str(admin_dir / "HARBOR_ADMIN_PASSWORD"),
                "RETRY_DELAY_SECONDS": "0",
                # These expose the pre-fix env transport if it still exists.
                "OIDC_CLIENT_SECRET": self.DESIRED,
                "HARBOR_ADMIN_PASSWORD": self.ADMIN,
            })
            result = subprocess.run(
                ["/bin/sh", "-c", script],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            state = json.loads(state_path.read_text(encoding="utf-8"))

        leaked_command = "\n".join(" ".join(argv) for argv in state["argv"])
        for sentinel in (self.DESIRED, self.OLD, self.ADMIN):
            self.assertNotIn(sentinel, result.stdout)
            self.assertNotIn(sentinel, result.stderr)
            self.assertNotIn(sentinel, leaked_command)
        return result, state

    def test_job_uses_file_secrets_and_a_memory_backed_workspace(self):
        self.assertEqual(
            self.container["image"],
            "natsio/nats-box:0.19.2@sha256:8031d190c7ee24081f3f27cc939fb647a1eeb29ebb5c60fef9b5b6c7a846d6a2",
        )
        self.assertNotIn("env", self.container)
        mounts = {mount["name"]: mount for mount in self.container["volumeMounts"]}
        self.assertEqual(mounts["harbor-oidc-secret"]["mountPath"], "/run/secrets/harbor-oidc")
        self.assertEqual(mounts["harbor-admin-secret"]["mountPath"], "/run/secrets/harbor-admin")
        volumes = {volume["name"]: volume for volume in self.pod["volumes"]}
        self.assertEqual(volumes["harbor-oidc-secret"]["secret"]["secretName"], "harbor-oidc-secret")
        self.assertEqual(volumes["harbor-admin-secret"]["secret"]["secretName"], "harbor-admin-secret")
        self.assertEqual(volumes["workspace"]["emptyDir"]["medium"], "Memory")
        self.assertTrue(self.pod["securityContext"]["runAsNonRoot"])
        self.assertTrue(self.container["securityContext"]["readOnlyRootFilesystem"])
        self.assertNotRegex(self.script, r"(?:^|\s)-u(?:\s|$)")
        self.assertNotIn("--insecure", self.script)
        self.assertEqual(self.script.count('--rawfile secret "${OIDC_SECRET_FILE}"'), 2)
        self.assertNotIn("--arg password", self.script)
        self.assertIn('--data-binary "@${data_file}"', self.script)
        self.assertNotRegex(self.script, r"(?m)^\s+(?:-d|--data)\s")

    def test_job_and_every_http_request_are_time_bounded(self):
        self.assertEqual(self.doc["spec"].get("activeDeadlineSeconds"), 300)
        result, state = self.run_hook("equal", self.DESIRED)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(state.get("config_contents"))
        for config in state["config_contents"]:
            self.assertRegex(config, r"(?m)^connect-timeout\s*=\s*5$")
            self.assertRegex(config, r"(?m)^max-time\s*=\s*15$")

    def test_generated_auth_configs_use_the_values_read_from_files(self):
        result, state = self.run_hook("equal", self.DESIRED)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIs(state.get("bearer_auth_validated"), True)
        self.assertIs(state.get("basic_auth_validated"), True)

    def test_keycloak_token_form_is_byte_exact_without_terminal_control_bytes(self):
        result, state = self.run_hook("equal", self.DESIRED)
        self.assertGreater(state["token_calls"], 0)
        self.assertIs(state.get("token_content_type_exact"), True)
        self.assertIs(state.get("token_form_has_terminal_control"), False)
        self.assertIs(state.get("token_form_exact"), True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def assert_success(self, scenario, current_secret):
        result, state = self.run_hook(scenario, current_secret)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["current_secret"], self.DESIRED)
        self.assertEqual(state["harbor_calls"], 1)
        self.assertEqual(state["harbor_body"]["oidc_client_secret"], self.DESIRED)
        self.assertIs(state["harbor_body"]["oidc_verify_cert"], True)
        self.assertTrue(state["config_modes"])
        self.assertEqual(set(state["config_modes"]), {0o600})
        return state

    def test_clean_default_already_equal_does_not_update(self):
        state = self.assert_success("equal", self.DESIRED)
        self.assertEqual(state["update_calls"], 0)
        self.assertEqual(state["secret_reads"], 1)

    def test_explicit_override_updates_once_then_proves_equality(self):
        state = self.assert_success("override", self.OLD)
        self.assertEqual(state["update_calls"], 1)
        self.assertEqual(state["secret_reads"], 2)

    def test_resume_already_equal_does_not_update(self):
        state = self.assert_success("resume", self.DESIRED)
        self.assertEqual(state["update_calls"], 0)

    def test_post_update_readback_mismatch_fails_closed(self):
        result, state = self.run_hook("readback_mismatch", self.OLD)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state["update_calls"], 1)
        self.assertEqual(state["harbor_calls"], 0)

    def test_empty_desired_secret_fails_before_any_http_request(self):
        result, state = self.run_hook("equal", self.OLD, desired_secret="")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state["argv"], [])
        self.assertEqual(state["harbor_calls"], 0)

    def test_missing_or_ambiguous_client_fails_closed(self):
        for scenario in ("missing_client", "ambiguous_client"):
            with self.subTest(scenario=scenario):
                result, state = self.run_hook(scenario, self.OLD)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(state["update_calls"], 0)
                self.assertEqual(state["harbor_calls"], 0)

    def test_keycloak_http_and_transport_failures_fail_closed(self):
        for scenario in (
            "auth_transport_failure", "auth_http_failure", "list_http_failure",
            "update_http_failure", "readback_http_failure",
        ):
            with self.subTest(scenario=scenario):
                result, state = self.run_hook(scenario, self.OLD)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(state["harbor_calls"], 0)

    def test_http_retry_exhaustion_is_bounded(self):
        result, state = self.run_hook("retry_exhaustion", self.OLD)
        self.assertNotEqual(result.returncode, 0)
        self.assertGreater(state["token_calls"], 0)
        self.assertLessEqual(state["token_calls"], 5)
        self.assertEqual(state["harbor_calls"], 0)


if __name__ == "__main__":
    unittest.main()
