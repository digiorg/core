#!/usr/bin/env python3
"""Harbor chart 1.19 ServiceMonitor compatibility contract."""
import os
import unittest
import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def load(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class HarborServiceMonitorTest(unittest.TestCase):
    def test_harbor_does_not_emit_duplicate_release_labels(self):
        values = load("platform/base/harbor/values.yaml")
        labels = values["metrics"]["serviceMonitor"].get("additionalLabels", {})
        self.assertNotIn(
            "release", labels,
            "chart 1.19 already emits release: harbor; an override creates invalid duplicate YAML",
        )

    def test_prometheus_selects_both_platform_and_harbor_release_labels(self):
        values = load("platform/base/grafana/values.yaml")
        selector = values["prometheus"]["prometheusSpec"]["serviceMonitorSelector"]
        expressions = selector.get("matchExpressions", [])
        release = next((x for x in expressions if x.get("key") == "release"), None)
        if release is None:
            self.fail("serviceMonitorSelector must include a release expression")
        self.assertEqual(release.get("operator"), "In")
        self.assertGreaterEqual(set(release.get("values", [])), {"prometheus", "harbor"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
