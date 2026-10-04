# ADR: Independent dashboard source inside the existing Fluentd owner

Status: source candidate; independent review and Chris's merge decision pending.
Authority: bounded source separation approved in t_24070940; no runtime approval.

## Context

The retained Fluentd Application owns the dashboard ConfigMap as well as writer
resources. Promoting its entire source to main also introduces #345 writer and
schema PreSync work. #364/#365 are merged but pinned retained runtime does not
consume them. Dashboard-only source promotion must not implicitly migrate data.

## Decision

Extract byte-identical dashboard YAML into one independently renderable base.
Keep standalone Fluentd base rendering backward-compatible through a base
reference. Prepare a two-source representation of the existing Application:
retained writer plus targeted manifest-generation deletion of its dashboard,
and separately pinned dashboard-only source. Retain same owner, namespace,
ConfigMap identity, policy and finalizer. No resource precedence/duplicate and
no transfer to a new Application. Existing Application source remains unchanged
in canonical source; the new representation is explicit offline preparation
for a future reviewed immutable consumer graph.

## Alternatives

- Promote all Fluentd main: rejected; writer/schema risk outside dashboard scope.
- New dashboard Application: rejected; ownership transfer/prune orchestration.
- Live selected-resource sync/patch: forbidden and not durable desired state.
- Keep duplication and rely on last-source-wins: rejected; hides producer drift.

## Consequences

The immutable writer pin can stay at its actual retained revision. Rollback may
restore the whole old single-source App, or use a dashboard commit that contains
the new path. Never use a pre-extraction SHA for the new dashboard path. Schema
migration stays separate. A future Root/Argo consumer closure and deployment
mechanism still require explicit scope, review, CI and approval; this preparer
is not a generic runtime generator. No closed #348 mechanism is changed.
