"""Exact publisher, old/new metadata and legacy compatibility through Kyverno CLI."""
import copy
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "policies/kyverno/cluster-policies/protect-grafana-import.yaml"
PROVIDER = "system:serviceaccount:crossplane-system:crossplane-provider-kubernetes"
ARGO = "system:serviceaccount:argocd:argocd-application-controller"


class DashboardAdmissionTests(unittest.TestCase):
    def test_reserved_import_exact_publisher_boundary(self):
        self.assertTrue(POLICY.exists(), "reserved import protection missing")
        cli = os.environ.get("KYVERNO_CLI") or shutil.which("kyverno")
        self.assertIsNotNone(cli, "Kyverno 1.18.1 CLI required for real policy tests")
        app = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {
            "name": "grafana-app-dev-alpha", "namespace": "alpha", "labels": {"grafana_dashboard": "1"},
            "annotations": {"grafana_folder": "/tmp/dashboards/DigiOrg Apps", "platform.digiorg.io/dashboard-owner": "xr-uid", "platform.digiorg.io/app-id": "alpha"}}, "data": {"app-dev-alpha.json": "{}"}}
        ordinary = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "ordinary", "namespace": "alpha"}, "data": {"config": "x"}}
        legacy = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "legacy-logs", "namespace": "logging", "labels": {"grafana_dashboard": "1"}}, "data": {"logs.json": "{}"}}
        cases = [("provider", PROVIDER, app, None, "CREATE", True),
                 ("app-sa", "system:serviceaccount:alpha:alpha-sa", app, None, "CREATE", False),
                 ("operator-not-publisher", "system:serviceaccount:monitoring:prometheus-operator", app, None, "CREATE", False),
                 ("admin-no-bypass", "cluster-admin", app, None, "CREATE", False),
                 ("ordinary", "someone", ordinary, None, "CREATE", True),
                 ("legacy-create", ARGO, legacy, None, "CREATE", True),
                 ("legacy-update", ARGO, legacy, legacy, "UPDATE", True),
                 ("provider-update", PROVIDER, app, app, "UPDATE", True),
                 ("provider-delete", PROVIDER, app, app, "DELETE", True),
                 ("legacy-delete", ARGO, legacy, legacy, "DELETE", True),
                 ("hostile-delete", "someone", app, app, "DELETE", False)]
        stripped = copy.deepcopy(app)
        stripped["metadata"].pop("labels")
        stripped["metadata"].pop("annotations")
        cases.append(("strip-protection", PROVIDER, stripped, app, "UPDATE", False))
        changed = copy.deepcopy(app)
        changed["metadata"]["annotations"]["platform.digiorg.io/dashboard-owner"] = "foreign"
        cases.append(("owner-change", PROVIDER, changed, app, "UPDATE", False))
        changed = copy.deepcopy(app)
        changed["metadata"]["annotations"]["grafana_folder"] = "/tmp/dashboards/../../etc"
        cases.append(("folder-traversal", PROVIDER, changed, None, "CREATE", False))
        foreign = copy.deepcopy(app)
        foreign["metadata"].pop("labels")
        foreign["metadata"].pop("annotations")
        # CLI AddVariable merges oldObject into a copy of newObject. Explicit inert
        # reserved keys are needed to represent an unreserved old ConfigMap faithfully.
        # Production CEL still checks key presence; this is not an admission bypass.
        foreign["metadata"]["labels"] = {"grafana_dashboard": "0"}
        foreign["metadata"]["annotations"] = {"grafana_folder": "", "platform.digiorg.io/dashboard-owner": "", "platform.digiorg.io/app-id": ""}
        cases.append(("foreign-adoption", PROVIDER, app, foreign, "UPDATE", False))
        # Pinned provider Delete is name-based without UID/owner preconditions.
        # An unlabelled replacement at its target must not be silently deleted.
        cases.append(("foreign-reserved-name-delete", PROVIDER, foreign, foreign, "DELETE", False))
        cases.append(("foreign-reserved-name-create", "someone", foreign, None, "CREATE", False))
        gitops = copy.deepcopy(app)
        gitops["metadata"]["annotations"]["platform.digiorg.io/dashboard-owner"] = "gitops:alpha"
        cases.extend([
            ("gitops-app-create", ARGO, gitops, None, "CREATE", True),
            ("gitops-app-delete", ARGO, gitops, gitops, "DELETE", True),
            ("provider-cannot-delete-gitops", PROVIDER, gitops, gitops, "DELETE", False),
            ("gitops-cannot-delete-provider", ARGO, app, app, "DELETE", False),
            ("namespace-controller-no-exception", "system:serviceaccount:kube-system:namespace-controller", app, app, "DELETE", False),
        ])
        foreign_owned = copy.deepcopy(app)
        # This UID is NOT the controller UID of alpha-grafana-dashboard.
        # A nonempty owner string is not actual controller ownership proof.
        foreign_owned["metadata"]["annotations"]["platform.digiorg.io/dashboard-owner"] = "foreign-xr-uid"
        cases.append(("foreign-uid-delete", PROVIDER, foreign_owned, foreign_owned, "DELETE", False))
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            for name, actor, new, old, operation, allowed in cases:
                with self.subTest(name=name):
                    (directory / "resource.yaml").write_text(yaml.safe_dump(new))
                    (directory / "user.yaml").write_text(yaml.safe_dump({"apiVersion": "cli.kyverno.io/v1alpha1", "kind": "UserInfo", "userInfo": {"username": actor, "groups": ["system:masters"] if name == "admin-no-bypass" else []}}))
                    (directory / "values.yaml").write_text(yaml.safe_dump({"apiVersion": "cli.kyverno.io/v1alpha1", "kind": "Values", "globalValues": {"request.operation": operation, "request.oldObject": old, "request.namespace": new["metadata"]["namespace"]}, "policies": [{"name": "protect-grafana-import", "rules": [{"name": "provider-controller-owner", "values": {"dashboardObject": {"apiVersion": "kubernetes.crossplane.io/v1alpha2", "kind": "Object", "metadata": {"name": "alpha-grafana-dashboard", "ownerReferences": [{"apiVersion": "platform.digiorg.io/v1alpha1", "kind": "Application", "name": "alpha-xr", "uid": "xr-uid", "controller": True}]}, "spec": {"forProvider": {"manifest": app}}}}}]}]}))
                    result = subprocess.run([cli, "apply", str(POLICY), "--resource", str(directory / "resource.yaml"),
                                             "--userinfo", str(directory / "user.yaml"), "--values-file", str(directory / "values.yaml"), "--remove-color"],
                                            capture_output=True, text=True, timeout=30)
                    print(name, result.returncode, result.stdout, result.stderr)
                    self.assertEqual(result.returncode, 0 if allowed else 1, result.stdout + result.stderr)
                    self.assertNotIn("error: 1", result.stdout, result.stdout + result.stderr)
                    self.assertIn("pass: " if allowed else "fail: ", result.stdout)


if __name__ == "__main__":
    unittest.main()
