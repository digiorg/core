#!/usr/bin/env python3
"""CNPG Cluster startup ordering (Issue #281, revised for Issue #283).

Confirmed #281 root cause: on a clean bootstrap the ``cnpg-cluster`` Application
tried to apply ``Cluster/postgresql-cnpg`` before the CNPG operator's admission
webhook (``cnpg-webhook-service``) accepted connections, so admission failed
with ``connection refused``. The webhook-readiness gate and fresh-operation
promotion logic locked here are still correct and still used — CNPG is now
promoted only by the separate, explicit ``main future-infra`` command (Issue
#283), which calls the same ``promote_cnpg_cluster`` function directly and
fails closed. It is never invoked from ``deploy_root_app`` or ``main up``: a
prior "best-effort" wrapper still executed CNPG's bounded waits before
catching its error, which delayed the core Ready banner — exactly what Issue
#283 prohibits. See test_cnpg_decoupled_promotion.py for that wiring.

The backup-PVC bind hook this module used to also lock was removed entirely
(Issue #283): CNPG no longer hosts any internal-platform database, so the
`pg_dump` CronJob and its PVC-bind hook that targeted those databases are
gone — see test_cnpg_future_app_infrastructure.py.

Pure-Nushell behaviour checks of the parsing predicates plus structural checks::

    python3 platform/tests/test_cnpg_startup_ordering.py
"""

import os
import re
import subprocess
import sys
import tempfile
import unittest

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.stderr.write("PyYAML is required: pip install pyyaml\n")
    raise

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SETUP = os.path.join(REPO_ROOT, "scripts", "local-setup.nu")
CLUSTER_APP_YAML = os.path.join(REPO_ROOT, "apps", "platform", "cnpg-cluster.yaml")
OPERATOR_APP_YAML = os.path.join(REPO_ROOT, "apps", "platform", "cnpg.yaml")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _docs(path):
    with open(path, encoding="utf-8") as fh:
        return [d for d in yaml.safe_load_all(fh) if d]


def _find(docs, kind, name=None):
    for d in docs:
        if d.get("kind") == kind and (name is None or d.get("metadata", {}).get("name") == name):
            return d
    return None


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


class WebhookEndpointPredicateTest(unittest.TestCase):
    """cnpg_webhook_endpoint_ready parses discovery.k8s.io/v1 EndpointSlices."""

    READY = (
        '{"kind":"EndpointSliceList","items":[{"endpoints":['
        '{"addresses":["10.244.0.20"],"conditions":{"ready":true}}]}]}'
    )
    NOT_READY = (
        '{"kind":"EndpointSliceList","items":[{"endpoints":['
        '{"addresses":["10.244.0.20"],"conditions":{"ready":false}}]}]}'
    )
    EMPTY = '{"kind":"EndpointSliceList","items":[]}'
    NO_ADDRS = (
        '{"kind":"EndpointSliceList","items":[{"endpoints":['
        '{"addresses":[],"conditions":{"ready":true}}]}]}'
    )

    def _call(self, payload):
        escaped = payload.replace("\\", "\\\\").replace("'", "\\'")
        return _run_nu(f"cnpg_webhook_endpoint_ready '{escaped}'")

    def test_ready_endpoint_is_true(self):
        self.assertEqual(self._call(self.READY), "true")

    def test_not_ready_endpoint_is_false(self):
        self.assertEqual(self._call(self.NOT_READY), "false")

    def test_no_endpoints_is_false(self):
        self.assertEqual(self._call(self.EMPTY), "false")

    def test_ready_but_no_address_is_false(self):
        self.assertEqual(self._call(self.NO_ADDRS), "false")

    def test_garbage_is_false(self):
        # Fail closed on unparseable input.
        self.assertEqual(self._call("not-json"), "false")


