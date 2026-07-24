#!/usr/bin/env python3
"""Regression contracts discovered by Issue #285 clean bootstrap run #6."""
from pathlib import Path
import json
import os
import re
import subprocess
import tempfile
import time
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SETUP = (ROOT / "scripts/local-setup.nu").read_text(encoding="utf-8")
NATS_VALUES = yaml.safe_load((ROOT / "platform/base/nats/values.yaml").read_text(encoding="utf-8"))
NACK_APP = yaml.safe_load((ROOT / "apps/platform/nats-jetstream-controller.yaml").read_text(encoding="utf-8"))
HARBOR_APP = yaml.safe_load((ROOT / "apps/platform/crossplane-harbor-bootstrap.yaml").read_text(encoding="utf-8"))


def func_body(name: str) -> str:
    start = SETUP.index(f"def {name} [")
    next_def = SETUP.find("\ndef ", start + 5)
    return SETUP[start:] if next_def < 0 else SETUP[start:next_def]


class NatsControllerAuthenticationTest(unittest.TestCase):
    SECRET = "nats-jetstream-controller-nkey"

    def test_nats_server_uses_dedicated_least_privilege_public_nkey(self):
        merge = NATS_VALUES["config"]["merge"]
        users = merge["accounts"]["APP"]["users"]
        controller = next(u for u in users if "nkey" in u)
        self.assertEqual(controller["nkey"], "<< $NATS_JSC_NKEY_PUBLIC >>")
        self.assertEqual(controller["permissions"]["publish"], ["$JS.API.>"])
        self.assertEqual(controller["permissions"]["subscribe"], ["_INBOX.>"])
        item = NATS_VALUES["container"]["env"]["NATS_JSC_NKEY_PUBLIC"]
        ref = item["valueFrom"]["secretKeyRef"]
        self.assertEqual(ref, {"name": self.SECRET, "key": "public.nk"})

    def test_nack_mounts_private_seed_from_matching_secret(self):
        values = yaml.safe_load(NACK_APP["spec"]["source"]["helm"]["values"])
        ref = values["jetstream"]["nats"]["nkey"]["secret"]
        self.assertEqual(ref, {"name": self.SECRET, "key": "seed.nk"})
        self.assertNotIn("password", NACK_APP["spec"]["source"]["helm"]["values"].lower())

    def test_nack_cluster_role_cannot_read_kubernetes_secrets(self):
        values = yaml.safe_load(NACK_APP["spec"]["source"]["helm"]["values"])
        rules = yaml.safe_load(values["rbacRules"])["rules"]
        secret_rules = [
            rule for rule in rules
            if rule.get("apiGroups") == [""] and "secrets" in rule.get("resources", [])
        ]
        self.assertEqual(secret_rules, [])
        jetstream_rules = [rule for rule in rules if rule.get("apiGroups") == ["jetstream.nats.io"]]
        self.assertEqual(len(jetstream_rules), 1)
        self.assertIn("streams", jetstream_rules[0]["resources"])
        self.assertIn("consumers", jetstream_rules[0]["resources"])

    def test_bootstrap_generates_and_validates_nkey_with_pinned_tool_image(self):
        body = func_body("ensure_nats_jetstream_controller_nkey")
        self.assertIn("natsio/nats-box:0.19.2@sha256:8031d190c7ee24081f3f27cc939fb647a1eeb29ebb5c60fef9b5b6c7a846d6a2", body)
        self.assertIn("nk -gen user", body)
        self.assertIn("nk -inkey /dev/stdin -pubout", body)
        self.assertIn('persist_opaque_secret "messaging" "nats-jetstream-controller-nkey" "seed.nk"', body)
        self.assertIn('persist_opaque_secret "messaging" "nats-jetstream-controller-nkey" "public.nk"', body)
        self.assertIn("ensure_nats_jetstream_controller_nkey", func_body("create_platform_namespaces_secrets"))
        self.assertIn("if ($public | is-empty)", body)
        self.assertIn('persist_opaque_secret "messaging" $name "public.nk" $derived', body)


