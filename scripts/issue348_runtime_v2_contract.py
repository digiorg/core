#!/usr/bin/env python3
"""Load the committed Issue #348 runtime-v2 validation descriptor."""

import json
from pathlib import Path

from issue348_runtime_v2_qualification import QualificationError, ValidationContract

CONTRACT_PATH = (
    Path(__file__).resolve().parents[1]
    / "specs/345-log-schema-isolation/issue348-runtime-v2-contract.json"
)


def load_contract(path, config):
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QualificationError("committed contract is unreadable") from error
    if (not isinstance(document, dict) or
            document.get("schema") != "issue348-runtime-v2-contract/v1" or
            document.get("runtime_tag") != config.runtime_tag or
            document.get("previous_tag") != config.previous_tag or
            document.get("previous_commit") != config.previous_commit or
            document.get("old_tag") != config.old_tag or
            document.get("old_commit") != config.old_commit or
            document.get("remote_url") != config.remote_url):
        raise QualificationError("committed contract identities do not match invocation")
    try:
        graph = tuple(sorted(
            (name, tuple(tuple(identity) for identity in identities))
            for name, identities in document["expected_source_graph"].items()
        ))
        expected_applications = tuple(document["expected_applications"])
        kyverno_resources = tuple(
            tuple(item) for item in document["kyverno_resources"]
        )
    except (KeyError, TypeError) as error:
        raise QualificationError("committed contract shape is invalid") from error
    if (len(expected_applications) != 32 or len(set(expected_applications)) != 32 or
            len(graph) != 32 or len(kyverno_resources) != 69):
        raise QualificationError("committed contract closure is invalid")
    return ValidationContract(
        expected_server=config.expected_server,
        expected_kube_system_uid=config.expected_kube_system_uid,
        expected_remote_tags=(
            (config.runtime_tag, config.runtime_commit),
            (config.previous_tag, config.previous_commit),
            (config.old_tag, config.old_commit),
        ),
        expected_applications=expected_applications,
        expected_source_graph=graph,
        kyverno_resources=kyverno_resources,
        runtime_tag=config.runtime_tag,
        previous_tag=config.previous_tag,
    )
