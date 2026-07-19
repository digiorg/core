#!/usr/bin/env python3
"""argocd-repo-server must survive the initial app-of-apps render burst (#279).

Confirmed runtime evidence (issue #279, reproduced on Windows and Linux):
``argocd-repo-server`` ships with

    requests: {cpu: 50m, memory: 64Mi}
    limits:   {cpu: 500m, memory: 256Mi}

and the argo-cd 10.1.4 chart's repo-server liveness/readiness probes default to
``timeoutSeconds: 1``. During the very first root-app reconciliation, Argo CD
concurrently renders every chart/Kustomize source in the app-of-apps set; under
that burst the ``/healthz?full=true`` liveness check missed its 1s deadline,
kubelet restarted repo-server mid-render, and severed the gRPC manifest-
generation stream (observed as ``ComparisonError ... rpc error: code =
Unavailable desc = error reading from server: EOF`` on External Secrets).

The fix raises repo-server's resource ceiling for the burst and gives its
probes a realistic timeout, without touching any other component's resources.

Pure python3 + PyYAML::

    python3 platform/tests/test_argocd_repo_server_hardening.py
"""
import os
import re
import unittest

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
VALUES = os.path.join(REPO_ROOT, "platform", "base", "argocd", "values.yaml")

# The exact under-provisioned baseline confirmed to cause liveness restarts.
CONFIRMED_INSUFFICIENT = {
    "requests": {"cpu": "50m", "memory": "64Mi"},
    "limits": {"cpu": "500m", "memory": "256Mi"},
}


def _cpu_millis(v: str) -> int:
    v = str(v)
    return int(v[:-1]) if v.endswith("m") else int(v) * 1000


def _mem_mi(v: str) -> int:
    v = str(v)
    if v.endswith("Gi"):
        return int(float(v[:-2]) * 1024)
    if v.endswith("Mi"):
        return int(v[:-2])
    raise ValueError(f"unsupported memory unit: {v}")


def _values():
    with open(VALUES, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class RepoServerResourceTest(unittest.TestCase):
    def test_requests_raised_above_confirmed_insufficient_baseline(self):
        repo_server = _values()["repoServer"]
        requests = repo_server["resources"]["requests"]
        self.assertGreater(
            _cpu_millis(requests["cpu"]), _cpu_millis(CONFIRMED_INSUFFICIENT["requests"]["cpu"]),
            "repoServer CPU request must be raised above the 50m baseline that restarted under load",
        )
        self.assertGreater(
            _mem_mi(requests["memory"]), _mem_mi(CONFIRMED_INSUFFICIENT["requests"]["memory"]),
            "repoServer memory request must be raised above the 64Mi baseline",
        )

    def test_limits_raised_above_confirmed_insufficient_baseline(self):
        repo_server = _values()["repoServer"]
        limits = repo_server["resources"]["limits"]
        self.assertGreaterEqual(_cpu_millis(limits["cpu"]), 1000)
        self.assertGreaterEqual(_mem_mi(limits["memory"]), 512)

    def test_probe_timeouts_survive_render_burst(self):
        repo_server = _values()["repoServer"]
        for probe_name in ("readinessProbe", "livenessProbe"):
            probe = repo_server.get(probe_name)
            self.assertIsNotNone(probe, f"repoServer.{probe_name} must be overridden (chart default timeoutSeconds: 1)")
            self.assertGreaterEqual(
                probe["timeoutSeconds"], 5,
                f"repoServer.{probe_name}.timeoutSeconds must exceed the 1s chart default",
            )

    def test_other_components_untouched(self):
        values = _values()
        # Only repoServer should move — controller/server/redis stay as-is.
        self.assertEqual(values["controller"]["resources"]["requests"]["cpu"], "250m")
        self.assertEqual(values["server"]["resources"]["requests"]["cpu"], "50m")


if __name__ == "__main__":
    unittest.main(verbosity=2)
