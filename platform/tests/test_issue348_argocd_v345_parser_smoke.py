#!/usr/bin/env python3
"""Opt-in parser smoke contract for the real pinned Argo CD v3.4.5 CLI."""

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ARGOCD_BINARY_ENV = "ARGOCD_V345_BINARY"
ARGOCD_V345_LINUX_AMD64_SHA256 = (
    "23303f05a58c1e041324d5645b0f9d6ea338b16bbf32f4a24508f388fcf9f9c0"
)


@unittest.skipUnless(
    os.environ.get(ARGOCD_BINARY_ENV),
    f"set {ARGOCD_BINARY_ENV} to run the pinned Argo CD v3.4.5 parser smoke",
)
class ArgoCdV345ParserSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binary = Path(os.environ[ARGOCD_BINARY_ENV]).resolve(strict=True)
        digest = hashlib.sha256(cls.binary.read_bytes()).hexdigest()
        if digest != ARGOCD_V345_LINUX_AMD64_SHA256:
            raise AssertionError(
                f"{ARGOCD_BINARY_ENV} is not the pinned Linux amd64 v3.4.5 binary"
            )

        version = subprocess.run(
            [str(cls.binary), "version", "--client", "--short"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if version.returncode != 0 or not version.stdout.startswith("argocd: v3.4.5+"):
            raise AssertionError(f"unexpected pinned CLI identity: {version.stdout!r}")

    def parser(self, argv):
        with tempfile.TemporaryDirectory(prefix="issue348-argocd-parser-") as temp:
            kubeconfig = Path(temp) / "kubeconfig"
            kubeconfig.write_text(
                "apiVersion: v1\nkind: Config\ncurrent-context: retained\n",
                encoding="utf-8",
            )
            kubeconfig.chmod(0o600)
            env = os.environ.copy()
            env["KUBECONFIG"] = str(kubeconfig)
            return subprocess.run(
                [str(self.binary), *argv, "--help"],
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
                env=env,
            )

    def test_previous_root_flags_are_rejected_by_v345(self):
        result = self.parser([
            "--kubeconfig", "/secure/kubeconfig",
            "--kube-context", "retained",
            "--namespace", "argocd",
            "--core", "app", "diff", "kyverno", "--refresh",
        ])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown flag: --kubeconfig", result.stdout + result.stderr)

    def test_environment_isolated_supported_invocation_parses_in_v345(self):
        result = self.parser([
            "app", "diff", "kyverno", "--core", "--refresh",
        ])
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)