#!/usr/bin/env python3
"""Executable contract tests for the Issue #290 AppClaim API readiness gate.

The tests source the real Nushell bootstrap and put a deterministic fake
``kubectl`` first on PATH. No live cluster or user kubeconfig is accessed.

Run:
    python3 platform/tests/test_appclaim_readiness_gate.py
"""

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[2]
SETUP_PATH = ROOT / "scripts" / "local-setup.nu"

XRD_PATH = (
    "/apis/apiextensions.crossplane.io/v1/"
    "compositeresourcedefinitions/applications.platform.digiorg.io"
)
APPLICATION_CRD_PATH = (
    "/apis/apiextensions.k8s.io/v1/customresourcedefinitions/"
    "applications.platform.digiorg.io"
)
APPCLAIM_CRD_PATH = (
    "/apis/apiextensions.k8s.io/v1/customresourcedefinitions/"
    "appclaims.platform.digiorg.io"
)


def _setup_text():
    return SETUP_PATH.read_text(encoding="utf-8")


def _func_body(text, name):
    marker = f"def {name} ["
    if marker not in text:
        return ""
    start = text.index(marker)
    end = text.find("\ndef ", start + len(marker))
    return text[start:] if end < 0 else text[start:end]


FAKE_KUBECTL = r"""#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
with open(os.environ["KUBECTL_ARGV_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\n")

expected_kubeconfig = os.environ["EXPECTED_KUBECONFIG"]
if "--kubeconfig" not in args:
    sys.stderr.write("missing explicit kubeconfig\n")
    sys.exit(90)
index = args.index("--kubeconfig")
if index + 1 >= len(args) or args[index + 1] != expected_kubeconfig:
    sys.stderr.write("wrong kubeconfig path\n")
    sys.exit(91)
if "--raw" not in args:
    sys.stderr.write("expected structured raw API request\n")
    sys.exit(92)

path = args[args.index("--raw") + 1]
scenario = os.environ.get("FAKE_SCENARIO", "success")
sentinel = os.environ["SENTINEL"]

xrd = {
    "metadata": {"name": "applications.platform.digiorg.io"},
    "spec": {"secret": sentinel},
    "status": {
        "conditions": [
            {
                "type": "Established",
                "status": "True",
                "reason": "WatchingCompositeResource",
                "message": sentinel,
            },
            {
                "type": "Offered",
                "status": "True",
                "reason": "WatchingCompositeResourceClaim",
                "message": sentinel,
            },
        ],
        "controllers": {
            "compositeResourceType": {
                "kind": "Application",
                "apiVersion": "platform.digiorg.io/v1alpha1",
            },
            "compositeResourceClaimType": {
                "kind": "AppClaim",
                "apiVersion": "platform.digiorg.io/v1alpha1",
            },
        },
    },
}

def crd(name):
    return {
        "metadata": {"name": name},
        "spec": {"secret": sentinel},
        "status": {
            "conditions": [
                {
                    "type": "Established",
                    "status": "True",
                    "reason": "InitialNamesAccepted",
                    "message": sentinel,
                }
            ]
        },
    }

application_crd = crd("applications.platform.digiorg.io")
appclaim_crd = crd("appclaims.platform.digiorg.io")

condition_scenarios = {
    "missing_xrd_established": ("Established", None),
    "missing_xrd_offered": ("Offered", None),
    "false_xrd_established": ("Established", "False"),
    "false_xrd_offered": ("Offered", "False"),
}
if scenario in condition_scenarios:
    condition_type, replacement = condition_scenarios[scenario]
    conditions = xrd["status"]["conditions"]
    if replacement is None:
        xrd["status"]["conditions"] = [
            item for item in conditions if item["type"] != condition_type
        ]
    else:
        next(item for item in conditions if item["type"] == condition_type)[
            "status"
        ] = replacement

controller_scenarios = {
    "missing_composite_kind": ("compositeResourceType", "kind"),
    "missing_composite_api_version": ("compositeResourceType", "apiVersion"),
    "missing_claim_kind": ("compositeResourceClaimType", "kind"),
    "missing_claim_api_version": ("compositeResourceClaimType", "apiVersion"),
}
if scenario in controller_scenarios:
    controller, field = controller_scenarios[scenario]
    del xrd["status"]["controllers"][controller][field]

wrong_controller_scenarios = {
    "wrong_composite_kind": ("compositeResourceType", "kind"),
    "wrong_composite_api_version": ("compositeResourceType", "apiVersion"),
    "wrong_claim_kind": ("compositeResourceClaimType", "kind"),
    "wrong_claim_api_version": ("compositeResourceClaimType", "apiVersion"),
}
if scenario in wrong_controller_scenarios:
    controller, field = wrong_controller_scenarios[scenario]
    xrd["status"]["controllers"][controller][field] = sentinel

whitespace_controller_scenarios = {
    "whitespace_composite_kind": ("compositeResourceType", "kind"),
    "whitespace_composite_api_version": ("compositeResourceType", "apiVersion"),
    "whitespace_claim_kind": ("compositeResourceClaimType", "kind"),
    "whitespace_claim_api_version": ("compositeResourceClaimType", "apiVersion"),
}
if scenario in whitespace_controller_scenarios:
    controller, field = whitespace_controller_scenarios[scenario]
    xrd["status"]["controllers"][controller][field] = " \t "

if scenario == "false_application_established":
    application_crd["status"]["conditions"][0]["status"] = "False"
if scenario == "false_appclaim_established":
    appclaim_crd["status"]["conditions"][0]["status"] = "False"
if scenario == "missing_application_established":
    application_crd["status"]["conditions"] = []
if scenario == "missing_appclaim_established":
    appclaim_crd["status"]["conditions"] = []

if scenario == "malformed_xrd" and path == os.environ["XRD_PATH"]:
    sys.stdout.write("{not-json")
    sys.exit(0)
if scenario == "empty_application_crd" and path == os.environ["APPLICATION_CRD_PATH"]:
    sys.stdout.write("  ")
    sys.exit(0)
if scenario == "missing_application_crd" and path == os.environ["APPLICATION_CRD_PATH"]:
    sys.stderr.write("NotFound " + sentinel)
    sys.exit(1)
if scenario == "missing_appclaim_crd" and path == os.environ["APPCLAIM_CRD_PATH"]:
    sys.stderr.write("NotFound " + sentinel)
    sys.exit(1)

documents = {
    os.environ["XRD_PATH"]: xrd,
    os.environ["APPLICATION_CRD_PATH"]: application_crd,
    os.environ["APPCLAIM_CRD_PATH"]: appclaim_crd,
}
if path not in documents:
    sys.stderr.write("unexpected path\n")
    sys.exit(93)
sys.stdout.write(json.dumps(documents[path]))
"""


