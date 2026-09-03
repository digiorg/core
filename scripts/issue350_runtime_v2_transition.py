#!/usr/bin/env python3
"""Deterministic Issue #350 runtime-v2 control-plane transition.

Only three Kubernetes objects can be mutated: the Argo CD application
controller StatefulSet and Application/root-app plus Application/argocd.  All
I/O dependencies are injectable; the behavioral suite uses a stateful fake and
never contacts Git or Kubernetes.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import NamedTuple

BARRIER_SECONDS = 300
ROLLBACK_SECONDS = 300
CONVERGENCE_SECONDS = 1200
RECOVERY_SECONDS = 900
OBSERVATION_SECONDS = 1800
OBSERVATION_SAMPLES = 31
CALL_SECONDS = 20
WAIT_SECONDS = 120
POLL_SECONDS = 5
ARGOCD_NAMESPACE = "argocd"
FLUENTD_NAMESPACE = "logging"
CONTROLLER_LABEL = "app.kubernetes.io/name=argocd-application-controller"
CONTROLLER_NAME = "argocd-application-controller"
CORE_REPO = "https://github.com/digiorg/core.git"
RUNTIME_TAG = "issue350-runtime-v2-20260902T063718Z"
OLD_TAG = "issue301-runtime-v16-20260817T130820Z"
OLD_COMMIT = "8e6b8908f99ebf76db47c15613eff523644c23f6"
CATALOG_REVISION = "d531180b322dc0128477ecb9bb0fc9071b41d631"
APP_CONFIG_REPO = "https://digiorg.local/gitea/DigiOrg/app-config.git"
CATALOG_REPO = "https://github.com/digiorg/core-catalog.git"
OPENSEARCH_IMAGE = "opensearchproject/opensearch:3.7.0@sha256:44ba7ea58a319adf61c33ab16873f9ef5dbb30b291a832d375172f0b2d24e3c9"
ACTIVE_PHASES = {"Running", "Terminating"}
EXPECTED_APPLICATIONS = (
    "app-config", "argocd", "backstage", "cert-manager", "cnpg", "cnpg-cluster",
    "core-catalog", "crossplane", "crossplane-harbor-bootstrap",
    "crossplane-provider-configs", "crossplane-providers", "crossplane-xrds",
    "external-secrets", "fluentd", "gitea", "gitea-actions-runner", "grafana",
    "harbor", "jaeger", "keycloak", "kyverno", "kyverno-policies", "landingpage",
    "monitoring-extras", "namespaces", "nats", "nats-jetstream-controller",
    "opencost", "opensearch", "postgresql", "root-app", "sonarqube",
)
# Exact (repoURL, chart, path, ref, targetRevision) closure rendered by the
# immutable mixed tag.  This is deliberately data, not a permissive classifier.
CLEAN_SOURCE_GRAPH = {
    "app-config": [(APP_CONFIG_REPO, None, "claims", None, "main")],
    "argocd": [(CORE_REPO, None, "platform/base/argocd", None, RUNTIME_TAG)],
    "backstage": [(CORE_REPO, None, "platform/base/backstage", None, OLD_TAG)],
    "cert-manager": [(CORE_REPO, None, "platform/base/cert-manager", None, OLD_TAG)],
    "cnpg": [("https://cloudnative-pg.github.io/charts", "cloudnative-pg", None, None, "0.29.0")],
    "cnpg-cluster": [(CORE_REPO, None, "platform/base/cnpg", None, OLD_TAG)],
    "core-catalog": [(CATALOG_REPO, None, "compositions/local", None, CATALOG_REVISION)],
    "crossplane": [("https://charts.crossplane.io/stable", "crossplane", None, None, "2.3.3")],
    "crossplane-harbor-bootstrap": [(CORE_REPO, None, "crossplane/bootstrap", None, OLD_TAG)],
    "crossplane-provider-configs": [(CORE_REPO, None, "crossplane/providers/configs", None, OLD_TAG)],
    "crossplane-providers": [(CORE_REPO, None, "crossplane/providers/packages", None, OLD_TAG)],
    "crossplane-xrds": [(CORE_REPO, None, "crossplane/xrds", None, OLD_TAG)],
    "external-secrets": [("https://charts.external-secrets.io", "external-secrets", None, None, "2.7.0"), (CORE_REPO, None, None, "values", OLD_TAG)],
    "fluentd": [(CORE_REPO, None, "platform/base/fluentd", None, OLD_TAG)],
    "gitea": [("https://dl.gitea.com/charts/", "gitea", None, None, "12.6.0"), (CORE_REPO, None, None, "values", OLD_TAG)],
    "gitea-actions-runner": [(CORE_REPO, None, "platform/base/gitea-actions-runner", None, OLD_TAG)],
    "grafana": [("https://prometheus-community.github.io/helm-charts", "kube-prometheus-stack", None, None, "87.17.0"), (CORE_REPO, None, None, "values", OLD_TAG)],
    "harbor": [("https://helm.goharbor.io", "harbor", None, None, "1.19.1"), (CORE_REPO, None, None, "values", OLD_TAG), (CORE_REPO, None, "platform/base/harbor", None, OLD_TAG)],
    "jaeger": [("https://jaegertracing.github.io/helm-charts", "jaeger", None, None, "4.11.1"), (CORE_REPO, None, None, "values", OLD_TAG), (CORE_REPO, None, "platform/base/jaeger", None, OLD_TAG)],
    "keycloak": [(CORE_REPO, None, "platform/base/keycloak", None, OLD_TAG)],
    "kyverno": [("https://kyverno.github.io/kyverno/", "kyverno", None, None, "3.8.1")],
    "kyverno-policies": [(CORE_REPO, None, "policies/kyverno", None, OLD_TAG)],
    "landingpage": [(CORE_REPO, None, "platform/base/landingpage", None, OLD_TAG)],
    "monitoring-extras": [(CORE_REPO, None, "platform/base/monitoring-extras", None, OLD_TAG)],
    "namespaces": [(CORE_REPO, None, "platform/base/namespaces", None, OLD_TAG)],
    "nats": [("https://nats-io.github.io/k8s/helm/charts", "nats", None, None, "2.14.2"), (CORE_REPO, None, None, "values", OLD_TAG), (CORE_REPO, None, "platform/base/nats", None, OLD_TAG)],
    "nats-jetstream-controller": [("https://nats-io.github.io/k8s/helm/charts", "nack", None, None, "0.34.0")],
    "opencost": [("https://opencost.github.io/opencost-helm-chart", "opencost", None, None, "2.5.27"), (CORE_REPO, None, None, "values", OLD_TAG), (CORE_REPO, None, "platform/base/opencost", None, OLD_TAG)],
    "opensearch": [("https://opensearch-project.github.io/helm-charts", "opensearch", None, None, "3.7.0"), (CORE_REPO, None, None, "values", RUNTIME_TAG), (CORE_REPO, None, "platform/base/opensearch", None, OLD_TAG)],
    "postgresql": [(CORE_REPO, None, "platform/base/postgresql", None, OLD_TAG)],
    "root-app": [(CORE_REPO, None, "apps", None, RUNTIME_TAG)],
    "sonarqube": [("https://SonarSource.github.io/helm-chart-sonarqube", "sonarqube", None, None, "2026.3.1"), (CORE_REPO, None, None, "values", OLD_TAG), (CORE_REPO, None, "platform/base/sonarqube", None, OLD_TAG)],
}
TRACKED_RUNTIME_FILES = (
    "scripts/issue350_runtime_v2_transition.py",
    "specs/350-opensearch-memory-headroom/runtime-v2-transition.md",
)
OS_NAMESPACE = "platform-db"
OS_STS = "opensearch-cluster-master"
OS_POD = "opensearch-cluster-master-0"
OS_CONTAINER = "opensearch"
OS_PVC = "opensearch-cluster-master-opensearch-cluster-master-0"
FLUENTD_DS = "fluentd"
EVIDENCE_FIELDS = {
    "event", "time", "name", "uid", "resource_version", "replicas",
    "pod_uids", "pod_uid_hashes", "spec_hash", "operation_id", "revision", "result", "error",
    "elapsed_seconds", "deadline_seconds", "application_count", "controller_spec_hash",
    "application_spec_hash", "targets", "operation_hashes", "revisions", "source_counts",
    "schema_job_absence", "current_revision", "update_revision",
    "statefulset_uid", "pvc_uid", "volume_name", "capacity", "index_uid_hashes",
    "pvc_spec_hash",
    "pod_restart_count", "memory_current", "memory_max", "memory_events_max",
    "last_termination_reason", "last_termination_time",
    "sample", "queue_lengths", "log_count", "jaeger_count", "template_hash",
}


class TransitionError(RuntimeError):
    """Fail-closed protocol error containing only redacted diagnostics."""


class CommandResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


class Config:
    def __init__(self, *, kubeconfig, context, expected_server,
                 expected_kube_system_uid, remote_url, runtime_tag,
                 runtime_commit, old_tag, old_commit, evidence,
                 current_primary_index=None, representative_log_index=None,
                 representative_jaeger_index=None, collect_retained=True,
                 mode="retained-transition"):
        self.mode = mode
        self.kubeconfig = Path(kubeconfig)
        self.context = context
        self.expected_server = expected_server
        self.expected_kube_system_uid = expected_kube_system_uid
        self.remote_url = remote_url
        self.runtime_tag = runtime_tag
        self.runtime_commit = runtime_commit
        self.old_tag = old_tag
        self.old_commit = old_commit
        self.evidence = Path(evidence)
        self.current_primary_index = current_primary_index
        self.representative_log_index = representative_log_index
        self.representative_jaeger_index = representative_jaeger_index
        self.collect_retained = collect_retained


class RealRunner:
    def run(self, argv, timeout):
        try:
            completed = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            raise TimeoutError("command exceeded outer timeout") from error
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)


class RealClock:
    monotonic = staticmethod(time.monotonic)
    sleep = staticmethod(time.sleep)
    time = staticmethod(time.time)


class Deadline:
    def __init__(self, clock, seconds, label):
        self.clock = clock
        self.end = clock.monotonic() + seconds
        self.label = label

    def remaining(self):
        return self.end - self.clock.monotonic()

    def call_timeout(self):
        remaining = self.remaining()
        if remaining <= 0:
            raise TransitionError(f"{self.label} deadline exceeded")
        return min(float(CALL_SECONDS), remaining)

    def sleep(self, seconds=POLL_SECONDS):
        remaining = self.remaining()
        if remaining <= 0:
            raise TransitionError(f"{self.label} deadline exceeded")
        self.clock.sleep(min(seconds, remaining))

    def child(self, seconds, label):
        child = Deadline(self.clock, seconds, label)
        child.end = min(child.end, self.end)
        return child


class Evidence:
    def __init__(self, path, clock):
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as error:
            raise TransitionError("evidence path must not exist") from error
        actual = stat.S_IMODE(os.fstat(fd).st_mode)
        if actual != 0o600:
            os.close(fd)
            raise TransitionError("evidence file mode is not 0600")
        self.file = os.fdopen(fd, "w", encoding="utf-8")
        self.clock = clock
        self.started = clock.monotonic()

    def write(self, event, **fields):
        record = {
            "event": event,
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "elapsed_seconds": self.clock.monotonic() - self.started,
        }
        for key, value in fields.items():
            if key not in EVIDENCE_FIELDS:
                raise TransitionError(f"evidence field is not allowlisted: {key}")
            record[key] = redact(value) if key == "error" else value
        self.file.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        self.file.flush()
        os.fsync(self.file.fileno())

    def close(self):
        self.file.close()


_AUTH_RE = re.compile(r"(?i)\b(Bearer|Basic)\s+[^\s,;]+")
_URL_USERINFO_RE = re.compile(r"(?i)(https?://)[^/@\s]+@")
_QUOTED_SECRET_RE = re.compile(
    r'''(?ix)(["']?(?:client[_-]?secret|secret|token|password|passwd|api[_-]?key)["']?\s*[:=]\s*)(["'][^"']*["']|[^\s,}\]]+)'''
)
_GENERIC_SECRET_RE = re.compile(
    r"(?i)\b(secret|token|password|passwd|api[_-]?key)\s*=\s*[^\s,;]+"
)
_RFC3339_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})"
)


def redact(value):
    text = str(value)
    text = _AUTH_RE.sub(lambda match: f"{match.group(1)} [REDACTED]", text)
    text = _URL_USERINFO_RE.sub(r"\1[REDACTED]@", text)
    text = _QUOTED_SECRET_RE.sub(lambda match: match.group(1) + '"[REDACTED]"', text)
    text = _GENERIC_SECRET_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    return text[:1000]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def rfc3339(value):
    if not isinstance(value, str) or not _RFC3339_RE.fullmatch(value):
        raise ValueError("invalid RFC3339 timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no timezone")
    return parsed


def timestamp_later(candidate, baseline):
    try:
        return rfc3339(candidate) > rfc3339(baseline)
    except (TypeError, ValueError):
        return False


def controller_non_replica_spec(controller):
    value = deepcopy_json(controller.get("spec", {}))
    value.pop("replicas", None)
    return value


def json_pointer(document, pointer):
    current = document
    for component in pointer.lstrip("/").split("/"):
        key = component.replace("~1", "/").replace("~0", "~")
        current = current[int(key)] if isinstance(current, list) else current[key]
    return current


def json_pointer_replace(document, pointer, value):
    parts = pointer.lstrip("/").split("/")
    current = document
    for component in parts[:-1]:
        key = component.replace("~1", "/").replace("~0", "~")
        current = current[int(key)] if isinstance(current, list) else current[key]
    key = parts[-1].replace("~1", "/").replace("~0", "~")
    if isinstance(current, list):
        current[int(key)] = value
    else:
        current[key] = value


def revision_list(sync):
    revisions = sync.get("revisions")
    if revisions is not None:
        return revisions
    revision = sync.get("revision")
    return [] if revision is None else [revision]


def operation_identity(application):
    state = application.get("status", {}).get("operationState")
    if not state:
        return None
    requested = revision_list(state.get("operation", {}).get("sync", {}))
    result = revision_list(state.get("syncResult", {}))
    return {"startedAt": state.get("startedAt"), "requestedRevisions": requested,
            "resultRevisions": result}


def valid_revision_list(value):
    return (isinstance(value, list) and bool(value) and
            all(isinstance(item, str) and bool(item) for item in value))


def source_list(application):
    spec = application["spec"]
    return spec.get("sources", [spec.get("source")])


def target(application, index=0):
    return source_list(application)[index]["targetRevision"]


def source_identity(item):
    return tuple(item.get(key) for key in ("repoURL", "chart", "path", "ref", "targetRevision"))


def status_revisions(application):
    sync = application.get("status", {}).get("sync", {})
    values = sync.get("revisions")
    return values if values is not None else [sync.get("revision")]


def resolved_commit(application):
    sync = application.get("status", {}).get("sync", {})
    has_single = "revision" in sync
    has_multiple = "revisions" in sync
    if has_single == has_multiple:
        raise TransitionError("app-config resolved revision must have exactly one representation")
    values = sync.get("revisions") if has_multiple else [sync.get("revision")]
    if (not isinstance(values, list) or len(values) != 1 or
            not isinstance(values[0], str) or not re.fullmatch(r"[0-9a-f]{40}", values[0])):
        raise TransitionError("app-config resolved revision must be exactly one lowercase 40-hex commit")
    return values[0]


def is_ready_pod(pod, owner_uid):
    owner = any(
        item.get("uid") == owner_uid and item.get("controller") is True
        for item in pod.get("metadata", {}).get("ownerReferences", [])
    )
    ready = any(
        item.get("type") == "Ready" and item.get("status") == "True"
        for item in pod.get("status", {}).get("conditions", [])
    )
    return owner and ready and pod.get("status", {}).get("phase") == "Running"


def exact_json(text, expected_type):
    try:
        value = json.loads(text)
    except (TypeError, json.JSONDecodeError) as error:
        raise TransitionError("retained API returned invalid JSON") from error
    if not isinstance(value, expected_type):
        raise TransitionError("retained API returned wrong JSON shape")
    return value


def named_container(pod_or_template, name):
    containers = pod_or_template.get("spec", {}).get("containers", [])
    matches = [item for item in containers if item.get("name") == name]
    if len(matches) != 1:
        raise TransitionError(f"expected exactly one {name} container")
    return matches[0]


class RetainedCollector:
    """Read-only retained-state acceptance, with no body persisted to evidence."""

    def __init__(self, client, config, clock, evidence):
        self.client = client
        self.config = config
        self.clock = clock
        self.evidence = evidence
        self.baseline_data = None
        self.candidate = None

    def get(self, resource, name, deadline, namespace):
        return self.client.get_json(["get", resource, name], deadline, namespace)

    def http(self, path, deadline):
        output = self.client.kubectl(
            ["exec", OS_POD, "-c", OS_CONTAINER, "--", "curl", "--fail", "--silent",
             "--show-error", "--max-time", "15", f"http://127.0.0.1:9200{path}"],
            deadline, OS_NAMESPACE)
        return exact_json(output, (dict, list))

    @staticmethod
    def pod_lifecycle(pod):
        if pod.get("metadata", {}).get("name") != OS_POD:
            raise TransitionError("OpenSearch Pod name is not exact")
        container = named_container(pod, OS_CONTAINER)
        statuses = [item for item in pod.get("status", {}).get("containerStatuses", [])
                    if item.get("name") == OS_CONTAINER]
        if len(statuses) != 1 or not isinstance(statuses[0].get("restartCount"), int):
            raise TransitionError("OpenSearch lifecycle status is incomplete")
        uid = pod.get("metadata", {}).get("uid")
        if not isinstance(uid, str) or not uid:
            raise TransitionError("OpenSearch Pod UID is missing")
        last = statuses[0].get("lastState", {}).get("terminated")
        if last is not None:
            reason, finished = last.get("reason"), last.get("finishedAt")
            if (not isinstance(reason, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,63}", reason)):
                raise TransitionError("OpenSearch last termination reason is malformed")
            try:
                rfc3339(finished)
            except (TypeError, ValueError) as error:
                raise TransitionError("OpenSearch last termination time is malformed") from error
            last = {"reason": reason, "finishedAt": finished}
        return {"uid": uid, "restart": statuses[0]["restartCount"],
                "last": last,
                "container": container}

    def cgroup(self, deadline):
        output = self.client.kubectl(
            ["exec", OS_POD, "-c", OS_CONTAINER, "--", "sh", "-c",
             "printf 'current='; cat /sys/fs/cgroup/memory.current; printf 'max='; cat /sys/fs/cgroup/memory.max; grep '^max ' /sys/fs/cgroup/memory.events"],
            deadline, OS_NAMESPACE)
        match = re.fullmatch(r"current=(\d+)\nmax=(\d+)\nmax (\d+)\n?", output)
        if not match:
            raise TransitionError("cgroup scalar output is malformed")
        current, maximum, events = map(int, match.groups())
        return current, maximum, events

    @staticmethod
    def pvc_identity(pvc):
        metadata, spec, status = pvc.get("metadata", {}), pvc.get("spec", {}), pvc.get("status", {})
        if metadata.get("name") != OS_PVC or status.get("phase") != "Bound":
            raise TransitionError("exact OpenSearch PVC is absent or not Bound")
        capacity = status.get("capacity", {}).get("storage")
        values = (metadata.get("uid"), spec.get("volumeName"), capacity, digest(spec))
        if not all(isinstance(value, str) and value for value in values):
            raise TransitionError("OpenSearch PVC identity is incomplete")
        return values

    @staticmethod
    def index_inventory(value, *, allow_empty=False):
        if not isinstance(value, list):
            raise TransitionError("index inventory is not an array")
        result = {}
        for item in value:
            if not isinstance(item, dict) or set(item) != {"index", "uuid", "status", "creation.date"}:
                raise TransitionError("index inventory fields are not exact")
            name, uid, status, created = (item["index"], item["uuid"], item["status"], item["creation.date"])
            if (not all(isinstance(v, str) and v for v in (name, uid, status, created)) or
                    name in result or status != "open" or not created.isdigit()):
                raise TransitionError("index inventory identity is malformed or ambiguous")
            result[name] = {"uid": uid, "created_ms": int(created)}
        if not result and not allow_empty:
            raise TransitionError("index inventory is empty")
        return result

    def validate_policy(self, policy, explain, indices):
        expected_policy_id = "digiorg-logs-retention-7d"
        expected_description = "Delete DigiOrg Fluentd log indices older than 7 days"
        expected_template = [{"index_patterns": ["digiorg-logs-*"], "priority": 100}]
        expected_states = [
            {"name": "hot", "actions": [], "transitions": [
                {"state_name": "delete", "conditions": {"min_index_age": "7d"}},
            ]},
            {"name": "delete", "actions": [{"delete": {}}], "transitions": []},
        ]

        try:
            if (not isinstance(policy, dict) or
                    set(policy) != {"_id", "_version", "_seq_no", "_primary_term", "policy"} or
                    policy["_id"] != expected_policy_id):
                raise KeyError
            revision_fields = (policy["_version"], policy["_seq_no"], policy["_primary_term"])
            if (any(not isinstance(value, int) or isinstance(value, bool) for value in revision_fields) or
                    policy["_version"] < 1 or policy["_seq_no"] < 0 or policy["_primary_term"] < 1):
                raise KeyError
            entry = policy["policy"]
            if (not isinstance(entry, dict) or
                    set(entry) != {"policy_id", "description", "last_updated_time",
                                   "schema_version", "error_notification", "default_state",
                                   "states", "ism_template"} or
                    not isinstance(explain, dict)):
                raise KeyError
            api_metadata = (entry["last_updated_time"], entry["schema_version"])
            if (any(not isinstance(value, int) or isinstance(value, bool) for value in api_metadata) or
                    entry["last_updated_time"] < 0 or entry["schema_version"] < 1):
                raise KeyError
            reviewed_policy = dict(entry)
            reviewed_policy.pop("last_updated_time")
            reviewed_policy.pop("schema_version")
            if reviewed_policy != {
                "policy_id": expected_policy_id,
                "description": expected_description,
                "error_notification": None,
                "default_state": "hot",
                "states": expected_states,
                "ism_template": expected_template,
            }:
                raise KeyError

            bindings = {}
            for name, identity in indices.items():
                item = explain.get(name)
                if not isinstance(item, dict):
                    raise KeyError
                bound = item.get("policy_id") == expected_policy_id
                if name.startswith("digiorg-logs-") and not bound:
                    raise KeyError
                if bound:
                    state = item.get("state")
                    action = item.get("action")
                    if (item.get("index") != name or
                            item.get("index_uuid") != identity.get("uid") or
                            item.get("index.plugins.index_state_management.policy_id") != expected_policy_id or
                            item.get("index.opendistro.index_state_management.policy_id") != expected_policy_id or
                            item.get("policy_seq_no") != policy["_seq_no"] or
                            item.get("policy_primary_term") != policy["_primary_term"] or
                            item.get("policy") != entry or
                            item.get("enabled") is not True or
                            not isinstance(state, dict) or
                            set(state) != {"name", "start_time"} or
                            not isinstance(action, dict) or
                            set(action) != {"name", "start_time", "index", "failed",
                                           "consumed_retries", "last_retry_time"}):
                        raise KeyError
                    expected_action = {"hot": ("transition", -1),
                                       "delete": ("delete", 0)}.get(state["name"])
                    if expected_action != (action["name"], action["index"]):
                        raise KeyError
                    integer_fields = (state["start_time"], action["start_time"],
                                      action["consumed_retries"], action["last_retry_time"])
                    if (any(not isinstance(value, int) or isinstance(value, bool) or value < 0
                            for value in integer_fields) or
                            action["failed"] is not False):
                        raise KeyError
                bindings[name] = bound
            return bindings
        except (KeyError, TypeError, IndexError):
            raise TransitionError("ISM policy/explain contract is incomplete") from None

    def baseline(self, deadline):
        sts = self.get("statefulset.apps", OS_STS, deadline, OS_NAMESPACE)
        pod = self.get("pod", OS_POD, deadline, OS_NAMESPACE)
        pvc = self.get("persistentvolumeclaim", OS_PVC, deadline, OS_NAMESPACE)
        lifecycle = self.pod_lifecycle(pod)
        cgroup = self.cgroup(deadline)
        indices = self.index_inventory(self.http(
            "/_cat/indices?format=json&h=index,uuid,status,creation.date&expand_wildcards=open", deadline))
        policy = self.http("/_plugins/_ism/policies/digiorg-logs-retention-7d", deadline)
        explain = self.http("/_plugins/_ism/explain/*?show_policy=true", deadline)
        bindings = self.validate_policy(policy, explain, indices)
        selected = (self.config.current_primary_index, self.config.representative_log_index,
                    self.config.representative_jaeger_index)
        if any(name not in indices for name in selected):
            raise TransitionError("selected retained index is absent from baseline")
        sts_uid = sts.get("metadata", {}).get("uid")
        if not isinstance(sts_uid, str) or not sts_uid:
            raise TransitionError("OpenSearch StatefulSet UID is missing")
        pvc_identity = self.pvc_identity(pvc)
        self.baseline_data = {
            "sts_uid": sts_uid, "spec": deepcopy_json(sts.get("spec", {})),
            "pod": lifecycle, "pvc": pvc_identity, "indices": indices, "policy": bindings,
        }
        self.evidence.write(
            "retained-baseline", statefulset_uid=sts_uid, pvc_uid=pvc_identity[0],
            volume_name=pvc_identity[1], capacity=pvc_identity[2],
            pvc_spec_hash=pvc_identity[3],
            uid=lifecycle["uid"], pod_restart_count=lifecycle["restart"],
            last_termination_reason=None if lifecycle["last"] is None else lifecycle["last"]["reason"],
            last_termination_time=None if lifecycle["last"] is None else lifecycle["last"]["finishedAt"],
            memory_events_max=cgroup[2], index_uid_hashes=sorted(digest(v["uid"]) for v in indices.values()),
            deadline_seconds=RECOVERY_SECONDS, result="pass")

    @staticmethod
    def workload_without_resource_change(spec):
        value = deepcopy_json(spec)
        template = value.get("template", {})
        container = named_container(template, OS_CONTAINER)
        container.pop("resources", None)
        env = container.get("env", [])
        for item in env:
            if item.get("name") == "OPENSEARCH_JAVA_OPTS":
                item["value"] = "<heap-resource-field>"
        return value

    def verify_durable(self, deadline, *, require_candidate):
        baseline = self.baseline_data
        sts = self.get("statefulset.apps", OS_STS, deadline, OS_NAMESPACE)
        pvc = self.get("persistentvolumeclaim", OS_PVC, deadline, OS_NAMESPACE)
        pod = self.get("pod", OS_POD, deadline, OS_NAMESPACE)
        lifecycle = self.pod_lifecycle(pod)
        if sts.get("metadata", {}).get("uid") != baseline["sts_uid"] or self.pvc_identity(pvc) != baseline["pvc"]:
            raise TransitionError("durable StatefulSet/PVC identity changed")
        spec = sts.get("spec", {})
        if self.workload_without_resource_change(spec) != self.workload_without_resource_change(baseline["spec"]):
            raise TransitionError("non-resource StatefulSet or Pod-template field changed")
        container = named_container(spec.get("template", {}), OS_CONTAINER)
        expected_resources = {"requests": {"cpu": "250m", "memory": "1Gi"},
                              "limits": {"cpu": "1000m", "memory": "2Gi"}}
        if container.get("resources") != expected_resources:
            raise TransitionError("OpenSearch resources are not exact candidate values")
        heaps = [item.get("value") for item in container.get("env", []) if item.get("name") == "OPENSEARCH_JAVA_OPTS"]
        if heaps != ["-Xmx512M -Xms512M"]:
            raise TransitionError("OpenSearch heap is not exact 512Mi")
        if lifecycle["uid"] == baseline["pod"]["uid"] or not is_ready_pod(pod, baseline["sts_uid"]):
            raise TransitionError("expected new Ready OpenSearch Pod lifecycle")
        template_hash = digest(spec.get("template", {}))
        if require_candidate and template_hash != self.candidate["template_hash"]:
            raise TransitionError("post-rollout candidate template hash changed")
        return lifecycle, template_hash

    def verify_indices(self, deadline):
        observed = self.index_inventory(self.http(
            "/_cat/indices?format=json&h=index,uuid,status,creation.date&expand_wildcards=open", deadline))
        policy = self.http("/_plugins/_ism/policies/digiorg-logs-retention-7d", deadline)
        explain = self.http("/_plugins/_ism/explain/*?show_policy=true", deadline)
        self.validate_policy(policy, explain, observed)
        now_ms = int(self.clock.time() * 1000)
        for name, identity in self.baseline_data["indices"].items():
            eligible = (name.startswith("digiorg-logs-") and self.baseline_data["policy"].get(name) and
                        identity["created_ms"] + 7 * 24 * 60 * 60 * 1000 <= now_ms)
            if ((name in observed and observed[name]["uid"] != identity["uid"]) or
                    (name not in observed and not eligible)):
                raise TransitionError("non-expiry-eligible index identity changed")

    def count(self, name, deadline):
        value = self.http(f"/{name}/_count?filter_path=count", deadline)
        count = value.get("count") if isinstance(value, dict) else None
        if not isinstance(count, int) or count < 0:
            raise TransitionError("index count is malformed")
        return count

    def health(self, deadline):
        health = self.http("/_cluster/health?filter_path=status", deadline)
        recovery = self.http("/_cat/recovery?active_only=true&format=json&h=index,stage", deadline)
        primary = self.http(f"/{self.config.current_primary_index}/_search?size=0&filter_path=hits.total.value", deadline)
        try:
            count = primary["hits"]["total"]["value"]
        except (KeyError, TypeError):
            count = None
        if not isinstance(health, dict) or set(health) != {"status"} or health.get("status") not in {"yellow", "green"}:
            raise TransitionError("OpenSearch health is not exact yellow/green scalar")
        if recovery != [] or not isinstance(count, int) or count < 0:
            raise TransitionError("recovery is active or current primary is unreadable")

    def fluentd_queues(self, deadline):
        before_ds = self.get("daemonset.apps", FLUENTD_DS, deadline, FLUENTD_NAMESPACE)
        before = self.client.get_json(["get", "pods", "-l", "app=fluentd"], deadline, FLUENTD_NAMESPACE)
        ds_uid = before_ds.get("metadata", {}).get("uid")
        listed = before.get("items")
        if not isinstance(listed, list):
            raise TransitionError("Fluentd Pod list is malformed")
        selected = [item for item in listed if is_ready_pod(item, ds_uid)]
        identities = {(item.get("metadata", {}).get("name"), item.get("metadata", {}).get("uid"))
                      for item in selected}
        if (not ds_uid or not identities or any(not name or not uid for name, uid in identities) or
                len(identities) != len(selected)):
            raise TransitionError("Ready Fluentd Pod UID set is empty or ambiguous")
        pods = {item["metadata"]["name"]: item for item in selected}
        queues = {}
        pattern = re.compile(r'^fluentd_output_status_buffer_queue_length\{([^}]*)\} ([0-9]+(?:\.[0-9]+)?)$')
        for name in sorted(pods):
            text = self.client.kubectl(
                ["get", "--raw", f"/api/v1/namespaces/logging/pods/{name}:24231/proxy/metrics"], deadline)
            matches = []
            for line in text.splitlines():
                found = pattern.fullmatch(line)
                if found and re.search(r'(?:^|,)plugin_id="out_opensearch"(?:,|$)', found.group(1)):
                    matches.append(float(found.group(2)))
            if len(matches) != 1:
                raise TransitionError("Fluentd queue metric is absent or ambiguous")
            queues[digest(name)] = matches[0]
        after_ds = self.get("daemonset.apps", FLUENTD_DS, deadline, FLUENTD_NAMESPACE)
        after = self.client.get_json(["get", "pods", "-l", "app=fluentd"], deadline, FLUENTD_NAMESPACE)
        after_listed = after.get("items")
        if not isinstance(after_listed, list):
            raise TransitionError("Fluentd post-metrics Pod list is malformed")
        after_selected = [item for item in after_listed
                          if is_ready_pod(item, after_ds.get("metadata", {}).get("uid"))]
        after_identities = {(item.get("metadata", {}).get("name"), item.get("metadata", {}).get("uid"))
                            for item in after_selected}
        if (after_ds.get("metadata", {}).get("uid") != ds_uid or after_identities != identities or
                len(after_identities) != len(after_selected)):
            raise TransitionError("Fluentd UID set raced during metrics collection")
        return queues

    def sample(self, deadline, require_candidate):
        lifecycle, template_hash = self.verify_durable(deadline, require_candidate=require_candidate)
        self.verify_indices(deadline)
        self.health(deadline)
        current, maximum, events = self.cgroup(deadline)
        if maximum != 2147483648 or maximum - current < 268435456:
            raise TransitionError("cgroup memory.max/headroom contract failed")
        if require_candidate:
            if (lifecycle["uid"] != self.candidate["pod_uid"] or lifecycle["restart"] != self.candidate["restart"] or
                    lifecycle["last"] != self.candidate["last"] or events != self.candidate["events"]):
                raise TransitionError("new Pod lifecycle or memory.events.max changed")
        queues = self.fluentd_queues(deadline)
        return {"lifecycle": lifecycle, "template_hash": template_hash, "current": current,
                "maximum": maximum, "events": events, "queues": queues,
                "log": self.count(self.config.representative_log_index, deadline),
                "jaeger": self.count(self.config.representative_jaeger_index, deadline)}

    def observe(self):
        recovery = Deadline(self.clock, RECOVERY_SECONDS, "retained recovery")
        while True:
            try:
                first = self.sample(recovery, False)
                break
            except TransitionError:
                if recovery.remaining() <= 30:
                    raise TransitionError("retained recovery deadline exceeded")
                recovery.sleep(30)
        self.candidate = {"pod_uid": first["lifecycle"]["uid"], "restart": first["lifecycle"]["restart"],
                          "last": first["lifecycle"]["last"], "events": first["events"],
                          "template_hash": first["template_hash"]}
        initial_log, initial_jaeger = first["log"], first["jaeger"]
        window_start = self.clock.monotonic()
        final = first
        for sample_number in range(OBSERVATION_SAMPLES):
            if sample_number:
                target_time = window_start + sample_number * 60
                now = self.clock.monotonic()
                if now > target_time:
                    raise TransitionError("retained sample cadence deadline missed")
                self.clock.sleep(target_time - now)
                final = self.sample(Deadline(self.clock, CALL_SECONDS * 20, "retained sample"), True)
            self.evidence.write(
                "retained-sample", sample=sample_number, uid=final["lifecycle"]["uid"],
                template_hash=final["template_hash"], pod_restart_count=final["lifecycle"]["restart"],
                memory_current=final["current"], memory_max=final["maximum"],
                memory_events_max=final["events"], queue_lengths=final["queues"],
                log_count=final["log"], jaeger_count=final["jaeger"],
                deadline_seconds=OBSERVATION_SECONDS, result="pass")
        if (self.clock.monotonic() - window_start < OBSERVATION_SECONDS or
                final["log"] <= initial_log or final["jaeger"] <= initial_jaeger or
                any(value != 0 for value in final["queues"].values())):
            raise TransitionError("30-minute retained growth/queue acceptance failed")
        self.evidence.write("retained-accepted", sample=30, log_count=final["log"],
                            jaeger_count=final["jaeger"], queue_lengths=final["queues"],
                            deadline_seconds=OBSERVATION_SECONDS, result="pass")


class CleanBootstrapAcceptance:
    """Bounded, post-`up`, read-only acceptance for an empty-cluster bootstrap."""

    def __init__(self, client, config, clock, evidence):
        self.client = client
        self.config = config
        self.clock = clock
        self.evidence = evidence
        self.reads = RetainedCollector(client, config, clock, evidence)
        self.anchor = None

    def application_snapshot(self, deadline):
        applications = self.client.applications(deadline)
        if tuple(sorted(applications)) != EXPECTED_APPLICATIONS:
            raise TransitionError("clean Application inventory is not the exact 32-name set")
        self.client.require_no_active(applications)
        self.client.require_healthy_synced(applications)
        identities = {}
        for name, application in applications.items():
            actual = [source_identity(item) for item in source_list(application)]
            if actual != CLEAN_SOURCE_GRAPH[name]:
                raise TransitionError(f"clean source graph mismatch for {name}")
            metadata = application.get("metadata", {})
            uid, resource_version = metadata.get("uid"), metadata.get("resourceVersion")
            if not all(isinstance(value, str) and value for value in (uid, resource_version)):
                raise TransitionError(f"clean Application identity is incomplete for {name}")
            if not isinstance(application.get("spec"), dict):
                raise TransitionError(f"clean Application spec is malformed for {name}")
            identities[name] = {"uid": uid, "spec_hash": digest(application["spec"])}
        return identities

    def controller_snapshot(self, deadline):
        controllers = self.client.get_json(
            ["get", "statefulsets.apps", "-l", CONTROLLER_LABEL], deadline, ARGOCD_NAMESPACE
        ).get("items", [])
        if len(controllers) != 1:
            raise TransitionError("clean bootstrap has no exact application controller")
        controller = controllers[0]
        metadata, spec, status = (controller.get("metadata", {}), controller.get("spec", {}),
                                  controller.get("status", {}))
        replicas = spec.get("replicas")
        revision = status.get("currentRevision")
        if (metadata.get("name") != CONTROLLER_NAME or metadata.get("namespace") != ARGOCD_NAMESPACE or
                not isinstance(metadata.get("uid"), str) or not metadata.get("uid") or
                not isinstance(replicas, int) or replicas <= 0 or
                status.get("replicas") != replicas or status.get("readyReplicas") != replicas or
                status.get("observedGeneration") != metadata.get("generation") or
                not isinstance(revision, str) or not revision or status.get("updateRevision") != revision):
            raise TransitionError("clean application controller readiness/revision is incomplete")
        pods = self.client.controller_pods(deadline)
        if len(pods) != replicas or not all(is_ready_pod(item, metadata["uid"]) for item in pods):
            raise TransitionError("clean application controller Pod closure is incomplete")
        return {"uid": metadata["uid"], "spec_hash": digest(spec), "revision": revision,
                "pod_uids": sorted(item["metadata"]["uid"] for item in pods)}

    def opensearch_snapshot(self, deadline):
        sts = self.reads.get("statefulset.apps", OS_STS, deadline, OS_NAMESPACE)
        pod = self.reads.get("pod", OS_POD, deadline, OS_NAMESPACE)
        pvc = self.reads.get("persistentvolumeclaim", OS_PVC, deadline, OS_NAMESPACE)
        metadata, spec, status = sts.get("metadata", {}), sts.get("spec", {}), sts.get("status", {})
        sts_uid = metadata.get("uid")
        if (metadata.get("name") != OS_STS or not isinstance(sts_uid, str) or not sts_uid or
                spec.get("replicas") != 1 or status.get("replicas") != 1 or
                status.get("readyReplicas") != 1 or not status.get("currentRevision") or
                status.get("observedGeneration") != metadata.get("generation") or
                status.get("currentRevision") != status.get("updateRevision") or
                not is_ready_pod(pod, sts_uid)):
            raise TransitionError("clean OpenSearch StatefulSet/Pod readiness is incomplete")
        lifecycle = self.reads.pod_lifecycle(pod)
        container = named_container(spec.get("template", {}), OS_CONTAINER)
        resources = {"requests": {"cpu": "250m", "memory": "1Gi"},
                     "limits": {"cpu": "1000m", "memory": "2Gi"}}
        env = {item.get("name"): item for item in container.get("env", []) if isinstance(item, dict)}
        if (container.get("image") != OPENSEARCH_IMAGE or
                container.get("imagePullPolicy") != "IfNotPresent" or
                container.get("resources") != resources or
                env.get("OPENSEARCH_JAVA_OPTS", {}).get("value") != "-Xmx512M -Xms512M" or
                env.get("DISABLE_SECURITY_PLUGIN", {}).get("value") != "true" or
                spec.get("template", {}).get("spec", {}).get("securityContext") !=
                {"fsGroup": 1000, "runAsUser": 1000} or
                container.get("securityContext") !=
                {"capabilities": {"drop": ["ALL"]}, "runAsNonRoot": True, "runAsUser": 1000}):
            raise TransitionError("clean OpenSearch image/resource/security contract is not exact")
        pvc_identity = self.reads.pvc_identity(pvc)
        pvc_spec = pvc.get("spec", {})
        if (pvc_spec.get("accessModes") != ["ReadWriteOnce"] or
                pvc_spec.get("storageClassName") != "standard" or
                pvc_spec.get("volumeMode") != "Filesystem" or
                pvc_spec.get("resources", {}).get("requests") != {"storage": "8Gi"} or
                pvc_spec.get("selector") not in (None, {}) or pvc_spec.get("dataSource") is not None or
                pvc_spec.get("dataSourceRef") is not None):
            raise TransitionError("clean OpenSearch PVC canonical spec is not exact")
        current, maximum, events = self.reads.cgroup(deadline)
        if maximum != 2147483648 or maximum - current < 268435456:
            raise TransitionError("clean OpenSearch cgroup 2Gi/headroom contract failed")
        return {"sts_uid": sts_uid, "sts_spec_hash": digest(spec), "pvc": list(pvc_identity),
                "pod_uid": lifecycle["uid"], "restart": lifecycle["restart"],
                "last": lifecycle["last"], "pod_spec_hash": digest(pod.get("spec", {})),
                "memory_events_max": events, "memory_current": current, "memory_max": maximum}

    def telemetry(self, deadline):
        indices = self.reads.index_inventory(self.reads.http(
            "/_cat/indices?format=json&h=index,uuid,status,creation.date&expand_wildcards=open", deadline),
            allow_empty=True)
        policy = self.reads.http("/_plugins/_ism/policies/digiorg-logs-retention-7d", deadline)
        explain = self.reads.http("/_plugins/_ism/explain/*?show_policy=true", deadline)
        self.reads.validate_policy(policy, explain, indices)
        health = self.reads.http("/_cluster/health?filter_path=status", deadline)
        recovery = self.reads.http("/_cat/recovery?active_only=true&format=json&h=index,stage", deadline)
        searchable = self.reads.http("/_search?size=0&filter_path=hits.total.value", deadline)
        total = self.reads.http("/_count?filter_path=count", deadline)
        jaeger_text = self.client.kubectl(
            ["get", "--raw", "/api/v1/namespaces/tracing/services/http:jaeger:16686/proxy/api/services"],
            deadline,
        )
        jaeger = exact_json(jaeger_text, dict)
        try:
            hits = searchable["hits"]["total"]["value"]
            documents = total["count"]
            services = jaeger["data"]
        except (KeyError, TypeError):
            hits, documents, services = None, None, None
        if (health not in ({"status": "yellow"}, {"status": "green"}) or recovery != [] or
                not isinstance(hits, int) or hits < 0 or
                not isinstance(documents, int) or documents < 0 or
                not isinstance(services, list)):
            raise TransitionError("clean natural log/trace health telemetry failed")
        queues = self.reads.fluentd_queues(deadline)
        return {"log": documents, "jaeger": len(services), "queues": queues}

    def sample(self, deadline):
        applications = self.application_snapshot(deadline)
        controller = self.controller_snapshot(deadline)
        opensearch = self.opensearch_snapshot(deadline)
        self.client.kubectl(
            ["get", "job.batch", "fluentd-log-schema", "-o", "json"], deadline,
            FLUENTD_NAMESPACE, expect_not_found=("jobs.batch", "fluentd-log-schema")
        )
        telemetry = self.telemetry(deadline)
        stable_opensearch = deepcopy_json(opensearch)
        stable_opensearch.pop("memory_current")
        current = {"applications": applications, "controller": controller,
                   "opensearch": stable_opensearch}
        if self.anchor is None:
            self.anchor = deepcopy_json(current)
        elif current != self.anchor:
            raise TransitionError("clean bootstrap UID/spec/revision/lifecycle closure drifted")
        return opensearch, telemetry

    def observe(self):
        recovery = Deadline(self.clock, RECOVERY_SECONDS, "clean bootstrap recovery")
        while True:
            try:
                first = self.sample(recovery)
                break
            except TransitionError:
                if recovery.remaining() <= 30:
                    raise TransitionError("clean bootstrap recovery deadline exceeded")
                recovery.sleep(30)
        window_start = self.clock.monotonic()
        final = first
        for sample_number in range(OBSERVATION_SAMPLES):
            if sample_number:
                target = window_start + sample_number * 60
                if self.clock.monotonic() > target:
                    raise TransitionError("clean bootstrap sample cadence deadline missed")
                self.clock.sleep(target - self.clock.monotonic())
                final = self.sample(Deadline(self.clock, CALL_SECONDS * 20, "clean bootstrap sample"))
            opensearch, telemetry = final
            self.evidence.write(
                "clean-bootstrap-sample", sample=sample_number,
                uid=opensearch["pod_uid"], statefulset_uid=opensearch["sts_uid"],
                pvc_uid=opensearch["pvc"][0], volume_name=opensearch["pvc"][1],
                capacity=opensearch["pvc"][2], pvc_spec_hash=opensearch["pvc"][3],
                spec_hash=opensearch["sts_spec_hash"], pod_restart_count=opensearch["restart"],
                memory_current=opensearch["memory_current"], memory_max=opensearch["memory_max"],
                memory_events_max=opensearch["memory_events_max"], queue_lengths=telemetry["queues"],
                log_count=telemetry["log"], jaeger_count=telemetry["jaeger"],
                application_count=32, application_spec_hash=digest(self.anchor["applications"]),
                deadline_seconds=OBSERVATION_SECONDS, result="pass")
        if self.clock.monotonic() - window_start < OBSERVATION_SECONDS:
            raise TransitionError("clean bootstrap observation window was shorter than 30 minutes")
        self.evidence.write("clean-bootstrap-accepted", sample=30, application_count=32,
                            schema_job_absence="exact-NotFound:logging/fluentd-log-schema",
                            deadline_seconds=OBSERVATION_SECONDS, result="pass")


def checkout_path_allowed(label, resolved, root, mode):
    """Allow only `up`'s fixed ignored kubeconfig inside a clean checkout."""
    resolved, root = Path(resolved).resolve(), Path(root).resolve()
    if resolved != root and root not in resolved.parents:
        return True
    return (mode == "clean-bootstrap-accept" and label == "kubeconfig" and
            resolved == root / "kubeconfig-local.yaml")


