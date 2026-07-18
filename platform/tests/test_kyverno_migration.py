#!/usr/bin/env python3
"""Kyverno's CRD migration hook must not churn on a clean-install cluster (#279).

``crds.migration.enabled`` renders a Helm ``post-upgrade`` hook Job
(``kyverno-migrate-resources``). Helm itself only runs ``post-upgrade`` hooks
on ``helm upgrade``, never on a fresh ``helm install`` — but Argo CD renders
every chart with ``helm template`` and maps ``post-upgrade``/``post-install``
hooks onto ``PostSync`` for *every* sync, with no concept of "this is the first
install". On a brand-new disposable KinD cluster there is no prior Kyverno CRD
state to migrate, so the hook is pure churn/noise (confirmed in issue #279).

The chart-default clean-install path must disable the migration hook; a real
upgrade with prior state must explicitly re-enable it for that one promotion,
per the documented procedure.

Pure python3 + PyYAML::

    python3 platform/tests/test_kyverno_migration.py
"""
import os
import unittest

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
KYVERNO_APP = os.path.join(REPO_ROOT, "apps", "platform", "kyverno.yaml")
VERSIONS_DOC = os.path.join(REPO_ROOT, "docs", "guides", "platform-versions.md")
POLICIES_DIR = os.path.join(REPO_ROOT, "policies", "kyverno")


def _kyverno_app():
    with open(KYVERNO_APP, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _kyverno_values():
    app = _kyverno_app()
    values_text = app["spec"]["source"]["helm"]["values"]
    return yaml.safe_load(values_text)


class KyvernoCleanInstallTest(unittest.TestCase):
    def test_migration_hook_disabled_for_clean_bootstrap(self):
        values = _kyverno_values()
        self.assertEqual(
            values.get("crds", {}).get("migration", {}).get("enabled"),
            False,
            "clean-install default must not run the CRD migration post-upgrade hook",
        )

    def test_only_api_defaulted_none_conversion_is_ignored(self):
        rules = _kyverno_app()["spec"].get("ignoreDifferences", [])
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["group"], "apiextensions.k8s.io")
        self.assertEqual(rules[0]["kind"], "CustomResourceDefinition")
        self.assertEqual(
            rules[0]["jqPathExpressions"],
            ['.spec.conversion | select(.strategy == "None")'],
        )

    def test_policy_api_defaults_are_declared(self):
        policies = []
        for root, _, files in os.walk(POLICIES_DIR):
            for name in files:
                if not name.endswith(".yaml") or name == "kustomization.yaml":
                    continue
                with open(os.path.join(root, name), encoding="utf-8") as fh:
                    policies.extend(d for d in yaml.safe_load_all(fh) if d and d.get("kind") == "ClusterPolicy")
        self.assertTrue(policies)
        for policy in policies:
            spec = policy["spec"]
            self.assertIn("validationFailureAction", spec, policy["metadata"]["name"])
            self.assertIn("background", spec, policy["metadata"]["name"])
            self.assertEqual(spec.get("admission"), True, policy["metadata"]["name"])
            self.assertEqual(spec.get("emitWarning"), False, policy["metadata"]["name"])
            for rule in spec.get("rules", []):
                self.assertTrue(rule.get("skipBackgroundRequests"), policy["metadata"]["name"])
                for match in rule.get("match", {}).get("any", []):
                    resources = match.get("resources", {})
                    self.assertNotIn("apiGroups", resources, policy["metadata"]["name"])
                    for kind in resources.get("kinds", []):
                        if kind.endswith("AppClaim"):
                            self.assertEqual(
                                kind,
                                "platform.digiorg.io/v1alpha1/AppClaim",
                                policy["metadata"]["name"],
                            )
                if "validate" in rule:
                    self.assertTrue(
                        rule["validate"].get("allowExistingViolations"),
                        policy["metadata"]["name"],
                    )

    def test_upgrade_path_is_documented(self):
        with open(VERSIONS_DOC, encoding="utf-8") as fh:
            doc = fh.read()
        self.assertIn("crds.migration.enabled", doc)
        # Must document both states: off by default, and how to switch it on
        # for a real upgrade with prior Kyverno state.
        section = doc[doc.index("crds.migration.enabled") - 500:]
        self.assertIn("upgrade", section.lower())
        self.assertIn("true", section)


if __name__ == "__main__":
    unittest.main(verbosity=2)
