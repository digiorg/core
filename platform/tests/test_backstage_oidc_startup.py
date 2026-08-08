#!/usr/bin/env python3
"""Executable Backstage OIDC discovery startup contract (Issue #298).

The Backstage process must not start until Keycloak serves a valid discovery
document for the exact public issuer.  These tests parse the real Deployment
and execute the init container's embedded shell with deterministic fake tools;
they do not require a cluster or network access.

Run directly with Python 3 + PyYAML::

    python3 platform/tests/test_backstage_oidc_startup.py
"""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENT_PATH = ROOT / "platform/base/backstage/deployment.yaml"
LOCAL_SETUP_PATH = ROOT / "scripts/local-setup.nu"
WAIT_NAME = "wait-for-oidc-discovery"
TOOL_IMAGE = (
    "natsio/nats-box:0.19.2@sha256:"
    "8031d190c7ee24081f3f27cc939fb647a1eeb29ebb5c60fef9b5b6c7a846d6a2"
)
DISCOVERY_URL = (
    "https://digiorg.local/keycloak/realms/digiorg-core-platform/"
    ".well-known/openid-configuration"
)
EXPECTED_ISSUER = "https://digiorg.local/keycloak/realms/digiorg-core-platform"
CA_PATH = "/etc/digiorg/ca/ca.crt"
EXPECTED_CURL_ARGV = [
    "--disable",
    "--fail",
    "--silent",
    "--show-error",
    "--connect-timeout",
    "3",
    "--max-time",
    "5",
    "--cacert",
    CA_PATH,
    DISCOVERY_URL,
]
RESPONSE_MARKER = "fixture-response-body-marker"
TOOL_DIAGNOSTIC_MARKER = "fixture-tool-diagnostic-marker"


FAKE_CURL = f"#!{sys.executable}\n" + r'''
import json
import os
from pathlib import Path
import sys

state_path = Path(os.environ["FAKE_CURL_STATE"])
state = json.loads(state_path.read_text(encoding="utf-8"))
state.setdefault("argv", []).append(sys.argv[1:])
index = state.get("calls", 0)
state["calls"] = index + 1
responses = state["responses"]
response = responses[min(index, len(responses) - 1)]
state_path.write_text(json.dumps(state), encoding="utf-8")

kind = response["kind"]
if kind == "transport":
    sys.stderr.write("fixture-tool-diagnostic-marker: transport\n")
    raise SystemExit(7)
if kind == "http_502":
    sys.stderr.write("fixture-tool-diagnostic-marker: HTTP 502\n")
    raise SystemExit(22)
if kind != "body":
    raise SystemExit(90)
sys.stdout.write(response.get("body", ""))
'''


FAKE_JQ = f"#!{sys.executable}\n" + r'''
import json
import os
from pathlib import Path
import re
import sys

EXPECTED = "https://digiorg.local/keycloak/realms/digiorg-core-platform"
args = sys.argv[1:]
log_path = Path(os.environ["FAKE_JQ_LOG"])
with log_path.open("a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\n")

exit_status = "-e" in args or "--exit-status" in args
filter_text = args[-1] if args else ""
object_check = re.search(r'type\s*==\s*"object"', filter_text) is not None

variables = {}
index = 0
while index < len(args):
    if args[index] == "--arg" and index + 2 < len(args):
        variables[args[index + 1]] = args[index + 2]
        index += 3
    else:
        index += 1

issuer_check = False
for name, value in variables.items():
    if value == EXPECTED and re.search(
        r"\.issuer\s*==\s*\$" + re.escape(name) + r"\b", filter_text
    ):
        issuer_check = True
if EXPECTED in filter_text and re.search(r"\.issuer\s*==", filter_text):
    issuer_check = True
if not (exit_status and object_check and issuer_check):
    raise SystemExit(3)

raw = sys.stdin.read()
try:
    document = json.loads(raw)
except (TypeError, ValueError):
    sys.stderr.write("fixture-tool-diagnostic-marker: malformed JSON\n")
    raise SystemExit(4)

raise SystemExit(0 if isinstance(document, dict) and document.get("issuer") == EXPECTED else 1)
'''


FAKE_SLEEP = r'''#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_SLEEP_LOG"
'''


def _deployment():
    documents = [
        document
        for document in yaml.safe_load_all(DEPLOYMENT_PATH.read_text(encoding="utf-8"))
        if isinstance(document, dict)
    ]
    matches = [
        document
        for document in documents
        if document.get("kind") == "Deployment"
        and document.get("metadata", {}).get("name") == "backstage"
    ]
    if len(matches) != 1:
        raise AssertionError("expected exactly one Backstage Deployment")
    return matches[0]


class BackstageOidcManifestContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.deployment = _deployment()
        cls.pod = cls.deployment["spec"]["template"]["spec"]

    def wait_container(self):
        matches = [
            container
            for container in self.pod.get("initContainers", [])
            if container.get("name") == WAIT_NAME
        ]
        self.assertEqual(
            len(matches),
            1,
            f"Backstage requires exactly one {WAIT_NAME} init container",
        )
        return matches[0]

    def main_container(self):
        matches = [
            container
            for container in self.pod.get("containers", [])
            if container.get("name") == "backstage"
        ]
        self.assertEqual(len(matches), 1, "expected exactly one Backstage container")
        return matches[0]

    def test_init_order_is_postgres_then_oidc_before_backstage(self):
        self.assertEqual(
            [container.get("name") for container in self.pod.get("initContainers", [])],
            ["wait-for-postgres", WAIT_NAME],
        )
        self.assertTrue(self.pod.get("containers"))
        self.assertEqual(self.pod["containers"][0].get("name"), "backstage")

    def test_wait_container_uses_the_existing_digest_pinned_tool_image(self):
        self.assertEqual(self.wait_container().get("image"), TOOL_IMAGE)

    def test_wait_container_is_fully_hardened(self):
        security = self.wait_container().get("securityContext")
        self.assertEqual(
            security,
            {
                "runAsNonRoot": True,
                "runAsUser": 65534,
                "runAsGroup": 65534,
                "readOnlyRootFilesystem": True,
                "allowPrivilegeEscalation": False,
                "capabilities": {"drop": ["ALL"]},
                "seccompProfile": {"type": "RuntimeDefault"},
            },
        )

    def test_wait_container_has_no_credentials_or_implicit_service_account_token(self):
        container = self.wait_container()
        self.assertNotIn("env", container)
        self.assertNotIn("envFrom", container)
        self.assertIs(self.pod.get("automountServiceAccountToken"), False)
        mounts = container.get("volumeMounts", [])
        self.assertEqual(
            mounts,
            [{
                "name": "digiorg-local-ca",
                "mountPath": "/etc/digiorg/ca",
                "readOnly": True,
            }],
        )
        for mount in mounts:
            self.assertNotRegex(mount.get("name", ""), r"(?i)token|credential")

    def test_main_container_requires_explicit_ingestor_token_when_automount_is_off(self):
        self.assertIs(self.pod.get("automountServiceAccountToken"), False)
        matches = [
            variable
            for variable in self.main_container().get("env", [])
            if variable.get("name") == "KUBERNETES_SERVICE_ACCOUNT_TOKEN"
        ]
        self.assertEqual(len(matches), 1)
        self.assertEqual(
            matches[0].get("valueFrom"),
            {
                "secretKeyRef": {
                    "name": "backstage-k8s-token",
                    "key": "token",
                    "optional": False,
                },
            },
        )

    def test_public_ca_volume_projects_only_ca_crt_read_only(self):
        volumes = [
            volume
            for volume in self.pod.get("volumes", [])
            if volume.get("name") == "digiorg-local-ca"
        ]
        self.assertEqual(len(volumes), 1)
        self.assertEqual(
            volumes[0].get("secret"),
            {
                "secretName": "digiorg-local-ca",
                "items": [{"key": "ca.crt", "path": "ca.crt", "mode": 0o444}],
            },
        )

    def test_script_has_exact_endpoint_and_no_tls_or_credential_bypasses(self):
        container = self.wait_container()
        command = container.get("command", [])
        self.assertEqual(command[:2], ["/bin/sh", "-c"])
        self.assertEqual(len(command), 3)
        script = command[2]
        self.assertIn(DISCOVERY_URL, script)
        self.assertIn(EXPECTED_ISSUER, script)
        self.assertIn(CA_PATH, script)
        self.assertNotRegex(script, r"(?i)http://")
        self.assertNotRegex(script, r"(?:^|\s)-k(?:\s|$)")
        self.assertNotIn("--insecure", script)
        self.assertNotRegex(script, r"(?i)NODE_TLS_REJECT_UNAUTHORIZED")
        self.assertNotRegex(script, r"(?i)insecureSkipVerify")
        self.assertNotRegex(script, r"(?i)(?:token|secret).*(?:env|mount)|(?:env|mount).*(?:token|secret)")

    def test_regular_init_failure_is_restarted_and_deadline_covers_retry_budget(self):
        # A regular init container is restartable under the Pod's Always policy:
        # after one finite failed invocation, kubelet retries it with backoff on
        # the same Pod, so OIDC can recover without deleting that Pod.
        self.assertIn(self.pod.get("restartPolicy", "Always"), ("Always",))

        script = self.wait_container()["command"][2]
        attempts = re.search(r"(?m)^\s*MAX_ATTEMPTS=(\d+)\s*$", script)
        retry_delay = re.search(r"(?m)^\s*RETRY_DELAY_SECONDS=(\d+)\s*$", script)
        max_time = re.search(r"(?:^|\s)--max-time\s+(\d+)(?:\s|$)", script)
        self.assertIsNotNone(attempts)
        self.assertIsNotNone(retry_delay)
        self.assertIsNotNone(max_time)
        self.assertEqual(int(attempts.group(1)), 60)
        self.assertEqual(int(retry_delay.group(1)), 5)
        self.assertEqual(int(max_time.group(1)), 5)

        worst_case_seconds = (
            int(attempts.group(1)) * int(max_time.group(1))
            + (int(attempts.group(1)) - 1) * int(retry_delay.group(1))
        )
        self.assertEqual(worst_case_seconds, 595)
        deadline = self.deployment["spec"].get("progressDeadlineSeconds")
        self.assertEqual(deadline, 900)
        self.assertGreater(deadline, worst_case_seconds)
        self.assertGreaterEqual(deadline - worst_case_seconds, 120)


class BackstageTokenBootstrapContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = LOCAL_SETUP_PATH.read_text(encoding="utf-8")

    def test_local_setup_produces_and_boundedly_waits_for_ingestor_token(self):
        producer_start = self.source.index(
            "# Backstage kubernetes-ingestor: create SA first, then token secret"
        )
        producer_end = self.source.index("# Monitoring namespace", producer_start)
        producer = self.source[producer_start:producer_end]

        manifest = re.search(
            r'"apiVersion: v1\s+kind: Secret\s+metadata:\s+'
            r'name: backstage-k8s-token\s+namespace: backstage\s+'
            r'annotations:\s+kubernetes\.io/service-account\.name: backstage\s+'
            r'type: kubernetes\.io/service-account-token"',
            producer,
        )
        self.assertIsNotNone(manifest)
        apply_marker = "kubectl apply -f $token_secret_file"
        wait_marker = (
            "kubectl get secret backstage-k8s-token -n backstage "
            "-o jsonpath='{.data.token}'"
        )
        self.assertEqual(producer.count(apply_marker), 1)
        self.assertEqual(producer.count(wait_marker), 1)
        self.assertLess(producer.index(apply_marker), producer.index(wait_marker))
        self.assertRegex(producer, r"for _ in 1\.\.30\s*\{")
        self.assertIn("sleep 1sec", producer)

    def test_token_is_applied_before_backstage_application_and_rollout_gates(self):
        apply_position = self.source.index("kubectl apply -f $token_secret_file")
        root_app_position = self.source.index("def deploy_root_app []")
        application_gate_position = self.source.index("def wait_for_argocd_apps []")
        rollout_gate_position = self.source.index("def restart_oidc_dependent_pods []")
        self.assertLess(apply_position, root_app_position)
        self.assertLess(apply_position, application_gate_position)
        self.assertLess(apply_position, rollout_gate_position)

        main_up = self.source[
            self.source.index('def "main up" []'):
            self.source.index('def "main bootstrap" []')
        ]
        self.assertLess(main_up.index("main bootstrap"), main_up.index("deploy_root_app"))

        bootstrap = self.source[
            self.source.index('def "main bootstrap" []'):
            self.source.index('def "main down" []')
        ]
        self.assertLess(
            bootstrap.index("create_platform_namespaces_secrets"),
            bootstrap.index("install_argocd"),
        )


class BackstageOidcScriptBehaviourTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.deployment = _deployment()
        cls.pod = cls.deployment["spec"]["template"]["spec"]

    def setUp(self):
        matches = [
            container
            for container in self.pod.get("initContainers", [])
            if container.get("name") == WAIT_NAME
        ]
        self.assertEqual(
            len(matches),
            1,
            f"cannot execute missing {WAIT_NAME} init container",
        )
        command = matches[0].get("command", [])
        self.assertEqual(command[:2], ["/bin/sh", "-c"])
        self.assertEqual(len(command), 3)
        self.script = command[2]

    @staticmethod
    def body(value):
        return {"kind": "body", "body": value}

    def run_script(self, responses):
        with tempfile.TemporaryDirectory(prefix="issue298 oidc startup ") as temp:
            temp_root = Path(temp)
            fake_bin = temp_root / "fake-bin"
            fake_bin.mkdir()

            for name, source in (
                ("curl", FAKE_CURL),
                ("jq", FAKE_JQ),
                ("sleep", FAKE_SLEEP),
            ):
                path = fake_bin / name
                path.write_text(source, encoding="utf-8")
                path.chmod(0o700)

            curl_state = temp_root / "curl-state.json"
            curl_state.write_text(
                json.dumps({"responses": responses, "calls": 0, "argv": []}),
                encoding="utf-8",
            )
            jq_log = temp_root / "jq-argv.jsonl"
            sleep_log = temp_root / "sleep-argv.txt"
            env = os.environ.copy()
            env.update({
                "PATH": str(fake_bin) + os.pathsep + os.defpath,
                "FAKE_CURL_STATE": str(curl_state),
                "FAKE_JQ_LOG": str(jq_log),
                "FAKE_SLEEP_LOG": str(sleep_log),
            })
            result = subprocess.run(
                ["/bin/sh", "-c", self.script],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            state = json.loads(curl_state.read_text(encoding="utf-8"))
            jq_calls = []
            if jq_log.exists():
                jq_calls = [json.loads(line) for line in jq_log.read_text(encoding="utf-8").splitlines()]
            sleeps = sleep_log.read_text(encoding="utf-8").splitlines() if sleep_log.exists() else []

        for argv in state["argv"]:
            self.assertEqual(argv, EXPECTED_CURL_ARGV)
        combined_output = result.stdout + result.stderr
        self.assertNotIn(RESPONSE_MARKER, combined_output)
        self.assertNotIn(TOOL_DIAGNOSTIC_MARKER, combined_output)
        return result, state["argv"], jq_calls, sleeps

    def assert_jq_contract(self, calls):
        self.assertTrue(calls, "every HTTP response must be validated by jq")
        for argv in calls:
            self.assertTrue("-e" in argv or "--exit-status" in argv, argv)
            filter_text = argv[-1]
            self.assertRegex(filter_text, r'type\s*==\s*"object"')
            self.assertRegex(filter_text, r"\.issuer\s*==")
            variables = {}
            for index, arg in enumerate(argv[:-2]):
                if arg == "--arg":
                    variables[argv[index + 1]] = argv[index + 2]
            issuer_is_exact = EXPECTED_ISSUER in filter_text or any(
                value == EXPECTED_ISSUER and f"${name}" in filter_text
                for name, value in variables.items()
            )
            self.assertTrue(issuer_is_exact, argv)

    def test_transport_and_http_failures_are_bounded_to_sixty_attempts(self):
        outputs = []
        for kind in ("transport", "http_502"):
            with self.subTest(kind=kind):
                result, curl_calls, jq_calls, sleeps = self.run_script([{"kind": kind}])
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(curl_calls), 60)
                self.assertEqual(jq_calls, [])
                self.assertEqual(sleeps, ["5"] * 59)
                outputs.append((result.stdout, result.stderr))
        self.assertEqual(outputs[0], outputs[1], "diagnostics must not expose variable tool failures")

    def test_invalid_documents_never_report_success_and_use_fixed_diagnostics(self):
        invalid_documents = {
            "malformed": RESPONSE_MARKER + " {",
            "empty": "",
            "missing issuer": json.dumps({"status": RESPONSE_MARKER}),
            "wrong issuer": json.dumps({
                "issuer": "https://wrong.invalid/realm",
                "status": RESPONSE_MARKER,
            }),
        }
        outputs = []
        for label, document in invalid_documents.items():
            with self.subTest(label=label):
                result, curl_calls, jq_calls, sleeps = self.run_script([self.body(document)])
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(curl_calls), 60)
                self.assertEqual(len(jq_calls), 60)
                self.assertEqual(sleeps, ["5"] * 59)
                self.assert_jq_contract(jq_calls)
                outputs.append((result.stdout, result.stderr))
        self.assertTrue(all(output == outputs[0] for output in outputs[1:]))

    def test_wrong_issuer_then_exact_issuer_retries_and_succeeds(self):
        valid = json.dumps({"issuer": EXPECTED_ISSUER, "status": RESPONSE_MARKER})
        wrong = json.dumps({"issuer": EXPECTED_ISSUER + "-wrong", "status": RESPONSE_MARKER})
        result, curl_calls, jq_calls, sleeps = self.run_script([
            self.body(wrong),
            self.body(valid),
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(curl_calls), 2)
        self.assertEqual(len(jq_calls), 2)
        self.assertEqual(sleeps, ["5"])
        self.assert_jq_contract(jq_calls)

    def test_transport_failures_then_valid_document_recovers_in_one_run(self):
        valid = json.dumps({"issuer": EXPECTED_ISSUER, "status": RESPONSE_MARKER})
        result, curl_calls, jq_calls, sleeps = self.run_script([
            {"kind": "transport"},
            {"kind": "http_502"},
            self.body(valid),
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(curl_calls), 3)
        self.assertEqual(len(jq_calls), 1)
        self.assertEqual(sleeps, ["5", "5"])
        self.assert_jq_contract(jq_calls)

    def test_exact_valid_first_response_has_one_attempt_and_no_sleep(self):
        valid = json.dumps({"issuer": EXPECTED_ISSUER, "status": RESPONSE_MARKER})
        result, curl_calls, jq_calls, sleeps = self.run_script([self.body(valid)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(curl_calls), 1)
        self.assertEqual(len(jq_calls), 1)
        self.assertEqual(sleeps, [])
        self.assert_jq_contract(jq_calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
