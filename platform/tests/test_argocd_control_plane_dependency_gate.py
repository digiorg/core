#!/usr/bin/env python3
"""Behavioral coverage for the gated-sync Argo dependency boundary (#352).

The stateful fake kubectl drives the real Nushell orchestration seam.  It
models rollout, EndpointSlice, Pod identity/restart, in-Pod DNS, and Argo
operation state transitions; assertions are made on observed calls and
outcomes, never on source text.
"""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
SETUP = REPO_ROOT / "scripts" / "local-setup.nu"

FAKE_KUBECTL = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys
import time

state_path = Path(os.environ["FAKE_STATE"])
log_path = Path(os.environ["FAKE_LOG"])
scenario = os.environ["FAKE_SCENARIO"]
args = sys.argv[1:]
with log_path.open("a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\n")

try:
    state = json.loads(state_path.read_text(encoding="utf-8"))
except FileNotFoundError:
    state = {"round": 0, "patches": 0, "patch_calls": 0,
             "resource_version": 100}

def save():
    state_path.write_text(json.dumps(state), encoding="utf-8")

def has(*parts):
    rendered = " ".join(args)
    return all(part in rendered for part in parts)

def emit(value):
    print(json.dumps(value, separators=(",", ":")))

def deployment(name, namespace, replicas=1):
    return {
        "apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": {"name": name, "namespace": namespace,
                     "uid": f"{name}-uid", "generation": 7},
        "spec": {"replicas": replicas},
        "status": {"observedGeneration": 7, "readyReplicas": replicas,
                   "updatedReplicas": replicas, "availableReplicas": replicas},
    }

def pod(name, uid, namespace, restarts=0):
    if "coredns" in name:
        labels = {"k8s-app": "kube-dns"}
    else:
        labels = {"app.kubernetes.io/name": name.rsplit("-", 1)[0]}
    return {"apiVersion": "v1", "kind": "Pod",
            "metadata": {"name": name, "namespace": namespace, "uid": uid,
                         "labels": labels},
            "spec": {"containers": [{"name": "main"}]},
            "status": {"phase": "Running", "conditions": [
                {"type": "Ready", "status": "True"}
            ], "containerStatuses": [
                {"name": "main", "ready": True, "restartCount": restarts}
            ]}}

def pods_for(selector):
    round_no = state["round"]
    if "application-controller" in selector:
        pods = [pod("argocd-application-controller-0", "controller-a", "argocd")]
        if scenario == "every_controller_pod":
            pods.append(pod("argocd-application-controller-1", "controller-b", "argocd"))
        return pods
    if "repo-server" in selector:
        uid = "repo-b" if scenario == "uid_reset" and round_no >= 2 else "repo-a"
        restarts = 1 if scenario == "restart_reset" and round_no >= 2 else 0
        pods = [pod("argocd-repo-server-0", uid, "argocd", restarts)]
        if scenario == "pod_ready_condition_false":
            pods[0]["spec"] = {"readinessGates": [{"conditionType": "example.com/dependency-ready"}]}
            pods[0]["status"]["conditions"] = [
                {"type": "Ready", "status": "False"},
                {"type": "example.com/dependency-ready", "status": "False"},
            ]
        if scenario == "every_repo_pod":
            pods.append(pod("argocd-repo-server-1", "repo-second", "argocd"))
        return pods
    if "argocd-redis" in selector:
        return [pod("argocd-redis-0", "redis-a", "argocd")]
    if "kube-dns" in selector:
        return [pod("coredns-0", "coredns-a", "kube-system")]
    return []

def endpoints_ready(service, namespace):
    ready = scenario not in {"never_ready", "leaky_failure"}
    return {"apiVersion": "discovery.k8s.io/v1", "kind": "EndpointSliceList",
            "items": [{"apiVersion": "discovery.k8s.io/v1", "kind": "EndpointSlice",
        "metadata": {"name": f"{service}-slice", "namespace": namespace,
                     "uid": f"{service}-slice-uid",
                     "labels": {"kubernetes.io/service-name": service}},
        "addressType": "IPv4", "endpoints": [{
        "addresses": ["10.0.0.8"], "conditions": {"ready": ready}
    }]}]}

if not any(arg.startswith("--request-timeout=") for arg in args):
    print("password=unbounded-command-secret", file=sys.stderr)
    sys.exit(91)

if scenario == "cleanup_kill_race":
    time.sleep(0.05)
    sys.exit(0)

if scenario == "hung_kubectl" and "get" in args and "deployment" in args:
    print("hung-command-secret", file=sys.stderr, flush=True)
    time.sleep(60)
    sys.exit(96)

