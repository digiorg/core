#!/usr/bin/env python3
"""Structurally non-mutating Issue #348 runtime-v2 preflight.

The production CLI accepts the same operational identities as the transition,
but its adapter dependency is read-only and its only write is exclusive local
mode-0600 evidence creation. Tests inject a fake adapter and never access a
cluster.
"""

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

from issue348_runtime_v2_qualification import (
    MutationPlan,
    QualificationError,
    ReadResult,
    SnapshotDecoder,
    Validator,
)
from issue348_runtime_v2_contract import CONTRACT_PATH, load_contract

CALL_SECONDS = 20


def _command(argv, *, env=None):
    try:
        result = subprocess.run(
            argv, check=False, capture_output=True, text=True,
            timeout=CALL_SECONDS, env=env,
        )
    except (subprocess.TimeoutExpired, OSError) as error:
        raise QualificationError("read-only command timed out or could not start") from error
    if result.returncode != 0:
        raise QualificationError("read-only command failed")
    return result.stdout


def _json_command(argv):
    output = _command(argv)
    try:
        value = json.loads(output)
    except json.JSONDecodeError as error:
        raise QualificationError("read-only command returned invalid JSON") from error
    if not isinstance(value, dict):
        raise QualificationError("read-only command returned wrong JSON shape")
    return value


class ReadOnlySnapshotAdapter:
    """Production adapter with an enumerated read-only command surface."""

    mutation_trace = ()

    def __init__(self, config, invocation):
        self.config = config
        self.invocation = tuple(invocation)

    def _kubectl(self, args, *, namespace=None):
        argv = [
            "kubectl", "--kubeconfig", str(self.config.kubeconfig),
            "--context", self.config.context,
            f"--request-timeout={CALL_SECONDS}s",
        ]
        if namespace:
            argv.extend(["-n", namespace])
        argv.extend(args)
        return argv

    def _remote_tags(self):
        identities = (
            (self.config.runtime_tag, self.config.runtime_commit),
            (self.config.previous_tag, self.config.previous_commit),
            (self.config.old_tag, self.config.old_commit),
        )
        for tag, commit in identities:
            reference = f"refs/tags/{tag}"
            peeled = f"{reference}^{{}}"
            output = _command([
                "git", "ls-remote", "--tags", self.config.remote_url,
                reference, peeled,
            ])
            refs = {}
            for line in output.splitlines():
                fields = line.split("\t")
                if len(fields) == 2:
                    refs[fields[1]] = fields[0]
            if set(refs) != {reference, peeled} or refs.get(peeled) != commit:
                raise QualificationError(
                    "remote tag is not annotated or does not peel to exact commit"
                )
        return identities

    def _argocd_diff(self):
        current_context = _command([
            "kubectl", "--kubeconfig", str(self.config.kubeconfig),
            "config", "current-context",
        ]).strip()
        if current_context != self.config.context:
            raise QualificationError(
                "read-only Argo CD diff requires the explicit context to be current"
            )
        view = _json_command([
            "kubectl", "--kubeconfig", str(self.config.kubeconfig),
            "config", "view", "--minify", "-o", "json",
        ])
        contexts = view.get("contexts")
        if (not isinstance(contexts, list) or len(contexts) != 1 or
                contexts[0].get("context", {}).get("namespace") != "argocd"):
            raise QualificationError(
                "read-only Argo CD diff requires current namespace argocd"
            )
        environment = os.environ.copy()
        environment["KUBECONFIG"] = str(self.config.kubeconfig)
        try:
            result = subprocess.run(
                ["argocd", "app", "diff", "kyverno", "--core", "--refresh"],
                check=False, capture_output=True, text=True,
                timeout=CALL_SECONDS, env=environment,
            )
        except subprocess.TimeoutExpired:
            return ReadResult(None, "", "timeout")
        except OSError as error:
            raise QualificationError("Argo CD diff could not start") from error
        return ReadResult(result.returncode, result.stdout, result.stderr)

    def collect(self):
        tags = self._remote_tags()
        view = _json_command(self._kubectl(["config", "view", "--minify", "-o", "json"]))
        clusters = view.get("clusters")
        if not isinstance(clusters, list) or len(clusters) != 1:
            raise QualificationError("cluster view has wrong shape")
        server = clusters[0].get("cluster", {}).get("server")
        namespace = _json_command(self._kubectl([
            "get", "namespace", "kube-system", "-o", "json",
        ]))
        application_list = _json_command(self._kubectl([
            "get", "--raw",
            "/apis/argoproj.io/v1alpha1/namespaces/argocd/applications",
        ]))
        controllers = _json_command(self._kubectl([
            "get", "statefulsets.apps", "-l",
            "app.kubernetes.io/name=argocd-application-controller", "-o", "json",
        ], namespace="argocd")).get("items")
        if not isinstance(controllers, list) or len(controllers) != 1:
            raise QualificationError(
                "expected exactly one application controller StatefulSet"
            )
        pods = _json_command(self._kubectl([
            "get", "pods", "-l",
            "app.kubernetes.io/name=argocd-application-controller", "-o", "json",
        ], namespace="argocd")).get("items")
        if not isinstance(pods, list):
            raise QualificationError("controller Pod list shape is invalid")
        hpas = _json_command(self._kubectl([
            "get", "horizontalpodautoscalers.autoscaling", "-A", "-o", "json",
        ]))
        return SnapshotDecoder.snapshot(
            remote_tags=tags,
            cluster_server=server,
            kube_system_uid=namespace.get("metadata", {}).get("uid"),
            application_list=application_list,
            controller=controllers[0],
            controller_pods=pods,
            hpa_list=hpas,
            argocd_diff=self._argocd_diff(),
            invocation=self.invocation,
        )


