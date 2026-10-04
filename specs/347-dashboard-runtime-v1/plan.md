# Preparation plan

Branch prepare/t_8e98c858 from merge 41b77b1b1af726563c7209f6d51f57a5ab1a03e4.
Write focused graph tests and observe RED before manifest changes. Add static
retained Application snapshot and Kustomize Root wrapper. Keep canonical apps/
and platform/base unchanged. No release generator or generated corrective logic.
Run focused GREEN, exact supported Python/dependencies/Kustomize/Helm/Nu/Argo
parser gates, all canonical regressions and base renders. Separately resolve
proposed tag to exact local candidate offline, collect complete before/after
inventories and semantic deltas, render retained writer/dashboard/Argo resources
and verify unchanged bodies/identities. Freeze commit and bundle; persist
checksummed evidence. Simon/default binds existing independent review card.
Hosted CI, Chris publication/merge/tag approval, preflight and deployment remain
separate. Follow guide's coherent owner rollback, Grafana gate and no data change.
