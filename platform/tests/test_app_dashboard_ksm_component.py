"""Real pinned KSM process against a synthetic loopback-only API, never a cluster.

Run explicitly: python -m unittest discover -s platform/tests -p '*ksm_component.py'.
The fixture asserts startup with no CRD and later inventory without process restart.
"""
import http.server
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
import yaml

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "registry.k8s.io/kube-state-metrics/kube-state-metrics:v2.19.1"


def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class KsmComponentTests(unittest.TestCase):
    def test_crd_late_inventory_services_conditions_and_narrow_api_reads(self):
        state = {"crd": False, "paths": [], "deleted": False}
        claim = {"apiVersion": "platform.digiorg.io/v1alpha1", "kind": "AppClaim",
                 "metadata": {"name": "claim-a", "namespace": "app-claims", "uid": "claim-uid", "resourceVersion": "1"},
                 "spec": {"appName": "alpha", "team": "test", "services": [{"name": "api"}, {"name": "web"}]},
                 "status": {"conditions": [{"type": "Ready", "status": "False"}]}}
        crd = {"apiVersion": "apiextensions.k8s.io/v1", "kind": "CustomResourceDefinition",
               "metadata": {"name": "appclaims.platform.digiorg.io", "resourceVersion": "1", "uid": "crd-uid"},
               "spec": {"group": "platform.digiorg.io", "names": {"kind": "AppClaim", "plural": "appclaims"},
                        "scope": "Namespaced", "versions": [{"name": "v1alpha1", "served": True, "storage": True}]}}
        namespace = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "alpha", "resourceVersion": "1", "labels": {
            "platform.digiorg.io/type": "app", "platform.digiorg.io/app-id": "alpha", "platform.digiorg.io/team": "test",
            "do-not-export": "private"}}, "status": {"phase": "Active"}}

        class API(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                state["paths"].append(self.path)
                path = self.path.split("?", 1)[0]
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if "watch=true" in self.path:
                    is_crd = "customresourcedefinitions" in path
                    sent = False
                    deleted = False
                    initial = ([crd] if state["crd"] else []) if is_crd else ([claim] if "appclaims" in path and not state["deleted"] else ([namespace] if "namespaces" in path else []))
                    if "sendInitialEvents=true" in self.path:
                        for obj in initial:
                            self.wfile.write((json.dumps({"type": "ADDED", "object": obj}) + "\n").encode())
                        prototype = crd if is_crd else (claim if "appclaims" in path else namespace)
                        bookmark = {"apiVersion": prototype["apiVersion"], "kind": prototype["kind"], "metadata": {"resourceVersion": "1", "annotations": {"k8s.io/initial-events-end": "true"}}}
                        self.wfile.write((json.dumps({"type": "BOOKMARK", "object": bookmark}) + "\n").encode())
                        self.wfile.flush()
                        sent = is_crd and state["crd"]
                    end = time.monotonic() + 70
                    while time.monotonic() < end:
                        try:
                            if is_crd and state["crd"] and not sent:
                                self.wfile.write((json.dumps({"type": "ADDED", "object": crd}) + "\n").encode())
                                self.wfile.flush()
                                sent = True
                            elif "appclaims" in path and state["deleted"] and not deleted:
                                self.wfile.write((json.dumps({"type": "DELETED", "object": claim}) + "\n").encode())
                                self.wfile.flush()
                                deleted = True
                            time.sleep(.1)
                        except (BrokenPipeError, ConnectionResetError):
                            break
                    return
                if path == "/version":
                    body = {"major": "1", "minor": "35", "gitVersion": "v1.35.0"}
                elif path.endswith("customresourcedefinitions"):
                    body = {"apiVersion": "apiextensions.k8s.io/v1", "kind": "CustomResourceDefinitionList",
                            "metadata": {"resourceVersion": "1"}, "items": [crd] if state["crd"] else []}
                elif path.endswith("appclaims"):
                    body = {"apiVersion": "platform.digiorg.io/v1alpha1", "kind": "AppClaimList",
                            "metadata": {"resourceVersion": "1"}, "items": [] if state["deleted"] else [claim]}
                elif path.endswith("namespaces"):
                    body = {"apiVersion": "v1", "kind": "NamespaceList", "metadata": {"resourceVersion": "1"}, "items": [namespace]}
                else:
                    body = {"kind": "APIResourceList", "groupVersion": "v1", "resources": []}
                self.wfile.write(json.dumps(body).encode())

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), API)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        metrics_port, telemetry_port = port(), port()
        name = "issue375-ksm-" + str(os.getpid())
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            directory.chmod(0o755)
            values = yaml.safe_load((ROOT / "platform/base/grafana/values.yaml").read_text())
            ksm = values["kube-state-metrics"]
            (directory / "config.yaml").write_text(yaml.safe_dump(ksm["customResourceState"]["config"]))
            kubeconfig = {"apiVersion": "v1", "kind": "Config", "clusters": [{"name": "fixture", "cluster": {"server": f"http://127.0.0.1:{server.server_port}"}}],
                          "contexts": [{"name": "fixture", "context": {"cluster": "fixture", "user": "fixture"}}],
                          "current-context": "fixture", "users": [{"name": "fixture", "user": {}}]}
            (directory / "kubeconfig").write_text(yaml.safe_dump(kubeconfig))
            for f in directory.iterdir():
                f.chmod(0o644)
            args = ["docker", "run", "--rm", "--name", name, "--network", "host", "--read-only", "--cap-drop=ALL",
                    "-v", f"{d}:/fixture:ro", IMAGE, "--kubeconfig=/fixture/kubeconfig", "--resources=namespaces",
                    f"--port={metrics_port}", f"--telemetry-port={telemetry_port}",
                    "--custom-resource-state-config-file=/fixture/config.yaml",
                    "--metric-labels-allowlist=" + ",".join(ksm["metricLabelsAllowlist"])]
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                def fetch():
                    with urllib.request.urlopen(f"http://127.0.0.1:{metrics_port}/metrics", timeout=2) as r:
                        return r.read().decode()

                def wait_for(check):
                    until = time.monotonic() + 50
                    last = ""
                    while time.monotonic() < until:
                        if proc.poll() is not None:
                            self.fail("KSM exited: " + proc.stdout.read())
                        try:
                            last = fetch()
                            if check(last):
                                return last
                        except (OSError, TimeoutError):
                            pass
                        time.sleep(.25)
                    self.fail("KSM metrics timeout: " + last[-5000:])

                before = wait_for(lambda m: "kube_namespace_labels{" in m)
                self.assertNotIn("digiorg_app_info{", before)
                state["crd"] = True
                metrics = wait_for(lambda m: 'service="web"' in m and "digiorg_app_info{" in m)
                rows = [s for s in metrics.splitlines() if s.startswith("digiorg_app_")]
                self.assertTrue(any(s.startswith("digiorg_app_info{") and 'namespace="alpha"' in s and 'claim_namespace="app-claims"' in s for s in rows), rows)
                self.assertTrue(any(s.startswith("digiorg_app_service_info{") and 'service="api"' in s for s in rows), rows)
                self.assertTrue(any('condition="Ready"' in s and 'status="False"' in s and s.endswith(" 1") for s in rows), rows)
                self.assertIn('label_platform_digiorg_io_app_id="alpha"', metrics)
                self.assertNotIn("do_not_export", metrics)
                state["deleted"] = True
                wait_for(lambda m: "digiorg_app_info{" not in m)
                self.assertIsNone(proc.poll(), "CRD-late collection must not require restart")
                self.assertFalse(any("secrets" in p or "/appclaims/claim-a" in p for p in state["paths"]), state["paths"])
                self.assertTrue(any("watch=true" in p and "customresourcedefinitions" in p for p in state["paths"]))
            finally:
                subprocess.run(["docker", "stop", "-t", "2", name], capture_output=True, timeout=15)
                output, _ = proc.communicate(timeout=15)
                if output:
                    print("Synthetic KSM process output:\n" + output[-12000:])
                server.shutdown()


if __name__ == "__main__":
    unittest.main()
