#!/usr/bin/env python3
"""Architecture contracts for Issue #348 runtime-v2 qualification slice B."""

import ast
from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import issue348_runtime_v2_qualification as qualification
import issue348_runtime_v2_preflight as preflight
import issue348_runtime_v2_mutator as mutator

FIXTURE = ROOT / "platform/tests/fixtures/issue348/argocd-v3.4.5/application-list.json"
TRANSITION = SCRIPTS / "issue348_runtime_v2_transition.py"
PREFLIGHT = SCRIPTS / "issue348_runtime_v2_preflight.py"
QUALIFICATION = SCRIPTS / "issue348_runtime_v2_qualification.py"
MUTATOR = SCRIPTS / "issue348_runtime_v2_mutator.py"
NEW_TAG = "issue348-runtime-v6-20260919T100440Z"
PREVIOUS_TAG = "issue350-352-runtime-v3-20260904T195619Z"


def application(name, target, uid, resource_version):
    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {
            "name": name,
            "namespace": "argocd",
            "uid": uid,
            "resourceVersion": resource_version,
        },
        "spec": {"source": {
            "repoURL": "https://github.com/digiorg/core.git",
            "path": "apps" if name == "root-app" else "platform/base/argocd",
            "targetRevision": target,
        }},
        "status": {"health": {"status": "Healthy"}, "sync": {"status": "Synced"}},
    }


def sample_snapshot(*, diff_returncode=0, diff_stdout="", diff_stderr=""):
    apps = [
        application("root-app", PREVIOUS_TAG, "uid-root", "rv-root"),
        application("argocd", PREVIOUS_TAG, "uid-argo", "rv-argo"),
    ]
    controller = {
        "apiVersion": "apps/v1",
        "kind": "StatefulSet",
        "metadata": {
            "name": "argocd-application-controller",
            "namespace": "argocd",
            "uid": "uid-controller",
            "resourceVersion": "rv-controller",
            "generation": 4,
        },
        "spec": {"replicas": 1},
        "status": {
            "observedGeneration": 4,
            "replicas": 1,
            "readyReplicas": 1,
            "currentRevision": "controller-revision",
            "updateRevision": "controller-revision",
        },
    }
    return qualification.SnapshotDecoder.snapshot(
        remote_tags=((NEW_TAG, "a" * 40), (PREVIOUS_TAG, "b" * 40)),
        cluster_server="https://api.example:6443",
        kube_system_uid="uid-kube-system",
        application_list={
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "ApplicationList",
            "items": apps,
        },
        controller=controller,
        controller_pods=({
            "metadata": {
                "uid": "pod-old",
                "ownerReferences": [{"uid": "uid-controller", "controller": True}],
            },
            "status": {
                "phase": "Running",
                "conditions": [{"type": "Ready", "status": "True"}],
            },
        },),
        hpa_list={
            "apiVersion": "autoscaling/v2",
            "kind": "HorizontalPodAutoscalerList",
            "metadata": {},
            "items": [],
        },
        argocd_diff=qualification.ReadResult(
            diff_returncode, diff_stdout, diff_stderr,
        ),
        invocation=("preflight", "--mode", "retained-convergence"),
    )


def sample_contract():
    return qualification.ValidationContract(
        expected_server="https://api.example:6443",
        expected_kube_system_uid="uid-kube-system",
        expected_remote_tags=((NEW_TAG, "a" * 40), (PREVIOUS_TAG, "b" * 40)),
        expected_applications=("argocd", "root-app"),
        expected_source_graph=(
            ("argocd", (("https://github.com/digiorg/core.git", None,
                         "platform/base/argocd", None, PREVIOUS_TAG),)),
            ("root-app", (("https://github.com/digiorg/core.git", None,
                           "apps", None, PREVIOUS_TAG),)),
        ),
        kyverno_resources=(),
        runtime_tag=NEW_TAG,
        previous_tag=PREVIOUS_TAG,
    )


