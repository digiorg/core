# Implementation plan

1. RED: add behavioral tests before implementation. Test missing preparer,
   immutable independent sources, actual retained writer rendering and CLI.
2. Move the canonical dashboard file without changing its bytes. Add a
   standalone dashboard base; reference it from Fluentd for compatibility.
3. Implement a pure narrow Application transformation and local-only CLI.
   Two sources remain inside the existing owner; delete only the old source's
   dashboard from its rendered output, not from the live cluster.
4. GREEN: exercise actual Kustomize outputs, unchanged writer inventory/bodies,
   dashboard contract, plugin lifecycle and full repository static gate.
5. Freeze local commit/tree/parent and safe evidence; Simon binds the existing
   independent-review card. Review is read-only; any changes invalidate it.
6. Follow docs/guides/log-dashboard-delivery.md for future approved consumer
   publication and retained-environment rollout. This source task does not
   produce or authorize a Root/Argo deployment closure or execute a sync.

Alternative rejected: a separate dashboard Application needs live resource
ownership migration and prune protection. Same-Application multi-source avoids
that risk and leaves the writer revision independently selectable.
