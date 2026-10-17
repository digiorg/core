"""Pinned Kyverno CLI with an isolated loopback API; never uses a real kubeconfig.

The API is synthetic. This proves actual GET/context/deny behavior, not API-server
webhook registration, Crossplane reconciliation or production effective permissions.
"""
import copy
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / 'policies/kyverno/cluster-policies/protect-grafana-import.yaml'
PROVIDER = 'system:serviceaccount:crossplane-system:crossplane-provider-kubernetes'


def configmap(uid='xr-uid'):
    return {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {
        'name': 'grafana-app-dev-alpha', 'namespace': 'alpha',
        'labels': {'grafana_dashboard': '1'}, 'annotations': {
            'grafana_folder': '/tmp/dashboards/DigiOrg Apps',
            'platform.digiorg.io/app-id': 'alpha', 'platform.digiorg.io/dashboard-owner': uid}},
        'data': {'app-dev-alpha.json': '{}'}}


def managed_object(uid='xr-uid'):
    return {'apiVersion': 'kubernetes.crossplane.io/v1alpha2', 'kind': 'Object',
            'metadata': {'name': 'alpha-grafana-dashboard', 'uid': 'object-uid',
                         'ownerReferences': [{'apiVersion': 'platform.digiorg.io/v1alpha1',
                                              'kind': 'Application', 'name': 'alpha-xr',
                                              'uid': uid, 'controller': True}]},
            'spec': {'forProvider': {'manifest': configmap(uid)}}}


