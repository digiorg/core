#!/usr/bin/env python3
"""Behavioral contracts for the offline-testable Issue #348 transition."""

from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import stat
import tempfile
import types
import unittest
from unittest import mock
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "issue348_runtime_v2_transition.py"
RUNBOOK = ROOT / "specs" / "345-log-schema-isolation" / "runtime-v2-transition.md"
SPEC = spec_from_file_location("issue348_transition", SCRIPT)
assert SPEC and SPEC.loader
transition = module_from_spec(SPEC)
SPEC.loader.exec_module(transition)

OLD_TAG = "issue301-runtime-v16-20260817T130820Z"
OLD_COMMIT = "8e6b8908f99ebf76db47c15613eff523644c23f6"
NEW_TAG = "issue348-runtime-v5-20260918T181846Z"
NEW_COMMIT = "0123456789abcdef0123456789abcdef01234567"
CANDIDATE_BASE_COMMIT = "b32d1c18eb0d1048d8e38743f5fdd1c68a72936d"
PREVIOUS_TAG = "issue350-352-runtime-v3-20260904T195619Z"
PREVIOUS_COMMIT = "f6e7d58c0b03ee6a3ec6ed9e1e22e5023f861549"
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


def kyverno_synced_resources():
    """Return the 58 non-hook resources in the reviewed Kyverno 3.8.1 inventory."""
    resources = []

    def add(group, kind, names, namespace=None):
        resources.extend({
            "group": group, "version": "v1", "kind": kind,
            "namespace": namespace, "name": name, "status": "Synced",
        } for name in names)

    add("", "ServiceAccount", (
        "kyverno-admission-controller", "kyverno-background-controller",
        "kyverno-cleanup-controller", "kyverno-reports-controller",
    ), "kyverno")
    add("", "ConfigMap", ("kyverno", "kyverno-metrics"), "kyverno")
    add("apiextensions.k8s.io", "CustomResourceDefinition", (
        "cleanuppolicies.kyverno.io", "clustercleanuppolicies.kyverno.io",
        "clusterpolicies.kyverno.io", "globalcontextentries.kyverno.io",
        "policies.kyverno.io", "policyexceptions.kyverno.io",
        "updaterequests.kyverno.io", "clusterephemeralreports.reports.kyverno.io",
        "ephemeralreports.reports.kyverno.io", "clusterpolicyreports.wgpolicyk8s.io",
        "policyreports.wgpolicyk8s.io",
    ))
    add("rbac.authorization.k8s.io", "ClusterRole", (
        "kyverno:admission-controller", "kyverno:admission-controller:core",
        "kyverno:background-controller", "kyverno:background-controller:core",
        "kyverno:cleanup-controller", "kyverno:cleanup-controller:core",
        "kyverno:rbac:admin:policies", "kyverno:rbac:view:policies",
        "kyverno:rbac:admin:policyreports", "kyverno:rbac:view:policyreports",
        "kyverno:rbac:admin:reports", "kyverno:rbac:view:reports",
        "kyverno:rbac:admin:updaterequests", "kyverno:rbac:view:updaterequests",
        "kyverno:reports-controller", "kyverno:reports-controller:core",
    ))
    add("rbac.authorization.k8s.io", "ClusterRoleBinding", (
        "kyverno:admission-controller", "kyverno:admission-controller:view",
        "kyverno:background-controller", "kyverno:background-controller:view",
        "kyverno:cleanup-controller", "kyverno:reports-controller",
        "kyverno:reports-controller:view",
    ))
    controller_names = (
        "kyverno-admission-controller", "kyverno-background-controller",
        "kyverno-cleanup-controller", "kyverno-reports-controller",
    )
    rbac_controller_names = tuple(name.replace("kyverno-", "kyverno:", 1)
                                  for name in controller_names)
    add("rbac.authorization.k8s.io", "Role", rbac_controller_names, "kyverno")
    add("rbac.authorization.k8s.io", "RoleBinding", rbac_controller_names, "kyverno")
    add("", "Service", (
        "kyverno-svc", "kyverno-svc-metrics",
        "kyverno-background-controller-metrics", "kyverno-cleanup-controller",
        "kyverno-cleanup-controller-metrics", "kyverno-reports-controller-metrics",
    ), "kyverno")
    add("apps", "Deployment", controller_names, "kyverno")
    assert len(resources) == 58
    return resources


def kyverno_full_resource_inventory(application):
    """Return the reviewed 69-entry Kyverno status inventory."""
    reviewed = deepcopy(application["status"]["resources"])
    if len(reviewed) == 69:
        return reviewed
    synced = kyverno_synced_resources()
    resources = synced[:29] + reviewed + synced[29:]
    assert len(resources) == 69
    return resources


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


