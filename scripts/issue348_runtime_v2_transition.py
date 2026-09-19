#!/usr/bin/env python3
"""Deterministic Issue #348 runtime-v2 control-plane transition.

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
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import NamedTuple

_SCRIPT_DIRECTORY = str(Path(__file__).resolve().parent)
if _SCRIPT_DIRECTORY not in sys.path:
    sys.path.insert(0, _SCRIPT_DIRECTORY)

from issue348_runtime_v2_mutator import Mutator, MutationRejected
from issue348_runtime_v2_contract import CONTRACT_PATH, load_contract
from issue348_runtime_v2_qualification import (
    MutationPlan,
    QualificationError,
    ReadResult,
    SnapshotDecoder,
    Validator,
)

BARRIER_SECONDS = 300
ROLLBACK_SECONDS = 300
CONVERGENCE_SECONDS = 1200
CONVERGENCE_STABLE_SAMPLES = 3
CONVERGENCE_SAMPLE_SECONDS = 5
CALL_SECONDS = 20
WAIT_SECONDS = 120
POLL_SECONDS = 5
ARGOCD_NAMESPACE = "argocd"
FLUENTD_NAMESPACE = "logging"
CONTROLLER_LABEL = "app.kubernetes.io/name=argocd-application-controller"
CONTROLLER_NAME = "argocd-application-controller"
CORE_REPO = "https://github.com/digiorg/core.git"
RUNTIME_TAG = "issue348-runtime-v6-20260919T100440Z"
PRODUCT_BASE_COMMIT = "ff25a5083059412f82525ace73e7c20b322fddbf"
CANDIDATE_BASE_COMMIT = "b32d1c18eb0d1048d8e38743f5fdd1c68a72936d"
PREVIOUS_TAG = "issue350-352-runtime-v3-20260904T195619Z"
PREVIOUS_COMMIT = "f6e7d58c0b03ee6a3ec6ed9e1e22e5023f861549"
OLD_TAG = "issue301-runtime-v16-20260817T130820Z"
OLD_COMMIT = "8e6b8908f99ebf76db47c15613eff523644c23f6"
CATALOG_REVISION = "d531180b322dc0128477ecb9bb0fc9071b41d631"
APP_CONFIG_REPO = "https://digiorg.local/gitea/DigiOrg/app-config.git"
CATALOG_REPO = "https://github.com/digiorg/core-catalog.git"
OPENSEARCH_IMAGE = "opensearchproject/opensearch:3.7.0@sha256:44ba7ea58a319adf61c33ab16873f9ef5dbb30b291a832d375172f0b2d24e3c9"
ACTIVE_PHASES = {"Running", "Terminating"}
COMPLETED_PHASES = {"Succeeded", "Failed", "Error"}
KYVERNO_SAFE_OUT_OF_SYNC_CRDS = {
    "deletingpolicies.policies.kyverno.io",
    "generatingpolicies.policies.kyverno.io",
    "imagevalidatingpolicies.policies.kyverno.io",
    "mutatingpolicies.policies.kyverno.io",
    "namespaceddeletingpolicies.policies.kyverno.io",
    "namespacedgeneratingpolicies.policies.kyverno.io",
    "namespacedimagevalidatingpolicies.policies.kyverno.io",
    "namespacedmutatingpolicies.policies.kyverno.io",
    "namespacedvalidatingpolicies.policies.kyverno.io",
    "policyexceptions.policies.kyverno.io",
    "validatingpolicies.policies.kyverno.io",
}


def _resource_contract(group, kind, names, namespace="", status="Synced"):
    return {(group, "v1", kind, namespace, name): status for name in names}


KYVERNO_EXPECTED_RESOURCE_STATUS = {}
KYVERNO_EXPECTED_RESOURCE_STATUS.update(_resource_contract("", "ConfigMap", (
    "kyverno", "kyverno-metrics",
), "kyverno"))
KYVERNO_EXPECTED_RESOURCE_STATUS.update(_resource_contract("", "Service", (
    "kyverno-background-controller-metrics", "kyverno-cleanup-controller",
    "kyverno-cleanup-controller-metrics", "kyverno-reports-controller-metrics",
    "kyverno-svc", "kyverno-svc-metrics",
), "kyverno"))
_KYVERNO_CONTROLLERS = (
    "kyverno-admission-controller", "kyverno-background-controller",
    "kyverno-cleanup-controller", "kyverno-reports-controller",
)
KYVERNO_EXPECTED_RESOURCE_STATUS.update(
    _resource_contract("", "ServiceAccount", _KYVERNO_CONTROLLERS, "kyverno")
)
KYVERNO_EXPECTED_RESOURCE_STATUS.update(_resource_contract(
    "apiextensions.k8s.io", "CustomResourceDefinition", (
        "cleanuppolicies.kyverno.io", "clustercleanuppolicies.kyverno.io",
        "clusterephemeralreports.reports.kyverno.io", "clusterpolicies.kyverno.io",
        "clusterpolicyreports.wgpolicyk8s.io", "ephemeralreports.reports.kyverno.io",
        "globalcontextentries.kyverno.io", "policies.kyverno.io",
        "policyexceptions.kyverno.io", "policyreports.wgpolicyk8s.io",
        "updaterequests.kyverno.io",
    )
))
KYVERNO_EXPECTED_RESOURCE_STATUS.update(_resource_contract(
    "apiextensions.k8s.io", "CustomResourceDefinition",
    KYVERNO_SAFE_OUT_OF_SYNC_CRDS, status="OutOfSync",
))
KYVERNO_EXPECTED_RESOURCE_STATUS.update(
    _resource_contract("apps", "Deployment", _KYVERNO_CONTROLLERS, "kyverno")
)
KYVERNO_EXPECTED_RESOURCE_STATUS.update(_resource_contract(
    "rbac.authorization.k8s.io", "ClusterRole", (
        "kyverno:admission-controller", "kyverno:admission-controller:core",
        "kyverno:background-controller", "kyverno:background-controller:core",
        "kyverno:cleanup-controller", "kyverno:cleanup-controller:core",
        "kyverno:rbac:admin:policies", "kyverno:rbac:admin:policyreports",
        "kyverno:rbac:admin:reports", "kyverno:rbac:admin:updaterequests",
        "kyverno:rbac:view:policies", "kyverno:rbac:view:policyreports",
        "kyverno:rbac:view:reports", "kyverno:rbac:view:updaterequests",
        "kyverno:reports-controller", "kyverno:reports-controller:core",
    )
))
KYVERNO_EXPECTED_RESOURCE_STATUS.update(_resource_contract(
    "rbac.authorization.k8s.io", "ClusterRoleBinding", (
        "kyverno:admission-controller", "kyverno:admission-controller:view",
        "kyverno:background-controller", "kyverno:background-controller:view",
        "kyverno:cleanup-controller", "kyverno:reports-controller",
        "kyverno:reports-controller:view",
    )
))
for _kind in ("Role", "RoleBinding"):
    KYVERNO_EXPECTED_RESOURCE_STATUS.update(_resource_contract(
        "rbac.authorization.k8s.io", _kind, (
            "kyverno:admission-controller", "kyverno:background-controller",
            "kyverno:cleanup-controller", "kyverno:reports-controller",
        ), "kyverno",
    ))
if len(KYVERNO_EXPECTED_RESOURCE_STATUS) != 69:
    raise RuntimeError("Issue #348 Kyverno resource contract must contain 69 identities")
KYVERNO_ANNOTATION = {"platform.digiorg.io/component": "kyverno-api"}
KYVERNO_V3_METADATA_BLOCK = """        # The kyverno-api subchart otherwise emits labels: {} on policy-v2
        # CRDs, which kube-apiserver normalizes away and Argo CD reports as
        # perpetual drift. Render one stable label instead of ignoring metadata.
        kyverno-api:
          labels:
            app.kubernetes.io/managed-by: kyverno