class SnapshotValidatorTest(unittest.TestCase):
    def test_snapshot_decoder_rejects_untyped_identity(self):
        snapshot = sample_snapshot()
        with self.assertRaisesRegex(qualification.QualificationError, "identity"):
            qualification.SnapshotDecoder.snapshot(
                remote_tags=snapshot.remote_tags,
                cluster_server=None,
                kube_system_uid=snapshot.kube_system_uid,
                application_list=json.loads(snapshot.application_list_json),
                controller=json.loads(snapshot.controller_json),
                controller_pods=json.loads(snapshot.controller_pods_json),
                hpa_list=json.loads(snapshot.hpa_list_json),
                argocd_diff=snapshot.argocd_diff,
                invocation=snapshot.invocation,
            )

    def test_snapshot_is_immutable_and_canonical(self):
        snapshot = sample_snapshot()
        with self.assertRaises(AttributeError):
            snapshot.cluster_server = "https://attacker.invalid"  # type: ignore[misc]
        decoded = qualification.SnapshotDecoder.applications(snapshot)
        decoded["root-app"]["metadata"]["uid"] = "changed"
        self.assertEqual(
            qualification.SnapshotDecoder.applications(snapshot)["root-app"]
            ["metadata"]["uid"],
            "uid-root",
        )
        self.assertEqual(len(snapshot.digest), 64)

    def test_pure_validator_consumes_snapshot_and_contract(self):
        validated = qualification.Validator.validate(sample_snapshot(), sample_contract())
        self.assertEqual(validated.controller_uid, "uid-controller")
        self.assertEqual(validated.controller_replicas, 1)
        self.assertEqual(validated.application_names, ("argocd", "root-app"))
        tree = ast.parse(QUALIFICATION.read_text(encoding="utf-8"))
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertTrue({"subprocess", "pathlib", "os"}.isdisjoint(imported))
        self.assertFalse(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                             and node.func.id == "open" for node in ast.walk(tree)))

    def test_live_fixture_uses_snapshot_decoder_and_validator(self):
        listing = qualification.SnapshotDecoder.decode_application_list(
            FIXTURE.read_text(encoding="utf-8")
        )
        kyverno = listing["kyverno"]
        expected = []
        for resource in kyverno["status"]["resources"]:
            expected.append((*qualification.canonical_resource_identity(resource),
                             resource["status"]))
        qualification.Validator.validate_application_statuses(
            listing, tuple(sorted(expected)), allow_only_kyverno=True,
        )

    def test_diff_timeout_nonzero_and_output_fail_closed(self):
        cases = (
            qualification.ReadResult(None, "", "command timeout"),
            qualification.ReadResult(2, "", "failed"),
            qualification.ReadResult(0, "material diff", ""),
            qualification.ReadResult(0, "", "warning"),
        )
        for result in cases:
            with self.subTest(result=result):
                snapshot = sample_snapshot(
                    diff_returncode=result.returncode,
                    diff_stdout=result.stdout,
                    diff_stderr=result.stderr,
                )
                with self.assertRaises(qualification.QualificationError):
                    qualification.Validator.validate(snapshot, sample_contract())


class MutationPlanTest(unittest.TestCase):
    def test_plan_rejects_tampered_validated_result(self):
        validated = qualification.Validator.validate(sample_snapshot(), sample_contract())
        tampered = validated._replace(controller_uid="replacement-controller")
        with self.assertRaisesRegex(qualification.QualificationError, "validated Snapshot"):
            qualification.MutationPlan.build(tampered, sample_contract())

    def test_normal_and_rollback_plans_are_exact_ordered_and_cas_bound(self):
        validated = qualification.Validator.validate(sample_snapshot(), sample_contract())
        plan = qualification.MutationPlan.build(validated, sample_contract())
        self.assertEqual([operation.name for operation in plan.normal], [
            "controller-barrier", "root-owner", "argocd-owner", "controller-restore",
        ])
        self.assertEqual(
            [(item.kind, item.namespace, item.resource_name) for item in plan.normal],
            [
                ("statefulsets.apps", "argocd", "argocd-application-controller"),
                ("applications.argoproj.io", "argocd", "root-app"),
                ("applications.argoproj.io", "argocd", "argocd"),
                ("statefulsets.apps", "argocd", "argocd-application-controller"),
            ],
        )
        for operation in plan.normal + plan.rollback:
            self.assertTrue(operation.uid)
            self.assertTrue(operation.resource_version)
            self.assertIsNotNone(operation.current_value)
        self.assertEqual([item.name for item in plan.rollback], [
            "rollback-root-owner", "rollback-argocd-owner", "rollback-controller",
        ])
        rendered = json.loads(plan.render())
        self.assertEqual(rendered["normal"][0]["preconditions"]["current_value"], 1)
        self.assertEqual(rendered["rollback"][0]["after"], PREVIOUS_TAG)

    def test_plan_rejects_unexpected_targets(self):
        validated = qualification.Validator.validate(sample_snapshot(), sample_contract())
        with self.assertRaisesRegex(qualification.QualificationError, "approved target"):
            qualification.MutationPlan.operation(
                "bad", "deployments.apps", "logging", "fluentd", "uid", "rv", 1, 0,
            )
        altered = deepcopy(qualification.SnapshotDecoder.applications(validated.snapshot))
        altered["unrelated"] = application("unrelated", PREVIOUS_TAG, "uid", "rv")
        self.assertNotIn("unrelated", json.loads(
            qualification.MutationPlan.build(validated, sample_contract()).render()
        )["normal"])


