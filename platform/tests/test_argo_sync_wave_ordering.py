#!/usr/bin/env python3
"""Argo CD cross-Application sync-wave ordering (Issue #283).

The App-of-Apps root Application (platform/base/argocd/applications/root-app.yaml)
recurses over every manifest under apps/platform/*.yaml. The child Application
sync-wave annotations remain useful ordering metadata, but they are NOT treated
as a cross-Application workload-readiness guarantee: Argo CD does not assess
Application CR health by default. Runtime safety comes from applying and proving
the core data layer before root-app exists; CNPG safety comes from keeping both
CNPG Applications manual and promoting them explicitly. This module locks the
corresponding metadata contracts:

  1. OpenSearch is core data-layer infrastructure exactly like legacy
     PostgreSQL (it is Jaeger's/Fluentd's trace+log backend) and must sync in
     the SAME early wave, not after its own consumers. The previous wave-3
     placement was justified by "ServiceMonitor CRD must exist first", but the
     Prometheus Operator CRDs are installed in Phase 1 (before the root
     Application exists at all) and OpenSearch's own Kustomize base carries no
     ServiceMonitor (that lives in platform/base/monitoring-extras, wave 5) —
     so that justification never actually applied to the opensearch
     Application's own sync.
  2. CNPG (the operator and the Cluster) is optional, future application
     database infrastructure that nothing on the core platform's dependency
     path consumes. Its late-wave metadata documents placement after all core
     Applications, while manual sync policies plus the explicit command provide
     the actual guarantee that CNPG cannot start or block core bootstrap.

Pure python3 + PyYAML, no cluster access::

    python3 platform/tests/test_argo_sync_wave_ordering.py
"""

import os
import sys
import unittest

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard
    sys.stderr.write("PyYAML is required: pip install pyyaml\n")
    raise

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
APPS_DIR = os.path.join(REPO_ROOT, "apps", "platform")
MONITORING_EXTRAS_KUSTOMIZATION = os.path.join(
    REPO_ROOT, "platform", "base", "monitoring-extras", "kustomization.yaml"
)
OPENSEARCH_KUSTOMIZATION = os.path.join(
    REPO_ROOT, "platform", "base", "opensearch", "kustomization.yaml"
)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _app(name):
    path = os.path.join(APPS_DIR, f"{name}.yaml")
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    assert doc["kind"] == "Application"
    return doc


def _wave(name):
    app = _app(name)
    return int(app["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"])


class CoreDataLayerWaveTest(unittest.TestCase):
    """PostgreSQL and OpenSearch are both core data-layer services (Issue #283)."""

    def test_opensearch_shares_postgresql_wave(self):
        self.assertEqual(
            _wave("opensearch"), _wave("postgresql"),
            "OpenSearch is core data-layer infrastructure exactly like legacy "
            "PostgreSQL and must sync in the same early wave",
        )

    def test_opensearch_precedes_its_consumers(self):
        for consumer in ("jaeger", "fluentd"):
            self.assertLess(
                _wave("opensearch"), _wave(consumer),
                f"OpenSearch must sync before its consumer {consumer}",
            )

    def test_postgresql_precedes_its_consumers(self):
        for consumer in ("keycloak", "backstage", "gitea", "sonarqube", "harbor"):
            self.assertLess(
                _wave("postgresql"), _wave(consumer),
                f"PostgreSQL must sync before its consumer {consumer}",
            )

    def test_opensearch_wave_not_justified_by_servicemonitor_in_its_own_base(self):
        # The opensearch Application's own Kustomize base must not define a
        # ServiceMonitor — that CRD-ordering concern (if it ever applied) lives
        # entirely in monitoring-extras (wave 5), not here.
        k = yaml.safe_load(_read(OPENSEARCH_KUSTOMIZATION))
        for resource in k.get("resources", []):
            self.assertNotIn("servicemonitor", resource.lower())

    def test_opensearch_servicemonitor_lives_in_monitoring_extras(self):
        k = yaml.safe_load(_read(MONITORING_EXTRAS_KUSTOMIZATION))
        self.assertTrue(
            any("opensearch" in r.lower() for r in k.get("resources", [])),
            "the OpenSearch ServiceMonitor is expected in monitoring-extras",
        )


class CnpgDecoupledWaveTest(unittest.TestCase):
    """CNPG is optional future-app infrastructure and must never gate core waves."""

    CORE_APPS = (
        "namespaces", "cert-manager", "external-secrets", "nats", "postgresql",
        "opensearch", "keycloak", "argocd", "backstage", "gitea", "grafana",
        "harbor", "jaeger", "landingpage", "opencost", "sonarqube",
        "crossplane", "kyverno", "crossplane-providers", "fluentd",
        "kyverno-policies", "monitoring-extras", "crossplane-provider-configs",
        "crossplane-xrds", "core-catalog",
    )

    def test_cnpg_operator_syncs_after_every_core_application(self):
        cnpg_wave = _wave("cnpg")
        for app in self.CORE_APPS:
            self.assertGreater(
                cnpg_wave, _wave(app),
                f"cnpg operator wave must be strictly after core Application {app}",
            )

    def test_cnpg_cluster_syncs_after_the_operator(self):
        self.assertGreater(_wave("cnpg-cluster"), _wave("cnpg"))

    def test_cnpg_cluster_syncs_after_every_core_application(self):
        cluster_wave = _wave("cnpg-cluster")
        for app in self.CORE_APPS:
            self.assertGreater(cluster_wave, _wave(app))


class CnpgManifestsDoNotReferenceMigrationFraming(unittest.TestCase):
    """Issue #283: CNPG Application manifests must not describe cutover/migration."""

    def test_cnpg_app_comments_do_not_claim_migration(self):
        for name in ("cnpg", "cnpg-cluster"):
            text = _read(os.path.join(APPS_DIR, f"{name}.yaml")).lower()
            self.assertNotIn("migration", text)
            self.assertNotIn("cutover", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
