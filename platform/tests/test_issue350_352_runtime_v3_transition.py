#!/usr/bin/env python3
"""Behavioral contracts for the offline-testable Issues #350/#352 transition."""

from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import stat
import tempfile
import types
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "issue350_352_runtime_v3_transition.py"
RUNBOOK = ROOT / "specs" / "350-opensearch-memory-headroom" / "runtime-v3-transition.md"
SPEC = spec_from_file_location("issue350_transition", SCRIPT)
assert SPEC and SPEC.loader
transition = module_from_spec(SPEC)
SPEC.loader.exec_module(transition)

OLD_TAG = "issue301-runtime-v16-20260817T130820Z"
OLD_COMMIT = "8e6b8908f99ebf76db47c15613eff523644c23f6"
NEW_TAG = "issue350-352-runtime-v3-20260904T195619Z"
NEW_COMMIT = "0123456789abcdef0123456789abcdef01234567"
PRODUCT_BASE_COMMIT = "95b89acfdcca32e348745206c2cce4867f51a6b8"
CORE = "https://github.com/digiorg/core.git"
EXPECTED_APPS = (
    "app-config", "argocd", "backstage", "cert-manager", "cnpg", "cnpg-cluster",
    "core-catalog", "crossplane", "crossplane-harbor-bootstrap",
    "crossplane-provider-configs", "crossplane-providers", "crossplane-xrds",
    "external-secrets", "fluentd", "gitea", "gitea-actions-runner", "grafana",
    "harbor", "jaeger", "keycloak", "kyverno", "kyverno-policies", "landingpage",
    "monitoring-extras", "namespaces", "nats", "nats-jetstream-controller",
    "opencost", "opensearch", "postgresql", "root-app", "sonarqube",
)


def source(target, path, repo=CORE, **extra):
    return {"repoURL": repo, "path": path, "targetRevision": target, **extra}


def operation(started, revisions, phase="Succeeded", result_revisions=None):
    value = {"phase": phase, "startedAt": started, "finishedAt": started}
    sync = {"revisions": revisions} if len(revisions) > 1 else {"revision": revisions[0]}
    value["operation"] = {"sync": sync}
    result_revisions = revisions if result_revisions is None else result_revisions
    value["syncResult"] = (
        {"revisions": result_revisions} if len(result_revisions) > 1
        else {"revision": result_revisions[0]}
    )
    return value


def canonical_ism_policy():
    return {
        "_id": "digiorg-logs-retention-7d", "_version": 1,
        "_seq_no": 0, "_primary_term": 1,
        "policy": {
            "policy_id": "digiorg-logs-retention-7d",
            "description": "Delete DigiOrg Fluentd log indices older than 7 days",
            "last_updated_time": 1900000000000,
            "schema_version": 1,
            "error_notification": None,
            "default_state": "hot",
            "states": [
                {"name": "hot", "actions": [], "transitions": [
                    {"state_name": "delete", "conditions": {"min_index_age": "7d"}},
                ]},
                {"name": "delete", "actions": [{"delete": {}}], "transitions": []},
            ],
            "ism_template": [{"index_patterns": ["digiorg-logs-*"], "priority": 100}],
        },
    }


def canonical_ism_explain():
    policy = canonical_ism_policy()["policy"]
    return {
        "primary": {"policy_id": None},
        "digiorg-logs-current": {
            "index.plugins.index_state_management.policy_id": "digiorg-logs-retention-7d",
            "index.opendistro.index_state_management.policy_id": "digiorg-logs-retention-7d",
            "index": "digiorg-logs-current", "index_uuid": "idx-log",
            "policy_id": "digiorg-logs-retention-7d", "enabled": True,
            "policy": policy, "policy_seq_no": 0, "policy_primary_term": 1,
            "index_creation_date": 1900000000000,
            "state": {"name": "hot", "start_time": 1900000000000},
            "action": {"name": "transition", "start_time": 1900000000000,
                       "index": -1, "failed": False, "consumed_retries": 0,
                       "last_retry_time": 0},
        },
        "jaeger-span-current": {"policy_id": None},
        "total_managed_indices": 1,
    }


def app(name, sources, resolved=OLD_COMMIT, op=None, reconciled="2026-09-02T06:00:00Z"):
    spec = {"sources": sources} if isinstance(sources, list) else {"source": sources}
    obj = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {"name": name, "namespace": "argocd", "uid": f"uid-{name}", "resourceVersion": f"rv-{name}-1"},
        "spec": spec,
        "status": {
            "health": {"status": "Healthy"},
            "sync": {"status": "Synced", "revision": resolved},
            "reconciledAt": reconciled,
        },
    }
    if op:
        obj["status"]["operationState"] = op
    return obj


def make_apps():
    apps = {
        "root-app": app("root-app", source(OLD_TAG, "apps"), op=operation("2026-09-02T05:00:00Z", [OLD_COMMIT])),
        "argocd": app("argocd", source(OLD_TAG, "platform/base/argocd"), op=operation("2026-09-02T05:01:00Z", [OLD_COMMIT])),
        "opensearch": app(
            "opensearch",
            [
                {"repoURL": "https://opensearch-project.github.io/helm-charts", "chart": "opensearch", "targetRevision": "3.7.0"},
                {"repoURL": CORE, "targetRevision": OLD_TAG, "ref": "values"},
                source(OLD_TAG, "platform/base/opensearch"),
            ],
            op=operation("2026-09-02T05:02:00Z", ["3.7.0", OLD_COMMIT, OLD_COMMIT]),
        ),
        "fluentd": app("fluentd", source(OLD_TAG, "platform/base/fluentd")),
        "app-config": app("app-config", source("main", "claims", "https://digiorg.local/gitea/DigiOrg/app-config.git"), resolved="a" * 40),
        "core-catalog": app("core-catalog", source("d531180b322dc0128477ecb9bb0fc9071b41d631", "compositions/local", "https://github.com/digiorg/core-catalog.git"), resolved="d531180b322dc0128477ecb9bb0fc9071b41d631"),
    }
    multi_core = {"harbor", "jaeger", "nats", "opencost", "sonarqube"}
    no_core = {"app-config", "cnpg", "core-catalog", "crossplane", "kyverno", "nats-jetstream-controller"}
    for name in EXPECTED_APPS:
        if name in apps:
            continue
        if name in no_core:
            apps[name] = app(name, {"repoURL": f"https://charts.example/{name}", "chart": name, "targetRevision": "1.0.0"})
        elif name in multi_core:
            apps[name] = app(name, [source(OLD_TAG, f"platform/base/{name}"), source(OLD_TAG, None, ref="values")])
        else:
            apps[name] = app(name, source(OLD_TAG, f"platform/base/{name}"))
    return apps


class FakeClock:
    def __init__(self):
        self.value = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.value

    def time(self):
        return 1900000000.0 + (self.value - 1000.0)

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += seconds