class OperatorAvailablePredicateTest(unittest.TestCase):
    """cnpg_operator_available accepts a Deployment or a List and checks health."""

    AVAILABLE_LIST = (
        '{"kind":"DeploymentList","items":[{"spec":{"replicas":1},'
        '"status":{"availableReplicas":1}}]}'
    )
    UNAVAILABLE_LIST = (
        '{"kind":"DeploymentList","items":[{"spec":{"replicas":1},'
        '"status":{"availableReplicas":0}}]}'
    )
    EMPTY_LIST = '{"kind":"DeploymentList","items":[]}'

    def _call(self, payload):
        escaped = payload.replace("\\", "\\\\").replace("'", "\\'")
        return _run_nu(f"cnpg_operator_available '{escaped}'")

    def test_available_is_true(self):
        self.assertEqual(self._call(self.AVAILABLE_LIST), "true")

    def test_unavailable_is_false(self):
        self.assertEqual(self._call(self.UNAVAILABLE_LIST), "false")

    def test_no_deployment_is_false(self):
        self.assertEqual(self._call(self.EMPTY_LIST), "false")

    def test_garbage_is_false(self):
        self.assertEqual(self._call("not-json"), "false")


class WebhookWaitStructureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = _read(SETUP)
        cls.wait = _func_body(cls.text, "wait_for_cnpg_webhook_ready")

    def test_uses_endpointslice_not_deprecated_endpoints(self):
        self.assertIn("endpointslices.discovery.k8s.io", self.wait,
                      "must query discovery.k8s.io/v1 EndpointSlices")
        # The deprecated core Endpoints API must not be used for this check.
        self.assertNotRegex(self.wait, r"kubectl get endpoints\b")

    def test_checks_operator_deployment_and_webhook_endpoint(self):
        self.assertIn("cnpg_operator_available", self.wait)
        self.assertIn("cnpg_webhook_endpoint_ready", self.wait)

    def test_fails_closed_with_bounded_redacted_diagnostic(self):
        self.assertIn("error make", self.wait)
        self.assertIn("redact_sync_diagnostic", self.wait)


class PromoteClusterStructureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = _read(SETUP)
        cls.promote = _func_body(cls.text, "promote_cnpg_cluster")

    def test_waits_for_webhook_before_syncing_cluster(self):
        wait_pos = self.promote.index("wait_for_cnpg_webhook_ready")
        patch_pos = self.promote.index("kubectl patch application cnpg-cluster")
        self.assertLess(wait_pos, patch_pos,
                        "must confirm operator/webhook readiness BEFORE applying "
                        "the Cluster")

    def test_uses_fresh_operation_identity(self):
        # A retry/resync must be a genuinely fresh operation (new startedAt).
        self.assertIn("status.operationState.startedAt", self.promote)
        self.assertIn("previous_started", self.promote)
        self.assertIn("started != $previous_started", self.promote)

    def test_resume_observes_existing_running_operation_before_patching(self):
        running = self.promote.index('initial_phase == "Running"')
        patch = self.promote.index("kubectl patch application cnpg-cluster")
        self.assertLess(running, patch,
                        "resume must observe an in-flight operation instead of "
                        "overwriting it with another operation")
        self.assertIn("resuming_existing_operation", self.promote)

    def test_already_converged_cluster_is_a_noop(self):
        patch = self.promote.index("kubectl patch application cnpg-cluster")
        ready = self.promote.index('initial_sync == "Synced"')
        self.assertLess(ready, patch)

    def test_promotion_is_no_longer_called_from_deploy_root_app_or_main_up(self):
        # Issue #283 (P1 correction): promote_cnpg_cluster is not on the core
        # deploy path at all, and NOT even wrapped/caught from `main up` — a
        # caught error still executes CNPG's bounded waits first, which would
        # delay the Ready banner. It is invoked only by the separate, explicit
        # `main future-infra` command, which fails closed. See
        # test_cnpg_decoupled_promotion.py for the full contract.
        deploy = _func_body(self.text, "deploy_root_app")
        self.assertNotIn("promote_cnpg_cluster", deploy)
        up = _func_body(self.text, '"main up"')
        self.assertNotIn("promote_cnpg_cluster", up)
        future_infra = _func_body(self.text, '"main future-infra"')
        self.assertIn("promote_cnpg_cluster", future_infra)


