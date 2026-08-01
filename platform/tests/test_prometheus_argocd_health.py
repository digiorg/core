#!/usr/bin/env python3
"""Execute the shipped Argo health contract for Prometheus CRs (#295)."""

from pathlib import Path
import unittest

from lupa import LuaRuntime
import yaml


ROOT = Path(__file__).resolve().parents[2]
VALUES = ROOT / "platform/base/argocd/values.yaml"
CUSTOMIZATION = "resource.customizations.health.monitoring.coreos.com_Prometheus"
TARGET_NAME = "prometheus-kube-prometheus-prometheus"
TARGET_NAMESPACE = "monitoring"
NOT_FOUND_MESSAGE = (
    "shard 0: statefulset "
    "monitoring/prometheus-prometheus-kube-prometheus-prometheus not found"
)


def _lua_value(lua, value):
    if isinstance(value, dict):
        table = lua.table()
        for key, item in value.items():
            table[key] = _lua_value(lua, item)
        return table
    if isinstance(value, list):
        table = lua.table()
        for index, item in enumerate(value, start=1):
            table[index] = _lua_value(lua, item)
        return table
    return value


def _script():
    values = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
    return values["configs"]["cm"][CUSTOMIZATION]


def _health(obj):
    lua = LuaRuntime(unpack_returned_tuples=True)
    health = lua.eval(f"function(obj)\n{_script()}\nend")
    result = health(_lua_value(lua, obj))
    return {"status": result["status"], "message": result["message"]}


def _prometheus(*conditions, generation=1,
                name=TARGET_NAME, namespace=TARGET_NAMESPACE):
    return {
        "metadata": {
            "name": name,
            "namespace": namespace,
            "generation": generation,
        },
        "status": {
            "conditions": list(conditions),
        },
    }


def _condition(condition_type, status, reason, message, observed_generation=1):
    return {
        "type": condition_type,
        "status": status,
        "reason": reason,
        "message": message,
        "observedGeneration": observed_generation,
    }


AVAILABLE = _condition("Available", "True", "", "Prometheus is available")
RECONCILED = _condition("Reconciled", "True", "", "Prometheus is reconciled")
STATEFULSET_NOT_FOUND = _condition(
    "Available", "False", "StatefulSetNotFound", NOT_FOUND_MESSAGE
)


class PrometheusArgoHealthTest(unittest.TestCase):
    def test_missing_status_or_conditions_is_progressing(self):
        fixtures = (
            {"metadata": {"name": TARGET_NAME, "namespace": TARGET_NAMESPACE, "generation": 1}},
            {
                "metadata": {"name": TARGET_NAME, "namespace": TARGET_NAMESPACE, "generation": 1},
                "status": {},
            },
            _prometheus(),
        )
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                self.assertEqual(_health(fixture)["status"], "Progressing")

    def test_exact_initial_statefulset_not_found_is_progressing(self):
        result = _health(_prometheus(STATEFULSET_NOT_FOUND, RECONCILED))
        self.assertEqual(result["status"], "Progressing")
        self.assertIn("operator-generated StatefulSet", result["message"])

    def test_current_generation_available_is_healthy(self):
        self.assertEqual(
            _health(_prometheus(AVAILABLE, RECONCILED))["status"],
            "Healthy",
        )

    def test_unrelated_or_mixed_negative_conditions_are_degraded(self):
        fixtures = (
            _prometheus(_condition("Available", "False", "CreateFailed", "admission denied")),
            _prometheus(
                STATEFULSET_NOT_FOUND,
                _condition("Reconciled", "False", "ReconcileError", "RBAC denied"),
            ),
            _prometheus(
                _condition(
                    "Available", "False", "StatefulSetNotFound",
                    "shard 0: statefulset monitoring/a-different-statefulset not found",
                )
            ),
            _prometheus(
                STATEFULSET_NOT_FOUND,
                name="another-prometheus",
            ),
        )
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                self.assertEqual(_health(fixture)["status"], "Degraded")

    def test_stale_observed_generation_is_degraded(self):
        stale_available = _condition(
            "Available", "True", "", "Prometheus is available", observed_generation=1
        )
        stale_reconciled = _condition(
            "Reconciled", "True", "", "Prometheus is reconciled", observed_generation=1
        )
        result = _health(
            _prometheus(stale_available, stale_reconciled, generation=2)
        )
        self.assertEqual(result["status"], "Degraded")
        self.assertIn("generation", result["message"].lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
