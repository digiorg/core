#!/usr/bin/env python3
"""Core #378: exact-chart scrape contract and opt-in real Grafana differential.

Static CI runs the fail-closed Helm regression. To execute the isolated component:
GRAFANA_SELF_METRICS_COMPONENT=1 python3 -m unittest discover -s platform/tests \
    -p test_grafana_self_metrics.py -v
The component needs Docker and the exact image already pulled; never pulls a tag,
contacts a cluster, follows the HTTPS redirect, or disables TLS verification.
"""
from copy import deepcopy
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid

import yaml

ROOT = Path(__file__).resolve().parents[2]
VALUES = ROOT / "platform/base/grafana/values.yaml"
APP = ROOT / "apps/platform/grafana.yaml"
IMAGE = "grafana/grafana@sha256:6ea068891652aa6a65ca9065c26b89de939653803c836426970305c11fd00534"
CONFIG_DIGEST = "sha256:e76fd1761e3cc1dd6071a53484b72762f8b358bb1ecd89c9e21d57090956998e"
REVISION = "b309c9bb3b81a748c3a75289236a27309ed2566a"
CHART_SHA256 = "e9d625daece8804bfa82959296e59cf146756d9aa638f207ebe20e01d6d75514"


def run(*args):
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True,
                          check=True, timeout=180).stdout


def render(inherited=False):
    app = yaml.safe_load(APP.read_text())
    source = app["spec"]["sources"][0]
    if (source["chart"], source["targetRevision"], source["repoURL"]) != (
        "kube-prometheus-stack", "87.17.0",
        "https://prometheus-community.github.io/helm-charts",
    ):
        raise AssertionError("reconcile Grafana regression with changed chart pin")
    with tempfile.TemporaryDirectory(prefix="grafana-378-chart-") as directory:
        run("helm", "pull", source["chart"], "--repo", source["repoURL"],
            "--version", source["targetRevision"], "--destination", directory)
        chart = Path(directory) / "kube-prometheus-stack-87.17.0.tgz"
        if hashlib.sha256(chart.read_bytes()).hexdigest() != CHART_SHA256:
            raise AssertionError("exact kube-prometheus-stack package checksum mismatch")
        args = ["helm", "template", "prometheus", str(chart), "--namespace",
                "monitoring", "-f", str(VALUES), "--set",
                "grafana.adminPassword=task-fixture-not-a-credential"]
        # Missing path inherits /metrics. Explicitly reproduce it without editing source.
        if inherited:
            args += ["--set", "grafana.serviceMonitor.path=/metrics"]
        return [doc for doc in yaml.safe_load_all(run(*args)) if isinstance(doc, dict)]


def resource(documents, kind, name):
    matches = [doc for doc in documents if doc.get("kind") == kind
               and doc["metadata"]["name"] == name]
    if len(matches) != 1:
        raise AssertionError(f"expected exactly one {kind}/{name}, got {len(matches)}")
    return matches[0]


class GrafanaSelfMetricsRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.documents = render()

    def test_monitor_matches_server_subpath(self):
        monitor = resource(self.documents, "ServiceMonitor", "prometheus-grafana")
        endpoint, = monitor["spec"]["endpoints"]
        server = yaml.safe_load(VALUES.read_text())["grafana"]["grafana.ini"]["server"]
        self.assertTrue(server["serve_from_sub_path"])
        self.assertEqual(server["root_url"], "https://digiorg.local/grafana")
        expected = urllib.parse.urlsplit(server["root_url"]).path.rstrip("/") + "/metrics"
        self.assertEqual(endpoint["path"], expected)
        self.assertEqual(endpoint["scheme"], "http")
        self.assertEqual(endpoint["port"], "http-web")
        self.assertNotIn("tlsConfig", endpoint)
        self.assertNotIn("authorization", endpoint)

    def test_only_rendered_monitor_path_changes(self):
        inherited = render(inherited=True)
        candidate = deepcopy(self.documents)
        old = resource(inherited, "ServiceMonitor", "prometheus-grafana")
        new = resource(candidate, "ServiceMonitor", "prometheus-grafana")
        self.assertEqual(old["spec"]["endpoints"][0]["path"], "/metrics")
        self.assertEqual(new["spec"]["endpoints"][0]["path"], "/grafana/metrics")
        new["spec"]["endpoints"][0]["path"] = "/metrics"
        self.assertEqual(candidate, inherited, "unrelated rendered resources changed")

    def test_manual_upgrade_gate_remains(self):
        app = yaml.safe_load(APP.read_text())
        self.assertEqual(app["metadata"]["annotations"]["platform.digiorg.io/upgrade-gate"],
                         "issue-275-manual")
        self.assertNotIn("automated", app["spec"]["syncPolicy"])


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@unittest.skipUnless(os.environ.get("GRAFANA_SELF_METRICS_COMPONENT") == "1",
                     "real Docker component requires explicit opt-in; not runtime acceptance")