def app(name, sources, resolved: str | list[str] = OLD_COMMIT, op=None,
        reconciled="2026-09-02T06:00:00Z"):
    spec = {"sources": sources} if isinstance(sources, list) else {"source": sources}
    sync = {"revisions": resolved} if isinstance(resolved, list) else {"revision": resolved}
    obj = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {"name": name, "namespace": "argocd", "uid": f"uid-{name}", "resourceVersion": f"rv-{name}-1"},
        "spec": spec,
        "status": {
            "health": {"status": "Healthy"},
            "sync": {"status": "Synced", **sync},
            "reconciledAt": reconciled,
        },
    }
    if op:
        obj["status"]["operationState"] = op
    return obj


def make_apps():
    apps = {
        "root-app": app("root-app", source(PREVIOUS_TAG, "apps"), resolved=PREVIOUS_COMMIT, op=operation("2026-09-02T05:00:00Z", [PREVIOUS_COMMIT])),
        "argocd": app("argocd", source(PREVIOUS_TAG, "platform/base/argocd"), resolved=PREVIOUS_COMMIT, op=operation("2026-09-02T05:01:00Z", [PREVIOUS_COMMIT])),
        "opensearch": app(
            "opensearch",
            [
                {"repoURL": "https://opensearch-project.github.io/helm-charts", "chart": "opensearch", "targetRevision": "3.7.0"},
                {"repoURL": CORE, "targetRevision": PREVIOUS_TAG, "ref": "values"},
                source(OLD_TAG, "platform/base/opensearch"),
            ],
            resolved=["3.7.0", PREVIOUS_COMMIT, OLD_COMMIT],
            op=operation("2026-09-02T05:02:00Z", ["3.7.0", PREVIOUS_COMMIT, OLD_COMMIT]),
        ),
        "fluentd": app(
            "fluentd", source(OLD_TAG, "platform/base/fluentd"),
            op=operation("2026-09-02T05:02:30Z", [OLD_COMMIT]),
        ),
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
    for name, identities in transition.PREFLIGHT_SOURCE_GRAPH.items():
        sources = [
            {key: value for key, value in zip(
                ("repoURL", "chart", "path", "ref", "targetRevision"), identity
            ) if value is not None}
            for identity in identities
        ]
        apps[name]["spec"].pop("source", None)
        apps[name]["spec"].pop("sources", None)
        if len(sources) == 1:
            apps[name]["spec"]["source"] = sources[0]
        else:
            apps[name]["spec"]["sources"] = sources
    kyverno = apps["kyverno"]
    kyverno["spec"]["source"]["helm"] = {
        "values": transition.KYVERNO_V3_METADATA_BLOCK,
    }
    kyverno["status"]["sync"]["status"] = "OutOfSync"
    kyverno["status"]["resources"] = [
        {"group": "apiextensions.k8s.io", "version": "v1",
         "kind": "CustomResourceDefinition", "name": name, "status": "OutOfSync"}
        for name in sorted(transition.KYVERNO_SAFE_OUT_OF_SYNC_CRDS)
    ]
    kyverno["status"]["resources"] = kyverno_full_resource_inventory(kyverno)
    kyverno["status"]["operationState"] = operation(
        "2026-09-02T05:03:00Z", ["3.8.1"]
    )
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
        self.fail_second_app_patch_after_apply = False
        self.fail_root_rollback = False
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
        self.application_inventory_reads = 0
        self.kyverno_stale_operation = False
        self.kyverno_invalid_finished = False
        self.kyverno_diff_output = ""
        self.kyverno_diff_stderr = ""
        self.kyverno_diff_timeout = False
        self.application_list_kind = "ApplicationList"
        self.unstable_final_reconciled = False
        self.previous_remote_commit = PREVIOUS_COMMIT
        self.old_remote_commit = OLD_COMMIT
        self.configured_kubeconfig: Path | None = None
        self.argocd_environments = []
        self.argocd_kubeconfig_snapshots = []

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
        opensearch["spec"]["sources"][2]["targetRevision"] = NEW_TAG
        opensearch["status"].update({"health": {"status": "Healthy"}, "sync": {"status": "Synced", "revisions": ["3.7.0", NEW_COMMIT, NEW_COMMIT]}, "reconciledAt": "2026-09-02T06:11:00Z"})
        fluentd = self.apps["fluentd"]
        fluentd["spec"]["source"]["targetRevision"] = NEW_TAG
        fluentd["status"].update({"health": {"status": "Healthy"},
                                  "sync": {"status": "Synced", "revision": NEW_COMMIT},
                                  "reconciledAt": "2026-09-02T06:11:30Z"})
        fluentd["status"]["operationState"] = operation(
            "2026-09-02T06:11:30Z", [NEW_COMMIT]
        )
        kyverno = self.apps["kyverno"]
        kyverno["spec"]["source"]["helm"]["values"] = transition.kyverno_candidate_values(
            kyverno["spec"]["source"]["helm"]["values"]
        )
        kyverno["status"].update({"health": {"status": "Healthy"},
                                  "sync": {"status": "Synced", "revision": "3.8.1"},
                                  "reconciledAt": "2026-09-02T06:12:00Z"})
        kyverno["status"].pop("resources", None)
        kyverno_started = "2026-09-02T05:03:00Z" if self.kyverno_stale_operation else "2026-09-02T06:12:00Z"
        kyverno["status"]["operationState"] = operation(kyverno_started, ["3.8.1"])
        if self.kyverno_invalid_finished:
            kyverno["status"]["operationState"]["finishedAt"] = "invalid"
        if self.app_config_drift_after_barrier:
            self.apps["app-config"]["status"]["sync"]["revision"] = "drifted"

    def run(self, argv, timeout, env=None):
        self.commands.append((list(argv), timeout))
        if argv[0] == "git":
            if argv[1:] == ["rev-parse", "--show-toplevel"]:
                return self._result(stdout=str(ROOT) + "\n")
            if argv[1:] == ["rev-parse", "HEAD"]:
                return self._result(stdout=NEW_COMMIT + "\n")
            if argv[1:] == ["rev-parse", "HEAD^"]:
                return self._result(stdout=CANDIDATE_BASE_COMMIT + "\n")
            if argv[1:] == ["status", "--porcelain=v1", "--untracked-files=all"]:
                return self._result(stdout="")
            if argv[1:3] == ["ls-tree", "--name-only"]:
                return self._result(stdout=argv[-1] + "\n")
            requested_ref = argv[-2]
            remote_commits = {
                f"refs/tags/{NEW_TAG}": NEW_COMMIT,
                f"refs/tags/{PREVIOUS_TAG}": self.previous_remote_commit,
                f"refs/tags/{OLD_TAG}": self.old_remote_commit,
            }
            peeled = remote_commits[requested_ref]
            return self._result(
                stdout=f"{'e' * 40}\t{requested_ref}\n{peeled}\t{requested_ref}^{{}}\n"
            )
        if argv[0] == "argocd":
            assert argv == ["argocd", "app", "diff", "kyverno", "--core", "--refresh"]
            assert env is not None and env.get("KUBECONFIG")
            isolated = Path(env["KUBECONFIG"])
            assert self.configured_kubeconfig is not None
            assert isolated != self.configured_kubeconfig
            assert isolated.is_file()
            assert stat.S_IMODE(isolated.stat().st_mode) == 0o600
            self.argocd_environments.append(dict(env))
            self.argocd_kubeconfig_snapshots.append(isolated.read_text(encoding="utf-8"))
            if self.kyverno_diff_timeout:
                raise TimeoutError("pinned Argo CD diff timed out")
            return self._result(stdout=self.kyverno_diff_output, stderr=self.kyverno_diff_stderr)
        self.assert_safe_kubectl(argv, timeout)
        verb_index = argv.index("config") if "config" in argv else next(i for i, x in enumerate(argv) if x in {"get", "patch"})
        verb = argv[verb_index]
        tail = argv[verb_index + 1:]
        if verb == "config":
            if tail == ["use-context", "retained"]:
                return self._result(stdout='Switched to context "retained".\n')
            if tail == ["set-context", "--current", "--namespace=argocd"]:
                return self._result(stdout='Context "retained" modified.\n')
            return self._json({"clusters": [{"cluster": {"server": self.server}}], "current-context": "retained"})
        if self.timeout_on_get and verb == "get":
            raise TimeoutError("sentinel Bearer TOPSECRET")
        if verb == "get" and tail[0:2] == ["namespace", "kube-system"]:
            return self._json({"metadata": {"uid": self.namespace_uid}})
        if verb == "get" and tail[0:2] == ["--raw", "/apis/argoproj.io/v1alpha1/namespaces/argocd/applications"]:
            self.application_inventory_reads += 1
            if self.unstable_final_reconciled and self.restore_count:
                self.apps["backstage"]["status"]["reconciledAt"] = (
                    f"2026-09-02T06:{self.application_inventory_reads % 60:02d}:00Z"
                )
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
            return self._json({"apiVersion": "argoproj.io/v1alpha1", "kind": self.application_list_kind,
                               "items": deepcopy(list(self.apps.values()))})
        if verb == "get" and tail[0] == "applications.argoproj.io":
            self.owner_readbacks += 1
            if self.owner_readback_uid_replacement and tail[1] == "argocd":
                self.apps["argocd"]["metadata"]["uid"] = "replacement-argocd"
            return self._json(deepcopy(self.apps[tail[1]]))
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
                if (name == "root-app" and self.fail_root_rollback and
                        payload[-1]["value"] == PREVIOUS_TAG):
                    return self._result(1, stderr="root rollback patch failed")
                if name == "argocd" and self.fail_second_app_patch and payload[-1]["value"] == NEW_TAG:
                    if self.fail_second_app_patch_after_apply:
                        transition.json_pointer_replace(
                            self.apps[name], payload[-1]["path"], payload[-1]["value"]
                        )
                        self.apps[name]["metadata"]["resourceVersion"] += "x"
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
        assert "--kubeconfig" in argv
        is_local_context_edit = (
            "config" in argv
            and any(item in argv for item in ("use-context", "set-context"))
        )
        assert "--context" in argv or is_local_context_edit
        assert any(x.startswith("--request-timeout=") for x in argv)
        assert 0 < timeout <= transition.CALL_SECONDS
        forbidden = {"delete", "apply", "replace", "rollout", "restart", "exec"}
        assert not forbidden.intersection(argv)
        if "patch" in argv:
            kind = argv[argv.index("patch") + 1]
            assert kind in {"statefulsets.apps", "applications.argoproj.io"}


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
            previous_tag=PREVIOUS_TAG, previous_commit=PREVIOUS_COMMIT,
            old_tag=OLD_TAG, old_commit=OLD_COMMIT, evidence=self.evidence,
            mode="retained-convergence",
        )
        self.clock = FakeClock()

    def tearDown(self):
        self.temp.cleanup()

    def execute(self, fake=None):
        fake = fake or StatefulFakeKubectl()
        fake.configured_kubeconfig = self.kubeconfig
        transition.execute(self.config, runner=fake, clock=self.clock)
        return fake


