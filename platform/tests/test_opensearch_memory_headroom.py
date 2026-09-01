"""Regression contracts for Issue #350 OpenSearch memory headroom."""

from importlib.util import module_from_spec, spec_from_file_location
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[2]
VALUES = ROOT / "platform" / "base" / "opensearch" / "values.yaml"
README = ROOT / "platform" / "base" / "opensearch" / "README.md"
RENDER_SCRIPT = ROOT / "scripts" / "render_platform_charts.py"
SPEC = spec_from_file_location("render_platform_charts", RENDER_SCRIPT)
assert SPEC and SPEC.loader
render_platform_charts = module_from_spec(SPEC)
SPEC.loader.exec_module(render_platform_charts)

VALID_RENDER = """
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: opensearch-cluster-master
spec:
  template:
    spec:
      containers:
        - name: opensearch
          env:
            - name: OPENSEARCH_JAVA_OPTS
              value: -Xmx512M -Xms512M
          resources:
            requests:
              cpu: 250m
              memory: 1Gi
            limits:
              cpu: 1000m
              memory: 2Gi
"""


class OpenSearchMemoryHeadroomTest(unittest.TestCase):
    def setUp(self):
        self.values = yaml.safe_load(VALUES.read_text(encoding="utf-8"))

    def test_combined_logs_and_traces_reserve_native_memory_headroom(self):
        self.assertEqual(self.values["opensearchJavaOpts"], "-Xmx512M -Xms512M")
        resources = self.values["resources"]
        self.assertEqual(resources["requests"]["memory"], "1Gi")
        self.assertEqual(resources["limits"]["memory"], "2Gi")
        self.assertEqual(resources["requests"]["cpu"], "250m")
        self.assertEqual(resources["limits"]["cpu"], "1000m")

    def test_render_gate_rejects_resource_drift(self):
        self.assertEqual(
            render_platform_charts.opensearch_resource_contract_errors(VALID_RENDER),
            [],
        )
        drifted = VALID_RENDER.replace("memory: 2Gi", "memory: 1Gi")
        self.assertTrue(
            render_platform_charts.opensearch_resource_contract_errors(drifted)
        )

    def test_render_gate_rejects_heap_drift(self):
        drifted = VALID_RENDER.replace(
            "-Xmx512M -Xms512M", "-Xmx1G -Xms1G"
        )
        errors = render_platform_charts.opensearch_resource_contract_errors(drifted)
        self.assertTrue(any("heap" in error for error in errors))

    def test_all_chart_main_invokes_the_opensearch_gate(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            app_directory = root / "apps" / "platform"
            values_directory = root / "platform" / "base" / "opensearch"
            app_directory.mkdir(parents=True)
            values_directory.mkdir(parents=True)
            (values_directory / "values.yaml").write_text("{}\n", encoding="utf-8")
            (app_directory / "opensearch.yaml").write_text(
                """
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: opensearch
spec:
  sources:
    - repoURL: https://opensearch-project.github.io/helm-charts
      chart: opensearch
      targetRevision: 3.7.0
      helm:
        valueFiles:
          - $values/platform/base/opensearch/values.yaml
  destination:
    namespace: platform-db
""",
                encoding="utf-8",
            )
            drifted = VALID_RENDER.replace(
                "-Xmx512M -Xms512M", "-Xmx1G -Xms1G"
            )
            completed = render_platform_charts.subprocess.CompletedProcess(
                args=[], returncode=0, stdout=drifted, stderr=""
            )
            output_directory = root / "renders"
            with (
                mock.patch.object(render_platform_charts, "ROOT", root),
                mock.patch.object(
                    render_platform_charts.subprocess,
                    "run",
                    return_value=completed,
                ),
                mock.patch.object(
                    sys,
                    "argv",
                    ["render_platform_charts.py", "--output-dir", str(output_directory)],
                ),
                mock.patch.object(sys, "stderr", io.StringIO()),
                mock.patch.object(sys, "stdout", io.StringIO()),
            ):
                self.assertEqual(render_platform_charts.main(), 1)

    def test_documentation_matches_the_combined_workload_contract(self):
        readme = README.read_text(encoding="utf-8")
        self.assertIn("combined Fluentd logs and Jaeger traces", readme)
        self.assertIn("1Gi request / 2Gi limit", readme)
        self.assertIn("512Mi heap", readme)
        self.assertNotIn("Memory | 512Mi request / 1Gi limit", readme)


if __name__ == "__main__":
    unittest.main()
