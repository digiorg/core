#!/usr/bin/env python3
"""Verify a completed post-publication Issue #348 release attestation."""

import argparse
import json
from pathlib import Path
import re
from typing import NamedTuple

TEMPLATE_PATH = Path("specs/345-log-schema-isolation/issue348-release-attestation.template.json")
SCHEMA_PATH = Path("specs/345-log-schema-isolation/issue348-release-attestation.schema.json")
SHA1 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
RUN_URL = re.compile(r"^https://github\.com/digiorg/core/actions/runs/[1-9][0-9]*$")
REVIEW_URL = re.compile(r"^https://github\.com/digiorg/core/pull/[1-9][0-9]*$")


class AttestationError(RuntimeError):
    """A release attestation is incomplete, malformed, moved, or stale."""


class ExpectedAttestation(NamedTuple):
    source_commit: str
    source_tree: str
    closure_commit: str
    closure_tree: str
    runtime_tag: str
    observed_tag_object: str
    observed_peeled_commit: str
    ci_run_url: str
    ci_head_sha: str
    source_review: str
    publication_review: str
    closure_manifest_sha256: str
    generator_version: str
    descriptor_sha256: str
    fixture_sha256: dict


def _exact_mapping(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise AttestationError(f"{label} fields are missing or unexpected")
    return value


def _identity(value, pattern, label):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise AttestationError(f"{label} is malformed")
    return value


def verify(document, expected):
    root = _exact_mapping(
        document,
        ("schema", "source", "closure", "tag", "ci", "reviews", "generator"),
        "attestation",
    )
    if root["schema"] != "issue348-release-attestation/v1":
        raise AttestationError("attestation schema is stale")
    source = _exact_mapping(root["source"], ("commit", "tree"), "source")
    closure = _exact_mapping(
        root["closure"], ("commit", "tree", "manifest_sha256"), "closure",
    )
    tag = _exact_mapping(root["tag"], ("name", "object", "peeled_commit"), "tag")
    ci = _exact_mapping(root["ci"], ("run_url", "head_sha"), "ci")
    reviews = _exact_mapping(
        root["reviews"], ("source_candidate", "publication_closure"), "reviews",
    )
    generator = _exact_mapping(
        root["generator"],
        ("version", "descriptor_sha256", "fixture_sha256"),
        "generator",
    )
    for label, value in (
        ("source commit", source["commit"]), ("source tree", source["tree"]),
        ("closure commit", closure["commit"]), ("closure tree", closure["tree"]),
        ("tag object", tag["object"]), ("peeled commit", tag["peeled_commit"]),
        ("CI head SHA", ci["head_sha"]),
    ):
        _identity(value, SHA1, label)
    for label, value in (
        ("closure manifest digest", closure["manifest_sha256"]),
        ("descriptor digest", generator["descriptor_sha256"]),
    ):
        _identity(value, SHA256, label)
    if not isinstance(generator["fixture_sha256"], dict) or not generator["fixture_sha256"]:
        raise AttestationError("fixture digests are missing")
    for name, digest in generator["fixture_sha256"].items():
        if not isinstance(name, str) or not name or not SHA256.fullmatch(digest or ""):
            raise AttestationError("fixture digest is malformed")
    if not isinstance(tag["name"], str) or tag["name"] != expected.runtime_tag:
        raise AttestationError("runtime tag is stale")
    if not isinstance(ci["run_url"], str) or not RUN_URL.fullmatch(ci["run_url"]):
        raise AttestationError("CI run reference is malformed")
    for value in reviews.values():
        if not isinstance(value, str) or not REVIEW_URL.fullmatch(value):
            raise AttestationError("review reference is malformed")
    if source != {"commit": expected.source_commit, "tree": expected.source_tree}:
        raise AttestationError("canonical source identity is stale")
    if closure["commit"] != expected.closure_commit or closure["tree"] != expected.closure_tree:
        raise AttestationError("generated closure identity is stale")
    if tag["object"] != expected.observed_tag_object:
        raise AttestationError("annotated tag object moved")
    if tag["peeled_commit"] != expected.observed_peeled_commit:
        raise AttestationError("peeled tag commit moved")
    if tag["peeled_commit"] != closure["commit"]:
        raise AttestationError("tag does not peel to closure commit")
    if ci != {"run_url": expected.ci_run_url, "head_sha": expected.ci_head_sha}:
        raise AttestationError("CI run identity is stale")
    if ci["head_sha"] != closure["commit"]:
        raise AttestationError("CI head is not the closure commit")
    if reviews != {
        "source_candidate": expected.source_review,
        "publication_closure": expected.publication_review,
    }:
        raise AttestationError("review reference is stale")
    if reviews["source_candidate"] == reviews["publication_closure"]:
        raise AttestationError("source and publication reviews must be separate")
    if tag["object"] == tag["peeled_commit"]:
        raise AttestationError("annotated tag object must be distinct from its peeled commit")
    if closure["manifest_sha256"] != expected.closure_manifest_sha256:
        raise AttestationError("closure manifest digest is stale")
    if generator["version"] != expected.generator_version:
        raise AttestationError("generator version is stale")
    if generator["descriptor_sha256"] != expected.descriptor_sha256:
        raise AttestationError("descriptor digest is stale")
    if generator["fixture_sha256"] != expected.fixture_sha256:
        raise AttestationError("fixture digests are stale")
    if closure["commit"] == source["commit"] or closure["tree"] == source["tree"]:
        raise AttestationError("closure identity is not distinct from source")
    return document


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attestation", required=True, type=Path)
    parser.add_argument("--expected", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        document = json.loads(args.attestation.read_text(encoding="utf-8"))
        expected = ExpectedAttestation(**json.loads(args.expected.read_text(encoding="utf-8")))
        verify(document, expected)
    except (OSError, json.JSONDecodeError, TypeError, AttestationError) as error:
        raise SystemExit(f"release attestation refused: {error}") from error


if __name__ == "__main__":
    main()
