#!/usr/bin/env python3
"""Guard the local Argo CD Application Controller OOM contract (#293).

Preserved kernel evidence reports a 1,131,472 KiB resident sample: 1,044,396
KiB of anonymous memory plus 87,076 KiB of file-backed memory. This contract
selects a bounded candidate limit above that sample and reduces controller
concurrency; representative runtime replay is still required.

Pure python3 + PyYAML::

    python3 platform/tests/test_argocd_controller_oom.py
"""
import os
import unittest

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
VALUES = os.path.join(REPO_ROOT, "platform", "base", "argocd", "values.yaml")

OBSERVED_ANON_KIB = 1_044_396
OBSERVED_FILE_KIB = 87_076
OBSERVED_RESIDENT_SAMPLE_KIB = OBSERVED_ANON_KIB + OBSERVED_FILE_KIB

UNAFFECTED_COMPONENT_RESOURCES = {
    "applicationSet": {
        "requests": {"cpu": "50m", "memory": "64Mi"},
        "limits": {"cpu": "500m", "memory": "256Mi"},
    },
    "repoServer": {
        "requests": {"cpu": "200m", "memory": "256Mi"},
        "limits": {"cpu": "1000m", "memory": "1Gi"},
    },
    "server": {
        "requests": {"cpu": "50m", "memory": "64Mi"},
        "limits": {"cpu": "500m", "memory": "256Mi"},
    },
    "redis": {
        "requests": {"cpu": "50m", "memory": "64Mi"},
        "limits": {"cpu": "200m", "memory": "128Mi"},
    },
}


def _mem_kib(value: str) -> int:
    value = str(value)
    if value.endswith("Gi"):
        return int(value[:-2]) * 1024 * 1024
    if value.endswith("Mi"):
        return int(value[:-2]) * 1024
    if value.endswith("Ki"):
        return int(value[:-2])
    raise ValueError(f"unsupported memory unit: {value}")


def _values():
    with open(VALUES, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class ApplicationControllerOOMTest(unittest.TestCase):
    def test_candidate_memory_limit_exceeds_observed_resident_sample(self):
        limit = _values()["controller"]["resources"]["limits"]["memory"]
        self.assertEqual(OBSERVED_RESIDENT_SAMPLE_KIB, 1_131_472)
        self.assertGreater(
            _mem_kib(limit),
            OBSERVED_RESIDENT_SAMPLE_KIB,
            "candidate controller limit must exceed the 1,131,472 KiB resident sample",
        )

    def test_memory_request_is_exact(self):
        request = _values()["controller"]["resources"]["requests"]["memory"]
        self.assertEqual(request, "384Mi")

    def test_memory_limit_is_exact_and_bounded(self):
        limit = _values()["controller"]["resources"]["limits"]["memory"]
        self.assertEqual(
            limit,
            "1280Mi",
            "controller memory limit must be the bounded 1280Mi target, not 2Gi",
        )

    def test_controller_processor_concurrency_is_reduced(self):
        params = _values()["configs"]["params"]
        expected = {
            "controller.status.processors": ("10", 20),
            "controller.operation.processors": ("5", 10),
        }
        for name, (configured, upstream_default) in expected.items():
            with self.subTest(parameter=name):
                actual = params.get(name)
                self.assertEqual(actual, configured, f"{name} must be the string {configured!r}")
                self.assertGreater(int(actual), 0)
                self.assertLess(int(actual), upstream_default)

    def test_sibling_component_resources_remain_unchanged(self):
        values = _values()
        for component, resources in UNAFFECTED_COMPONENT_RESOURCES.items():
            with self.subTest(component=component):
                self.assertEqual(values[component]["resources"], resources)


if __name__ == "__main__":
    unittest.main(verbosity=2)
