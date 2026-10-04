#!/usr/bin/env python3
"""Offline separation contracts: same Application owner, independent sources."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/log_dashboard_delivery.py'
RETAINED = '8e6b8908f99ebf76db47c15613eff523644c23f6'
DASHBOARD = '54a66660baf6d96b5111e75a4c58519e2f3e2167'
CM = ('ConfigMap', 'logging', 'fluentd-grafana-dashboards')


def module():
    if not SCRIPT.exists():
        raise AssertionError('independent dashboard delivery implementation is missing')
    spec = importlib.util.spec_from_file_location('log_dashboard_delivery', SCRIPT)
    assert spec is not None and spec.loader is not None
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def app():
    value = yaml.safe_load((ROOT / 'apps/platform/fluentd.yaml').read_text())
    value['spec']['source']['targetRevision'] = RETAINED
    return value


def inventory(documents):
    result = {}
    for doc in documents:
        if not doc:
            continue
        identity = (doc['kind'], doc['metadata'].get('namespace', ''), doc['metadata']['name'])
        if identity in result:
            raise AssertionError(f'duplicate rendered identity: {identity}')
        result[identity] = doc
    return result


class DeliveryContracts(unittest.TestCase):
    def test_independent_sources_preserve_application_owner_and_policy(self):
        before = app()
        original = deepcopy(before)
        after = module().prepare(before, DASHBOARD)
        self.assertEqual(before, original, 'pure transformation must not mutate input')
        expected = deepcopy(before)
        del expected['spec']['source']
        expected['spec']['sources'] = after['spec']['sources']
        self.assertEqual(after, expected)
        writer, dashboard = after['spec']['sources']
        self.assertEqual(writer['targetRevision'], RETAINED)
        self.assertEqual(writer['path'], 'platform/base/fluentd')
        self.assertEqual(dashboard, {
            'repoURL': 'https://github.com/digiorg/core.git',
            'targetRevision': DASHBOARD,
            'path': 'platform/base/log-dashboards',
        })
        self.assertNotIn('source', after['spec'])

    def test_floating_pins_and_unexpected_source_shapes_fail_closed(self):
        tool = module()
        for revision in ['main', 'v1', '', 'a' * 39, 'a' * 41, 'A' * 40]:
            with self.subTest(revision=revision):
                with self.assertRaises(ValueError):
                    tool.prepare(app(), revision)
                value = app()
                value['spec']['source']['targetRevision'] = revision
                with self.assertRaises(ValueError):
                    tool.prepare(value, DASHBOARD)
        for field, value in [('path', 'platform/base/opensearch'), ('repoURL', 'https://example.org/core.git'), ('kustomize', {'patches': []})]:
            changed = app()
            changed['spec']['source'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                tool.prepare(changed, DASHBOARD)
        wrong = app()
        wrong['metadata']['name'] = 'opensearch'
        with self.assertRaises(ValueError):
            tool.prepare(wrong, DASHBOARD)
        wrong = app()
        wrong['spec']['sources'] = []
        with self.assertRaises(ValueError):
            tool.prepare(wrong, DASHBOARD)

    @unittest.skipUnless(shutil.which('kustomize'), 'Kustomize is exercised in the render gate')
    def test_actual_retained_render_has_no_writer_schema_or_ownership_delta(self):
        tool = module()
        sources = tool.prepare(app(), DASHBOARD)['spec']['sources']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # Use the actual immutable retained producer, not a invented writer fixture.
            for relative in subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', RETAINED, '--', 'platform/base/fluentd'], cwd=ROOT, text=True).splitlines():
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(subprocess.check_output(['git', 'show', f'{RETAINED}:{relative}'], cwd=ROOT))
            writer = root / 'platform/base/fluentd'
            original = inventory(yaml.safe_load_all(subprocess.check_output(['kustomize', 'build', str(writer)], text=True)))
            kustomization = yaml.safe_load((writer / 'kustomization.yaml').read_text())
            kustomization['patches'] = sources[0]['kustomize']['patches']
            (writer / 'kustomization.yaml').write_text(yaml.safe_dump(kustomization))
            retained = inventory(yaml.safe_load_all(subprocess.check_output(['kustomize', 'build', str(writer)], text=True)))
            dashboards = inventory(yaml.safe_load_all(subprocess.check_output(['kustomize', 'build', str(ROOT / sources[1]['path'])], text=True)))
            self.assertEqual(set(dashboards), {CM})
            self.assertNotIn(CM, retained)
            combined = inventory(list(retained.values()) + list(dashboards.values()))
            self.assertEqual(set(combined), set(original), 'no additions or pruned resources')
            self.assertEqual({key: value for key, value in original.items() if key != CM}, retained)
            self.assertNotEqual(original[CM]['data'], combined[CM]['data'])
            self.assertFalse(any(key[0] == 'Job' for key in combined))
            # Current main retains standalone compatibility, still owns exactly one CM.
            current = inventory(yaml.safe_load_all(subprocess.check_output(['kustomize', 'build', str(ROOT / 'platform/base/fluentd')], text=True)))
            self.assertEqual(current[CM], dashboards[CM])

    @unittest.skipUnless(shutil.which('kustomize'), 'Kustomize is exercised in the render gate')
    def test_current_writer_keeps_its_hook_and_exact_other_resources(self):
        sources = module().prepare(app(), DASHBOARD)['spec']['sources']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ['fluentd', 'log-dashboards']:
                shutil.copytree(ROOT / 'platform/base' / name, root / name)
            writer = root / 'fluentd'
            before = inventory(yaml.safe_load_all(subprocess.check_output(['kustomize', 'build', str(writer)], text=True)))
            path = writer / 'kustomization.yaml'
            value = yaml.safe_load(path.read_text())
            value['patches'] = sources[0]['kustomize']['patches']
            path.write_text(yaml.safe_dump(value))
            after = inventory(yaml.safe_load_all(subprocess.check_output(['kustomize', 'build', str(writer)], text=True)))
            self.assertEqual(after, {key: value for key, value in before.items() if key != CM})
            self.assertIn(('Job', 'logging', 'fluentd-log-schema'), after)
            self.assertEqual(after[('Job', 'logging', 'fluentd-log-schema')]['metadata']['annotations']['argocd.argoproj.io/hook'], 'PreSync')

    def test_malformed_metadata_spec_and_destination_fail_closed(self):
        tool = module()
        for field in ['metadata', 'spec']:
            for value in [None, [], 'invalid']:
                changed = app()
                changed[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    tool.prepare(changed, DASHBOARD)
        changed = app()
        changed['spec']['destination']['namespace'] = 'platform-db'
        with self.assertRaises(ValueError):
            tool.prepare(changed, DASHBOARD)

    def test_cli_exclusive_output_and_readonly_input(self):
        tool = module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'fluentd.yaml'
            source.write_text(yaml.safe_dump(app()))
            before = source.read_bytes()
            output = root / 'prepared.yaml'
            command = [sys.executable, str(SCRIPT), '--application', str(source), '--dashboard-commit', DASHBOARD, '--output', str(output)]
            result = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(yaml.safe_load(output.read_text()), tool.prepare(app(), DASHBOARD))
            self.assertEqual(source.read_bytes(), before)
            existing = output.read_bytes()
            result = subprocess.run(command, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(output.read_bytes(), existing)


if __name__ == '__main__':
    unittest.main(verbosity=2)