class PromoteClusterResumeRuntimeTest(unittest.TestCase):
    """Exercise CNPG operation-state branches through the real Nushell function."""

    FAKE_KUBECTL = r'''#!/bin/sh
args="$*"
printf '%s\n' "$args" >> "$FAKE_LOG"
case "$args" in
  *"get application cnpg-cluster"*"-o name"*)
    echo application.argoproj.io/cnpg-cluster
    ;;
  *"get deployment"*"-l app.kubernetes.io/name=cloudnative-pg"*)
    printf '%s\n' '{"kind":"DeploymentList","items":[{"spec":{"replicas":1},"status":{"availableReplicas":1}}]}'
    ;;
  *"get endpointslices"*)
    printf '%s\n' '{"items":[{"endpoints":[{"conditions":{"ready":true},"addresses":["10.0.0.1"]}]}]}'
    ;;
  *"get application cnpg-cluster"*"-o json"*)
    count=$(cat "$FAKE_STATE" 2>/dev/null || echo 0)
    count=$((count + 1))
    printf '%s' "$count" > "$FAKE_STATE"
    case "$FAKE_SCENARIO" in
      running_success)
        if [ "$count" -eq 1 ]; then phase=Running; started=old; sync=OutOfSync; else phase=Succeeded; started=old; sync=Synced; fi
        ;;
      already_ready)
        phase=Succeeded; started=old; sync=Synced
        ;;
      stale_fresh)
        if [ "$count" -eq 1 ]; then phase=Failed; started=old; sync=OutOfSync; elif [ "$count" -eq 2 ]; then phase=Running; started=old; sync=OutOfSync; else phase=Succeeded; started=new; sync=Synced; fi
        ;;
      fresh_failed)
        if [ "$count" -eq 1 ]; then phase=Failed; started=old; else phase=Failed; started=new; fi; sync=OutOfSync
        ;;
      patch_failure)
        phase=Failed; started=old; sync=OutOfSync
        ;;
      running_identity_change)
        if [ "$count" -eq 1 ]; then phase=Running; started=old; sync=OutOfSync; else phase=Succeeded; started=new; sync=Synced; fi
        ;;
      running_no_identity)
        phase=Running; started=; sync=OutOfSync
        ;;
      invalid_json)
        printf '%s\n' 'not-json'; exit 0
        ;;
      get_failure)
        exit 1
        ;;
      *) exit 2 ;;
    esac
    printf '{"status":{"operationState":{"phase":"%s","startedAt":"%s"},"sync":{"status":"%s"},"health":{"status":"Healthy"}}}\n' "$phase" "$started" "$sync"
    ;;
  *"patch application cnpg-cluster"*)
    : > "$FAKE_PATCH"
    [ "$FAKE_SCENARIO" = patch_failure ] && exit 1
    printf '%s\n' '{}'
    ;;
  *)
    printf 'unexpected kubectl call: %s\n' "$args" >&2
    exit 1
    ;;
esac
'''

    def _run_scenario(self, scenario):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        kubectl = os.path.join(tmp.name, "kubectl")
        state = os.path.join(tmp.name, "state")
        patch_marker = os.path.join(tmp.name, "patch-called")
        call_log = os.path.join(tmp.name, "calls.log")
        with open(kubectl, "w", encoding="utf-8") as fh:
            fh.write(self.FAKE_KUBECTL)
        os.chmod(kubectl, 0o755)
        env = os.environ.copy()
        env["PATH"] = tmp.name + os.pathsep + env.get("PATH", "")
        env.update({
            "FAKE_STATE": state,
            "FAKE_PATCH": patch_marker,
            "FAKE_LOG": call_log,
            "FAKE_SCENARIO": scenario,
        })
        result = subprocess.run(
            ["nu", "-c", f"source {SETUP}; promote_cnpg_cluster 0sec"],
            capture_output=True, text=True, timeout=12, env=env,
        )
        return result, patch_marker, call_log

    def test_running_operation_is_observed_not_patched(self):
        result, patch_marker, _ = self._run_scenario("running_success")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.exists(patch_marker), result.stdout)
        self.assertIn("Resuming observation", result.stdout)

    def test_already_ready_is_a_noop(self):
        result, patch_marker, _ = self._run_scenario("already_ready")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.exists(patch_marker), result.stdout)
        self.assertIn("already Synced and Healthy", result.stdout)

    def test_terminal_state_starts_and_waits_for_fresh_operation(self):
        result, patch_marker, _ = self._run_scenario("stale_fresh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.exists(patch_marker))
        self.assertIn("sync Succeeded", result.stdout)

    def test_fresh_failed_operation_fails_closed(self):
        result, patch_marker, _ = self._run_scenario("fresh_failed")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(os.path.exists(patch_marker))
        self.assertIn("CNPG Cluster sync failed", result.stderr)

    def test_resumed_operation_identity_change_is_rejected(self):
        result, patch_marker, _ = self._run_scenario("running_identity_change")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(os.path.exists(patch_marker))
        self.assertIn("identity changed unexpectedly", result.stderr)

    def test_running_operation_without_identity_fails_without_patch(self):
        result, patch_marker, _ = self._run_scenario("running_no_identity")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(os.path.exists(patch_marker))
        self.assertIn("has no startedAt identity", result.stderr)

    def test_invalid_application_json_fails_closed(self):
        result, _, _ = self._run_scenario("invalid_json")
        self.assertNotEqual(result.returncode, 0)

    def test_application_get_failure_fails_closed(self):
        result, _, _ = self._run_scenario("get_failure")
        self.assertNotEqual(result.returncode, 0)

    def test_operation_patch_failure_fails_closed(self):
        result, patch_marker, _ = self._run_scenario("patch_failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(os.path.exists(patch_marker))
        self.assertIn("Could not start CNPG Cluster sync", result.stderr)


class ClusterAppScriptDrivenTest(unittest.TestCase):
    """cnpg-cluster is promoted by the script after webhook readiness, so it must
    not auto-sync the Cluster (which is exactly what caused the webhook race)."""

    def test_cluster_app_is_not_automated(self):
        app = _find(_docs(CLUSTER_APP_YAML), "Application", "cnpg-cluster")
        self.assertIsNotNone(app)
        policy = app["spec"].get("syncPolicy", {})
        self.assertNotIn(
            "automated", policy,
            "cnpg-cluster must be script-driven (webhook-ordered), not automated",
        )


class OperatorAppScriptDrivenTest(unittest.TestCase):
    """The cnpg (operator) Application must ALSO be script-driven, not
    automated. `main up` never touches CNPG at all (Issue #283); if the
    operator Application stayed automated, Argo would still provision the
    CNPG operator on its own the moment root-app creates the Application CR
    (regardless of its late sync-wave), directly contradicting that design
    and consuming cluster resources during/soon after core bootstrap."""

    def test_operator_app_is_not_automated(self):
        app = _find(_docs(OPERATOR_APP_YAML), "Application", "cnpg")
        self.assertIsNotNone(app)
        policy = app["spec"].get("syncPolicy", {})
        self.assertNotIn(
            "automated", policy,
            "cnpg (operator) must be script-driven, not automated — `main up` "
            "must never provision it",
        )


class PromoteOperatorStructureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = _read(SETUP)
        cls.promote = _func_body(cls.text, "promote_cnpg_operator")

    def test_uses_fresh_operation_identity(self):
        self.assertIn("status.operationState.startedAt", self.promote)
        self.assertIn("previous_started", self.promote)
        self.assertIn("started != $previous_started", self.promote)

    def test_resume_observes_existing_running_operation_before_patching(self):
        running = self.promote.index('initial_phase == "Running"')
        patch = self.promote.index("kubectl patch application cnpg ")
        self.assertLess(running, patch,
                        "resume must observe an in-flight operation instead of "
                        "overwriting it with another operation")
        self.assertIn("resuming_existing_operation", self.promote)

    def test_already_converged_operator_is_a_noop(self):
        patch = self.promote.index("kubectl patch application cnpg ")
        ready = self.promote.index('initial_sync == "Synced"')
        self.assertLess(ready, patch)

    def test_fails_closed_with_redacted_diagnostics(self):
        self.assertIn("error make", self.promote)
        self.assertIn("redact_sync_diagnostic", self.promote)
        # Never print raw operationState.message directly.
        self.assertNotIn('print $"($message)"', self.promote)

    def test_called_before_promote_cnpg_cluster_in_future_infra(self):
        future_infra = _func_body(self.text, '"main future-infra"')
        operator_pos = future_infra.index("promote_cnpg_operator")
        cluster_pos = future_infra.index("promote_cnpg_cluster")
        self.assertLess(
            operator_pos, cluster_pos,
            "the operator Application must be promoted before the Cluster "
            "Application is touched",
        )


class PromoteOperatorResumeRuntimeTest(unittest.TestCase):
    """Exercise promote_cnpg_operator's operation-state branches for real."""

    FAKE_KUBECTL = r'''#!/bin/sh
args="$*"
case "$args" in
  *"get application cnpg "*"-o name"*)
    echo application.argoproj.io/cnpg
    ;;
  *"get application cnpg "*"-o json"*)
    count=$(cat "$FAKE_STATE" 2>/dev/null || echo 0)
    count=$((count + 1))
    printf '%s' "$count" > "$FAKE_STATE"
    case "$FAKE_SCENARIO" in
      already_ready)
        phase=Succeeded; started=old; sync=Synced
        ;;
      running_success)
        if [ "$count" -eq 1 ]; then phase=Running; started=old; sync=OutOfSync; else phase=Succeeded; started=old; sync=Synced; fi
        ;;
      stale_fresh)
        if [ "$count" -eq 1 ]; then phase=Failed; started=old; sync=OutOfSync; elif [ "$count" -eq 2 ]; then phase=Running; started=old; sync=OutOfSync; else phase=Succeeded; started=new; sync=Synced; fi
        ;;
      fresh_failed)
        if [ "$count" -eq 1 ]; then phase=Failed; started=old; else phase=Failed; started=new; fi; sync=OutOfSync
        ;;
      patch_failure)
        phase=Failed; started=old; sync=OutOfSync
        ;;
      *) exit 2 ;;
    esac
    printf '{"status":{"operationState":{"phase":"%s","startedAt":"%s"},"sync":{"status":"%s"},"health":{"status":"Healthy"}}}\n' "$phase" "$started" "$sync"
    ;;
  *"patch application cnpg "*)
    : > "$FAKE_PATCH"
    [ "$FAKE_SCENARIO" = patch_failure ] && exit 1
    printf '%s\n' '{}'
    ;;
  *)
    printf 'unexpected kubectl call: %s\n' "$args" >&2
    exit 1
    ;;
esac
'''

    def _run_scenario(self, scenario):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        kubectl = os.path.join(tmp.name, "kubectl")
        state = os.path.join(tmp.name, "state")
        patch_marker = os.path.join(tmp.name, "patch-called")
        with open(kubectl, "w", encoding="utf-8") as fh:
            fh.write(self.FAKE_KUBECTL)
        os.chmod(kubectl, 0o755)
        env = os.environ.copy()
        env["PATH"] = tmp.name + os.pathsep + env.get("PATH", "")
        env.update({
            "FAKE_STATE": state,
            "FAKE_PATCH": patch_marker,
            "FAKE_SCENARIO": scenario,
        })
        result = subprocess.run(
            ["nu", "-c", f"source {SETUP}; promote_cnpg_operator 0sec"],
            capture_output=True, text=True, timeout=12, env=env,
        )
        return result, patch_marker

    def test_already_ready_is_a_noop(self):
        result, patch_marker = self._run_scenario("already_ready")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.exists(patch_marker), result.stdout)
        self.assertIn("already Synced and Healthy", result.stdout)

    def test_running_operation_is_observed_not_patched(self):
        result, patch_marker = self._run_scenario("running_success")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.exists(patch_marker), result.stdout)
        self.assertIn("Resuming observation", result.stdout)

    def test_terminal_state_starts_and_waits_for_fresh_operation(self):
        result, patch_marker = self._run_scenario("stale_fresh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.exists(patch_marker))
        self.assertIn("sync Succeeded", result.stdout)

    def test_fresh_failed_operation_fails_closed(self):
        result, patch_marker = self._run_scenario("fresh_failed")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(os.path.exists(patch_marker))
        self.assertIn("CNPG operator sync failed", result.stderr)

    def test_operation_patch_failure_fails_closed(self):
        result, patch_marker = self._run_scenario("patch_failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(os.path.exists(patch_marker))
        self.assertIn("Could not start CNPG operator sync", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
