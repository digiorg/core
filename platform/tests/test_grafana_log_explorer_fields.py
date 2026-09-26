#!/usr/bin/env python3
"""Cross-contract field checks for the DigiOrg Log Explorer (Issue #347)."""

import json
from pathlib import Path
import re
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
FLUENTD = ROOT / "platform/base/fluentd"
DASHBOARDS = FLUENTD / "grafana-dashboards.yaml"
LOG_SCHEMA = FLUENTD / "log-schema-job.yaml"
IDENTITY_FIELDS = (
    "kubernetes.namespace_name",
    "kubernetes.pod_name",
    "kubernetes.container_name",
)
QUERY_FIELD = re.compile(r"(?<![\w.])([@A-Za-z_][\w.@-]*):")


def load_yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def log_explorer():
    configmap = load_yaml(DASHBOARDS)
    return json.loads(configmap["data"]["digiorg-log-explorer.json"])


def schema_fields():
    job = load_yaml(LOG_SCHEMA)
    script = job["spec"]["template"]["spec"]["containers"][0]["args"][0]
    match = re.search(r"(?ms)^\s*TEMPLATE_PAYLOAD='(\{.*?\})'\s*$", script)
    if not match:
        raise AssertionError("production log-schema template payload was not found")
    properties = json.loads(match.group(1))["template"]["mappings"]["properties"]

    fields = {}

    def inventory(mappings, prefix=""):
        for name, mapping in mappings.items():
            field = f"{prefix}.{name}" if prefix else name
            if "type" in mapping:
                fields[field] = mapping["type"]
            if "properties" in mapping:
                inventory(mapping["properties"], field)
            for child, child_mapping in mapping.get("fields", {}).items():
                fields[f"{field}.{child}"] = child_mapping["type"]

    inventory(properties)
    return fields


def variable_field_inventory(dashboard):
    fields = {}
    queries = {}
    for variable in dashboard["templating"]["list"]:
        if variable["type"] != "query":
            continue
        for source in ("definition", "query"):
            query = json.loads(variable[source])
            if query.get("find") != "terms":
                raise AssertionError(
                    f"{variable['name']}.{source} must remain a terms query"
                )
            reference = (variable["name"], source)
            fields[reference] = query["field"]
            queries[reference] = tuple(QUERY_FIELD.findall(query.get("query", "")))
    return fields, queries


def panel_field_inventory(dashboard):
    buckets = {}
    queries = {}
    time_fields = {}
    for panel in dashboard["panels"]:
        for target in panel.get("targets", []):
            target_key = (panel["id"], target["refId"])
            queries[target_key] = tuple(QUERY_FIELD.findall(target.get("query", "")))
            time_fields[target_key] = target.get("timeField")
            for aggregation in target.get("bucketAggs", []):
                key = target_key + (aggregation["id"], aggregation["type"])
                buckets[key] = aggregation["field"]
    return buckets, queries, time_fields


class LogExplorerFieldContractTest(unittest.TestCase):
    def test_template_variable_fields_match_the_schema_contract(self):
        dashboard = log_explorer()
        self.assertEqual(
            {variable["name"]: variable["type"] for variable in dashboard["templating"]["list"]},
            {
                "logs_ds": "datasource",
                "namespace": "query",
                "pod": "query",
                "container": "query",
                "level": "custom",
            },
        )
        fields, queries = variable_field_inventory(dashboard)
        expected = {
            (name, source): field
            for name, field in zip(
                ("namespace", "pod", "container"),
                IDENTITY_FIELDS,
            )
            for source in ("definition", "query")
        }
        self.assertEqual(fields, expected)
        self.assertEqual(
            queries,
            {
                (name, source): (
                    () if name == "namespace" else ("kubernetes.namespace_name",)
                )
                for name in ("namespace", "pod", "container")
                for source in ("definition", "query")
            },
        )

        supported = schema_fields()
        for reference, field in fields.items():
            with self.subTest(reference=reference, field=field):
                self.assertEqual(supported.get(field), "keyword")
                self.assertNotIn(field, {f"{identity}.keyword" for identity in IDENTITY_FIELDS})
        for reference, query_fields in queries.items():
            for field in query_fields:
                with self.subTest(reference=reference, query_field=field):
                    self.assertIn(field, supported)

        referenced = set(fields.values())
        referenced.update(field for query_fields in queries.values() for field in query_fields)
        self.assertTrue(referenced.isdisjoint({f"{field}.keyword" for field in IDENTITY_FIELDS}))

    def test_panel_field_inventory_matches_the_schema_contract(self):
        dashboard = log_explorer()
        self.assertEqual(
            {panel["id"]: (panel["title"], panel["type"]) for panel in dashboard["panels"]},
            {
                1: ("Log Volume", "timeseries"),
                2: ("Error Rate", "timeseries"),
                3: ("Log Level Breakdown", "piechart"),
                4: ("Error Count by Namespace", "table"),
                5: ("Top Noisy Pods", "table"),
                6: ("Recent Logs", "logs"),
            },
        )
        buckets, queries, time_fields = panel_field_inventory(dashboard)
        self.assertEqual(
            buckets,
            {
                (1, "A", "2", "date_histogram"): "@timestamp",
                (2, "A", "2", "date_histogram"): "@timestamp",
                (3, "A", "1", "terms"): "level.keyword",
                (4, "A", "1", "terms"): "kubernetes.namespace_name",
                (5, "A", "1", "terms"): "kubernetes.pod_name",
            },
        )
        self.assertEqual(
            queries,
            {
                (1, "A"): ("kubernetes.namespace_name",),
                (2, "A"): ("level", "kubernetes.namespace_name"),
                (3, "A"): ("kubernetes.namespace_name",),
                (4, "A"): ("level",),
                (5, "A"): ("kubernetes.namespace_name",),
                (6, "A"): (
                    "kubernetes.namespace_name",
                    "kubernetes.pod_name",
                    "level",
                ),
            },
        )
        self.assertEqual(time_fields, {(panel_id, "A"): "@timestamp" for panel_id in range(1, 7)})

        supported = schema_fields()
        for reference, field in buckets.items():
            expected_type = "date" if reference[-1] == "date_histogram" else "keyword"
            with self.subTest(reference=reference, field=field):
                self.assertEqual(supported.get(field), expected_type)
        for reference, fields in queries.items():
            for field in fields:
                with self.subTest(reference=reference, query_field=field):
                    self.assertIn(field, supported)
        for reference, field in time_fields.items():
            with self.subTest(reference=reference, time_field=field):
                self.assertEqual(supported.get(field), "date")

        referenced = set(buckets.values())
        referenced.update(field for fields in queries.values() for field in fields)
        referenced.update(time_fields.values())
        self.assertTrue(referenced.isdisjoint({f"{field}.keyword" for field in IDENTITY_FIELDS}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
