#!/usr/bin/env python3
"""The host `argocd` CLI is optional, not a mandatory bootstrap prerequisite
(Issue #283).

It is used ONLY by `argocd_app_has_no_material_diff` — a fail-closed secondary
check for the narrow case where Argo CD reports a stale `Healthy`/`OutOfSync`
Application status. Requiring an exact-patch-matching `argocd` binary just to
START the bootstrap was unrelated to the actual database-critical-path defect
this issue fixes and made the tool a hard blocker for a fallback most runs
never exercise. This module locks:

  * `check_prerequisites` no longer exits when `argocd` is absent (kind,
    kubectl and helm remain mandatory).
  * When present, the client is checked for MAJOR.MINOR compatibility (not
    exact-patch) — avoiding brittleness against safe patch releases.
  * `argocd_app_has_no_material_diff` still fails closed (returns false) when
    the binary is missing OR incompatible, so material-drift detection is
    never weakened.

Pure Nushell behaviour checks plus structural checks::

    python3 platform/tests/test_argocd_cli_optional.py
"""

import os
import re
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SETUP = os.path.join(REPO_ROOT, "scripts", "local-setup.nu")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _func_body(text, name):
    start = text.index(f"def {name} ")
    end = text.index("\ndef ", start + 10)
    return text[start:end]


def _run_nu(expr: str) -> str:
    result = subprocess.run(
        ["nu", "-c", f"source {SETUP}; {expr}"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise AssertionError(f"nu failed: {result.stderr}")
    return result.stdout.strip()


class CheckPrerequisitesStructureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = _read(SETUP)
        cls.body = _func_body(cls.text, "check_prerequisites")

    def test_kind_kubectl_helm_remain_mandatory(self):
        for tool in ('"kind"', '"kubectl"', '"helm"'):
            self.assertIn(tool, self.body)

    def test_argocd_is_not_in_the_mandatory_tools_list(self):
        marker = self.body.index("let tools = [")
        end = self.body.index("]", marker)
        mandatory_block = self.body[marker:end]
        self.assertNotIn('"argocd"', mandatory_block,
                         "argocd must not be a mandatory prerequisite tool")

    def test_missing_argocd_does_not_exit(self):
        self.assertNotIn(
            'error make {msg: $"argocd CLI',
            self.text,
            "a missing/incompatible argocd CLI must not raise a fatal error",
        )

    def test_argocd_presence_is_still_checked_informationally(self):
        self.assertIn("which argocd", self.body)


class VersionCompatibilityPredicateTest(unittest.TestCase):
    """argocd_client_version_compatible matches MAJOR.MINOR, tolerating patch drift."""

    def test_matching_minor_with_different_patch_is_true(self):
        self.assertEqual(
            _run_nu('argocd_client_version_compatible "argocd: v3.4.9+abcdef" "3.4"'),
            "true",
        )

    def test_matching_minor_exact_patch_is_true(self):
        self.assertEqual(
            _run_nu('argocd_client_version_compatible "argocd: v3.4.5+564b949" "3.4"'),
            "true",
        )

    def test_different_minor_is_false(self):
        self.assertEqual(
            _run_nu('argocd_client_version_compatible "argocd: v3.5.0" "3.4"'),
            "false",
        )

    def test_different_major_is_false(self):
        self.assertEqual(
            _run_nu('argocd_client_version_compatible "argocd: v4.4.5" "3.4"'),
            "false",
        )

    def test_garbage_is_false(self):
        self.assertEqual(
            _run_nu('argocd_client_version_compatible "garbage v3.4.5" "3.4"'),
            "false",
        )


class MaterialDiffFallbackStillFailsClosedTest(unittest.TestCase):
    """argocd_app_has_no_material_diff must stay fail-closed on absence/incompatibility."""

    @classmethod
    def setUpClass(cls):
        cls.body = _func_body(_read(SETUP), "argocd_app_has_no_material_diff")

    def test_returns_false_when_binary_missing(self):
        self.assertIn("which argocd", self.body)
        self.assertIn("is-empty", self.body)

    def test_checks_version_compatibility_before_trusting_diff(self):
        self.assertIn("argocd_client_version_compatible", self.body)

    def test_missing_binary_runtime_returns_false(self):
        # This sandbox has no argocd binary installed anywhere on PATH; use the
        # real environment (a fabricated empty PATH would also hide `nu` itself).
        self.assertTrue((__import__("shutil").which("argocd")) is None,
                        "this test assumes no argocd binary is on PATH")
        result = subprocess.run(
            ["nu", "-c", f"source {SETUP}; argocd_app_has_no_material_diff foo | to json --raw"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "false")

    def test_incompatible_version_runtime_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "argocd")
            with open(fake, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\necho 'argocd: v2.9.9+deadbeef'\n")
            os.chmod(fake, 0o755)
            env = os.environ.copy()
            env["PATH"] = tmp + os.pathsep + env.get("PATH", "")
            result = subprocess.run(
                ["nu", "-c", f"source {SETUP}; argocd_app_has_no_material_diff foo | to json --raw"],
                capture_output=True, text=True, timeout=15, env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "false")


if __name__ == "__main__":
    unittest.main(verbosity=2)