class GrafanaSelfMetricsComponentTest(unittest.TestCase):
    def test_exact_image_root_redirect_and_selected_metrics(self):
        documents = render()
        deployment = resource(documents, "Deployment", "prometheus-grafana")
        grafana, = [c for c in deployment["spec"]["template"]["spec"]["containers"]
                    if c["name"] == "grafana"]
        self.assertEqual(grafana["image"], "docker.io/grafana/grafana:13.1.0")
        # Verify registry manifest bytes and config before executing the cached image.
        manifest = json.loads(run("docker", "manifest", "inspect", "--verbose", IMAGE))
        raw = base64.b64decode(manifest["Raw"], validate=True)
        self.assertEqual("sha256:" + hashlib.sha256(raw).hexdigest(), IMAGE.split("@")[1])
        self.assertEqual(json.loads(raw)["config"]["digest"], CONFIG_DIGEST)
        with urllib.request.urlopen(
            "https://auth.docker.io/token?service=registry.docker.io&scope=repository:grafana/grafana:pull",
            timeout=30,
        ) as response:
            token = json.load(response)["token"]
        request = urllib.request.Request(
            "https://registry-1.docker.io/v2/grafana/grafana/blobs/" + CONFIG_DIGEST,
            headers={"Authorization": "Bearer " + token},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            config_bytes = response.read()
        self.assertEqual("sha256:" + hashlib.sha256(config_bytes).hexdigest(), CONFIG_DIGEST)
        config = json.loads(config_bytes)
        local, = json.loads(run("docker", "image", "inspect", IMAGE))
        self.assertEqual((config["os"], config["architecture"]), ("linux", "amd64"))
        self.assertEqual(local["RootFS"]["Layers"], config["rootfs"]["diff_ids"])
        self.assertIn(IMAGE, local["RepoDigests"])
        server = yaml.safe_load(VALUES.read_text())["grafana"]["grafana.ini"]["server"]
        name = "core-378-metrics-" + uuid.uuid4().hex[:12]
        created = False
        try:
            run("docker", "create", "--pull=never", "--name", name,
                "--label", "digiorg.task=core-378-self-metrics", "--cap-drop=ALL",
                "--security-opt=no-new-privileges", "--memory=512m", "--cpus=1",
                "-p", "127.0.0.1::3000", "-e", "GF_SERVER_ROOT_URL=" + server["root_url"],
                "-e", "GF_SERVER_SERVE_FROM_SUB_PATH=true",
                "-e", "GF_PLUGINS_PREINSTALL_DISABLED=true",
                "-e", "GF_ANALYTICS_REPORTING_ENABLED=false",
                "-e", "GF_ANALYTICS_CHECK_FOR_UPDATES=false", IMAGE)
            created = True
            run("docker", "start", name)
            container, = json.loads(run("docker", "inspect", name))
            self.assertFalse(container["HostConfig"]["Privileged"])
            self.assertNotEqual(container["HostConfig"]["NetworkMode"], "host")
            self.assertFalse(container["Mounts"])
            binding, = container["NetworkSettings"]["Ports"]["3000/tcp"]
            self.assertEqual(binding["HostIp"], "127.0.0.1")
            base = "http://127.0.0.1:" + binding["HostPort"]
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            for _ in range(90):
                try:
                    with opener.open(base + "/grafana/api/health", timeout=2) as response:
                        if response.status == 200:
                            break
                except (urllib.error.URLError, TimeoutError, ConnectionError):
                    pass
                time.sleep(1)
            else:
                self.fail("exact Grafana component did not become healthy")
            # Establish process version/revision before accepting handler evidence.
            with opener.open(base + "/grafana/metrics", timeout=10) as response:
                text = response.read().decode()
                self.assertEqual(response.status, 200)
                self.assertIsNone(response.headers.get("Location"))
                self.assertIn("text/plain", response.headers.get("Content-Type", ""))
            build, = [line for line in text.splitlines() if line.startswith("grafana_build_info{")]
            self.assertIn('version="13.1.0"', build)
            self.assertIn('revision="' + REVISION + '"', build)
            with self.assertRaises(urllib.error.HTTPError) as caught:
                opener.open(base + "/metrics", timeout=10)
            self.assertEqual(caught.exception.code, 301)
            self.assertEqual(caught.exception.headers["Location"],
                             server["root_url"] + "/metrics")
            caught.exception.close()
            print(json.dumps({"image": IMAGE, "config_digest": CONFIG_DIGEST,
                              "build_info": build, "root_status": caught.exception.code,
                              "root_location": caught.exception.headers["Location"],
                              "subpath_status": 200, "loopback_only": True,
                              "cluster_validation": False}))
            endpoint, = resource(documents, "ServiceMonitor", "prometheus-grafana")["spec"]["endpoints"]
            try:
                selected = opener.open(base + endpoint["path"], timeout=10)
            except urllib.error.HTTPError as error:
                error.close()
                self.fail(f"rendered selected path {endpoint['path']} returned {error.code}, expected 200/no redirect")
            with selected as response:
                self.assertEqual(response.status, 200)
                self.assertIsNone(response.headers.get("Location"))
                self.assertIn(build, response.read().decode())
            print(json.dumps({"image": IMAGE, "config_digest": CONFIG_DIGEST,
                              "build_info": build, "root_status": caught.exception.code,
                              "root_location": caught.exception.headers["Location"],
                              "selected_path": endpoint["path"], "selected_status": 200,
                              "loopback_only": True, "cluster_validation": False}))
        finally:
            if created:
                run("docker", "rm", "--force", name)


if __name__ == "__main__":
    unittest.main()
