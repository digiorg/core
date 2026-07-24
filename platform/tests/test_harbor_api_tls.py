#!/usr/bin/env python3
"""TLS contract for credential-bearing Harbor PostSync API jobs."""

from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
OIDC = ROOT / "platform/base/harbor/harbor-oidc-config-job.yaml"
PROXY = ROOT / "platform/base/harbor/harbor-proxy-cache-job.yaml"
SETUP = ROOT / "scripts/local-setup.nu"


class HarborPostSyncTlsTest(unittest.TestCase):
    def _doc_and_script(self, path):
        doc = yaml.safe_load(path.read_text())
        pod = doc["spec"]["template"]["spec"]
        container = pod["containers"][0]
        return pod, container, container["command"][2]

    def test_bootstrap_distributes_public_ca_to_harbor(self):
        source = SETUP.read_text()
        self.assertIn('copy_digiorg_local_ca_to_namespace "harbor"', source)

    def test_both_jobs_use_verified_ingress_tls(self):
        for path in (OIDC, PROXY):
            with self.subTest(path=path.name):
                _, _, script = self._doc_and_script(path)
                self.assertIn('HARBOR_API="https://digiorg.local/api/v2.0"', script)
                self.assertNotIn("http://harbor", script)
                self.assertNotRegex(script, r"\bcurl\b[^\n]*(?:\s-k(?:\s|$)|--insecure)")
                self.assertRegex(
                    script,
                    r'--cacert (?:/etc/digiorg-ca/ca\.crt|"\$\{CA_CERT\}")',
                )

    def test_both_jobs_wait_for_optional_public_ca_mount(self):
        for path in (OIDC, PROXY):
            with self.subTest(path=path.name):
                pod, container, script = self._doc_and_script(path)
                mount = next(m for m in container["volumeMounts"] if m["name"] == "digiorg-local-ca")
                self.assertEqual(mount["mountPath"], "/etc/digiorg-ca")
                self.assertTrue(mount["readOnly"])
                volume = next(v for v in pod["volumes"] if v["name"] == "digiorg-local-ca")
                self.assertEqual(volume["secret"]["secretName"], "digiorg-local-ca")
                self.assertTrue(volume["secret"]["optional"])
                self.assertIn("/etc/digiorg-ca/ca.crt", script)
                self.assertIn("until [ -s", script)


if __name__ == "__main__":
    unittest.main()