"""
KYVERNO_V4_METADATA_BLOCK = """        # The kyverno-api subchart otherwise emits labels: {} and annotations: {}
        # on policy-v2 CRDs. Kube-apiserver normalizes those empty maps away,
        # which Argo CD reports as perpetual drift. Render stable metadata instead
        # of ignoring labels or annotations.
        kyverno-api:
          labels:
            app.kubernetes.io/managed-by: kyverno
          annotations:
            platform.digiorg.io/component: kyverno-api
"""
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
    "fluentd": [(CORE_REPO, None, "platform/base/fluentd", None, RUNTIME_TAG)],
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
    "opensearch": [("https://opensearch-project.github.io/helm-charts", "opensearch", None, None, "3.7.0"), (CORE_REPO, None, None, "values", RUNTIME_TAG), (CORE_REPO, None, "platform/base/opensearch", None, RUNTIME_TAG)],
    "postgresql": [(CORE_REPO, None, "platform/base/postgresql", None, OLD_TAG)],
    "root-app": [(CORE_REPO, None, "apps", None, RUNTIME_TAG)],
    "sonarqube": [("https://SonarSource.github.io/helm-chart-sonarqube", "sonarqube", None, None, "2026.3.1"), (CORE_REPO, None, None, "values", OLD_TAG), (CORE_REPO, None, "platform/base/sonarqube", None, OLD_TAG)],
}
PREFLIGHT_SOURCE_GRAPH = {
    name: [identity[:-1] + (PREVIOUS_TAG if identity[-1] == RUNTIME_TAG else identity[-1],)
           for identity in identities]
    for name, identities in CLEAN_SOURCE_GRAPH.items()
}
PREFLIGHT_SOURCE_GRAPH["fluentd"][0] = (
    CORE_REPO, None, "platform/base/fluentd", None, OLD_TAG,
)
PREFLIGHT_SOURCE_GRAPH["opensearch"][2] = (
    CORE_REPO, None, "platform/base/opensearch", None, OLD_TAG,
)
TRACKED_RUNTIME_FILES = (
    "scripts/issue348_runtime_v2_transition.py",
    "scripts/issue348_runtime_v2_contract.py",
    "scripts/issue348_runtime_v2_mutator.py",
    "scripts/issue348_runtime_v2_qualification.py",
    "specs/345-log-schema-isolation/issue348-runtime-v2-contract.json",
    "specs/345-log-schema-isolation/runtime-v2-transition.md",
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
    "mode", "stable_samples",
    "statefulset_uid", "pvc_uid", "volume_name", "capacity", "index_uid_hashes",
    "pvc_spec_hash",
    "pod_restart_count", "memory_current", "memory_max", "memory_events_max",
    "last_termination_reason", "last_termination_time",
    "sample", "queue_lengths", "log_count", "jaeger_count", "template_hash",
    "snapshot_digest", "proposed_mutation_plan", "runtime_mutated", "mutation_trace",
    "process_invocation_count", "transition_execution_count", "first_mutation_count",
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
                 runtime_commit, previous_tag, previous_commit,
                 old_tag, old_commit, evidence, mode="retained-convergence"):
        self.mode = mode
        self.kubeconfig = Path(kubeconfig)
        self.context = context
        self.expected_server = expected_server
        self.expected_kube_system_uid = expected_kube_system_uid
        self.remote_url = remote_url
        self.runtime_tag = runtime_tag
        self.runtime_commit = runtime_commit
        self.previous_tag = previous_tag
        self.previous_commit = previous_commit
        self.old_tag = old_tag
        self.old_commit = old_commit
        self.evidence = Path(evidence)



class RealRunner:
    def run(self, argv, timeout, env=None):
        try:
            completed = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
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
    return {"phase": state.get("phase"), "startedAt": state.get("startedAt"),
            "finishedAt": state.get("finishedAt"), "requestedRevisions": requested,
            "resultRevisions": result}


def valid_revision_list(value):
    return (isinstance(value, list) and bool(value) and
            all(isinstance(item, str) and bool(item) for item in value))


def source_list(application):
    spec = application["spec"]
    return spec.get("sources", [spec.get("source")])


def kyverno_candidate_values(previous):
    if not isinstance(previous, str) or previous.count(KYVERNO_V3_METADATA_BLOCK) != 1:
        raise TransitionError("Kyverno previous Helm values are not the exact v3 metadata block")
    candidate = previous.replace(KYVERNO_V3_METADATA_BLOCK, KYVERNO_V4_METADATA_BLOCK)
    if candidate.count("platform.digiorg.io/component: kyverno-api") != 1:
        raise TransitionError("Kyverno candidate annotation is not exact")
    return candidate


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
    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise TransitionError(f"retained API returned duplicate JSON key: {key}")
            value[key] = item
        return value

    try:
        value = json.loads(text, object_pairs_hook=unique_object)
    except (TypeError, json.JSONDecodeError) as error:
        raise TransitionError("retained API returned invalid JSON") from error
    if not isinstance(value, expected_type):
        raise TransitionError("retained API returned wrong JSON shape")
    return value


_API_GROUP_RE = re.compile(
    r"(?:[a-z0-9](?:[-a-z0-9]*[a-z0-9])?)(?:\.(?:[a-z0-9](?:[-a-z0-9]*[a-z0-9])?))*"
)
_API_VERSION_RE = re.compile(r"v[1-9][0-9]*(?:(?:alpha|beta)[1-9][0-9]*)?")
_KIND_RE = re.compile(r"[A-Z][A-Za-z0-9]*")


def canonical_kyverno_resource(resource):
    """Return canonical identity/status for one typed Argo resource status."""
    allowed = {"group", "version", "kind", "namespace", "name", "status", "health"}
    if not isinstance(resource, dict) or set(resource) - allowed:
        raise TransitionError("Kyverno resource identity/status mismatch")
    raw_group = resource.get("group")
    if raw_group in (None, ""):
        group = ""
    elif isinstance(raw_group, str) and _API_GROUP_RE.fullmatch(raw_group):
        group = raw_group
    else:
        raise TransitionError("Kyverno resource identity/status mismatch")
    version = resource.get("version")
    kind = resource.get("kind")
    name = resource.get("name")
    namespace = resource.get("namespace")
    status = resource.get("status")
    if (not isinstance(version, str) or not _API_VERSION_RE.fullmatch(version) or
            group == "" and version != "v1" or
            not isinstance(kind, str) or not _KIND_RE.fullmatch(kind) or
            not isinstance(name, str) or not name or
            namespace is not None and not isinstance(namespace, str) or
            not isinstance(status, str) or status not in {"Synced", "OutOfSync"} or
            resource.get("health") is not None and
            not isinstance(resource.get("health"), dict)):
        raise TransitionError("Kyverno resource identity/status mismatch")
    identity = (group, version, kind, namespace or "", name)
    return identity, status


def argocd_diff_argv(application):
    if not isinstance(application, str) or not application or application.startswith("-"):
        raise TransitionError("Argo CD Application name is invalid")
    return ["argocd", "app", "diff", application, "--core", "--refresh"]


def named_container(pod_or_template, name):
    containers = pod_or_template.get("spec", {}).get("containers", [])
    matches = [item for item in containers if item.get("name") == name]
    if len(matches) != 1:
        raise TransitionError(f"expected exactly one {name} container")
    return matches[0]


def checkout_path_allowed(_label, resolved, root, _mode):
    """Runtime inputs and evidence must never be inside the reviewed checkout."""
    resolved, root = Path(resolved).resolve(), Path(root).resolve()
    return resolved != root and root not in resolved.parents


def qualification_contract(config):
    """Return the immutable committed contract consumed by the pure Validator."""
    return load_contract(CONTRACT_PATH, config)


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
        self.snapshot = None
        self.validated_snapshot = None
        self.mutation_plan = None
        self.mutator = None
        self.process_invocation_count = 0
        self.transition_execution_count = 0
        self.first_mutation_count = 0

    def run_command(self, argv, deadline, *, expect_not_found=None):
        self.process_invocation_count += 1
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

    def validate_remote(self, deadline):
        identities = (
            (self.config.runtime_tag, self.config.runtime_commit),
            (self.config.previous_tag, self.config.previous_commit),
            (self.config.old_tag, self.config.old_commit),
        )
        for tag, commit in identities:
            ref = f"refs/tags/{tag}"
            peeled_ref = f"{ref}^{{}}"
            output = self.run_command(
                ["git", "ls-remote", "--tags", self.config.remote_url, ref, peeled_ref],
                deadline,
            )
            if not isinstance(output, str):
                raise TransitionError("remote tag lookup returned invalid output")
            refs = {}
            for line in output.splitlines():
                fields = line.split("\t")
                if len(fields) == 2:
                    refs[fields[1]] = fields[0]
            if (set(refs) != {ref, peeled_ref} or
                    not re.fullmatch(r"[0-9a-f]{40}", refs.get(ref, "")) or
                    refs.get(peeled_ref) != commit):
                raise TransitionError(
                    "remote tag is not annotated or does not peel to exact commit"
                )
        return identities

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
        parent = self.run_command(["git", "rev-parse", "HEAD^"], deadline)
        if parent not in {CANDIDATE_BASE_COMMIT, CANDIDATE_BASE_COMMIT + "\n"}:
            raise TransitionError("runtime commit is not based directly on the authorized Issue #348 v5 base")
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
        output = self.kubectl(
            ["get", "--raw", "/apis/argoproj.io/v1alpha1/namespaces/argocd/applications"],
            deadline,
        )
        listing = exact_json(output, dict)
        items = listing.get("items")
        if (listing.get("apiVersion") != "argoproj.io/v1alpha1" or
                listing.get("kind") != "ApplicationList" or
                not isinstance(items, list) or len(items) > 100):
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
            if application.get("operation") is not None or "operation" in application.get("spec", {}):
                raise TransitionError(f"Application {name} has a pending operation")
            phase = application.get("status", {}).get("operationState", {}).get("phase")
            if phase in ACTIVE_PHASES:
                raise TransitionError(f"Application {name} has active operation")

    @staticmethod
    def require_final_healthy_synced(applications):
        for name, application in applications.items():
            status = application.get("status", {})
            if status.get("health", {}).get("status") != "Healthy" or status.get("sync", {}).get("status") != "Synced":
                raise TransitionError(f"Application {name} is not exact Healthy/Synced")

    @staticmethod
    def require_preflight_status(applications):
        for name, application in applications.items():
            status = application.get("status", {})
            if status.get("health", {}).get("status") != "Healthy":
                raise TransitionError(f"Application {name} is not exact Healthy")
            sync = status.get("sync", {}).get("status")
            if name != "kyverno":
                if sync != "Synced":
                    raise TransitionError(f"unexpected non-synced Application {name}")
                continue
            if sync != "OutOfSync":
                raise TransitionError("Kyverno must be the exact retained OutOfSync exception")
            resources = status.get("resources")
            if not isinstance(resources, list):
                raise TransitionError("Kyverno non-synced resource inventory mismatch")
            actual = {}
            for resource in resources:
                identity, resource_status = canonical_kyverno_resource(resource)
                if identity in actual:
                    raise TransitionError("Kyverno resource inventory has duplicate identity")
                actual[identity] = resource_status
            if actual != KYVERNO_EXPECTED_RESOURCE_STATUS:
                raise TransitionError("Kyverno complete resource inventory mismatch")

    def require_preflight_graph(self, applications):
        required = {"root-app", "argocd", "opensearch", "fluentd", "app-config", "core-catalog"}
        if not required <= set(applications):
            raise TransitionError("required retained Applications are missing")
        actual_graph = {
            name: [source_identity(item) for item in source_list(application)]
            for name, application in applications.items()
        }
        if actual_graph != PREFLIGHT_SOURCE_GRAPH:
            raise TransitionError("complete retained source graph mismatch")
        root_source = source_list(applications["root-app"])[0]
        argo_source = source_list(applications["argocd"])[0]
        if source_identity(root_source) != (CORE_REPO, None, "apps", None, self.config.previous_tag):
            raise TransitionError("root-app retained graph mismatch")
        if source_identity(argo_source) != (CORE_REPO, None, "platform/base/argocd", None, self.config.previous_tag):
            raise TransitionError("argocd retained graph mismatch")
        os_sources = source_list(applications["opensearch"])
        expected_os = [
            ("https://opensearch-project.github.io/helm-charts", "opensearch", None, None, "3.7.0"),
            (CORE_REPO, None, None, "values", self.config.previous_tag),
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
        expected_revisions = {
            "root-app": [self.config.previous_commit],
            "argocd": [self.config.previous_commit],
            "opensearch": ["3.7.0", self.config.previous_commit, self.config.old_commit],
            "fluentd": [self.config.old_commit],
        }
        if any(status_revisions(applications[name]) != revisions
               for name, revisions in expected_revisions.items()):
            raise TransitionError("retained status revisions do not match exact commits")
        core_targets = [item.get("targetRevision") for application in applications.values()
                        for item in source_list(application)
                        if item and item.get("repoURL") == CORE_REPO]
        if (len(core_targets) != 32 or core_targets.count(self.config.previous_tag) != 3 or
                core_targets.count(self.config.old_tag) != 29 or
                set(core_targets) != {self.config.previous_tag, self.config.old_tag}):
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
        items = listing.get("items") if isinstance(listing, dict) else None
        if (not isinstance(listing, dict) or listing.get("apiVersion") != "autoscaling/v2" or
                listing.get("kind") != "HorizontalPodAutoscalerList" or
                not isinstance(listing.get("metadata"), dict) or
                not isinstance(items, list) or len(items) > 1000):
            raise TransitionError("HPA list shape is invalid")
        for item in items:
            metadata = item.get("metadata") if isinstance(item, dict) else None
            spec = item.get("spec") if isinstance(item, dict) else None
            ref = spec.get("scaleTargetRef") if isinstance(spec, dict) else None
            if (not isinstance(item, dict) or item.get("apiVersion") != "autoscaling/v2" or
                    item.get("kind") != "HorizontalPodAutoscaler" or
                    not isinstance(metadata, dict) or
                    not all(isinstance(metadata.get(key), str) and metadata[key]
                            for key in ("name", "namespace")) or
                    not isinstance(spec, dict) or not isinstance(ref, dict) or
                    not all(isinstance(ref.get(key), str) and ref[key]
                            for key in ("apiVersion", "kind", "name"))):
                raise TransitionError("HPA list shape is invalid")
            api_group = ref["apiVersion"].split("/", 1)[0].lower()
            if (metadata["namespace"] == ARGOCD_NAMESPACE and
                    api_group == "apps" and ref["kind"].lower() == "statefulset" and
                    ref["name"] == CONTROLLER_NAME):
                raise TransitionError("HPA targets application controller")
        return listing

    def controller_pods(self, deadline):
        listing = self.get_json(["get", "pods", "-l", CONTROLLER_LABEL], deadline, ARGOCD_NAMESPACE)
        return listing.get("items", [])

    def kyverno_material_diff(self, deadline):
        isolated_path = None
        try:
            fd, raw_path = tempfile.mkstemp(
                prefix="issue348-argocd-kubeconfig-", suffix=".yaml"
            )
            os.close(fd)
            isolated_path = Path(raw_path)
            checkout_root = Path(__file__).resolve().parents[1]
            if not checkout_path_allowed(
                    "isolated kubeconfig", isolated_path, checkout_root, self.config.mode):
                raise TransitionError("isolated kubeconfig path must be outside repository root")
            shutil.copyfile(self.config.kubeconfig, isolated_path)
            isolated_path.chmod(0o600)

            request_seconds = max(
                1, min(CALL_SECONDS, int(max(1, deadline.remaining())))
            )
            kubectl_prefix = [
                "kubectl", "--kubeconfig", str(isolated_path),
                f"--request-timeout={request_seconds}s", "config",
            ]
            self.run_command(
                [*kubectl_prefix, "use-context", self.config.context], deadline
            )
            self.run_command(
                [*kubectl_prefix, "set-context", "--current", "--namespace=argocd"],
                deadline,
            )

            env = os.environ.copy()
            env["KUBECONFIG"] = str(isolated_path)
            try:
                self.process_invocation_count += 1
                return self.runner.run(
                    argocd_diff_argv("kyverno"),
                    timeout=deadline.call_timeout(),
                    env=env,
                )
            except (TimeoutError, OSError) as error:
                raise TransitionError(redact(error)) from error
        except OSError as error:
            raise TransitionError("unable to prepare isolated Argo CD kubeconfig") from error
        finally:
            if isolated_path is not None:
                try:
                    isolated_path.unlink(missing_ok=True)
                except OSError as error:
                    raise TransitionError(
                        "unable to remove isolated Argo CD kubeconfig"
                    ) from error

    def preflight(self, deadline):
        remote_tags = self.validate_remote(deadline)
        view = self.get_json(["config", "view", "--minify"], deadline)
        servers = [item.get("cluster", {}).get("server") for item in view.get("clusters", [])]
        namespace = self.get_json(["get", "namespace", "kube-system"], deadline)
        if servers != [self.config.expected_server] or namespace.get("metadata", {}).get("uid") != self.config.expected_kube_system_uid:
            raise TransitionError("cluster identity does not match expected server and namespace UID")
        applications = self.applications(deadline)
        if tuple(sorted(applications)) != EXPECTED_APPLICATIONS:
            raise TransitionError("Application inventory does not equal reviewed 32-name set")
        self.require_no_active(applications)
        self.require_preflight_status(applications)
        self.require_preflight_graph(applications)
        diff = self.kyverno_material_diff(deadline)
        if diff.returncode != 0:
            raise TransitionError(f"Kyverno material diff command failed: {redact(diff.stderr)}")
        if diff.stdout != "" or diff.stderr != "":
            raise TransitionError("Kyverno material diff output is not exactly zero bytes")
        baseline_operations = {}
        for name, application in applications.items():
            identity = operation_identity(applications[name])
            try:
                valid = (identity is not None and identity["phase"] in COMPLETED_PHASES and
                         rfc3339(identity["startedAt"]) and rfc3339(identity["finishedAt"]) and
                         valid_revision_list(identity["requestedRevisions"]) and
                         valid_revision_list(identity["resultRevisions"]))
            except (KeyError, TypeError, ValueError):
                valid = False
            required = name in {"root-app", "argocd", "opensearch", "kyverno", "fluentd"}
            has_marker = "operationState" in application.get("status", {})
            if (required or has_marker) and not valid:
                raise TransitionError(f"{name} prior operation identity is required")
            baseline_operations[name] = identity
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
        hpa_listing = self.hpa_absent(deadline)
        try:
            snapshot = SnapshotDecoder.snapshot(
                remote_tags=remote_tags,
                cluster_server=servers[0] if len(servers) == 1 else "",
                kube_system_uid=namespace.get("metadata", {}).get("uid"),
                application_list={
                    "apiVersion": "argoproj.io/v1alpha1",
                    "kind": "ApplicationList",
                    "items": list(applications.values()),
                },
                controller=controller,
                controller_pods=pods,
                hpa_list=hpa_listing,
                argocd_diff=ReadResult(diff.returncode, diff.stdout, diff.stderr),
                invocation=(
                    "issue348_runtime_v2_transition.py", "--mode", self.config.mode,
                    "--context", self.config.context,
                ),
            )
            validated = Validator.validate(snapshot, qualification_contract(self.config))
            plan = MutationPlan.build(validated, qualification_contract(self.config))
        except QualificationError as error:
            raise TransitionError(str(error)) from error
        self.snapshot = snapshot
        self.validated_snapshot = validated
        self.mutation_plan = plan
        self.mutator = Mutator(plan, self.kubectl, kubectl_adapter=True)
        self.controller = deepcopy_json(controller)
        self.old_pod_uids = {pod["metadata"]["uid"] for pod in pods}
        app_config_revision = resolved_commit(applications["app-config"])
        self.baseline = {
            "specs": {name: deepcopy_json(item["spec"]) for name, item in applications.items()},
            "spec_hashes": {name: digest(item["spec"]) for name, item in applications.items()},
            "uids": {name: item["metadata"]["uid"] for name, item in applications.items()},
            "resource_versions": {name: applications[name]["metadata"]["resourceVersion"] for name in ("root-app", "argocd")},
            "operations": baseline_operations,
            "app_config_revision": app_config_revision,
            "opensearch_preflight_reconciled": applications["opensearch"].get("status", {}).get("reconciledAt"),
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
            revisions={name: value["requestedRevisions"] for name, value in self.baseline["operations"].items()
                       if value is not None},
            current_revision=status["currentRevision"], update_revision=status["updateRevision"],
            snapshot_digest=snapshot.digest,
            proposed_mutation_plan=json.loads(plan.render()), runtime_mutated=False,
            mutation_trace=[], process_invocation_count=self.process_invocation_count,
            transition_execution_count=self.transition_execution_count,
            first_mutation_count=self.first_mutation_count,
            deadline_seconds=BARRIER_SECONDS, result="pass",
        )

    def stop_controller(self, deadline):
        controller = self.controller
        if self.mutator is None:
            raise TransitionError("mutation is forbidden before validation and plan generation")
        self.phase = "stop-attempted"
        self.first_mutation_count += 1
        try:
            self.mutator.execute("controller-barrier", deadline)
        except MutationRejected as error:
            raise TransitionError(str(error)) from error
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
        self.evidence.write(
            "controller-stopped", uid=controller["metadata"]["uid"], replicas=0,
            process_invocation_count=self.process_invocation_count,
            transition_execution_count=self.transition_execution_count,
            first_mutation_count=self.first_mutation_count,
            deadline_seconds=WAIT_SECONDS, result="pass",
        )

    def barrier_recheck(self, deadline):
        self.hpa_absent(deadline)
        current_controller = self.get_json(
            ["get", "statefulsets.apps", CONTROLLER_NAME], deadline, ARGOCD_NAMESPACE)
        if (current_controller.get("metadata", {}).get("uid") != self.controller["metadata"]["uid"] or
                controller_non_replica_spec(current_controller) != self.baseline["controller_spec"]):
            raise TransitionError("controller UID or non-replica spec changed at barrier")
        applications = self.applications(deadline)
        self.require_no_active(applications)
        self.require_preflight_status(applications)
        self.require_preflight_graph(applications)
        self.require_application_closure(applications, self.baseline["specs"], "barrier")
        for name in applications:
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
            operation_hashes={name: digest(operation_identity(applications[name])) for name in applications},
            deadline_seconds=BARRIER_SECONDS, result="pass",
        )
        return applications

    def app_patch(self, name, old_application, new_revision, deadline):
        if self.mutator is None:
            raise TransitionError("mutation is forbidden before validation and plan generation")
        try:
            if new_revision == self.config.runtime_tag:
                operation_name = "root-owner" if name == "root-app" else "argocd-owner"
                return self.mutator.execute(operation_name, deadline, old_application)
            if new_revision == self.config.previous_tag:
                operation_name = (
                    "rollback-root-owner" if name == "root-app"
                    else "rollback-argocd-owner"
                )
                return self.mutator.rollback(
                    operation_name, deadline, old_application
                )
        except MutationRejected as error:
            raise TransitionError(str(error)) from error
        raise TransitionError("owner revision is absent from the approved plan")

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
        for name in applications:
            if operation_identity(applications[name]) != self.baseline["operations"][name]:
                raise TransitionError(f"last stopped gate operation identity changed for {name}")
        if resolved_commit(applications["app-config"]) != self.baseline["app_config_revision"]:
            raise TransitionError("last stopped gate app-config resolved revision changed")

    def restore_controller(self, deadline, *, rollback=False):
        prior = self.controller
        current = self.get_json(["get", "statefulsets.apps", prior["metadata"]["name"]], deadline, ARGOCD_NAMESPACE)
        if (current["metadata"]["uid"] != prior["metadata"]["uid"] or
                current.get("spec", {}).get("replicas") != 0 or
                controller_non_replica_spec(current) != self.baseline["controller_spec"]):
            raise TransitionError("controller changed while stopped")
        self.phase = "restore-attempted"
        try:
            if rollback:
                self.mutator.rollback("rollback-controller", deadline, current)
            else:
                self.mutator.execute("controller-restore", deadline, current)
        except MutationRejected as error:
            raise TransitionError(str(error)) from error
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
        for name in ("root-app", "argocd"):
            try:
                current = self.get_json(["get", "applications.argoproj.io", name], deadline, ARGOCD_NAMESPACE)
                if current.get("metadata", {}).get("uid") != self.baseline["uids"][name]:
                    raise TransitionError(f"rollback rejected Application UID changed for {name}")
                current_target = target(current)
                if current_target == self.config.runtime_tag:
                    self.app_patch(name, current, self.config.previous_tag, deadline)
                elif current_target != self.config.previous_tag:
                    raise TransitionError(f"rollback rejected third target for {name}")
            except BaseException as error:
                errors.append(redact(error))
        for name in ("root-app", "argocd"):
            try:
                current = self.get_json(["get", "applications.argoproj.io", name], deadline, ARGOCD_NAMESPACE)
                if current.get("spec") != self.baseline["specs"][name]:
                    raise TransitionError(f"rollback exact old spec readback failed for {name}")
            except BaseException as error:
                errors.append(redact(error))
        try:
            self.restore_controller(deadline, rollback=True)
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
        try:
            started = rfc3339(identity.get("startedAt"))
            finished = rfc3339(identity.get("finishedAt"))
        except (TypeError, ValueError):
            return False
        return (
            identity is not None and identity != baseline_identity and
            timestamp_later(identity.get("startedAt"), baseline_identity.get("startedAt")) and
            finished >= started and
            identity.get("requestedRevisions") == revisions and
            identity.get("resultRevisions") == revisions and
            state.get("phase") == "Succeeded"
        )

    def expected_final_specs(self):
        expected = deepcopy_json(self.baseline["specs"])
        expected["root-app"]["source"]["targetRevision"] = self.config.runtime_tag
        expected["argocd"]["source"]["targetRevision"] = self.config.runtime_tag
        expected["fluentd"]["source"]["targetRevision"] = self.config.runtime_tag
        expected["opensearch"]["sources"][1]["targetRevision"] = self.config.runtime_tag
        expected["opensearch"]["sources"][2]["targetRevision"] = self.config.runtime_tag
        values = expected["kyverno"]["source"]["helm"]["values"]
        expected["kyverno"]["source"]["helm"]["values"] = kyverno_candidate_values(values)
        return expected

    def final_passes(self, applications, deadline):
        try:
            self.require_no_active(applications)
            self.require_final_healthy_synced(applications)
            root = applications["root-app"]
            argo = applications["argocd"]
            fluentd = applications["fluentd"]
            os_app = applications["opensearch"]
            kyverno = applications["kyverno"]
            self.require_application_closure(applications, self.expected_final_specs(), "final")
            if target(root) != self.config.runtime_tag or status_revisions(root) != [self.config.runtime_commit]:
                return False
            if not self.operation_passes(root, self.baseline["operations"]["root-app"], [self.config.runtime_commit]):
                return False
            if (target(argo) != self.config.runtime_tag or
                    status_revisions(argo) != [self.config.runtime_commit] or
                    not timestamp_later(argo.get("status", {}).get("reconciledAt"),
                                        self.baseline["argocd_pre_restore_reconciled"])):
                return False
            if [item.get("targetRevision") for item in source_list(os_app)] != [
                    "3.7.0", self.config.runtime_tag, self.config.runtime_tag]:
                return False
            if status_revisions(os_app) != ["3.7.0", self.config.runtime_commit, self.config.runtime_commit]:
                return False
            if not timestamp_later(os_app.get("status", {}).get("reconciledAt"),
                                   self.baseline["opensearch_preflight_reconciled"]):
                return False
            if operation_identity(os_app) != self.baseline["operations"]["opensearch"]:
                return False
            if status_revisions(kyverno) != ["3.8.1"]:
                return False
            if not self.operation_passes(kyverno, self.baseline["operations"]["kyverno"], ["3.8.1"]):
                return False
            if (target(fluentd) != self.config.runtime_tag or
                    status_revisions(fluentd) != [self.config.runtime_commit] or
                    not self.operation_passes(
                        fluentd, self.baseline["operations"]["fluentd"],
                        [self.config.runtime_commit])):
                return False
            for name, application in applications.items():
                if name not in {"root-app", "kyverno", "fluentd"} and operation_identity(application) != self.baseline["operations"][name]:
                    return False
            if (target(applications["app-config"]) != "main" or
                    resolved_commit(applications["app-config"]) != self.baseline["app_config_revision"]):
                return False
            if target(applications["core-catalog"]) != CATALOG_REVISION:
                return False
            core_targets = [item.get("targetRevision") for application in applications.values()
                            for item in source_list(application)
                            if item and item.get("repoURL") == CORE_REPO]
            counts = {
                "candidate": core_targets.count(self.config.runtime_tag),
                "old": core_targets.count(self.config.old_tag),
                "previous": core_targets.count(self.config.previous_tag),
                "other": sum(value not in {self.config.runtime_tag, self.config.old_tag,
                                            self.config.previous_tag} for value in core_targets),
            }
            if len(core_targets) != 32 or counts != {
                    "candidate": 5, "old": 27, "previous": 0, "other": 0}:
                return False
            self.kubectl(
                ["get", "job.batch", "fluentd-log-schema", "-o", "json"], deadline,
                FLUENTD_NAMESPACE,
                expect_not_found=("jobs.batch", "fluentd-log-schema"),
            )
            self.final_facts = {
                "application_count": len(applications),
                "application_spec_hash": digest({name: item["spec"] for name, item in applications.items()}),
                "targets": {"root-app": target(root), "argocd": target(argo),
                            "opensearch-values": target(os_app, 1),
                            "fluentd": target(applications["fluentd"])},
                "operation_hashes": {"root-app": digest(operation_identity(root)),
                                     "kyverno": digest(operation_identity(kyverno)),
                                     "fluentd": digest(operation_identity(fluentd))},
                "revisions": {"root-requested": operation_identity(root)["requestedRevisions"],
                              "root-result": operation_identity(root)["resultRevisions"],
                              "kyverno-requested": operation_identity(kyverno)["requestedRevisions"],
                              "kyverno-result": operation_identity(kyverno)["resultRevisions"],
                              "fluentd-requested": operation_identity(fluentd)["requestedRevisions"],
                              "fluentd-result": operation_identity(fluentd)["resultRevisions"],
                              "app-config": status_revisions(applications["app-config"])},
                "source_counts": counts,
                "schema_job_absence": "exact-NotFound:logging/fluentd-log-schema",
            }
            return True
        except (KeyError, IndexError, TransitionError):
            return False

    def converge(self, deadline):
        stable = 0
        anchor = None
        while stable < CONVERGENCE_STABLE_SAMPLES:
            applications = self.applications(deadline)
            if self.final_passes(applications, deadline):
                current = digest({
                    "uids": {name: item["metadata"]["uid"] for name, item in applications.items()},
                    "specs": {name: item["spec"] for name, item in applications.items()},
                    "status": {name: {
                        "health": item.get("status", {}).get("health"),
                        "sync": item.get("status", {}).get("sync"),
                        "reconciledAt": item.get("status", {}).get("reconciledAt"),
                        "operation": operation_identity(item),
                    } for name, item in applications.items()},
                    "facts": self.final_facts,
                })
                if current == anchor:
                    stable += 1
                else:
                    anchor = current
                    stable = 1
            else:
                anchor = None
                stable = 0
            if stable < CONVERGENCE_STABLE_SAMPLES:
                deadline.sleep(CONVERGENCE_SAMPLE_SECONDS)
        self.evidence.write(
            "control-plane-closed", revision=self.config.runtime_commit,
            mode="convergence-only-not-acceptance", stable_samples=stable,
            deadline_seconds=CONVERGENCE_SECONDS, result="pass", **self.final_facts)

    def execute(self):
        self.transition_execution_count += 1
        preflight_deadline = Deadline(self.clock, BARRIER_SECONDS, "preflight")
        self.preflight(preflight_deadline)
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


def deepcopy_json(value):
    return json.loads(json.dumps(value))


def validate_local_config(config):
    if config.mode != "retained-convergence":
        raise TransitionError("explicit supported Issue #348 mode is required")
    if (config.remote_url != CORE_REPO or config.runtime_tag != RUNTIME_TAG or
            config.previous_tag != PREVIOUS_TAG or config.previous_commit != PREVIOUS_COMMIT or
            config.old_tag != OLD_TAG or config.old_commit != OLD_COMMIT):
        raise TransitionError("fixed Issue #348 identity literals do not match")
    scalar_inputs = (
        config.context, config.expected_server, config.expected_kube_system_uid,
        config.remote_url, config.runtime_tag, config.runtime_commit,
        config.previous_tag, config.previous_commit,
        config.old_tag, config.old_commit,
    )
    if any(str(value).startswith("-") for value in scalar_inputs):
        raise TransitionError("option-like input is forbidden")
    if not config.context.strip():
        raise TransitionError("explicit non-default context is required")

    try:
        if config.kubeconfig.resolve() == (Path.home() / ".kube" / "config").resolve():
            raise TransitionError("default kubeconfig is forbidden")
        file_stat = config.kubeconfig.stat()
    except FileNotFoundError as error:
        raise TransitionError("explicit kubeconfig does not exist") from error
    if not stat.S_ISREG(file_stat.st_mode) or stat.S_IMODE(file_stat.st_mode) & 0o077:
        raise TransitionError("kubeconfig must be a regular file with mode 0600 or stricter")
    commits = (config.runtime_commit, config.previous_commit, config.old_commit)
    if any(not re.fullmatch(r"[0-9a-f]{40}", value) for value in commits):
        raise TransitionError("runtime, previous, and old commits must be exact lowercase SHAs")
    if not isinstance(config.expected_server, str) or not config.expected_server.startswith("https://") or not config.expected_kube_system_uid:
        raise TransitionError("expected cluster server and kube-system UID are required")
    if config.evidence.exists():
        raise TransitionError("evidence path must not exist")


def execute(config, *, runner=None, clock=None):
    validate_local_config(config)
    clock = clock or RealClock()
    runner = runner or RealRunner()
    Protocol(config, runner, clock, None).validate_local_checkout(
        Deadline(clock, BARRIER_SECONDS, "local checkout validation"))
    evidence = Evidence(config.evidence, clock)
    protocol = Protocol(config, runner, clock, evidence)
    try:
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
    parser.add_argument("--mode", required=True, choices=("retained-convergence",))
    parser.add_argument("--kubeconfig", required=True, type=Path)
    parser.add_argument("--context", required=True)
    parser.add_argument("--expected-server", required=True)
    parser.add_argument("--expected-kube-system-uid", required=True)
    parser.add_argument("--remote-url", required=True)
    parser.add_argument("--runtime-tag", required=True)
    parser.add_argument("--runtime-commit", required=True)
    parser.add_argument("--previous-tag", required=True)
    parser.add_argument("--previous-commit", required=True)
    parser.add_argument("--old-tag", required=True)
    parser.add_argument("--old-commit", required=True)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args(argv)
    return Config(**vars(args))


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
