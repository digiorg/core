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

import base64
import json
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
        # Issue #283: cnpg/cnpg-cluster are deliberately excluded from the
        # fail-closed core gate (they are optional future-app infrastructure,
        # promoted separately and non-fatally — see
        # test_cnpg_decoupled_promotion.py). Every OTHER manifest Application
        # must still be waited on exactly.
        manifests = _manifest_app_names() - {"cnpg", "cnpg-cluster"}
        waited = self._waited_names()
        self.assertEqual(
            waited, manifests,
            "the wait_for_argocd_apps inventory must match apps/platform/*.yaml "
            "exactly (except the intentionally-excluded cnpg/cnpg-cluster); "
            f"missing={manifests - waited} extra={waited - manifests}",
        )

    def test_namespaces_is_included(self):
        # The #281 evidence identified `namespaces` as the omitted child app.
        self.assertIn("namespaces", self._waited_names())


class HelmVersionCompatibilityTest(unittest.TestCase):
    """Helm 3 vs Helm 4 --force-conflicts handling for the self-managed ArgoCD
    release. (The `argocd` CLI prerequisite itself is optional as of Issue
    #283 — see test_argocd_cli_optional.py.)"""

    @classmethod
    def setUpClass(cls):
        cls.text = _read(SETUP)

    def test_argocd_bootstrap_upgrade_handles_helm3_and_helm4(self):
        install = _func_body(self.text, "install_argocd")
        oidc = _func_body(self.text, "patch_argocd_oidc_ca")
        self.assertIn("...$helm_conflict_args", install)
        self.assertIn("...$helm_conflict_args", oidc)
        self.assertEqual(
            _run_nu('helm_force_conflicts_args_for_version "v3.12.3+g3a31588" | to json --raw'),
            "[]",
        )
        self.assertEqual(
            _run_nu('helm_force_conflicts_args_for_version "v4.2.3+gabcdef" | to json --raw'),
            '["--force-conflicts"]',
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

    def test_bootstrap_summary_does_not_print_default_credentials(self):
        disallowed = "admin" + " / " + "admin"
        self.assertNotIn(disallowed, self.text)


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
        self.assertIn("kubectl_error_is_exact_not_found", body)
        self.assertIn('"sonarqube-admin-secret"', body)
        self.assertIn('default "admin"', body)
        self.assertIn("Failed to read the SonarQube admin password", body)
        self.assertIn("/api/settings/values", body)
        self.assertIn("/api/settings/list_definitions", body)
        self.assertIn("sonarqube_settings_match", body)
        self.assertIn("sonarqube_setting_definition_present", body)
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
        definitions = '{"definitions":[{"key":"sonar.auth.saml.certificate.secured","type":"PASSWORD"}]}'
        no_definitions = '{"definitions":[]}'
        self.assertEqual(_run_nu(f"sonarqube_setting_definition_present '{definitions}' 'sonar.auth.saml.certificate.secured'"), "true")
        self.assertEqual(_run_nu(f"sonarqube_setting_definition_present '{no_definitions}' 'sonar.auth.saml.certificate.secured'"), "false")
        self.assertEqual(_run_nu("sonarqube_http_status_matches 0 '204' '204'"), "true")
        self.assertEqual(_run_nu("sonarqube_http_status_matches 0 '302' '204'"), "false")
        self.assertEqual(_run_nu("sonarqube_http_status_matches 7 '204' '204'"), "false")

    def test_sonarqube_http_runs_in_cluster_without_host_dns_dependency(self):
        body = _func_body(self.text, "configure_sonarqube")
        self.assertIn("sonarqube-sonarqube.code-quality.svc.cluster.local:9000", body)
        self.assertIn("keycloak.keycloak.svc.cluster.local:8080", body)
        self.assertIn("exec -i -n code-quality $sonar_pod -c sonarqube -- curl", body)
        self.assertNotIn("$sonar_auth_config | curl", body)

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
        self.assertNotIn("--vertical", body, "the pinned Gitea auth list command has no --vertical flag")
        self.assertIn("su git -c 'gitea admin auth list'", body)
        self.assertIn("$gitea_token | kubectl", body)
        self.assertIn("curl --config -", body)
        self.assertIn("gitea-bootstrap-token", body)
        self.assertIn("persist_gitea_bootstrap_token", body)
        self.assertNotIn("/dev/stdin", body)
        self.assertIn("/api/v1/orgs/DigiOrg", body)
        self.assertNotIn("tea login", body)
        self.assertNotIn('curl -fsSk -H "Authorization: token ***"', body)

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

    def test_gitea_membership_get_uses_exact_http_200(self):
        body = _func_body(self.text, "configure_gitea")
        self.assertIn('($admin_check.stdout | str trim) == "200"', body)
        self.assertIn('($dev_check.stdout | str trim) == "200"', body)

    def test_generated_platform_secrets_are_reused_on_resume(self):
        body = _func_body(self.text, "create_platform_namespaces_secrets")
        self.assertIn("secret_value_or_default", body)
        for key in (
            "POSTGRES_PASSWORD", "KEYCLOAK_DB_PASSWORD", "BACKSTAGE_DB_PASSWORD",
            "GITEA_DB_PASSWORD", "SONARQUBE_DB_PASSWORD", "HARBOR_DB_PASSWORD",
        ):
            self.assertIn(key, body)

    def test_harbor_oidc_secret_key_is_consistent_across_clean_and_resume_owners(self):
        body = _func_body(self.text, "create_platform_namespaces_secrets")
        self.assertIn(
            'secret_value_or_legacy_key_or_default "harbor" "harbor-oidc-secret" "OIDC_CLIENT_SECRET" "client-secret"',
            body,
        )
        self.assertIn("persist_harbor_oidc_secret $harbor_oidc_secret", body)
        self.assertNotIn("--from-literal=OIDC_CLIENT_SECRET=($harbor_oidc_secret)", body)
        secret_manifest = _read(os.path.join(
            REPO_ROOT, "platform", "base", "harbor", "harbor-oidc-secret.yaml"
        ))
        config_job = _read(os.path.join(
            REPO_ROOT, "platform", "base", "harbor", "harbor-oidc-config-job.yaml"
        ))
        self.assertIn("OIDC_CLIENT_SECRET", secret_manifest)
        self.assertIn("key: OIDC_CLIENT_SECRET", config_job)

    def test_all_secret_fallbacks_use_exact_notfound_classifier(self):
        self.assertIn("kubectl_error_is_exact_not_found", _func_body(self.text, "secret_value_or_default"))
        self.assertIn("kubectl_error_is_exact_not_found", _func_body(self.text, "configure_gitea"))
        self.assertIn("kubectl_error_is_exact_not_found", _func_body(self.text, "configure_sonarqube"))

    def test_single_replica_gitea_uses_recreate_strategy_for_shared_data(self):
        values_path = os.path.join(REPO_ROOT, "platform", "base", "gitea", "values.yaml")
        values = yaml.safe_load(_read(values_path))
        self.assertEqual(values.get("replicaCount"), 1)
        self.assertEqual(values.get("strategy", {}).get("type"), "Recreate")


class SecretResumeStabilityTest(unittest.TestCase):
    """Generated values are reused; only exact NotFound permits a fallback."""

    def _run(self, scenario, override=""):
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "kubectl")
            with open(fake, "w", encoding="utf-8") as fh:
                fh.write(
                    "#!/usr/bin/env python3\n"
                    "import base64, os, sys\n"
                    "scenario = os.environ['FAKE_SCENARIO']\n"
                    "if scenario == 'existing':\n"
                    "    print(base64.b64encode(b'existing-value').decode(), end='')\n"
                    "elif scenario == 'notfound':\n"
                    "    print('Error from server (NotFound): secrets \\\"sample\\\" not found', file=sys.stderr); sys.exit(1)\n"
                    "elif scenario == 'namespace_notfound':\n"
                    "    print('Error from server (NotFound): namespaces \\\"testns\\\" not found', file=sys.stderr); sys.exit(1)\n"
                    "elif scenario == 'wrong_name_notfound':\n"
                    "    print('Error from server (NotFound): secrets \\\"other\\\" not found', file=sys.stderr); sys.exit(1)\n"
                    "elif scenario == 'mixed_forbidden':\n"
                    "    print('Error from server (Forbidden): audit context: secrets \\\"sample\\\" not found', file=sys.stderr); sys.exit(1)\n"
                    "else:\n"
                    "    print('forbidden', file=sys.stderr); sys.exit(1)\n"
                )
            os.chmod(fake, 0o755)
            env = os.environ.copy()
            env["PATH"] = tmp + os.pathsep + env.get("PATH", "")
            env["FAKE_SCENARIO"] = scenario
            env["TEST_OVERRIDE"] = override
            return subprocess.run(
                ["nu", "-c", f"source {SETUP}; secret_value_or_default testns sample TOKEN $env.TEST_OVERRIDE fallback-value"],
                capture_output=True, text=True, timeout=10, env=env,
            )

    def test_existing_value_is_reused(self):
        result = self._run("existing")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "existing-value")

    def test_exact_notfound_uses_fallback(self):
        result = self._run("notfound")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "fallback-value")
        namespace = self._run("namespace_notfound")
        self.assertEqual(namespace.returncode, 0, namespace.stderr)
        self.assertEqual(namespace.stdout.strip(), "fallback-value")

    def test_lookup_failure_is_fatal_and_override_wins(self):
        failed = self._run("forbidden")
        self.assertNotEqual(failed.returncode, 0)
        mixed = self._run("mixed_forbidden")
        self.assertNotEqual(mixed.returncode, 0)
        wrong_name = self._run("wrong_name_notfound")
        self.assertNotEqual(wrong_name.returncode, 0)
        overridden = self._run("forbidden", "explicit-value")
        self.assertEqual(overridden.returncode, 0, overridden.stderr)
        self.assertEqual(overridden.stdout.strip(), "explicit-value")


