#!/usr/bin/env python3
"""Generate the reviewed Issue #348 post-merge runtime closure offline."""

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import NamedTuple

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

GENERATOR_VERSION = "issue348-runtime-v2-release-closure/1"
CORE_REPO = "https://github.com/digiorg/core.git"
RUNTIME_TAG = "issue348-runtime-v6-20260919T100440Z"
PREVIOUS_TAG = "issue350-352-runtime-v3-20260904T195619Z"
PREVIOUS_COMMIT = "f6e7d58c0b03ee6a3ec6ed9e1e22e5023f861549"
RETAINED_TAG = "issue301-runtime-v16-20260817T130820Z"
RETAINED_COMMIT = "8e6b8908f99ebf76db47c15613eff523644c23f6"
DESCRIPTOR_PATH = Path("specs/345-log-schema-isolation/issue348-runtime-v2-contract.json")
OUTPUT_PATH = Path("issue348-runtime-v2-release-closure.json")
FIXTURE_PATHS = (
    Path("platform/tests/fixtures/issue348/argocd-v3.4.5/application-list.json"),
    Path("platform/tests/fixtures/issue348/argocd-v3.4.5/manifest.json"),
)
SHA1 = re.compile(r"^[0-9a-f]{40}$")


class ClosureError(RuntimeError):
    """The source checkout cannot safely produce the reviewed closure."""


class Rule(NamedTuple):
    path: str
    pointer: str
    target: str


_RULES = (
    Rule("platform/base/argocd/applications/root-app.yaml", "/spec/source/targetRevision", "candidate"),
    Rule("apps/platform/argocd.yaml", "/spec/source/targetRevision", "candidate"),
    Rule("apps/platform/backstage.yaml", "/spec/source/targetRevision", "retained"),
    Rule("apps/platform/cert-manager.yaml", "/spec/source/targetRevision", "retained"),
    Rule("apps/platform/cnpg-cluster.yaml", "/spec/source/targetRevision", "retained"),
    Rule("apps/platform/crossplane-harbor-bootstrap.yaml", "/spec/source/targetRevision", "retained"),
    Rule("apps/platform/crossplane-provider-configs.yaml", "/spec/source/targetRevision", "retained"),
    Rule("apps/platform/crossplane-providers.yaml", "/spec/source/targetRevision", "retained"),
    Rule("apps/platform/crossplane-xrds.yaml", "/spec/source/targetRevision", "retained"),
    Rule("apps/platform/external-secrets.yaml", "/spec/sources/1/targetRevision", "retained"),
    Rule("apps/platform/fluentd.yaml", "/spec/source/targetRevision", "candidate"),
    Rule("apps/platform/gitea.yaml", "/spec/sources/1/targetRevision", "retained"),
    Rule("apps/platform/gitea-actions-runner.yaml", "/spec/source/targetRevision", "retained"),
    Rule("apps/platform/grafana.yaml", "/spec/sources/1/targetRevision", "retained"),
    Rule("apps/platform/harbor.yaml", "/spec/sources/1/targetRevision", "retained"),
    Rule("apps/platform/harbor.yaml", "/spec/sources/2/targetRevision", "retained"),
    Rule("apps/platform/jaeger.yaml", "/spec/sources/1/targetRevision", "retained"),
    Rule("apps/platform/jaeger.yaml", "/spec/sources/2/targetRevision", "retained"),
    Rule("apps/platform/keycloak.yaml", "/spec/source/targetRevision", "retained"),
    Rule("apps/platform/kyverno-policies.yaml", "/spec/source/targetRevision", "retained"),
    Rule("apps/platform/landingpage.yaml", "/spec/source/targetRevision", "retained"),
    Rule("apps/platform/monitoring-extras.yaml", "/spec/source/targetRevision", "retained"),
    Rule("apps/platform/namespaces.yaml", "/spec/source/targetRevision", "retained"),
    Rule("apps/platform/nats.yaml", "/spec/sources/1/targetRevision", "retained"),
    Rule("apps/platform/nats.yaml", "/spec/sources/2/targetRevision", "retained"),
    Rule("apps/platform/opencost.yaml", "/spec/sources/1/targetRevision", "retained"),
    Rule("apps/platform/opencost.yaml", "/spec/sources/2/targetRevision", "retained"),
    Rule("apps/platform/opensearch.yaml", "/spec/sources/1/targetRevision", "candidate"),
    Rule("apps/platform/opensearch.yaml", "/spec/sources/2/targetRevision", "candidate"),
    Rule("apps/platform/postgresql.yaml", "/spec/source/targetRevision", "retained"),
    Rule("apps/platform/sonarqube.yaml", "/spec/sources/1/targetRevision", "retained"),
    Rule("apps/platform/sonarqube.yaml", "/spec/sources/2/targetRevision", "retained"),
)
ALLOWED_FIELDS = tuple((rule.path, rule.pointer) for rule in _RULES)