class OwnerComponentTests(unittest.TestCase):
    def test_exact_owner_lookup_permission(self):
        path = ROOT / 'policies/kyverno/cluster-policies/grafana-owner-lookup-rbac.yaml'
        self.assertTrue(path.exists(), 'approved exact-subject GET-only grant missing')
        role, binding = list(yaml.safe_load_all(path.read_text()))
        self.assertEqual(role['rules'], [{'apiGroups': ['kubernetes.crossplane.io'], 'resources': ['objects'], 'verbs': ['get']}])
        self.assertEqual(binding['roleRef']['name'], role['metadata']['name'])
        self.assertEqual(binding['subjects'], [{'kind': 'ServiceAccount', 'name': 'kyverno-admission-controller', 'namespace': 'kyverno'}])

    def test_owner_policy_and_prerequisite_are_in_gitops(self):
        docs = yaml.safe_load((ROOT / 'policies/kyverno/cluster-policies/kustomization.yaml').read_text())
        self.assertIn('protect-grafana-import.yaml', docs['resources'])
        self.assertIn('grafana-owner-lookup-rbac.yaml', docs['resources'])
        workflow = (ROOT / '.github/workflows/platform-validation.yml').read_text()
        self.assertIn('kyverno-cli_v1.18.1_linux_x86_64.tar.gz', workflow)
        self.assertIn('5e6bba9ca85beec6c93e94ca7fb0972a66df3b2e67636a08bef090cd3fc6535c', workflow)

    def test_api_lookup_lifecycle_and_fail_closed(self):
        cli = os.environ.get('KYVERNO_CLI') or shutil.which('kyverno')
        self.assertIsNotNone(cli, 'pinned Kyverno CLI required')
        state = {'object': managed_object(), 'code': 200, 'paths': []}
        class API(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                path = self.path.split('?', 1)[0]
                state['paths'].append(path)
                code = 200
                if path == '/apis/kubernetes.crossplane.io/v1alpha2/objects/alpha-grafana-dashboard':
                    code, body = state['code'], state['object']
                    if code != 200:
                        body = {'apiVersion': 'v1', 'kind': 'Status', 'status': 'Failure', 'reason': 'NotFound' if code == 404 else 'Forbidden', 'code': code}
                elif path in ('/api', '/api/'):
                    body = {'kind': 'APIVersions', 'versions': ['v1']}
                elif path in ('/apis', '/apis/'):
                    body = {'kind': 'APIGroupList', 'groups': []}
                elif path == '/api/v1':
                    body = {'kind': 'APIResourceList', 'groupVersion': 'v1', 'resources': [
                        {'name': 'namespaces', 'kind': 'Namespace', 'namespaced': False, 'verbs': ['get', 'list']},
                        {'name': 'configmaps', 'kind': 'ConfigMap', 'namespaced': True, 'verbs': ['get', 'list']}]}
                elif path == '/api/v1/namespaces/alpha':
                    body = {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': 'alpha', 'labels': {'fixture': 'true'}}}
                elif path == '/version':
                    body = {'major': '1', 'minor': '35', 'gitVersion': 'v1.35.0'}
                elif path.endswith('/configmaps'):
                    body = {'apiVersion': 'v1', 'kind': 'ConfigMapList', 'items': [state['target']]}
                else:
                    code, body = 404, {'kind': 'Status', 'apiVersion': 'v1', 'status': 'Failure', 'reason': 'NotFound', 'code': 404}
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), API)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cases = [('initial-create', 'CREATE', managed_object(), configmap(), 200, True),
                 ('update', 'UPDATE', managed_object(), configmap(), 200, True),
                 ('finalizing-delete', 'DELETE', managed_object(), configmap(), 200, True),
                 ('foreign-uid-delete', 'DELETE', managed_object(), configmap('foreign'), 200, False),
                 ('missing-object', 'DELETE', managed_object(), configmap(), 404, False),
                 ('lookup-forbidden', 'CREATE', managed_object(), configmap(), 403, False),
                 ('lookup-unavailable', 'UPDATE', managed_object(), configmap(), 503, False),
                 ('recreated-owner', 'DELETE', managed_object('new-xr'), configmap(), 200, False)]
        for change in ('missing-owner', 'not-controller', 'wrong-kind', 'wrong-target', 'wrong-namespace', 'wrong-app-id', 'missing-desired-owner'):
            obj = managed_object()
            if change == 'missing-owner':
                obj['metadata'].pop('ownerReferences')
            elif change == 'not-controller':
                obj['metadata']['ownerReferences'][0]['controller'] = False
            elif change == 'wrong-kind':
                obj['metadata']['ownerReferences'][0]['kind'] = 'Other'
            elif change == 'wrong-target':
                obj['spec']['forProvider']['manifest']['metadata']['name'] = 'foreign'
            elif change == 'wrong-namespace':
                obj['spec']['forProvider']['manifest']['metadata']['namespace'] = 'bravo'
            elif change == 'wrong-app-id':
                obj['spec']['forProvider']['manifest']['metadata']['annotations']['platform.digiorg.io/app-id'] = 'bravo'
            else:
                obj['spec']['forProvider']['manifest']['metadata']['annotations'].pop('platform.digiorg.io/dashboard-owner')
            cases.append((change, 'DELETE', obj, configmap(), 200, False))
        try:
            with tempfile.TemporaryDirectory() as d:
                directory = Path(d)
                kube = {'apiVersion': 'v1', 'kind': 'Config', 'clusters': [{'name': 'fixture', 'cluster': {'server': f'http://127.0.0.1:{server.server_port}'}}],
                        'contexts': [{'name': 'fixture', 'context': {'cluster': 'fixture', 'user': 'fixture'}}], 'current-context': 'fixture', 'users': [{'name': 'fixture', 'user': {}}]}
                (directory / 'kubeconfig').write_text(yaml.safe_dump(kube))
                (directory / 'user.yaml').write_text(yaml.safe_dump({'apiVersion': 'cli.kyverno.io/v1alpha1', 'kind': 'UserInfo', 'userInfo': {'username': PROVIDER}}))
                for name, operation, obj, target, code, allowed in cases:
                    with self.subTest(name=name):
                        state.update(object=copy.deepcopy(obj), target=target, code=code, paths=[])
                        if name == 'finalizing-delete':
                            state['object']['metadata'].update(deletionTimestamp='2026-10-17T00:00:00Z', finalizers=['finalizer.managedresource.crossplane.io'])
                        (directory / 'resource.yaml').write_text(yaml.safe_dump(target))
                        (directory / 'values.yaml').write_text(yaml.safe_dump({'apiVersion': 'cli.kyverno.io/v1alpha1', 'kind': 'Values', 'globalValues': {'request.operation': operation, 'request.namespace': 'alpha', 'request.oldObject': target if operation != 'CREATE' else None}}))
                        result = subprocess.run([cli, 'apply', str(POLICY), '--cluster', '--kubeconfig', str(directory / 'kubeconfig'), '--userinfo', str(directory / 'user.yaml'), '-f', str(directory / 'values.yaml'), '--remove-color'], capture_output=True, text=True, timeout=40)
                        print(name, result.returncode, result.stdout, result.stderr, 'paths', state['paths'])
                        self.assertEqual(result.returncode, 0 if allowed else 1, result.stdout + result.stderr)
                        self.assertIn('/apis/kubernetes.crossplane.io/v1alpha2/objects/alpha-grafana-dashboard', state['paths'])
                        if allowed:
                            self.assertIn('pass: 2', result.stdout)
                        else:
                            self.assertNotIn('pass: 2', result.stdout)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
