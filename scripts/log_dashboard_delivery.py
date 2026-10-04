#!/usr/bin/env python3
"""Prepare an offline Fluentd Application with independently pinned dashboards.

No Git, network, Kubernetes or Argo CD client is invoked. This is source
preparation, NOT a release-closure generator or a deployment executable.
"""
import argparse
from copy import deepcopy
from pathlib import Path
import re
import sys

import yaml

REPOSITORY = 'https://github.com/digiorg/core.git'
SHA = re.compile(r'[0-9a-f]{40}')
DASHBOARD_PATCH = {
    'target': {'version': 'v1', 'kind': 'ConfigMap', 'name': 'fluentd-grafana-dashboards'},
    'patch': '$patch: delete\napiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: fluentd-grafana-dashboards\n',
}


def prepare(application, dashboard_commit):
    """Pure, narrow transformation; preserve writer pin and Application owner.

Only a reviewed single-source Core Fluentd input is supported. Existing
Kustomize overrides and already-multi-source inputs fail closed instead of
silently merging patches or overriding source precedence.
"""
    if not isinstance(dashboard_commit, str) or not SHA.fullmatch(dashboard_commit):
        raise ValueError('dashboard commit must be an exact lowercase 40-hex commit')
    if not isinstance(application, dict):
        raise ValueError('expected an Application mapping')
    metadata = application.get('metadata', {})
    spec = application.get('spec', {})
    if not isinstance(metadata, dict) or not isinstance(spec, dict):
        raise ValueError('expected Application metadata and spec mappings')
    if (application.get('apiVersion') != 'argoproj.io/v1alpha1'
            or application.get('kind') != 'Application'
            or metadata.get('name') != 'fluentd'
            or metadata.get('namespace') != 'argocd'
            or not isinstance(spec, dict)
            or 'sources' in spec):
        raise ValueError('expected single-source argocd/fluentd Application')
    source = spec.get('source')
    if not isinstance(source, dict) or set(source) != {'repoURL', 'targetRevision', 'path'}:
        raise ValueError('unexpected writer source fields; reconcile explicitly')
    if (source['repoURL'] != REPOSITORY or source['path'] != 'platform/base/fluentd'
            or not isinstance(source['targetRevision'], str)
            or not SHA.fullmatch(source['targetRevision'])):
        raise ValueError('writer source must use the exact Core path and immutable commit')
    if spec.get('destination') != {
            'server': 'https://kubernetes.default.svc', 'namespace': 'logging'}:
        raise ValueError('unexpected writer destination')
    result = deepcopy(application)
    writer = result['spec'].pop('source')
    writer['kustomize'] = {'patches': [deepcopy(DASHBOARD_PATCH)]}
    result['spec']['sources'] = [writer, {
        'repoURL': REPOSITORY,
        'targetRevision': dashboard_commit,
        'path': 'platform/base/log-dashboards',
    }]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--application', type=Path, required=True,
                        help='local immutable-pinned Fluentd Application snapshot')
    parser.add_argument('--dashboard-commit', required=True)
    parser.add_argument('--output', type=Path, required=True,
                        help='new local file; existing files are never overwritten')
    args = parser.parse_args(argv)
    try:
        application = yaml.safe_load(args.application.read_text(encoding='utf-8'))
        result = prepare(application, args.dashboard_commit)
        with args.output.open('x', encoding='utf-8') as output:
            output.write(yaml.safe_dump(result, sort_keys=False))
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(f'preparation refused: {error}', file=sys.stderr)
        return 1
    print('Prepared local Application only; publication and deployment are not authorized.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
