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
import subprocess
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