class StatefulFakeKubectl:
    """Stateful command fake: no process, network, container, or cluster access."""

    def __init__(self):
        self.apps = make_apps()
        self.server = "https://api.retained.example:6443"
        self.namespace_uid = "uid-kube-system"
        self.controller = {
            "apiVersion": "apps/v1", "kind": "StatefulSet",
            "metadata": {"name": "argocd-application-controller", "namespace": "argocd", "uid": "sts-uid", "resourceVersion": "sts-rv-1", "generation": 7},
            "spec": {"replicas": 1, "serviceName": "argocd-application-controller", "template": {"metadata": {"labels": {"safe": "true"}}}},
            "status": {"observedGeneration": 7, "replicas": 1, "readyReplicas": 1, "currentRevision": "rev-a", "updateRevision": "rev-a"},
        }
        self.pods = [self._pod("pod-old")]
        self.hpas = []
        self.hpa_listing: object = None
        self.commands = []
        self.patch_payloads = []
        self.restore_count = 0
        self.fail_second_app_patch = False
        self.concurrent_spec_mutation = False
        self.active_after_barrier = False
        self.app_config_drift_after_barrier = False
        self.stale_final_operation = False
        self.wrong_final_operation_revision = False
        self.wrong_final_sync_result_revision = False
        self.missing_final_sync_result = False
        self.argocd_stale_comparison = False
        self.argocd_invalid_comparison = False
        self.fresh_operation_not_later = False
        self.reuse_old_pod = False
        self.never_zero = False
        self.timeout_on_get = False
        self.interrupt_after_stop_accept = False
        self.interrupt_after_restore_accept = False
        self.schema_job_present_in_logging = False
        self.schema_not_found_stderr = 'Error from server (NotFound): jobs.batch "fluentd-log-schema" not found\n'
        self.mutate_controller_while_stopped = False
        self.change_controller_revision_after_restore = False
        self.hpa_after_barrier = False
        self.barrier_app_mutation = None
        self.final_app_mutation = None
        self.rollback_uid_replacement = False
        self.owner_readback_uid_replacement = False
        self.final_stopped_app_mutation = None
        self.completed_non_target_operation_before_stop = False
        self.owner_readbacks = 0

    def _pod(self, uid):
        return {"metadata": {"name": uid, "uid": uid, "namespace": "argocd", "ownerReferences": [{"uid": "sts-uid", "controller": True}]}, "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}]}}

    @staticmethod
    def _result(returncode=0, stdout="", stderr=""):
        return transition.CommandResult(returncode, stdout, stderr)

    def _json(self, value):
        return self._result(stdout=json.dumps(value))

    def _converge(self):
        root = self.apps["root-app"]
        root["status"].update({"health": {"status": "Healthy"}, "sync": {"status": "Synced", "revision": NEW_COMMIT}, "reconciledAt": "new-root"})
        root_revision = OLD_COMMIT if self.wrong_final_operation_revision else NEW_COMMIT
        root_started = "2026-09-02T05:00:00Z" if self.stale_final_operation else "2026-09-02T06:10:00Z"
        if self.fresh_operation_not_later:
            root_started = "2026-09-02T04:59:00Z"
        result_revision = OLD_COMMIT if self.wrong_final_sync_result_revision else NEW_COMMIT
        root["status"]["operationState"] = operation(root_started, [root_revision], result_revisions=[result_revision])
        if self.missing_final_sync_result:
            root["status"]["operationState"].pop("syncResult")
        argo = self.apps["argocd"]
        argo_reconciled = "2026-09-02T06:00:00Z" if self.argocd_stale_comparison else "2026-09-02T06:20:00Z"
        if self.argocd_invalid_comparison:
            argo_reconciled = "not-rfc3339"
        argo["status"].update({"health": {"status": "Healthy"}, "sync": {"status": "Synced", "revision": NEW_COMMIT}, "reconciledAt": argo_reconciled})
        opensearch = self.apps["opensearch"]
        opensearch["spec"]["sources"][1]["targetRevision"] = NEW_TAG
        opensearch["status"].update({"health": {"status": "Healthy"}, "sync": {"status": "Synced", "revisions": ["3.7.0", NEW_COMMIT, OLD_COMMIT]}, "reconciledAt": "new-os"})
        opensearch["status"]["operationState"] = operation("2026-09-02T06:11:00Z", ["3.7.0", NEW_COMMIT, OLD_COMMIT])
        if self.app_config_drift_after_barrier:
            self.apps["app-config"]["status"]["sync"]["revision"] = "drifted"

    def run(self, argv, timeout):
        self.commands.append((list(argv), timeout))
        if argv[0] == "git":
            if argv[1:] == ["rev-parse", "--show-toplevel"]:
                return self._result(stdout=str(ROOT) + "\n")
            if argv[1:] == ["rev-parse", "HEAD"]:
                return self._result(stdout=NEW_COMMIT + "\n")
            if argv[1:] == ["rev-parse", "HEAD^"]:
                return self._result(stdout=PRODUCT_BASE_COMMIT + "\n")
            if argv[1:] == ["status", "--porcelain=v1", "--untracked-files=all"]:
                return self._result(stdout="")
            if argv[1:3] == ["ls-tree", "--name-only"]:
                return self._result(stdout=argv[-1] + "\n")
            return self._result(stdout=f"tag-object\trefs/tags/{NEW_TAG}\n{NEW_COMMIT}\trefs/tags/{NEW_TAG}^{{}}\n")
        self.assert_safe_kubectl(argv, timeout)
        verb_index = argv.index("config") if "config" in argv else next(i for i, x in enumerate(argv) if x in {"get", "patch"})
        verb = argv[verb_index]
        tail = argv[verb_index + 1:]
        if verb == "config":
            return self._json({"clusters": [{"cluster": {"server": self.server}}], "current-context": "retained"})
        if self.timeout_on_get and verb == "get":
            raise TimeoutError("sentinel Bearer TOPSECRET")
        if verb == "get" and tail[0:2] == ["namespace", "kube-system"]:
            return self._json({"metadata": {"uid": self.namespace_uid}})
        if verb == "get" and tail[0] == "applications.argoproj.io":
            if len(tail) > 1 and not tail[1].startswith("-"):
                self.owner_readbacks += 1
                if self.owner_readback_uid_replacement and tail[1] == "argocd":
                    self.apps["argocd"]["metadata"]["uid"] = "replacement-argocd"
                return self._json(deepcopy(self.apps[tail[1]]))
            if self.controller["spec"]["replicas"] == 0 and self.active_after_barrier:
                self.apps["backstage"]["status"]["operationState"] = operation("active", [OLD_COMMIT], "Running")
            if self.controller["spec"]["replicas"] == 0 and self.barrier_app_mutation:
                name, mutation = self.barrier_app_mutation
                mutation(self.apps[name])
            if (self.controller["spec"]["replicas"] == 0 and self.owner_readbacks >= 2 and
                    self.final_stopped_app_mutation):
                name, mutation = self.final_stopped_app_mutation
                mutation(self.apps[name])
                self.final_stopped_app_mutation = None
            if self.controller["spec"]["replicas"] > 0 and self.restore_count and self.final_app_mutation:
                name, mutation = self.final_app_mutation
                mutation(self.apps[name])
            return self._json({"items": deepcopy(list(self.apps.values()))})
        if verb == "get" and tail[0] == "statefulsets.apps":
            if len(tail) > 1 and not tail[1].startswith("-"):
                if self.controller["spec"]["replicas"] == 0 and self.mutate_controller_while_stopped:
                    self.controller["spec"]["template"]["metadata"]["labels"]["unsafe"] = "mutation"
                return self._json(deepcopy(self.controller))
            return self._json({"items": [deepcopy(self.controller)]})
        if verb == "get" and tail[0] == "pods":
            return self._json({"items": deepcopy(self.pods)})
        if verb == "get" and tail[0] == "horizontalpodautoscalers.autoscaling":
            if self.hpa_listing is not None:
                return self._json(deepcopy(self.hpa_listing))
            if self.controller["spec"]["replicas"] == 0 and self.hpa_after_barrier:
                return self._json({
                    "apiVersion": "autoscaling/v2", "kind": "HorizontalPodAutoscalerList",
                    "metadata": {}, "items": [{
                        "apiVersion": "autoscaling/v2", "kind": "HorizontalPodAutoscaler",
                        "metadata": {"name": "late", "namespace": "argocd"},
                        "spec": {"scaleTargetRef": {"apiVersion": "APPS/v1", "kind": "statefulset", "name": "argocd-application-controller"}},
                    }],
                })
            return self._json({"apiVersion": "autoscaling/v2",
                               "kind": "HorizontalPodAutoscalerList",
                               "metadata": {}, "items": deepcopy(self.hpas)})
        if verb == "get" and tail[0:2] == ["job.batch", "fluentd-log-schema"]:
            namespace = argv[argv.index("-n") + 1]
            if namespace == "logging" and self.schema_job_present_in_logging:
                return self._json({"kind": "Job", "metadata": {"name": "fluentd-log-schema", "namespace": "logging"}})
            return self._result(1, "", self.schema_not_found_stderr)
        if verb == "patch":
            kind, name = tail[0], tail[1]
            payload = json.loads(tail[tail.index("-p") + 1])
            self.patch_payloads.append((kind, name, payload))
            if kind == "statefulsets.apps":
                tests = {item["path"]: item["value"] for item in payload if item["op"] == "test"}
                for path, expected in tests.items():
                    actual = transition.json_pointer(self.controller, path)
                    if actual != expected:
                        return self._result(1, stderr="Conflict")
                replicas = payload[-1]["value"]
                if replicas == 0 and self.completed_non_target_operation_before_stop:
                    self.apps["backstage"]["status"]["operationState"] = operation(
                        "2026-09-02T05:30:00Z", [OLD_COMMIT]
                    )
                self.controller["spec"]["replicas"] = replicas
                self.controller["metadata"]["generation"] += 1
                self.controller["metadata"]["resourceVersion"] = f"sts-rv-{self.controller['metadata']['generation']}"
                if not (replicas == 0 and self.never_zero):
                    self.controller["status"].update({"observedGeneration": self.controller["metadata"]["generation"], "replicas": replicas, "readyReplicas": replicas})
                    self.pods = [] if replicas == 0 else [self._pod("pod-old" if self.reuse_old_pod else "pod-new")]
                if replicas > 0:
                    self.restore_count += 1
                    if self.change_controller_revision_after_restore:
                        self.controller["status"].update({"currentRevision": "rev-b", "updateRevision": "rev-b"})
                    self._converge()
                    if self.interrupt_after_restore_accept:
                        raise KeyboardInterrupt("restore accepted")
                elif self.interrupt_after_stop_accept:
                    raise KeyboardInterrupt("stop accepted")
                return self._json(deepcopy(self.controller))
            if kind == "applications.argoproj.io":
                if name == "argocd" and self.fail_second_app_patch and payload[-1]["value"] == NEW_TAG:
                    if self.rollback_uid_replacement:
                        self.apps["root-app"]["metadata"]["uid"] = "replacement-root-uid"
                    return self._result(1, stderr='token="SUPERSECRET" patch failed')
                target = self.apps[name]
                for item in payload:
                    if item["op"] == "test" and transition.json_pointer(target, item["path"]) != item["value"]:
                        return self._result(1, stderr="Conflict")
                transition.json_pointer_replace(target, payload[-1]["path"], payload[-1]["value"])
                target["metadata"]["resourceVersion"] += "x"
                if self.concurrent_spec_mutation and name == "argocd" and payload[-1]["value"] == NEW_TAG:
                    target["spec"]["project"] = "evil"
                return self._json(deepcopy(target))
        raise AssertionError(f"unhandled command: {argv}")

    def assert_safe_kubectl(self, argv, timeout):
        assert argv[0] == "kubectl"
        assert "--kubeconfig" in argv and "--context" in argv
        assert any(x.startswith("--request-timeout=") for x in argv)
        assert 0 < timeout <= transition.CALL_SECONDS
        forbidden = {"delete", "apply", "replace", "rollout", "restart", "exec"}
        assert not forbidden.intersection(argv)
        if "patch" in argv:
            kind = argv[argv.index("patch") + 1]
            assert kind in {"statefulsets.apps", "applications.argoproj.io"}


class FakeRetainedClient:
    def __init__(self):
        self.new = False
        self.pvc_uid = "pvc-uid"
        self.memory_max = 2147483648
        self.log_count = 100
        self.jaeger_count = 200
        self.commands = []
        self.fluentd_uid_race = False
        self.fluentd_reads = 0
        self.malformed_policy = False
        self.hot_via_warm_policy = False
        self.policy_reads = 0
        self.explain_reads = 0
        self.pvc_spec_mutation = None
        self.vary_memory_current = False
        self.memory_current_reads = 0
        self.additional_indices = []
        self.additional_explain = {}

    def _container(self):
        resources = ({"requests": {"cpu": "100m", "memory": "1Gi"}, "limits": {"cpu": "1000m", "memory": "1Gi"}}
                     if not self.new else
                     {"requests": {"cpu": "250m", "memory": "1Gi"}, "limits": {"cpu": "1000m", "memory": "2Gi"}})
        return {"name": "opensearch", "image": transition.OPENSEARCH_IMAGE,
                "imagePullPolicy": "IfNotPresent", "resources": resources,
                "env": [{"name": "OPENSEARCH_JAVA_OPTS", "value": "-Xmx512M -Xms512M"},
                        {"name": "DISABLE_SECURITY_PLUGIN", "value": "true"}],
                "securityContext": {"capabilities": {"drop": ["ALL"]},
                                    "runAsNonRoot": True, "runAsUser": 1000},
                "volumeMounts": [{"name": "data", "mountPath": "/usr/share/opensearch/data"}]}

    def _pod(self, name, uid, owner):
        last_state = ({"terminated": {"reason": "OOMKilled", "finishedAt": "2026-09-02T05:30:00Z"}}
                      if uid == "old-pod" else {})
        return {"metadata": {"name": name, "uid": uid, "ownerReferences": [{"uid": owner, "controller": True}]},
                "spec": {"containers": [self._container()]},
                "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}],
                           "containerStatuses": [{"name": "opensearch", "restartCount": 0, "lastState": last_state}]}}

    def get_json(self, args, deadline, namespace=None):
        self.commands.append(("get_json", list(args), namespace, deadline.call_timeout()))
        resource = args[1]
        if resource == "statefulset.apps":
            return {"metadata": {"name": transition.OS_STS, "uid": "os-sts-uid", "generation": 3},
                    "spec": {"serviceName": "stable", "template": {"metadata": {"labels": {"stable": "yes"}},
                                                                  "spec": {"securityContext": {"fsGroup": 1000, "runAsUser": 1000}, "containers": [self._container()]}},
                             "replicas": 1},
                    "status": {"observedGeneration": 3, "replicas": 1, "readyReplicas": 1,
                               "currentRevision": "os-rev", "updateRevision": "os-rev"}}
        if resource == "pod":
            return self._pod(transition.OS_POD, "new-pod" if self.new else "old-pod", "os-sts-uid")
        if resource == "persistentvolumeclaim":
            spec = {
                "accessModes": ["ReadWriteOnce"],
                "resources": {"requests": {"storage": "8Gi"}},
                "storageClassName": "standard",
                "volumeMode": "Filesystem",
                "selector": None,
                "dataSource": None,
                "dataSourceRef": None,
                "volumeName": "pv-1",
            }
            if self.pvc_spec_mutation:
                self.pvc_spec_mutation(spec)
            return {"metadata": {"name": transition.OS_PVC, "uid": self.pvc_uid},
                    "spec": spec, "status": {"phase": "Bound", "capacity": {"storage": "8Gi"}}}
        if resource == "daemonset.apps":
            return {"metadata": {"name": "fluentd", "uid": "ds-uid"}}
        if resource == "pods":
            self.fluentd_reads += 1
            uid = f"fluentd-{self.fluentd_reads}" if self.fluentd_uid_race else "fluentd-uid"
            return {"items": [self._pod("fluentd-a", uid, "ds-uid")]}
        raise AssertionError(args)

    def kubectl(self, args, deadline, namespace=None):
        self.commands.append(("kubectl", list(args), namespace, deadline.call_timeout()))
        if args[:2] == ["get", "--raw"]:
            return 'fluentd_output_status_buffer_queue_length{plugin_id="out_opensearch",host="x"} 0\n'
        if args[0] != "exec":
            raise AssertionError(args)
        if "sh" in args:
            self.memory_current_reads += 1
            current = 1073741824 + (self.memory_current_reads if self.vary_memory_current else 0)
            return f"current={current}\nmax={self.memory_max}\nmax 0\n"
        url = args[-1]
        if "_cat/indices" in url:
            return json.dumps([
                {"index": "primary", "uuid": "idx-primary", "status": "open", "creation.date": "1900000000000"},
                {"index": "digiorg-logs-current", "uuid": "idx-log", "status": "open", "creation.date": "1900000000000"},
                {"index": "jaeger-span-current", "uuid": "idx-jaeger", "status": "open", "creation.date": "1900000000000"},
                *deepcopy(self.additional_indices),
            ])
        if "_plugins/_ism/policies" in url:
            self.policy_reads += 1
            age = "wrong" if self.malformed_policy else "7d"
            hot_target = "warm" if self.hot_via_warm_policy else "delete"
            states = [{"name": "hot", "actions": [], "transitions": [{"state_name": hot_target, "conditions": {"min_index_age": age}}]}]
            if self.hot_via_warm_policy:
                states.append({"name": "warm", "actions": [], "transitions": [{"state_name": "delete", "conditions": {"min_index_age": "7d"}}]})
            states.append({"name": "delete", "actions": [{"delete": {}}], "transitions": []})
            policy = canonical_ism_policy()
            policy["policy"]["states"] = states
            return json.dumps(policy)
        if "_plugins/_ism/explain" in url:
            self.explain_reads += 1
            explain = canonical_ism_explain()
            explain.update(deepcopy(self.additional_explain))
            explain["total_managed_indices"] += sum(
                item.get("policy_id") == "digiorg-logs-retention-7d"
                for item in self.additional_explain.values()
            )
            return json.dumps(explain)
        if "_cluster/health" in url:
            return '{"status":"yellow"}'
        if "_cat/recovery" in url:
            return '[]'
        if "_search" in url:
            return '{"hits":{"total":{"value":1}}}'
        if "digiorg-logs-current/_count" in url or "digiorg-logs-*/_count" in url:
            self.log_count += 1
            return json.dumps({"count": self.log_count})
        if "jaeger-span-current/_count" in url or "jaeger-span-*/_count" in url:
            self.jaeger_count += 1
            return json.dumps({"count": self.jaeger_count})
        if "/_count?" in url:
            return json.dumps({"count": self.log_count + self.jaeger_count})
        raise AssertionError(url)