def execute(adapter, contract, evidence_path, invocation=()):
    evidence_path = Path(evidence_path)
    if evidence_path.exists():
        raise QualificationError("evidence path must not exist")
    write_capabilities = {
        "patch", "write", "apply", "delete", "replace", "mutate", "execute_mutation",
    }
    if any(callable(getattr(adapter, name, None)) for name in write_capabilities):
        raise QualificationError("preflight adapter exposes a write capability")
    if tuple(getattr(adapter, "mutation_trace", ())) != ():
        raise QualificationError("read-only adapter exposed a nonempty mutation trace")
    snapshot = adapter.collect()
    validated = Validator.validate(snapshot, contract)
    plan = MutationPlan.build(validated, contract)
    rendered_plan = json.loads(plan.render())
    record = {
        "schema": "issue348-runtime-v2-preflight/v1",
        "snapshot_digest": snapshot.digest,
        "proposed_mutation_plan": rendered_plan,
        "identities": {
            "cluster_server": snapshot.cluster_server,
            "kube_system_uid": snapshot.kube_system_uid,
            "controller_uid": validated.controller_uid,
            "applications": list(validated.application_names),
            "remote_tags": [list(item) for item in snapshot.remote_tags],
        },
        "invocation": list(invocation or snapshot.invocation),
        "mutation_trace": [],
        "runtime_mutated": False,
    }
    try:
        fd = os.open(evidence_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise QualificationError("evidence path must not exist") from error
    actual_mode = stat.S_IMODE(os.fstat(fd).st_mode)
    if actual_mode != 0o600:
        os.close(fd)
        raise QualificationError("evidence file mode is not 0600")
    with os.fdopen(fd, "w", encoding="utf-8") as evidence:
        evidence.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        evidence.flush()
        os.fsync(evidence.fileno())
    return record


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
    parser.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    return parser.parse_args(argv)


def main(argv=None):
    invocation = tuple(sys.argv if argv is None else [Path(__file__).name, *argv])
    config = parse_args(argv)
    try:
        scalar_inputs = (
            config.context, config.expected_server, config.expected_kube_system_uid,
            config.remote_url, config.runtime_tag, config.runtime_commit,
            config.previous_tag, config.previous_commit,
            config.old_tag, config.old_commit,
        )
        if (any(not isinstance(value, str) or not value or value.startswith("-")
                for value in scalar_inputs) or
                not config.expected_server.startswith("https://") or
                any(not re.fullmatch(r"[0-9a-f]{40}", value) for value in (
                    config.runtime_commit, config.previous_commit, config.old_commit,
                ))):
            raise QualificationError("preflight scalar identities are malformed")
        if config.kubeconfig.resolve() == (Path.home() / ".kube/config").resolve():
            raise QualificationError("default kubeconfig is forbidden")
        file_stat = config.kubeconfig.stat()
        if (not stat.S_ISREG(file_stat.st_mode) or
                stat.S_IMODE(file_stat.st_mode) & 0o077):
            raise QualificationError(
                "kubeconfig must be a regular file with mode 0600 or stricter"
            )
        contract = load_contract(config.contract, config)
        execute(
            ReadOnlySnapshotAdapter(config, invocation),
            contract,
            config.evidence,
            invocation=invocation,
        )
    except (OSError, QualificationError) as error:
        print(f"preflight failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