APPLICATION_PATHS = (
    "platform/base/argocd/applications/root-app.yaml",
    "apps/platform/app-config.yaml", "apps/platform/argocd.yaml",
    "apps/platform/backstage.yaml", "apps/platform/cert-manager.yaml",
    "apps/platform/cnpg-cluster.yaml", "apps/platform/cnpg.yaml",
    "apps/platform/core-catalog.yaml", "apps/platform/crossplane-harbor-bootstrap.yaml",
    "apps/platform/crossplane-provider-configs.yaml", "apps/platform/crossplane-providers.yaml",
    "apps/platform/crossplane-xrds.yaml", "apps/platform/crossplane.yaml",
    "apps/platform/external-secrets.yaml", "apps/platform/fluentd.yaml",
    "apps/platform/gitea-actions-runner.yaml", "apps/platform/gitea.yaml",
    "apps/platform/grafana.yaml", "apps/platform/harbor.yaml",
    "apps/platform/jaeger.yaml", "apps/platform/keycloak.yaml",
    "apps/platform/kyverno-policies.yaml", "apps/platform/kyverno.yaml",
    "apps/platform/landingpage.yaml", "apps/platform/monitoring-extras.yaml",
    "apps/platform/namespaces.yaml", "apps/platform/nats-jetstream-controller.yaml",
    "apps/platform/nats.yaml", "apps/platform/opencost.yaml",
    "apps/platform/opensearch.yaml", "apps/platform/postgresql.yaml",
    "apps/platform/sonarqube.yaml",
)

