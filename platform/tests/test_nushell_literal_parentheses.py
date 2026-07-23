#!/usr/bin/env python3
"""Runtime regression tests for literal parentheses in Nushell interpolation.

Inside ``$"..."`` Nushell treats unescaped parentheses as subexpressions. A
human-readable annotation such as ``(private)`` therefore attempts to execute a
command named ``private``. These tests execute the real status-print statements
from ``scripts/local-setup.nu`` so parse-only checks cannot miss this again.
"""
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
SETUP = ROOT / "scripts" / "local-setup.nu"


def _statement_containing(marker: str) -> str:
    matches = [line.strip() for line in SETUP.read_text(encoding="utf-8").splitlines()
               if marker in line and line.strip().startswith("print $")]
    if len(matches) != 1:
        raise AssertionError(f"expected one print statement containing {marker!r}, got {len(matches)}")
    return matches[0]


def _run_statement(marker: str, preamble: str = "") -> subprocess.CompletedProcess[str]:
    statement = _statement_containing(marker)
    return subprocess.run(
        ["nu", "-c", f"{preamble}{statement}"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


class NushellLiteralParenthesesRuntimeTest(unittest.TestCase):
    def assert_statement_renders_literal(self, marker: str, literal: str, preamble: str = ""):
        result = _run_statement(marker, preamble)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(literal, result.stdout)

    def test_app_config_private_annotation_is_literal(self):
        self.assert_statement_renders_literal("Repository 'DigiOrg/app-config' created", "(private)")

    def test_missing_argocd_optional_annotation_is_literal(self):
        self.assert_statement_renders_literal("argocd CLI not found", "(optional)")

    def test_incompatible_argocd_optional_annotation_is_literal(self):
        self.assert_statement_renders_literal(
            "argocd CLI found but not compatible",
            "v3.4.x (optional)",
            'let expected_argocd_minor = "3.4"; ',
        )

    def test_gitea_credential_annotations_are_literal(self):
        cases = (
            ("'crossplane-gitea-credentials' already present", "(membership re-verified)"),
            ("Least-privilege 'crossplane-gitea-credentials' created", "(write:repository only)"),
            ("ArgoCD app-config repository credential already present", "(membership re-verified)"),
            ("ArgoCD app-config repository credential created", "(read:repository only)"),
            ("Backstage app-config publish credential already present", "(membership re-verified)"),
            ("Least-privilege 'backstage-gitea-credentials' created", "(write:repository only)"),
        )
        for marker, literal in cases:
            with self.subTest(marker=marker):
                self.assert_statement_renders_literal(marker, literal)


if __name__ == "__main__":
    unittest.main(verbosity=2)
