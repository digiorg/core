# Documentation

This directory contains the documentation for the DigiOrg Core Platform.

## Contents

### Guides

- **[Getting Started](guides/getting-started.md)** — Quick start guide for new users
- **[Local Development](guides/local-development.md)** — Setting up the local KinD environment

### Architecture

- **[Architecture Overview](architecture.md)** — High-level platform architecture

### Architecture Decision Records (ADRs)

- **[ADR-001: Bootstrap Framework Architecture](adr/001-bootstrap-framework-architecture.md)** — Platform Architecture Decisions — All platform architectural decisions (bootstrap, observability, IAM, secrets, messaging, code quality)

### Platform Documentation

- **[Platform components & wave deployment order](platform/README.md)**
- **[Setup script reference](scripts/README.md)**

## Quick Links

### Local Development

```bash
# Start local cluster
nu scripts/local-setup.nu up

# Makefile shortcuts
make up
make down

# Access services
# Landing:    https://digiorg.local/           (Login via Keycloak)
# Keycloak:   https://digiorg.local/keycloak   (admin / admin)
# ArgoCD:     https://digiorg.local/argocd     (Login via Keycloak)
# Grafana:    https://digiorg.local/grafana     (Login via Keycloak)
# Backstage:  https://digiorg.local/backstage   (Login via Keycloak)
# Gitea:      https://digiorg.local/gitea       (Login via Keycloak)
# SonarQube:  https://digiorg.local/sonarqube   (admin / admin — change immediately)
# Jaeger:     https://digiorg.local/jaeger      (Login via Keycloak)
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
| Jaeger | https://www.jaegertracing.io/docs/ |
| OpenSearch | https://docs.opensearch.org/ |
| NATS | https://docs.nats.io/ |
| SonarQube | https://docs.sonarsource.com/sonarqube-server/ |
| cert-manager | https://cert-manager.io/docs/ |
| External Secrets Operator | https://external-secrets.io/ |
| Gitea | https://docs.gitea.com/ |
| Nushell | https://www.nushell.sh/book/ |