class Protocol:
    def __init__(self, config, runner, clock, evidence):
        self.config = config
        self.runner = runner
        self.clock = clock
        self.evidence = evidence
        self.phase = "preflight"
        self.baseline = None
        self.controller = None
        self.old_pod_uids = set()
        self.final_facts = None

    def run_command(self, argv, deadline, *, expect_not_found=None):
        try:
            result = self.runner.run(argv, timeout=deadline.call_timeout())
        except (TimeoutError, OSError) as error:
            raise TransitionError(redact(error)) from error
        if expect_not_found:
            resource, name = expect_not_found
            expected = f'Error from server (NotFound): {resource} "{name}" not found'
            if (result.returncode == 1 and result.stdout == "" and
                    result.stderr in {expected, expected + "\n"}):
                return None
            raise TransitionError("expected exact NotFound result")
        if result.returncode != 0:
            raise TransitionError(f"command failed: {redact(result.stderr)}")
        return result.stdout

    def kubectl(self, args, deadline, namespace=None, expect_not_found=None):
        request_seconds = max(1, min(CALL_SECONDS, int(max(1, deadline.remaining()))))
        argv = [
            "kubectl", "--kubeconfig", str(self.config.kubeconfig),
            "--context", self.config.context,
            f"--request-timeout={request_seconds}s",
        ]
        if namespace:
            argv.extend(["-n", namespace])
        argv.extend(args)
        return self.run_command(argv, deadline, expect_not_found=expect_not_found)

    def get_json(self, args, deadline, namespace=None):
        output = self.kubectl([*args, "-o", "json"], deadline, namespace)
        try:
            return json.loads(output)
        except (TypeError, json.JSONDecodeError) as error:
            raise TransitionError("command returned invalid JSON") from error

    def patch(self, kind, name, operations, deadline):
        output = self.kubectl(
            ["patch", kind, name, "--type=json", "-p", canonical(operations), "-o", "json"],
            deadline,
            ARGOCD_NAMESPACE,
        )
        try:
            return json.loads(output)
        except json.JSONDecodeError as error:
            raise TransitionError("patch returned invalid JSON") from error

    def validate_remote(self, deadline):
        ref = f"refs/tags/{self.config.runtime_tag}"
        output = self.run_command(
            ["git", "ls-remote", "--tags", self.config.remote_url, ref, f"{ref}^{{}}"],
            deadline,
        )
        refs = {}
        for line in output.splitlines():
            fields = line.split("\t")
            if len(fields) == 2:
                refs[fields[1]] = fields[0]
        if ref not in refs or refs.get(f"{ref}^{{}}") != self.config.runtime_commit:
            raise TransitionError("remote tag is not annotated or does not peel to runtime commit")

    def validate_local_checkout(self, deadline):
        root_text = self.run_command(["git", "rev-parse", "--show-toplevel"], deadline)
        try:
            root = Path(root_text.rstrip("\n")).resolve(strict=True)
        except (OSError, ValueError) as error:
            raise TransitionError("local checkout root is invalid") from error
        if Path(__file__).resolve() != root / TRACKED_RUNTIME_FILES[0]:
            raise TransitionError("runtime script is not the expected local checkout file")
        head = self.run_command(["git", "rev-parse", "HEAD"], deadline)
        if head not in {self.config.runtime_commit, self.config.runtime_commit + "\n"}:
            raise TransitionError("local checkout HEAD is not the runtime commit")
        dirty = self.run_command(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"], deadline)
        if dirty != "":
            raise TransitionError("local checkout is not clean")
        for relative in TRACKED_RUNTIME_FILES:
            tracked = self.run_command(
                ["git", "ls-tree", "--name-only", "HEAD", "--", relative], deadline)
            if tracked not in {relative, relative + "\n"}:
                raise TransitionError(f"required runtime file is not tracked at HEAD: {relative}")
        for label, path in (("kubeconfig", self.config.kubeconfig), ("evidence", self.config.evidence)):
            resolved = path.resolve()
            if not checkout_path_allowed(label, resolved, root, self.config.mode):
                raise TransitionError(f"{label} path must be outside repository root")

    def applications(self, deadline):
        listing = self.get_json(["get", "applications.argoproj.io"], deadline, ARGOCD_NAMESPACE)
        items = listing.get("items")
        if not isinstance(items, list):
            raise TransitionError("Application list shape is invalid")
        result = {}
        for item in items:
            name = item.get("metadata", {}).get("name") if isinstance(item, dict) else None
            if not isinstance(name, str) or not name or name in result:
                raise TransitionError("Application inventory has malformed or duplicate names")
            result[name] = item
        return result

    @staticmethod
    def require_no_active(applications):
        for name, application in applications.items():
            if "operation" in application.get("spec", {}):
                raise TransitionError(f"Application {name} has spec.operation")
            phase = application.get("status", {}).get("operationState", {}).get("phase")
            if phase in ACTIVE_PHASES:
                raise TransitionError(f"Application {name} has active operation")

    @staticmethod
    def require_healthy_synced(applications):
        for name, application in applications.items():
            status = application.get("status", {})
            if status.get("health", {}).get("status") != "Healthy" or status.get("sync", {}).get("status") != "Synced":
                raise TransitionError(f"Application {name} is not exact Healthy/Synced")

    def require_old_graph(self, applications):
        required = {"root-app", "argocd", "opensearch", "fluentd", "app-config", "core-catalog"}
        if not required <= set(applications):
            raise TransitionError("required retained Applications are missing")
        root_source = source_list(applications["root-app"])[0]
        argo_source = source_list(applications["argocd"])[0]
        if source_identity(root_source) != (CORE_REPO, None, "apps", None, self.config.old_tag):
            raise TransitionError("root-app retained graph mismatch")
        if source_identity(argo_source) != (CORE_REPO, None, "platform/base/argocd", None, self.config.old_tag):
            raise TransitionError("argocd retained graph mismatch")
        os_sources = source_list(applications["opensearch"])
        expected_os = [
            ("https://opensearch-project.github.io/helm-charts", "opensearch", None, None, "3.7.0"),
            (CORE_REPO, None, None, "values", self.config.old_tag),
            (CORE_REPO, None, "platform/base/opensearch", None, self.config.old_tag),
        ]
        if len(os_sources) != 3 or [source_identity(item) for item in os_sources] != expected_os:
            raise TransitionError("OpenSearch retained source graph mismatch")
        if source_identity(source_list(applications["fluentd"])[0]) != (
                CORE_REPO, None, "platform/base/fluentd", None, self.config.old_tag):
            raise TransitionError("Fluentd retained source graph mismatch")
        if source_identity(source_list(applications["app-config"])[0]) != (
                APP_CONFIG_REPO, None, "claims", None, "main"):
            raise TransitionError("app-config source graph mismatch")
        if source_identity(source_list(applications["core-catalog"])[0]) != (
                CATALOG_REPO, None, "compositions/local", None, CATALOG_REVISION):
            raise TransitionError("core-catalog source graph mismatch")
        core_targets = [item.get("targetRevision") for application in applications.values()
                        for item in source_list(application)
                        if item and item.get("repoURL") == CORE_REPO]
        if len(core_targets) != 32 or set(core_targets) != {self.config.old_tag}:
            raise TransitionError("retained Core source multiplicity/targets mismatch")

    def require_application_closure(self, applications, expected_specs, label):
        if set(applications) != set(self.baseline["uids"]):
            raise TransitionError(f"Application closure changed inventory at {label}")
        for name, application in applications.items():
            if (application.get("metadata", {}).get("uid") != self.baseline["uids"][name] or
                    application.get("spec") != expected_specs[name]):
                raise TransitionError(f"Application closure changed identity/spec for {name} at {label}")

    def hpa_absent(self, deadline):
        listing = self.get_json(["get", "horizontalpodautoscalers.autoscaling", "-A"], deadline)
        for item in listing.get("items", []):
            ref = item.get("spec", {}).get("scaleTargetRef", {})
            api_group = str(ref.get("apiVersion", "")).split("/", 1)[0].lower()
            if (item.get("metadata", {}).get("namespace") == ARGOCD_NAMESPACE and
                    api_group == "apps" and str(ref.get("kind", "")).lower() == "statefulset" and
                    ref.get("name") == CONTROLLER_NAME):
                raise TransitionError("HPA targets application controller")

    def controller_pods(self, deadline):
        listing = self.get_json(["get", "pods", "-l", CONTROLLER_LABEL], deadline, ARGOCD_NAMESPACE)
        return listing.get("items", [])

    def preflight(self, deadline):
        self.validate_remote(deadline)
        view = self.get_json(["config", "view", "--minify"], deadline)
        servers = [item.get("cluster", {}).get("server") for item in view.get("clusters", [])]
        namespace = self.get_json(["get", "namespace", "kube-system"], deadline)
        if servers != [self.config.expected_server] or namespace.get("metadata", {}).get("uid") != self.config.expected_kube_system_uid:
            raise TransitionError("cluster identity does not match expected server and namespace UID")
        applications = self.applications(deadline)
        if tuple(sorted(applications)) != EXPECTED_APPLICATIONS:
            raise TransitionError("Application inventory does not equal reviewed 32-name set")
        self.require_no_active(applications)
        self.require_healthy_synced(applications)
        self.require_old_graph(applications)
        for name in ("root-app", "argocd", "opensearch"):
            identity = operation_identity(applications[name])
            try:
                valid = (identity is not None and rfc3339(identity["startedAt"]) and
                         valid_revision_list(identity["requestedRevisions"]) and
                         valid_revision_list(identity["resultRevisions"]))
            except (KeyError, TypeError, ValueError):
                valid = False
            if not valid:
                raise TransitionError(f"{name} prior operation identity is required")
        controllers = self.get_json(["get", "statefulsets.apps", "-l", CONTROLLER_LABEL], deadline, ARGOCD_NAMESPACE).get("items", [])
        if len(controllers) != 1:
            raise TransitionError("expected exactly one application controller StatefulSet")
        controller = controllers[0]
        if (controller.get("metadata", {}).get("name") != CONTROLLER_NAME or
                controller.get("metadata", {}).get("namespace") != ARGOCD_NAMESPACE):
            raise TransitionError("expected exact application controller name and namespace")
        replicas = controller.get("spec", {}).get("replicas")
        status = controller.get("status", {})
        if (not isinstance(replicas, int) or replicas <= 0 or
                status.get("replicas") != replicas or status.get("readyReplicas") != replicas or
                status.get("currentRevision") != status.get("updateRevision") or
                status.get("observedGeneration") != controller.get("metadata", {}).get("generation")):
            raise TransitionError("application controller is not fully ready")
        pods = self.controller_pods(deadline)
        if len(pods) != replicas or not all(is_ready_pod(pod, controller["metadata"]["uid"]) for pod in pods):
            raise TransitionError("controller Pod identities/readiness mismatch")
        self.hpa_absent(deadline)
        self.controller = deepcopy_json(controller)
        self.old_pod_uids = {pod["metadata"]["uid"] for pod in pods}
        app_config_revision = resolved_commit(applications["app-config"])
        self.baseline = {
            "specs": {name: deepcopy_json(item["spec"]) for name, item in applications.items()},
            "spec_hashes": {name: digest(item["spec"]) for name, item in applications.items()},
            "uids": {name: item["metadata"]["uid"] for name, item in applications.items()},
            "resource_versions": {name: applications[name]["metadata"]["resourceVersion"] for name in ("root-app", "argocd")},
            "operations": {name: operation_identity(applications[name]) for name in ("root-app", "argocd", "opensearch")},
            "app_config_revision": app_config_revision,
            "controller_spec": controller_non_replica_spec(controller),
            "controller_revision": status["currentRevision"],
        }
        self.evidence.write(
            "preflight", name=controller["metadata"]["name"],
            uid=controller["metadata"]["uid"], resource_version=controller["metadata"]["resourceVersion"],
            replicas=replicas, pod_uid_hashes=sorted(digest(uid) for uid in self.old_pod_uids),
            application_count=len(applications), controller_spec_hash=digest(self.baseline["controller_spec"]),
            application_spec_hash=digest(self.baseline["spec_hashes"]),
            operation_hashes={name: digest(value) for name, value in self.baseline["operations"].items()},
            revisions={name: value["requestedRevisions"] for name, value in self.baseline["operations"].items()},
            current_revision=status["currentRevision"], update_revision=status["updateRevision"],
            deadline_seconds=BARRIER_SECONDS, result="pass",
        )

    def stop_controller(self, deadline):
        controller = self.controller
        operations = [
            {"op": "test", "path": "/metadata/uid", "value": controller["metadata"]["uid"]},
            {"op": "test", "path": "/metadata/resourceVersion", "value": controller["metadata"]["resourceVersion"]},
            {"op": "test", "path": "/spec/replicas", "value": controller["spec"]["replicas"]},
            {"op": "replace", "path": "/spec/replicas", "value": 0},
        ]
        self.phase = "stop-attempted"
        self.patch("statefulsets.apps", controller["metadata"]["name"], operations, deadline)
        self.phase = "stopped"
        wait_deadline = deadline.child(WAIT_SECONDS, "controller stop wait")
        while True:
            current = self.get_json(["get", "statefulsets.apps", controller["metadata"]["name"]], wait_deadline, ARGOCD_NAMESPACE)
            pods = self.controller_pods(wait_deadline)
            status = current.get("status", {})
            owned = [pod for pod in pods if any(ref.get("uid") == controller["metadata"]["uid"] for ref in pod.get("metadata", {}).get("ownerReferences", []))]
            if (current.get("spec", {}).get("replicas") == 0 and
                    status.get("observedGeneration") == current.get("metadata", {}).get("generation") and
                    (status.get("replicas") or 0) == 0 and (status.get("readyReplicas") or 0) == 0 and not owned):
                break
            wait_deadline.sleep()
        self.evidence.write("controller-stopped", uid=controller["metadata"]["uid"], replicas=0,
                            deadline_seconds=WAIT_SECONDS, result="pass")

    def barrier_recheck(self, deadline):
        self.hpa_absent(deadline)
        current_controller = self.get_json(
            ["get", "statefulsets.apps", CONTROLLER_NAME], deadline, ARGOCD_NAMESPACE)
        if (current_controller.get("metadata", {}).get("uid") != self.controller["metadata"]["uid"] or
                controller_non_replica_spec(current_controller) != self.baseline["controller_spec"]):
            raise TransitionError("controller UID or non-replica spec changed at barrier")
        applications = self.applications(deadline)
        self.require_no_active(applications)
        self.require_healthy_synced(applications)
        self.require_old_graph(applications)
        self.require_application_closure(applications, self.baseline["specs"], "barrier")
        for name in ("root-app", "argocd", "opensearch"):
            if operation_identity(applications[name]) != self.baseline["operations"][name]:
                raise TransitionError(f"{name} prior operation identity changed at barrier")
        for name in ("opensearch", "fluentd"):
            if (applications[name]["metadata"]["uid"] != self.baseline["uids"][name] or
                    digest(applications[name]["spec"]) != self.baseline["spec_hashes"][name]):
                raise TransitionError(f"{name} identity changed at barrier")
        if resolved_commit(applications["app-config"]) != self.baseline["app_config_revision"]:
            raise TransitionError("app-config resolved revision changed at barrier")
        self.evidence.write(
            "barrier-verified", uid=self.controller["metadata"]["uid"], replicas=0,
            application_count=len(applications), application_spec_hash=digest(self.baseline["spec_hashes"]),
            targets={name: target(applications[name]) for name in ("root-app", "argocd", "fluentd", "app-config", "core-catalog")},
            operation_hashes={name: digest(operation_identity(applications[name])) for name in ("root-app", "argocd", "opensearch")},
            deadline_seconds=BARRIER_SECONDS, result="pass",
        )
        return applications

    def app_patch(self, name, old_application, new_revision, deadline):
        source = source_list(old_application)[0]
        operations = [
            {"op": "test", "path": "/metadata/uid", "value": old_application["metadata"]["uid"]},
            {"op": "test", "path": "/metadata/resourceVersion", "value": old_application["metadata"]["resourceVersion"]},
            {"op": "test", "path": "/spec/source/repoURL", "value": source["repoURL"]},
            {"op": "test", "path": "/spec/source/path", "value": source["path"]},
            {"op": "test", "path": "/spec/source/targetRevision", "value": source["targetRevision"]},
            {"op": "replace", "path": "/spec/source/targetRevision", "value": new_revision},
        ]
        return self.patch("applications.argoproj.io", name, operations, deadline)

    def close_owners(self, applications, deadline):
        for name in ("root-app", "argocd"):
            self.app_patch(name, applications[name], self.config.runtime_tag, deadline)
        closed_specs = {}
        for name in ("root-app", "argocd"):
            current = self.get_json(["get", "applications.argoproj.io", name], deadline, ARGOCD_NAMESPACE)
            expected = deepcopy_json(self.baseline["specs"][name])
            expected["source"]["targetRevision"] = self.config.runtime_tag
            if current.get("metadata", {}).get("uid") != self.baseline["uids"][name]:
                raise TransitionError(f"{name} UID changed during owner readback")
            if current.get("spec") != expected:
                raise TransitionError(f"{name} spec changed beyond targetRevision")
            closed_specs[name] = current["spec"]
            if name == "argocd":
                self.baseline["argocd_pre_restore_reconciled"] = current.get("status", {}).get("reconciledAt")
        self.evidence.write(
            "owners-closed", revision=self.config.runtime_tag,
            targets={name: self.config.runtime_tag for name in ("root-app", "argocd")},
            application_spec_hash=digest(closed_specs),
            deadline_seconds=BARRIER_SECONDS, result="pass",
        )

    def final_stopped_recheck(self, deadline):
        """Last fail-closed gate before reconciliation can resume."""
        self.hpa_absent(deadline)
        current = self.get_json(
            ["get", "statefulsets.apps", CONTROLLER_NAME], deadline, ARGOCD_NAMESPACE)
        status = current.get("status", {})
        if (current.get("metadata", {}).get("uid") != self.controller["metadata"]["uid"] or
                current.get("spec", {}).get("replicas") != 0 or
                controller_non_replica_spec(current) != self.baseline["controller_spec"] or
                status.get("observedGeneration") != current.get("metadata", {}).get("generation") or
                (status.get("replicas") or 0) != 0 or (status.get("readyReplicas") or 0) != 0):
            raise TransitionError("controller changed at last stopped gate")
        pods = self.controller_pods(deadline)
        if any(any(ref.get("uid") == self.controller["metadata"]["uid"]
                   for ref in pod.get("metadata", {}).get("ownerReferences", [])) for pod in pods):
            raise TransitionError("controller Pod exists at last stopped gate")
        applications = self.applications(deadline)
        self.require_no_active(applications)
        expected = deepcopy_json(self.baseline["specs"])
        for name in ("root-app", "argocd"):
            expected[name]["source"]["targetRevision"] = self.config.runtime_tag
        try:
            self.require_application_closure(applications, expected, "last stopped gate")
        except TransitionError as error:
            raise TransitionError(f"last stopped gate: {error}") from error
        for name in ("root-app", "argocd", "opensearch"):
            if operation_identity(applications[name]) != self.baseline["operations"][name]:
                raise TransitionError(f"last stopped gate operation identity changed for {name}")
        if resolved_commit(applications["app-config"]) != self.baseline["app_config_revision"]:
            raise TransitionError("last stopped gate app-config resolved revision changed")

    def restore_controller(self, deadline):
        prior = self.controller
        current = self.get_json(["get", "statefulsets.apps", prior["metadata"]["name"]], deadline, ARGOCD_NAMESPACE)
        if (current["metadata"]["uid"] != prior["metadata"]["uid"] or
                current.get("spec", {}).get("replicas") != 0 or
                controller_non_replica_spec(current) != self.baseline["controller_spec"]):
            raise TransitionError("controller changed while stopped")
        operations = [
            {"op": "test", "path": "/metadata/uid", "value": current["metadata"]["uid"]},
            {"op": "test", "path": "/metadata/resourceVersion", "value": current["metadata"]["resourceVersion"]},
            {"op": "test", "path": "/spec/replicas", "value": 0},
            {"op": "replace", "path": "/spec/replicas", "value": prior["spec"]["replicas"]},
        ]
        self.phase = "restore-attempted"
        self.patch("statefulsets.apps", current["metadata"]["name"], operations, deadline)
        self.phase = "restored"
        wait_deadline = deadline.child(WAIT_SECONDS, "controller restore wait")
        stable_uids = None
        stable_samples = 0
        while stable_samples < 2:
            current = self.get_json(["get", "statefulsets.apps", prior["metadata"]["name"]], wait_deadline, ARGOCD_NAMESPACE)
            pods = self.controller_pods(wait_deadline)
            replicas = prior["spec"]["replicas"]
            status = current.get("status", {})
            pod_uids = {pod["metadata"]["uid"] for pod in pods}
            ready = (
                current["metadata"]["uid"] == prior["metadata"]["uid"] and
                current.get("spec", {}).get("replicas") == replicas and
                controller_non_replica_spec(current) == self.baseline["controller_spec"] and
                status.get("observedGeneration") == current["metadata"].get("generation") and
                status.get("replicas") == replicas and status.get("readyReplicas") == replicas and
                status.get("currentRevision") == self.baseline["controller_revision"] and
                status.get("updateRevision") == self.baseline["controller_revision"] and
                len(pods) == replicas and all(is_ready_pod(pod, prior["metadata"]["uid"]) for pod in pods)
            )
            if ready and pod_uids and pod_uids.isdisjoint(self.old_pod_uids):
                if pod_uids == stable_uids:
                    stable_samples += 1
                else:
                    stable_uids = pod_uids
                    stable_samples = 1
            else:
                stable_uids = None
                stable_samples = 0
            if stable_samples < 2:
                if wait_deadline.remaining() <= POLL_SECONDS:
                    raise TransitionError("new controller Pod identities did not stabilize before barrier deadline")
                wait_deadline.sleep()
        self.evidence.write(
            "controller-restored", uid=prior["metadata"]["uid"], replicas=prior["spec"]["replicas"],
            pod_uid_hashes=sorted(digest(uid) for uid in stable_uids),
            controller_spec_hash=digest(self.baseline["controller_spec"]),
            current_revision=self.baseline["controller_revision"], update_revision=self.baseline["controller_revision"],
            deadline_seconds=WAIT_SECONDS, result="pass",
        )

    def rollback(self, deadline):
        errors = []
        try:
            for name in ("root-app", "argocd"):
                current = self.get_json(["get", "applications.argoproj.io", name], deadline, ARGOCD_NAMESPACE)
                if current.get("metadata", {}).get("uid") != self.baseline["uids"][name]:
                    raise TransitionError(f"rollback rejected Application UID changed for {name}")
                current_target = target(current)
                if current_target == self.config.runtime_tag:
                    self.app_patch(name, current, self.config.old_tag, deadline)
                elif current_target != self.config.old_tag:
                    raise TransitionError(f"rollback rejected third target for {name}")
            for name in ("root-app", "argocd"):
                current = self.get_json(["get", "applications.argoproj.io", name], deadline, ARGOCD_NAMESPACE)
                if current.get("spec") != self.baseline["specs"][name]:
                    raise TransitionError(f"rollback exact old spec readback failed for {name}")
        except BaseException as error:  # Restoration is still mandatory and bounded.
            errors.append(redact(error))
        try:
            self.restore_controller(deadline)
        except BaseException as error:
            errors.append(redact(error))
        self.evidence.write("rollback", result="failed" if errors else "pass", error="; ".join(errors) if errors else "")
        if errors:
            raise TransitionError("; ".join(errors))

    def classify_ambiguous_controller_patch(self, deadline):
        """Resolve a signal/transport race by readback, never by assumption."""
        prior = self.controller
        current = self.get_json(
            ["get", "statefulsets.apps", prior["metadata"]["name"]],
            deadline,
            ARGOCD_NAMESPACE,
        )
        if current.get("metadata", {}).get("uid") != prior["metadata"]["uid"]:
            raise TransitionError("controller UID changed during ambiguous patch")
        replicas = current.get("spec", {}).get("replicas")
        if replicas == 0:
            self.phase = "stopped"
            return
        if replicas == prior["spec"]["replicas"]:
            self.phase = "restored" if self.phase == "restore-attempted" else "preflight"
            return
        raise TransitionError("controller has a third replica value after ambiguous patch")

    def operation_passes(self, application, baseline_identity, revisions):
        state = application.get("status", {}).get("operationState", {})
        identity = operation_identity(application)
        if identity is None or baseline_identity is None:
            return False
        return (
            identity is not None and identity != baseline_identity and
            timestamp_later(identity.get("startedAt"), baseline_identity.get("startedAt")) and
            identity.get("requestedRevisions") == revisions and
            identity.get("resultRevisions") == revisions and
            state.get("phase") == "Succeeded"
        )

    def final_passes(self, applications, deadline):
        try:
            self.require_no_active(applications)
            self.require_healthy_synced(applications)
            root = applications["root-app"]
            argo = applications["argocd"]
            os_app = applications["opensearch"]
            expected_specs = deepcopy_json(self.baseline["specs"])
            expected_specs["root-app"]["source"]["targetRevision"] = self.config.runtime_tag
            expected_specs["argocd"]["source"]["targetRevision"] = self.config.runtime_tag
            expected_specs["opensearch"]["sources"][1]["targetRevision"] = self.config.runtime_tag
            self.require_application_closure(applications, expected_specs, "final")
            if target(root) != self.config.runtime_tag or status_revisions(root) != [self.config.runtime_commit]:
                return False
            if not self.operation_passes(root, self.baseline["operations"]["root-app"], [self.config.runtime_commit]):
                return False
            if (target(argo) != self.config.runtime_tag or status_revisions(argo) != [self.config.runtime_commit] or
                    not timestamp_later(argo.get("status", {}).get("reconciledAt"),
                                        self.baseline["argocd_pre_restore_reconciled"])):
                return False
            if [item.get("targetRevision") for item in source_list(os_app)] != ["3.7.0", self.config.runtime_tag, self.config.old_tag]:
                return False
            if status_revisions(os_app) != ["3.7.0", self.config.runtime_commit, self.config.old_commit]:
                return False
            if not self.operation_passes(os_app, self.baseline["operations"]["opensearch"], ["3.7.0", self.config.runtime_commit, self.config.old_commit]):
                return False
            if target(applications["fluentd"]) != self.config.old_tag:
                return False
            if target(applications["app-config"]) != "main" or resolved_commit(applications["app-config"]) != self.baseline["app_config_revision"]:
                return False
            if target(applications["core-catalog"]) != CATALOG_REVISION:
                return False
            core_targets = []
            for application in applications.values():
                for item in source_list(application):
                    if item and item.get("repoURL") == CORE_REPO:
                        core_targets.append(item.get("targetRevision"))
            if len(core_targets) != 32 or core_targets.count(self.config.runtime_tag) != 3 or core_targets.count(self.config.old_tag) != 29:
                return False
            self.kubectl(
                ["get", "job.batch", "fluentd-log-schema", "-o", "json"],
                deadline,
                FLUENTD_NAMESPACE,
                expect_not_found=("jobs.batch", "fluentd-log-schema"),
            )
            self.final_facts = {
                "application_count": len(applications),
                "application_spec_hash": digest({name: item["spec"] for name, item in applications.items()}),
                "targets": {"root-app": target(root), "argocd": target(argo),
                            "opensearch-values": target(os_app, 1), "fluentd": target(applications["fluentd"])},
                "operation_hashes": {
                    "root-app": digest(operation_identity(root)),
                    "opensearch": digest(operation_identity(os_app)),
                },
                "revisions": {
                    "root-requested": operation_identity(root)["requestedRevisions"],
                    "root-result": operation_identity(root)["resultRevisions"],
                    "opensearch-requested": operation_identity(os_app)["requestedRevisions"],
                    "opensearch-result": operation_identity(os_app)["resultRevisions"],
                    "app-config": status_revisions(applications["app-config"]),
                },
                "source_counts": {"v2": 3, "old": 29},
                "schema_job_absence": "exact-NotFound:logging/fluentd-log-schema",
            }
            return True
        except (KeyError, IndexError, TransitionError):
            return False

    def converge(self, deadline):
        while True:
            applications = self.applications(deadline)
            if self.final_passes(applications, deadline):
                self.evidence.write(
                    "control-plane-closed", revision=self.config.runtime_commit,
                    deadline_seconds=CONVERGENCE_SECONDS, result="pass", **self.final_facts)
                return
            deadline.sleep()

    def execute(self):
        preflight_deadline = Deadline(self.clock, BARRIER_SECONDS, "preflight")
        self.preflight(preflight_deadline)
        collector = None
        if self.config.collect_retained:
            collector = RetainedCollector(self, self.config, self.clock, self.evidence)
            collector.baseline(preflight_deadline)
        barrier_deadline = Deadline(self.clock, BARRIER_SECONDS, "barrier")
        try:
            self.stop_controller(barrier_deadline)
            applications = self.barrier_recheck(barrier_deadline)
            self.close_owners(applications, barrier_deadline)
            self.final_stopped_recheck(barrier_deadline)
            self.restore_controller(barrier_deadline)
        except BaseException as primary:
            recovery_deadline = Deadline(self.clock, ROLLBACK_SECONDS, "rollback")
            if self.phase in {"stop-attempted", "restore-attempted"}:
                try:
                    self.classify_ambiguous_controller_patch(recovery_deadline)
                except BaseException as classification_error:
                    raise TransitionError(
                        f"{redact(primary)}; ambiguous patch readback: {redact(classification_error)}"
                    ) from primary
            if self.phase == "stopped":
                try:
                    self.rollback(recovery_deadline)
                except BaseException as rollback_error:
                    raise TransitionError(f"{redact(primary)}; rollback: {redact(rollback_error)}") from primary
            if isinstance(primary, TransitionError):
                raise
            raise TransitionError(redact(primary)) from primary
        self.converge(Deadline(self.clock, CONVERGENCE_SECONDS, "convergence"))
        if collector is not None:
            collector.observe()


def deepcopy_json(value):
    return json.loads(json.dumps(value))


def validate_local_config(config):
    if config.mode not in {"retained-transition", "clean-bootstrap-accept"}:
        raise TransitionError("explicit supported Issue #350 mode is required")
    if (config.remote_url != CORE_REPO or config.runtime_tag != RUNTIME_TAG or
            config.old_tag != OLD_TAG or config.old_commit != OLD_COMMIT):
        raise TransitionError("fixed Issue #350 identity literals do not match")
    scalar_inputs = (
        config.context, config.expected_server, config.expected_kube_system_uid,
        config.remote_url, config.runtime_tag, config.runtime_commit,
        config.old_tag, config.old_commit,
    )
    if any(str(value).startswith("-") for value in scalar_inputs):
        raise TransitionError("option-like input is forbidden")
    if not config.context.strip():
        raise TransitionError("explicit non-default context is required")
    if (config.mode == "clean-bootstrap-accept" and
            (config.context != "kind-digiorg-core-dev" or
             config.kubeconfig.name != "kubeconfig-local.yaml")):
        raise TransitionError("clean-bootstrap requires the exact KinD context and up-generated kubeconfig")
    try:
        if config.kubeconfig.resolve() == (Path.home() / ".kube" / "config").resolve():
            raise TransitionError("default kubeconfig is forbidden")
        file_stat = config.kubeconfig.stat()
    except FileNotFoundError as error:
        raise TransitionError("explicit kubeconfig does not exist") from error
    if not stat.S_ISREG(file_stat.st_mode) or stat.S_IMODE(file_stat.st_mode) & 0o077:
        raise TransitionError("kubeconfig must be a regular file with mode 0600 or stricter")
    if not re.fullmatch(r"[0-9a-f]{40}", config.runtime_commit) or not re.fullmatch(r"[0-9a-f]{40}", config.old_commit):
        raise TransitionError("runtime and old commits must be exact 40-character lowercase SHAs")
    if config.mode == "retained-transition":
        if not isinstance(config.expected_server, str) or not config.expected_server.startswith("https://") or not config.expected_kube_system_uid:
            raise TransitionError("expected cluster server and kube-system UID are required")
    if config.evidence.exists():
        raise TransitionError("evidence path must not exist")
    if config.mode == "clean-bootstrap-accept" and config.collect_retained:
        raise TransitionError("clean-bootstrap mode cannot execute the retained collector")
    if config.mode == "retained-transition" and config.collect_retained:
        names = (config.current_primary_index, config.representative_log_index,
                 config.representative_jaeger_index)
        if any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", value) for value in names):
            raise TransitionError("retained index names must be explicit safe path segments")
        if (not config.representative_log_index.startswith("digiorg-logs-") or
                not config.representative_jaeger_index.startswith("jaeger-")):
            raise TransitionError("representative log and Jaeger index names are not exact families")


def execute(config, *, runner=None, clock=None):
    validate_local_config(config)
    clock = clock or RealClock()
    runner = runner or RealRunner()
    Protocol(config, runner, clock, None).validate_local_checkout(
        Deadline(clock, BARRIER_SECONDS, "local checkout validation"))
    evidence = Evidence(config.evidence, clock)
    protocol = Protocol(config, runner, clock, evidence)
    try:
        if config.mode == "clean-bootstrap-accept":
            protocol.validate_remote(Deadline(clock, BARRIER_SECONDS, "remote tag validation"))
            CleanBootstrapAcceptance(protocol, config, clock, evidence).observe()
        else:
            protocol.execute()
    except BaseException as error:
        try:
            evidence.write("failure", result="failed", error=redact(error))
        finally:
            evidence.close()
        if isinstance(error, TransitionError):
            raise
        raise TransitionError(redact(error)) from error
    evidence.close()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True,
                        choices=("retained-transition", "clean-bootstrap-accept"))
    parser.add_argument("--kubeconfig", required=True, type=Path)
    parser.add_argument("--context", required=True)
    parser.add_argument("--expected-server")
    parser.add_argument("--expected-kube-system-uid")
    parser.add_argument("--remote-url", required=True)
    parser.add_argument("--runtime-tag", required=True)
    parser.add_argument("--runtime-commit", required=True)
    parser.add_argument("--old-tag", default=OLD_TAG)
    parser.add_argument("--old-commit", default=OLD_COMMIT)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--current-primary-index")
    parser.add_argument("--representative-log-index")
    parser.add_argument("--representative-jaeger-index")
    args = parser.parse_args(argv)
    values = vars(args)
    values["collect_retained"] = args.mode == "retained-transition"
    return Config(**values)


def main(argv=None):
    config = parse_args(argv)
    interrupted = {"signal": None}

    def handle_signal(signum, _frame):
        interrupted["signal"] = signum
        raise KeyboardInterrupt(f"signal {signum}")

    previous = {}
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        previous[signum] = signal.signal(signum, handle_signal)
    try:
        execute(config)
    except TransitionError as error:
        print(f"transition failed: {redact(error)}", file=sys.stderr)
        return 130 if interrupted["signal"] else 1
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
