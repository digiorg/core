"""Issue 375 inventory and sidecar contracts; parse data, not YAML substrings."""
from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]


class AppInventoryConfigurationTests(unittest.TestCase):
    def test_registration_output_is_wired_without_a_speculative_catalog_pin(self):
        base = ROOT / 'platform/base/app-dashboards'
        resources = yaml.safe_load((base / 'kustomization.yaml').read_text())['resources']
        self.assertIn('registered-apps.generated.yaml', resources)
        self.assertEqual(yaml.safe_load((base / 'registrations.yaml').read_text()), {'apps': []})
        self.assertEqual(yaml.safe_load((base / 'registered-apps.generated.yaml').read_text())['items'], [])
        catalog = yaml.safe_load((ROOT / 'apps/platform/core-catalog.yaml').read_text())
        self.assertEqual(catalog['spec']['source']['targetRevision'], '2867d1b4d47f472c7bb4097d61226e206e32a5a1')

    def test_pinned_ksm_has_bounded_inventory_and_folder_integration(self):
        values = yaml.safe_load((ROOT / "platform/base/grafana/values.yaml").read_text())
        ksm = values.get("kube-state-metrics", {})
        self.assertEqual(ksm.get("metricLabelsAllowlist"), [
            "namespaces=[platform.digiorg.io/type,platform.digiorg.io/app-id,platform.digiorg.io/team]"])
        self.assertEqual(ksm["rbac"]["extraRules"], [
            {"apiGroups": ["platform.digiorg.io"], "resources": ["appclaims"], "verbs": ["get", "list", "watch"]},
            {"apiGroups": ["apiextensions.k8s.io"], "resources": ["customresourcedefinitions"], "verbs": ["list", "watch"]}])
        crs = ksm["customResourceState"]
        self.assertTrue(crs["enabled"])
        resources = crs["config"]["spec"]["resources"]
        self.assertEqual(len(resources), 1)
        resource = resources[0]
        self.assertEqual(resource["groupVersionKind"], {"group": "platform.digiorg.io", "version": "v1alpha1", "kind": "AppClaim"})
        self.assertEqual(resource["metricNamePrefix"], "digiorg_app")
        metrics = {m["name"]: m["each"] for m in resource["metrics"]}
        self.assertEqual(metrics["info"]["type"], "Info")
        self.assertEqual(metrics["service_info"]["info"]["path"], ["spec", "services"])
        self.assertEqual(metrics["service_info"]["info"]["labelsFromPath"], {"service": ["name"]})
        self.assertEqual(resource["labelsFromPath"]["namespace"], ["spec", "appName"])
        self.assertEqual(metrics["condition"]["stateSet"]["path"], ["status", "conditions"])
        self.assertEqual(set(metrics["condition"]["stateSet"]["list"]), {"True", "False", "Unknown"})
        dashboard = values["grafana"]["sidecar"]["dashboards"]
        self.assertEqual(dashboard["labelValue"], "1")
        self.assertEqual(dashboard["folderAnnotation"], "grafana_folder")
        self.assertTrue(dashboard["provider"]["foldersFromFilesStructure"])
        self.assertEqual(dashboard["provider"]["folder"], "")
        self.assertFalse(dashboard["provider"]["disableDelete"])
        self.assertFalse(dashboard["provider"]["allowUiUpdates"])


if __name__ == "__main__":
    unittest.main()
