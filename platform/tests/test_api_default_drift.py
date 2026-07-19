#!/usr/bin/env python3
"""Regression contracts for API-defaulted resources found by clean bootstrap #279."""
import os
import unittest

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def load(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class StatefulSetDriftTest(unittest.TestCase):
    def test_postgresql_declares_pvc_api_defaults_and_ignores_only_status(self):
        docs = []
        with open(os.path.join(ROOT, "platform/base/postgresql/statefulset.yaml"), encoding="utf-8") as fh:
            docs = [doc for doc in yaml.safe_load_all(fh) if doc]
        sts = next(doc for doc in docs if doc.get("kind") == "StatefulSet")
        pvc = sts["spec"]["volumeClaimTemplates"][0]
        self.assertEqual(pvc["apiVersion"], "v1")
        self.assertEqual(pvc["kind"], "PersistentVolumeClaim")
        self.assertEqual(pvc["spec"]["volumeMode"], "Filesystem")
        rules = load("apps/platform/postgresql.yaml")["spec"]["ignoreDifferences"]
        self.assertEqual(rules[0]["jqPathExpressions"], [".spec.volumeClaimTemplates[]?.status"])

    def test_opensearch_ignores_only_chart_omitted_pvc_defaults(self):
        rules = load("apps/platform/opensearch.yaml")["spec"]["ignoreDifferences"]
        self.assertEqual(rules[0]["name"], "opensearch-cluster-master")
        self.assertEqual(
            set(rules[0]["jqPathExpressions"]),
            {
                ".spec.volumeClaimTemplates[]?.apiVersion",
                ".spec.volumeClaimTemplates[]?.kind",
                ".spec.volumeClaimTemplates[]?.spec.volumeMode",
                ".spec.volumeClaimTemplates[]?.status",
            },
        )


    def test_grafana_ignores_only_generated_admin_password(self):
        app = load("apps/platform/grafana.yaml")
        self.assertIn("RespectIgnoreDifferences=true", app["spec"]["syncPolicy"]["syncOptions"])
        rules = app["spec"]["ignoreDifferences"]
        self.assertEqual(
            {(rule["kind"], rule["name"]): rule["jsonPointers"] for rule in rules},
            {
                ("Secret", "prometheus-grafana"): ["/data/admin-password"],
                ("Deployment", "prometheus-grafana"): [
                    "/spec/template/metadata/annotations/checksum~1secret"
                ],
            },
        )


class CertificateOwnershipTest(unittest.TestCase):
    def test_upstream_cert_manager_namespace_is_removed(self):
        kustomization = load("platform/base/cert-manager/kustomization.yaml")
        patches = kustomization.get("patches", [])
        self.assertTrue(
            any(
                patch.get("target", {}).get("kind") == "Namespace"
                and patch.get("target", {}).get("name") == "cert-manager"
                and "$patch: delete" in patch.get("patch", "")
                for patch in patches
            )
        )

    def test_ingresses_do_not_compete_with_declared_certificate(self):
        ingress_dir = os.path.join(ROOT, "platform", "base", "ingress")
        for name in os.listdir(ingress_dir):
            if not name.endswith(".yaml"):
                continue
            with open(os.path.join(ingress_dir, name), encoding="utf-8") as fh:
                for doc in yaml.safe_load_all(fh):
                    if not doc or doc.get("kind") != "Ingress":
                        continue
                    annotations = doc.get("metadata", {}).get("annotations", {})
                    self.assertNotIn("cert-manager.io/cluster-issuer", annotations, name)
                    self.assertNotIn("cert-manager.io/issuer", annotations, name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
