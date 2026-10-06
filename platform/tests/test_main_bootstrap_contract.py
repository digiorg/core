"""Disposable prototype: one main graph, no retained-release prerequisite.

Existing behavior is locked, not reimplemented. Documentation RED/GREEN proves
that the supported entrypoint no longer implies an immutable Core closure.
Runtime and authenticated Grafana acceptance remain separately authorized.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
CORE = "https://github.com/digiorg/core.git"
ROOT_APP = ROOT / "platform/base/argocd/applications/root-app.yaml"


class MainBootstrapContract(unittest.TestCase):
    def test_supported_docs_select_main_without_a_release_preparer(self):
        scripts = (ROOT / "scripts/README.md").read_text()
        apps = (ROOT / "apps/README.md").read_text()
        self.assertIn("## Disposable pre-1.0 main workflow", scripts)
        self.assertIn("No runtime tag, release closure, or dashboard preparer is required", scripts)
        self.assertIn("targetRevision: main", apps)
        self.assertNotIn("targetRevision: <immutable-revision>", apps)

    def test_entire_canonical_graph_tracks_main_for_core_only(self):
        root = yaml.safe_load(ROOT_APP.read_text())
        self.assertEqual(root["spec"]["source"]["path"], "apps")
        self.assertTrue(root["spec"]["source"]["directory"]["recurse"])
        children = sorted((ROOT / "apps").rglob("*.yaml"))
        self.assertEqual(len(children), 31)
        core_sources = 0
        for path in [ROOT_APP, *children]:
            app = yaml.safe_load(path.read_text())
            self.assertEqual(app["kind"], "Application", str(path))
            spec = app["spec"]
            self.assertFalse("source" in spec and "sources" in spec)
            for source in spec.get("sources", [spec.get("source")]):
                if source["repoURL"] == CORE:
                    core_sources += 1
                    self.assertEqual(source["targetRevision"], "main", str(path))
        self.assertEqual(core_sources, 32)
        fluentd = yaml.safe_load((ROOT / "apps/platform/fluentd.yaml").read_text())
        self.assertEqual(fluentd["spec"]["source"]["path"], "platform/base/fluentd")
        self.assertNotIn("sources", fluentd["spec"])
        # External supply-chain sources are still verified by check_pins.py;
        # they are not converted to main by this prototype contract.

    @unittest.skipUnless(shutil.which("kustomize"), "requires Kustomize")
    def test_standalone_fluentd_keeps_one_dashboard_and_schema_safety_hook(self):
        docs = list(yaml.safe_load_all(subprocess.check_output(
            ["kustomize", "build", "platform/base/fluentd"], cwd=ROOT, text=True)))
        dashboards = [d for d in docs if d and d["kind"] == "ConfigMap"
                      and d["metadata"]["name"] == "fluentd-grafana-dashboards"]
        self.assertEqual(len(dashboards), 1)
        separate = list(yaml.safe_load_all(subprocess.check_output(
            ["kustomize", "build", "platform/base/log-dashboards"], cwd=ROOT, text=True)))
        self.assertEqual(dashboards, separate)
        hooks = [d for d in docs if d and d["kind"] == "Job"
                 and d["metadata"]["name"] == "fluentd-log-schema"]
        self.assertEqual(len(hooks), 1)
        self.assertEqual(hooks[0]["metadata"]["annotations"]["argocd.argoproj.io/hook"], "PreSync")

    @unittest.skipUnless(shutil.which("nu"), "requires Nushell")
    def test_real_root_entrypoint_reuses_canonical_manifest_on_resume_offline(self):
        text = (ROOT / "scripts/local-setup.nu").read_text()
        start = text.index("def deploy_root_app ")
        end = text.index("\ndef ", start + 5)
        body = text[start:end]
        # Exercise the real Nushell entrypoint; all runtime dependencies are
        # explicitly replaced. No cluster, Docker, credentials or network.
        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory)
            fake = scratch / "kubectl"
            fake.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\n')
            fake.chmod(0o755)
            harness = scratch / "root.nu"
            harness.write_text(
                'let KUBECONFIG_PATH = "offline-unused"\n'
                'def wait_for_repo_server_stable [] {}\n'
                'def wait_for_configuration_dependencies [label: string, apps: list, secrets: list] {}\n'
                'def patch_argocd_oidc_ca [] {}\n'
                'def deploy_core_data_layer [] {}\n'
                'def sync_gated_apps_for_local_dev [] {}\n'
                'def redact_sync_diagnostic [message: string] { $message }\n'
                + body + '\ndeploy_root_app\ndeploy_root_app\n')
            env = dict(os.environ, PATH=str(scratch) + os.pathsep + os.environ["PATH"],
                       CALL_LOG=str(scratch / "calls.log"))
            result = subprocess.run(["nu", "--no-config-file", str(harness)],
                                    cwd=ROOT, env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = (scratch / "calls.log").read_text().splitlines()
            self.assertEqual(calls, [
                "--kubeconfig offline-unused --request-timeout=30s apply -f apps/platform/cert-manager.yaml",
                "apply -f platform/base/argocd/applications/root-app.yaml",
            ] * 2)


if __name__ == "__main__":
    unittest.main()