class SourceContractTest(unittest.TestCase):
    def test_v5_identity_is_bound_to_reviewed_candidate_and_previous_runtime(self):
        self.assertEqual(transition.RUNTIME_TAG, NEW_TAG)
        self.assertEqual(transition.CANDIDATE_BASE_COMMIT, CANDIDATE_BASE_COMMIT)
        self.assertEqual(transition.PREVIOUS_TAG, PREVIOUS_TAG)
        self.assertEqual(transition.PREVIOUS_COMMIT, PREVIOUS_COMMIT)

    def test_normal_source_graph_stays_on_main_and_generated_closure_is_explicit(self):
        manifest_paths = [ROOT / "platform/base/argocd/applications/root-app.yaml",
                          *sorted((ROOT / "apps/platform").glob("*.yaml"))]
        actual = {}
        for path in manifest_paths:
            application = yaml.safe_load(path.read_text(encoding="utf-8"))
            actual[application["metadata"]["name"]] = [
                transition.source_identity(item) for item in transition.source_list(application)
            ]
        core = [identity for identities in actual.values() for identity in identities if identity[0] == CORE]
        self.assertEqual(len(core), 32)
        self.assertTrue(all(identity[-1] == "main" for identity in core))
        generated = [identity for identities in transition.CLEAN_SOURCE_GRAPH.values()
                     for identity in identities if identity[0] == CORE]
        self.assertEqual(sum(identity[-1] == NEW_TAG for identity in generated), 5)
        self.assertEqual(sum(identity[-1] == OLD_TAG for identity in generated), 27)
        self.assertFalse(any(identity[-1] == PREVIOUS_TAG for identity in generated))

    def test_cli_is_explicit_retained_convergence_without_index_arguments(self):
        config = transition.parse_args([
            "--mode", "retained-convergence", "--kubeconfig", "/secure/k",
            "--context", "retained", "--expected-server", "https://api.example",
            "--expected-kube-system-uid", "uid", "--remote-url", CORE,
            "--runtime-tag", NEW_TAG, "--runtime-commit", NEW_COMMIT,
            "--previous-tag", PREVIOUS_TAG, "--previous-commit", PREVIOUS_COMMIT,
            "--old-tag", OLD_TAG, "--old-commit", OLD_COMMIT, "--evidence", "/secure/e",
        ])
        self.assertEqual(config.mode, "retained-convergence")
        self.assertFalse(hasattr(config, "current_primary_index"))
        parser_text = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("RetainedCollector", parser_text)
        self.assertNotIn("Acceptance", parser_text)
        self.assertNotIn("OBSERVATION_SAMPLES", parser_text)

    def test_production_deadlines_and_stable_sample_count_are_bounded(self):
        self.assertEqual(transition.BARRIER_SECONDS, 300)
        self.assertEqual(transition.CONVERGENCE_SECONDS, 1200)
        self.assertEqual(transition.CONVERGENCE_STABLE_SAMPLES, 3)
        self.assertGreater(transition.CONVERGENCE_SAMPLE_SECONDS, 0)
        self.assertEqual(transition.CALL_SECONDS, 20)

    def test_source_forbids_shell_and_workload_mutation_literals(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("shell=True", text)
        for forbidden in ("kubectl delete", "kubectl apply", "kubectl exec", "rollout restart"):
            self.assertNotIn(forbidden, text)


class RunbookContractTest(unittest.TestCase):
    def test_runbook_documents_complete_kyverno_resource_inventory_contract(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        self.assertIn("complete status inventory must equal the reviewed 69", text)
        self.assertIn("58 Synced and exactly 11 OutOfSync", text)

    def test_runbook_documents_v345_kubeconfig_and_namespace_isolation(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        self.assertIn("`KUBECONFIG`", text)
        self.assertIn("`argocd app diff kyverno --core --refresh`", text)
        self.assertIn("current context to the selected `--context`", text)
        self.assertIn("namespace to `argocd`", text)
        self.assertNotIn("`argocd --core app diff kyverno --refresh`", text)

    def test_runbook_declares_transition_only_non_acceptance_boundary(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        for phrase in ("retained-convergence", "convergence-only", "not acceptance",
                       "does not start", "31-sample", "30-minute",
                       "separate acceptance eligibility decision"):
            self.assertIn(phrase, text)
        self.assertNotIn("--current-primary-index", text)

    def test_runbook_binds_graph_exception_operations_and_rollback(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        for phrase in (NEW_TAG, PREVIOUS_TAG, OLD_TAG, "5 candidate / 27 old / 0 previous / 0 other",
                       "11", "CustomResourceDefinition", "zero bytes", "Root fresh",
                       "Kyverno fresh", "Fluentd fresh", "Argo CD", "OpenSearch", "three consecutive",
                       "no automatic rollback", "fluentd-log-schema", "mode `0600`"):
            self.assertIn(phrase, text)

    def test_runbook_keeps_publication_and_cluster_authority_external(self):
        text = RUNBOOK.read_text(encoding="utf-8")
        for phrase in ("does not authorize publication", "does not authorize rollout",
                       "annotated tag-object", "peeled commit", "tree SHA",
                       "separate post-publication launcher"):
            self.assertIn(phrase, text)


class SecurityTest(Harness):
    def test_all_runtime_tags_must_remotely_peel_to_exact_commits(self):
        for attribute in ("previous_remote_commit", "old_remote_commit"):
            with self.subTest(attribute=attribute):
                if self.evidence.exists():
                    self.evidence.unlink()
                fake = StatefulFakeKubectl()
                setattr(fake, attribute, "f" * 40)
                with self.assertRaisesRegex(transition.TransitionError, "remote tag"):
                    self.execute(fake)
                self.assertFalse(any(argv[0] == "kubectl" for argv, _ in fake.commands))

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
        self.assertIn(["git", "ls-tree", "--name-only", "HEAD", "--", "scripts/issue348_runtime_v2_transition.py"], git_commands)
        self.assertIn(["git", "ls-tree", "--name-only", "HEAD", "--", "specs/345-log-schema-isolation/runtime-v2-transition.md"], git_commands)

    def test_dirty_checkout_fails_before_kubernetes(self):
        class BadGit(StatefulFakeKubectl):
            def run(self, argv, timeout, env=None):
                if argv[0] == "git" and argv[1:3] == ["status", "--porcelain=v1"]:
                    self.commands.append((list(argv), timeout))
                    return self._result(stdout=" M changed\n")
                return super().run(argv, timeout, env=env)
        fake = BadGit()
        with self.assertRaisesRegex(transition.TransitionError, "checkout"):
            self.execute(fake)
        self.assertFalse(any(argv[0] == "kubectl" for argv, _ in fake.commands))

    def test_wrong_checkout_head_fails_before_kubernetes(self):
        class WrongHead(StatefulFakeKubectl):
            def run(self, argv, timeout, env=None):
                if argv == ["git", "rev-parse", "HEAD"]:
                    self.commands.append((list(argv), timeout))
                    return self._result(stdout="f" * 40 + "\n")
                return super().run(argv, timeout, env=env)
        fake = WrongHead()
        with self.assertRaisesRegex(transition.TransitionError, "HEAD"):
            self.execute(fake)
        self.assertFalse(any(argv[0] == "kubectl" for argv, _ in fake.commands))

    def test_wrong_runtime_parent_fails_before_kubernetes(self):
        class WrongParent(StatefulFakeKubectl):
            def run(self, argv, timeout, env=None):
                if argv == ["git", "rev-parse", "HEAD^"]:
                    self.commands.append((list(argv), timeout))
                    return self._result(stdout="0" * 40 + "\n")
                return super().run(argv, timeout, env=env)

        fake = WrongParent()
        with self.assertRaisesRegex(transition.TransitionError, "authorized Issue #348 v5 base"):
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
                with self.assertRaisesRegex(transition.TransitionError, "fixed Issue #348"):
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
        self.assertEqual(by_event["control-plane-closed"]["source_counts"],
                         {"candidate": 5, "old": 27, "other": 0, "previous": 0})
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
    def test_kyverno_diff_uses_v345_supported_argv_and_isolated_namespaced_context(self):
        inherited_default = str(Path.home() / ".kube" / "config")
        with mock.patch.dict(os.environ, {"KUBECONFIG": inherited_default}):
            fake = self.execute()
        argocd_commands = [argv for argv, _ in fake.commands if argv[0] == "argocd"]
        self.assertEqual(
            argocd_commands,
            [["argocd", "app", "diff", "kyverno", "--core", "--refresh"]],
        )
        self.assertEqual(fake.argocd_kubeconfig_snapshots, ["safe"])
        self.assertEqual(len(fake.argocd_environments), 1)
        isolated = fake.argocd_environments[0]["KUBECONFIG"]
        self.assertNotEqual(isolated, str(self.kubeconfig))
        self.assertNotEqual(isolated, inherited_default)
        self.assertFalse(Path(isolated).exists(), "isolated kubeconfig was not removed")
        self.assertEqual(self.kubeconfig.read_text(encoding="utf-8"), "safe")

        config_commands = [argv for argv, _ in fake.commands if "config" in argv]
        self.assertIn(
            [
                "kubectl", "--kubeconfig", isolated,
                "--request-timeout=20s", "config", "use-context", "retained",
            ],
            config_commands,
        )
        self.assertIn(
            [
                "kubectl", "--kubeconfig", isolated,
                "--request-timeout=20s", "config", "set-context", "--current",
                "--namespace=argocd",
            ],
            config_commands,
        )

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
        self.assertEqual(transition.target(fake.apps["root-app"]), PREVIOUS_TAG)
        self.assertEqual(transition.target(fake.apps["argocd"]), PREVIOUS_TAG)

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
        self.assertEqual(fake.apps["root-app"]["spec"]["source"]["targetRevision"], PREVIOUS_TAG)

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
                          if kind == "applications.argoproj.io" and name == "root-app" and payload[-1]["value"] == PREVIOUS_TAG]
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
        self.assertIn(("root-app", PREVIOUS_TAG), mutations)
        self.assertEqual(mutations[-1], ("argocd-application-controller", 1))
        self.assertEqual(fake.apps["root-app"]["spec"]["source"]["targetRevision"], PREVIOUS_TAG)

    def test_rollback_attempts_both_owners_before_controller_restore(self):
        fake = StatefulFakeKubectl()
        fake.fail_second_app_patch = True
        fake.fail_second_app_patch_after_apply = True
        fake.fail_root_rollback = True
        with self.assertRaisesRegex(transition.TransitionError, "root rollback patch failed"):
            self.execute(fake)
        self.assertEqual(transition.target(fake.apps["argocd"]), PREVIOUS_TAG)
        self.assertEqual(fake.controller["spec"]["replicas"], 1)

    def test_concurrent_spec_mutation_triggers_rollback(self):
        fake = StatefulFakeKubectl()
        fake.concurrent_spec_mutation = True
        with self.assertRaisesRegex(transition.TransitionError, "spec changed"):
            self.execute(fake)
        self.assertEqual(fake.apps["root-app"]["spec"]["source"]["targetRevision"], PREVIOUS_TAG)
        self.assertEqual(fake.apps["argocd"]["spec"]["source"]["targetRevision"], PREVIOUS_TAG)

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
        self.assertEqual(transition.target(fake.apps["root-app"]), PREVIOUS_TAG)

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

    def test_kyverno_preflight_accepts_full_69_resource_inventory(self):
        fake = StatefulFakeKubectl()
        fake.apps["kyverno"]["status"]["resources"] = (
            kyverno_full_resource_inventory(fake.apps["kyverno"])
        )
        self.assertEqual(len(fake.apps["kyverno"]["status"]["resources"]), 69)

        self.execute(fake)

        self.assertTrue(fake.patch_payloads)

    def test_duplicate_reviewed_out_of_sync_resource_fails_closed(self):
        fake = StatefulFakeKubectl()
        resources = kyverno_full_resource_inventory(fake.apps["kyverno"])
        reviewed = next(item for item in resources if item["status"] == "OutOfSync")
        resources.append(deepcopy(reviewed))
        fake.apps["kyverno"]["status"]["resources"] = resources
        self.assertEqual(sum(item["status"] == "OutOfSync" for item in resources), 12)

        with self.assertRaisesRegex(
                transition.TransitionError, "^Kyverno resource inventory has duplicate identity$"):
            self.execute(fake)

        self.assertFalse(fake.patch_payloads)

    def test_extra_unreviewed_out_of_sync_resources_fail_closed(self):
        unexpected = (
            {"group": "apps", "version": "v1", "kind": "Deployment",
             "namespace": "kyverno", "name": "unexpected", "status": "OutOfSync"},
            {"group": "apiextensions.k8s.io", "version": "v1",
             "kind": "CustomResourceDefinition", "namespace": None,
             "name": "unexpected.example.io", "status": "OutOfSync"},
        )
        for resource in unexpected:
            with self.subTest(resource=resource):
                if self.evidence.exists():
                    self.evidence.unlink()
                fake = StatefulFakeKubectl()
                resources = kyverno_full_resource_inventory(fake.apps["kyverno"])
                resources.append(resource)
                fake.apps["kyverno"]["status"]["resources"] = resources

                with self.assertRaises(transition.TransitionError):
                    self.execute(fake)

                self.assertFalse(fake.patch_payloads)

    def test_unhashable_kyverno_resource_status_fails_with_transition_error(self):
        statuses = ([], {}, ["Synced"], {"a": 1})
        for status in statuses:
            for existing_status in ("OutOfSync", "Synced"):
                with self.subTest(status=status, existing_status=existing_status):
                    fake = StatefulFakeKubectl()
                    resources = kyverno_full_resource_inventory(fake.apps["kyverno"])
                    resource = next(
                        item for item in resources if item["status"] == existing_status
                    )
                    resource["status"] = status
                    fake.apps["kyverno"]["status"]["resources"] = resources

                    with self.assertRaisesRegex(
                            transition.TransitionError,
                            "^Kyverno resource identity/status mismatch$"):
                        transition.Protocol.require_preflight_status(fake.apps)

                    self.assertFalse(fake.patch_payloads)

    def test_malformed_synced_kyverno_resource_fails_closed(self):
        fake = StatefulFakeKubectl()
        fake.apps["kyverno"]["status"]["resources"].append({"status": "Synced"})

        with self.assertRaisesRegex(
                transition.TransitionError,
                "^Kyverno resource identity/status mismatch$"):
            self.execute(fake)

        self.assertFalse(fake.patch_payloads)

    def test_exact_kyverno_preflight_exception_is_required(self):
        def first_out_of_sync(application):
            return next(item for item in application["status"]["resources"]
                        if item["status"] == "OutOfSync")

        cases = (
            lambda app: app["status"]["sync"].update({"status": "Synced"}),
            lambda app: app["status"]["resources"].pop(),
            lambda app: first_out_of_sync(app).update({"status": "Synced"}),
            lambda app: first_out_of_sync(app).update({"kind": "Deployment"}),
        )
        for mutate in cases:
            with self.subTest(mutate=mutate):
                if self.evidence.exists():
                    self.evidence.unlink()
                fake = StatefulFakeKubectl()
                mutate(fake.apps["kyverno"])
                with self.assertRaises(transition.TransitionError):
                    self.execute(fake)
                self.assertFalse(fake.patch_payloads)

    def test_other_non_synced_application_is_rejected_before_mutation(self):
        fake = StatefulFakeKubectl()
        fake.apps["backstage"]["status"]["sync"]["status"] = "OutOfSync"
        with self.assertRaisesRegex(transition.TransitionError, "unexpected non-synced"):
            self.execute(fake)
        self.assertFalse(fake.patch_payloads)

    def test_nonempty_kyverno_material_diff_is_rejected_before_mutation(self):
        fake = StatefulFakeKubectl()
        fake.kyverno_diff_output = "material-diff\n"
        with self.assertRaisesRegex(transition.TransitionError, "zero bytes"):
            self.execute(fake)
        self.assertFalse(fake.patch_payloads)

    def test_kyverno_material_diff_timeout_is_fail_closed_before_mutation(self):
        fake = StatefulFakeKubectl()
        fake.kyverno_diff_timeout = True
        with self.assertRaisesRegex(transition.TransitionError, "timed out"):
            self.execute(fake)
        self.assertFalse(fake.patch_payloads)

    def test_kyverno_diff_success_with_stderr_is_not_empty_output(self):
        fake = StatefulFakeKubectl()
        fake.kyverno_diff_stderr = "warning\n"
        with self.assertRaisesRegex(transition.TransitionError, "zero bytes"):
            self.execute(fake)
        self.assertFalse(fake.patch_payloads)

    def test_typed_application_list_identity_is_fail_closed(self):
        fake = StatefulFakeKubectl()
        fake.application_list_kind = "List"
        with self.assertRaisesRegex(transition.TransitionError, "list shape"):
            self.execute(fake)
        self.assertFalse(fake.patch_payloads)

    def test_kyverno_annotation_is_the_only_helm_values_change_and_operation_is_fresh(self):
        baseline = make_apps()["kyverno"]["spec"]
        fake = self.execute()
        final = fake.apps["kyverno"]["spec"]
        expected = deepcopy(baseline)
        expected["source"]["helm"]["values"] = transition.kyverno_candidate_values(
            expected["source"]["helm"]["values"]
        )
        self.assertEqual(final, expected)
        parsed = yaml.safe_load(final["source"]["helm"]["values"])
        self.assertEqual(parsed["kyverno-api"]["annotations"], transition.KYVERNO_ANNOTATION)
        self.assertNotEqual(transition.operation_identity(fake.apps["kyverno"]),
                            transition.operation_identity(make_apps()["kyverno"]))

    def test_stale_kyverno_operation_never_converges(self):
        fake = StatefulFakeKubectl()
        fake.kyverno_stale_operation = True
        with self.assertRaisesRegex(transition.TransitionError, "convergence deadline"):
            self.execute(fake)
        self.assertEqual(transition.target(fake.apps["root-app"]), NEW_TAG)

    def test_fluentd_requires_fresh_successful_presync_operation(self):
        fake = StatefulFakeKubectl()

        def stale_operation(application):
            application["status"]["operationState"] = operation(
                "2026-09-02T05:02:30Z", [NEW_COMMIT]
            )

        setattr(fake, "final_app_mutation", ("fluentd", stale_operation))
        with self.assertRaisesRegex(transition.TransitionError, "convergence deadline"):
            self.execute(fake)
        self.assertEqual(transition.target(fake.apps["fluentd"]), NEW_TAG)

    def test_fluentd_requires_exact_candidate_status_revision(self):
        fake = StatefulFakeKubectl()
        setattr(fake, "final_app_mutation", (
            "fluentd",
            lambda application: application["status"]["sync"].update(
                {"revision": OLD_COMMIT}
            ),
        ))
        with self.assertRaisesRegex(transition.TransitionError, "convergence deadline"):
            self.execute(fake)

    def test_success_requires_three_stable_convergence_samples_and_is_not_acceptance(self):
        fake = self.execute()
        records = [json.loads(line) for line in self.evidence.read_text().splitlines()]
        closed = [item for item in records if item["event"] == "control-plane-closed"]
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]["stable_samples"], 3)
        self.assertEqual(closed[0]["mode"], "convergence-only-not-acceptance")
        self.assertNotIn("accepted", self.evidence.read_text().lower())
        self.assertGreaterEqual(fake.application_inventory_reads, 6)
        core_targets = [source["targetRevision"] for app in fake.apps.values()
                        for source in transition.source_list(app)
                        if source and source.get("repoURL") == CORE]
        self.assertEqual(core_targets.count(NEW_TAG), 5)
        self.assertEqual(core_targets.count(OLD_TAG), 27)
        self.assertNotIn(PREVIOUS_TAG, core_targets)

    def test_any_sibling_source_identity_drift_is_rejected_preflight(self):
        fake = StatefulFakeKubectl()
        fake.apps["backstage"]["spec"]["source"]["path"] = "platform/base/lookalike"
        with self.assertRaisesRegex(transition.TransitionError, "source graph"):
            self.execute(fake)
        self.assertFalse(fake.patch_payloads)

    def test_preflight_requires_exact_retained_status_revisions(self):
        mutations = (
            lambda apps: apps["root-app"]["status"]["sync"].update(
                {"revision": "f" * 40}
            ),
            lambda apps: apps["argocd"]["status"]["sync"].update(
                {"revision": "f" * 40}
            ),
            lambda apps: apps["opensearch"]["status"]["sync"].update(
                {"revisions": ["3.7.0", "f" * 40, OLD_COMMIT]}
            ),
            lambda apps: apps["fluentd"]["status"]["sync"].update(
                {"revision": "f" * 40}
            ),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                if self.evidence.exists():
                    self.evidence.unlink()
                fake = StatefulFakeKubectl()
                mutation(fake.apps)
                with self.assertRaisesRegex(
                        transition.TransitionError, "retained status revisions"):
                    self.execute(fake)
                self.assertFalse(fake.patch_payloads)

    def test_fresh_kyverno_operation_requires_valid_finished_timestamp(self):
        fake = StatefulFakeKubectl()
        fake.kyverno_invalid_finished = True
        with self.assertRaisesRegex(transition.TransitionError, "convergence deadline"):
            self.execute(fake)

    def test_three_samples_require_stable_literal_status_projection(self):
        fake = StatefulFakeKubectl()
        fake.unstable_final_reconciled = True
        with self.assertRaisesRegex(transition.TransitionError, "convergence deadline"):
            self.execute(fake)


if __name__ == "__main__":
    unittest.main(verbosity=2)
