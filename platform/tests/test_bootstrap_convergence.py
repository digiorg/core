#!/usr/bin/env python3
"""Bootstrap convergence gate + control-flow contract (Issue #281).

A clean KinD bootstrap stalled at ``24/26`` because:

  * the client-side readiness inventory in ``wait_for_argocd_apps`` drifted from
    ``apps/platform/*.yaml`` (it omitted ``namespaces``);
  * the timeout printed only the aggregate ``<ready>/<total>`` ratio and never
    named the blocking Applications, and its status table was unreachable after
    ``error make``;
  * the only ``Healthy/OutOfSync`` material-diff fallback depends on the
    ``argocd`` CLI, which ``check_prerequisites`` never validated, so the
    fallback was silently unavailable;
  * the single global gate ran *before* the identity-configuration phases, so an
    unrelated late-wave drift (here ``cnpg-cluster``) aborted Gitea/SonarQube/
    OIDC configuration entirely.

This module locks the corrected contract with pure ``python3`` structural checks
plus a couple of real-Nushell behaviour checks of the new pure helper::

    python3 platform/tests/test_bootstrap_convergence.py
"""

import os
import re
import subprocess
import sys
import tempfile
import unittest

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard
    sys.stderr.write("PyYAML is required: pip install pyyaml\n")
    raise

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SETUP = os.path.join(REPO_ROOT, "scripts", "local-setup.nu")
APPS_DIR = os.path.join(REPO_ROOT, "apps", "platform")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _manifest_app_names():
    names = set()
    for name in os.listdir(APPS_DIR):
        if not name.endswith((".yaml", ".yml")):
            continue
        with open(os.path.join(APPS_DIR, name), encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        if doc and doc.get("kind") == "Application":
            names.add(doc["metadata"]["name"])
    return names


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


class InventoryParityTest(unittest.TestCase):
    """The waited Application-name set must equal every apps/platform manifest."""

    @classmethod
    def setUpClass(cls):
        cls.text = _read(SETUP)

    def _waited_names(self):
        body = _func_body(self.text, "wait_for_argocd_apps")
        marker = body.index("let apps = [")
        block = body[marker:body.index("]", marker)]
        return set(re.findall(r'"([a-z0-9-]+)"', block))

    def test_wait_inventory_matches_manifests_exactly(self):
        manifests = _manifest_app_names()
        waited = self._waited_names()
        self.assertEqual(
            waited, manifests,
            "the wait_for_argocd_apps inventory must match apps/platform/*.yaml "
            f"exactly; missing={manifests - waited} extra={waited - manifests}",
        )

    def test_namespaces_is_included(self):
        # The #281 evidence identified `namespaces` as the omitted child app.
        self.assertIn("namespaces", self._waited_names())


class ArgocdPrerequisiteTest(unittest.TestCase):
    """The material-diff fallback depends on argocd; make the dep explicit."""

    @classmethod
    def setUpClass(cls):
        cls.text = _read(SETUP)

    def test_check_prerequisites_requires_argocd(self):
        body = _func_body(self.text, "check_prerequisites")
        self.assertIn('"argocd"', body,
                      "check_prerequisites must require the argocd CLI so the "
                      "Healthy/OutOfSync material-diff fallback is never silently "
                      "unavailable")

    def test_check_prerequisites_verifies_matching_argocd_version(self):
        body = _func_body(self.text, "check_prerequisites")
        self.assertIn("argocd version --client", body)
        self.assertIn("v3.4.5", body)
        self.assertIn("argocd_client_version_matches", body)

    def test_argocd_version_match_is_exact_but_allows_build_metadata(self):
        self.assertEqual(
            _run_nu('argocd_client_version_matches "argocd: v3.4.5+564b949" "3.4.5"'),
            "true",
        )
        self.assertEqual(
            _run_nu('argocd_client_version_matches "argocd: v3.4.50+fake" "3.4.5"'),
            "false",
        )
        self.assertEqual(
            _run_nu('argocd_client_version_matches "garbage v3.4.5" "3.4.5"'),
            "false",
        )


class NonReadyDiagnosticsTest(unittest.TestCase):
    """Timeout must name every non-ready Application with bounded redacted state."""

    @classmethod
    def setUpClass(cls):
        cls.text = _read(SETUP)
        cls.wait = _func_body(cls.text, "wait_for_argocd_apps")

    def test_collects_health_sync_and_phase(self):
        self.assertIn("status.operationState.phase", self.wait)
        self.assertIn("status.health.status", self.wait)
        self.assertIn("status.sync.status", self.wait)

    def test_diagnostics_are_redacted(self):
        # Application state is untrusted text; the reporting helper reuses the
        # existing redaction as defence-in-depth.
        helper = _func_body(self.text, "format_non_ready_report")
        self.assertIn("redact_sync_diagnostic", helper)

    def test_non_ready_report_printed_before_timeout_error(self):
        # The bounded non-ready report and the status table must both appear
        # before `error make` fires on timeout.
        error_pos = self.wait.index("error make")
        report_pos = self.wait.index("format_non_ready_report")
        table_pos = self.wait.index("get applications -n argocd -o wide")
        self.assertLess(report_pos, error_pos,
                        "non-ready report must print before the timeout error")
        self.assertLess(table_pos, error_pos,
                        "status table must print before the timeout error")

    def test_status_table_on_success_path_too(self):
        # The final status table must be on BOTH the success and failure paths;
        # i.e. it appears at least twice in the function.
        self.assertGreaterEqual(
            self.wait.count("get applications -n argocd -o wide"), 2,
            "status table must be reachable on both success and timeout paths",
        )


class NonReadyReportHelperTest(unittest.TestCase):
    """The pure formatter turns app-state records into bounded redacted lines."""

    def test_formats_name_health_sync_phase(self):
        expr = (
            "format_non_ready_report ["
            "{name: cnpg-cluster, health: Healthy, sync: OutOfSync, phase: Running}, "
            "{name: kyverno, health: Healthy, sync: OutOfSync, phase: Succeeded}]"
        )
        out = _run_nu(expr)
        self.assertIn("cnpg-cluster", out)
        self.assertIn("OutOfSync", out)
        self.assertIn("Running", out)
        self.assertIn("kyverno", out)

    def test_report_is_empty_for_no_non_ready_apps(self):
        self.assertEqual(_run_nu("format_non_ready_report []"), "")


class ConfigurationDependencyTest(unittest.TestCase):
    """Each identity phase has explicit, fail-closed direct dependency gates."""

    @classmethod
    def setUpClass(cls):
        cls.text = _read(SETUP)
        cls.up = _func_body(cls.text, '"main up"')

    def test_gitea_dependencies_are_waited_before_configuration(self):
        wait = self.up.index('wait_for_configuration_dependencies "Gitea"')
        configure = self.up.index("configure_gitea")
        self.assertLess(wait, configure)
        block = self.up[wait:configure]
        for app in ("gitea", "keycloak", "cert-manager"):
            self.assertIn(f'"{app}"', block)
        self.assertIn("digiorg-local-ca", block)
        self.assertIn("digiorg-local-tls", block)

    def test_sonarqube_dependencies_are_waited_before_configuration(self):
        wait = self.up.index('wait_for_configuration_dependencies "SonarQube"')
        configure = self.up.index("configure_sonarqube")
        self.assertLess(wait, configure)
        block = self.up[wait:configure]
        for app in ("sonarqube", "keycloak", "cert-manager"):
            self.assertIn(f'"{app}"', block)
        self.assertIn("digiorg-local-ca", block)
        self.assertIn("digiorg-local-tls", block)

    def test_sonarqube_configuration_is_verified_by_readback(self):
        body = _func_body(self.text, "configure_sonarqube")
        self.assertIn('secrets "sonarqube-admin-secret" not found', body)
        self.assertIn('default "admin"', body)
        self.assertIn("Failed to read the SonarQube admin password", body)
        self.assertIn("/api/settings/values", body)
        self.assertIn("sonarqube_settings_match", body)
        self.assertIn("sonarqube_setting_present", body)
        self.assertIn("sonarqube_http_status_matches", body)
        self.assertIn("SonarQube settings readback did not match", body)
        good = '{"settings":[{"key":"sonar.auth.saml.enabled","value":"true"},{"key":"sonar.auth.saml.certificate.secured"}]}'
        bad = '{"settings":[{"key":"sonar.auth.saml.enabled","value":"false"}]}'
        missing = '{"settings":[]}'
        expected = '{"sonar.auth.saml.enabled":"true"}'
        self.assertEqual(_run_nu(f"sonarqube_settings_match '{good}' '{expected}'"), "true")
        self.assertEqual(_run_nu(f"sonarqube_settings_match '{bad}' '{expected}'"), "false")
        self.assertEqual(_run_nu(f"sonarqube_settings_match '{missing}' '{expected}'"), "false")
        self.assertEqual(_run_nu("sonarqube_settings_match 'not-json' '{\"x\":\"y\"}'"), "false")
        self.assertEqual(_run_nu(f"sonarqube_setting_present '{good}' 'sonar.auth.saml.certificate.secured'"), "true")
        self.assertEqual(_run_nu(f"sonarqube_setting_present '{missing}' 'sonar.auth.saml.certificate.secured'"), "false")
        self.assertEqual(_run_nu("sonarqube_http_status_matches 0 '204' '204'"), "true")
        self.assertEqual(_run_nu("sonarqube_http_status_matches 0 '302' '204'"), "false")
        self.assertEqual(_run_nu("sonarqube_http_status_matches 7 '204' '204'"), "false")

    def test_dependency_wait_is_bounded_and_fail_closed(self):
        helper = _func_body(self.text, "wait_for_configuration_dependencies")
        self.assertIn("error make", helper)
        self.assertIn("status.health.status", helper)
        self.assertIn("status.sync.status", helper)
        self.assertIn("Certificate", helper)
        self.assertIn("Ready", helper)

    def test_configuration_functions_do_not_silently_skip(self):
        for name in ("configure_gitea", "configure_sonarqube", "patch_argocd_oidc_ca"):
            body = _func_body(self.text, name)
            self.assertNotIn("skipping", body.lower(),
                             f"{name} must fail closed rather than report success after skipping")
            self.assertIn("error make", body)

    def test_gitea_api_uses_generated_token_without_process_arg_exposure(self):
        body = _func_body(self.text, "configure_gitea")
        self.assertNotIn("--page", body, "the pinned Gitea admin user list command has no pagination flags")
        self.assertNotIn("--page-size", body)
        self.assertIn("$gitea_token | kubectl", body)
        self.assertIn("curl --config -", body)
        self.assertIn("__DIGIORG_TOKEN_PLACEHOLDER__", body)
        self.assertNotIn('curl -fsSk -H "Authorization: token ${token}"', body)
        self.assertNotIn('--token="${token}"', body)
        self.assertNotIn("Authorization: token ($gitea_token)", body)
        self.assertNotIn("Authorization: token ***", body)

    def test_required_oidc_restarts_fail_closed_after_configuration(self):
        body = _func_body(self.text, "restart_oidc_dependent_pods")
        self.assertNotIn("catch", body)
        self.assertIn("restart_oidc_deployment", body)
        self.assertIn("wait_for_configuration_dependencies", body)
        self.assertIn('"argocd" "grafana" "backstage" "landingpage"', body)
        helper = _func_body(self.text, "restart_oidc_deployment")
        self.assertIn("error make", helper)
        self.assertNotIn("is not present; final convergence", helper)

    def test_gitea_final_readback_requires_both_bootstrap_members(self):
        body = _func_body(self.text, "configure_gitea")
        self.assertIn("missing_members", body)
        self.assertIn('"digiorgadmin" "digiorgdeveloper"', body)
        self.assertIn("Required Gitea Owners team members are missing", body)


class GiteaTokenTransportTest(unittest.TestCase):
    """The real token travels over stdin/config, never through child argv."""

    def test_curl_receives_authorization_via_stdin_config_not_argv(self):
        sentinel = "sentinel-token-never-in-argv"
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "curl")
            argv_log = os.path.join(tmp, "argv")
            stdin_log = os.path.join(tmp, "stdin")
            with open(fake, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$ARGV_LOG\"\ncat > \"$STDIN_LOG\"\nprintf '[]'\n")
            os.chmod(fake, 0o755)
            env = os.environ.copy()
            env["PATH"] = tmp + os.pathsep + env.get("PATH", "")
            env["ARGV_LOG"] = argv_log
            env["STDIN_LOG"] = stdin_log
            shell = (
                'IFS= read -r token; '
                'printf "header = \\\"Authorization: token %s\\\"\\n" "$token" '
                '| curl --config - -fsSk https://example.invalid/api'
            )
            result = subprocess.run(
                ["sh", "-c", shell], input=sentinel + "\n", text=True,
                capture_output=True, env=env, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(argv_log, encoding="utf-8") as fh:
                self.assertNotIn(sentinel, fh.read())
            with open(stdin_log, encoding="utf-8") as fh:
                self.assertIn(sentinel, fh.read())
            self.assertNotIn(sentinel, result.stdout + result.stderr)

    def test_tea_receives_only_placeholder_then_shell_rewrites_config(self):
        sentinel = "sentinel-tea-token-never-in-argv"
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "tea")
            argv_log = os.path.join(tmp, "tea-argv")
            with open(fake, "w", encoding="utf-8") as fh:
                fh.write(
                    "#!/bin/sh\n"
                    "printf '%s\\n' \"$@\" > \"$TEA_ARGV_LOG\"\n"
                    "last=\nfor arg do last=$arg; done\n"
                    "value=${last#--token=}\n"
                    "mkdir -p \"$HOME/.config/tea\"\n"
                    "printf 'logins:\\n  - token: %s\\n' \"$value\" > \"$HOME/.config/tea/config.yml\"\n"
                )
            os.chmod(fake, 0o755)
            env = os.environ.copy()
            env["PATH"] = tmp + os.pathsep + env.get("PATH", "")
            env["HOME"] = tmp
            env["TEA_ARGV_LOG"] = argv_log
            shell = (
                'set -eu; IFS= read -r token; placeholder=__DIGIORG_TOKEN_PLACEHOLDER__; '
                'tea login add --name=test --url=https://example.invalid --token="$placeholder" >/dev/null; '
                'cfg="${HOME:-/root}/.config/tea/config.yml"; tmpfile="${cfg}.tmp"; : > "$tmpfile"; '
                'while IFS= read -r line; do case "$line" in *"$placeholder"*) '
                'prefix=${line%%$placeholder*}; suffix=${line#*$placeholder}; '
                'printf "%s%s%s\\n" "$prefix" "$token" "$suffix" ;; '
                '*) printf "%s\\n" "$line" ;; esac; done < "$cfg" > "$tmpfile"; '
                'mv "$tmpfile" "$cfg"'
            )
            result = subprocess.run(
                ["sh", "-c", shell], input=sentinel + "\n", text=True,
                capture_output=True, env=env, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            with open(argv_log, encoding="utf-8") as fh:
                argv = fh.read()
            self.assertNotIn(sentinel, argv)
            self.assertIn("__DIGIORG_TOKEN_PLACEHOLDER__", argv)
            with open(os.path.join(tmp, ".config", "tea", "config.yml"), encoding="utf-8") as fh:
                config = fh.read()
            self.assertIn(sentinel, config)
            self.assertNotIn("__DIGIORG_TOKEN_PLACEHOLDER__", config)
            self.assertNotIn(sentinel, result.stdout + result.stderr)


class OidcRestartRuntimeTest(unittest.TestCase):
    """Required deployment lookup, restart and rollout failures are all fatal."""

    FAKE_KUBECTL = r'''#!/bin/sh
args="$*"
case "$args" in
  *"get deployment testdep"*)
    [ "$FAKE_SCENARIO" = lookup_failure ] && exit 1
    echo deployment.apps/testdep
    ;;
  *"rollout restart deployment testdep"*)
    [ "$FAKE_SCENARIO" = restart_failure ] && exit 1
    echo restarted
    ;;
  *"rollout status deployment testdep"*)
    [ "$FAKE_SCENARIO" = rollout_failure ] && exit 1
    echo complete
    ;;
  *) exit 2 ;;
esac
'''

    def _run(self, scenario):
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "kubectl")
            with open(fake, "w", encoding="utf-8") as fh:
                fh.write(self.FAKE_KUBECTL)
            os.chmod(fake, 0o755)
            env = os.environ.copy()
            env["PATH"] = tmp + os.pathsep + env.get("PATH", "")
            env["FAKE_SCENARIO"] = scenario
            return subprocess.run(
                ["nu", "-c", f"source {SETUP}; restart_oidc_deployment testns testdep 1s"],
                capture_output=True, text=True, timeout=10, env=env,
            )

    def test_lookup_failure_is_fatal(self):
        result = self._run("lookup_failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("is not present", result.stderr)

    def test_restart_failure_is_fatal(self):
        result = self._run("restart_failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Failed to restart", result.stderr)

    def test_rollout_failure_is_fatal(self):
        result = self._run("rollout_failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("did not complete", result.stderr)

    def test_success_requires_completed_rollout(self):
        result = self._run("success")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("testns/testdep restarted", result.stdout)


class ControlFlowTest(unittest.TestCase):
    """Config phases run dependency-aware; the global gate is the FINAL gate."""

    @classmethod
    def setUpClass(cls):
        cls.text = _read(SETUP)
        cls.up = _func_body(cls.text, '"main up"')
        cls.deploy = _func_body(cls.text, "deploy_root_app")

    def test_final_convergence_gate_runs_after_config_phases(self):
        up = self.up
        gitea = up.index("configure_gitea")
        sonar = up.index("configure_sonarqube")
        restart = up.index("restart_oidc_dependent_pods")
        gate = up.index("wait_for_argocd_apps")
        ready = up.index("Platform Ready")
        self.assertTrue(
            gitea < gate and sonar < gate and restart < gate < ready,
            "the all-Application convergence gate must run AFTER the identity "
            "configuration phases and immediately before the Platform Ready banner",
        )

    def test_deploy_root_app_does_not_run_global_gate(self):
        # The global gate must not abort configuration; deploy_root_app promotes
        # gated syncs but must NOT invoke the all-app convergence gate.
        self.assertNotIn("wait_for_argocd_apps", self.deploy,
                         "the all-app gate must not run inside deploy_root_app "
                         "(it would abort identity configuration on unrelated drift)")

    def test_final_gate_remains_fail_closed(self):
        # wait_for_argocd_apps must still error on timeout (fail-closed).
        wait = _func_body(self.text, "wait_for_argocd_apps")
        self.assertIn("error make", wait)


if __name__ == "__main__":
    unittest.main(verbosity=2)
