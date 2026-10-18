"""Evaluate shipped PromQL with the exact pinned Prometheus version, no cluster."""
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "quay.io/prometheus/prometheus:v3.13.1-distroless@sha256:214f8427c8fba80c327bb94a75feb802ae12f2d6ca30812aa6e7d22f09bbea80"


class AppHealthPromqlTests(unittest.TestCase):
    def test_health_state_and_unknown_fault_matrix(self):
        doc = yaml.safe_load((ROOT / "platform/base/app-dashboards/rules.yaml").read_text())
        groups = doc["spec"]["groups"]
        cases = []
        for name, expected in [("healthy", 5), ("provisioning", 1), ("no_workload", 2), ("scaled_zero", 3),
                               ("reduced", 4), ("crashloop", 4), ("pending", 4), ("unschedulable", 4),
                               ("oom", 4), ("restarts", 4), ("missing_feed", 0), ("stale_inventory", 0),
                               ("missing_readiness", 0), ("collision", 0), ("completed_job_fault", 5), ("stale_workload", 0), ("partial_services", 1)]:
            def series(metric, values="1+0x10"):
                return {"series": metric, "values": values}
            info = 'digiorg_app_info{app_id="alpha",namespace="alpha",claim_uid="uid-a"}'
            inputs = [series(info, "1+0x3" if name == "stale_inventory" else "1+0x10"),
                      series('kube_namespace_status_phase{namespace="alpha",phase="Active"}')]
            if name != "missing_feed":
                inputs.append(series('up{job="kube-state-metrics"}'))
            if name != "no_workload":
                inputs.append(series('digiorg_app_service_info{app_id="alpha",namespace="alpha",service="api"}'))
            if name != "provisioning":
                inputs.append(series('kube_deployment_spec_replicas{namespace="alpha",deployment="api"}', "0+0x10" if name == "scaled_zero" else "1+0x10"))
                if name != "missing_readiness":
                    inputs.append(series('kube_deployment_status_replicas_available{namespace="alpha",deployment="api"}', "0+0x10" if name in ("scaled_zero", "reduced") else "1+0x10"))
            fault = {
                "crashloop": 'kube_pod_container_status_waiting_reason{namespace="alpha",pod="api-a",reason="CrashLoopBackOff"}',
                "pending": 'kube_pod_status_phase{namespace="alpha",pod="api-a",phase="Pending"}',
                "unschedulable": 'kube_pod_status_unschedulable{namespace="alpha",pod="api-a"}',
                "oom": 'kube_pod_container_status_last_terminated_reason{namespace="alpha",pod="api-a",reason="OOMKilled"}',
                "restarts": 'kube_pod_container_status_restarts_total{namespace="alpha",pod="api-a",container="api"}',
            }.get(name)
            if fault:
                inputs.extend([series('kube_pod_owner{namespace="alpha",pod="api-a",owner_kind="ReplicaSet",owner_name="api-rs",owner_is_controller="true"}'),
                               series('kube_replicaset_owner{namespace="alpha",replicaset="api-rs",owner_kind="Deployment",owner_name="api",owner_is_controller="true"}')])
                inputs.append(series(fault, "0+1x10" if name == "restarts" else "1+0x10"))
            if name == "completed_job_fault":
                inputs.extend([series('kube_pod_container_status_last_terminated_reason{namespace="alpha",pod="old-build",reason="OOMKilled"}'),
                               series('kube_pod_owner{namespace="alpha",pod="old-build",owner_kind="Job",owner_name="build",owner_is_controller="true"}')])
            if name == "partial_services":
                inputs.append(series('digiorg_app_service_info{app_id="alpha",namespace="alpha",service="web"}'))
            if name == "stale_workload":
                for sample in inputs:
                    if sample['series'].startswith('kube_deployment_'):
                        sample['values'] = '1+0x3'
            if name == "collision":
                inputs.append(series('digiorg_app_info{app_id="alpha",namespace="alpha",claim_uid="uid-b"}'))
            # Completed provisioning Job never contributes to service faults.
            inputs.append(series('kube_job_status_failed{namespace="alpha",job_name="old-build"}', "10+0x10"))
            cases.append({"name": name, "interval": "30s", "input_series": inputs,
                          "promql_expr_test": [{"expr": "digiorg:app_status # " + name, "eval_time": "5m",
                                                "exp_samples": [{"labels": 'digiorg:app_status{app_id="alpha",namespace="alpha"}', "value": expected}]}]})
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            directory.chmod(0o755)
            (directory / "rules.yaml").write_text(yaml.safe_dump({"groups": groups}))
            (directory / "test.yaml").write_text(yaml.safe_dump({"rule_files": ["rules.yaml"], "evaluation_interval": "30s", "tests": cases}))
            result = subprocess.run(["docker", "run", "--rm", "--network=none", "--entrypoint", "/bin/promtool",
                                     "-v", f"{d}:/fixture:ro", "-w", "/fixture", IMAGE, "test", "rules", "test.yaml"],
                                    capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
