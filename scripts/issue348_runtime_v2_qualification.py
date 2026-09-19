#!/usr/bin/env python3
"""Pure qualification models and planning for Issue #348 runtime-v2.

This module deliberately has no filesystem, process, network, clock, or write
capability. Adapters serialize observations into ``Snapshot``; ``Validator``
then consumes only that immutable value and a committed contract.
"""

import hashlib
import json
import re
from typing import NamedTuple


class QualificationError(RuntimeError):
    """Fail-closed qualification error."""


class ReadResult(NamedTuple):
    returncode: int | None
    stdout: str
    stderr: str


class Snapshot(NamedTuple):
    remote_tags: tuple[tuple[str, str], ...]
    cluster_server: str
    kube_system_uid: str
    application_list_json: str
    controller_json: str
    controller_pods_json: str
    hpa_list_json: str
    argocd_diff: ReadResult
    invocation: tuple[str, ...]

    @property
    def digest(self):
        return _digest({
            "remote_tags": self.remote_tags,
            "cluster_server": self.cluster_server,
            "kube_system_uid": self.kube_system_uid,
            "application_list": json.loads(self.application_list_json),
            "controller": json.loads(self.controller_json),
            "controller_pods": json.loads(self.controller_pods_json),
            "hpa_list": json.loads(self.hpa_list_json),
            "argocd_diff": self.argocd_diff,
            "invocation": self.invocation,
        })


class ValidationContract(NamedTuple):
    expected_server: str
    expected_kube_system_uid: str
    expected_remote_tags: tuple[tuple[str, str], ...]
    expected_applications: tuple[str, ...]
    expected_source_graph: tuple[tuple[str, tuple[tuple[object, ...], ...]], ...]
    kyverno_resources: tuple[tuple[str, str, str, str, str, str], ...]
    runtime_tag: str
    previous_tag: str


class ValidatedSnapshot(NamedTuple):
    snapshot: Snapshot
    application_names: tuple[str, ...]
    applications_json: str
    controller_uid: str
    controller_resource_version: str
    controller_replicas: int
    root_uid: str
    root_resource_version: str
    root_current_revision: str
    argocd_uid: str
    argocd_resource_version: str
    argocd_current_revision: str


class MutationOperation(NamedTuple):
    name: str
    kind: str
    namespace: str
    resource_name: str
    uid: str
    resource_version: str
    path: str
    current_value: object
    after: object
    extra_preconditions: tuple[tuple[str, object], ...] = ()

    def record(self):
        return {
            "name": self.name,
            "target": {
                "kind": self.kind,
                "namespace": self.namespace,
                "name": self.resource_name,
            },
            "preconditions": {
                "uid": self.uid,
                "resource_version": self.resource_version,
                "path": self.path,
                "current_value": self.current_value,
                "extra": [
                    {"path": path, "value": value}
                    for path, value in self.extra_preconditions
                ],
            },
            "after": self.after,
        }


_ALLOWED_TARGETS = {
    ("statefulsets.apps", "argocd", "argocd-application-controller"),
    ("applications.argoproj.io", "argocd", "root-app"),
    ("applications.argoproj.io", "argocd", "argocd"),
}


