# Disposable pre-1.0 main bootstrap

Authority: Chris's approved Kanban task `t_bff0019a`; standard delivery,
independent reviewer `t_557a6ce5`. Source preparation only, no publication or
cluster access. This user-specific direction supersedes retained-release
migration prerequisites for this one disposable development environment.

## Requirements

- MB-1: One canonical Root/Argo/child Application graph follows main for
  `digiorg/core` sources; external dependency pins and security gates stay.
- MB-2: Existing `local-setup.nu` is the rebuild entrypoint. No runtime tag,
  release generator, parallel Application tree or dashboard preparer is needed.
- MB-3: Preserve #369 extraction: standalone Fluentd renders one canonical
  dashboard plus its schema safety hook. Include merged main corrections.
- MB-4: Publication/CI/merge precede a readiness notice; destructive reset has
  separate Chris approval. Stop on failure; Chris owns visual Grafana acceptance.

## Scope and findings

At base `41b77b1b1af726563c7209f6d51f57a5ab1a03e4`, Root and all 32 Core
source references already use main. No bootstrap call to historical release
or dashboard preparation tools exists. Therefore do not manufacture a runtime
change: correct active docs and add executable offline preservation contracts.
Complexity is medium source clarification, standard review. No bootstrap
algorithm, runtime permission or external pin change is required.

Non-goals: runtime reset, retained-data migration, ingest runtime proof, new
release machinery, deletion of historical tools, publication, merge or risk
acceptance. Static tests cannot prove clean deployment or visual correctness.