# These reviewed byte digests bind the canonical pre-closure Application manifests.
SOURCE_DIGESTS = {
    "platform/base/argocd/applications/root-app.yaml": "0ee4ac9fd06f9102ad780ec0ce6d307503a54832fa8bb2078c42ed65ad0db556",
    "apps/platform/app-config.yaml": "ac68f9febf6785456308accdc2e63043b2bc94d9a78ab05b4d0f58a721c0cd4b",
    "apps/platform/argocd.yaml": "6e91d42d222d30a6139b7b3b5caa960c42fdc6e763be0b904c6c7f078bd31191",
    "apps/platform/backstage.yaml": "4ca14d01c641ed37e163493d95d40b54325ab3c1ce9574d8e96dfbae42ed0971",
    "apps/platform/cert-manager.yaml": "94d31afd2d38e2dcc36b0ef837f7b05ed473974ea0d50947da65917d79408ef8",
    "apps/platform/cnpg-cluster.yaml": "658526fe69dcc0cebdce6103a0bdf96c42a9507318555960f195b36c346a173b",
    "apps/platform/cnpg.yaml": "07016721a18b4ff5fdd151fa513b74d3663eec9370af457fbcdeb52a853208cd",
    "apps/platform/core-catalog.yaml": "ad1241f99975913311870129087c5553822335365dc159d75c8b65e494db902e",
    "apps/platform/crossplane-harbor-bootstrap.yaml": "30e8cc4665e307c9bd955ca1ee829e2f89baff6d56d9c4fb7d5141cd6aea671f",
    "apps/platform/crossplane-provider-configs.yaml": "415cd63000f14ae0efa00329e703ae1ffaff9a0146bd47178f34f828c9b6b90d",
    "apps/platform/crossplane-providers.yaml": "98e9d05ec2898b7324dae906129dd6a3086c2cfc87f2c97838ad256a6173923c",
    "apps/platform/crossplane-xrds.yaml": "04da38ba72a78f530a84be7a694f017132606f80c19d4794e49d2d42e88fa33e",
    "apps/platform/crossplane.yaml": "c463b48021fe52d970587b2543caed5560dc8d819ffb99e08c755cebf503851b",
    "apps/platform/external-secrets.yaml": "a638f7fde7841fa4cbeaf975c00a8669b461fe261dbb68e608a5d7edd9a8be73",
    "apps/platform/fluentd.yaml": "afc9e17014f2387d5c3b7e1dba254b22c88701b061b37df5c39cf56044f130d0",
    "apps/platform/gitea-actions-runner.yaml": "dcf72ce918b20f1c6ac19c1e05d3f7243b2bfb24ad80446213bd7e97368913d0",
    "apps/platform/gitea.yaml": "c27ae6d5376a428c39bbc8003ce834669b59011ca7d8cc3f757cfda613e6f84c",
    "apps/platform/grafana.yaml": "d0fefaece757cc5d54c2c79aad1ccd609bca338c1963d6fc4c491c9bf4fc1d64",
    "apps/platform/harbor.yaml": "4bdd5cf500044d941074a568b250c18c3b9bdefd5dbab771a9e055b19e601d71",
    "apps/platform/jaeger.yaml": "5e45330fadf9cc0f4909f9272b9ef2b18fe9ce281be3f48650e7592767475790",
    "apps/platform/keycloak.yaml": "4527efe1412e1886e42157a098ff8c7c35cbef877342a6dd96f9545479057151",
    "apps/platform/kyverno-policies.yaml": "a9fe1a24213aab39717c8b82cfbd12a3b005056949a21e6bce4bc2d9a889aa21",
    "apps/platform/kyverno.yaml": "294d0650149c81ef3ce01c21339ad6a7f25602bbab1f9f0e5f17c5882b360ff4",
    "apps/platform/landingpage.yaml": "bac3cb880d1e6cbe41341f42570f9fec807d64736758944dee3d8f64bbf37c4c",
    "apps/platform/monitoring-extras.yaml": "4398723cbb34ad4daa5f6a1e39852512b584260e7f1f126bc6054c96181dc07d",
    "apps/platform/namespaces.yaml": "c588fa59beb66e167d6e15e982096824a47470e1e1cdaa618b8e07c7cd319714",
    "apps/platform/nats-jetstream-controller.yaml": "5e9af170af33f5ea3a5672a61afaea975cc6a6552bdae91299944bf7540a2656",
    "apps/platform/nats.yaml": "2cff07754cfb755a0fc530f45e82b2ea23a4d3901067d0860920ebd707426df8",
    "apps/platform/opencost.yaml": "e0b3a565ac170e2081f5d27eecef6e0b5d99aaac9ef6e58db26f55885bb281e9",
    "apps/platform/opensearch.yaml": "0f6a1909dcab09665839dfa89746b34586b6ac1e9c04587efff9ab3daa197534",
    "apps/platform/postgresql.yaml": "4dc7a38d7d9c92c3a4e1314b954e69dc67298a1da26e7e48f6f4116d4bd43ec6",
    "apps/platform/sonarqube.yaml": "843d94d4638d52ba260e0a905ee81f2d33f6afe5e2ed017cfdcedd7bc0f8edb5",
}


class ExactLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader, node, deep=False):
    keys = []
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in keys:
            raise ClosureError(f"YAML has duplicate key: {key}")
        keys.append(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


ExactLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping,
)


@dataclass(frozen=True)
class ClosureConfig:
    worktree: Path
    source_commit: str
    source_tree: str
    expected_branch: str
    required_base: str
    runtime_tag: str
    descriptor: Path
    previous_tag: str
    previous_commit: str
    retained_tag: str
    retained_commit: str
    output: Path