class AppClaimReadinessGateTest(unittest.TestCase):
    maxDiff = None

    def _run(self, scenario, attempts=1, interval="1ms"):
        with tempfile.TemporaryDirectory(prefix="issue 290 readiness ") as tmp:
            tmp_path = Path(tmp)
            fake = tmp_path / "kubectl"
            fake.write_text(FAKE_KUBECTL, encoding="utf-8")
            fake.chmod(0o755)
            argv_log = tmp_path / "kubectl argv.jsonl"
            original_kubeconfig = str(tmp_path / "original config.yaml")
            env = os.environ.copy()
            env.update(
                {
                    "PATH": tmp + os.pathsep + env.get("PATH", ""),
                    "KUBECONFIG": original_kubeconfig,
                    "KUBECTL_ARGV_LOG": str(argv_log),
                    "EXPECTED_KUBECONFIG": str(tmp_path / "kubeconfig-local.yaml"),
                    "FAKE_SCENARIO": scenario,
                    "SENTINEL": "secret-body-must-never-be-printed",
                    "XRD_PATH": XRD_PATH,
                    "APPLICATION_CRD_PATH": APPLICATION_CRD_PATH,
                    "APPCLAIM_CRD_PATH": APPCLAIM_CRD_PATH,
                }
            )
            expression = (
                f"source '{SETUP_PATH}'; "
                f"wait_for_appclaim_api_ready {attempts} {interval}; "
                'print $"ORIGINAL_KUBECONFIG=($env.KUBECONFIG)"'
            )
            started = time.monotonic()
            result = subprocess.run(
                ["nu", "-c", expression],
                cwd=tmp,
                env=env,
                capture_output=True,
                text=True,
                timeout=8,
            )
            elapsed = time.monotonic() - started
            calls = (
                [
                    json.loads(line)
                    for line in argv_log.read_text(encoding="utf-8").splitlines()
                ]
                if argv_log.exists()
                else []
            )
            return result, elapsed, calls, original_kubeconfig

    def test_success_uses_structured_raw_json_and_preserves_kubeconfig(self):
        result, elapsed, calls, original = self._run("success")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(elapsed, 3)
        self.assertIn("AppClaim API is ready", result.stdout)
        self.assertIn(f"ORIGINAL_KUBECONFIG={original}", result.stdout)
        self.assertNotIn("secret-body-must-never-be-printed", result.stdout + result.stderr)
        self.assertEqual(len(calls), 3)
        self.assertEqual(
            {call[call.index("--raw") + 1] for call in calls},
            {XRD_PATH, APPLICATION_CRD_PATH, APPCLAIM_CRD_PATH},
        )

    def test_each_missing_or_false_xrd_condition_fails_closed(self):
        for scenario, expected in (
            ("missing_xrd_established", "Established"),
            ("false_xrd_established", "Established"),
            ("missing_xrd_offered", "Offered"),
            ("false_xrd_offered", "Offered"),
        ):
            with self.subTest(scenario=scenario):
                result, elapsed, _calls, _original = self._run(scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertLess(elapsed, 3)
                output = result.stdout + result.stderr
                self.assertIn("applications.platform.digiorg.io", output)
                self.assertIn(expected, output)
                self.assertNotIn("secret-body-must-never-be-printed", output)

    def test_each_missing_controller_identity_field_fails_closed(self):
        for scenario, expected in (
            ("missing_composite_kind", "compositeResourceType.kind"),
            ("missing_composite_api_version", "compositeResourceType.apiVersion"),
            ("missing_claim_kind", "compositeResourceClaimType.kind"),
            ("missing_claim_api_version", "compositeResourceClaimType.apiVersion"),
        ):
            with self.subTest(scenario=scenario):
                result, elapsed, _calls, _original = self._run(scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertLess(elapsed, 3)
                output = result.stdout + result.stderr
                self.assertIn(expected, output)
                self.assertNotIn("secret-body-must-never-be-printed", output)

    def test_each_wrong_or_whitespace_controller_identity_fails_closed_redacted(self):
        for scenario, expected in (
            ("wrong_composite_kind", "compositeResourceType.kind"),
            ("whitespace_composite_kind", "compositeResourceType.kind"),
            ("wrong_composite_api_version", "compositeResourceType.apiVersion"),
            ("whitespace_composite_api_version", "compositeResourceType.apiVersion"),
            ("wrong_claim_kind", "compositeResourceClaimType.kind"),
            ("whitespace_claim_kind", "compositeResourceClaimType.kind"),
            ("wrong_claim_api_version", "compositeResourceClaimType.apiVersion"),
            ("whitespace_claim_api_version", "compositeResourceClaimType.apiVersion"),
        ):
            with self.subTest(scenario=scenario):
                result, elapsed, _calls, _original = self._run(scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertLess(elapsed, 3)
                output = result.stdout + result.stderr
                self.assertIn(expected, output)
                self.assertIn("ControllerIdentityMismatch", output)
                self.assertLessEqual(
                    len(output),
                    1500,
                    "readiness diagnostics must remain bounded",
                )
                self.assertNotIn("secret-body-must-never-be-printed", output)
                self.assertNotIn('"message"', output)
                self.assertNotIn('"spec"', output)

    def test_each_generated_crd_must_exist_and_be_established(self):
        for scenario, expected in (
            ("missing_application_crd", "applications.platform.digiorg.io"),
            ("missing_application_established", "applications.platform.digiorg.io"),
            ("false_application_established", "applications.platform.digiorg.io"),
            ("missing_appclaim_crd", "appclaims.platform.digiorg.io"),
            ("missing_appclaim_established", "appclaims.platform.digiorg.io"),
            ("false_appclaim_established", "appclaims.platform.digiorg.io"),
        ):
            with self.subTest(scenario=scenario):
                result, elapsed, _calls, _original = self._run(scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertLess(elapsed, 3)
                output = result.stdout + result.stderr
                self.assertIn(expected, output)
                self.assertNotIn("secret-body-must-never-be-printed", output)

    def test_malformed_and_empty_kubectl_json_fail_closed(self):
        for scenario, expected in (
            ("malformed_xrd", "InvalidJSON"),
            ("empty_application_crd", "EmptyData"),
        ):
            with self.subTest(scenario=scenario):
                result, elapsed, _calls, _original = self._run(scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertLess(elapsed, 3)
                output = result.stdout + result.stderr
                self.assertIn(expected, output)
                self.assertNotIn("secret-body-must-never-be-printed", output)

    def test_timeout_is_bounded_and_repolls(self):
        result, elapsed, calls, _original = self._run(
            "false_xrd_offered", attempts=2, interval="10ms"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(elapsed, 3)
        self.assertEqual(
            sum(XRD_PATH in call for call in calls),
            2,
            "a pending status must be polled until the bounded attempt budget expires",
        )
        self.assertIn("attempt 2/2", result.stdout + result.stderr)


class AppClaimReadinessSourceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = _setup_text()
        cls.gate = _func_body(cls.text, "wait_for_appclaim_api_ready")
        cls.up = _func_body(cls.text, '"main up"')

    def test_gate_is_after_final_argo_convergence_and_before_success(self):
        argo = self.up.index("wait_for_argocd_apps")
        appclaim = self.up.index("wait_for_appclaim_api_ready")
        ready = self.up.index("Platform Ready!")
        self.assertLess(argo, appclaim)
        self.assertLess(appclaim, ready)
        self.assertEqual(self.up.count("wait_for_appclaim_api_ready"), 1)

    def test_gate_is_portable_structured_and_bounded(self):
        parser = _func_body(self.text, "parse_appclaim_readiness_json")
        self.assertIn("--kubeconfig $KUBECONFIG_PATH", self.gate)
        self.assertIn("get --raw", self.gate)
        self.assertIn("from json", parser)
        self.assertIn("parse_appclaim_readiness_json", self.gate)
        self.assertIn("max_attempts: int = 20", self.gate)
        self.assertIn("poll_interval: duration = 2sec", self.gate)
        self.assertNotIn("$env.KUBECONFIG =", self.gate)
        self.assertNotIn("/dev/stdin", self.gate)
        self.assertNotIn("mktemp", self.gate)

    def test_default_conservative_worst_case_is_within_180_seconds(self):
        attempts_match = re.search(
            r"max_attempts:\s*int\s*=\s*(\d+)", self.gate
        )
        interval_match = re.search(
            r"poll_interval:\s*duration\s*=\s*(\d+)sec", self.gate
        )
        self.assertIsNotNone(attempts_match)
        self.assertIsNotNone(interval_match)

        attempts = int(attempts_match.group(1))
        poll_interval_seconds = int(interval_match.group(1))
        request_timeout_sites = [
            int(value)
            for value in re.findall(r"--request-timeout=(\d+)s", self.gate)
        ]
        self.assertEqual(
            len(request_timeout_sites),
            2,
            "the XRD request and generated-CRD loop must each set a timeout",
        )
        crds_match = re.search(
            r"let crds\s*=\s*\[(.*?)\n\s*\]", self.gate, re.DOTALL
        )
        self.assertIsNotNone(crds_match)
        generated_crd_requests = len(
            re.findall(r"^\s*path:", crds_match.group(1), re.MULTILINE)
        )
        request_timeouts = [
            request_timeout_sites[0],
            *([request_timeout_sites[1]] * generated_crd_requests),
        ]
        self.assertEqual(
            len(request_timeouts),
            3,
            "one XRD plus two generated CRDs are requested serially per attempt",
        )
        self.assertEqual(
            len(set(request_timeouts)),
            1,
            "all serial readiness requests must use one portable timeout",
        )

        conservative_worst_case_seconds = (
            attempts * sum(request_timeouts)
            + (attempts - 1) * poll_interval_seconds
        )
        self.assertLessEqual(
            conservative_worst_case_seconds,
            180,
            "default AppClaim readiness gate exceeds the 180-second ceiling",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
