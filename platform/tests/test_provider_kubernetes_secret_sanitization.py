#!/usr/bin/env python3
"""Provider-kubernetes must never mirror Secret data into Object status."""

from pathlib import Path
import unittest
import yaml

MANIFEST = Path(__file__).resolve().parents[2] / "crossplane/providers/packages/provider-kubernetes.yaml"


class ProviderKubernetesSecretSanitizationTest(unittest.TestCase):
    def test_runtime_enables_secret_status_sanitization(self):
        docs = list(yaml.safe_load_all(MANIFEST.read_text()))
        runtime = next(doc for doc in docs if doc and doc.get("kind") == "DeploymentRuntimeConfig")
        containers = runtime["spec"]["deploymentTemplate"]["spec"]["template"]["spec"]["containers"]
        package_runtime = next(container for container in containers if container.get("name") == "package-runtime")
        env = {item["name"]: str(item.get("value", "")) for item in package_runtime.get("env", [])}
        self.assertEqual(
            env.get("SANITIZE_SECRETS", "").lower(),
            "true",
            "provider-kubernetes v1.2.1 defaults sanitization off and otherwise copies Secret.data into Object status.atProvider.manifest",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
