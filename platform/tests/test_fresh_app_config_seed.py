#!/usr/bin/env python3
"""Fresh-bootstrap contract for the approved Issue #301 AppClaim.

The app-config repository lives inside the disposable KinD cluster. A clean
reset therefore starts from an empty repository and must seed the already
approved local-development Claim through Git before Argo CD can reproduce the
Claim -> XR -> Composition -> CI/CD -> workload chain.
"""

import json
import os
import subprocess
import tempfile
import unittest

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SETUP = os.path.join(REPO_ROOT, "scripts", "local-setup.nu")
SEED = os.path.join(
    REPO_ROOT,
    "bootstrap",
    "app-config",
    "claims",
    "digiorg-core-dev",
    "app-claims",
    "AppClaim",
    "myapp.yaml",
)
TARGET = "claims/digiorg-core-dev/app-claims/AppClaim/myapp.yaml"


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _func_body(text, name):
    start = text.index(f"def {name} ")
    end = text.index("\ndef ", start + 10)
    return text[start:end]


class ApprovedSeedManifestTest(unittest.TestCase):
    def test_seed_manifest_is_the_exact_approved_local_claim_contract(self):
        self.assertTrue(os.path.isfile(SEED), "approved AppClaim seed is missing")
        doc = yaml.safe_load(_read(SEED))
        self.assertEqual(doc["apiVersion"], "platform.digiorg.io/v1alpha1")
        self.assertEqual(doc["kind"], "AppClaim")
        self.assertEqual(
            doc["metadata"],
            {
                "name": "myapp",
                "namespace": "app-claims",
                "annotations": {
                    "terasky.backstage.io/source-info": '{"pushToGit":true,"gitBranch":"main","gitRepo":"digiorg.local?owner=DigiOrg&repo=app-config","gitLayout":"cluster-scoped","basePath":"digiorg-core-dev/app-claims/AppClaim"}',
                    "terasky.backstage.io/add-to-catalog": "true",
                    "terasky.backstage.io/owner": "group:default/digiorgadmin",
                    "terasky.backstage.io/system": "app-claims",
                    "terasky.backstage.io/source-file-url": "https://digiorg.local/DigiOrg/app-config/blob/main/digiorg-core-dev/app-claims/AppClaim/myapp.yaml",
                },
            },
        )
        self.assertEqual(
            doc["spec"],
            {
                "appName": "myapp",
                "compositeDeletePolicy": "Background",
                "compositionUpdatePolicy": "Automatic",
                "database": {"enabled": False},
                "gitea": {"cicd": True, "enabled": True, "visibility": "public"},
                "messaging": {"enabled": False},
                "services": [
                    {
                        "build": {"context": ".", "enabled": True},
                        "image": "myappapi",
                        "name": "myappapi",
                        "port": 9950,
                        "replicas": 1,
                    }
                ],
                "size": "S",
                "team": "platform-team",
                "writeConnectionSecretToRef": {"name": "myappsecret"},
            },
        )


class FreshSeedWiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.body = _func_body(_read(SETUP), "configure_app_config_repo")

    def test_seed_is_committed_to_the_exact_argo_watched_target(self):
        self.assertIn(TARGET, self.body)
        self.assertIn("bootstrap/app-config/claims", self.body)
        self.assertIn("encode base64", self.body)

    def test_existing_approved_or_user_modified_claim_is_never_overwritten(self):
        self.assertIn('if $app_claim_seed_status == "200"', self.body)
        self.assertIn('else if $app_claim_seed_status == "404"', self.body)
        claim_section = self.body[self.body.index(TARGET) :]
        self.assertEqual(claim_section.count("-X POST"), 1)
        self.assertNotIn("-X PUT", claim_section)
        self.assertNotIn("-X PATCH", claim_section)

    def test_unexpected_status_and_transport_errors_fail_closed(self):
        self.assertIn("Failed to query the approved app-config AppClaim seed", self.body)
        self.assertIn("Unexpected HTTP status while checking the approved AppClaim seed", self.body)
        self.assertIn("Failed to seed the approved app-config AppClaim", self.body)


class FreshSeedBehaviourTest(unittest.TestCase):
    def _run(self, status):
        with tempfile.TemporaryDirectory() as tmp:
            fake = os.path.join(tmp, "kubectl")
            log = os.path.join(tmp, "calls.jsonl")
            with open(fake, "w", encoding="utf-8") as fh:
                fh.write(
                    "#!/usr/bin/env python3\n"
                    "import json,os,sys\n"
                    "args=sys.argv[1:]; script=' '.join(args); stdin=sys.stdin.read()\n"
                    "with open(os.environ['CALL_LOG'],'a') as f: f.write(json.dumps({'args':args,'stdinBytes':len(stdin)})+'\\n')\n"
                    "if '/contents/claims/.gitkeep' in script: print('200',end='')\n"
                    "elif '/contents/claims/digiorg-core-dev/app-claims/AppClaim/myapp.yaml' in script:\n"
                    "  print('201' if '-X POST' in script else os.environ['CLAIM_STATUS'],end='')\n"
                    "elif '/repos/DigiOrg/app-config' in script: print('200',end='')\n"
                    "else: sys.exit(3)\n"
                )
            os.chmod(fake, 0o755)
            env = os.environ.copy()
            env.update({"PATH": tmp + os.pathsep + env["PATH"], "CALL_LOG": log, "CLAIM_STATUS": status})
            result = subprocess.run(
                ["nu", "--no-config-file", "-c", f"source {SETUP}; configure_app_config_repo fake-pod sentinel-token"],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=20,
            )
            calls = [json.loads(line) for line in _read(log).splitlines()] if os.path.exists(log) else []
            return result, calls

    def test_missing_claim_is_created_once_through_gitea(self):
        result, calls = self._run("404")
        self.assertEqual(result.returncode, 0, result.stderr)
        scripts = [" ".join(call["args"]) for call in calls]
        claim_calls = [s for s in scripts if TARGET in s]
        self.assertEqual(len(claim_calls), 2)
        self.assertEqual(sum("-X POST" in s for s in claim_calls), 1)

    def test_existing_claim_is_preserved_without_write(self):
        result, calls = self._run("200")
        self.assertEqual(result.returncode, 0, result.stderr)
        scripts = [" ".join(call["args"]) for call in calls]
        claim_calls = [s for s in scripts if TARGET in s]
        self.assertEqual(len(claim_calls), 1)
        self.assertFalse(any("-X POST" in s or "-X PUT" in s for s in claim_calls))

    def test_unexpected_claim_status_fails_closed_without_write(self):
        result, calls = self._run("500")
        self.assertNotEqual(result.returncode, 0)
        scripts = [" ".join(call["args"]) for call in calls]
        claim_calls = [s for s in scripts if TARGET in s]
        self.assertFalse(any("-X POST" in s or "-X PUT" in s for s in claim_calls))


if __name__ == "__main__":
    unittest.main()
