# Issue 375 pinned component tests

This is test infrastructure, not a replacement production binary. It imports
unchanged Kyverno v1.18.1 APIs, including the same schema/default policy loader,
full `policy.Validate` and CLI `PolicyProcessor` used by `kyverno apply`.
No policy field is removed, no validator error is ignored, no upstream source is
patched, and the production policy/engine versions are unchanged. The exact
upstream pod-security-admission module replacement is carried into go.mod because
Go does not inherit replacements from dependencies. go.sum locks dependencies.

The pinned CLI's apply wrapper calls Validate with a nil client, even in cluster
mode. With spec.webhookConfiguration.matchConditions this dereferences nil at
pkg/validation/policy/validate.go:139. The adapter supplies synthetic discovery
reporting Kubernetes v1.35.4 for that version check; its mock=true validator mode
is identical to the CLI's. Full schema/default loading still runs and invalid
match-condition CEL is rejected. The synthetic validator emits upstream warnings
about missing VAP-generation permissions: these are fixture limitations, not a
claim of live permission verification or accepted production warnings.

The existing Python publisher and owner-GET matrices retain every case and
security assertion. Only their executable selection changes to the explicit
KYVERNO_COMPONENT path. The processor's store/variables/user/engine path is the
unchanged upstream implementation. Offline publisher cases supply an Object
context fixture, as before. Owner cases perform actual HTTP GET against an
isolated loopback API and assert that exact path was contacted; no fake GET
result is substituted in that matrix. Only explicit credential-free HTTP IPv4
loopback kubeconfigs are accepted. No ambient kubeconfig is read.

`matcher_test.go` directly executes the real Kubernetes apiserver v0.35.4
webhook match-condition compiler/matcher against the actual candidate expression.
False means exclusion before webhook invocation, including ordinary ConfigMap
CREATE/UPDATE/DELETE. True preserves old/new label stripping, folder protection,
canonical names, foreign replacement, null-object behavior and namespace effects.
This is component proof, NOT an installed webhook or outage test. The Python CEL
projection test remains on the unmodified pinned CLI, separately attributed.

Reproduce (Go 1.26.1 and repository Python/tool prerequisites):

    cd platform/tests/kyverno-component
    go mod verify
    go test -mod=readonly -p 4 -v ./...
    go build -mod=readonly -p 4 -o /absolute/scratch/kyverno-component .
    cd ../../..
    export KYVERNO_COMPONENT=/absolute/scratch/kyverno-component
    python3 -m unittest discover -s platform/tests -p 'test_app_dashboard*.py' -v

Portable full-suite dependency: the historical separation test renders git object
8e6b8908f99ebf76db47c15613eff523644c23f6. It is not an ancestor of this candidate.
The R2 portable Core bundle explicitly includes refs/heads/evidence/retained-fluentd-writer
at that exact object, in addition to the candidate and R1 history. Restore the full
bundle, not a single-branch shallow clone. For a normal canonical clone, fetch
that exact object from the canonical Core repository before running the suite;
verify `git cat-file -e 8e6b8908f99ebf76db47c15613eff523644c23f6^{commit}`.
No historical test or fixture was rewritten to hide this dependency.

Live Kyverno webhook registration/reconciliation, apiserver outage behavior,
effective RBAC and production Kubernetes version remain separate unverified
runtime-acceptance prerequisites. The authoritative Object lookup stays on the
protected path: unavailable/missing/foreign evidence denies admission, including
cleanup, and may require explicit operator resolution. No failurePolicy weakening,
admin exception or namespace-controller cleanup bypass is introduced.
