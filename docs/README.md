# Documentation

This directory contains the documentation for the DigiOrg Core Platform.

## Contents

### Guides

- **[Getting Started](guides/getting-started.md)** — Quick start guide for new users
- **[Local Development](guides/local-development.md)** — Setting up the local KinD environment

### Architecture

- **[Architecture Overview](architecture.md)** — High-level platform architecture

### Architecture Decision Records (ADRs)

- **[ADR-001: Bootstrap Framework Architecture](adr/001-bootstrap-framework-architecture.md)** — Terraform + Crossplane + Nushell approach

## Quick Links

### Local Development

```bash
# Start local cluster
nu scripts/local-setup.nu up

# Access services
# Keycloak:  http://digiorg.local/keycloak  (admin / admin)
# ArgoCD:    http://digiorg.local/argocd    (via Keycloak)
# Grafana:   http://digiorg.local/grafana   (via Keycloak)
# Backstage: http://digiorg.local/backstage (via Keycloak)
```

### Component Documentation

| Component | Documentation |
|-----------|---------------|
| Keycloak | https://www.keycloak.org/documentation |
| ArgoCD | https://argo-cd.readthedocs.io/ |
| Backstage | https://backstage.io/docs |
| Crossplane | https://docs.crossplane.io/ |
| Kyverno | https://kyverno.io/docs/ |
| Prometheus | https://prometheus.io/docs/ |
| Grafana | https://grafana.com/docs/ |
