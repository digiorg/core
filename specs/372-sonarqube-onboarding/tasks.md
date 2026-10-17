# Tasks — issue 372

Local implementation boundary:
- [x] Freeze clean isolated repository bases and current upstream/PR inspection.
- [x] Record approved security exceptions; verify exact pinned-image defaults.
- [x] RED/GREEN actual repo-ready KCL Job and shipped HTTPS API runner.
- [x] RED/GREEN explicit adoption, safe absence, policy checks, bounded GET
      retries, one POST only and ambiguous-create readback without takeover.
- [x] RED/GREEN duplicate identity inventory and truthful enrollment readiness.
- [x] RED/GREEN account/policy bootstrap and existing Secret persistence wiring.
- [x] Run real non-root pinned runner image against synthetic HTTPS API.
- [x] Run candidate helpers against isolated SonarQube 26.5.0.122743.
- [x] Freeze exact revisions; delivery manifest binds the complete local matrix
      and reports unmet gates separately (not a remote-CI/runtime claim).

Separate gates, NOT completed by local implementation:
- [ ] Simon binds existing independent review t_6b016750; Aisha reviews.
- [ ] Chris authorizes publication; exact-head authoritative remote CI passes.
- [ ] Chris merges Catalog; canonical producer identity is verified.
- [ ] Core's eventual Catalog pin is implemented/reviewed/tested and merged.
- [ ] Separately authorized credential/bootstrap preparation and runtime rollout.
- [ ] Live permissions/defaults, Portal journey, backfill and retention acceptance.

No live credential creation/rotation, shared-cluster mutation, push, PR or merge
is authorized in this local candidate task. Pin change is deliberately absent.
