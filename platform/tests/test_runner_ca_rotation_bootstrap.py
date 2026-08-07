#!/usr/bin/env python3
"""Fresh-bootstrap safety for the Gitea runner CA rotation restart."""

from pathlib import Path
import re
import unittest

SOURCE = (Path(__file__).resolve().parents[2] / "scripts/local-setup.nu").read_text()


class RunnerCaRestartBootstrapSafetyTest(unittest.TestCase):
    def test_phase_3_checks_token_before_optional_restart_and_gitea_configuration(self):
        gated_sync = re.search(
            r"def sync_gated_apps_for_local_dev \[\] \{.*?\n}\n",
            SOURCE,
            flags=re.DOTALL,
        )
        deploy_root_app = re.search(
            r"def deploy_root_app \[\] \{.*?\n}\n", SOURCE, flags=re.DOTALL
        )
        main_up = re.search(r'def "main up" \[\] \{.*?\n}\n', SOURCE, flags=re.DOTALL)
        self.assertIsNotNone(gated_sync)
        self.assertIsNotNone(deploy_root_app)
        self.assertIsNotNone(main_up)
        gated_body = gated_sync.group(0) if gated_sync is not None else ""
        deploy_body = deploy_root_app.group(0) if deploy_root_app is not None else ""
        main_body = main_up.group(0) if main_up is not None else ""
        token_lookup = gated_body.index("get secret gitea-actions-runner-token")
        restart = gated_body.index(
            'restart_oidc_deployment_if_present "gitea" "gitea-actions-runner"'
        )
        self.assertLess(token_lookup, restart)
        self.assertIn("\n    sync_gated_apps_for_local_dev\n", deploy_body)
        self.assertLess(
            main_body.index("\n    deploy_root_app\n"),
            main_body.index("\n    configure_gitea\n"),
        )

    def test_fresh_bootstrap_restart_requires_existing_runner_token(self):
        gated_sync = re.search(
            r"def sync_gated_apps_for_local_dev \[\] \{.*?\n}\n",
            SOURCE,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(gated_sync)
        body = gated_sync.group(0) if gated_sync is not None else ""
        self.assertIn("get secret gitea-actions-runner-token", body)
        self.assertIn("--ignore-not-found", body)
        self.assertRegex(body, r"runner_token_lookup\.exit_code\s*!=\s*0")
        self.assertRegex(
            body,
            r"if \$runner_token_lookup\.exit_code != 0 \{\s*error make",
        )
        self.assertRegex(
            body,
            r"if \$runner_token_exists \{\s*"
            r'restart_oidc_deployment_if_present "gitea" "gitea-actions-runner"',
        )

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