class MutatorBoundaryTest(unittest.TestCase):
    def test_mutator_rejects_replayed_rollback_operation(self):
        plan = qualification.MutationPlan.build(
            qualification.Validator.validate(sample_snapshot(), sample_contract()),
            sample_contract(),
        )
        executor = mutator.Mutator(plan, lambda operation, deadline: None)
        executor.rollback("rollback-root-owner", object())
        with self.assertRaisesRegex(mutator.MutationRejected, "approved plan"):
            executor.rollback("rollback-root-owner", object())

    def test_mutator_executes_only_the_next_approved_operation(self):
        plan = qualification.MutationPlan.build(
            qualification.Validator.validate(sample_snapshot(), sample_contract()),
            sample_contract(),
        )
        calls = []
        executor = mutator.Mutator(plan, lambda operation, deadline: calls.append(operation))
        with self.assertRaisesRegex(mutator.MutationRejected, "approved plan"):
            executor.execute("root-owner", object())
        executor.execute("controller-barrier", object())
        self.assertEqual([item.name for item in calls], ["controller-barrier"])
        with self.assertRaisesRegex(mutator.MutationRejected, "approved plan"):
            executor.execute_operation(plan.normal[1]._replace(resource_name="unrelated"), object())

    def test_write_capability_is_isolated_from_preflight_dependency_graph(self):
        preflight_text = PREFLIGHT.read_text(encoding="utf-8")
        qualification_text = QUALIFICATION.read_text(encoding="utf-8")
        for text in (preflight_text, qualification_text):
            self.assertNotIn("issue348_runtime_v2_mutator", text)
            self.assertNotIn("kubectl patch", text)
            self.assertNotIn("subprocess.run", qualification_text)
        self.assertIn("class Mutator", MUTATOR.read_text(encoding="utf-8"))
        transition_text = TRANSITION.read_text(encoding="utf-8")
        self.assertIn("issue348_runtime_v2_mutator", transition_text)
        self.assertIn("MutationPlan.build", transition_text)


class PreflightEntrypointTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.evidence = Path(self.temp.name) / "preflight.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_preflight_rejects_adapter_with_write_capability(self):
        class UnsafeAdapter:
            mutation_trace = ()

            def collect(self):
                return sample_snapshot()

            def patch(self):
                raise AssertionError("must be unreachable")

        with self.assertRaisesRegex(qualification.QualificationError, "write capability"):
            preflight.execute(UnsafeAdapter(), sample_contract(), self.evidence)
        self.assertFalse(self.evidence.exists())

    def test_fake_adapter_preflight_writes_exclusive_0600_nonmutating_evidence(self):
        class FakeAdapter:
            mutation_trace = ()

            def collect(self):
                return sample_snapshot()

        result = preflight.execute(
            FakeAdapter(), sample_contract(), self.evidence,
            invocation=("python3", "scripts/issue348_runtime_v2_preflight.py"),
        )
        self.assertFalse(result["runtime_mutated"])
        self.assertEqual(result["mutation_trace"], [])
        self.assertEqual(stat.S_IMODE(self.evidence.stat().st_mode), 0o600)
        written = json.loads(self.evidence.read_text(encoding="utf-8"))
        self.assertEqual(written, result)
        self.assertEqual(written["snapshot_digest"], sample_snapshot().digest)
        self.assertEqual(len(written["proposed_mutation_plan"]["normal"]), 4)
        self.assertEqual(written["identities"]["controller_uid"], "uid-controller")
        with self.assertRaisesRegex(qualification.QualificationError, "must not exist"):
            preflight.execute(FakeAdapter(), sample_contract(), self.evidence)

    def test_fake_adapter_cannot_expose_a_mutation_trace(self):
        class UnsafeAdapter:
            mutation_trace = ("write",)

            def collect(self):
                return sample_snapshot()

        with self.assertRaisesRegex(qualification.QualificationError, "mutation trace"):
            preflight.execute(UnsafeAdapter(), sample_contract(), self.evidence)
        self.assertFalse(self.evidence.exists())

    def test_preflight_diff_failures_create_no_evidence(self):
        results = (
            qualification.ReadResult(None, "", "timeout"),
            qualification.ReadResult(2, "", "failed"),
            qualification.ReadResult(0, "material diff", ""),
            qualification.ReadResult(0, "", "warning"),
        )
        for result in results:
            with self.subTest(result=result):
                class FailedDiffAdapter:
                    mutation_trace = ()

                    def collect(self):
                        snapshot = sample_snapshot()
                        return snapshot._replace(argocd_diff=result)

                with self.assertRaises(qualification.QualificationError):
                    preflight.execute(
                        FailedDiffAdapter(), sample_contract(), self.evidence
                    )
                self.assertFalse(self.evidence.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
