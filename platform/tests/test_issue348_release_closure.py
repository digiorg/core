#!/usr/bin/env python3
"""Offline contracts for Issue #348 deterministic release closure and attestation."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import issue348_runtime_v2_release_closure as closure
import issue348_release_attestation as attestation

RUNTIME_TAG = "issue348-runtime-v6-20260919T100440Z"
PREVIOUS_TAG = "issue350-352-runtime-v3-20260904T195619Z"
PREVIOUS_COMMIT = "f6e7d58c0b03ee6a3ec6ed9e1e22e5023f861549"
RETAINED_TAG = "issue301-runtime-v16-20260817T130820Z"
RETAINED_COMMIT = "8e6b8908f99ebf76db47c15613eff523644c23f6"
DESCRIPTOR = Path("specs/345-log-schema-isolation/issue348-runtime-v2-contract.json")
OUTPUT = Path("issue348-runtime-v2-release-closure.json")
CANONICAL_SOURCE_COMMIT = "743a1b08435f8a6e613ed8e59ba92fb062452d9f"
CANONICAL_SOURCE_TREE = "16ec64176d1bdaeb4b450269c9c1a677dede744d"
SOURCE_BUNDLE = Path("platform/tests/fixtures/issue348/release-closure-source.json")
FIXTURES = (
    Path("platform/tests/fixtures/issue348/argocd-v3.4.5/application-list.json"),
    Path("platform/tests/fixtures/issue348/argocd-v3.4.5/manifest.json"),
)


def run_git(repo, *args, check=True):
    result = subprocess.run(
        ["git", *args], cwd=repo, check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if check and result.returncode:
        raise AssertionError(result.stderr)
    return result


def commit_all(repo, message):
    run_git(repo, "add", "--all")
    env = {
        "GIT_AUTHOR_DATE": "2026-09-19T10:04:40Z",
        "GIT_COMMITTER_DATE": "2026-09-19T10:04:40Z",
    }
    result = subprocess.run(
        ["git", "commit", "-m", message], cwd=repo, check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={**__import__("os").environ, **env},
    )
    if result.returncode:
        raise AssertionError(result.stderr)


def _source_bundle():
    value = json.loads((ROOT / SOURCE_BUNDLE).read_text(encoding="utf-8"))
    if set(value) != {"schema", "source", "files"}:
        raise AssertionError("source fixture bundle shape changed")
    if value["schema"] != "issue348-release-closure-source-fixture/v1":
        raise AssertionError("source fixture bundle schema changed")
    if value["source"] != {
            "commit": CANONICAL_SOURCE_COMMIT, "tree": CANONICAL_SOURCE_TREE}:
        raise AssertionError("source fixture provenance changed")
    if set(value["files"]) != set(closure.APPLICATION_PATHS):
        raise AssertionError("source fixture inventory changed")
    if set(value["files"]) != set(closure.SOURCE_DIGESTS):
        raise AssertionError("source digest inventory changed")

    actual_tree = run_git(
        ROOT, "rev-parse", f"{CANONICAL_SOURCE_COMMIT}^{{tree}}",
    ).stdout.strip()
    if actual_tree != CANONICAL_SOURCE_TREE:
        raise AssertionError("canonical source tree changed")

    files = {}
    for relative in closure.APPLICATION_PATHS:
        entry = value["files"][relative]
        if set(entry) != {"content", "sha256"}:
            raise AssertionError(f"source fixture entry shape changed: {relative}")
        data = entry["content"].encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        if digest != entry["sha256"] or digest != closure.SOURCE_DIGESTS[relative]:
            raise AssertionError(f"source fixture digest changed: {relative}")
        result = subprocess.run(
            ["git", "show", f"{CANONICAL_SOURCE_COMMIT}:{relative}"],
            cwd=ROOT, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if result.returncode or result.stdout != data:
            raise AssertionError(f"source fixture differs from canonical commit: {relative}")
        files[relative] = data
    return files


def _generated_source(bundle):
    generated = dict(bundle)
    rules_by_path = {}
    for path, pointer in closure.ALLOWED_FIELDS:
        rules_by_path.setdefault(path, []).append(pointer)
    for relative, pointers in rules_by_path.items():
        text = bundle[relative].decode("utf-8")
        replacements = []
        for pointer in pointers:
            node = closure._scalar_node(text, pointer)
            if text[node.start_mark.index:node.end_mark.index] != "main":
                raise AssertionError(f"source fixture is not canonical: {relative} {pointer}")
            replacements.append((
                node.start_mark.index,
                node.end_mark.index,
                closure.expected_target(relative, pointer),
            ))
        for start, end, target in sorted(replacements, reverse=True):
            text = text[:start] + target + text[end:]
        generated[relative] = text.encode("utf-8")
    return generated


def _expected_changes():
    return sorted(({
        "path": path,
        "field": pointer,
        "before": "main",
        "after": closure.expected_target(path, pointer),
    } for path, pointer in closure.ALLOWED_FIELDS), key=lambda item: (
        item["path"], item["field"],
    ))


def _validate_checkout_source(root, bundle):
    actual = {relative: (root / relative).read_bytes()
              for relative in closure.APPLICATION_PATHS}
    if actual == bundle:
        return
    if actual != _generated_source(bundle):
        raise AssertionError("checkout is neither canonical source nor exact generated closure")

    manifest = json.loads((root / OUTPUT).read_text(encoding="utf-8"))
    if set(manifest) != {
            "schema", "generator_version", "source", "reserved_runtime_tag",
            "predecessor", "retained", "expected_graph", "changes",
            "descriptor_sha256", "fixture_digests"}:
        raise AssertionError("generated closure inventory shape changed")
    if manifest["schema"] != "issue348-runtime-v2-release-closure/v1":
        raise AssertionError("generated closure schema changed")
    if manifest["generator_version"] != closure.GENERATOR_VERSION:
        raise AssertionError("generated closure version changed")
    if manifest["source"] != {
            "commit": run_git(root, "rev-parse", "HEAD").stdout.strip(),
            "tree": run_git(root, "rev-parse", "HEAD^{tree}").stdout.strip()}:
        raise AssertionError("generated closure source identity changed")
    if manifest["reserved_runtime_tag"] != RUNTIME_TAG:
        raise AssertionError("generated closure runtime tag changed")
    if manifest["predecessor"] != {
            "tag": PREVIOUS_TAG, "commit": PREVIOUS_COMMIT}:
        raise AssertionError("generated closure predecessor changed")
    if manifest["retained"] != {
            "tag": RETAINED_TAG, "commit": RETAINED_COMMIT}:
        raise AssertionError("generated closure retained identity changed")
    if manifest["expected_graph"] != {
            "candidate": 5, "retained": 27, "previous": 0, "other": 0}:
        raise AssertionError("generated closure graph changed")
    if manifest["changes"] != _expected_changes():
        raise AssertionError("generated closure changes changed")
    descriptor_digest = hashlib.sha256((root / DESCRIPTOR).read_bytes()).hexdigest()
    if manifest["descriptor_sha256"] != descriptor_digest:
        raise AssertionError("generated closure descriptor digest changed")
    fixture_digests = {
        path.as_posix(): hashlib.sha256((root / path).read_bytes()).hexdigest()
        for path in FIXTURES
    }
    if manifest["fixture_digests"] != fixture_digests:
        raise AssertionError("generated closure live-fixture digests changed")


def build_seed(destination, checkout_root):
    bundle = _source_bundle()
    _validate_checkout_source(checkout_root, bundle)
    destination.mkdir()
    run_git(destination, "init", "-b", "main")
    run_git(destination, "config", "user.email", "issue348@example.invalid")
    run_git(destination, "config", "user.name", "Issue 348 Fixture")
    (destination / "BASE").write_text("canonical base\n", encoding="utf-8")
    commit_all(destination, "base")
    base_commit = run_git(destination, "rev-parse", "HEAD").stdout.strip()

    for relative, data in bundle.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for relative in (DESCRIPTOR, *FIXTURES):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(checkout_root / relative, target)
    protected = destination / "scripts/protected.py"
    protected.parent.mkdir(parents=True, exist_ok=True)
    protected.write_text("PROTECTED = True\n", encoding="utf-8")
    commit_all(destination, "canonical merged source")
    return base_commit


class ClosureHarness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed_temp = tempfile.TemporaryDirectory()
        cls.seed = Path(cls.seed_temp.name) / "seed"
        cls.base_commit = build_seed(cls.seed, ROOT)

    @classmethod
    def tearDownClass(cls):
        cls.seed_temp.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        shutil.copytree(self.seed, self.repo)

    def tearDown(self):
        self.temp.cleanup()

    def identities(self):
        return (
            run_git(self.repo, "rev-parse", "HEAD").stdout.strip(),
            run_git(self.repo, "rev-parse", "HEAD^{tree}").stdout.strip(),
        )

    def config(self, **changes):
        source_commit, source_tree = self.identities()
        values = {
            "worktree": self.repo,
            "source_commit": source_commit,
            "source_tree": source_tree,
            "expected_branch": "main",
            "required_base": self.base_commit,
            "runtime_tag": RUNTIME_TAG,
            "descriptor": self.repo / DESCRIPTOR,
            "previous_tag": PREVIOUS_TAG,
            "previous_commit": PREVIOUS_COMMIT,
            "retained_tag": RETAINED_TAG,
            "retained_commit": RETAINED_COMMIT,
            "output": self.repo / OUTPUT,
        }
        values.update(changes)
        return closure.ClosureConfig(**values)

    def recommit(self, message="sabotage"):
        commit_all(self.repo, message)


class DeterministicClosureTest(ClosureHarness):
    def test_generated_checkout_can_build_canonical_harness_seed(self):
        closure.generate(self.config())
        rebuilt = Path(self.temp.name) / "rebuilt"
        rebuilt_base = build_seed(rebuilt, self.repo)
        source_commit = run_git(rebuilt, "rev-parse", "HEAD").stdout.strip()
        source_tree = run_git(rebuilt, "rev-parse", "HEAD^{tree}").stdout.strip()
        manifest = closure.generate(closure.ClosureConfig(
            worktree=rebuilt,
            source_commit=source_commit,
            source_tree=source_tree,
            expected_branch="main",
            required_base=rebuilt_base,
            runtime_tag=RUNTIME_TAG,
            descriptor=rebuilt / DESCRIPTOR,
            previous_tag=PREVIOUS_TAG,
            previous_commit=PREVIOUS_COMMIT,
            retained_tag=RETAINED_TAG,
            retained_commit=RETAINED_COMMIT,
            output=rebuilt / OUTPUT,
        ))

        self.assertEqual(len(manifest["changes"]), 32)
        self.assertEqual(
            {(item["path"], item["field"]) for item in manifest["changes"]},
            set(closure.ALLOWED_FIELDS),
        )

    def test_output_is_byte_identical_and_exactly_allowlisted(self):
        second = Path(self.temp.name) / "second"
        shutil.copytree(self.seed, second)
        first_manifest = closure.generate(self.config())
        second_config = self.config(
            worktree=second,
            descriptor=second / DESCRIPTOR,
            output=second / OUTPUT,
        )
        second_manifest = closure.generate(second_config)

        self.assertEqual((self.repo / OUTPUT).read_bytes(), (second / OUTPUT).read_bytes())
        self.assertEqual(first_manifest, second_manifest)
        inventory = json.loads((self.repo / OUTPUT).read_text(encoding="utf-8"))
        self.assertNotIn("closure_commit", inventory)
        self.assertNotIn("closure_tree", inventory)
        self.assertEqual(inventory["expected_graph"], {
            "candidate": 5, "other": 0, "previous": 0, "retained": 27,
        })
        self.assertEqual(len(inventory["changes"]), 32)
        self.assertEqual(
            {(item["path"], item["field"]) for item in inventory["changes"]},
            set(closure.ALLOWED_FIELDS),
        )
        changed = set(run_git(self.repo, "diff", "--name-only").stdout.splitlines())
        self.assertEqual(changed, {path for path, _ in closure.ALLOWED_FIELDS})
        self.assertEqual(
            run_git(self.repo, "ls-files", "--others", "--exclude-standard").stdout.splitlines(),
            [OUTPUT.as_posix()],
        )
        for fixture in FIXTURES:
            self.assertEqual(
                inventory["fixture_digests"][fixture.as_posix()],
                hashlib.sha256((self.repo / fixture).read_bytes()).hexdigest(),
            )

    def test_external_and_non_target_fields_are_unchanged(self):
        before = {
            path: yaml.safe_load((self.repo / path).read_text(encoding="utf-8"))
            for path, _ in closure.ALLOWED_FIELDS
        }
        closure.generate(self.config())
        for path in sorted(before):
            after = yaml.safe_load((self.repo / path).read_text(encoding="utf-8"))
            expected = deepcopy(before[path])
            for allowed_path, pointer in closure.ALLOWED_FIELDS:
                if allowed_path == path:
                    closure.replace_pointer(
                        expected, pointer, closure.expected_target(path, pointer),
                    )
            self.assertEqual(after, expected, path)

    def test_dirty_wrong_revision_tree_branch_base_and_existing_tag_fail(self):
        cases = (
            (lambda: (self.repo / "dirty").write_text("x", encoding="utf-8"), {}, "clean"),
            (lambda: None, {"source_commit": "f" * 40}, "source commit"),
            (lambda: None, {"source_tree": "f" * 40}, "source tree"),
            (lambda: run_git(self.repo, "checkout", "-b", "wrong"), {}, "branch"),
            (lambda: None, {"required_base": "f" * 40}, "base"),
            (lambda: run_git(self.repo, "tag", RUNTIME_TAG), {}, "tag"),
        )
        for prepare, changes, message in cases:
            with self.subTest(message=message):
                shutil.rmtree(self.repo)
                shutil.copytree(self.seed, self.repo)
                prepare()
                with self.assertRaisesRegex(closure.ClosureError, message):
                    closure.generate(self.config(**changes))
                self.assertFalse((self.repo / OUTPUT).exists())

    def test_stale_descriptor_and_stale_v5_v4_source_identity_fail(self):
        descriptor = json.loads((self.repo / DESCRIPTOR).read_text(encoding="utf-8"))
        descriptor["runtime_tag"] = "issue348-runtime-v5-20260918T181846Z"
        (self.repo / DESCRIPTOR).write_text(json.dumps(descriptor), encoding="utf-8")
        self.recommit()
        with self.assertRaisesRegex(closure.ClosureError, "descriptor"):
            closure.generate(self.config())

        for stale in (
                "issue348-runtime-v5-20260918T181846Z",
                "issue348-runtime-v4-20260918T162845Z"):
            with self.subTest(stale=stale):
                shutil.rmtree(self.repo)
                shutil.copytree(self.seed, self.repo)
                path = self.repo / "apps/platform/fluentd.yaml"
                path.write_text(path.read_text().replace("targetRevision: main", f"targetRevision: {stale}"), encoding="utf-8")
                self.recommit()
                with self.assertRaisesRegex(closure.ClosureError, "source graph|reviewed source"):
                    closure.generate(self.config())

    def test_unknown_duplicate_malformed_and_extra_manifest_change_fail(self):
        mutations = (
            (lambda text: text.replace("path: platform/base/fluentd", "path: platform/base/unknown"), "source graph"),
            (lambda text: text.replace("  destination:", "  source:\n    repoURL: https://github.com/digiorg/core.git\n    targetRevision: main\n    path: platform/base/fluentd\n  destination:"), "YAML|duplicate|shape"),
            (lambda text: text + "broken: [\n", "YAML"),
            (lambda text: text.replace("  project: default\n", "  project: default\n  unexpected: true\n", 1), "reviewed source"),
        )
        for mutate, message in mutations:
            with self.subTest(message=message):
                shutil.rmtree(self.repo)
                shutil.copytree(self.seed, self.repo)
                path = self.repo / "apps/platform/fluentd.yaml"
                path.write_text(mutate(path.read_text(encoding="utf-8")), encoding="utf-8")
                self.recommit()
                with self.assertRaisesRegex(closure.ClosureError, message):
                    closure.generate(self.config())

    def test_protected_output_path_is_rejected(self):
        with self.assertRaisesRegex(closure.ClosureError, "output path"):
            closure.generate(self.config(output=self.repo / "scripts/closure.json"))
        self.assertEqual((self.repo / "scripts/protected.py").read_text(), "PROTECTED = True\n")

    def test_existing_nonancestor_base_is_rejected(self):
        tree = run_git(self.repo, "rev-parse", "HEAD^{tree}").stdout.strip()
        result = subprocess.run(
            ["git", "commit-tree", tree], cwd=self.repo, check=False, text=True,
            input="unrelated base\n", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={
                **__import__("os").environ,
                "GIT_AUTHOR_NAME": "Issue 348 Fixture",
                "GIT_AUTHOR_EMAIL": "issue348@example.invalid",
                "GIT_COMMITTER_NAME": "Issue 348 Fixture",
                "GIT_COMMITTER_EMAIL": "issue348@example.invalid",
                "GIT_AUTHOR_DATE": "2026-09-19T10:04:40Z",
                "GIT_COMMITTER_DATE": "2026-09-19T10:04:40Z",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        with self.assertRaisesRegex(closure.ClosureError, "ancestor"):
            closure.generate(self.config(required_base=result.stdout.strip()))


class ReleaseAttestationTest(unittest.TestCase):
    def valid(self):
        return {
            "schema": "issue348-release-attestation/v1",
            "source": {"commit": "a" * 40, "tree": "b" * 40},
            "closure": {"commit": "c" * 40, "tree": "d" * 40,
                        "manifest_sha256": "1" * 64},
            "tag": {"name": RUNTIME_TAG, "object": "e" * 40,
                    "peeled_commit": "c" * 40},
            "ci": {"run_url": "https://github.com/digiorg/core/actions/runs/123",
                   "head_sha": "c" * 40},
            "reviews": {
                "source_candidate": "https://github.com/digiorg/core/pull/400",
                "publication_closure": "https://github.com/digiorg/core/pull/401",
            },
            "generator": {"version": closure.GENERATOR_VERSION,
                          "descriptor_sha256": "2" * 64,
                          "fixture_sha256": {"fixture": "3" * 64}},
        }

    def expected(self):
        value = self.valid()
        return attestation.ExpectedAttestation(
            source_commit=value["source"]["commit"],
            source_tree=value["source"]["tree"],
            closure_commit=value["closure"]["commit"],
            closure_tree=value["closure"]["tree"],
            runtime_tag=RUNTIME_TAG,
            observed_tag_object=value["tag"]["object"],
            observed_peeled_commit=value["tag"]["peeled_commit"],
            ci_run_url=value["ci"]["run_url"],
            ci_head_sha=value["ci"]["head_sha"],
            source_review=value["reviews"]["source_candidate"],
            publication_review=value["reviews"]["publication_closure"],
            closure_manifest_sha256=value["closure"]["manifest_sha256"],
            generator_version=closure.GENERATOR_VERSION,
            descriptor_sha256=value["generator"]["descriptor_sha256"],
            fixture_sha256=value["generator"]["fixture_sha256"],
        )

    def test_complete_consistent_attestation_verifies(self):
        value = self.valid()
        self.assertEqual(attestation.verify(value, self.expected()), value)

    def test_schema_accepts_completed_record_and_rejects_template(self):
        schema = json.loads((ROOT / attestation.SCHEMA_PATH).read_text(encoding="utf-8"))
        validator = jsonschema.Draft202012Validator(schema)
        validator.validate(self.valid())
        template = json.loads((ROOT / attestation.TEMPLATE_PATH).read_text(encoding="utf-8"))
        self.assertTrue(list(validator.iter_errors(template)))

    def test_missing_malformed_inconsistent_moved_and_stale_identities_fail(self):
        cases = []
        missing = self.valid(); del missing["closure"]["tree"]; cases.append((missing, self.expected()))
        malformed = self.valid(); malformed["tag"]["object"] = "not-a-sha"; cases.append((malformed, self.expected()))
        inconsistent = self.valid(); inconsistent["ci"]["head_sha"] = "f" * 40; cases.append((inconsistent, self.expected()))
        moved = self.expected()._replace(observed_tag_object="f" * 40); cases.append((self.valid(), moved))
        stale = self.expected()._replace(descriptor_sha256="f" * 64); cases.append((self.valid(), stale))
        stale_ci = self.expected()._replace(ci_run_url="https://github.com/digiorg/core/actions/runs/999"); cases.append((self.valid(), stale_ci))
        stale_review = self.expected()._replace(source_review="https://github.com/digiorg/core/pull/999"); cases.append((self.valid(), stale_review))
        for value, expected in cases:
            with self.subTest(value=value):
                with self.assertRaises(attestation.AttestationError):
                    attestation.verify(value, expected)

    def test_template_does_not_fabricate_future_identities(self):
        template = json.loads((ROOT / attestation.TEMPLATE_PATH).read_text(encoding="utf-8"))
        self.assertEqual(template["schema"], "issue348-release-attestation/v1")
        for section, field in (
            ("source", "commit"), ("source", "tree"),
            ("closure", "commit"), ("closure", "tree"),
            ("tag", "object"), ("tag", "peeled_commit"),
            ("ci", "head_sha"),
        ):
            self.assertIsNone(template[section][field])


class ValidationAddendumTest(unittest.TestCase):
    def test_addendum_has_stable_requirements_and_truthful_ordered_status(self):
        text = (ROOT / "specs/345-log-schema-isolation/validation-348.md").read_text(encoding="utf-8")
        for requirement in range(1, 17):
            self.assertIn(f"I348-V{requirement:02d}", text)
        for pending in (
            "source-candidate review", "pull request", "exact-SHA CI", "squash merge",
            "publication", "post-publication preflight", "convergence", "30-minute acceptance",
        ):
            self.assertRegex(text, rf"(?i){pending}.*pending")
        self.assertIn("runtime_mutated=false", text)
        self.assertIn("no automatic retry", text)


class DocumentationReconciliationTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(os.environ.get("ISSUE348_DOC_ROOT", ROOT))

    def test_runbook_defines_generator_and_attestation_boundaries(self):
        text = (self.root / "specs/345-log-schema-isolation/runtime-v2-transition.md").read_text(encoding="utf-8")
        for phrase in (
            "issue348_runtime_v2_release_closure.py",
            "5 candidate / 27 retained / 0 previous / 0 other",
            "issue348-release-attestation.template.json",
            "must not be run in the source-candidate worktree",
            "future commit, tree, tag-object, CI, and review identities remain null",
        ):
            self.assertIn(phrase, text)

    def test_tasks_record_pr349_history_without_claiming_runtime_validation(self):
        text = (self.root / "specs/345-log-schema-isolation/tasks.md").read_text(encoding="utf-8")
        for identity in (
            "4fa6dbb76f906a3c5727c87adfa10a588221e447",
            "d8f93d64811f54a5f3ddb0a4f193b5bc08d3b894",
            "33329044341", "33329042253",
        ):
            self.assertIn(identity, text)
        for pending in (
            "Review and deliver the separate Issue #348 source candidate",
            "Generate and review the deterministic post-merge runtime closure",
            "Run the separately authorized post-publication read-only preflight",
            "Obtain separate retained-convergence authorization",
        ):
            self.assertIn(f"[ ] {pending}", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
