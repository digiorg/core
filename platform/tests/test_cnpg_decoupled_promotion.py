#!/usr/bin/env python3
"""CNPG is decoupled from `main up` and the fail-closed core-platform gate
(Issue #283).

CNPG (operator + Cluster) is optional, future hosted-application database
infrastructure. Nothing on the internal platform's dependency path consumes
it, so:

  * it must be excluded from `wait_for_argocd_apps`'s fail-closed inventory
    (a stalled/failed CNPG must never time out the whole bootstrap);
  * `main up` must not call CNPG promotion AT ALL — even a caught/non-fatal
    call still WAITS (operator availability, webhook readiness, up to 90
    polls of the Cluster operation) before `main up` can reach its Ready
    banner, which delays core bootstrap by however long CNPG takes to fail.
    A prior "best-effort" wrapper (`promote_future_app_infrastructure`,
    called synchronously from `main up`) was misleading: catching the error
    does not undo the minutes already spent waiting for it. CNPG is
    provisioned only via a separate, EXPLICIT command
    (`nu scripts/local-setup.nu future-infra`) that an operator runs after
    core bootstrap succeeds, and which correctly fails closed on error.

Pure python3 structural checks plus fake-kubectl runtime checks::

    python3 platform/tests/test_cnpg_decoupled_promotion.py
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


def _func_body(text, name):
    start = text.index(f"def {name} ")
    end = text.index("\ndef ", start + 10)
    return text[start:end]


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


class CoreGateExcludesFutureInfraTest(unittest.TestCase):
    """wait_for_argocd_apps must be a fail-closed CORE-platform gate only."""

    @classmethod
    def setUpClass(cls):
        cls.text = _read(SETUP)
        cls.wait = _func_body(cls.text, "wait_for_argocd_apps")

    def _waited_names(self):
        marker = self.wait.index("let apps = [")
        block = self.wait[marker:self.wait.index("]", marker)]
        return set(re.findall(r'"([a-z0-9-]+)"', block))

    def test_cnpg_and_cnpg_cluster_are_excluded(self):
        waited = self._waited_names()
        self.assertNotIn("cnpg", waited)
        self.assertNotIn("cnpg-cluster", waited)

    def test_every_other_manifest_application_is_still_waited(self):
        # The exclusion is narrow and intentional — every OTHER Application
        # from apps/platform/*.yaml must still be part of the fail-closed gate.
        manifests = _manifest_app_names() - {"cnpg", "cnpg-cluster"}
        waited = self._waited_names()
        self.assertEqual(
            waited, manifests,
            "the wait_for_argocd_apps inventory must match apps/platform/*.yaml "
            f"exactly except cnpg/cnpg-cluster; missing={manifests - waited} "
            f"extra={waited - manifests}",
        )

    def test_exclusion_is_documented(self):
        self.assertIn("283", self.wait)
        self.assertTrue(
            "cnpg" in self.wait.lower() and "future" in self.wait.lower(),
            "the exclusion of cnpg/cnpg-cluster from the fail-closed gate must "
            "be documented in wait_for_argocd_apps",
        )


class MainUpNeverPromotesCnpgTest(unittest.TestCase):
    """`main up` must not call CNPG promotion in any form — not even wrapped.

    A caught/non-fatal call still executes promote_cnpg_cluster's bounded
    waits (operator-Available polling, webhook-endpoint polling, up to 90
    Cluster-operation polls) before returning, so `main up` cannot reach its
    Ready banner until CNPG either converges or exhausts every one of those
    waits. That delay is exactly what Issue #283 prohibits: future
    application infrastructure must never delay core-platform bootstrap, not
    merely "never fail" it.
    """

    @classmethod
    def setUpClass(cls):
        cls.text = _read(SETUP)
        cls.deploy = _func_body(cls.text, "deploy_root_app")
        cls.up = _func_body(cls.text, '"main up"')

    def test_promote_cnpg_cluster_not_called_in_deploy_root_app(self):
        self.assertNotIn("promote_cnpg_cluster", self.deploy)

    def test_main_up_never_mentions_cnpg_promotion(self):
        self.assertNotIn("promote_cnpg_cluster", self.up)
        self.assertNotIn("promote_future_app_infrastructure", self.up)

    def test_no_best_effort_wrapper_remains(self):
        # The prior "best-effort" wrapper (caught the error but still waited)
        # was the misleading part of the design; it must not exist at all.
        self.assertNotIn("def promote_future_app_infrastructure", self.text)

    def test_main_up_reaches_ready_banner_without_any_cnpg_wait(self):
        # No function whose body contains a CNPG wait/poll may be reachable
        # from `main up` at all — the strongest structural proof that the
        # Ready banner cannot be delayed by CNPG.
        for forbidden in ("promote_cnpg_cluster", "wait_for_cnpg_webhook_ready"):
            self.assertNotIn(forbidden, self.up)

    def test_gitea_sonarqube_oidc_and_gate_precede_ready_banner_with_no_cnpg_between(self):
        gitea = self.up.index("configure_gitea")
        sonar = self.up.index("configure_sonarqube")
        restart = self.up.index("restart_oidc_dependent_pods")
        gate = self.up.index("wait_for_argocd_apps")
        ready = self.up.index("Platform Ready")
        self.assertTrue(gitea < sonar < restart < gate < ready)


class FutureInfraCommandTest(unittest.TestCase):
    """`main future-infra` is the explicit, separate, fail-closed CNPG command."""

    @classmethod
    def setUpClass(cls):
        cls.text = _read(SETUP)
        cls.cmd = _func_body(cls.text, '"main future-infra"')

    def test_command_is_defined(self):
        self.assertIn('def "main future-infra"', self.text)

    def test_command_calls_promote_cnpg_cluster_directly(self):
        self.assertIn("promote_cnpg_cluster", self.cmd)

    def test_command_does_not_swallow_errors(self):
        # Unlike the removed wrapper, this command must propagate failures —
        # no try/catch that converts an error into a status string.
        self.assertNotIn("try {", self.cmd)
        self.assertNotIn("} catch", self.cmd)

    def test_command_is_documented_in_help(self):
        main_help = _func_body(self.text, "main []")
        self.assertIn("future-infra", main_help)

    def test_top_level_usage_comment_mentions_future_infra(self):
        header = self.text[:self.text.index("def main [")]
        self.assertIn("future-infra", header)


class FutureInfraCommandRuntimeTest(unittest.TestCase):
    """Behavioral proof that `main future-infra` is fail-closed end-to-end."""

    # Application exists, operator/webhook are ready, but the Cluster's last
    # operation is Failed (not Running, not Succeeded+Synced+Healthy) so the
    # script proceeds straight to `kubectl patch`, which itself fails — the
    # fastest deterministic failure path through promote_cnpg_cluster.
    # The operator Application ("cnpg") is already Synced+Healthy in both
    # scenarios below — promote_cnpg_operator's own resume-safe branches are
    # covered separately (PromoteOperatorResumeRuntimeTest in
    # test_cnpg_startup_ordering.py). Here we only need it out of the way so
    # these tests can isolate promote_cnpg_cluster's failure/success paths.
    OPERATOR_READY = '''\
  *"get application cnpg "*"-o name"*)
    echo application.argoproj.io/cnpg
    ;;
  *"get application cnpg "*"-o json"*)
    printf '{"status":{"operationState":{"phase":"Succeeded","startedAt":"old"},"sync":{"status":"Synced"},"health":{"status":"Healthy"}}}\\n'
    ;;
'''

    FAKE_KUBECTL_FAILS = r'''#!/bin/sh
args="$*"
case "$args" in
''' + OPERATOR_READY + r'''  *"get application cnpg-cluster"*"-o name"*)
    echo application.argoproj.io/cnpg-cluster
    ;;
  *"get deployment"*"-l app.kubernetes.io/name=cloudnative-pg"*)
    printf '%s\n' '{"kind":"DeploymentList","items":[{"spec":{"replicas":1},"status":{"availableReplicas":1}}]}'
    ;;
  *"get endpointslices"*)
    printf '%s\n' '{"items":[{"endpoints":[{"conditions":{"ready":true},"addresses":["10.0.0.1"]}]}]}'
    ;;
  *"get application cnpg-cluster"*"-o json"*)
    printf '{"status":{"operationState":{"phase":"Failed","startedAt":"old"},"sync":{"status":"OutOfSync"},"health":{"status":"Healthy"}}}\n'
    ;;
  *"patch application cnpg-cluster"*)
    exit 1
    ;;
  *)
    printf 'unexpected kubectl call: %s\n' "$args" >&2
    exit 1
    ;;
esac
'''

    FAKE_KUBECTL_READY = r'''#!/bin/sh
args="$*"
case "$args" in
''' + OPERATOR_READY + r'''  *"get application cnpg-cluster"*"-o name"*)
    echo application.argoproj.io/cnpg-cluster
    ;;
  *"get deployment"*"-l app.kubernetes.io/name=cloudnative-pg"*)
    printf '%s\n' '{"kind":"DeploymentList","items":[{"spec":{"replicas":1},"status":{"availableReplicas":1}}]}'
    ;;
  *"get endpointslices"*)
    printf '%s\n' '{"items":[{"endpoints":[{"conditions":{"ready":true},"addresses":["10.0.0.1"]}]}]}'
    ;;
  *"get application cnpg-cluster"*"-o json"*)
    printf '{"status":{"operationState":{"phase":"Succeeded","startedAt":"old"},"sync":{"status":"Synced"},"health":{"status":"Healthy"}}}\n'
    ;;
  *)
    printf 'unexpected kubectl call: %s\n' "$args" >&2
    exit 1
    ;;
esac
'''

    def _run(self, script):
        with tempfile.TemporaryDirectory() as tmp:
            kubectl = os.path.join(tmp, "kubectl")
            with open(kubectl, "w", encoding="utf-8") as fh:
                fh.write(script)
            os.chmod(kubectl, 0o755)
            env = os.environ.copy()
            env["PATH"] = tmp + os.pathsep + env.get("PATH", "")
            return subprocess.run(
                ["nu", SETUP, "future-infra"],
                capture_output=True, text=True, timeout=30, env=env,
            )

    def test_failing_cnpg_promotion_fails_the_command_closed(self):
        result = self._run(self.FAKE_KUBECTL_FAILS)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CNPG", result.stdout + result.stderr)

    def test_already_ready_cluster_succeeds(self):
        result = self._run(self.FAKE_KUBECTL_READY)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
