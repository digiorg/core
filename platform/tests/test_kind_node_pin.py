#!/usr/bin/env python3
"""The disposable KinD acceptance environment must be reproducible (Issue #279)."""
import os
import re
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SETUP = os.path.join(REPO_ROOT, "scripts", "local-setup.nu")


class KindNodePinTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(SETUP, encoding="utf-8") as fh:
            cls.text = fh.read()

    def test_kubernetes_1361_node_image_is_digest_pinned(self):
        match = re.search(
            r'let KIND_NODE_IMAGE = "kindest/node:v1\.36\.1@sha256:([0-9a-f]{64})"',
            self.text,
        )
        self.assertIsNotNone(match)
        self.assertIn("kind create cluster --image $KIND_NODE_IMAGE", self.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