def _git(root, *args, check=True):
    result = subprocess.run(
        ["git", *args], cwd=root, check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if check and result.returncode:
        raise ClosureError(f"git {' '.join(args)} failed")
    return result


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _read_yaml(path):
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=ExactLoader)
    except (OSError, UnicodeError, yaml.YAMLError, ClosureError) as error:
        raise ClosureError(f"YAML is invalid: {path}") from error
    if not isinstance(value, dict):
        raise ClosureError(f"unexpected manifest shape: {path}")
    return value


def _sources(application):
    spec = application.get("spec")
    if not isinstance(spec, dict) or (("source" in spec) == ("sources" in spec)):
        raise ClosureError("unexpected manifest source shape")
    sources = spec.get("sources", [spec.get("source")])
    if not isinstance(sources, list) or not sources or not all(isinstance(x, dict) for x in sources):
        raise ClosureError("unexpected manifest source shape")
    return sources


def _identity(source):
    return tuple(source.get(key) for key in ("repoURL", "chart", "path", "ref", "targetRevision"))


def _descriptor(config):
    if config.descriptor.resolve() != (config.worktree / DESCRIPTOR_PATH).resolve():
        raise ClosureError("descriptor path is not the reviewed descriptor")
    try:
        value = json.loads(config.descriptor.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ClosureError("descriptor is unreadable") from error
    required = {
        "schema", "runtime_tag", "previous_tag", "previous_commit", "old_tag",
        "old_commit", "remote_url", "expected_applications", "expected_source_graph",
        "kyverno_resources",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ClosureError("descriptor shape is stale")
    if (
        value["schema"] != "issue348-runtime-v2-contract/v1"
        or value["runtime_tag"] != config.runtime_tag
        or value["previous_tag"] != config.previous_tag
        or value["previous_commit"] != config.previous_commit
        or value["old_tag"] != config.retained_tag
        or value["old_commit"] != config.retained_commit
        or value["remote_url"] != CORE_REPO
    ):
        raise ClosureError("descriptor identities are stale")
    graph = value.get("expected_source_graph")
    applications = value.get("expected_applications")
    if (
        not isinstance(graph, dict) or not isinstance(applications, list)
        or len(graph) != 32 or len(applications) != 32
        or sorted(graph) != sorted(applications) or len(set(applications)) != 32
    ):
        raise ClosureError("descriptor graph is stale")
    core_targets = [item[4] for entries in graph.values() for item in entries
                    if isinstance(item, list) and len(item) == 5 and item[0] == CORE_REPO]
    # The committed descriptor is the retained preflight contract: Root, Argo CD,
    # and OpenSearch values are on the previous runtime while the other 29 Core
    # sources remain on the retained sibling.  The separate explicit rule table
    # above defines the generated 5/27 publication closure.
    if (core_targets.count(config.previous_tag), core_targets.count(config.retained_tag)) != (3, 29):
        raise ClosureError("descriptor graph is stale")
    if any(item not in (config.previous_tag, config.retained_tag) for item in core_targets):
        raise ClosureError("descriptor graph contains stale identity")
    return value


def _validate_config(config):
    root = config.worktree.resolve()
    if not root.is_dir() or _git(root, "rev-parse", "--show-toplevel").stdout.strip() != str(root):
        raise ClosureError("worktree boundary is not an exact repository root")
    if config.output.resolve() != (root / OUTPUT_PATH).resolve():
        raise ClosureError("output path is outside the explicit closure-manifest boundary")
    if config.output.exists():
        raise ClosureError("output path must not exist")
    if not all(SHA1.fullmatch(item or "") for item in (
            config.source_commit, config.source_tree, config.required_base,
            config.previous_commit, config.retained_commit)):
        raise ClosureError("source/base identity is malformed")
    if (
        config.runtime_tag != RUNTIME_TAG or config.previous_tag != PREVIOUS_TAG
        or config.previous_commit != PREVIOUS_COMMIT or config.retained_tag != RETAINED_TAG
        or config.retained_commit != RETAINED_COMMIT
    ):
        raise ClosureError("fixed release identities do not match")
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all").stdout:
        raise ClosureError("worktree must be clean")
    if _git(root, "branch", "--show-current").stdout.strip() != config.expected_branch:
        raise ClosureError("wrong canonical branch")
    if _git(root, "rev-parse", "HEAD").stdout.strip() != config.source_commit:
        raise ClosureError("source commit does not match HEAD")
    if _git(root, "rev-parse", "HEAD^{tree}").stdout.strip() != config.source_tree:
        raise ClosureError("source tree does not match HEAD")
    if _git(root, "cat-file", "-e", f"{config.required_base}^{{commit}}", check=False).returncode:
        raise ClosureError("required base does not exist")
    if _git(root, "merge-base", "--is-ancestor", config.required_base, config.source_commit,
            check=False).returncode:
        raise ClosureError("required base is not an ancestor")
    if not _git(root, "show-ref", "--verify", "--quiet", f"refs/tags/{config.runtime_tag}",
                check=False).returncode:
        raise ClosureError("reserved runtime tag already exists or moved")
    for relative in (*APPLICATION_PATHS, DESCRIPTOR_PATH.as_posix(),
                     *(path.as_posix() for path in FIXTURE_PATHS)):
        if _git(root, "cat-file", "-e", f"{config.source_commit}:{relative}", check=False).returncode:
            raise ClosureError(f"source commit lacks reviewed source: {relative}")
    return root


def _validate_source(root, descriptor):
    actual_graph = {}
    for relative in APPLICATION_PATHS:
        path = root / relative
        application = _read_yaml(path)
        try:
            name = application["metadata"]["name"]
        except (KeyError, TypeError) as error:
            raise ClosureError("unexpected manifest shape") from error
        if not isinstance(name, str) or name in actual_graph:
            raise ClosureError("duplicate or malformed Application source")
        actual_graph[name] = [_identity(source) for source in _sources(application)]
    expected_graph = {}
    for name, entries in descriptor["expected_source_graph"].items():
        expected_graph[name] = [
            tuple([*entry[:4], "main" if entry[0] == CORE_REPO else entry[4]])
            for entry in entries
        ]
    if actual_graph != expected_graph:
        raise ClosureError("source graph does not match reviewed descriptor")
    for relative, digest in SOURCE_DIGESTS.items():
        if _sha256((root / relative).read_bytes()) != digest:
            raise ClosureError(f"reviewed source digest mismatch: {relative}")


def _pointer_parts(pointer):
    if not pointer.startswith("/"):
        raise ClosureError("invalid allowlisted field pointer")
    return pointer[1:].split("/")


def _mapping_value(node, key):
    if not isinstance(node, MappingNode):
        raise ClosureError("unexpected YAML node shape")
    matches = [value for key_node, value in node.value
               if isinstance(key_node, ScalarNode) and key_node.value == key]
    if len(matches) != 1:
        raise ClosureError("allowlisted YAML field is missing or duplicated")
    return matches[0]


def _scalar_node(text, pointer):
    try:
        node = yaml.compose(text)
    except yaml.YAMLError as error:
        raise ClosureError("YAML is invalid") from error
    for part in _pointer_parts(pointer):
        if isinstance(node, MappingNode):
            node = _mapping_value(node, part)
        elif isinstance(node, SequenceNode) and part.isdigit():
            index = int(part)
            if index >= len(node.value):
                raise ClosureError("allowlisted YAML index is absent")
            node = node.value[index]
        else:
            raise ClosureError("allowlisted YAML field has unexpected shape")
    if not isinstance(node, ScalarNode):
        raise ClosureError("allowlisted YAML field is not scalar")
    return node


def _value_at(document, pointer):
    current = document
    for part in _pointer_parts(pointer):
        current = current[int(part)] if isinstance(current, list) else current[part]
    return current


def replace_pointer(document, pointer, value):
    parts = _pointer_parts(pointer)
    current = document
    for part in parts[:-1]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    if isinstance(current, list):
        current[int(parts[-1])] = value
    else:
        current[parts[-1]] = value


def expected_target(path, pointer):
    matches = [rule for rule in _RULES if rule.path == path and rule.pointer == pointer]
    if len(matches) != 1:
        raise ClosureError("field is outside the allowlist")
    return RUNTIME_TAG if matches[0].target == "candidate" else RETAINED_TAG


def _tracked_snapshot(root):
    paths = _git(root, "ls-files", "-z").stdout.split("\0")
    return {path: _sha256((root / path).read_bytes()) for path in paths if path}


def _apply(root):
    by_path = {}
    for rule in _RULES:
        by_path.setdefault(rule.path, []).append(rule)
    changes = []
    for relative, rules in sorted(by_path.items()):
        path = root / relative
        text = path.read_text(encoding="utf-8")
        before = _read_yaml(path)
        expected = json.loads(json.dumps(before))
        replacements = []
        for rule in rules:
            if _value_at(before, rule.pointer) != "main":
                raise ClosureError("allowlisted source is not canonical main")
            target = RUNTIME_TAG if rule.target == "candidate" else RETAINED_TAG
            replace_pointer(expected, rule.pointer, target)
            node = _scalar_node(text, rule.pointer)
            if text[node.start_mark.index:node.end_mark.index] != "main":
                raise ClosureError("targetRevision scalar is not exact canonical main")
            replacements.append((node.start_mark.index, node.end_mark.index, target))
            changes.append({
                "path": relative, "field": rule.pointer,
                "before": "main", "after": target,
            })
        for start, end, target in sorted(replacements, reverse=True):
            text = text[:start] + target + text[end:]
        path.write_text(text, encoding="utf-8")
        if _read_yaml(path) != expected:
            raise ClosureError("output changed a field outside the allowlist")
    return sorted(changes, key=lambda item: (item["path"], item["field"]))


def generate(config):
    root = _validate_config(config)
    descriptor = _descriptor(config)
    _validate_source(root, descriptor)
    before = _tracked_snapshot(root)
    changes = _apply(root)
    after = _tracked_snapshot(root)
    changed_paths = {path for path in before if before[path] != after[path]}
    allowed_paths = {rule.path for rule in _RULES}
    if changed_paths != allowed_paths or set(before) != set(after):
        raise ClosureError("output path changed outside the allowlist")
    manifest = {
        "schema": "issue348-runtime-v2-release-closure/v1",
        "generator_version": GENERATOR_VERSION,
        "source": {"commit": config.source_commit, "tree": config.source_tree},
        "reserved_runtime_tag": config.runtime_tag,
        "predecessor": {"tag": config.previous_tag, "commit": config.previous_commit},
        "retained": {"tag": config.retained_tag, "commit": config.retained_commit},
        "expected_graph": {"candidate": 5, "retained": 27, "previous": 0, "other": 0},
        "changes": changes,
        "descriptor_sha256": _sha256(config.descriptor.read_bytes()),
        "fixture_digests": {
            path.as_posix(): _sha256((root / path).read_bytes()) for path in FIXTURE_PATHS
        },
    }
    config.output.write_text(
        json.dumps(manifest, sort_keys=True, indent=2, separators=(",", ": ")) + "\n",
        encoding="utf-8",
    )
    untracked = _git(root, "ls-files", "--others", "--exclude-standard").stdout.splitlines()
    if untracked != [OUTPUT_PATH.as_posix()]:
        raise ClosureError("unexpected output path outside the allowlist")
    return manifest


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", required=True, type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--expected-branch", required=True)
    parser.add_argument("--required-base", required=True)
    parser.add_argument("--runtime-tag", required=True)
    parser.add_argument("--descriptor", required=True, type=Path)
    parser.add_argument("--previous-tag", required=True)
    parser.add_argument("--previous-commit", required=True)
    parser.add_argument("--retained-tag", required=True)
    parser.add_argument("--retained-commit", required=True)
    parser.add_argument("--output", required=True, type=Path)
    values = vars(parser.parse_args(argv))
    return ClosureConfig(**values)


def main(argv=None):
    try:
        generate(parse_args(argv))
    except ClosureError as error:
        raise SystemExit(f"release closure refused: {error}") from error


if __name__ == "__main__":
    main()
