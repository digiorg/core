#!/usr/bin/env python3
"""Fresh-bootstrap safety for the Gitea runner CA rotation restart."""

from pathlib import Path
import re
import unittest

SOURCE = (Path(__file__).resolve().parents[2] / "scripts/local-setup.nu").read_text()


class RunnerCaRestartBootstrapSafetyTest(unittest.TestCase):
    def test_phase_3_checks_token_before_optional_restart_and_gitea_configuration(self):
        token_lookup = SOURCE.index("get secret gitea-actions-runner-token")
        restart = SOURCE.index('restart_oidc_deployment_if_present "gitea" "gitea-actions-runner"')
        configure = SOURCE.index("    configure_gitea\n", restart)
        self.assertLess(token_lookup, restart)
        self.assertLess(restart, configure)

    def test_fresh_bootstrap_restart_requires_existing_runner_token(self):
        main_up = re.search(r'def "main up" \[\] \{.*?\n}\n', SOURCE, flags=re.DOTALL)
        self.assertIsNotNone(main_up)
        body = main_up.group(0) if main_up is not None else ""
        self.assertIn("get secret gitea-actions-runner-token", body)
        self.assertIn("--ignore-not-found", body)
        self.assertRegex(body, r"runner_token_lookup\.exit_code\s*!=\s*0")
        self.assertIn("if $gitea_ca_changed and $runner_token_exists {", body)

    def test_optional_restart_distinguishes_missing_from_lookup_failure(self):
        match = re.search(
            r"def restart_oidc_deployment_if_present .*?\n}\n",
            SOURCE,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match)
        body = match.group(0) if match is not None else ""
        self.assertIn("--ignore-not-found", body)
        self.assertRegex(body, r"exit_code\s*!=\s*0")
        self.assertRegex(body, r"stdout.*is-empty")
        self.assertIn("restart_oidc_deployment $namespace $deployment $timeout", body)

    def test_strict_restart_helper_remains_fail_closed_for_required_deployments(self):
        strict = re.search(
            r"def restart_oidc_deployment \[.*?\n}\n",
            SOURCE,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(strict)
        strict_body = strict.group(0) if strict is not None else ""
        self.assertIn("Required OIDC-dependent deployment", strict_body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
