# Issue #348 v6 slice A RED → GREEN evidence

Scope: offline qualification of the sanitized Argo CD v3.4.5 ApplicationList projection. No live preflight, transition, cluster access, publication, tag, or release was performed.

## RED

Command:

    python3 platform/tests/test_issue348_live_contract.py LiveFixtureContractTest.test_captured_application_list_passes_production_status_validation -v

Observed against unchanged v5 validation:

    ERROR: test_captured_application_list_passes_production_status_validation
    TransitionError: Kyverno resource identity/status mismatch
    Ran 1 test ... FAILED (errors=1)

Root cause: Argo CD v3.4.5 omits `group` for core resources in `status.resources`; v5 required every resource to contain a string-valued `group`.

## GREEN contract

The production validator now canonicalizes only missing, null, or empty `group` to core `""`; retains non-empty groups exactly; validates API identity types; normalizes an absent/null/empty namespace to `""`; and compares the complete canonical `(group, version, kind, namespace, name) -> status` mapping with the reviewed 69-resource contract. Duplicate, omitted, additional, status-drifted, namespace-drifted, and identity-drifted resources fail closed.

Focused GREEN command:

    python3 platform/tests/test_issue348_live_contract.py -v

Observed result:

    Ran 8 tests ... OK

Pinned CLI parser smoke command:

    ARGOCD_V345_BINARY=/tmp/t_a4947472-evidence/argocd python3 platform/tests/test_issue348_argocd_v345_parser_smoke.py -v

Observed result:

    Ran 2 tests ... OK

The local binary was independently verified as SHA-256 `23303f05a58c1e041324d5645b0f9d6ea338b16bbf32f4a24508f388fcf9f9c0` and reported `argocd: v3.4.5+564b949`.

# Slice B architecture and preflight RED -> GREEN evidence

Scope: repository-only architecture, fake-adapter behavior, and static dependency qualification. No live preflight, transition, Kubernetes/Argo access, SSH, publication, tag, push, PR, merge, convergence, or acceptance was performed.

## B1 immutable Snapshot and pure Validator

Initial RED command:

    python3 platform/tests/test_issue348_runtime_v2_architecture.py -v

Observed result:

    ModuleNotFoundError: No module named 'issue348_runtime_v2_qualification'

Typed-decoder vertical RED command:

    python3 platform/tests/test_issue348_runtime_v2_architecture.py SnapshotValidatorTest.test_snapshot_decoder_rejects_untyped_identity -v

Observed result: `FAILED`; `QualificationError not raised`. GREEN after strict scalar/tag/invocation typing: `Ran 1 test ... OK`.

The immutable Snapshot stores canonical serialized projections so caller mutation cannot change the qualified value. The production transition and sanitized Argo CD v3.4.5 fixture both use `SnapshotDecoder` and the pure `Validator`; the pure module imports no filesystem, OS, or subprocess capability.

## B2 declarative MutationPlan

Vertical RED command:

    python3 platform/tests/test_issue348_runtime_v2_architecture.py MutationPlanTest.test_plan_rejects_tampered_validated_result -v

Observed result: `FAILED`; a forged `ValidatedSnapshot` was accepted. GREEN after mandatory revalidation: `Ran 1 test ... OK`.

The rendered normal plan is ordered controller barrier, Root owner, Argo owner, controller restore. The rollback envelope is Root owner, Argo owner, controller restore. Every entry carries exact target, UID, resourceVersion/readback token, path, current value, and replacement; construction rejects every target outside the three approved control-plane objects.

## B3 isolated Mutator

Vertical RED command:

    python3 platform/tests/test_issue348_runtime_v2_architecture.py MutatorBoundaryTest.test_mutator_rejects_replayed_rollback_operation -v

Observed result: `FAILED`; a rollback operation could be replayed. GREEN after single-consumption enforcement: `Ran 1 test ... OK`.

The transition no longer has a patch method. Only `issue348_runtime_v2_mutator.py` renders and invokes approved patch operations; sequence mismatch, altered operation, unknown target, and replay fail closed.

