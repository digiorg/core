#!/usr/bin/env python3
"""Regression contract for Issue #303 Grafana plugin lifecycle."""

from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
VALUES = ROOT / "platform" / "base" / "grafana" / "values.yaml"
APP = ROOT / "apps" / "platform" / "grafana.yaml"


class GrafanaPluginLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.values = yaml.safe_load(VALUES.read_text(encoding="utf-8"))
        self.application = yaml.safe_load(APP.read_text(encoding="utf-8"))

    def test_bundled_elasticsearch_plugin_is_not_background_updated(self):
        chart = self.application["spec"]["sources"][0]
        self.assertEqual(chart["chart"], "kube-prometheus-stack")
        self.assertEqual(chart["targetRevision"], "87.17.0")

        grafana = self.values["grafana"]
        plugins = grafana["grafana.ini"].get("plugins", {})
        self.assertIs(
            plugins.get("preinstall_auto_update"),
            False,
            "Grafana must not stop and replace its compatible bundled Elasticsearch plugin",
        )
        self.assertNotIn(
            "shadowBundledPlugins",
            grafana,
            "the repair must not hide unrelated bundled plugins",
        )
        self.assertEqual(
            {source["type"] for source in grafana["additionalDataSources"]},
            {"jaeger", "elasticsearch"},
            "the lifecycle repair must not migrate provisioned datasource types",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