class HarborOidcLegacySecretMigrationTest(unittest.TestCase):
    """A pre-fix client-secret value migrates without rotation or disclosure."""

    def _run(self, scenario, override=""):
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "kubectl")
            with open(fake, "w", encoding="utf-8") as fh:
                fh.write(
                    "#!/usr/bin/env python3\n"
                    "import base64, os, sys\n"
                    "scenario = os.environ['FAKE_SCENARIO']\n"
                    "args = ' '.join(sys.argv[1:])\n"
                    "if scenario == 'missing':\n"
                    "    print('Error from server (NotFound): secrets \\\"harbor-oidc-secret\\\" not found', file=sys.stderr); sys.exit(1)\n"
                    "if 'OIDC_CLIENT_SECRET' in args and scenario == 'canonical':\n"
                    "    print(base64.b64encode(b'canonical-value').decode(), end=''); sys.exit(0)\n"
                    "if 'client-secret' in args and scenario == 'legacy':\n"
                    "    print(base64.b64encode(b'legacy-value').decode(), end=''); sys.exit(0)\n"
                    "sys.exit(0)\n"
                )
            os.chmod(fake, 0o755)
            env = os.environ.copy()
            env["PATH"] = tmp + os.pathsep + env.get("PATH", "")
            env["FAKE_SCENARIO"] = scenario
            env["TEST_OVERRIDE"] = override
            return subprocess.run(
                ["nu", "-c", (
                    f"source {SETUP}; "
                    "secret_value_or_legacy_key_or_default harbor harbor-oidc-secret "
                    "OIDC_CLIENT_SECRET client-secret $env.TEST_OVERRIDE fallback-value"
                )],
                capture_output=True, text=True, timeout=10, env=env,
            )

    def test_canonical_value_wins(self):
        result = self._run("canonical")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "canonical-value")

    def test_legacy_value_is_reused_for_one_time_migration(self):
        result = self._run("legacy")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "legacy-value")

    def test_missing_secret_uses_fallback(self):
        result = self._run("missing")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "fallback-value")

    def test_existing_secret_without_either_key_is_fatal(self):
        result = self._run("neither")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no usable", result.stderr)

    def test_explicit_override_wins_without_lookup(self):
        result = self._run("neither", "rotated-value")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "rotated-value")

    def _run_persist(self, scenario):
        sentinel = "harbor-oidc-never-in-argv"
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "kubectl")
            argv_log = os.path.join(tmp, "argv")
            manifest_log = os.path.join(tmp, "manifest.json")
            with open(fake, "w", encoding="utf-8") as fh:
                fh.write(
                    "#!/usr/bin/env python3\n"
                    "import json, os, sys\n"
                    "args = sys.argv[1:]\n"
                    "with open(os.environ['ARGV_LOG'], 'a', encoding='utf-8') as log: log.write(json.dumps(args) + '\\n')\n"
                    "scenario = os.environ['FAKE_SCENARIO']\n"
                    "if 'apply' in args:\n"
                    "    data = sys.stdin.read()\n"
                    "    if scenario == 'apply_failure': sys.exit(1)\n"
                    "    open(os.environ['MANIFEST_LOG'], 'w', encoding='utf-8').write(data); sys.exit(0)\n"
                    "if 'get' in args:\n"
                    "    if scenario == 'readback_failure': sys.exit(1)\n"
                    "    obj = json.load(open(os.environ['MANIFEST_LOG'], encoding='utf-8'))\n"
                    "    print('d3Jvbmc=' if scenario == 'readback_mismatch' else obj['data']['OIDC_CLIENT_SECRET'], end=''); sys.exit(0)\n"
                    "sys.exit(2)\n"
                )
            os.chmod(fake, 0o755)
            env = os.environ.copy()
            env["PATH"] = tmp + os.pathsep + env.get("PATH", "")
            env["ARGV_LOG"] = argv_log
            env["MANIFEST_LOG"] = manifest_log
            env["FAKE_SCENARIO"] = scenario
            env["TEST_SECRET"] = sentinel
            result = subprocess.run(
                ["nu", "-c", f"source {SETUP}; persist_harbor_oidc_secret $env.TEST_SECRET"],
                capture_output=True, text=True, timeout=10, env=env,
            )
            argv = ""
            if os.path.exists(argv_log):
                with open(argv_log, encoding="utf-8") as fh:
                    argv = fh.read()
            manifest = None
            if os.path.exists(manifest_log):
                with open(manifest_log, encoding="utf-8") as fh:
                    manifest = json.load(fh)
            return result, sentinel, argv, manifest

    def test_persist_uses_stdin_not_argv_and_verifies_readback(self):
        result, sentinel, argv, manifest = self._run_persist("success")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(sentinel, argv)
        self.assertNotIn(sentinel, result.stdout + result.stderr)
        self.assertIsNotNone(manifest)
        assert manifest is not None
        self.assertEqual(base64.b64decode(manifest["data"]["OIDC_CLIENT_SECRET"]).decode(), sentinel)
        self.assertNotIn("/dev/stdin", _func_body(_read(SETUP), "persist_harbor_oidc_secret"))

    def test_persist_apply_and_readback_failures_are_fatal(self):
        for scenario in ("apply_failure", "readback_failure", "readback_mismatch"):
            with self.subTest(scenario=scenario):
                result, _, _, _ = self._run_persist(scenario)
                self.assertNotEqual(result.returncode, 0)


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

    def _run_secret_transport(self, scenario):
        sentinel = "sentinel-kubernetes-secret-token-never-in-argv"
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "kubectl")
            argv_log = os.path.join(tmp, "kubectl-argv")
            manifest_log = os.path.join(tmp, "manifest.json")
            with open(fake, "w", encoding="utf-8") as fh:
                fh.write(
                    "#!/usr/bin/env python3\n"
                    "import json, os, sys\n"
                    "args = sys.argv[1:]\n"
                    "with open(os.environ['KUBECTL_ARGV_LOG'], 'a', encoding='utf-8') as log: "
                    "log.write(json.dumps(args) + '\\n')\n"
                    "scenario = os.environ.get('FAKE_SCENARIO', 'success')\n"
                    "if 'apply' in args:\n"
                    "    data = sys.stdin.read()\n"
                    "    if scenario == 'apply_failure': sys.exit(1)\n"
                    "    open(os.environ['MANIFEST_LOG'], 'w', encoding='utf-8').write(data)\n"
                    "    print('secret/gitea-bootstrap-token configured')\n"
                    "elif 'get' in args:\n"
                    "    if scenario == 'readback_failure': sys.exit(1)\n"
                    "    obj = json.load(open(os.environ['MANIFEST_LOG'], encoding='utf-8'))\n"
                    "    value = obj['data']['token']\n"
                    "    print('d3Jvbmc=' if scenario == 'readback_mismatch' else value, end='')\n"
                    "else:\n"
                    "    sys.exit(2)\n"
                )
            os.chmod(fake, 0o755)
            env = os.environ.copy()
            env["PATH"] = tmp + os.pathsep + env.get("PATH", "")
            env["KUBECTL_ARGV_LOG"] = argv_log
            env["MANIFEST_LOG"] = manifest_log
            env["FAKE_SCENARIO"] = scenario
            env["TEST_TOKEN"] = sentinel
            result = subprocess.run(
                ["nu", "-c", f"source {SETUP}; persist_gitea_bootstrap_token $env.TEST_TOKEN"],
                capture_output=True, text=True, env=env, timeout=10,
            )
            argv = ""
            if os.path.exists(argv_log):
                with open(argv_log, encoding="utf-8") as fh:
                    argv = fh.read()
            manifest = None
            if os.path.exists(manifest_log):
                with open(manifest_log, encoding="utf-8") as fh:
                    manifest = yaml.safe_load(fh)
            return result, sentinel, argv, manifest

    def test_kubernetes_secret_manifest_is_cross_platform_and_verified(self):
        result, sentinel, argv, manifest = self._run_secret_transport("success")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(sentinel, argv)
        self.assertNotIn("/dev/stdin", _func_body(_read(SETUP), "persist_gitea_bootstrap_token"))
        self.assertNotIn(sentinel, result.stdout + result.stderr)
        self.assertIsNotNone(manifest)
        assert manifest is not None
        self.assertEqual(manifest["metadata"]["name"], "gitea-bootstrap-token")
        self.assertEqual(base64.b64decode(manifest["data"]["token"]).decode(), sentinel)

    def test_kubernetes_secret_apply_failure_is_fatal(self):
        result, _, _, _ = self._run_secret_transport("apply_failure")
        self.assertNotEqual(result.returncode, 0)

    def test_kubernetes_secret_readback_failure_and_mismatch_are_fatal(self):
        for scenario in ("readback_failure", "readback_mismatch"):
            with self.subTest(scenario=scenario):
                result, _, _, _ = self._run_secret_transport(scenario)
                self.assertNotEqual(result.returncode, 0)


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
