#!/usr/bin/env python3
"""Crossplane must be able to observe the target Namespace before Catalog runs.

Catalog PR #23 deliberately uses function-kcl RequiredResources to gate the
one-shot Harbor robot CREATE on an existing Active Namespace. Crossplane v2.3.3
runs that fetch through its own dynamic informer. A fresh cluster proved that
its ServiceAccount cannot get/list/watch Namespace objects by default, so the
informer never syncs and the XR cannot render the Namespace it is waiting for.
"""
import os
import unittest
import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONFIG_DIR = os.path.join(ROOT, "crossplane", "providers", "configs")
KUSTOMIZATION = os.path.join(CONFIG_DIR, "kustomization.yaml")
RBAC = os.path.join(CONFIG_DIR, "crossplane-namespace-observer-rbac.yaml")
APPS = os.path.join(ROOT, "apps", "platform")


def load(path):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def wave(name):
    app = load(os.path.join(APPS, f"{name}.yaml"))
    return int(app["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"])


class CrossplaneNamespaceObserverRbacTest(unittest.TestCase):
    def setUp(self):
        self.role = load(RBAC)

    def test_provider_configs_owns_the_exact_aggregated_role(self):
        resources = load(KUSTOMIZATION)["resources"]
        self.assertEqual(resources.count("crossplane-namespace-observer-rbac.yaml"), 1)
        self.assertEqual(self.role["apiVersion"], "rbac.authorization.k8s.io/v1")
        self.assertEqual(self.role["kind"], "ClusterRole")
        self.assertEqual(self.role["metadata"]["name"], "crossplane-namespace-observer")
        self.assertEqual(
            self.role["metadata"]["labels"],
            {"rbac.crossplane.io/aggregate-to-crossplane": "true"},
        )
        self.assertEqual(set(self.role), {"apiVersion", "kind", "metadata", "rules"})
        self.assertEqual(set(self.role["metadata"]), {"name", "labels"})

    def test_role_is_exactly_namespace_informer_read_only(self):
        self.assertEqual(
            self.role["rules"],
            [{"apiGroups": [""], "resources": ["namespaces"], "verbs": ["list", "watch"]}],
        )
        self.assertEqual(set(self.role["rules"][0]), {"apiGroups", "resources", "verbs"})

    def test_no_binding_is_added(self):
        for name in os.listdir(CONFIG_DIR):
            if not name.endswith((".yaml", ".yml")):
                continue
            doc = load(os.path.join(CONFIG_DIR, name))
            self.assertNotIn((doc or {}).get("kind"), {"RoleBinding", "ClusterRoleBinding"})

    def test_permission_precedes_xrd_catalog_and_claim_delivery(self):
        provider_configs = wave("crossplane-provider-configs")
        self.assertLess(provider_configs, wave("crossplane-xrds"))
        self.assertLess(provider_configs, wave("core-catalog"))
        self.assertLess(provider_configs, wave("app-config"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
