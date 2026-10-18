"""Actual pinned Helm file-provider render, with a baseline legacy dashboard comparison."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import urllib.request
import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART_URL = 'https://github.com/prometheus-community/helm-charts/releases/download/kube-prometheus-stack-87.17.0/kube-prometheus-stack-87.17.0.tgz'
CHART_SHA = 'e9d625daece8804bfa82959296e59cf146756d9aa638f207ebe20e01d6d75514'


class FolderProviderTests(unittest.TestCase):
    def test_real_provider_single_tree_and_legacy_dashboard_preservation(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            chart = Path(os.environ.get('APP_DASHBOARD_CHART', directory / 'chart.tgz'))
            if not chart.exists():
                with urllib.request.urlopen(CHART_URL, timeout=60) as response:
                    chart.write_bytes(response.read())
            self.assertEqual(hashlib.sha256(chart.read_bytes()).hexdigest(), CHART_SHA)
            old = subprocess.run(['git', 'show', 'ca30a538648ef46a55f3f4da511e1ed0fb60e80d:platform/base/grafana/values.yaml'], cwd=ROOT, capture_output=True, text=True, check=True).stdout
            (directory / 'baseline.yaml').write_text(old)
            def render(values, template):
                result = subprocess.run(['helm', 'template', 'prometheus', str(chart), '--namespace', 'monitoring', '-f', str(values), '--show-only', template], capture_output=True, text=True, check=True, timeout=60)
                docs = list(yaml.safe_load_all(result.stdout))
                self.assertFalse(any(doc.get('kind') == 'Secret' for doc in docs))
                return docs
            values = ROOT / 'platform/base/grafana/values.yaml'
            providers = render(values, 'charts/grafana/templates/configmap-dashboard-provider.yaml')
            self.assertEqual(len(providers), 1)
            provider = yaml.safe_load(providers[0]['data']['provider.yaml'])['providers']
            self.assertEqual(len(provider), 1)
            self.assertTrue(provider[0]['options']['foldersFromFilesStructure'])
            # Pinned chart deliberately omits folder when foldersFromFilesStructure=true.
            self.assertNotIn('folder', provider[0])
            self.assertEqual(provider[0]['options']['path'], '/tmp/dashboards')
            legacy = 'templates/grafana/dashboards-1.14/k8s-resources-namespace.yaml'
            self.assertEqual(render(values, legacy), render(directory / 'baseline.yaml', legacy))
            self.assertNotIn('grafana_folder', render(values, legacy)[0]['metadata'].get('annotations', {}))
            def full_render(value_file):
                result = subprocess.run(['helm', 'template', 'prometheus', str(chart), '--namespace', 'monitoring', '-f', str(value_file)], capture_output=True, text=True, check=True, timeout=60)
                return [doc for doc in yaml.safe_load_all(result.stdout) if doc]
            current = full_render(values)
            baseline = full_render(directory / 'baseline.yaml')
            def dashboard_imports(docs):
                return [doc for doc in docs if doc.get('metadata', {}).get('labels', {}).get('grafana_dashboard') == '1']
            self.assertEqual(dashboard_imports(current), dashboard_imports(baseline))
            self.assertTrue(dashboard_imports(current))
            self.assertTrue(all(doc['kind'] == 'ConfigMap' for doc in dashboard_imports(current)))
            def sidecars(docs):
                deployment = next(doc for doc in docs if doc['kind'] == 'Deployment' and doc['metadata']['name'] == 'prometheus-grafana')
                return {container['name']: container for container in deployment['spec']['template']['spec']['containers']}
            containers = sidecars(current)
            env = {item['name']: item.get('value') for item in containers['grafana-sc-dashboard']['env']}
            self.assertEqual(env['RESOURCE'], 'configmap', 'Secret dashboard import must be disabled')
            self.assertEqual(env['LABEL'], 'grafana_dashboard')
            self.assertEqual(env['LABEL_VALUE'], '1')
            self.assertEqual(containers['grafana-sc-datasources'], sidecars(baseline)['grafana-sc-datasources'])


if __name__ == '__main__':
    unittest.main()
