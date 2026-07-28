#!/usr/bin/env python3
"""Gated Argo sync must retry only transient errors, and say why (#279).

Confirmed runtime evidence: the first gated Application (external-secrets)
failed with

    ComparisonError: Failed to load target state: failed to generate manifest
    for source 1 of 2:
    rpc error: code = Unavailable desc = error reading from server: EOF

caused by argocd-repo-server's liveness restart severing the in-flight gRPC
manifest-generation call — not a real ESO/chart problem (the same source
rendered successfully moments later). ``scripts/local-setup.nu`` must:

  * classify this class of error (gRPC Unavailable/EOF, connection resets,
    transient DNS/network failures) as retryable, with bounded exponential
    backoff and a genuinely *fresh* sync operation (not re-reading the same
    stale ``Error`` operation forever);
  * classify deterministic manifest/resource/hook/policy errors as fatal —
    fail immediately, no retry;
  * print ``operationState.message`` and failed resource/hook messages either
    way.

This test drives the real ``is_retryable_sync_error`` function lifted straight
out of ``scripts/local-setup.nu`` via the actual Nushell interpreter (no mocks
of the classification logic itself), plus static structural checks for the
retry/backoff/diagnostics wiring since the rest of the function talks to a
real cluster.

Requires the `nu` binary (Nushell 0.114.1, matches docs/guides/platform-versions.md)::

    python3 platform/tests/test_gated_sync_retry_classification.py
"""
import os
import shutil
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SETUP = os.path.join(REPO_ROOT, "scripts", "local-setup.nu")

# (operationState.message, expected classification) confirmed from issue #279
# diagnostics plus representative deterministic failures that must NOT retry.
CASES = [
    (
        "ComparisonError: Failed to load target state: failed to generate "
        "manifest for source 1 of 2:\nrpc error: code = Unavailable desc = "
        "error reading from server: EOF",
        True,
    ),
    ("rpc error: code = Unavailable desc = transport is closing", True),
    ("failed to list refs: EOF", True),
    ("lookup github.com on 10.96.0.10:53: server misbehaving", True),
    ("repository checkout dial tcp: connect: connection refused", True),
    ("repo-server manifest generation context deadline exceeded", True),
    (
        "one or more synchronization tasks completed unsuccessfully\n"
        "Prometheus/prometheus: shard 0: pod prometheus-0: containers with "
        "incomplete status: [init-config-reloader]",
        True,
    ),
    (
        "rpc error: code = Internal desc = error reading from server: EOF",
        True,
    ),
    # stdout10 (Issue #285 fresh-cluster run): confirmed live evidence -- the
    # initial grafana sync failed with this exact Prometheus resource message,
    # and minutes later, with no intervention, Grafana was Synced/Healthy and
    # Prometheus was Available=True/Reconciled=True with every monitoring
    # container Ready and zero restarts. Same config-reloader startup race as
    # the "incomplete status: [init-config-reloader]" case above, just the
    # main-container wording instead of the init-container wording.
    (
        "one or more synchronization tasks completed unsuccessfully\n"
        "Prometheus/prometheus: shard 0: pod prometheus-0: containers with "
        "unready status: [prometheus config-reloader]",
        True,
    ),
    # Guards: an "unready status" message must NOT be accepted for an
    # unrelated container set, either alone or concatenated with the confirmed
    # Prometheus race. Argo combines all failed resource diagnostics before
    # classification, so an allowlisted failure must never mask another one.
    (
        "containers with unready status: [some-other-container]",
        False,
    ),
    (
        "Prometheus/prometheus: containers with unready status: "
        "[prometheus config-reloader]\n"
        "Deployment/other: containers with unready status: "
        "[some-other-container]",
        False,
    ),
    (
        "Prometheus/prometheus: containers with unready status: "
        "[prometheus config-reloader]\n"
        "Deployment/other: containers with unready status: "
        "[some-other-container",
        False,
    ),
    ("containers with unready status: []", False),
    ("containers with pending status: [some-other-container]", False,),
    (
        "rpc error: code = InvalidArgument desc = failed to unmarshal manifest: "
        "yaml: line 7: mapping values are not allowed here",
        False,
    ),
    ("rpc error: code = PermissionDenied desc = access denied", False),
    ("rpc error: code = Unauthenticated desc = missing credentials", False),
    ("rpc error: code = Unknown desc = failed to unmarshal manifest", False),
    ("helm template: YAML parse error: unexpected EOF", False),
    (
        "failed to apply: admission webhook validate.example.svc: dial tcp: "
        "connect: connection refused",
        False,
    ),
    (
        "rpc error: code = Internal desc = Manifest generation error (cached): "
        "helm template failed with exit status 1",
        False,
    ),
    (
        "one or more objects failed to apply, reason: Deployment.apps \"foo\" "
        "is invalid: spec.template.spec.containers: Required value",
        False,
    ),
    ("admission webhook \"validate.kyverno.svc\" denied the request", False),
    ("failed to sync: failed to run hook: BackoffLimitExceeded", False),
    (
        "containers with incomplete status: [init] hook failed: BackoffLimitExceeded",
        False,
    ),
    (
        "containers with incomplete status: [init] helm template failed with unexpected EOF",
        False,
    ),
    ("", False),
]