class CrossplaneHarborOrderingTest(unittest.TestCase):
    def test_harbor_bootstrap_is_manual_and_gated_after_provider_configs(self):
        self.assertNotIn("automated", HARBOR_APP["spec"].get("syncPolicy", {}))
        gated = SETUP[SETUP.index("let gated_apps"):]
        self.assertLess(gated.index('"crossplane-provider-configs"'), gated.index('"crossplane-harbor-bootstrap"'))
        self.assertLess(gated.index('"crossplane-harbor-bootstrap"'), gated.index('"crossplane-xrds"'))

    def test_gate_fails_closed_on_provider_revision_and_request_crd(self):
        body = func_body("wait_for_provider_http_ready")
        self.assertIn("provider-http", body)
        self.assertIn("ProviderRevision", body)
        self.assertIn("RevisionHealthy", body)
        self.assertIn("RuntimeHealthy", body)
        self.assertIn("requests.http.crossplane.io", body)
        self.assertIn("condition=Established", body)
        loop = func_body("sync_gated_apps_for_local_dev")
        self.assertLess(loop.index("wait_for_provider_http_ready"), loop.index("kubectl patch application $app"))

    def test_harbor_bootstrap_waits_for_ca_before_sync_without_depending_on_harbor_app(self):
        """Issue #285 runtime-v10: `crossplane-harbor-bootstrap` was promoted
        by this gated loop before Phase 3 (main up) had copied the
        digiorg.local CA into crossplane-system, so `Request
        harbor-crossplane-system-robot` went OutOfSync/Synced=False with
        ReconcileError: missing crossplane-system/digiorg-local-ca. The
        branch that gates this Application must wait only for cert-manager
        itself (never the not-yet-synced Harbor Application -- that would
        deadlock on Harbor's own PostSync hooks) and its Certificate, then
        copy the CA, then run the existing provider-http gate, in that exact
        order, before this Application is patched to sync."""
        body = func_body("sync_gated_apps_for_local_dev")
        branch_start = body.index('if $app == "crossplane-harbor-bootstrap"')
        branch_end = body.index("mut exists = false", branch_start)
        branch = body[branch_start:branch_end]

        self.assertIn("wait_for_configuration_dependencies", branch)
        self.assertIn('copy_digiorg_local_ca_to_namespace "crossplane-system"', branch)
        self.assertIn("wait_for_provider_http_ready", branch)

        dependency_pos = branch.index("wait_for_configuration_dependencies")
        copy_pos = branch.index('copy_digiorg_local_ca_to_namespace "crossplane-system"')
        provider_pos = branch.index("wait_for_provider_http_ready")
        self.assertLess(
            dependency_pos, copy_pos,
            "must wait for cert-manager/its Certificate before copying the CA",
        )
        self.assertLess(
            copy_pos, provider_pos,
            "must copy the CA before the existing provider-http gate runs",
        )

        dependency_call = branch[dependency_pos:copy_pos]
        self.assertIn('"cert-manager"', dependency_call)
        self.assertIn('"digiorg-local-ca"', dependency_call)
        self.assertNotIn(
            '"harbor"', dependency_call,
            "must never wait on the not-yet-synced Harbor Application -- "
            "that would deadlock on Harbor's own PostSync hooks",
        )


    def test_gate_fails_fast_and_redacts_deterministic_kubectl_failures(self):
        scenarios = {
            "forbidden": (1, "", "Forbidden transport-secret-sentinel"),
            "credential-helper": (1, "", "error: executable credential-helper not found transport-secret-sentinel"),
            "malformed": (0, "{not-json", ""),
        }
        for name, (code, stdout, stderr) in scenarios.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                fake = Path(tmp) / "kubectl"
                fake.write_text(
                    "#!/usr/bin/env python3\n"
                    "import sys\n"
                    f"sys.stdout.write({stdout!r})\n"
                    f"sys.stderr.write({stderr!r})\n"
                    f"sys.exit({code})\n",
                    encoding="utf-8",
                )
                fake.chmod(0o755)
                env = os.environ.copy()
                env["PATH"] = tmp + os.pathsep + env.get("PATH", "")
                started = time.monotonic()
                result = subprocess.run(
                    ["nu", "-c", f"source {ROOT / 'scripts/local-setup.nu'}; wait_for_provider_http_ready"],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=5,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertLess(time.monotonic() - started, 3)
                self.assertNotIn("transport-secret-sentinel", result.stdout + result.stderr)

        not_found = subprocess.run(
            [
                "nu", "-c",
                f"source {ROOT / 'scripts/local-setup.nu'}; "
                "kubectl_result_is_not_found {stdout: '', stderr: 'Error from server (NotFound): provider-http not found'}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(not_found.returncode, 0, not_found.stderr)
        self.assertEqual(not_found.stdout.strip(), "true")


class ProviderHttpRawApiGateTest(unittest.TestCase):
    """PR #286: `kubectl get provider`/`providerrevision` resolves through
    discovery/RESTMapper, which can be stale immediately after the package
    CRDs install and then fails with a generic (non-NotFound) error that
    never recovers -- this is exactly what stalled runtime-v8 stdout5 right
    after crossplane-provider-configs went Healthy. The gate must instead
    hit the pinned pkg.crossplane.io/v1 raw API directly.

    runtime-v9: a ProviderRevision never carries a bare `Healthy` condition
    -- Crossplane v2.3.3's revision reconciler
    (internal/controller/pkg/revision/reconciler.go) only ever marks
    `RevisionHealthy`/`RuntimeHealthy` on the revision itself; the aggregate
    `Healthy` condition is written to the parent Provider by the manager
    reconciler's `PackageHealth()` (apis/pkg/v1/conditions.go). Checking for
    `Healthy` on the revision therefore never matched anything and stalled
    every clean bootstrap for the full 180-attempt/360s timeout."""

    FAKE_KUBECTL = (
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['KUBECTL_ARGV_LOG'], 'a', encoding='utf-8') as log:\n"
        "    log.write(json.dumps(args) + '\\n')\n"
        "scenario = os.environ.get('FAKE_SCENARIO', 'healthy')\n"
        "counter_path = os.environ['CALL_COUNTER']\n"
        "def bump(key):\n"
        "    counts = json.loads(open(counter_path, encoding='utf-8').read()) if os.path.exists(counter_path) else {}\n"
        "    counts[key] = counts.get(key, 0) + 1\n"
        "    open(counter_path, 'w', encoding='utf-8').write(json.dumps(counts))\n"
        "    return counts[key]\n"
        "if 'wait' in args:\n"
        "    sys.exit(0)\n"
        "if '--raw' not in args:\n"
        "    sys.exit(2)\n"
        "path = args[args.index('--raw') + 1]\n"
        "if path.startswith('/apis/pkg.crossplane.io/v1/providers/'):\n"
        "    n = bump('provider')\n"
        "    if scenario == 'provider_not_found_then_success' and n < 3:\n"
        "        sys.stderr.write('Error from server (NotFound): providers.pkg.crossplane.io \"provider-http\" not found\\n')\n"
        "        sys.exit(1)\n"
        "    if scenario == 'provider_forbidden':\n"
        "        sys.stderr.write('Error from server (Forbidden): cannot get providers.pkg.crossplane.io: Authorization: Bearer ' + os.environ['SENTINEL'] + '\\n')\n"
        "        sys.exit(1)\n"
        "    if scenario == 'credential_helper_not_found':\n"
        "        sys.stderr.write('error: getting credentials: exec: executable credential-helper not found: secret=' + os.environ['SENTINEL'] + '\\n')\n"
        "        sys.exit(1)\n"
        "    if scenario == 'provider_huge_stderr':\n"
        "        sys.stderr.write('z' * 5000)\n"
        "        sys.exit(1)\n"
        "    if scenario == 'provider_malformed_json':\n"
        "        sys.stdout.write('{not-json')\n"
        "        sys.exit(0)\n"
        "    if scenario == 'invalid_revision_name':\n"
        "        sys.stdout.write(json.dumps({'status': {'currentRevision': '../../etc/passwd'}}))\n"
        "        sys.exit(0)\n"
        "    if scenario == 'oversized_revision_label':\n"
        "        sys.stdout.write(json.dumps({'status': {'currentRevision': 'a' * 64}}))\n"
        "        sys.exit(0)\n"
        "    unhealthy_revision_scenarios = ('missing_runtime_healthy', 'false_runtime_healthy', 'missing_revision_healthy', 'false_revision_healthy')\n"
        "    if scenario in unhealthy_revision_scenarios and n >= 2:\n"
        "        sys.stderr.write('Error from server (Forbidden): cannot get providers.pkg.crossplane.io: revision-unhealthy-retry-sentinel\\n')\n"
        "        sys.exit(1)\n"
        "    sys.stdout.write(json.dumps({'status': {'currentRevision': 'provider-http-abc123def456'}}))\n"
        "    sys.exit(0)\n"
        "if path.startswith('/apis/pkg.crossplane.io/v1/providerrevisions/'):\n"
        "    n = bump('revision')\n"
        "    if scenario == 'revision_not_found_then_success' and n < 3:\n"
        "        sys.stderr.write('Error from server (NotFound): providerrevisions.pkg.crossplane.io \"x\" not found\\n')\n"
        "        sys.exit(1)\n"
        "    if scenario == 'missing_runtime_healthy':\n"
        "        sys.stdout.write(json.dumps({'spec': {'desiredState': 'Active'}, 'status': {'conditions': [{'type': 'RevisionHealthy', 'status': 'True'}]}}))\n"
        "        sys.exit(0)\n"
        "    if scenario == 'false_runtime_healthy':\n"
        "        sys.stdout.write(json.dumps({'spec': {'desiredState': 'Active'}, 'status': {'conditions': [{'type': 'RevisionHealthy', 'status': 'True'}, {'type': 'RuntimeHealthy', 'status': 'False'}]}}))\n"
        "        sys.exit(0)\n"
        "    if scenario == 'missing_revision_healthy':\n"
        "        sys.stdout.write(json.dumps({'spec': {'desiredState': 'Active'}, 'status': {'conditions': [{'type': 'RuntimeHealthy', 'status': 'True'}]}}))\n"
        "        sys.exit(0)\n"
        "    if scenario == 'false_revision_healthy':\n"
        "        sys.stdout.write(json.dumps({'spec': {'desiredState': 'Active'}, 'status': {'conditions': [{'type': 'RevisionHealthy', 'status': 'False'}, {'type': 'RuntimeHealthy', 'status': 'True'}]}}))\n"
        "        sys.exit(0)\n"
        "    sys.stdout.write(json.dumps({'spec': {'desiredState': 'Active'}, 'status': {'conditions': [{'type': 'RevisionHealthy', 'status': 'True'}, {'type': 'RuntimeHealthy', 'status': 'True'}]}}))\n"
        "    sys.exit(0)\n"
        "sys.exit(2)\n"
    )

    def _run(self, scenario, sentinel="sentinel-http-credential-value", timeout=15):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "kubectl"
            fake.write_text(self.FAKE_KUBECTL, encoding="utf-8")
            fake.chmod(0o755)
            argv_log = Path(tmp) / "argv.log"
            counter = Path(tmp) / "counter.json"
            env = os.environ.copy()
            env["PATH"] = tmp + os.pathsep + env.get("PATH", "")
            env["KUBECTL_ARGV_LOG"] = str(argv_log)
            env["CALL_COUNTER"] = str(counter)
            env["FAKE_SCENARIO"] = scenario
            env["SENTINEL"] = sentinel
            started = time.monotonic()
            result = subprocess.run(
                ["nu", "-c", f"source {ROOT / 'scripts/local-setup.nu'}; wait_for_provider_http_ready"],
                capture_output=True,
                text=True,
                env=env,
                timeout=timeout,
            )
            elapsed = time.monotonic() - started
            calls = [json.loads(line) for line in argv_log.read_text(encoding="utf-8").splitlines()] if argv_log.exists() else []
            return result, elapsed, calls

    def test_uses_exact_raw_v1_paths_for_provider_and_revision(self):
        result, _elapsed, calls = self._run("healthy")
        self.assertEqual(result.returncode, 0, result.stderr)
        raw_paths = [c[c.index("--raw") + 1] for c in calls if "--raw" in c]
        self.assertIn("/apis/pkg.crossplane.io/v1/providers/provider-http", raw_paths)
        self.assertIn(
            "/apis/pkg.crossplane.io/v1/providerrevisions/provider-http-abc123def456",
            raw_paths,
        )
        for call in calls:
            if "get" in call:
                self.assertIn("--raw", call, "must never resolve provider/providerrevision via discovery/RESTMapper")
        wait_calls = [c for c in calls if c and c[0] == "wait"]
        self.assertEqual(
            wait_calls,
            [["wait", "--for=condition=Established", "crd/requests.http.crossplane.io", "--timeout=5s"]],
        )

    def test_provider_not_found_retries_then_succeeds(self):
        result, elapsed, _calls = self._run("provider_not_found_then_success")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(elapsed, 3.5)

    def test_revision_not_found_retries_then_succeeds(self):
        result, elapsed, _calls = self._run("revision_not_found_then_success")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(elapsed, 3.5)

    def test_missing_or_false_provider_revision_conditions_never_reach_crd_success(self):
        """Neither a missing nor a False RevisionHealthy/RuntimeHealthy condition
        may ever be treated as ready. The fake fails the *next* provider read
        once the gate has observed one unhealthy revision, so each scenario
        resolves in a single retry cycle instead of exhausting the real
        180-attempt/360s loop."""
        scenarios = (
            "missing_runtime_healthy",
            "false_runtime_healthy",
            "missing_revision_healthy",
            "false_revision_healthy",
        )
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                result, elapsed, calls = self._run(scenario, timeout=10)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertLess(
                    elapsed, 5,
                    "must fail closed after one retry rather than exhausting the 180-attempt/360s loop",
                )
                wait_calls = [c for c in calls if c and c[0] == "wait"]
                self.assertEqual(
                    wait_calls, [],
                    "must never reach the CRD Established wait when provider-http is not fully healthy",
                )

    def test_provider_forbidden_fails_fast_without_leaking_credential(self):
        result, elapsed, _calls = self._run("provider_forbidden")
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(elapsed, 3)
        self.assertNotIn("sentinel-http-credential-value", result.stdout + result.stderr)

    def test_credential_helper_not_found_fails_fast_without_leaking_credential(self):
        result, elapsed, _calls = self._run("credential_helper_not_found")
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(elapsed, 3)
        self.assertNotIn("sentinel-http-credential-value", result.stdout + result.stderr)

    def test_malformed_json_fails_fast(self):
        result, elapsed, _calls = self._run("provider_malformed_json")
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(elapsed, 3)

    def test_invalid_revision_name_fails_closed_without_querying_revision(self):
        for scenario in ("invalid_revision_name", "oversized_revision_label"):
            with self.subTest(scenario=scenario):
                result, elapsed, calls = self._run(scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertLess(elapsed, 3)
                raw_paths = [c[c.index("--raw") + 1] for c in calls if "--raw" in c]
                self.assertFalse(any("providerrevisions" in p for p in raw_paths))

    def test_fatal_diagnostic_is_bounded_even_without_a_redactable_pattern(self):
        result, elapsed, _calls = self._run("provider_huge_stderr")
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(elapsed, 3)
        self.assertLess(len(result.stderr), 2000)


class GiteaServiceIdentityTest(unittest.TestCase):
    def test_bool_flag_uses_equals_form(self):
        body = func_body("gitea_create_user_random_password")
        self.assertIn("--must-change-password=false", body)
        self.assertNotIn("--must-change-password false", body)

    def test_existing_service_users_have_must_change_unset_on_resume(self):
        helper = func_body("gitea_unset_service_user_must_change_password")
        self.assertIn("admin user must-change-password --unset", helper)
        for function, username in (
            ("configure_crossplane_gitea_credentials", "crossplane-provisioner"),
            ("configure_argocd_gitea_access", "argocd-reader"),
            ("configure_backstage_gitea_publisher", "backstage-appclaim-publisher"),
        ):
            body = func_body(function)
            self.assertIn(f'gitea_unset_service_user_must_change_password $gitea_pod "{username}"', body)


class SecretMetadataTest(unittest.TestCase):
    def test_new_credential_writers_avoid_client_side_apply_and_scrub_annotation(self):
        scrubber = func_body("scrub_secret_last_applied_annotation")
        self.assertIn("kubectl.kubernetes.io/last-applied-configuration-", scrubber)
        self.assertIn("jsonpath='{.metadata.annotations.kubectl", scrubber)

        opaque = func_body("persist_opaque_secret")
        self.assertIn("get secret", opaque)
        self.assertIn("--ignore-not-found", opaque)
        self.assertIn("data: {($key): $encoded}", opaque)
        self.assertIn("apply --server-side", opaque)
        self.assertIn("digiorg-bootstrap-secret-", opaque)
        self.assertIn("--field-manager $field_manager", opaque)
        self.assertNotRegex(opaque, r"(?<!server-side )apply -f -")
        self.assertGreaterEqual(opaque.count("scrub_secret_last_applied_annotation"), 2)
        self.assertLess(
            opaque.index("scrub_secret_last_applied_annotation"),
            opaque.index("apply --server-side"),
            "legacy last-applied annotation must be scrubbed before target-only SSA",
        )

        repo = func_body("persist_argocd_repo_secret")
        self.assertIn("apply --server-side", repo)
        self.assertIn("--field-manager", repo)
        self.assertNotRegex(repo, r"(?<!server-side )apply -f -")
        self.assertIn("scrub_secret_last_applied_annotation", repo)


if __name__ == "__main__":
    unittest.main(verbosity=2)