## B4 standalone non-mutating preflight

Vertical RED command:

    python3 platform/tests/test_issue348_runtime_v2_architecture.py PreflightEntrypointTest.test_preflight_rejects_adapter_with_write_capability -v

Observed result: `FAILED`; an adapter exposing `patch` was accepted. GREEN after capability rejection: `Ran 1 test ... OK`.

Fake-adapter tests also cover exclusive mode-`0600` evidence, reuse rejection, exact snapshot digest, exact identities and invocation, proposed plan, empty mutation trace, and `runtime_mutated=false`. Timeout, nonzero, stdout, and stderr Argo diff results fail closed. Static tests confirm the preflight/qualification dependency graph has no Mutator import or patch command. The production read-only adapter is present but was not invoked.

## B5 transition integration and counters

Vertical RED command:

    python3 platform/tests/test_issue348_runtime_v2_transition.py SecurityTest.test_evidence_separates_invocation_execution_and_first_mutation_counters -v

Observed result: `ERROR`; `controller-stopped` lacked `process_invocation_count`. GREEN after recording separate process-invocation, transition-execution, and first-mutation counters: `Ran 1 test ... OK`.

The protected transition now qualifies a Snapshot, obtains a validated result, builds a MutationPlan, and only then constructs the Mutator. Existing required arguments, CAS payloads, rollback, interruption, and convergence behavior remain under the prior behavioral suite.

## B6 documentation boundary

Vertical RED command:

    python3 platform/tests/test_issue348_runtime_v2_transition.py RunbookContractTest.test_runbook_documents_snapshot_plan_mutator_and_preflight_boundaries -v

Observed result: `FAILED`; the capability-boundary phrase was absent. GREEN after the runbook update: `Ran 1 test ... OK`.

The runbook describes architecture and invocation semantics without claiming publication, merge, live preflight success, convergence, cluster validation, or acceptance.

## Final local verification matrix

- Reserved-v6 identity correction: after changing only the candidate test expectation to `issue348-runtime-v6-20260919T100440Z`, `python3 platform/tests/test_issue348_runtime_v2_candidate.py -v` observed two expected failures because the transition still exposed the stale v5 tag. After updating the transition, committed contract, and affected graph expectations without changing runtime-closure manifests, the same command observed `Ran 5 tests ... OK`.
- Focused Issue #348 command in the isolated pinned Python 3.12 environment: `ARGOCD_V345_BINARY=/tmp/t_a4947472-evidence/argocd python3 -m unittest discover -s platform/tests -p 'test_issue348*.py' -v`; observed `Ran 115 tests ... OK`, zero skips.
- Exact parser-only command: `ARGOCD_V345_BINARY=/tmp/t_a4947472-evidence/argocd python3 platform/tests/test_issue348_argocd_v345_parser_smoke.py -v`; observed `Ran 2 tests ... OK`. The binary SHA-256 remained `23303f05a58c1e041324d5645b0f9d6ea338b16bbf32f4a24508f388fcf9f9c0` and version remained `argocd: v3.4.5+564b949`.
- Python compilation: isolated Python 3.12 `python3 -m py_compile` over all eight changed Issue #348 Python production and test files; passed.
- Repository lint: `make lint`; passed its available checks. `yamllint` was not installed and the target reported that check skipped. `kubeconfig-local.yaml` was absent, so no Kubernetes command ran.
- Authoritative broad platform command: an isolated `uv` Python 3.12 environment installed `.github/requirements-platform-validation-py312-linux.txt` with hashes, including `lupa==2.6`, then ran `ARGOCD_V345_BINARY=/tmp/t_a4947472-evidence/argocd make test`; observed `Ran 1180 tests ... OK (skipped=1)`.
- High-confidence secret scan over the fixture and changed Issue #348 production/spec files found zero candidate secrets. The test scan found only three existing synthetic redaction sentinels (`TOPSECRET`, `SUPERSECRET`, `RAWTOKEN`/`RAWPASS`), not credentials.
- `git diff --check` passed. Exact changed-path inventory and commit identities are recorded from Git after local commit.