class CleanBootstrapFake(StatefulFakeKubectl):
    """Native-command-boundary fake for post-`up` clean acceptance."""

    def __init__(self):
        super().__init__()
        self.runtime = FakeRetainedClient()
        self.runtime.new = True
        self.missing_telemetry = False
        self.no_observability_indices = False
        for name in ("root-app", "argocd"):
            self.apps[name]["spec"]["source"]["targetRevision"] = NEW_TAG
        self._converge()
        for name, identities in transition.CLEAN_SOURCE_GRAPH.items():
            sources = [dict((key, value) for key, value in zip(
                ("repoURL", "chart", "path", "ref", "targetRevision"), identity
            ) if value is not None) for identity in identities]
            self.apps[name]["spec"].pop("source", None)
            self.apps[name]["spec"].pop("sources", None)
            self.apps[name]["spec"]["source" if len(sources) == 1 else "sources"] = sources[0] if len(sources) == 1 else sources
        for item in self.apps.values():
            item.get("status", {}).pop("operationState", None)

    def run(self, argv, timeout):
        self.commands.append((list(argv), timeout))
        if argv[0] == "git":
            if argv[1:] == ["rev-parse", "--show-toplevel"]:
                return self._result(stdout=str(ROOT) + "\n")
            if argv[1:] == ["rev-parse", "HEAD"]:
                return self._result(stdout=NEW_COMMIT + "\n")
            if argv[1:] == ["rev-parse", "HEAD^"]:
                return self._result(stdout=PRODUCT_BASE_COMMIT + "\n")
            if argv[1:] == ["status", "--porcelain=v1", "--untracked-files=all"]:
                return self._result(stdout="")
            if argv[1:3] == ["ls-tree", "--name-only"]:
                return self._result(stdout=argv[-1] + "\n")
            return self._result(stdout=f"tag-object\trefs/tags/{NEW_TAG}\n{NEW_COMMIT}\trefs/tags/{NEW_TAG}^{{}}\n")
        self.assert_safe_read(argv, timeout)
        verb_index = argv.index("get") if "get" in argv else argv.index("exec")
        verb = argv[verb_index]
        tail = argv[verb_index + 1:]
        namespace = argv[argv.index("-n") + 1] if "-n" in argv else None
        if verb == "get" and tail[0] == "applications.argoproj.io":
            return self._json({"items": deepcopy(list(self.apps.values()))})
        if verb == "get" and tail[0] == "statefulsets.apps" and "-l" in tail:
            return self._json({"items": [deepcopy(self.controller)]})
        if verb == "get" and tail[0] == "pods" and namespace == "argocd":
            return self._json({"items": deepcopy(self.pods)})
        if verb == "get" and tail[0:2] == ["job.batch", "fluentd-log-schema"]:
            return self._result(1, "", 'Error from server (NotFound): jobs.batch "fluentd-log-schema" not found\n')
        resource_map = {
            "statefulset.apps": "statefulset.apps",
            "pod": "pod",
            "persistentvolumeclaim": "persistentvolumeclaim",
            "daemonset.apps": "daemonset.apps",
            "pods": "pods",
        }
        if verb == "get" and tail[0] in resource_map:
            value = self.runtime.get_json(["get", resource_map[tail[0]], *tail[1:2]],
                                          transition.Deadline(FakeClock(), 30, "fake"), namespace)
            return self._json(value)
        args = [verb, *tail]
        if verb == "get" and tail[0] == "--raw" and "/namespaces/tracing/services/" in tail[1]:
            return self._result(stdout="{}" if self.missing_telemetry else '{"data":["jaeger-query"]}')
        output = self.runtime.kubectl(args, transition.Deadline(FakeClock(), 30, "fake"), namespace)
        if self.no_observability_indices and "_cat/indices" in args[-1]:
            output = "[]"
        return self._result(stdout=output)

    @staticmethod
    def assert_safe_read(argv, timeout):
        assert argv[0] == "kubectl"
        assert "--kubeconfig" in argv and "--context" in argv
        assert any(item.startswith("--request-timeout=") for item in argv)
        assert 0 < timeout <= transition.CALL_SECONDS
        assert not {"patch", "apply", "delete", "create", "replace", "rollout"}.intersection(argv)


class RetainedCollectorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = FakeClock()
        self.evidence_path = Path(self.temp.name) / "retained.jsonl"
        self.evidence = transition.Evidence(self.evidence_path, self.clock)
        self.config = types.SimpleNamespace(current_primary_index="primary",
                                            representative_log_index="digiorg-logs-current",
                                            representative_jaeger_index="jaeger-span-current")
        self.client = FakeRetainedClient()
        self.collector = transition.RetainedCollector(self.client, self.config, self.clock, self.evidence)

    def tearDown(self):
        if not self.evidence.file.closed:
            self.evidence.close()
        self.temp.cleanup()

    def test_baseline_records_validated_old_pod_termination_scalars(self):
        self.collector.baseline(transition.Deadline(self.clock, 300, "baseline"))
        self.evidence.close()
        record = json.loads(self.evidence_path.read_text().splitlines()[0])
        self.assertEqual(record["last_termination_reason"], "OOMKilled")
        self.assertEqual(record["last_termination_time"], "2026-09-02T05:30:00Z")

    def test_baseline_then_exact_31_sample_window_succeeds(self):
        self.collector.baseline(transition.Deadline(self.clock, 300, "baseline"))
        self.client.new = True
        self.collector.observe()
        self.evidence.close()
        records = [json.loads(line) for line in self.evidence_path.read_text().splitlines()]
        self.assertEqual(sum(record["event"] == "retained-sample" for record in records), 31)
        self.assertEqual(records[-1]["event"], "retained-accepted")
        self.assertEqual(self.clock.value, 2800.0)
        flattened = [word for _, args, _, _ in self.client.commands for word in args]
        self.assertNotIn("delete", flattened)
        self.assertIn(transition.OS_PVC, flattened)

    def test_wrong_exact_cgroup_limit_fails_closed(self):
        self.collector.baseline(transition.Deadline(self.clock, 300, "baseline"))
        self.client.new = True
        self.client.memory_max = 1073741824
        with self.assertRaisesRegex(transition.TransitionError, "recovery deadline"):
            self.collector.observe()

    def test_pvc_replacement_fails_closed(self):
        self.collector.baseline(transition.Deadline(self.clock, 300, "baseline"))
        self.client.new = True
        self.client.pvc_uid = "replacement"
        with self.assertRaisesRegex(transition.TransitionError, "recovery deadline"):
            self.collector.observe()

    def test_fluentd_uid_race_fails_closed(self):
        self.collector.baseline(transition.Deadline(self.clock, 300, "baseline"))
        self.client.new = True
        self.client.fluentd_uid_race = True
        with self.assertRaisesRegex(transition.TransitionError, "recovery deadline"):
            self.collector.observe()

    def test_malformed_ism_policy_blocks_baseline(self):
        self.client.malformed_policy = True
        with self.assertRaisesRegex(transition.TransitionError, "ISM policy"):
            self.collector.baseline(transition.Deadline(self.clock, 300, "baseline"))

    def test_hot_via_warm_delete_policy_blocks_baseline(self):
        self.client.hot_via_warm_policy = True
        with self.assertRaisesRegex(transition.TransitionError, "ISM policy"):
            self.collector.baseline(transition.Deadline(self.clock, 300, "baseline"))

    def assert_policy_rejected(self, mutate):
        policy = canonical_ism_policy()
        mutate(policy["policy"])
        indices = {
            "primary": {"uid": "idx-primary", "created_ms": 1900000000000},
            "digiorg-logs-current": {"uid": "idx-log", "created_ms": 1900000000000},
            "jaeger-span-current": {"uid": "idx-jaeger", "created_ms": 1900000000000},
        }
        with self.assertRaisesRegex(transition.TransitionError, "ISM policy"):
            self.collector.validate_policy(policy, canonical_ism_explain(), indices)

    def test_duplicate_hot_state_shadowing_is_rejected(self):
        self.assert_policy_rejected(lambda policy: policy["states"].append({
            "name": "hot", "actions": [], "transitions": [
                {"state_name": "delete", "conditions": {"min_index_age": "7d"}},
            ],
        }))

    def test_extra_ism_template_is_rejected(self):
        self.assert_policy_rejected(lambda policy: policy["ism_template"].append({
            "index_patterns": ["other-*"], "priority": 100,
        }))

    def test_ism_template_missing_priority_is_rejected(self):
        self.assert_policy_rejected(lambda policy: policy["ism_template"][0].pop("priority"))

    def test_ism_template_additional_field_is_rejected(self):
        self.assert_policy_rejected(lambda policy: policy["ism_template"][0].update({
            "last_updated_time": 1900000000000,
        }))

    def test_hot_state_action_is_rejected(self):
        self.assert_policy_rejected(lambda policy: policy["states"][0]["actions"].append({
            "read_only": {},
        }))

    def test_extra_delete_action_is_rejected(self):
        self.assert_policy_rejected(lambda policy: policy["states"][1]["actions"].append({
            "notification": {"destination": {"slack": {"url": "https://invalid.example"}}},
        }))

    def test_delete_outgoing_transition_is_rejected(self):
        self.assert_policy_rejected(lambda policy: policy["states"][1]["transitions"].append({
            "state_name": "hot",
        }))

    def test_extra_state_is_rejected(self):
        self.assert_policy_rejected(lambda policy: policy["states"].append({
            "name": "warm", "actions": [], "transitions": [],
        }))

    def assert_explain_rejected(self, mutate):
        explain = canonical_ism_explain()
        mutate(explain["digiorg-logs-current"])
        with self.assertRaisesRegex(transition.TransitionError, "ISM policy"):
            self.collector.validate_policy(
                canonical_ism_policy(), explain,
                {"digiorg-logs-current": {"uid": "idx-log", "created_ms": 1900000000000}},
            )

    def test_explain_binding_missing_current_state_is_rejected(self):
        self.assert_explain_rejected(lambda item: item.pop("state"))

    def test_explain_binding_wrong_current_state_is_rejected(self):
        self.assert_explain_rejected(lambda item: item["state"].update({"name": "delete"}))

    def test_explain_binding_missing_current_action_is_rejected(self):
        self.assert_explain_rejected(lambda item: item.pop("action"))

    def test_explain_binding_wrong_current_action_is_rejected(self):
        self.assert_explain_rejected(lambda item: item["action"].update({"name": "delete"}))

    def test_explain_embedded_policy_with_wrong_reviewed_content_is_rejected(self):
        self.assert_explain_rejected(
            lambda item: item["policy"]["states"][0]["transitions"][0].update(
                {"conditions": {"min_index_age": "30d"}}
            )
        )

    def test_explain_embedded_stale_policy_revision_is_rejected(self):
        self.assert_explain_rejected(
            lambda item: item["policy"].update({"last_updated_time": 1899999999999})
        )

    def test_explain_action_failed_true_is_rejected(self):
        self.assert_explain_rejected(lambda item: item["action"].update({"failed": True}))

    def test_new_current_unbound_log_index_is_rejected(self):
        self.collector.baseline(transition.Deadline(self.clock, 300, "baseline"))
        self.client.additional_indices = [{
            "index": "digiorg-logs-new", "uuid": "idx-log-new", "status": "open",
            "creation.date": "1900000001000",
        }]
        self.client.additional_explain = {
            "digiorg-logs-new": {"policy_id": None, "enabled": None},
        }
        with self.assertRaisesRegex(transition.TransitionError, "ISM policy"):
            self.collector.verify_indices(transition.Deadline(self.clock, 300, "sample"))

    def test_new_current_correctly_bound_log_index_is_validated_after_inventory(self):
        self.collector.baseline(transition.Deadline(self.clock, 300, "baseline"))
        self.client.additional_indices = [{
            "index": "digiorg-logs-new", "uuid": "idx-log-new", "status": "open",
            "creation.date": "1900000001000",
        }]
        item = deepcopy(canonical_ism_explain()["digiorg-logs-current"])
        item.update({
            "index": "digiorg-logs-new", "index_uuid": "idx-log-new",
            "index_creation_date": 1900000001000,
        })
        self.client.additional_explain = {"digiorg-logs-new": item}
        self.client.commands.clear()

        self.collector.verify_indices(transition.Deadline(self.clock, 300, "sample"))

        urls = [args[-1] for command, args, _, _ in self.client.commands if command == "kubectl"]
        self.assertIn("_cat/indices", urls[0])
        self.assertIn("_plugins/_ism/policies", urls[1])
        self.assertIn("_plugins/_ism/explain", urls[2])

    def test_policy_and_explain_are_revalidated_at_every_observation(self):
        self.collector.baseline(transition.Deadline(self.clock, 300, "baseline"))
        self.client.new = True
        self.collector.observe()
        self.assertEqual(self.client.policy_reads, 32)
        self.assertEqual(self.client.explain_reads, 32)

    def test_complete_pvc_spec_drift_fails_closed(self):
        mutations = (
            lambda spec: spec.update({"accessModes": ["ReadOnlyMany"]}),
            lambda spec: spec.update({"storageClassName": "other"}),
            lambda spec: spec.update({"volumeMode": "Block"}),
            lambda spec: spec.update({"selector": {"matchLabels": {"disk": "other"}}}),
            lambda spec: spec["resources"]["requests"].update({"storage": "9Gi"}),
            lambda spec: spec.update({"dataSource": {"kind": "PersistentVolumeClaim", "name": "seed"}}),
            lambda spec: spec.update({"dataSourceRef": {"kind": "VolumeSnapshot", "name": "seed"}}),
            lambda spec: spec.update({"futureField": {"must": "also-be-closed"}}),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.client = FakeRetainedClient()
                self.collector = transition.RetainedCollector(
                    self.client, self.config, self.clock, self.evidence
                )
                self.collector.baseline(transition.Deadline(self.clock, 300, "baseline"))
                self.client.new = True
                self.client.pvc_spec_mutation = mutate
                with self.assertRaisesRegex(transition.TransitionError, "PVC identity"):
                    self.collector.verify_durable(
                        transition.Deadline(self.clock, 300, "PVC drift"),
                        require_candidate=False,
                    )


class CleanBootstrapAcceptanceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.kubeconfig = root / "kubeconfig-local.yaml"
        self.kubeconfig.write_text("safe", encoding="utf-8")
        self.kubeconfig.chmod(0o600)
        self.evidence = root / "clean-bootstrap.jsonl"
        self.clock = FakeClock()
        self.config = transition.Config(
            mode="clean-bootstrap-accept", kubeconfig=self.kubeconfig,
            context="kind-digiorg-core-dev", expected_server=None,
            expected_kube_system_uid=None, remote_url=CORE,
            runtime_tag=NEW_TAG, runtime_commit=NEW_COMMIT,
            old_tag=OLD_TAG, old_commit=OLD_COMMIT, evidence=self.evidence,
            collect_retained=False,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_clean_bootstrap_success_is_31_samples_and_never_transitions_or_mutates(self):
        fake = CleanBootstrapFake()
        transition.execute(self.config, runner=fake, clock=self.clock)
        records = [json.loads(line) for line in self.evidence.read_text().splitlines()]
        self.assertEqual(sum(item["event"] == "clean-bootstrap-sample" for item in records), 31)
        self.assertEqual(records[-1]["event"], "clean-bootstrap-accepted")
        self.assertEqual(stat.S_IMODE(self.evidence.stat().st_mode), 0o600)
        commands = [argv for argv, _ in fake.commands]
        self.assertFalse(any("patch" in argv for argv in commands))
        self.assertFalse(any(argv[0] == "nu" for argv in commands))
        self.assertTrue(all(argv[0] in {"git", "kubectl"} for argv in commands))

    def test_clean_bootstrap_queries_current_inventory_before_policy_and_explain(self):
        fake = CleanBootstrapFake()
        transition.execute(self.config, runner=fake, clock=self.clock)
        urls = [item for argv, _ in fake.commands for item in argv
                if isinstance(item, str) and item.startswith("http://127.0.0.1:9200/")]
        inventory = "http://127.0.0.1:9200/_cat/indices?format=json&h=index,uuid,status,creation.date&expand_wildcards=open"
        policy = "http://127.0.0.1:9200/_plugins/_ism/policies/digiorg-logs-retention-7d"
        explain = "http://127.0.0.1:9200/_plugins/_ism/explain/*?show_policy=true"
        positions = [(index, urls.index(policy, index), urls.index(explain, index))
                     for index, value in enumerate(urls) if value == inventory]
        self.assertEqual(len(positions), 31)
        self.assertTrue(all(inventory_pos < policy_pos < explain_pos
                            for inventory_pos, policy_pos, explain_pos in positions))

    def test_clean_bootstrap_fails_closed_when_natural_telemetry_is_missing(self):
        fake = CleanBootstrapFake()
        fake.missing_telemetry = True
        with self.assertRaisesRegex(transition.TransitionError, "telemetry|recovery deadline"):
            transition.execute(self.config, runner=fake, clock=self.clock)
        self.assertFalse(any("patch" in argv for argv, _ in fake.commands))

    def test_clean_bootstrap_does_not_require_preexisting_log_or_trace_indices(self):
        fake = CleanBootstrapFake()
        fake.no_observability_indices = True
        transition.execute(self.config, runner=fake, clock=self.clock)

    def test_clean_bootstrap_allows_memory_current_to_vary_with_stable_counter_and_headroom(self):
        fake = CleanBootstrapFake()
        fake.runtime.vary_memory_current = True
        transition.execute(self.config, runner=fake, clock=self.clock)

    def test_clean_bootstrap_does_not_require_retained_identities_or_selected_indices(self):
        self.assertIsNone(self.config.current_primary_index)
        self.assertIsNone(self.config.representative_log_index)
        self.assertIsNone(self.config.representative_jaeger_index)
        fake = CleanBootstrapFake()
        self.assertTrue(all("operationState" not in item.get("status", {}) for item in fake.apps.values()))
        transition.execute(self.config, runner=fake, clock=self.clock)



class Harness(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.kubeconfig = root / "kubeconfig"
        self.kubeconfig.write_text("safe", encoding="utf-8")
        self.kubeconfig.chmod(0o600)
        self.evidence = root / "evidence.jsonl"
        self.config = transition.Config(
            kubeconfig=self.kubeconfig, context="retained",
            expected_server="https://api.retained.example:6443",
            expected_kube_system_uid="uid-kube-system", remote_url=CORE,
            runtime_tag=NEW_TAG, runtime_commit=NEW_COMMIT,
            old_tag=OLD_TAG, old_commit=OLD_COMMIT, evidence=self.evidence,
            collect_retained=False,
        )
        self.clock = FakeClock()

    def tearDown(self):
        self.temp.cleanup()

    def execute(self, fake=None):
        fake = fake or StatefulFakeKubectl()
        transition.execute(self.config, runner=fake, clock=self.clock)
        return fake


class SourceContractTest(unittest.TestCase):
    def test_v3_identity_is_bound_to_reviewed_issue352_merge(self):
        self.assertEqual(transition.RUNTIME_TAG, NEW_TAG)
        self.assertEqual(transition.PRODUCT_BASE_COMMIT, PRODUCT_BASE_COMMIT)

    def test_clean_source_graph_constant_matches_all_reviewed_application_manifests(self):
        manifest_paths = [ROOT / "platform/base/argocd/applications/root-app.yaml",
                          *sorted((ROOT / "apps/platform").glob("*.yaml"))]
        actual = {}
        for path in manifest_paths:
            application = yaml.safe_load(path.read_text(encoding="utf-8"))
            actual[application["metadata"]["name"]] = [
                transition.source_identity(item) for item in transition.source_list(application)
            ]
        self.assertEqual(actual, transition.CLEAN_SOURCE_GRAPH)

    def test_clean_cli_needs_no_retained_cluster_or_index_arguments(self):
        config = transition.parse_args([
            "--mode", "clean-bootstrap-accept",
            "--kubeconfig", "/secure/issue350-clean-bootstrap/kubeconfig-local.yaml",
            "--context", "kind-digiorg-core-dev", "--remote-url", CORE,
            "--runtime-tag", NEW_TAG, "--runtime-commit", NEW_COMMIT,
            "--evidence", "/secure/issue350-clean-bootstrap-accept.jsonl",
        ])
        self.assertFalse(config.collect_retained)
        self.assertIsNone(config.expected_server)
        self.assertIsNone(config.current_primary_index)

    def test_clean_mode_requires_exact_kind_context_and_up_kubeconfig_name(self):
        root = Path(tempfile.gettempdir())
        base = dict(
            mode="clean-bootstrap-accept", kubeconfig=root / "kubeconfig-local.yaml",
            context="kind-digiorg-core-dev", expected_server=None,
            expected_kube_system_uid=None, remote_url=CORE, runtime_tag=NEW_TAG,
            runtime_commit=NEW_COMMIT, old_tag=OLD_TAG, old_commit=OLD_COMMIT,
            evidence=root / "new-clean-evidence-does-not-exist.jsonl", collect_retained=False,
        )
        for field, value in (("context", "other"), ("kubeconfig", root / "other.yaml")):
            with self.subTest(field=field):
                values = dict(base)
                values[field] = value
                with self.assertRaisesRegex(transition.TransitionError, "clean-bootstrap"):
                    transition.validate_local_config(transition.Config(**values))

    def test_only_clean_mode_allows_the_exact_up_generated_kubeconfig_in_checkout(self):
        generated = ROOT / "kubeconfig-local.yaml"
        self.assertTrue(transition.checkout_path_allowed(
            "kubeconfig", generated, ROOT, "clean-bootstrap-accept"
        ))
        self.assertFalse(transition.checkout_path_allowed(
            "evidence", ROOT / "evidence.jsonl", ROOT, "clean-bootstrap-accept"
        ))
        self.assertFalse(transition.checkout_path_allowed(
            "kubeconfig", generated, ROOT, "retained-transition"
        ))
        self.assertFalse(transition.checkout_path_allowed(
            "kubeconfig", ROOT / "other-kubeconfig", ROOT, "clean-bootstrap-accept"
        ))

    def test_exact_reviewed_application_inventory_and_core_multiplicity(self):
        apps = make_apps()
        self.assertEqual(tuple(sorted(apps)), EXPECTED_APPS)
        core_refs = [item for application in apps.values() for item in transition.source_list(application)
                     if item and item.get("repoURL") == CORE]
        self.assertEqual(len(apps), 32)
        self.assertEqual(len(core_refs), 32)
        manifest_paths = [ROOT / "platform/base/argocd/applications/root-app.yaml",
                          *sorted((ROOT / "apps/platform").glob("*.yaml"))]
        actual_names = tuple(sorted(path.stem for path in manifest_paths))
        self.assertEqual(actual_names, EXPECTED_APPS)
        actual_counts = {path.stem: path.read_text(encoding="utf-8").count(f"repoURL: {CORE}")
                         for path in manifest_paths}
        fake_counts = {name: sum(item.get("repoURL") == CORE for item in transition.source_list(application))
                       for name, application in apps.items()}
        self.assertEqual(fake_counts, actual_counts)

    def test_production_deadlines_are_bounded_and_safe(self):
        self.assertEqual(transition.BARRIER_SECONDS, 300)
        self.assertEqual(transition.CONVERGENCE_SECONDS, 1200)
        self.assertEqual(transition.CALL_SECONDS, 20)
        self.assertEqual(transition.WAIT_SECONDS, 120)
        self.assertFalse(hasattr(transition, "FAST_TEST_MODE"))

    def test_source_forbids_shell_and_workload_mutation_literals(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("shell=True", text)
        for forbidden in ("kubectl delete", "kubectl apply", "kubectl exec", "rollout restart"):
            self.assertNotIn(forbidden, text)

    def test_cli_always_enables_integrated_retained_collector(self):
        config = transition.parse_args([
            "--mode", "retained-transition",
            "--kubeconfig", "/secure/k", "--context", "retained",
            "--expected-server", "https://api.example", "--expected-kube-system-uid", "uid",
            "--remote-url", CORE, "--runtime-tag", NEW_TAG, "--runtime-commit", NEW_COMMIT,
            "--old-tag", OLD_TAG, "--old-commit", OLD_COMMIT, "--evidence", "/secure/e",
            "--current-primary-index", "primary", "--representative-log-index", "logs-1",
            "--representative-jaeger-index", "jaeger-1",
        ])
        self.assertTrue(config.collect_retained)


class RunbookContractTest(unittest.TestCase):
    def test_annotated_tag_object_and_peeled_commit_have_distinct_exact_bindings(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        self.assertIn("tag-object ref must exist and has its own exact recorded object SHA", text)
        self.assertIn("only the peeled `^{}` ref equals the runtime commit", text)
        self.assertIn("tag-object SHA is distinct from the runtime commit SHA", text)
        self.assertNotIn("both the tag object ref and peeled ref equal to `$RUNTIME_COMMIT`", text)

    def test_tracked_runbook_has_no_runnable_clean_bootstrap_and_requires_external_exact_binding(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        clean = text.split("## Deterministic clean bootstrap", 1)[1].split("## Deterministic retained acceptance", 1)[0]
        for forbidden in ("kind delete cluster", "local-setup.nu up"):
            self.assertNotIn(forbidden, text)
        self.assertNotIn("```bash", clean)
        self.assertNotIn("--mode clean-bootstrap-accept", clean)
        for requirement in (
            "separate post-publication launcher", "literal exact reviewed commit SHA",
            "tree SHA", "tag-object SHA", "peeled SHA",
            "independently reviewed", "target-path-probed",
            "separate user deployment approval",
        ):
            self.assertIn(requirement, clean)

    def test_runbook_closes_exact_ism_and_complete_pvc_contracts(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        for phrase in (
            "direct hot→delete", "rejects hot→warm→delete",
            "policy and explain are re-queried and revalidated at every sample",
            "embedded `policy` must exactly equal the current GET-policy body",
            "every currently open `digiorg-logs-*` index, including indices created after baseline",
            "`action.failed` must be exactly `false`",
            "accessModes", "storageClassName", "volumeMode", "selector",
            "requested storage", "dataSource", "dataSourceRef", "every other PVC spec field",
            "canonical PVC spec hash",
        ):
            self.assertIn(phrase, text)

    def test_runbook_declares_executable_authority_and_exact_retained_window(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        self.assertIn("`scripts/issue350_352_runtime_v3_transition.py` is authoritative", text)
        self.assertNotIn("`scripts/issue350_352_runtime_v3_transition.py is authoritative`", text)
        for argument in (
            "--kubeconfig", "--context", "--expected-server",
            "--expected-kube-system-uid", "--remote-url", "--runtime-tag",
            "--runtime-commit", "--old-tag", "--old-commit", "--evidence",
        ):
            self.assertIn(argument, text)
        for criterion in (
            "exactly 30 minutes", "every 60 seconds", "memory.events.max delta = 0",
            "memory.max - memory.current >= 268435456", "active recovery count = 0",
            "Fluentd `out_opensearch` buffer queue", "queue reaches `0`",
            "representative log index count", "representative Jaeger index count",
            "No traffic is injected",
            "15-minute recovery-readiness gate", "new OpenSearch Pod UID",
            "post-rollout t=0", "31 samples", "durable StatefulSet/PVC",
            "http://127.0.0.1:9200", "h=index,stage", "empty JSON array",
            "/api/v1/namespaces/logging/pods/${pod}:24231/proxy/metrics",
            'fluentd_output_status_buffer_queue_length{plugin_id="out_opensearch"',
            "digiorg-logs-retention-7d", "min_index_age", "creation_date",
            "complete specs remain memory-only",
        ):
            self.assertIn(criterion, text)
        for incorrect in ("platform-logging", "https://127.0.0.1:9200", "24220/api/plugins.json", "h=index,active_only", "Pod/PVC identities"):
            self.assertNotIn(incorrect, text)

    def test_runbook_documents_exact_control_plane_closure(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        for criterion in (
            "requested and syncResult revision", "chronologically later", "RFC3339",
            "non-replica StatefulSet spec", "preflight currentRevision",
            "all Application UIDs", "every complete Application spec",
            "exact-NotFound", "Job/logging/fluentd-log-schema",
        ):
            self.assertIn(criterion, text)

    def test_runbook_keeps_release_and_mutation_boundaries_explicit(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        self.assertIn("does not authorize rollout", text)
        self.assertIn("separate Chris approval", text)
        self.assertIn("annotated tag", text)
        self.assertIn("exact-SHA CI", text)
        self.assertIn("no automatic rollback", text)
        self.assertIn("only permitted mutations", text)

    def test_runbook_uses_exact_checkout_pvc_and_executable_collector(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        self.assertIn('git clone --no-checkout "$REMOTE_URL" "$CHECKOUT_350"', text)
        self.assertIn('git checkout --detach "refs/tags/$RUNTIME_TAG^{}"', text)
        self.assertIn("OS_PVC=opensearch-cluster-master-opensearch-cluster-master-0", text)
        self.assertIn('get persistentvolumeclaim "$OS_PVC"', text)
        self.assertNotIn("get pvc -l app=opensearch", text)
        self.assertNotIn("There is intentionally no workload collector", text)
        self.assertIn("collector is integrated", text)
        self.assertIn("cgroup `memory.max` exactly `2147483648`", text)
        self.assertIn("post-rollout candidate template hash", text)
        self.assertNotIn("canonical template/resources", text)
        self.assertIn("--current-primary-index", text)
        self.assertIn("--representative-log-index", text)
        self.assertIn("--representative-jaeger-index", text)


class SecurityTest(Harness):
    def test_exact_json_rejects_duplicate_keys_recursively_and_preserves_normal_json(self):
        for payload in ('{"count":0,"count":1}',
                        '{"hits":{"total":0,"total":1}}'):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(transition.TransitionError, "duplicate JSON key"):
                    transition.exact_json(payload, dict)
        self.assertEqual(
            transition.exact_json('{"count":1,"nested":{"ok":true}}', dict),
            {"count": 1, "nested": {"ok": True}},
        )

    def test_local_checkout_integrity_git_checks_precede_kubernetes(self):
        fake = self.execute()
        commands = [argv for argv, _ in fake.commands]
        first_kubectl = next(i for i, argv in enumerate(commands) if argv[0] == "kubectl")
        git_commands = commands[:first_kubectl]
        self.assertIn(["git", "rev-parse", "--show-toplevel"], git_commands)
        self.assertIn(["git", "rev-parse", "HEAD"], git_commands)
        self.assertIn(["git", "rev-parse", "HEAD^"], git_commands)
        self.assertIn(["git", "status", "--porcelain=v1", "--untracked-files=all"], git_commands)
        self.assertIn(["git", "ls-tree", "--name-only", "HEAD", "--", "scripts/issue350_352_runtime_v3_transition.py"], git_commands)
        self.assertIn(["git", "ls-tree", "--name-only", "HEAD", "--", "specs/350-opensearch-memory-headroom/runtime-v3-transition.md"], git_commands)

    def test_dirty_checkout_fails_before_kubernetes(self):
        class BadGit(StatefulFakeKubectl):
            def run(self, argv, timeout):
                if argv[0] == "git" and argv[1:3] == ["status", "--porcelain=v1"]:
                    self.commands.append((list(argv), timeout))
                    return self._result(stdout=" M changed\n")
                return super().run(argv, timeout)
        fake = BadGit()
        with self.assertRaisesRegex(transition.TransitionError, "checkout"):
            self.execute(fake)
        self.assertFalse(any(argv[0] == "kubectl" for argv, _ in fake.commands))

    def test_wrong_checkout_head_fails_before_kubernetes(self):
        class WrongHead(StatefulFakeKubectl):
            def run(self, argv, timeout):
                if argv == ["git", "rev-parse", "HEAD"]:
                    self.commands.append((list(argv), timeout))
                    return self._result(stdout="f" * 40 + "\n")
                return super().run(argv, timeout)
        fake = WrongHead()
        with self.assertRaisesRegex(transition.TransitionError, "HEAD"):
            self.execute(fake)
        self.assertFalse(any(argv[0] == "kubectl" for argv, _ in fake.commands))

    def test_wrong_runtime_parent_fails_before_kubernetes(self):
        class WrongParent(StatefulFakeKubectl):
            def run(self, argv, timeout):
                if argv == ["git", "rev-parse", "HEAD^"]:
                    self.commands.append((list(argv), timeout))
                    return self._result(stdout="0" * 40 + "\n")
                return super().run(argv, timeout)

        fake = WrongParent()
        with self.assertRaisesRegex(transition.TransitionError, "product base"):
            self.execute(fake)
        self.assertFalse(any(argv[0] == "kubectl" for argv, _ in fake.commands))

    def test_kubeconfig_and_evidence_must_be_outside_checkout(self):
        fake = StatefulFakeKubectl()
        self.config.evidence = ROOT / "inside-evidence-must-not-be-created.jsonl"
        with self.assertRaisesRegex(transition.TransitionError, "outside repository"):
            self.execute(fake)
        self.assertFalse(self.config.evidence.exists())
        self.assertFalse(any(argv[0] == "kubectl" for argv, _ in fake.commands))

    def test_app_config_resolved_revision_requires_one_lowercase_commit(self):
        for value in (None, "", "app-config-sha", "A" * 40):
            with self.subTest(value=value):
                if self.evidence.exists():
                    self.evidence.unlink()
                fake = StatefulFakeKubectl()
                fake.apps["app-config"]["status"]["sync"]["revision"] = value
                with self.assertRaisesRegex(transition.TransitionError, "app-config resolved revision"):
                    self.execute(fake)
                self.assertFalse(fake.patch_payloads)
        if self.evidence.exists():
            self.evidence.unlink()
        fake = StatefulFakeKubectl()
        fake.apps["app-config"]["status"]["sync"]["revisions"] = ["a" * 40]
        with self.assertRaisesRegex(transition.TransitionError, "exactly one representation"):
            self.execute(fake)
        self.assertFalse(fake.patch_payloads)

    def test_one_shot_identity_literals_are_exact_before_commands(self):
        cases = {
            "remote_url": "--upload-pack=evil",
            "runtime_tag": "wrong-runtime",
            "old_tag": "wrong-old",
            "old_commit": "f" * 40,
        }
        expected = {
            "remote_url": CORE, "runtime_tag": NEW_TAG,
            "old_tag": OLD_TAG, "old_commit": OLD_COMMIT,
        }
        for attribute, value in cases.items():
            with self.subTest(attribute=attribute):
                if self.evidence.exists():
                    self.evidence.unlink()
                fake = StatefulFakeKubectl()
                setattr(self.config, attribute, value)
                with self.assertRaisesRegex(transition.TransitionError, "fixed Issue #350"):
                    self.execute(fake)
                self.assertFalse(fake.commands)
                setattr(self.config, attribute, expected[attribute])

    def test_option_like_context_is_rejected_before_commands(self):
        fake = StatefulFakeKubectl()
        self.config.context = "--context-from-attacker"
        with self.assertRaisesRegex(transition.TransitionError, "option-like"):
            self.execute(fake)
        self.assertFalse(fake.commands)

    def test_invalid_prior_operation_timestamp_fails_before_mutation(self):
        for timestamp in ("not-rfc3339", "2026-09-02 05:00:00Z"):
            with self.subTest(timestamp=timestamp):
                if self.evidence.exists():
                    self.evidence.unlink()
                fake = StatefulFakeKubectl()
                fake.apps["root-app"]["status"]["operationState"]["startedAt"] = timestamp
                with self.assertRaisesRegex(transition.TransitionError, "operation identity"):
                    self.execute(fake)
                self.assertFalse(fake.patch_payloads)

    def test_rejects_default_or_insecure_kubeconfig_and_existing_evidence(self):
        fake = StatefulFakeKubectl()
        self.kubeconfig.chmod(0o640)
        with self.assertRaisesRegex(transition.TransitionError, "0600"):
            self.execute(fake)
        self.kubeconfig.chmod(0o600)
        self.evidence.write_text("existing", encoding="utf-8")
        with self.assertRaisesRegex(transition.TransitionError, "must not exist"):
            self.execute(fake)
        self.assertFalse(fake.commands)

    def test_evidence_is_exclusive_0600_allowlisted_and_diagnostics_are_redacted(self):
        fake = self.execute()
        self.assertEqual(stat.S_IMODE(self.evidence.stat().st_mode), 0o600)
        records = [json.loads(line) for line in self.evidence.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(records)
        self.assertTrue(all(set(record) <= transition.EVIDENCE_FIELDS for record in records))
        joined = self.evidence.read_text(encoding="utf-8")
        self.assertNotIn('"spec":', joined)
        dirty = 'Bearer ABC Basic ZGVhZA== https://alice:pw@example/x token="S1" password: S2 secret=S3 {"clientSecret":"S4"}'
        clean = transition.redact(dirty)
        for sentinel in ("ABC", "ZGVhZA", "alice", "pw", "S1", "S2", "S3", "S4"):
            self.assertNotIn(sentinel, clean)
        self.assertIn("[REDACTED]", clean)
        self.assertTrue(any(command[0][0] == "git" for command in fake.commands))

    def test_evidence_has_auditable_phase_facts_and_rfc3339_time(self):
        self.execute()
        records = [json.loads(line) for line in self.evidence.read_text(encoding="utf-8").splitlines()]
        by_event = {record["event"]: record for record in records}
        for event in ("preflight", "controller-stopped", "barrier-verified", "owners-closed", "controller-restored", "control-plane-closed"):
            self.assertIn(event, by_event)
            self.assertTrue(by_event[event]["time"].endswith("Z"))
            self.assertIsInstance(by_event[event]["elapsed_seconds"], (int, float))
            self.assertGreater(by_event[event]["deadline_seconds"], 0)
        self.assertEqual(by_event["preflight"]["application_count"], 32)
        self.assertIn("controller_spec_hash", by_event["preflight"])
        self.assertIn("application_spec_hash", by_event["barrier-verified"])
        expected_owner_specs = {name: deepcopy(make_apps()[name]["spec"]) for name in ("root-app", "argocd")}
        for spec in expected_owner_specs.values():
            spec["source"]["targetRevision"] = NEW_TAG
        self.assertEqual(by_event["owners-closed"]["application_spec_hash"], transition.digest(expected_owner_specs))
        self.assertEqual(by_event["control-plane-closed"]["schema_job_absence"], "exact-NotFound:logging/fluentd-log-schema")
        self.assertEqual(by_event["control-plane-closed"]["source_counts"], {"old": 29, "v3": 3})
        self.assertIn("operation_hashes", by_event["control-plane-closed"])

    def test_evidence_write_always_redacts_error_fields(self):
        path = Path(self.temp.name) / "direct-evidence.jsonl"
        evidence = transition.Evidence(path, self.clock)
        evidence.write("probe", error='Bearer RAWTOKEN {"password":"RAWPASS"}')
        evidence.close()
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("RAWTOKEN", text)
        self.assertNotIn("RAWPASS", text)
        self.assertIn("[REDACTED]", text)

    def test_wrong_cluster_fails_before_mutation(self):
        fake = StatefulFakeKubectl()
        fake.namespace_uid = "wrong"
        with self.assertRaisesRegex(transition.TransitionError, "cluster identity"):
            self.execute(fake)
        self.assertFalse(fake.patch_payloads)

    def test_missing_required_prior_operation_identity_fails_preflight(self):
        fake = StatefulFakeKubectl()
        fake.apps["root-app"]["status"].pop("operationState")
        with self.assertRaisesRegex(transition.TransitionError, "prior operation identity"):
            self.execute(fake)
        self.assertFalse(fake.patch_payloads)

    def test_missing_or_malformed_prior_operation_revisions_fail_preflight(self):
        mutations = (
            lambda state: state.pop("syncResult"),
            lambda state: state["operation"]["sync"].update({"revisions": "not-a-list"}),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                if self.evidence.exists():
                    self.evidence.unlink()
                fake = StatefulFakeKubectl()
                mutate(fake.apps["root-app"]["status"]["operationState"])
                with self.assertRaisesRegex(transition.TransitionError, "prior operation identity"):
                    self.execute(fake)
                self.assertFalse(fake.patch_payloads)

    def test_timeout_diagnostic_sentinel_is_redacted_from_error_and_evidence(self):
        fake = StatefulFakeKubectl()
        fake.timeout_on_get = True
        with self.assertRaises(transition.TransitionError) as caught:
            self.execute(fake)
        self.assertNotIn("TOPSECRET", str(caught.exception))
        self.assertNotIn("TOPSECRET", self.evidence.read_text(encoding="utf-8"))


class TransitionBehaviorTest(Harness):
    def test_completed_non_target_operation_crossing_stop_is_rejected_before_owner_patches(self):
        fake = StatefulFakeKubectl()
        fake.completed_non_target_operation_before_stop = True
        with self.assertRaisesRegex(transition.TransitionError, "operation identity changed"):
            self.execute(fake)
        self.assertEqual(fake.restore_count, 1)
        self.assertFalse(any(kind == "applications.argoproj.io"
                             for kind, _, _ in fake.patch_payloads))

    def test_extra_or_missing_application_fails_preflight(self):
        mutations = (
            lambda apps: apps.pop("backstage"),
            lambda apps: apps.update({"external-unrelated": app("external-unrelated", source(OLD_TAG, "x"))}),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                if self.evidence.exists():
                    self.evidence.unlink()
                fake = StatefulFakeKubectl()
                mutate(fake.apps)
                with self.assertRaisesRegex(transition.TransitionError, "inventory"):
                    self.execute(fake)
                self.assertFalse(fake.patch_payloads)

    def test_owner_uid_replacement_on_readback_rolls_back_while_stopped(self):
        fake = StatefulFakeKubectl()
        fake.owner_readback_uid_replacement = True
        with self.assertRaisesRegex(transition.TransitionError, "UID"):
            self.execute(fake)
        self.assertEqual(fake.restore_count, 1)

    def test_final_stopped_recheck_rejects_concurrent_application_mutation(self):
        fake = StatefulFakeKubectl()
        fake.final_stopped_app_mutation = ("backstage", lambda item: item["spec"].update({"project": "race"}))
        with self.assertRaisesRegex(transition.TransitionError, "last stopped gate"):
            self.execute(fake)
        self.assertEqual(fake.restore_count, 1)
        self.assertEqual(transition.target(fake.apps["root-app"]), OLD_TAG)
        self.assertEqual(transition.target(fake.apps["argocd"]), OLD_TAG)

    def test_preflight_requires_exact_retained_source_tuples(self):
        mutations = (
            lambda apps: apps["root-app"]["spec"]["source"].update({"ref": "unexpected"}),
            lambda apps: apps["argocd"]["spec"]["source"].update({"chart": "unexpected"}),
            lambda apps: apps["opensearch"]["spec"]["sources"][1].update({"path": "wrong"}),
            lambda apps: apps["fluentd"]["spec"]["source"].update({"repoURL": "https://wrong.invalid/core.git"}),
            lambda apps: apps["app-config"]["spec"]["source"].update({"path": "wrong"}),
            lambda apps: apps["core-catalog"]["spec"]["source"].update({"repoURL": "https://wrong.invalid/catalog.git"}),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                if self.evidence.exists():
                    self.evidence.unlink()
                fake = StatefulFakeKubectl()
                mutate(fake.apps)
                with self.assertRaisesRegex(transition.TransitionError, "source graph|graph mismatch"):
                    self.execute(fake)
                self.assertFalse(fake.patch_payloads)

    def test_schema_job_in_logging_blocks_even_if_not_found_elsewhere(self):
        fake = StatefulFakeKubectl()
        fake.schema_job_present_in_logging = True
        with self.assertRaisesRegex(transition.TransitionError, "convergence deadline"):
            self.execute(fake)
        schema_commands = [command for command, _ in fake.commands if "fluentd-log-schema" in command]
        self.assertTrue(schema_commands)
        self.assertTrue(all(command[command.index("-n") + 1] == "logging" for command in schema_commands))

    def test_generic_notfound_marker_collision_is_not_job_absence(self):
        fake = StatefulFakeKubectl()
        fake.schema_not_found_stderr = "credential helper NotFound while loading context"
        with self.assertRaisesRegex(transition.TransitionError, "convergence deadline"):
            self.execute(fake)

    def test_success_has_deterministic_call_order_and_exact_cas_payloads(self):
        fake = self.execute()
        mutations = [(kind, name, payload[-1]["value"]) for kind, name, payload in fake.patch_payloads]
        self.assertEqual(mutations[:4], [
            ("statefulsets.apps", "argocd-application-controller", 0),
            ("applications.argoproj.io", "root-app", NEW_TAG),
            ("applications.argoproj.io", "argocd", NEW_TAG),
            ("statefulsets.apps", "argocd-application-controller", 1),
        ])
        stop = fake.patch_payloads[0][2]
        self.assertEqual(stop, [
            {"op": "test", "path": "/metadata/uid", "value": "sts-uid"},
            {"op": "test", "path": "/metadata/resourceVersion", "value": "sts-rv-1"},
            {"op": "test", "path": "/spec/replicas", "value": 1},
            {"op": "replace", "path": "/spec/replicas", "value": 0},
        ])
        root_patch = fake.patch_payloads[1][2]
        self.assertEqual([item["path"] for item in root_patch], ["/metadata/uid", "/metadata/resourceVersion", "/spec/source/repoURL", "/spec/source/path", "/spec/source/targetRevision", "/spec/source/targetRevision"])
        self.assertEqual(fake.restore_count, 1)
        self.assertTrue(any("fluentd-log-schema" in command[0] for command in fake.commands))

    def test_active_operation_before_barrier_fails_without_mutation(self):
        fake = StatefulFakeKubectl()
        fake.apps["backstage"]["status"]["operationState"] = operation("now", [OLD_COMMIT], "Running")
        with self.assertRaisesRegex(transition.TransitionError, "active operation"):
            self.execute(fake)
        self.assertFalse(fake.patch_payloads)

    def test_pending_top_level_operation_before_barrier_fails_without_mutation(self):
        fake = StatefulFakeKubectl()
        fake.apps["backstage"]["operation"] = {"sync": {"prune": True}}
        with self.assertRaisesRegex(transition.TransitionError, "pending operation"):
            self.execute(fake)
        self.assertFalse(fake.patch_payloads)

    def test_active_operation_after_barrier_rolls_back_and_restores(self):
        fake = StatefulFakeKubectl()
        fake.active_after_barrier = True
        with self.assertRaisesRegex(transition.TransitionError, "active operation"):
            self.execute(fake)
        self.assertEqual(fake.patch_payloads[-1][2][-1]["value"], 1)
        self.assertEqual(fake.apps["root-app"]["spec"]["source"]["targetRevision"], OLD_TAG)

    def test_barrier_rejects_any_application_spec_or_uid_change(self):
        mutations = (
            ("backstage", lambda item: item["spec"].update({"project": "evil"})),
            ("cert-manager", lambda item: item["metadata"].update({"uid": "replacement"})),
        )
        for mutation in mutations:
            with self.subTest(name=mutation[0]):
                if self.evidence.exists():
                    self.evidence.unlink()
                fake = StatefulFakeKubectl()
                fake.barrier_app_mutation = mutation
                with self.assertRaisesRegex(transition.TransitionError, "Application closure"):
                    self.execute(fake)
                self.assertFalse(any(item[0] == "applications.argoproj.io" for item in fake.patch_payloads))

    def test_final_rejects_any_application_spec_or_uid_change(self):
        mutations = (
            ("fluentd", lambda item: item["spec"].update({"project": "evil"})),
            ("external-secrets", lambda item: item["metadata"].update({"uid": "replacement"})),
        )
        for mutation in mutations:
            with self.subTest(name=mutation[0]):
                if self.evidence.exists():
                    self.evidence.unlink()
                fake = StatefulFakeKubectl()
                fake.final_app_mutation = mutation
                with self.assertRaisesRegex(transition.TransitionError, "convergence deadline"):
                    self.execute(fake)

    def test_rollback_rejects_owner_uid_replacement_before_patch(self):
        fake = StatefulFakeKubectl()
        fake.fail_second_app_patch = True
        fake.rollback_uid_replacement = True
        with self.assertRaisesRegex(transition.TransitionError, "UID changed"):
            self.execute(fake)
        root_rollbacks = [payload for kind, name, payload in fake.patch_payloads
                          if kind == "applications.argoproj.io" and name == "root-app" and payload[-1]["value"] == OLD_TAG]
        self.assertFalse(root_rollbacks)

    def test_hpa_and_degraded_controller_fail_before_mutation(self):
        fake = StatefulFakeKubectl()
        fake.hpas = [{
            "apiVersion": "autoscaling/v2", "kind": "HorizontalPodAutoscaler",
            "metadata": {"name": "bad", "namespace": "argocd"},
            "spec": {"scaleTargetRef": {"apiVersion": "apps/v1", "kind": "StatefulSet", "name": "argocd-application-controller"}},
        }]
        with self.assertRaisesRegex(transition.TransitionError, "HPA"):
            self.execute(fake)
        self.assertFalse(fake.patch_payloads)
        self.evidence.unlink()
        fake = StatefulFakeKubectl()
        fake.controller["status"]["readyReplicas"] = 0
        with self.assertRaisesRegex(transition.TransitionError, "fully ready"):
            self.execute(fake)
        self.assertFalse(fake.patch_payloads)

    def test_malformed_hpa_enumeration_fails_closed_before_mutation(self):
        valid_item = {
            "apiVersion": "autoscaling/v2", "kind": "HorizontalPodAutoscaler",
            "metadata": {"name": "unrelated", "namespace": "other"},
            "spec": {"scaleTargetRef": {
                "apiVersion": "apps/v1", "kind": "Deployment", "name": "unrelated",
            }},
        }
        valid_list = {
            "apiVersion": "autoscaling/v2", "kind": "HorizontalPodAutoscalerList",
            "metadata": {}, "items": [],
        }
        malformed = (
            {},
            {**valid_list, "apiVersion": "autoscaling/v1"},
            {**valid_list, "kind": "List"},
            {**valid_list, "items": None},
            {**valid_list, "items": {}},
            {**valid_list, "items": [valid_item] * 1001},
            {**valid_list, "items": [{}]},
        )
        for listing in malformed:
            with self.subTest(listing=listing):
                if self.evidence.exists():
                    self.evidence.unlink()
                fake = StatefulFakeKubectl()
                fake.hpa_listing = listing
                with self.assertRaisesRegex(transition.TransitionError, "HPA list shape"):
                    self.execute(fake)
                self.assertFalse(fake.patch_payloads)

    def test_controller_name_and_namespace_are_exact(self):
        for field, value in (("name", "lookalike-controller"), ("namespace", "other")):
            with self.subTest(field=field):
                if self.evidence.exists():
                    self.evidence.unlink()
                fake = StatefulFakeKubectl()
                fake.controller["metadata"][field] = value
                with self.assertRaisesRegex(transition.TransitionError, "exact application controller"):
                    self.execute(fake)
                self.assertFalse(fake.patch_payloads)

    def test_hpa_created_after_stop_blocks_owner_patches(self):
        fake = StatefulFakeKubectl()
        fake.hpa_after_barrier = True
        with self.assertRaisesRegex(transition.TransitionError, "HPA"):
            self.execute(fake)
        self.assertFalse(any(item[0] == "applications.argoproj.io" for item in fake.patch_payloads))

    def test_controller_non_replica_spec_mutation_while_stopped_is_rejected(self):
        fake = StatefulFakeKubectl()
        fake.mutate_controller_while_stopped = True
        with self.assertRaisesRegex(transition.TransitionError, "controller.*spec"):
            self.execute(fake)
        self.assertFalse(any(item[0] == "applications.argoproj.io" for item in fake.patch_payloads))

    def test_restored_controller_must_return_to_preflight_revision(self):
        fake = StatefulFakeKubectl()
        fake.change_controller_revision_after_restore = True
        with self.assertRaisesRegex(transition.TransitionError, "new controller Pod"):
            self.execute(fake)

    def test_second_patch_failure_rolls_root_back_while_stopped_then_restores(self):
        fake = StatefulFakeKubectl()
        fake.fail_second_app_patch = True
        with self.assertRaises(transition.TransitionError):
            self.execute(fake)
        mutations = [(name, payload[-1]["value"]) for _, name, payload in fake.patch_payloads]
        self.assertIn(("root-app", OLD_TAG), mutations)
        self.assertEqual(mutations[-1], ("argocd-application-controller", 1))
        self.assertEqual(fake.apps["root-app"]["spec"]["source"]["targetRevision"], OLD_TAG)

    def test_concurrent_spec_mutation_triggers_rollback(self):
        fake = StatefulFakeKubectl()
        fake.concurrent_spec_mutation = True
        with self.assertRaisesRegex(transition.TransitionError, "spec changed"):
            self.execute(fake)
        self.assertEqual(fake.apps["root-app"]["spec"]["source"]["targetRevision"], OLD_TAG)
        self.assertEqual(fake.apps["argocd"]["spec"]["source"]["targetRevision"], OLD_TAG)

    def test_restore_requires_exact_new_stable_pod_identity(self):
        fake = StatefulFakeKubectl()
        fake.reuse_old_pod = True
        with self.assertRaisesRegex(transition.TransitionError, "new controller Pod"):
            self.execute(fake)
        self.assertEqual(fake.apps["root-app"]["spec"]["source"]["targetRevision"], NEW_TAG)
        app_patch_count = sum(kind == "applications.argoproj.io" for kind, _, _ in fake.patch_payloads)
        self.assertEqual(app_patch_count, 2, "must not roll back after controller restoration")

    def test_argocd_zero_diff_requires_fresh_comparison_not_operation(self):
        fake = StatefulFakeKubectl()
        fake.argocd_stale_comparison = True
        with self.assertRaisesRegex(transition.TransitionError, "convergence deadline"):
            self.execute(fake)
        self.assertEqual(fake.apps["root-app"]["spec"]["source"]["targetRevision"], NEW_TAG)

    def test_fresh_timestamps_must_be_valid_and_chronologically_later(self):
        for attr in ("fresh_operation_not_later", "argocd_invalid_comparison"):
            with self.subTest(attr=attr):
                if self.evidence.exists():
                    self.evidence.unlink()
                fake = StatefulFakeKubectl()
                setattr(fake, attr, True)
                with self.assertRaisesRegex(transition.TransitionError, "convergence deadline"):
                    self.execute(fake)

    def test_stale_or_wrong_operation_revision_is_rejected(self):
        for attr in ("stale_final_operation", "wrong_final_operation_revision"):
            with self.subTest(attr=attr):
                fake = StatefulFakeKubectl()
                setattr(fake, attr, True)
                with self.assertRaisesRegex(transition.TransitionError, "convergence deadline"):
                    self.execute(fake)
                if self.evidence.exists():
                    self.evidence.unlink()

    def test_wrong_or_missing_sync_result_revision_is_rejected(self):
        for attr in ("wrong_final_sync_result_revision", "missing_final_sync_result"):
            with self.subTest(attr=attr):
                if self.evidence.exists():
                    self.evidence.unlink()
                fake = StatefulFakeKubectl()
                setattr(fake, attr, True)
                with self.assertRaisesRegex(transition.TransitionError, "convergence deadline"):
                    self.execute(fake)

    def test_app_config_resolved_revision_drift_is_rejected(self):
        fake = StatefulFakeKubectl()
        fake.app_config_drift_after_barrier = True
        with self.assertRaisesRegex(transition.TransitionError, "convergence deadline"):
            self.execute(fake)

    def test_waits_and_subprocesses_are_bounded(self):
        fake = StatefulFakeKubectl()
        fake.never_zero = True
        with self.assertRaisesRegex(transition.TransitionError, "deadline"):
            self.execute(fake)
        self.assertLessEqual(sum(self.clock.sleeps), transition.WAIT_SECONDS + transition.POLL_SECONDS)
        self.assertTrue(all(0 < timeout <= transition.CALL_SECONDS for _, timeout in fake.commands))

    def test_forbidden_workload_and_data_mutations_never_occur(self):
        fake = self.execute()
        flattened = [item for command, _ in fake.commands for item in command]
        for forbidden in ("delete", "apply", "exec", "persistentvolumeclaims", "indices", "mapping", "shards"):
            self.assertNotIn(forbidden, flattened)
        patched = {(kind, name) for kind, name, _ in fake.patch_payloads}
        self.assertEqual(patched, {
            ("statefulsets.apps", "argocd-application-controller"),
            ("applications.argoproj.io", "root-app"),
            ("applications.argoproj.io", "argocd"),
        })

    def test_interruption_after_stop_accept_rolls_back_and_restores(self):
        fake = StatefulFakeKubectl()
        fake.interrupt_after_stop_accept = True
        with self.assertRaises(transition.TransitionError):
            self.execute(fake)
        self.assertEqual(fake.controller["spec"]["replicas"], 1)
        self.assertEqual(transition.target(fake.apps["root-app"]), OLD_TAG)

    def test_interruption_after_restore_accept_never_rolls_back_owners(self):
        fake = StatefulFakeKubectl()
        fake.interrupt_after_restore_accept = True
        with self.assertRaises(transition.TransitionError):
            self.execute(fake)
        self.assertEqual(fake.controller["spec"]["replicas"], 1)
        self.assertEqual(transition.target(fake.apps["root-app"]), NEW_TAG)
        self.assertEqual(transition.target(fake.apps["argocd"]), NEW_TAG)
        app_patches = [entry for entry in fake.patch_payloads if entry[0] == "applications.argoproj.io"]
        self.assertEqual(len(app_patches), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
