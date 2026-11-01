"""Rendered discovery contract for the dedicated OpenSearch exporter (#380)."""
import copy
import pathlib
import subprocess
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]


def rendered(path):
    result = subprocess.run(["kustomize", "build", str(ROOT / path)],
                            capture_output=True, text=True, check=True)
    return [obj for obj in yaml.safe_load_all(result.stdout) if obj]


def peer(namespace, labels):
    return {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": namespace}},
            "podSelector": {"matchLabels": labels}}


EXPECTED_POLICY = {
    "podSelector": {"matchLabels": {"app.kubernetes.io/name": "opensearch-metrics-exporter"}},
    "policyTypes": ["Ingress", "Egress"],
    "ingress": [{"from": [peer("monitoring", {"operator.prometheus.io/name": "prometheus-kube-prometheus-prometheus"})],
                 "ports": [{"protocol": "TCP", "port": 9114}]}],
    "egress": [
        {"to": [peer("platform-db", {"app.kubernetes.io/name": "opensearch", "app.kubernetes.io/instance": "opensearch", "app.kubernetes.io/component": "opensearch-cluster-master"})],
         "ports": [{"protocol": "TCP", "port": 9200}]},
        {"to": [peer("kube-system", {"k8s-app": "kube-dns"})],
         "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}]},
    ],
}


def policy_is_exact(spec):
    return spec == EXPECTED_POLICY


class NetworkPolicyContract(unittest.TestCase):
    def test_rendered_policy_is_exact_and_selects_exporter(self):
        objects = rendered("platform/base/opensearch")
        policies = [o for o in objects if o["kind"] == "NetworkPolicy"]
        self.assertEqual(len(policies), 1)
        self.assertEqual(policies[0]["metadata"]["namespace"], "platform-db")
        self.assertTrue(policy_is_exact(policies[0]["spec"]))
        deployment = next(o for o in objects if o["kind"] == "Deployment" and o["metadata"]["name"] == "opensearch-metrics-exporter")
        labels = deployment["spec"]["template"]["metadata"]["labels"]
        self.assertTrue(all(labels.get(k) == v for k, v in policies[0]["spec"]["podSelector"]["matchLabels"].items()))

    def test_rejects_unsafe_policy_mutations(self):
        original = next(o["spec"] for o in rendered("platform/base/opensearch")
                        if o["kind"] == "NetworkPolicy")
        self.assertTrue(policy_is_exact(original))
        mutations = []
        p = copy.deepcopy(original); p["policyTypes"] = ["Ingress"]; mutations.append(p)
        p = copy.deepcopy(original); p["podSelector"]["matchLabels"]["app.kubernetes.io/name"] = "wrong"; mutations.append(p)
        p = copy.deepcopy(original); p["ingress"][0]["from"] = [{}]; mutations.append(p)
        p = copy.deepcopy(original); p["ingress"][0]["from"][0]["podSelector"]["matchLabels"] = {"wrong": "label"}; mutations.append(p)
        p = copy.deepcopy(original); p["ingress"][0]["from"] = [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "monitoring"}}}, {"podSelector": {"matchLabels": {"operator.prometheus.io/name": "prometheus-kube-prometheus-prometheus"}}}]; mutations.append(p)
        p = copy.deepcopy(original); p["egress"].append({}); mutations.append(p)
        p = copy.deepcopy(original); p["egress"][0]["ports"].append({"protocol": "TCP", "port": 443}); mutations.append(p)
        p = copy.deepcopy(original); p["egress"][0]["to"][0]["podSelector"]["matchLabels"] = {}; mutations.append(p)
        p = copy.deepcopy(original); p["egress"][1]["to"] = [{"ipBlock": {"cidr": "0.0.0.0/0"}}]; mutations.append(p)
        for i, mutation in enumerate(mutations):
            with self.subTest(mutation=i):
                self.assertFalse(policy_is_exact(mutation))


