"""Issue 372: consume the canonical reviewed SonarQube Catalog producer."""
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]


class SonarQubeCatalogIntegrationTests(unittest.TestCase):
    def test_catalog_uses_canonical_onboarding_merge(self):
        app = yaml.safe_load((ROOT / "apps/platform/core-catalog.yaml").read_text())
        self.assertEqual(app["spec"]["source"], {
            "repoURL": "https://github.com/digiorg/core-catalog.git",
            "targetRevision": "2867d1b4d47f472c7bb4097d61226e206e32a5a1",
            "path": "compositions/local",
        })


if __name__ == "__main__":
    unittest.main()
