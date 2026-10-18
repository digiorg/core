"""Evaluate the webhook CEL predicate with pinned Kyverno's real CEL engine.

This proves predicate behavior, not installed API-server webhook registration.
"""
import copy
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / 'policies/kyverno/cluster-policies/protect-grafana-import.yaml'


class WebhookScopeTests(unittest.TestCase):
    def test_old_new_reserved_predicate(self):
        policy = yaml.safe_load(POLICY.read_text())
        conditions = policy['spec'].get('webhookConfiguration', {}).get('matchConditions', [])
        self.assertEqual(len(conditions), 1, 'API-server matchConditions required, not rule preconditions')
        self.assertEqual(policy['spec']['failurePolicy'], 'Fail')
        cli = os.environ.get('KYVERNO_CLI') or shutil.which('kyverno')
        self.assertIsNotNone(cli)
        # Project the actual expression into CEL validation so the CLI evaluates
        # it without pretending CLI policy filtering is API-server filtering.
        probe = {'apiVersion': 'kyverno.io/v1', 'kind': 'ClusterPolicy', 'metadata': {'name': 'scope-probe'}, 'spec': {
            'background': False, 'validationFailureAction': 'Enforce', 'rules': [{
                'name': 'scope', 'match': {'any': [{'resources': {'kinds': ['ConfigMap']}}]},
                'validate': {'cel': {'expressions': [{'expression': conditions[0]['expression'], 'message': 'outside protected scope'}]}}
            }]}}
        plain = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': 'ordinary', 'namespace': 'alpha'}}
        labelled = copy.deepcopy(plain)
        labelled['metadata']['labels'] = {'grafana_dashboard': '1'}
        annotated = copy.deepcopy(plain)
        annotated['metadata']['annotations'] = {'grafana_folder': '/tmp/dashboards/DigiOrg Apps'}
        reserved = copy.deepcopy(plain)
        reserved['metadata']['name'] = 'grafana-app-dev-alpha'
        cases = [('ordinary-create', plain, None, False), ('ordinary-update', plain, plain, False),
                 ('label-create', labelled, None, True), ('annotation-create', annotated, None, True),
                 ('name-create', reserved, None, True), ('strip-label', plain, labelled, True),
                 ('strip-folder', plain, annotated, True), ('label-update', labelled, plain, True)]
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / 'policy.yaml').write_text(yaml.safe_dump(probe))
            for name, obj, old, matches in cases:
                with self.subTest(name=name):
                    (directory / 'resource.yaml').write_text(yaml.safe_dump(obj))
                    # Empty old metadata keys prevent CLI's old/new merge from
                    # inheriting labelled new metadata into an ordinary old object.
                    old = copy.deepcopy(old)
                    if old is not None:
                        old['metadata'].setdefault('labels', {'grafana_dashboard': '0'})
                        old['metadata'].setdefault('annotations', {'grafana_folder': ''})
                    (directory / 'values.yaml').write_text(yaml.safe_dump({'apiVersion': 'cli.kyverno.io/v1alpha1', 'kind': 'Values', 'globalValues': {'request.oldObject': old, 'request.operation': 'UPDATE' if old else 'CREATE'}}))
                    result = subprocess.run([cli, 'apply', str(directory / 'policy.yaml'), '--resource', str(directory / 'resource.yaml'), '--values-file', str(directory / 'values.yaml'), '--remove-color'], capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0 if matches else 1, result.stdout + result.stderr)
                    self.assertNotIn('error: 1', result.stdout)


if __name__ == '__main__':
    unittest.main()