class DiscoveryContract(unittest.TestCase):
    def test_shared_component_label_is_not_unique(self):
        services = [
            {"app.kubernetes.io/name": "opensearch", "app.kubernetes.io/component": "opensearch-cluster-master"},
            {"app.kubernetes.io/name": "opensearch", "app.kubernetes.io/component": "opensearch-cluster-master"},
        ]
        selector = {"app.kubernetes.io/name": "opensearch", "app.kubernetes.io/component": "opensearch-cluster-master"}
        selected = [s for s in services if all(s.get(k) == v for k, v in selector.items())]
        self.assertEqual(len(selected), 2, "shared component label must not be mistaken for unique discovery")

    def test_single_exporter_collects_all_nodes(self):
        objects = rendered("platform/base/opensearch")
        deployments = [o for o in objects if o["kind"] == "Deployment" and o["metadata"]["name"] == "opensearch-metrics-exporter"]
        self.assertEqual(len(deployments), 1)
        spec = deployments[0]["spec"]
        self.assertEqual(spec["replicas"], 1)
        self.assertEqual(spec["strategy"]["type"], "Recreate")
        pod = spec["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        container = pod["containers"][0]
        self.assertIn("--es.all", container["args"])
        self.assertIn("--es.uri=http://opensearch-cluster-master.platform-db.svc.cluster.local:9200", container["args"])
        self.assertEqual(container["image"], "quay.io/prometheuscommunity/elasticsearch-exporter:v1.11.0@sha256:a056739b095df4baaa076f0b31321394233da9a240eeada2623d1b028b7ee7a6")

    def test_exporter_service_resolves_only_exporter_pods(self):
        objects = rendered("platform/base/opensearch")
        service = next(o for o in objects if o["kind"] == "Service" and o["metadata"]["name"] == "opensearch-metrics-exporter")
        deployment = next(o for o in objects if o["kind"] == "Deployment" and o["metadata"]["name"] == "opensearch-metrics-exporter")
        labels = deployment["spec"]["template"]["metadata"]["labels"]
        self.assertTrue(all(labels.get(k) == v for k, v in service["spec"]["selector"].items()))
        port = service["spec"]["ports"][0]
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(port["name"], "metrics")
        self.assertIn(port["targetPort"], [p["name"] for p in container["ports"]])
        self.assertEqual(container["readinessProbe"]["httpGet"]["path"], "/healthz")
        self.assertEqual(container["securityContext"]["capabilities"]["drop"], ["ALL"])
        self.assertFalse(container["securityContext"]["allowPrivilegeEscalation"])

    def test_only_exporter_service_is_selected(self):
        objects = rendered("platform/base/monitoring-extras")
        monitor = next(o for o in objects if o["kind"] == "ServiceMonitor"
                       and o["metadata"]["name"] == "opensearch")
        selector = monitor["spec"]["selector"]["matchLabels"]
        services = [
            {"name": "ordinary", "labels": {"app.kubernetes.io/name": "opensearch", "app.kubernetes.io/component": "opensearch-cluster-master"}},
            {"name": "headless", "labels": {"app.kubernetes.io/name": "opensearch", "app.kubernetes.io/component": "opensearch-cluster-master"}},
        ]
        services += [{"name": o["metadata"]["name"], "labels": o["metadata"].get("labels", {})}
                     for o in rendered("platform/base/opensearch") if o["kind"] == "Service"]
        selected = [s["name"] for s in services if all(s["labels"].get(k) == v for k, v in selector.items())]
        self.assertEqual(selected, ["opensearch-metrics-exporter"])
        self.assertEqual(monitor["spec"]["endpoints"], [{"port": "metrics", "path": "/metrics", "interval": "30s"}])
        self.assertEqual(monitor["spec"]["namespaceSelector"], {"matchNames": ["platform-db"]})
        self.assertEqual(monitor["metadata"]["labels"]["release"], "prometheus")


if __name__ == "__main__":
    unittest.main()