if scenario == "leaky_failure" and not ("get" in args and "application" in args):
    print("Authorization: Bearer must-not-leak", file=sys.stderr)
    sys.exit(1)

if "get" in args and "deployment" in args:
    if "coredns" in args:
        state["round"] += 1
        state["dns_calls_this_round"] = 0
        if scenario == "operation_race_during_gate" and state["round"] == 1:
            state["resource_version"] += 1
            state["external_operation"] = True
        save()
    replicas = 2 if scenario == "every_repo_pod" and "argocd-repo-server" in args else 1
    name = next((value for value in ("coredns", "argocd-repo-server", "argocd-redis") if value in args), "")
    namespace = args[args.index("-n") + 1]
    value = deployment(name, namespace, replicas)
    if scenario == "deployment_identity_missing" and name == "argocd-repo-server":
        value["metadata"].pop("uid")
    emit(value)
    sys.exit(0)

if "get" in args and "pods" in args:
    selector = next((a for a in args if a.startswith("app.") or a.startswith("k8s-app=")), "")
    pods = pods_for(selector)
    if (scenario == "uid_swap_during_final_probes"
            and state.get("dns_calls_this_round", 0) > 0
            and "repo-server" in selector):
        pods[0]["metadata"]["uid"] = "repo-swapped"
    emit({"apiVersion": "v1", "kind": "PodList", "items": pods})
    sys.exit(0)

typed_endpoint_paths = {
    "/apis/discovery.k8s.io/v1/namespaces/kube-system/endpointslices?labelSelector=kubernetes.io%2Fservice-name%3Dkube-dns": ("kube-dns", "kube-system"),
    "/apis/discovery.k8s.io/v1/namespaces/argocd/endpointslices?labelSelector=kubernetes.io%2Fservice-name%3Dargocd-repo-server": ("argocd-repo-server", "argocd"),
    "/apis/discovery.k8s.io/v1/namespaces/argocd/endpointslices?labelSelector=kubernetes.io%2Fservice-name%3Dargocd-redis": ("argocd-redis", "argocd"),
}
if (len(args) == 4
        and args[:3] == ["--request-timeout=10s", "get", "--raw"]
        and args[3] in typed_endpoint_paths):
    service, namespace = typed_endpoint_paths[args[3]]
    value = endpoints_ready(service, namespace)
    if scenario == "endpoint_slice_identity_missing" and service == "argocd-repo-server":
        value["items"][0].pop("metadata")
    emit(value)
    sys.exit(0)

if "exec" in args:
    pod_name = next((arg.split("/", 1)[1] for arg in args if arg.startswith("pod/")), "")
    rendered = " ".join(args)
    dns_name = next((name for name in (
        "argocd-repo-server.argocd.svc.cluster.local",
        "argocd-redis.argocd.svc.cluster.local",
    ) if name in rendered), "")
    state["dns_calls"] = state.get("dns_calls", 0) + 1
    state["dns_calls_this_round"] = state.get("dns_calls_this_round", 0) + 1
    save()
    round_no = state["round"]
    fail_controller = (
        scenario == "controller_dns_fail"
        or scenario == "dns_down_before_patch" and state.get("dns_down", False)
        or scenario == "every_controller_pod" and pod_name.endswith("-1")
        or scenario == "delayed_recovery" and round_no <= 2
    )
    fail_repo = scenario == "repo_dns_fail" or scenario == "every_repo_pod" and pod_name.endswith("-1")
    if "application-controller" in pod_name:
        if dns_name != "argocd-repo-server.argocd.svc.cluster.local":
            sys.exit(92)
        if not fail_controller:
            print("spoofed-success" if scenario == "spoofed_dns_stdout" else "__ARGO_DEPENDENCY_DNS_OK__")
        sys.exit(1 if fail_controller else 0)
    if "repo-server" in pod_name:
        if dns_name != "argocd-redis.argocd.svc.cluster.local":
            sys.exit(93)
        if not fail_repo:
            print("spoofed-success" if scenario == "spoofed_dns_stdout" else "__ARGO_DEPENDENCY_DNS_OK__")
        sys.exit(1 if fail_repo else 0)
    sys.exit(94)

if "patch" in args and "application" in args:
    payload = json.loads(args[args.index("-p") + 1])
    if payload.get("metadata", {}).get("resourceVersion") != str(state["resource_version"]):
        sys.exit(97)
    state["patch_calls"] += 1
    state["resource_version"] += 1
    if scenario not in ("patch_without_new_operation", "pending_operation_without_status"):
        state["patches"] += 1
    save()
    emit({"apiVersion": "argoproj.io/v1alpha1", "kind": "Application",
          "metadata": {"name": "test-app", "namespace": "argocd",
                       "uid": "test-app-uid",
                       "resourceVersion": str(state["resource_version"])}})
    sys.exit(0)

