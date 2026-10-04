# Retained Log Dashboard consumer Applications

This is the explicit, reviewed declarative consumer snapshot for preparation
t_8e98c858, not the canonical bootstrap apps/ directory and not generated output.
It has 31 child Applications from retained repository commit
f6e7d58c0b03ee6a3ec6ed9e1e22e5023f861549. Historical runtime correspondence is
not fresh runtime verification. Root selects this directory recursively with
include '*.yaml'; this README is not an Application.

All 28 unrelated child specifications remain identical. 27 manifests are
byte-identical; Gitea only normalizes four inherited whitespace-only lines.
Only argocd (owner link), grafana (Core values source) and fluentd (two-source
same-owner dashboard delivery) differ. The sibling argocd/ wrapper renders
Root back to this directory. Both owner links use the literal proposed tag
log-dashboard-runtime-v1. No tag exists or is created by this source.

Do not apply this directory alongside canonical apps/ to a second owner.
See docs/guides/log-dashboard-runtime-v1.md for publication, preflight,
ordering, rollback and pending live acceptance. Do not use local-setup reset,
the closed #348 runner or selected-resource sync for this change.