class MutationPlan(NamedTuple):
    snapshot_digest: str
    normal: tuple[MutationOperation, ...]
    rollback: tuple[MutationOperation, ...]

    @staticmethod
    def operation(name, kind, namespace, resource_name, uid, resource_version,
                  current_value, after, extra_preconditions=()):
        if (kind, namespace, resource_name) not in _ALLOWED_TARGETS:
            raise QualificationError("operation is not an approved target")
        path = ("/spec/replicas" if kind == "statefulsets.apps"
                else "/spec/source/targetRevision")
        if not all(isinstance(value, str) and value for value in
                   (name, kind, namespace, resource_name, uid, resource_version)):
            raise QualificationError("operation identity and CAS preconditions are required")
        return MutationOperation(
            name, kind, namespace, resource_name, uid, resource_version,
            path, current_value, after, tuple(extra_preconditions),
        )

    @classmethod
    def build(cls, validated, contract):
        if not isinstance(validated, ValidatedSnapshot):
            raise QualificationError("plan requires a validated Snapshot")
        if Validator.validate(validated.snapshot, contract) != validated:
            raise QualificationError("plan rejected a tampered validated Snapshot")
        applications = SnapshotDecoder.applications(validated.snapshot)

        def source_preconditions(name):
            source = _source_list(applications[name])[0]
            return (
                ("/spec/source/repoURL", source.get("repoURL")),
                ("/spec/source/path", source.get("path")),
            )

        normal = (
            cls.operation(
                "controller-barrier", "statefulsets.apps", "argocd",
                "argocd-application-controller", validated.controller_uid,
                validated.controller_resource_version, validated.controller_replicas, 0,
            ),
            cls.operation(
                "root-owner", "applications.argoproj.io", "argocd", "root-app",
                validated.root_uid, validated.root_resource_version,
                validated.root_current_revision, contract.runtime_tag,
                source_preconditions("root-app"),
            ),
            cls.operation(
                "argocd-owner", "applications.argoproj.io", "argocd", "argocd",
                validated.argocd_uid, validated.argocd_resource_version,
                validated.argocd_current_revision, contract.runtime_tag,
                source_preconditions("argocd"),
            ),
            cls.operation(
                "controller-restore", "statefulsets.apps", "argocd",
                "argocd-application-controller", validated.controller_uid,
                "$readback-after-barrier", 0, validated.controller_replicas,
            ),
        )
        rollback = (
            cls.operation(
                "rollback-root-owner", "applications.argoproj.io", "argocd", "root-app",
                validated.root_uid, "$readback-before-rollback",
                contract.runtime_tag, contract.previous_tag,
                source_preconditions("root-app"),
            ),
            cls.operation(
                "rollback-argocd-owner", "applications.argoproj.io", "argocd", "argocd",
                validated.argocd_uid, "$readback-before-rollback",
                contract.runtime_tag, contract.previous_tag,
                source_preconditions("argocd"),
            ),
            cls.operation(
                "rollback-controller", "statefulsets.apps", "argocd",
                "argocd-application-controller", validated.controller_uid,
                "$readback-before-restore", 0, validated.controller_replicas,
            ),
        )
        return cls(validated.snapshot.digest, normal, rollback)

    def render(self):
        return _canonical({
            "snapshot_digest": self.snapshot_digest,
            "normal": [item.record() for item in self.normal],
            "rollback": [item.record() for item in self.rollback],
        })


class SnapshotDecoder:
    """Strictly decode adapter observations into immutable canonical strings."""

    @staticmethod
    def decode_application_list(value):
        if isinstance(value, str):
            value = _exact_json(value, dict)
        if not isinstance(value, dict):
            raise QualificationError("Application list shape is invalid")
        items = value.get("items")
        if (value.get("apiVersion") != "argoproj.io/v1alpha1" or
                value.get("kind") != "ApplicationList" or
                not isinstance(items, list) or len(items) > 100):
            raise QualificationError("Application list shape is invalid")
        applications = {}
        for item in items:
            name = item.get("metadata", {}).get("name") if isinstance(item, dict) else None
            if not isinstance(name, str) or not name or name in applications:
                raise QualificationError(
                    "Application inventory has malformed or duplicate names"
                )
            applications[name] = item
        return applications

    @classmethod
    def snapshot(cls, *, remote_tags, cluster_server, kube_system_uid,
                 application_list, controller, controller_pods, hpa_list,
                 argocd_diff, invocation):
        if (not isinstance(cluster_server, str) or not cluster_server or
                not isinstance(kube_system_uid, str) or not kube_system_uid or
                not isinstance(invocation, (list, tuple)) or
                not all(isinstance(item, str) and item for item in invocation)):
            raise QualificationError("Snapshot identity inputs are malformed")
        if (not isinstance(remote_tags, (list, tuple)) or
                not all(isinstance(item, (list, tuple)) and len(item) == 2 and
                        all(isinstance(value, str) and value for value in item)
                        for item in remote_tags)):
            raise QualificationError("Snapshot remote tag identities are malformed")
        applications = cls.decode_application_list(application_list)
        canonical_listing = {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "ApplicationList",
            "items": [applications[name] for name in sorted(applications)],
        }
        if not isinstance(argocd_diff, ReadResult):
            raise QualificationError("Argo CD diff result is malformed")
        return Snapshot(
            tuple(tuple(item) for item in remote_tags),
            cluster_server,
            kube_system_uid,
            _canonical(canonical_listing),
            _canonical(controller),
            _canonical(list(controller_pods)),
            _canonical(hpa_list),
            argocd_diff,
            tuple(invocation),
        )

    @staticmethod
    def applications(snapshot):
        return SnapshotDecoder.decode_application_list(snapshot.application_list_json)

    @staticmethod
    def controller(snapshot):
        return _exact_json(snapshot.controller_json, dict)

    @staticmethod
    def controller_pods(snapshot):
        return _exact_json(snapshot.controller_pods_json, list)

    @staticmethod
    def hpa_list(snapshot):
        return _exact_json(snapshot.hpa_list_json, dict)


