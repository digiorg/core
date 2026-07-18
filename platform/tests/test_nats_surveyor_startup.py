#!/usr/bin/env python3
"""NATS Surveyor must not noisily crash-loop before NATS is reachable (#279).

Confirmed runtime evidence: on first deployment ``nats-surveyor`` restarts with
``Error: couldn't start surveyor: nats: no servers available for connection``
because it starts before the NATS Service has a ready endpoint. It recovers on
its own once ``nats-0`` is Ready, so the underlying wiring is correct — this is
a startup-order/retry weakness, not a config bug.

The fix is a bounded init container (mirrors the existing
``wait-for-postgres`` pattern in ``platform/base/backstage/deployment.yaml``)
that waits for the NATS Service to accept TCP connections before the surveyor
container starts, so the Pod does not enter a noisy CrashLoopBackOff during the
initial app-of-apps burst. The wait must be *bounded* (so a permanently broken
NATS endpoint still surfaces as a failure) and must not touch the main
container's auth/config args, so a genuine auth/config error still fails loud.

Pure python3 + PyYAML::

    python3 platform/tests/test_nats_surveyor_startup.py
"""
import os
import sys
import unittest

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.stderr.write("PyYAML is required: pip install pyyaml\n")
    raise

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SURVEYOR = os.path.join(REPO_ROOT, "platform", "base", "nats", "surveyor.yaml")
NATS_APP = os.path.join(REPO_ROOT, "apps", "platform", "nats.yaml")


def _docs():
    with open(SURVEYOR, encoding="utf-8") as fh:
        return [d for d in yaml.safe_load_all(fh) if d]


class SurveyorStartupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.deployment = next(d for d in _docs() if d.get("kind") == "Deployment")
        cls.pod_spec = cls.deployment["spec"]["template"]["spec"]

    def test_has_bounded_init_container_waiting_for_nats(self):
        init_containers = self.pod_spec.get("initContainers", [])
        self.assertTrue(init_containers, "surveyor must wait for NATS before starting")
        wait_container = init_containers[0]
        command_text = " ".join(wait_container.get("command", []))
        self.assertIn("nats.messaging.svc.cluster.local", command_text)
        self.assertIn("4222", command_text)
        # Must be bounded — a fixed retry count, not an infinite `until`/`while true`.
        self.assertRegex(command_text, r"seq 1 [0-9]+|for .* in 1\.\.[0-9]+")
        self.assertNotRegex(command_text, r"while true|until true")

    def test_main_container_auth_and_config_untouched(self):
        main = next(c for c in self.pod_spec["containers"] if c["name"] == "nats-surveyor")
        args = main["args"]
        self.assertIn("sys", args)
        self.assertIn("sys_password", args)
        # A permanent auth/config error must still be visible: no retry/backoff
        # flags added to the surveyor binary itself that would swallow it.
        self.assertNotIn("--retry", args)

    def test_main_container_probes_unchanged(self):
        main = next(c for c in self.pod_spec["containers"] if c["name"] == "nats-surveyor")
        self.assertEqual(main["livenessProbe"]["httpGet"]["path"], "/metrics")
        self.assertEqual(main["readinessProbe"]["httpGet"]["path"], "/metrics")


class NatsStatefulSetDriftTest(unittest.TestCase):
    def test_ignores_kubernetes_injected_pvc_template_status(self):
        # Kubernetes persists status.phase inside StatefulSet PVC templates.
        # Argo CD 3.4.5 does not normalize this nested status field and reports
        # the otherwise-identical StatefulSet OutOfSync forever.
        with open(NATS_APP, encoding="utf-8") as fh:
            app = yaml.safe_load(fh)
        rules = app["spec"].get("ignoreDifferences", [])
        matching = [
            rule for rule in rules
            if rule.get("group") == "apps"
            and rule.get("kind") == "StatefulSet"
            and rule.get("name") == "nats"
        ]
        self.assertEqual(len(matching), 1)
        expressions = matching[0].get("jqPathExpressions", [])
        self.assertIn(".spec.volumeClaimTemplates[]?.status", expressions)


if __name__ == "__main__":
    unittest.main(verbosity=2)
