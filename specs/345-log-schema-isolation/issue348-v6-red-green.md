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

# Slice C deterministic release closure RED -> GREEN evidence

Scope: repository-reviewed offline generation, attestation, validation-child documentation, and historical runbook/task reconciliation. Every generator execution used an isolated temporary Git repository copied from the reviewed manifests. The generator was not run against this worktree, and no real closure manifest, tag, attestation, publication, GitHub write, SSH session, cluster contact, preflight, transition, convergence, or acceptance occurred.

## C1 deterministic generated runtime closure

Initial RED command:

    python3 platform/tests/test_issue348_release_closure.py -v

Observed result before production modules existed:

    ModuleNotFoundError: No module named 'issue348_runtime_v2_release_closure'

GREEN uses a fixed 32-field `(path, JSON pointer)` allowlist and reviewed pre-closure byte digests for all 32 Application manifests plus Root. It verifies clean exact worktree/branch/HEAD/tree, required-base ancestry, committed source paths, reserved-tag absence, descriptor identities/shape, exact baseline source graph, and manifest digests before writing. It replaces only scalar `targetRevision: main` values, preserves every other byte and semantic field, compares the tracked-file hash inventory before/after, and permits only one deterministic untracked closure-manifest path.

The generated inventory contains source commit/tree, reserved tag, exact 32 changed paths/fields and before/after values, predecessor/retained identities, **5 candidate / 27 retained / 0 previous / 0 other**, generator version, descriptor SHA-256, and fixture SHA-256 values. It intentionally omits future closure commit/tree. Tests prove byte-identical output and reject dirty input, wrong commit/tree/branch/base, non-ancestor base, existing tag, stale descriptor, v5/v4 source identity, unknown/duplicate sources, malformed YAML, extra manifest fields, and protected output paths.

## C2 release-attestation contract

Schema RED command:

    python3 platform/tests/test_issue348_release_closure.py ReleaseAttestationTest.test_schema_accepts_completed_record_and_rejects_template -v

Observed result:

    ValidationError: Additional properties are not allowed ('manifest_sha256' was unexpected)

GREEN corrected the closure schema to bind commit, tree, and manifest digest as one closed object. A second vertical RED added independently expected closure, CI, and review identities and initially failed with `TypeError: ExpectedAttestation.__new__() got an unexpected keyword argument 'closure_commit'`. GREEN now rejects missing, malformed, inconsistent, moved, or stale source/closure/tag/CI/review/generator/descriptor/fixture identities. The annotated tag object must differ from and peel to the closure commit; exact CI head must equal the closure commit; source and publication review references must be separate. The source template leaves all unknown future identities null and is deliberately schema-invalid until completed.

## C3 stable Issue #348 validation child

RED command:

    python3 platform/tests/test_issue348_release_closure.py ValidationAddendumTest -v

Observed result:

    FileNotFoundError: specs/345-log-schema-isolation/validation-348.md

GREEN adds `validation-348.md` with stable `I348-V01` through `I348-V16` requirements for fixture provenance/raw shape, exact parser, capability separation, non-mutating preflight, deterministic closure, reviews/delivery/publication, separately authorized preflight/convergence/acceptance, attempt ledger/evidence, rollback, and no-retry. Its ordered status marks only offline implementation complete and every review, PR, hosted CI, merge, publication, live preflight, convergence, and acceptance gate pending.

## C4 tasks and runbook reconciliation

Sabotage RED command against the unchanged `HEAD` documentation snapshot:

    ISSUE348_DOC_ROOT=/tmp/t_9f5837c3-c4-red python3 platform/tests/test_issue348_release_closure.py DocumentationReconciliationTest -v

Observed result: `Ran 2 tests ... FAILED (failures=2)` because the starting runbook had no generator/attestation boundary and the task list still claimed PR #349 delivery and merge were pending.

GREEN command against the candidate documentation:

    python3 platform/tests/test_issue348_release_closure.py DocumentationReconciliationTest -v

Observed result: `Ran 2 tests ... OK`. The task list now records PR #349 head `4fa6dbb76f906a3c5727c87adfa10a588221e447`, successful workflow runs `33329044341` and `33329042253`, and human merge commit `d8f93d64811f54a5f3ddb0a4f193b5bc08d3b894`, while explicitly retaining incomplete review/runtime gates. The runbook separates post-merge generation, attestation, publication-closure review, publication, read-only preflight, convergence, and acceptance.

## Slice C and whole-branch GREEN matrix

- Slice C focused: `python3 platform/tests/test_issue348_release_closure.py -v`; `Ran 14 tests ... OK`.
- All Issue #348 tests: `ARGOCD_V345_BINARY=/tmp/t_a4947472-evidence/argocd python3 -m unittest discover -s platform/tests -p 'test_issue348*.py'`; `Ran 129 tests ... OK`, zero skips.
- Exact parser gate: `ARGOCD_V345_BINARY=/tmp/t_a4947472-evidence/argocd python3 platform/tests/test_issue348_argocd_v345_parser_smoke.py -v`; `Ran 2 tests ... OK`. Binary SHA-256: `23303f05a58c1e041324d5645b0f9d6ea338b16bbf32f4a24508f388fcf9f9c0`; version: `argocd: v3.4.5+564b949`.
- Python compilation: pinned Python 3.12.13 `python3 -m py_compile scripts/issue348*.py platform/tests/test_issue348*.py`; passed.
- JSON/schema/template checks: all three committed JSON documents parse; Draft 2020-12 validation accepts a completed attestation and rejects the null template.
- Generator sabotage/property coverage: included in the 14 Slice C tests; all temporary repositories, no configured remotes, byte-identical dual generation, exact tracked/untracked inventory, and all required refusal cases passed.
- Platform render: `python3 scripts/render_platform_charts.py`; `HELM_RENDER_PASS=13; no floating rendered image tags` (existing exact-tag-only image warnings remain).
- Repository lint: `make lint`; passed available checks. `yamllint` was not installed and was explicitly skipped; `kubeconfig-local.yaml` was absent, so no Kubernetes command ran.
- High-confidence secret scan over Issue #348 scripts/tests/specs: zero private-key, AWS key, GitHub token, Slack token, or long Bearer-token matches.
- Authoritative whole branch: isolated Python 3.12.13 environment installed the hash-pinned requirements including `lupa==2.6`; `ARGOCD_V345_BINARY=/tmp/t_a4947472-evidence/argocd make test`; `Ran 1194 tests ... OK (skipped=1)`.
- `git diff --check` passed. The real closure output path is absent and the reserved v6 tag does not exist locally.

The one full-suite skip is pre-existing and outside Issue #348; the Issue #348 suite has zero skips. No source review, PR, exact-SHA hosted CI, source merge, closure generation, tag, attestation completion, publication review, publication, live preflight, convergence, or acceptance is claimed.
