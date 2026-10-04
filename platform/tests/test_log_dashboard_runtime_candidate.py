"""Bounded declarative consumer graph; no release generator or cluster access."""
from copy import deepcopy
from pathlib import Path
import subprocess
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
RETAINED = 'f6e7d58c0b03ee6a3ec6ed9e1e22e5023f861549'
WRITER = '8e6b8908f99ebf76db47c15613eff523644c23f6'
MERGE = '41b77b1b1af726563c7209f6d51f57a5ab1a03e4'
TAG = 'log-dashboard-runtime-v1'
PREFIX = 'platform/runtime/log-dashboard-v1'

def blob(path):
    return subprocess.check_output(['git', 'show', f'{RETAINED}:{path}'], cwd=ROOT)

def old(path):
    return yaml.safe_load(blob(path))

def candidate(path):
    return yaml.safe_load((ROOT / PREFIX / path).read_text())

def paths():
    return [p for p in subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', RETAINED, 'apps'], cwd=ROOT, text=True).splitlines() if p.endswith('.yaml')]

class RuntimeCandidate(unittest.TestCase):
    def test_complete_preserved_unrelated_inventory(self):
        original = paths()
        actual = sorted(str(p.relative_to(ROOT / PREFIX)) for p in (ROOT / PREFIX / 'apps').rglob('*.yaml'))
        self.assertEqual(actual, sorted(original))
        self.assertEqual(len(actual), 31)
        allowed = {'apps/platform/argocd.yaml', 'apps/platform/grafana.yaml', 'apps/platform/fluentd.yaml'}
        for path in original:
            if path not in allowed:
                with self.subTest(path=path):
                    actual_bytes = (ROOT / PREFIX / path).read_bytes()
                    if path == 'apps/platform/gitea.yaml':
                        # Only four inherited whitespace-only lines are normalized.
                        normalized = b'\n'.join(line.rstrip() for line in blob(path).split(b'\n'))
                        self.assertEqual(actual_bytes, normalized)
                        self.assertEqual(yaml.safe_load(actual_bytes), old(path))
                    else:
                        self.assertEqual(actual_bytes, blob(path))

    def test_argo_only_changes_necessary_owner_link(self):
        expected = old('apps/platform/argocd.yaml')
        expected['spec']['source']['targetRevision'] = TAG
        expected['spec']['source']['path'] = PREFIX + '/argocd'
        self.assertEqual(candidate('apps/platform/argocd.yaml'), expected)

    def test_grafana_only_values_pin_and_retained_manual_gate(self):
        expected = old('apps/platform/grafana.yaml')
        expected['spec']['sources'][1]['targetRevision'] = MERGE
        self.assertEqual(candidate('apps/platform/grafana.yaml'), expected)
        self.assertEqual(expected['spec']['sources'][0]['targetRevision'], '87.17.0')
        self.assertNotIn('automated', expected['spec']['syncPolicy'])

    def test_fluentd_exact_reviewed_transform_preserves_writer_and_owner(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('delivery', ROOT / 'scripts/log_dashboard_delivery.py')
        assert spec is not None and spec.loader is not None
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        original = old('apps/platform/fluentd.yaml')
        original['spec']['source']['targetRevision'] = WRITER
        self.assertEqual(candidate('apps/platform/fluentd.yaml'), tool.prepare(original, MERGE))

    def test_actual_argo_render_closes_root_cycle_without_owner_exception(self):
        output = subprocess.check_output(['kustomize', 'build', str(ROOT / PREFIX / 'argocd')], text=True)
        docs = [d for d in yaml.safe_load_all(output) if d]
        roots = [d for d in docs if d['kind'] == 'Application' and d['metadata']['name'] == 'root-app']
        self.assertEqual(len(roots), 1)
        expected = old('platform/base/argocd/applications/root-app.yaml')
        expected['spec']['source']['targetRevision'] = TAG
        expected['spec']['source']['path'] = PREFIX + '/apps'
        self.assertEqual(roots[0], expected)
        # Retained and canonical Argo non-Root configuration must be identical.
        retained_paths = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', RETAINED, 'platform/base/argocd'], cwd=ROOT, text=True).splitlines()
        for path in retained_paths:
            if path.endswith('/applications/root-app.yaml'):
                continue
            with self.subTest(path=path):
                self.assertEqual((ROOT / path).read_bytes(), blob(path))
        source = roots[0]['spec']['source']
        self.assertEqual(source['directory'], {'recurse': True, 'include': '*.yaml'})
        self.assertEqual(len(list((ROOT / source['path']).rglob('*.yaml'))), 31)
        self.assertEqual(candidate('apps/platform/argocd.yaml')['spec']['source']['targetRevision'], source['targetRevision'])

if __name__ == '__main__':
    unittest.main(verbosity=2)