if "get" in args and "application" in args:
    if scenario == "malformed_application_pre" and state["patch_calls"] == 0:
        emit({"status": {"sync": {"status": "Synced"}}})
        sys.exit(0)
    if scenario == "pending_operation_without_status" and state["patch_calls"] == 0:
        emit({"apiVersion": "argoproj.io/v1alpha1", "kind": "Application",
              "metadata": {"name": "test-app", "namespace": "argocd",
                           "uid": "test-app-uid",
                           "resourceVersion": str(state["resource_version"])},
              "operation": {"sync": {"prune": True}},
              "status": {"sync": {"status": "Synced"},
                         "health": {"status": "Healthy"}}})
        sys.exit(0)
    if scenario == "dns_down_before_patch" and state["patches"] == 0:
        state["dns_down"] = True
        save()
    patches = state["patches"]
    phase = "Succeeded"
    message = ""
    if scenario == "retry_once" and patches == 1:
        phase = "Error"
        message = "repo-server lookup service on 10.96.0.10:53: server misbehaving"
    elif scenario == "deterministic" and patches >= 1:
        phase = "Error"
        message = "helm template failed: YAML parse error"
    elif scenario == "always_transient" and patches >= 1:
        phase = "Error"
        message = "rpc error: code = Unavailable desc = EOF"
    started = (("external-operation" if state.get("external_operation") else "old-operation")
               if patches == 0 else f"operation-{patches}")
    emit({"apiVersion": "argoproj.io/v1alpha1", "kind": "Application",
          "metadata": {"name": "test-app", "namespace": "argocd",
                       "uid": "test-app-uid",
                       "resourceVersion": str(state["resource_version"])},
          "status": {"operationState": {
        "phase": phase, "startedAt": started, "finishedAt": started,
        "message": message, "syncResult": {"resources": []}},
        "sync": {"status": "Synced"}, "health": {"status": "Healthy"}}})
    sys.exit(0)

