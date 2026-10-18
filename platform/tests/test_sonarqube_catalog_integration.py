"""Issue 372: preserve onboarding through the canonical issue 375 successor."""
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]


class SonarQubeCatalogIntegrationTests(unittest.TestCase):
    def test_catalog_uses_canonical_onboarding_successor_merge(self):
        app = yaml.safe_load((ROOT / "apps/platform/core-catalog.yaml").read_text())
        self.assertEqual(app["spec"]["source"], {
            "repoURL": "https://github.com/digiorg/core-catalog.git",
            "targetRevision": "14bed077beb3da068454775869f5e5009b5fc21c",
            "path": "compositions/local",
        })


if __name__ == "__main__":
    unittest.main()
