import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
INGRESS = ROOT / "platform/base/ingress/digiorg-ingress.yaml"


def documents():
    return [d for d in yaml.safe_load_all(INGRESS.read_text()) if d]


class GiteaSmartHttpCompatibilityIngressTest(unittest.TestCase):
    def setUp(self):
        self.docs = documents()
        matches = [
            d
            for d in self.docs
            if d.get("kind") == "Ingress"
            and d.get("metadata", {}).get("name") == "gitea-smart-http-ingress"
        ]
        self.assertEqual(len(matches), 1)
        self.ingress = matches[0]

    def test_routes_only_git_smart_http_endpoints_without_rewrite(self):
        annotations = self.ingress["metadata"].get("annotations", {})
        self.assertEqual(annotations.get("nginx.ingress.kubernetes.io/use-regex"), "true")
        self.assertNotIn("nginx.ingress.kubernetes.io/rewrite-target", annotations)

        rules = self.ingress["spec"]["rules"]
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["host"], "digiorg.local")
        paths = rules[0]["http"]["paths"]
        self.assertEqual(len(paths), 1)
        route = paths[0]
        self.assertEqual(route["pathType"], "ImplementationSpecific")
        self.assertEqual(
            route["path"],
            "/([^/]+)/([^/]+)/(info/refs|git-upload-pack|git-receive-pack)$",
        )
        pattern = re.compile(route["path"])

        for path in (
            "/DigiOrg/myapp/info/refs",
            "/DigiOrg/myapp/git-upload-pack",
            "/DigiOrg/myapp/git-receive-pack",
            "/other/repository/info/refs",
        ):
            self.assertIsNotNone(pattern.fullmatch(path), path)

        for path in (
            "/",
            "/gitea/DigiOrg/myapp",
            "/DigiOrg/myapp",
            "/DigiOrg/myapp/issues",
            "/api/v1/repos/DigiOrg/myapp",
            "/three/segments/extra/info/refs",
        ):
            self.assertIsNone(pattern.fullmatch(path), path)

        service = route["backend"]["service"]
        self.assertEqual(service["name"], "gitea-http")
        self.assertEqual(service["port"]["number"], 3000)

    def test_uses_existing_tls_identity(self):
        self.assertEqual(
            self.ingress["spec"]["tls"],
            [{"hosts": ["digiorg.local"], "secretName": "digiorg-local-tls"}],
        )

    def test_landing_page_catch_all_remains_owned_by_platform_ingress(self):
        platform = next(
            d
            for d in self.docs
            if d.get("kind") == "Ingress"
            and d.get("metadata", {}).get("name") == "digiorg-platform"
        )
        root = [
            p
            for p in platform["spec"]["rules"][0]["http"]["paths"]
            if p["path"] == "/"
        ]
        self.assertEqual(len(root), 1)
        self.assertEqual(root[0]["backend"]["service"]["name"], "landingpage")


if __name__ == "__main__":
    unittest.main()