def _run_nu(expr: str) -> str:
    result = subprocess.run(
        ["nu", "-c", f"source {SETUP}; {expr}"],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise AssertionError(f"nu failed: {result.stderr}")
    return result.stdout.strip()


class RetryClassificationTest(unittest.TestCase):
    def test_classifier_matches_confirmed_cases(self):
        for message, expected in CASES:
            with self.subTest(message=message[:60]):
                escaped = message.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
                out = _run_nu(f'is_retryable_sync_error "{escaped}"')
                self.assertEqual(out, str(expected).lower(),
                                  f"message classified as {out}, expected {expected}")


class DiagnosticRedactionTest(unittest.TestCase):
    CASES = [
        ("password=super-secret-value", "super-secret-value"),
        ("token: token-value-123", "token-value-123"),
        ("api_key=key-123456", "key-123456"),
        ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload", "eyJhbGciOiJIUzI1NiJ9.payload"),
        ("Authorization: Basic YWRtaW46cGFzcw==", "YWRtaW46cGFzcw=="),
        ('{"token":"sentinel-json-value"}', "sentinel-json-value"),
        ("secret=sentinel-secret-value", "sentinel-secret-value"),
        ('password: "quoted value with spaces"', "quoted value with spaces"),
        ("request to https://admin:hunter2@harbor.example/api failed", "admin:hunter2"),
        ("Secret data: c2Vuc2l0aXZl", "c2Vuc2l0aXZl"),
    ]

    def test_sensitive_values_are_redacted_by_real_nushell(self):
        for message, sensitive in self.CASES:
            with self.subTest(message=message):
                escaped = message.replace("\\", "\\\\").replace('"', '\\"')
                out = _run_nu(f'redact_sync_diagnostic "{escaped}"')
                self.assertNotIn(sensitive, out)
                self.assertIn("[REDACTED]", out)

    def test_redacted_diagnostic_is_length_bounded(self):
        out = _run_nu(f'redact_sync_diagnostic "{"x" * 5000}"')
        self.assertLessEqual(len(out), 1024)


class RetryWiringStructureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(SETUP, encoding="utf-8") as fh:
            cls.text = fh.read()

    def test_bounded_retry_with_backoff(self):
        self.assertIn("max_operation_retries", self.text)
        self.assertRegex(self.text, r"1sec\s*\*|\*\s*1sec|2\s*\*\*\s*\$retry_count")

    def test_reissues_a_fresh_operation_on_retry(self):
        # The sync patch must live inside the same enclosing retry loop as the
        # backoff/retry-count bookkeeping, so a retry loops back and reissues
        # a brand-new `kubectl patch` (fresh operation) rather than re-polling
        # the same stale terminal operation.
        start = self.text.index("def sync_gated_apps_for_local_dev")
        end = self.text.index("\ndef ", start + 10)
        body = self.text[start:end]
        loop_start = body.index("loop {")
        patch_pos = body.index("kubectl patch application $app", loop_start)
        retry_incr_pos = body.index("$retry_count = $retry_count + 1", loop_start)
        self.assertLess(
            patch_pos, retry_incr_pos,
            "the sync patch must be inside the same retry `loop {}` as the "
            "backoff/retry increment so a retry reissues a fresh operation",
        )

    def test_prints_operation_state_message_and_resource_diagnostics(self):
        self.assertIn("operationState.message", self.text)
        self.assertIn("syncResult.resources", self.text)

    def test_classification_includes_resource_messages(self):
        start = self.text.index("def sync_gated_apps_for_local_dev")
        end = self.text.index("\ndef ", start + 10)
        body = self.text[start:end]
        self.assertIn("classification_text", body)
        self.assertIn("syncResult.resources", body)
        self.assertIn("is_retryable_sync_error $classification_text", body)

    def test_uses_started_at_as_fresh_operation_identity(self):
        start = self.text.index("def sync_gated_apps_for_local_dev")
        end = self.text.index("\ndef ", start + 10)
        body = self.text[start:end]
        self.assertIn("previous_started", body)
        self.assertIn("status.operationState.startedAt", body)
        self.assertIn("started != $previous_started", body)

    def test_preserves_major_upgrade_gate_contract(self):
        # Must not regress the existing gated-sync contract (test_major_upgrade_gates.py)
        self.assertIn("status.operationState.finishedAt", self.text)
        self.assertIn("status.operationState.phase", self.text)
        self.assertIn("status.sync.status", self.text)
        self.assertIn('phase in ["Failed" "Error"]', self.text)
        self.assertIn("Synced+Healthy", self.text)

    def test_stale_status_fallback_remains_fail_closed(self):
        start = self.text.index("def argocd_app_has_no_material_diff")
        end = self.text.index("\n# Wait for ArgoCD apps", start)
        body = self.text[start:end]
        self.assertIn("argocd app diff $app --core --refresh", body)
        self.assertIn("$diff.exit_code == 0", body)
        self.assertNotIn("$diff.stdout", body)
        self.assertNotIn("$diff.stderr", body)

        wait_start = self.text.index("def wait_for_argocd_apps")
        wait_end = self.text.index("\n# -----------------------------------------------------------------------------", wait_start)
        wait_body = self.text[wait_start:wait_end]
        self.assertIn('$health == "Healthy" and $sync == "OutOfSync"', wait_body)
        self.assertIn("argocd_app_has_no_material_diff $app", wait_body)


class MaterialDiffFallbackRuntimeTest(unittest.TestCase):
    def _run_with_fake_argocd(
        self,
        exit_code: int,
        *,
        expression: str = "argocd_app_has_no_material_diff kyverno",
        version_exit_code: int = 0,
        version: str = "argocd: v3.4.5+564b949",
        kubectl_exit_code: int = 0,
        include_argocd: bool = True,
    ) -> tuple[str, str, str, str, bool]:
        nu = shutil.which("nu")
        assert nu is not None
        with tempfile.TemporaryDirectory() as tmp:
            work = os.path.join(tmp, "working directory with spaces")
            native_temp = os.path.join(tmp, "native temp directory with spaces")
            bin_dir = os.path.join(tmp, "fake tools")
            os.makedirs(work)
            os.makedirs(native_temp)
            os.makedirs(bin_dir)
            kubeconfig = os.path.join(work, "kubeconfig-local.yaml")
            original = "apiVersion: v1\nkind: Config\ncurrent-context: sentinel\n"
            with open(kubeconfig, "w", encoding="utf-8") as fh:
                fh.write(original)
            kubectl_observation = os.path.join(tmp, "kubectl-observation")
            argocd_observation = os.path.join(tmp, "argocd-observation")
            for name, script in {
                "mktemp": "#!/bin/sh\nexit 127\n",
                "kubectl": (
                    "#!/bin/sh\n"
                    "printf '%s\\n' \"$*\" > \"$KUBECTL_OBSERVATION\"\n"
                    f"exit {kubectl_exit_code}\n"
                ),
                "argocd": (
                    "#!/bin/sh\n"
                    # Issue #283: argocd_app_has_no_material_diff also checks
                    # client/server MAJOR.MINOR compatibility (not exact-patch)
                    # before trusting a diff; answer that call distinctly so
                    # the diff exit-code path under test is still isolated.
                    "case \"$*\" in\n"
                    "  *'version --client --short'*)\n"
                    f"    echo '{version}'\n"
                    f"    exit {version_exit_code}\n"
                    "    ;;\n"
                    "esac\n"
                    "printf '%s\\n' \"$KUBECONFIG\" > \"$ARGOCD_OBSERVATION\"\n"
                    "test -f \"$KUBECONFIG\" || exit 73\n"
                    "echo 'Authorization: Bearer should-not-leak'\n"
                    "echo 'password=should-not-leak' >&2\n"
                    f"exit {exit_code}\n"
                ),
            }.items():
                if name == "argocd" and not include_argocd:
                    continue
                path = os.path.join(bin_dir, name)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(script)
                os.chmod(path, 0o755)

            result = subprocess.run(
                [nu, "--no-config-file", "-c", f"source {SETUP}; {expression}"],
                cwd=work,
                env={
                    **os.environ,
                    "PATH": f"{bin_dir}:/usr/bin:/bin",
                    "TMPDIR": native_temp,
                    "KUBECTL_OBSERVATION": kubectl_observation,
                    "ARGOCD_OBSERVATION": argocd_observation,
                },
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("should-not-leak", result.stdout + result.stderr)
            with open(kubeconfig, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), original, "the original kubeconfig was mutated")
            kubectl_args = ""
            if os.path.exists(kubectl_observation):
                with open(kubectl_observation, encoding="utf-8") as fh:
                    kubectl_args = fh.read().strip()
            temp_kubeconfig = ""
            if os.path.exists(argocd_observation):
                with open(argocd_observation, encoding="utf-8") as fh:
                    temp_kubeconfig = fh.read().strip()
            temp_still_exists = bool(temp_kubeconfig and os.path.exists(temp_kubeconfig))
            return (
                result.stdout.strip(),
                result.stderr.strip(),
                kubectl_args,
                temp_kubeconfig,
                temp_still_exists,
            )

    def test_zero_diff_is_accepted(self):
        stdout, _, _, _, _ = self._run_with_fake_argocd(0)
        self.assertEqual(stdout, "true")

    def test_diff_or_cli_error_is_rejected(self):
        self.assertEqual(self._run_with_fake_argocd(1)[0], "false")
        self.assertEqual(self._run_with_fake_argocd(2)[0], "false")

    def test_uses_native_temp_path_with_spaces_and_removes_the_copy(self):
        stdout, _, kubectl_args, temp_kubeconfig, temp_still_exists = (
            self._run_with_fake_argocd(0)
        )
        self.assertEqual(stdout, "true")
        self.assertIn("config set-context --current --namespace=argocd", kubectl_args)
        self.assertIn("native temp directory with spaces", temp_kubeconfig)
        self.assertFalse(temp_still_exists, "the copied kubeconfig was not removed")


class FreshGatedOperationDecisionRuntimeTest(unittest.TestCase):
    """Drive the exact fresh-operation acceptance predicate through real Nushell."""

    def _decision(
        self,
        *,
        saw_new: bool,
        phase: str,
        sync: str,
        health: str,
        diff_exit_code: int = 0,
        version_exit_code: int = 0,
        version: str = "argocd: v3.4.5+564b949",
        kubectl_exit_code: int = 0,
        include_argocd: bool = True,
    ) -> str:
        expression = (
            "gated_sync_operation_succeeded crossplane-harbor-bootstrap "
            f"{str(saw_new).lower()} {phase} {sync} {health}"
        )
        return MaterialDiffFallbackRuntimeTest()._run_with_fake_argocd(
            diff_exit_code,
            expression=expression,
            version_exit_code=version_exit_code,
            version=version,
            kubectl_exit_code=kubectl_exit_code,
            include_argocd=include_argocd,
        )[0]

    def test_accepts_only_fresh_succeeded_healthy_synced_or_zero_diff(self):
        accepted = [
            (True, "Succeeded", "Synced", "Healthy", 1),
            (True, "Succeeded", "OutOfSync", "Healthy", 0),
        ]
        for saw_new, phase, sync, health, diff_exit_code in accepted:
            with self.subTest(sync=sync):
                self.assertEqual(
                    self._decision(
                        saw_new=saw_new,
                        phase=phase,
                        sync=sync,
                        health=health,
                        diff_exit_code=diff_exit_code,
                    ),
                    "true",
                )

    def test_rejects_stale_terminal_running_failed_unhealthy_and_unknown(self):
        rejected = [
            (False, "Succeeded", "Synced", "Healthy"),
            (False, "Succeeded", "OutOfSync", "Healthy"),
            (True, "Failed", "Synced", "Healthy"),
            (True, "Error", "Synced", "Healthy"),
            (True, "Running", "Synced", "Healthy"),
            (True, "Succeeded", "Synced", "Progressing"),
            (True, "Succeeded", "Synced", "Unknown"),
            (True, "Succeeded", "Unknown", "Healthy"),
        ]
        for saw_new, phase, sync, health in rejected:
            with self.subTest(
                saw_new=saw_new, phase=phase, sync=sync, health=health
            ):
                self.assertEqual(
                    self._decision(
                        saw_new=saw_new,
                        phase=phase,
                        sync=sync,
                        health=health,
                    ),
                    "false",
                )

    def test_rejects_material_diff_and_every_cli_setup_or_version_error(self):
        cases = [
            {"diff_exit_code": 1},
            {"diff_exit_code": 2},
            {"kubectl_exit_code": 1},
            {"version_exit_code": 2},
            {"version": "argocd: v3.5.0+incompatible"},
            {"include_argocd": False},
        ]
        for case in cases:
            with self.subTest(case=case):
                self.assertEqual(
                    self._decision(
                        saw_new=True,
                        phase="Succeeded",
                        sync="OutOfSync",
                        health="Healthy",
                        **case,
                    ),
                    "false",
                )


class FreshGatedOperationWiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(SETUP, encoding="utf-8") as fh:
            cls.text = fh.read()
        start = cls.text.index("def sync_gated_apps_for_local_dev")
        end = cls.text.index("\ndef ", start + 10)
        cls.loop = cls.text[start:end]

    def test_decision_is_wired_before_the_post_sync_credential_gate(self):
        self.assertIn("gated_sync_operation_succeeded", self.loop)
        self.assertLess(
            self.loop.index("gated_sync_operation_succeeded"),
            self.loop.index("ensure_crossplane_harbor_credentials"),
        )

    def test_acceptance_prints_only_a_fixed_safe_note(self):
        self.assertIn("fresh successful operation has no material diff", self.loop)
        self.assertNotIn("$diff.stdout", self.loop)
        self.assertNotIn("$diff.stderr", self.loop)


class MaterialDiffWindowsPortabilityStructureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(SETUP, encoding="utf-8") as fh:
            text = fh.read()
        start = text.index("def argocd_app_has_no_material_diff")
        end = text.index("\ndef ", start + 10)
        cls.body = text[start:end]

    def test_uses_nushell_native_unpredictable_temp_path(self):
        self.assertNotIn("mktemp", self.body)
        self.assertNotIn("/dev/", self.body)
        self.assertIn("$nu.temp-dir", self.body)
        self.assertIn("path join", self.body)
        self.assertIn("random uuid", self.body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
