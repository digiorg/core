"""Issue 375 overview must anchor current inventory, not only running Pods."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "platform/base/app-dashboards"


class OverviewTests(unittest.TestCase):
    def test_overview_render_and_inventory_states(self):
        self.assertTrue((BASE / "kustomization.yaml").is_file(), "missing integrated overview")
        result = subprocess.run(["kustomize", "build", str(ROOT / "platform/base/monitoring-extras")],
                                capture_output=True, text=True, check=True)
        docs = list(yaml.safe_load_all(result.stdout))
        cm = next(d for d in docs if d.get("metadata", {}).get("name") == "grafana-app-health")
        self.assertEqual(cm["metadata"]["annotations"]["grafana_folder"], "/tmp/dashboards/DigiOrg Platform")
        dashboard = json.loads(cm["data"]["app-health.json"])
        self.assertEqual(dashboard["uid"], "digiorg-app-health")
        self.assertEqual(dashboard["refresh"], "30s")
        self.assertEqual(dashboard["time"], {"from": "now-1h", "to": "now"})
        panels = {p["title"]: p for p in dashboard["panels"]}
        self.assertIn("Unknown", panels["Inventory feed"]["fieldConfig"]["defaults"]["noValue"])
        target = panels["Registered app health"]["targets"][0]
        self.assertTrue(target["instant"])
        self.assertEqual(target["format"], "table")
        self.assertEqual(target["expr"], "digiorg:app_status")
        self.assertIn("__data.fields.app_id", json.dumps(panels["Registered app health"]))
        rules = next(d for d in docs if d.get("kind") == "PrometheusRule" and d["metadata"]["name"] == "digiorg-app-health")
        records = {r["record"]: r["expr"] for g in rules["spec"]["groups"] for r in g["rules"]}
        self.assertIn("digiorg_app_info", records["digiorg:app_inventory"])
        self.assertIn("digiorg_registered_app_info", records["digiorg:app_inventory"])
        self.assertIn("digiorg:app_expected_service", records["digiorg:app_missing_services"])
        self.assertIn("unless on(namespace,service,kind)", records["digiorg:app_missing_services"])
        self.assertIn("digiorg:inventory_feed_healthy", records["digiorg:app_status"])
        self.assertNotIn("or vector(0)", " ".join(records.values()))
        self.assertTrue(any("kube_pod_status_unschedulable" in e for e in records.values()))
        self.assertTrue(any("kube_pod_container_status_waiting_reason" in e for e in records.values()))
        self.assertNotIn("kube_job_status_failed", " ".join(records.values()))


if __name__ == "__main__":
    unittest.main()