print("token=unexpected-command-secret", file=sys.stderr)
sys.exit(95)
'''


class ArgoDependencyGateBehaviorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        fake = root / "kubectl"
        fake.write_text(FAKE_KUBECTL, encoding="utf-8")
        fake.chmod(0o755)
        self.log = root / "calls.jsonl"
        self.state = root / "state.json"
        self.env = {
            **os.environ,
            "PATH": f"{root}{os.pathsep}{os.environ.get('PATH', '')}",
            "FAKE_LOG": str(self.log),
            "FAKE_STATE": str(self.state),
        }

    def run_nu(self, scenario, expression, timeout=30):
        self.env["FAKE_SCENARIO"] = scenario
        return subprocess.run(
            ["nu", "--no-config-file", "-c", f"source {SETUP}; {expression}"],
            cwd=REPO_ROOT, env=self.env, capture_output=True, text=True,
            timeout=timeout,
        )

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def patch_indexes(self):
        return [i for i, call in enumerate(self.calls()) if "patch" in call]

    def gate_round_indexes(self):
        return [
            i for i, call in enumerate(self.calls())
            if "deployment" in call and "coredns" in call
        ]

    @staticmethod
    def sync_expression(max_samples=6):
        return (
            "sync_gated_application_for_local_dev test-app '{}' 3 "
            f"0sec {max_samples} 0sec 0sec"
        )

    def assert_secret_safe(self, result):
        combined = result.stdout + result.stderr
        for secret in ("must-not-leak", "unexpected-command-secret", "unbounded-command-secret"):
            self.assertNotIn(secret, combined)

    def evaluate_json_helper(self, helper, value, *args):
        encoded = json.dumps(value, separators=(",", ":"))
        suffix = "".join(f" {json.dumps(arg)}" for arg in args)
        result = self.run_nu(
            "ready", f"{helper} {json.dumps(encoded)}{suffix} | to json --raw"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    @staticmethod
    def valid_endpoint_slices(service="service-a", namespace="trusted"):
        return {"apiVersion": "discovery.k8s.io/v1", "kind": "EndpointSliceList",
                "items": [{"apiVersion": "discovery.k8s.io/v1", "kind": "EndpointSlice",
            "metadata": {"name": "service-a-slice", "namespace": namespace,
                         "uid": "slice-uid",
                         "labels": {"kubernetes.io/service-name": service}},
            "addressType": "IPv4", "endpoints": [{
            "addresses": ["10.0.0.8"], "conditions": {"ready": True}
        }]}]}

    @staticmethod
    def valid_deployment(name="workload", namespace="trusted"):
        return {
            "apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": name, "namespace": namespace,
                         "uid": "deployment-uid", "generation": 7},
            "spec": {"replicas": 1},
            "status": {"observedGeneration": 7, "readyReplicas": 1,
                       "updatedReplicas": 1, "availableReplicas": 1},
        }

    @staticmethod
    def valid_pod_list():
        return {"apiVersion": "v1", "kind": "PodList", "items": [{
            "apiVersion": "v1", "kind": "Pod",
            "metadata": {"name": "pod-a", "namespace": "trusted", "uid": "uid-a",
                         "labels": {"app": "expected"}},
            "spec": {"containers": [{"name": "main"}]},
            "status": {"phase": "Running", "conditions": [
                {"type": "Ready", "status": "True"}
            ], "containerStatuses": [
                {"name": "main", "ready": True, "restartCount": 0}
            ]},
        }]}

    def test_endpoint_slice_parser_rejects_every_malformed_shape(self):
        valid = self.valid_endpoint_slices()
        self.assertTrue(self.evaluate_json_helper(
            "dependency_endpoints_ready", valid, "service-a", "trusted"
        ))
        base_slice = valid["items"][0]
        malformed = [
            {key: value for key, value in valid.items() if key != "apiVersion"},
            {**valid, "apiVersion": "v1"},
            {**valid, "kind": "List"},
            {**valid, "items": [*valid["items"], "malformed-sibling"]},
            {**valid, "items": [{key: value for key, value in base_slice.items() if key != "apiVersion"}]},
            {**valid, "items": [{**base_slice, "kind": "Endpoints"}]},
            {**valid, "items": [{**base_slice, "metadata": {**base_slice["metadata"], "name": ""}}]},
            {**valid, "items": [{**base_slice, "metadata": {**base_slice["metadata"], "uid": ""}}]},
            {**valid, "items": [{**base_slice, "metadata": {**base_slice["metadata"], "deletionTimestamp": "2026-01-01T00:00:00Z"}}]},
            {**valid, "items": [{**base_slice, "addressType": "FQDN"}]},
            {**valid, "items": [{**base_slice, "metadata": {**base_slice["metadata"], "namespace": "spoofed"}}]},
            {**valid, "items": [{**base_slice, "metadata": {**base_slice["metadata"], "labels": {"kubernetes.io/service-name": "other"}}}]},
            {**valid, "items": [{**base_slice, "endpoints": [base_slice["endpoints"][0], None]}]},
            {**valid, "items": [{**base_slice, "endpoints": [{"addresses": [None], "conditions": {"ready": True}}]}]},
            {**valid, "items": [{**base_slice, "endpoints": [{"addresses": [""], "conditions": {"ready": True}}]}]},
            {**valid, "items": [{**base_slice, "endpoints": [{"addresses": ["10.0.0.8", 4], "conditions": {"ready": True}}]}]},
            {**valid, "items": [{**base_slice, "endpoints": [{"addresses": ["repo.example"], "conditions": {"ready": True}}]}]},
            {**valid, "items": [{**base_slice, "endpoints": [{"addresses": ["10.0.0.8"], "conditions": "ready"}]}]},
            {**valid, "items": [{**base_slice, "endpoints": [{"addresses": ["10.0.0.8"], "conditions": {"ready": 1}}]}]},
        ]
        for candidate in malformed:
            with self.subTest(candidate=candidate):
                self.assertFalse(self.evaluate_json_helper(
                    "dependency_endpoints_ready", candidate, "service-a", "trusted"
                ))

    def test_endpoint_slice_parser_rejects_oversized_collections(self):
        valid = self.valid_endpoint_slices()
        base_slice = valid["items"][0]
        base_endpoint = base_slice["endpoints"][0]
        oversized = [
            {**valid, "items": [
                {**base_slice, "metadata": {**base_slice["metadata"],
                    "name": f"service-a-{index}", "uid": f"slice-{index}"}}
                for index in range(33)
            ]},
            {**valid, "items": [{**base_slice, "endpoints": [base_endpoint] * 101}]},
            {**valid, "items": [{**base_slice, "endpoints": [{
                **base_endpoint, "addresses": ["10.0.0.8"] * 17
            }]}]},
        ]
        for candidate in oversized:
            with self.subTest(size=len(json.dumps(candidate))):
                self.assertFalse(self.evaluate_json_helper(
                    "dependency_endpoints_ready", candidate, "service-a", "trusted"
                ))

    def test_endpoint_slice_without_type_address_or_identity_is_rejected(self):
        self.assertFalse(self.evaluate_json_helper(
            "dependency_endpoints_ready",
            {"kind": "EndpointSliceList", "items": [{"endpoints": [{
                "addresses": ["10.0.0.8"], "conditions": {"ready": True}
            }]}]},
            "service-a", "trusted",
        ))

    def test_deployment_parser_requires_exact_integer_rollout_shape(self):
        valid = self.valid_deployment()
        self.assertTrue(self.evaluate_json_helper(
            "dependency_deployment_ready", valid, "workload", "trusted"
        ))
        malformed = [
            {key: value for key, value in valid.items() if key != "apiVersion"},
            {**valid, "apiVersion": "v1"},
            {**valid, "kind": "StatefulSet"},
            {**valid, "metadata": {**valid["metadata"], "name": "other"}},
            {**valid, "metadata": {**valid["metadata"], "namespace": "spoofed"}},
            {**valid, "metadata": {**valid["metadata"], "uid": ""}},
            {**valid, "metadata": {**valid["metadata"], "deletionTimestamp": "2026-01-01T00:00:00Z"}},
            {**valid, "spec": {"replicas": 1.5}},
            {**valid, "spec": {"replicas": 0}},
            {**valid, "metadata": {"generation": -1}},
            {**valid, "status": {**valid["status"], "observedGeneration": -1}},
            {**valid, "status": {**valid["status"], "readyReplicas": -1}},
            {**valid, "status": {**valid["status"], "updatedReplicas": 1.0}},
            {**valid, "status": {**valid["status"], "availableReplicas": True}},
        ]
        for candidate in malformed:
            with self.subTest(candidate=candidate):
                self.assertFalse(self.evaluate_json_helper(
                    "dependency_deployment_ready", candidate, "workload", "trusted"
                ))

    def test_deployment_without_api_version_or_identity_is_rejected(self):
        self.assertFalse(self.evaluate_json_helper(
            "dependency_deployment_ready",
            {"kind": "Deployment", "metadata": {"generation": 7},
             "spec": {"replicas": 1},
             "status": {"observedGeneration": 7, "readyReplicas": 1,
                        "updatedReplicas": 1, "availableReplicas": 1}},
            "workload", "trusted",
        ))

    def test_malformed_deployment_or_endpoint_slice_never_patch(self):
        for scenario in ("deployment_identity_missing", "endpoint_slice_identity_missing"):
            with self.subTest(scenario=scenario):
                self.state.unlink(missing_ok=True)
                self.log.unlink(missing_ok=True)
                result = self.run_nu(scenario, self.sync_expression(3))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.patch_indexes(), [])

    def test_pod_parser_rejects_weak_shapes_and_duplicate_identities(self):
        valid = self.valid_pod_list()
        snapshot = self.evaluate_json_helper(
            "dependency_pod_snapshot", valid, "trusted", "app", "expected"
        )
        self.assertTrue(snapshot["valid"])
        base_pod = valid["items"][0]
        base_status = base_pod["status"]
        base_container = base_status["containerStatuses"][0]
        malformed = [
            {key: value for key, value in valid.items() if key != "apiVersion"},
            {**valid, "kind": "List"},
            {**valid, "items": [base_pod, "malformed-sibling"]},
            {**valid, "items": [{key: value for key, value in base_pod.items() if key != "apiVersion"}]},
            {**valid, "items": [{**base_pod, "kind": "Deployment"}]},
            {**valid, "items": [{**base_pod, "metadata": {"name": "", "uid": "uid-a"}}]},
            {**valid, "items": [{**base_pod, "metadata": {"name": "pod-a", "uid": None}}]},
            {**valid, "items": [{**base_pod, "metadata": {**base_pod["metadata"], "namespace": "spoofed"}}]},
            {**valid, "items": [{**base_pod, "metadata": {**base_pod["metadata"], "labels": {"app": "other"}}}]},
            {**valid, "items": [{**base_pod, "metadata": {**base_pod["metadata"], "deletionTimestamp": "2026-01-01T00:00:00Z"}}]},
            {**valid, "items": [{key: value for key, value in base_pod.items() if key != "spec"}]},
            {**valid, "items": [{**base_pod, "spec": {"containers": [{"name": "other"}]}}]},
            {**valid, "items": [{**base_pod, "status": {**base_status, "phase": "Pending"}}]},
            {**valid, "items": [{**base_pod, "status": {**base_status, "conditions": []}}]},
            {**valid, "items": [{**base_pod, "status": {**base_status, "conditions": [{"type": "Ready", "status": "True"}, "malformed"]}}]},
            {**valid, "items": [{**base_pod, "status": {**base_status, "conditions": [{"type": "Ready", "status": "False"}]}}]},
            {**valid, "items": [{**base_pod, "status": {**base_status, "conditions": [{"type": "Ready", "status": "True"}, {"type": "Ready", "status": "True"}]}}]},
            {**valid, "items": [{**base_pod, "spec": {"readinessGates": [{"conditionType": "example.com/ready"}]}, "status": {**base_status, "conditions": [{"type": "Ready", "status": "True"}, {"type": "example.com/ready", "status": "False"}]}}]},
            {**valid, "items": [{**base_pod, "spec": {"readinessGates": None}}]},
            {**valid, "items": [{**base_pod, "spec": {"readinessGates": ["malformed"]}}]},
            {**valid, "items": [{**base_pod, "status": {**base_status, "containerStatuses": {"main": base_container}}}]},
            {**valid, "items": [{**base_pod, "status": {**base_status, "containerStatuses": [{**base_container, "name": ""}]}}]},
            {**valid, "items": [{**base_pod, "status": {**base_status, "containerStatuses": [base_container, base_container]}}]},
            {**valid, "items": [{**base_pod, "status": {**base_status, "containerStatuses": [{**base_container, "ready": 1}]}}]},
            {**valid, "items": [{**base_pod, "status": {**base_status, "containerStatuses": [{**base_container, "restartCount": -1}]}}]},
            {**valid, "items": [base_pod, {**base_pod, "metadata": {"name": "pod-a", "uid": "uid-b"}}]},
            {**valid, "items": [base_pod, {**base_pod, "metadata": {"name": "pod-b", "uid": "uid-a"}}]},
        ]
        for candidate in malformed:
            with self.subTest(candidate=candidate):
                snapshot = self.evaluate_json_helper(
                    "dependency_pod_snapshot", candidate, "trusted", "app", "expected"
                )
                self.assertFalse(snapshot["valid"])

    def test_pod_ready_condition_and_readiness_gate_failure_never_patch(self):
        result = self.run_nu("pod_ready_condition_false", self.sync_expression(3))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.patch_indexes(), [])

    def test_ready_but_controller_dns_failing_never_patches(self):
        result = self.run_nu("controller_dns_fail", self.sync_expression(4))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.patch_indexes(), [])
        self.assertIn("controllerDNS=false", result.stdout + result.stderr)
        self.assert_secret_safe(result)

    def test_ready_but_repo_server_dns_failing_never_patches(self):
        result = self.run_nu("repo_dns_fail", self.sync_expression(4))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.patch_indexes(), [])
        self.assertIn("repoServerDNS=false", result.stdout + result.stderr)

    def test_arbitrary_successful_dns_stdout_cannot_spoof_the_probe(self):
        result = self.run_nu("spoofed_dns_stdout", self.sync_expression(3))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.patch_indexes(), [])
        self.assertNotIn("spoofed-success", result.stdout + result.stderr)

    def test_uid_swap_during_final_dns_probes_fails_without_patch(self):
        result = self.run_nu("uid_swap_during_final_probes", self.sync_expression(3))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.patch_indexes(), [])
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(state.get("patches", 0), 0)

    def test_dns_failure_after_accepted_sample_cannot_reach_patch(self):
        result = self.run_nu("dns_down_before_patch", self.sync_expression(3))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.patch_indexes(), [])
        calls = self.calls()
        application_reads = [
            i for i, call in enumerate(calls)
            if "get" in call and "application" in call
        ]
        dns_execs = [i for i, call in enumerate(calls) if "exec" in call]
        self.assertTrue(application_reads)
        self.assertTrue(dns_execs)
        self.assertLess(application_reads[0], dns_execs[0])

    def test_malformed_application_prestate_never_patches(self):
        result = self.run_nu("malformed_application_pre", self.sync_expression(3))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.patch_indexes(), [])

    def test_successful_patch_without_new_operation_is_never_accepted(self):
        result = self.run_nu("patch_without_new_operation", self.sync_expression(3))
        self.assertNotEqual(result.returncode, 0)
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(state.get("patch_calls"), 1)
        self.assertEqual(state.get("patches"), 0)

    def test_pending_operation_without_status_is_rejected_before_gate_or_patch(self):
        result = self.run_nu("pending_operation_without_status", self.sync_expression(3))
        self.assertNotEqual(result.returncode, 0)
        state = (json.loads(self.state.read_text(encoding="utf-8"))
                 if self.state.exists() else {})
        self.assertEqual(state.get("round", 0), 0)
        self.assertEqual(state.get("patch_calls", 0), 0)

    def test_operation_started_during_gate_invalidates_guarded_patch(self):
        result = self.run_nu("operation_race_during_gate", self.sync_expression(3))
        self.assertNotEqual(result.returncode, 0)
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(state.get("patch_calls", 0), 0)
        self.assertEqual(state.get("patches", 0), 0)

    def test_every_current_dns_client_pod_must_resolve(self):
        for scenario, pod_name in (
            ("every_controller_pod", "pod/argocd-application-controller-1"),
            ("every_repo_pod", "pod/argocd-repo-server-1"),
        ):
            with self.subTest(scenario=scenario):
                self.state.unlink(missing_ok=True)
                self.log.unlink(missing_ok=True)
                result = self.run_nu(scenario, self.sync_expression(4))
                self.assertNotEqual(result.returncode, 0)
                execs = [call for call in self.calls() if "exec" in call]
                self.assertTrue(any(pod_name in call for call in execs))
                self.assertEqual(self.patch_indexes(), [])

    def test_delayed_dns_recovery_requires_three_consecutive_stable_samples(self):
        result = self.run_nu("delayed_recovery", self.sync_expression(7))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.gate_round_indexes()), 5)
        self.assertEqual(len(self.patch_indexes()), 1)
        self.assertLess(self.gate_round_indexes()[-1], self.patch_indexes()[0])

    def test_uid_and_restart_changes_reset_stability(self):
        for scenario in ("uid_reset", "restart_reset"):
            with self.subTest(scenario=scenario):
                self.state.unlink(missing_ok=True)
                self.log.unlink(missing_ok=True)
                result = self.run_nu(scenario, self.sync_expression(6))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(self.gate_round_indexes()), 4)

    def test_timeout_fails_closed_without_patch_and_redacts_command_output(self):
        result = self.run_nu("leaky_failure", self.sync_expression(4))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.gate_round_indexes()), 4)
        self.assertEqual(self.patch_indexes(), [])
        self.assertIn("did not become dependency-stable", result.stdout + result.stderr)
        self.assert_secret_safe(result)

    def test_retry_regates_before_reissuing_a_fresh_operation(self):
        result = self.run_nu("retry_once", self.sync_expression(6))
        self.assertEqual(result.returncode, 0, result.stderr)
        patches = self.patch_indexes()
        rounds = self.gate_round_indexes()
        self.assertEqual(len(patches), 2)
        self.assertEqual(len(rounds), 6)
        self.assertLess(rounds[2], patches[0])
        self.assertLess(patches[0], rounds[3])
        self.assertLess(rounds[5], patches[1])

    def test_every_initial_and_retry_patch_has_no_dependency_gate_bypass(self):
        result = self.run_nu("retry_once", self.sync_expression(6))
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        patches = self.patch_indexes()
        self.assertEqual(len(patches), 2)
        previous_patch = -1
        for patch_index in patches:
            gate_rounds = [
                i for i in range(previous_patch + 1, patch_index)
                if "deployment" in calls[i] and "coredns" in calls[i]
            ]
            self.assertEqual(len(gate_rounds), 3)
            prior_identity_call = calls[gate_rounds[0] - 1]
            self.assertIn("get", prior_identity_call)
            self.assertIn("application", prior_identity_call)
            dns_execs = [
                i for i in range(gate_rounds[0], patch_index)
                if "exec" in calls[i]
            ]
            self.assertTrue(dns_execs)
            final_closure = calls[dns_execs[-1] + 1:patch_index]
            self.assertEqual(len(final_closure), 4)
            self.assertTrue(all("get" in call and "pods" in call
                                for call in final_closure))
            previous_patch = patch_index

    def test_deterministic_failure_does_not_retry(self):
        result = self.run_nu("deterministic", self.sync_expression(6))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.patch_indexes()), 1)
        self.assertEqual(len(self.gate_round_indexes()), 3)

    def test_transient_failures_stop_after_exactly_four_operations(self):
        result = self.run_nu("always_transient", self.sync_expression(6))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.patch_indexes()), 4)
        self.assertEqual(len(self.gate_round_indexes()), 12)

    def test_all_gate_subprocesses_are_bounded(self):
        result = self.run_nu(
            "ready", "wait_for_argocd_control_plane_dependencies 0sec 3 3"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.calls())
        self.assertTrue(all(any(arg.startswith("--request-timeout=") for arg in call)
                            for call in self.calls()))
        exec_calls = [call for call in self.calls() if "exec" in call]
        self.assertTrue(exec_calls)
        self.assertTrue(all("timeout" in call and "getent" in " ".join(call)
                            for call in exec_calls))

    def test_gate_uses_exact_typed_endpoint_slice_raw_paths(self):
        expected_paths = {
            "/apis/discovery.k8s.io/v1/namespaces/kube-system/endpointslices?labelSelector=kubernetes.io%2Fservice-name%3Dkube-dns",
            "/apis/discovery.k8s.io/v1/namespaces/argocd/endpointslices?labelSelector=kubernetes.io%2Fservice-name%3Dargocd-repo-server",
            "/apis/discovery.k8s.io/v1/namespaces/argocd/endpointslices?labelSelector=kubernetes.io%2Fservice-name%3Dargocd-redis",
        }
        result = self.run_nu(
            "ready", "wait_for_argocd_control_plane_dependencies 0sec 3 3"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        raw_calls = [call for call in calls if "--raw" in call]
        self.assertEqual(len(raw_calls), 9)
        self.assertEqual({call[-1] for call in raw_calls}, expected_paths)
        self.assertTrue(all(call == ["--request-timeout=10s", "get", "--raw", call[-1]]
                            for call in raw_calls))
        self.assertFalse(any("endpointslices.discovery.k8s.io" in call
                             for call in calls))

    def test_hung_kubectl_is_killed_at_the_total_gate_deadline(self):
        started = time.monotonic()
        result = self.run_nu(
            "hung_kubectl",
            "wait_for_argocd_control_plane_dependencies 0sec 3 3 700ms",
            timeout=5,
        )
        elapsed = time.monotonic() - started
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(elapsed, 2.5)
        self.assertEqual(self.patch_indexes(), [])
        self.assertNotIn("hung-command-secret", result.stdout + result.stderr)

    def test_completed_job_kill_race_returns_bounded_timeout_record(self):
        source = SETUP.read_text(encoding="utf-8")
        start = source.index("def bounded_dependency_kubectl ")
        end = source.index("\n# Issue #352:", start)
        bounded_function = source[start:end]
        self.assertIn("job kill $job_id", bounded_function)
        bounded_function = bounded_function.replace(
            "job kill $job_id", "sleep 100ms\n        job kill $job_id", 1
        )
        harness = Path(self.tmp.name) / "bounded-kill-race.nu"
        harness.write_text(
            bounded_function
            + "\nbounded_dependency_kubectl ((date now) + 10ms) -- "
              "--request-timeout=10s version | to json --raw\n",
            encoding="utf-8",
        )
        self.env["FAKE_SCENARIO"] = "cleanup_kill_race"
        result = subprocess.run(
            ["nu", "--no-config-file", str(harness)], cwd=REPO_ROOT,
            env=self.env, capture_output=True, text=True, timeout=3,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "stdout": "", "stderr": "", "exit_code": 124,
        })

    def test_live_job_kill_failure_is_not_swallowed(self):
        source = SETUP.read_text(encoding="utf-8")
        start = source.index("def bounded_dependency_kubectl ")
        end = source.index("\n# Issue #352:", start)
        bounded_function = source[start:end]
        self.assertIn("job kill $job_id", bounded_function)
        bounded_function = bounded_function.replace(
            "job kill $job_id", 'error make {msg: "forced kill failure"}', 1
        )
        harness = Path(self.tmp.name) / "bounded-live-kill-failure.nu"
        harness.write_text(
            bounded_function
            + "\nbounded_dependency_kubectl ((date now) + 100ms) -- "
              "--request-timeout=10s get deployment coredns -n kube-system -o json "
              "| to json --raw\n",
            encoding="utf-8",
        )
        self.env["FAKE_SCENARIO"] = "hung_kubectl"
        result = subprocess.run(
            ["nu", "--no-config-file", str(harness)], cwd=REPO_ROOT,
            env=self.env, capture_output=True, text=True, timeout=3,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Dependency command cleanup failed", result.stderr)
        self.assertNotIn("hung-command-secret", result.stdout + result.stderr)

    def test_gated_application_order_is_unchanged(self):
        result = self.run_nu("ready", "gated_apps_for_local_dev | to json --raw")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [
            "external-secrets", "nats", "grafana", "opencost", "gitea",
            "sonarqube", "crossplane", "crossplane-providers",
            "crossplane-provider-configs", "crossplane-harbor-bootstrap",
            "crossplane-xrds", "core-catalog",
        ])


if __name__ == "__main__":
    unittest.main(verbosity=2)