_API_GROUP_RE = re.compile(
    r"(?:[a-z0-9](?:[-a-z0-9]*[a-z0-9])?)(?:\.(?:[a-z0-9](?:[-a-z0-9]*[a-z0-9])?))*"
)
_API_VERSION_RE = re.compile(r"v[1-9][0-9]*(?:(?:alpha|beta)[1-9][0-9]*)?")
_KIND_RE = re.compile(r"[A-Z][A-Za-z0-9]*")


def canonical_resource_identity(resource):
    allowed = {"group", "version", "kind", "namespace", "name", "status", "health"}
    if not isinstance(resource, dict) or set(resource) - allowed:
        raise QualificationError("Kyverno resource identity/status mismatch")
    raw_group = resource.get("group")
    if raw_group in (None, ""):
        group = ""
    elif isinstance(raw_group, str) and _API_GROUP_RE.fullmatch(raw_group):
        group = raw_group
    else:
        raise QualificationError("Kyverno resource identity/status mismatch")
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
        raise QualificationError("Kyverno resource identity/status mismatch")
    return group, version, kind, namespace or "", name


def _source_list(application):
    spec = application.get("spec")
    if not isinstance(spec, dict):
        raise QualificationError("Application source graph is malformed")
    sources = spec.get("sources")
    if sources is None:
        sources = [spec.get("source")]
    if not isinstance(sources, list) or not sources or not all(
            isinstance(item, dict) for item in sources):
        raise QualificationError("Application source graph is malformed")
    return sources


def _source_identity(source):
    return tuple(source.get(key) for key in
                 ("repoURL", "chart", "path", "ref", "targetRevision"))


def _ready_pod(pod, owner_uid):
    return (
        isinstance(pod, dict) and
        any(item.get("uid") == owner_uid and item.get("controller") is True
            for item in pod.get("metadata", {}).get("ownerReferences", [])) and
        any(item.get("type") == "Ready" and item.get("status") == "True"
            for item in pod.get("status", {}).get("conditions", [])) and
        pod.get("status", {}).get("phase") == "Running"
    )


