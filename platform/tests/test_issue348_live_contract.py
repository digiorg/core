#!/usr/bin/env python3
"""Live-contract qualification for Issue #348's Argo CD v3.4.5 capture."""

from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "issue348_runtime_v2_transition.py"
FIXTURE = ROOT / "platform/tests/fixtures/issue348/argocd-v3.4.5/application-list.json"
SPEC = spec_from_file_location("issue348_live_contract", SCRIPT)
assert SPEC and SPEC.loader
transition = module_from_spec(SPEC)
SPEC.loader.exec_module(transition)


def captured_kyverno():
    listing = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert listing["apiVersion"] == "argoproj.io/v1alpha1"
    assert listing["kind"] == "ApplicationList"
    return listing["items"][0]


class LiveFixtureContractTest(unittest.TestCase):
    def test_captured_application_list_passes_production_status_validation(self):
        application = captured_kyverno()
        transition.Protocol.require_preflight_status({"kyverno": application})

    def test_core_group_absent_null_and_empty_normalize_to_core(self):
        application = captured_kyverno()
        resource = application["status"]["resources"][0]
        self.assertNotIn("group", resource)
        expected = ("", "v1", "ConfigMap", "kyverno", "kyverno")
        for group in (None, ""):
            with self.subTest(group=group):
                candidate = deepcopy(resource)
                candidate["group"] = group
                self.assertEqual(transition.canonical_kyverno_resource(candidate)[0], expected)

    def test_nonempty_group_is_preserved_and_nonstring_group_is_rejected(self):
        resource = next(
            item for item in captured_kyverno()["status"]["resources"]
            if item.get("group") == "apps"
        )
        self.assertEqual(transition.canonical_kyverno_resource(resource)[0][0], "apps")
        for group in (1, [], {}):
            with self.subTest(group=group):
                candidate = deepcopy(resource)
                candidate["group"] = group
                with self.assertRaisesRegex(transition.TransitionError, "identity/status"):
                    transition.canonical_kyverno_resource(candidate)

    def test_api_group_and_version_shapes_are_typed(self):
        core = deepcopy(captured_kyverno()["status"]["resources"][0])
        core["version"] = "v1beta1"
        noncore = deepcopy(next(
            item for item in captured_kyverno()["status"]["resources"]
            if item.get("group") == "apps"
        ))
        noncore["group"] = "bad/group"
        for resource in (core, noncore):
            with self.subTest(resource=resource):
                with self.assertRaisesRegex(transition.TransitionError, "identity/status"):
                    transition.canonical_kyverno_resource(resource)

    def test_inventory_rejects_duplicate_omission_extra_status_and_identity_drift(self):
        mutations = (
            lambda resources: resources.append(deepcopy(resources[0])),
            lambda resources: resources.pop(),
            lambda resources: resources.append({
                "group": "apps", "version": "v1", "kind": "Deployment",
                "namespace": "kyverno", "name": "unexpected", "status": "Synced",
            }),
            lambda resources: resources[0].update({"status": "OutOfSync"}),
            lambda resources: resources[0].update({"name": "identity-drift"}),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                application = captured_kyverno()
                mutate(application["status"]["resources"])
                with self.assertRaises(transition.TransitionError):
                    transition.Protocol.require_preflight_status({"kyverno": application})


class ParserAndArgvContractTest(unittest.TestCase):
    def setUp(self):
        self.argv = [
            "--mode", "retained-convergence", "--kubeconfig", "/secure/k",
            "--context", "retained", "--expected-server", "https://api.example",
            "--expected-kube-system-uid", "uid", "--remote-url", transition.CORE_REPO,
            "--runtime-tag", transition.RUNTIME_TAG, "--runtime-commit", "a" * 40,
            "--previous-tag", transition.PREVIOUS_TAG,
            "--previous-commit", transition.PREVIOUS_COMMIT,
            "--old-tag", transition.OLD_TAG, "--old-commit", transition.OLD_COMMIT,
            "--evidence", "/secure/e",
        ]

    def test_operational_cluster_identity_arguments_are_parser_required(self):
        for option in ("--expected-server", "--expected-kube-system-uid"):
            with self.subTest(option=option):
                argv = self.argv[:]
                index = argv.index(option)
                del argv[index:index + 2]
                with self.assertRaises(SystemExit):
                    transition.parse_args(argv)

    def test_unknown_and_option_like_values_fail(self):
        with self.assertRaises(SystemExit):
            transition.parse_args([*self.argv, "--unknown"])
        argv = self.argv[:]
        argv[argv.index("--context") + 1] = "--attacker-option"
        with self.assertRaises(SystemExit):
            transition.parse_args(argv)

    def test_reviewed_argocd_argv_is_token_for_token_data(self):
        self.assertEqual(
            transition.argocd_diff_argv("kyverno"),
            ["argocd", "app", "diff", "kyverno", "--core", "--refresh"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
