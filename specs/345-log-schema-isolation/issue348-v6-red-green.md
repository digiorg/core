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