class Validator:
    """Pure validator: no adapters, files, processes, clocks, or mutation."""

    @staticmethod
    def validate_application_statuses(applications, kyverno_resources,
                                      allow_only_kyverno=False):
        expected_resources = {
            tuple(item[:5]): item[5] for item in kyverno_resources
        }
        if len(expected_resources) != len(kyverno_resources):
            raise QualificationError("Kyverno resource contract has duplicate identity")
        for name, application in applications.items():
            if not isinstance(application, dict):
                raise QualificationError("Application object is malformed")
            if (application.get("operation") is not None or
                    "operation" in application.get("spec", {})):
                raise QualificationError(f"Application {name} has a pending operation")
            phase = application.get("status", {}).get("operationState", {}).get("phase")
            if phase in {"Running", "Terminating"}:
                raise QualificationError(f"Application {name} has active operation")
            status = application.get("status", {})
            if status.get("health", {}).get("status") != "Healthy":
                raise QualificationError(f"Application {name} is not exact Healthy")
            sync = status.get("sync", {}).get("status")
            if name != "kyverno":
                if sync != "Synced":
                    raise QualificationError(f"unexpected non-synced Application {name}")
                continue
            if not expected_resources:
                if sync != "Synced":
                    raise QualificationError("Kyverno status contract is missing")
                continue
            if sync != "OutOfSync":
                raise QualificationError(
                    "Kyverno must be the exact retained OutOfSync exception"
                )
            resources = status.get("resources")
            if not isinstance(resources, list):
                raise QualificationError("Kyverno non-synced resource inventory mismatch")
            actual = {}
            for resource in resources:
                identity = canonical_resource_identity(resource)
                if identity in actual:
                    raise QualificationError(
                        "Kyverno resource inventory has duplicate identity"
                    )
                actual[identity] = resource["status"]
            if actual != expected_resources:
                raise QualificationError("Kyverno complete resource inventory mismatch")
        if allow_only_kyverno and set(applications) != {"kyverno"}:
            raise QualificationError("fixture must contain only Kyverno")

    @classmethod
    def validate(cls, snapshot, contract):
        if not isinstance(snapshot, Snapshot) or not isinstance(contract, ValidationContract):
            raise QualificationError("Validator requires Snapshot and committed contract")
        if snapshot.remote_tags != contract.expected_remote_tags:
            raise QualificationError("remote tag identities do not match exact commits")
        if (snapshot.cluster_server != contract.expected_server or
                snapshot.kube_system_uid != contract.expected_kube_system_uid):
            raise QualificationError(
                "cluster identity does not match expected server and namespace UID"
            )
        diff = snapshot.argocd_diff
        if diff.returncode is None:
            raise QualificationError("Kyverno material diff command timed out")
        if diff.returncode != 0:
            raise QualificationError("Kyverno material diff command failed")
        if diff.stdout != "" or diff.stderr != "":
            raise QualificationError(
                "Kyverno material diff output is not exactly zero bytes"
            )

        applications = SnapshotDecoder.applications(snapshot)
        names = tuple(sorted(applications))
        if names != tuple(sorted(contract.expected_applications)):
            raise QualificationError(
                "Application inventory does not equal reviewed name set"
            )
        cls.validate_application_statuses(applications, contract.kyverno_resources)
        actual_graph = tuple(sorted(
            (name, tuple(_source_identity(source) for source in _source_list(application)))
            for name, application in applications.items()
        ))
        if actual_graph != tuple(sorted(contract.expected_source_graph)):
            raise QualificationError("complete retained source graph mismatch")

        controller = SnapshotDecoder.controller(snapshot)
        metadata = controller.get("metadata", {})
        spec = controller.get("spec", {})
        status = controller.get("status", {})
        if (controller.get("apiVersion") != "apps/v1" or
                controller.get("kind") != "StatefulSet" or
                metadata.get("name") != "argocd-application-controller" or
                metadata.get("namespace") != "argocd"):
            raise QualificationError(
                "expected exact application controller name and namespace"
            )
        uid = metadata.get("uid")
        resource_version = metadata.get("resourceVersion")
        replicas = spec.get("replicas")
        if (not isinstance(uid, str) or not uid or
                not isinstance(resource_version, str) or not resource_version or
                not isinstance(replicas, int) or replicas <= 0 or
                status.get("replicas") != replicas or
                status.get("readyReplicas") != replicas or
                status.get("currentRevision") != status.get("updateRevision") or
                status.get("observedGeneration") != metadata.get("generation")):
            raise QualificationError("application controller is not fully ready")
        pods = SnapshotDecoder.controller_pods(snapshot)
        if len(pods) != replicas or not all(_ready_pod(pod, uid) for pod in pods):
            raise QualificationError("controller Pod identities/readiness mismatch")

        hpa_list = SnapshotDecoder.hpa_list(snapshot)
        items = hpa_list.get("items")
        if (hpa_list.get("apiVersion") != "autoscaling/v2" or
                hpa_list.get("kind") != "HorizontalPodAutoscalerList" or
                not isinstance(hpa_list.get("metadata"), dict) or
                not isinstance(items, list) or len(items) > 1000):
            raise QualificationError("HPA list shape is invalid")
        for item in items:
            metadata_hpa = item.get("metadata") if isinstance(item, dict) else None
            spec_hpa = item.get("spec") if isinstance(item, dict) else None
            ref = spec_hpa.get("scaleTargetRef") if isinstance(spec_hpa, dict) else None
            if (not isinstance(item, dict) or item.get("apiVersion") != "autoscaling/v2" or
                    item.get("kind") != "HorizontalPodAutoscaler" or
                    not isinstance(metadata_hpa, dict) or
                    not all(isinstance(metadata_hpa.get(key), str) and metadata_hpa[key]
                            for key in ("name", "namespace")) or
                    not isinstance(ref, dict) or
                    not all(isinstance(ref.get(key), str) and ref[key]
                            for key in ("apiVersion", "kind", "name"))):
                raise QualificationError("HPA list shape is invalid")
            api_group = ref["apiVersion"].split("/", 1)[0].lower()
            if (metadata_hpa["namespace"] == "argocd" and api_group == "apps" and
                    ref["kind"].lower() == "statefulset" and
                    ref["name"] == "argocd-application-controller"):
                raise QualificationError("HPA targets application controller")

        def owner(name):
            application = applications[name]
            owner_metadata = application.get("metadata", {})
            source = _source_list(application)[0]
            values = (
                owner_metadata.get("uid"), owner_metadata.get("resourceVersion"),
                source.get("targetRevision"),
            )
            if not all(isinstance(value, str) and value for value in values):
                raise QualificationError(f"{name} CAS identity is malformed")
            return values

        root_uid, root_rv, root_revision = owner("root-app")
        argo_uid, argo_rv, argo_revision = owner("argocd")
        return ValidatedSnapshot(
            snapshot, names, snapshot.application_list_json, uid, resource_version,
            replicas, root_uid, root_rv, root_revision, argo_uid, argo_rv,
            argo_revision,
        )


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _exact_json(text, expected_type):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise QualificationError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(text, object_pairs_hook=unique_object)
    except (TypeError, json.JSONDecodeError) as error:
        raise QualificationError("invalid JSON observation") from error
    if not isinstance(value, expected_type):
        raise QualificationError("JSON observation has wrong shape")
    return value
