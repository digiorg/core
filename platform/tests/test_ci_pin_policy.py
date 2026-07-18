#!/usr/bin/env python3
"""The pin-policy checker and platform regression tests must run in CI (Issue #275).

Issue #275 requires "CI checks that reject `latest`, branch tags, wildcard/range
chart versions, and non-digest-pinned image references". A checker that never
runs in CI does not satisfy that, so this test asserts a GitHub Actions workflow
exists that actually invokes ``scripts/check_pins.py`` and the platform test
suite, on pushes and pull requests.

Pure python3 + PyYAML::

    python3 platform/tests/test_ci_pin_policy.py
"""

import glob
import os
import re
import sys
import unittest

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.stderr.write("PyYAML is required: pip install pyyaml\n")
    raise

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WORKFLOW_DIR = os.path.join(REPO_ROOT, ".github", "workflows")


def _workflow_texts():
    out = {}
    for path in glob.glob(os.path.join(WORKFLOW_DIR, "*.y*ml")):
        with open(path, encoding="utf-8") as fh:
            out[path] = fh.read()
    return out


class CiWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.texts = _workflow_texts()

    def test_a_workflow_exists(self):
        self.assertTrue(self.texts, "expected a GitHub Actions workflow under .github/workflows")

    def test_some_workflow_runs_check_pins(self):
        self.assertTrue(
            any("scripts/check_pins.py" in t for t in self.texts.values()),
            "a CI workflow must run scripts/check_pins.py",
        )

    def test_some_workflow_renders_helm_and_checks_chart_images(self):
        workflow = next(
            text for text in self.texts.values() if "scripts/check_pins.py" in text
        )
        self.assertIn("scripts/render_platform_charts.py", workflow)
        self.assertIn("helm-v4.2.3-linux-amd64.tar.gz", workflow)

    def test_some_workflow_runs_platform_tests(self):
        self.assertTrue(
            any(("platform/tests" in t or "unittest discover" in t)
                for t in self.texts.values()),
            "a CI workflow must run the platform/tests suite",
        )

    def test_triggers_on_push_and_pull_request(self):
        found_pr = found_push = False
        for text in self.texts.values():
            if "scripts/check_pins.py" not in text:
                continue
            doc = yaml.safe_load(text)
            # YAML parses the `on:` key as the boolean True.
            on = doc.get("on", doc.get(True, {}))
            if isinstance(on, list):
                keys = set(on)
            elif isinstance(on, dict):
                keys = set(on.keys())
            else:
                keys = {on}
            found_pr = found_pr or "pull_request" in keys
            found_push = found_push or "push" in keys
        self.assertTrue(found_pr, "CI must trigger on pull_request")
        self.assertTrue(found_push, "CI must trigger on push")

    def test_github_actions_are_commit_pinned(self):
        workflow = next(
            text for text in self.texts.values() if "scripts/check_pins.py" in text
        )
        uses = re.findall(r"^\s*-?\s*uses:\s*[^@\s]+@([^\s#]+)", workflow, re.MULTILINE)
        self.assertTrue(uses)
        for ref in uses:
            self.assertRegex(ref, r"^[0-9a-f]{40}$")

    def test_validation_tool_versions_are_reproducible_and_compatible(self):
        workflow = next(
            text for text in self.texts.values() if "scripts/check_pins.py" in text
        )
        # OpenCost's build.nu uses Nushell syntax supported by the repository's
        # current toolchain (0.114.1); CI 0.98.0 reports an IDE parse diagnostic.
        self.assertIn("NU_VERSION=0.114.1", workflow)
        # Never install kustomize from a floating master-branch script.
        self.assertIn("KUSTOMIZE_VERSION=v5.8.1", workflow)
        self.assertNotIn("kustomize/master/hack/install_kustomize.sh", workflow)

    def test_nushell_is_installed_before_platform_regression_tests(self):
        workflow = next(
            text for text in self.texts.values() if "scripts/check_pins.py" in text
        )
        marker = "Install Nushell for behavioral tests"
        self.assertIn(marker, workflow)
        self.assertLess(
            workflow.index(marker),
            workflow.index("Platform regression tests"),
            "behavioral .nu tests require the pinned Nushell binary before Python tests run",
        )

    def test_workflows_are_valid_yaml(self):
        for path, text in self.texts.items():
            try:
                yaml.safe_load(text)
            except yaml.YAMLError as exc:  # pragma: no cover
                self.fail(f"{path} is invalid YAML: {exc}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
