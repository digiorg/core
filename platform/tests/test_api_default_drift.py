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


class ProviderHttpRequestDriftTest(unittest.TestCase):
    """Issue #285 stdout12: the crossplane-harbor-bootstrap Application's sync
    operation Succeeded and its health went Healthy, yet its only resource
    (`Request/harbor-crossplane-system-robot`) stayed permanently OutOfSync
    until `sync_gated_apps_for_local_dev`'s 15-minute per-Application budget
    expired and failed the whole run.

    Cause: `http.crossplane.io_requests.yaml` (provider-http v1.0.14's own
    packaged CRD) declares `spec.managementPolicies` with `default: ["*"]`.
    Argo CD applies this Application server-side (`ServerSideApply=true`), so
    the API server's schema default is attributed in `.metadata.managedFields`
    to Argo's own field manager -- the managed-fields diff normalizer
    therefore keeps it in the live comparison while Git never declared it, and
    the resource can never converge.

    The correction must be the API default itself, declared verbatim in Git --
    NOT an `ignoreDifferences` rule over the Request's spec, which would also
    mask genuine drift of the least-privilege permission payload."""

    REQUEST_CRD_DEFAULTS = {
        # Verified against provider-http v1.0.14's packaged CRD
        # (package/crds/http.crossplane.io_requests.yaml, v1alpha2):
        # only these four fields carry an OpenAPI `default:`.
        "spec.deletionPolicy": "Delete",
        "spec.managementPolicies": ["*"],
        "spec.providerConfigRef.policy.resolution": "Required",
        "spec.forProvider.secretInjectionConfigs.keyMappings.missingFieldStrategy": "delete",
    }

    @classmethod
    def setUpClass(cls):
        cls.request = load("crossplane/bootstrap/harbor-robot-request.yaml")
        cls.app = load("apps/platform/crossplane-harbor-bootstrap.yaml")

    def test_management_policies_api_default_is_declared_in_git(self):
        self.assertEqual(
            self.request["spec"].get("managementPolicies"),
            ["*"],
            "the CRD defaults spec.managementPolicies to ['*']; leaving it out of "
            "Git makes the Request permanently OutOfSync under ServerSideApply",
        )

    def test_every_other_defaulted_field_is_declared_or_deliberately_absent(self):
        spec = self.request["spec"]
        # Declared explicitly, and equal to a real value the API will not re-default.
        self.assertEqual(spec["deletionPolicy"], "Orphan")
        for mapping in spec["forProvider"]["secretInjectionConfigs"][0]["keyMappings"]:
            self.assertEqual(mapping["missingFieldStrategy"], "preserve")
        # providerConfigRef.policy is an object with no default of its own, so
        # the API server never materialises it (and never defaults .resolution)
        # while it is absent. Declaring it would add drift, not remove it.
        self.assertNotIn("policy", spec["providerConfigRef"])

    def test_drift_is_not_papered_over_with_ignore_differences(self):
        self.assertNotIn(
            "ignoreDifferences",
            self.app["spec"],
            "the Request's spec must be diffed in full; API defaults are fixed by "
            "declaring them, never by ignoring paths",
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
