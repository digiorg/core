# DigiOrg Core Platform

> 🇩🇪 [Deutsch](#deutsch) | 🇬🇧 [English](#english)

---

<a name="deutsch"></a>
## 🇩🇪 Deutsch

Eine Enterprise-ready Plattform für AI-gesteuerte DevSecOps-Automatisierung mit Multi-Cloud-Support und GitOps-First-Architektur.

### Vision

Die DigiOrg Core Platform ermöglicht es Unternehmen, ihre DevSecOps-Prozesse durch AI-Agenten zu automatisieren. Die Plattform kombiniert moderne GitOps-Praktiken mit AI-gestützter Entscheidungsfindung für:

- **Automatisierte Incident-Remediation** — AI-Agenten analysieren und beheben Probleme selbstständig
- **Policy-as-Code Enforcement** — Compliance-Regeln werden kontinuierlich überwacht und durchgesetzt
- **Multi-Cloud Infrastructure Management** — Einheitliche Abstraktion über AWS, Azure, GCP und EU-Cloud-Provider
- **Self-Healing Infrastructure** — Proaktive Erkennung und Behebung von Drift und Fehlkonfigurationen

### Architektur

```
┌─────────────────────────────────────────────────────────────┐
│                    Industry Solutions                       │
│                 (DigiOrg Core Workflows)                    │
├─────────────────────────────────────────────────────────────┤
│                  Business Integration                       │
│     (AI Orchestration, Policy Engine, Tenant Management)    │
├─────────────────────────────────────────────────────────────┤
│               Digital IT Foundation                         │
│  ┌─────────────┬─────────────┬─────────────┬──────────────┐ │
│  │   GitOps    │  Security   │ Observability│   IaC       │ │
│  │   ArgoCD    │  Kyverno    │  Prometheus  │  Crossplane │ │
│  │   Backstage │  Keycloak   │  Grafana     │  Terraform  │ │
│  │             │             │  Jaeger      │             │ │
│  │             │             │  OpenSearch  │             │ │
│  └─────────────┴─────────────┴─────────────┴──────────────┘ │
├─────────────────────────────────────────────────────────────┤
│                  Kubernetes Runtime                         │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  GKE │ EKS │ AKS │ StackIT │ IONOS │ OpenShift │ KinD  │ │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### Kern-Komponenten

#### 🎯 Developer Portal
- **Backstage** — Internal Developer Portal mit Service Catalog
- **Keycloak SSO** — Zentrale Authentifizierung für alle Komponenten

#### 💻 Source Control
- **Gitea** — Self-hosted Git Service mit Code Review, Issue Tracking und CI/CD

#### 🗄️ Data Layer
- **Shared PostgreSQL** — Zentrale Datenbank für Keycloak, Backstage, Gitea und SonarQube (Namespace: `platform-db`)

#### 🔐 Security Stack
- **Kyverno** — Policy-as-Code Engine
- **Keycloak** — Identity & Access Management mit OIDC/SSO

#### 🔒 TLS & Secrets
- **cert-manager** — TLS-Zertifikatsausstellung, selbstsignierte CA für `digiorg.local`
- **External Secrets Operator** — Provider-Wechsel: Fake-Backend für Entwicklung, Azure Key Vault / HashiCorp Vault für Produktion

#### 📊 Observability Stack

Drei-Säulen-System: **Metriken** (Prometheus + Grafana) + **Traces** (Jaeger + OpenSearch) + **Logs** (geplant)

- **Prometheus + Grafana** — Metriken und Dashboards (mit Keycloak OAuth)

#### 🔍 Tracing
- **Jaeger** — Distributed Tracing UI, gespeichert in OpenSearch (Namespace: `tracing`; Keycloak OIDC via oauth2-proxy)

#### 🗄️ Observability-Datenspeicher
- **OpenSearch** — Traces und Logs (Namespace: `platform-db`)

#### 📡 Messaging
- **NATS JetStream** — Persistentes Pub/Sub-Messaging (Namespace: `messaging`)

#### ✅ Code-Qualität
- **SonarQube** — Statische Code-Analyse, SAST, Keycloak SAML (Namespace: `code-quality`)

#### 🌐 Landing Page
- **Plattform-Startseite** — Einstiegspunkt und Service-Discovery-UI (Namespace: `platform-apps`)

#### 🚀 GitOps Engine
- **ArgoCD** — GitOps Continuous Delivery (mit Keycloak SSO)
- **Crossplane** — Infrastructure-as-Code

## Wellen-Abhängigkeitsgraph & Versions-Pinning-Policy

### Sync-Wave-Reihenfolge

| Wave | Anwendungen | Abhängigkeiten |
|------|-------------|----------------|
| -1 | **namespaces** | Keine — erstellt alle Platform-Namespaces vor |
| 0 | cert-manager, cnpg, external-secrets, nats, postgresql | Namespaces (Wave -1) |
| 1 | argocd, keycloak | Wave-0-Komponenten bereit |
| 2 | backstage, gitea, grafana, harbor, jaeger, landingpage, opencost, sonarqube | Keycloak SSO (Wave 1), PostgreSQL (Wave 0) |
| 3 | crossplane, kyverno, opensearch | Wave-2-Apps laufen |
| 4 | crossplane-providers, fluentd, kyverno-policies | Crossplane/Kyverno (Wave 3) |
| 5 | monitoring-extras | ServiceMonitor-CRD aus kube-prometheus-stack (Wave 2) |
| 6 | crossplane-provider-configs | Crossplane-Provider (Wave 4) |
| 7 | crossplane-xrds | Provider-Configs (Wave 6) |
| 8 | core-catalog | XRDs registriert (Wave 7) |

### Versions-Pinning-Policy

**Helm-Charts** — Immer auf exakte Versionen pinnen (z.B. `1.43.2`), keine Floating Ranges (`1.*`, `1.2.x`).
Gepinnte Versionen bewusst nach Tests aktualisieren und die Änderung in der PR-Beschreibung dokumentieren.

**Git-Referenzen** — Immer `targetRevision: main` verwenden (niemals `HEAD`). Für Cross-Repository-Referenzen
(z.B. core-catalog) auf einen spezifischen Commit-SHA pinnen und bewusst aktualisieren.

**Cross-Repository-Abhängigkeiten** — `core-catalog.git` ist auf einen spezifischen Commit-SHA gepinnt.
Bei Updates zuerst API/CRD-Kompatibilität mit dem core Wave 0–7 Stack validieren.

### Quick Start (Lokale Entwicklung)

```bash
# Voraussetzungen: Docker, kubectl, Helm, KinD, Nushell

# 1. Repository klonen
git clone https://github.com/digiorg/core.git
cd core

# 2. /etc/hosts anpassen (einmalig)
echo "127.0.0.1 digiorg.local" | sudo tee -a /etc/hosts

# 3. Lokales Cluster starten
nu scripts/local-setup.nu up

# 4. CA-Zertifikat importieren: ./digiorg-local-ca.crt (wird automatisch gespeichert — siehe scripts/README.md)

# 5. Services aufrufen (alle via digiorg.local)
#    - Landing:    https://digiorg.local/           (Startseite mit Keycloak SSO)
#    - Keycloak:   https://digiorg.local/keycloak   (admin / admin)
#    - ArgoCD:     https://digiorg.local/argocd     (Login via Keycloak)
#    - Grafana:    https://digiorg.local/grafana    (Login via Keycloak)
#    - Backstage:  https://digiorg.local/backstage  (Login via Keycloak)
#    - Gitea:      https://digiorg.local/gitea      (Login via Keycloak)
#    - SonarQube:  https://digiorg.local/sonarqube  (admin / admin — sofort ändern)
#    - Jaeger:     https://digiorg.local/jaeger     (Login via Keycloak)
```

> **Makefile-Shortcuts:** `make up` / `make down` / `make reset` / `make status` sind als Alternativen verfügbar — rufen dasselbe Nushell-Script auf (mit Bash-Fallback).

### Cloud Provider Support

| Provider | Region | Status |
|----------|--------|--------|
| KinD (Local) | Local | ✅ Verfügbar |
| AWS (EKS) | US, EU | 🟡 Geplant |
| Azure (AKS) | US, EU | 🟡 Geplant |
| GCP (GKE) | US, EU | 🟡 Geplant |
| StackIT | DE | 🟡 Geplant |
| IONOS Cloud | DE | 🟡 Geplant |

### Projektstruktur

```
core/
├── docs/                    # Dokumentation
│   ├── adr/                 # Architecture Decision Records
│   └── guides/              # Benutzerhandbücher
├── platform/
│   ├── bootstrap/           # KinD Cluster-Konfiguration
│   └── base/                # Kubernetes Manifeste (Kustomize)
│       ├── argocd/          # ArgoCD mit Keycloak SSO
│       ├── backstage/       # Backstage Developer Portal
│       ├── cert-manager/    # TLS-Zertifikatsverwaltung (selbstsignierte CA + Let's Encrypt)
│       ├── crossplane/      # Crossplane Setup
│       ├── external-secrets/ # External Secrets Operator (Secret-Backend-Abstraktion)
│       ├── gitea/           # Gitea Git Service
│       ├── grafana/         # Prometheus + Grafana (Metriken & Dashboards)
│       ├── ingress/         # NGINX Ingress + Routing
│       ├── jaeger/          # Distributed Tracing
│       ├── keycloak/        # Keycloak IdP
│       ├── kyverno/         # Policy Engine
│       ├── landingpage/     # Plattform-Startseite mit SSO
│       ├── nats/            # Message Broker (JetStream)
│       ├── opensearch/      # Observability-Datenspeicher (Traces + zukünftige Logs)
│       ├── postgresql/      # Shared PostgreSQL (Keycloak + Backstage + Gitea + SonarQube)
│       └── sonarqube/       # Code-Qualität & Sicherheit
├── apps/                    # ArgoCD Application Manifeste
├── policies/                # Kyverno Policies
├── crossplane/              # Crossplane XRDs & Compositions
├── terraform/               # IaC Module für Cloud Provider
└── scripts/                 # Automatisierungs-Scripts (Nushell)
```

### Dokumentation

- [Architektur-Übersicht](docs/architecture.md)
- [Getting Started Guide](docs/guides/getting-started.md)
- [Local Development Guide](docs/guides/local-development.md)
- [ADR-001: Platform Architecture Decisions](docs/adr/001-bootstrap-framework-architecture.md)
- [Beitragen](CONTRIBUTING.md)

### Lizenz

MIT License — siehe [LICENSE](LICENSE)

---

<a name="english"></a>
## 🇬🇧 English

An enterprise-ready platform for AI-driven DevSecOps automation with multi-cloud support and GitOps-first architecture.

### Vision

The DigiOrg Core Platform enables organizations to automate their DevSecOps processes through AI agents. The platform combines modern GitOps practices with AI-powered decision-making for:

- **Automated Incident Remediation** — AI agents analyze and resolve issues autonomously
- **Policy-as-Code Enforcement** — Compliance rules are continuously monitored and enforced
- **Multi-Cloud Infrastructure Management** — Unified abstraction across AWS, Azure, GCP, and EU cloud providers
- **Self-Healing Infrastructure** — Proactive detection and remediation of drift and misconfigurations

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Industry Solutions                       │
│              (DigiOrg Core Workflows)                       │
├─────────────────────────────────────────────────────────────┤
│                  Business Integration                       │
│     (AI Orchestration, Policy Engine, Tenant Management)    │
├─────────────────────────────────────────────────────────────┤
│               Digital IT Foundation                         │
│  ┌─────────────┬─────────────┬─────────────┬──────────────┐ │
│  │   GitOps    │  Security   │ Observability│   IaC       │ │
│  │   ArgoCD    │  Kyverno    │  Prometheus  │  Crossplane │ │
│  │   Backstage │  Keycloak   │  Grafana     │  Terraform  │ │
│  │             │             │  Jaeger      │             │ │
│  │             │             │  OpenSearch  │             │ │
│  └─────────────┴─────────────┴─────────────┴──────────────┘ │
├─────────────────────────────────────────────────────────────┤
│                  Kubernetes Runtime                         │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  GKE │ EKS │ AKS │ StackIT │ IONOS │ OpenShift │ KinD  │ │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### Core Components

#### 🎯 Developer Portal
- **Backstage** — Internal Developer Portal with Service Catalog
- **Keycloak SSO** — Centralized authentication for all components

#### 💻 Source Control
- **Gitea** — Self-hosted Git service with code review, issue tracking, and CI/CD

#### 🗄️ Data Layer
- **Shared PostgreSQL** — Central database for Keycloak, Backstage, Gitea, and SonarQube (Namespace: `platform-db`)

#### 🔐 Security Stack
- **Kyverno** — Policy-as-Code engine
- **Keycloak** — Identity & Access Management with OIDC/SSO

#### 🔒 TLS & Secrets
- **cert-manager** — TLS certificate issuance, self-signed CA for `digiorg.local`
- **External Secrets Operator** — Provider-swap: Fake backend for development, Azure Key Vault / HashiCorp Vault for production

#### 📊 Observability Stack

Three-pillar system: **Metrics** (Prometheus + Grafana) + **Traces** (Jaeger + OpenSearch) + **Logs** (planned)

- **Prometheus + Grafana** — Metrics and dashboards (with Keycloak OAuth)

#### 🔍 Tracing
- **Jaeger** — Distributed Tracing UI, backed by OpenSearch (Namespace: `tracing`; Keycloak OIDC via oauth2-proxy)

#### 🗄️ Observability Data
- **OpenSearch** — Trace and log storage backend (Namespace: `platform-db`)

#### 📡 Messaging
- **NATS JetStream** — Persistent pub/sub messaging (Namespace: `messaging`)

#### ✅ Code Quality
- **SonarQube** — Static code analysis, SAST, Keycloak SAML (Namespace: `code-quality`)

#### 🌐 Landing Page
- **Platform entry point** — Service discovery UI (Namespace: `platform-apps`)

#### 🚀 GitOps Engine
- **ArgoCD** — GitOps Continuous Delivery (with Keycloak SSO)
- **Crossplane** — Infrastructure-as-Code

## Wave Dependency Graph & Version Pinning Policy

### Sync Wave Order

| Wave | Applications | Dependencies |
|------|-------------|--------------|
| -1 | **namespaces** | None — pre-creates all platform namespaces |
| 0 | cert-manager, cnpg, external-secrets, nats, postgresql | Namespaces (wave -1) |
| 1 | argocd, keycloak | Wave 0 components ready |
| 2 | backstage, gitea, grafana, harbor, jaeger, landingpage, opencost, sonarqube | Keycloak SSO (wave 1), PostgreSQL (wave 0) |
| 3 | crossplane, kyverno, opensearch | Wave 2 apps running |
| 4 | crossplane-providers, fluentd, kyverno-policies | Crossplane/Kyverno (wave 3) |
| 5 | monitoring-extras | ServiceMonitor CRD from kube-prometheus-stack (wave 2) |
| 6 | crossplane-provider-configs | Crossplane providers (wave 4) |
| 7 | crossplane-xrds | Provider configs (wave 6) |
| 8 | core-catalog | XRDs registered (wave 7) |

### Version Pinning Policy

**Helm Charts** — Always pin to exact versions (e.g., `1.43.2`), never floating ranges (`1.*`, `1.2.x`).
Update pinned versions deliberately after testing; document the change in the PR description.

**Git References** — Always use `targetRevision: main` (never `HEAD`). For cross-repository
references (e.g., core-catalog), pin to a specific commit SHA and update it consciously.

**Cross-Repository Dependencies** — `core-catalog.git` is pinned to a specific commit SHA.
When updating, validate API/CRD compatibility with the core wave 0–7 stack first.

### Quick Start (Local Development)

```bash
# Prerequisites: Docker, kubectl, Helm, KinD, Nushell

# 1. Clone repository
git clone https://github.com/digiorg/core.git
cd core

# 2. Add hosts entry (once)
echo "127.0.0.1 digiorg.local" | sudo tee -a /etc/hosts

# 3. Start local cluster
nu scripts/local-setup.nu up

# 4. Import CA certificate: ./digiorg-local-ca.crt (saved automatically — see scripts/README.md)

# 5. Access services (all via digiorg.local)
#    - Landing:    https://digiorg.local/           (Homepage with Keycloak SSO)
#    - Keycloak:   https://digiorg.local/keycloak   (admin / admin)
#    - ArgoCD:     https://digiorg.local/argocd     (Login via Keycloak)
#    - Grafana:    https://digiorg.local/grafana    (Login via Keycloak)
#    - Backstage:  https://digiorg.local/backstage  (Login via Keycloak)
#    - Gitea:      https://digiorg.local/gitea      (Login via Keycloak)
#    - SonarQube:  https://digiorg.local/sonarqube  (admin / admin — change immediately)
#    - Jaeger:     https://digiorg.local/jaeger     (Login via Keycloak)
```

> **Makefile shortcuts:** `make up` / `make down` / `make reset` / `make status` are available as alternatives — they call the same Nushell script with a Bash fallback.

### Cloud Provider Support

| Provider | Region | Status |
|----------|--------|--------|
| KinD (Local) | Local | ✅ Available |
| AWS (EKS) | US, EU | 🟡 Planned |
| Azure (AKS) | US, EU | 🟡 Planned |
| GCP (GKE) | US, EU | 🟡 Planned |
| StackIT | DE | 🟡 Planned |
| IONOS Cloud | DE | 🟡 Planned |

### Project Structure

```
core/
├── docs/                    # Documentation
│   ├── adr/                 # Architecture Decision Records
│   └── guides/              # User guides
├── platform/
│   ├── bootstrap/           # KinD cluster configuration
│   └── base/                # Kubernetes manifests (Kustomize)
│       ├── argocd/          # ArgoCD with Keycloak SSO
│       ├── backstage/       # Backstage Developer Portal
│       ├── cert-manager/    # TLS certificate management (self-signed CA + Let's Encrypt)
│       ├── crossplane/      # Crossplane setup
│       ├── external-secrets/ # External Secrets Operator (secret backend abstraction)
│       ├── gitea/           # Gitea Git Service
│       ├── grafana/         # Prometheus + Grafana (metrics & dashboards)
│       ├── ingress/         # NGINX Ingress + routing
│       ├── jaeger/          # Distributed Tracing
│       ├── keycloak/        # Keycloak IdP
│       ├── kyverno/         # Policy engine
│       ├── landingpage/     # Platform Landing Page with SSO
│       ├── nats/            # Message Broker (JetStream)
│       ├── opensearch/      # Observability data backend (traces + future logs)
│       ├── postgresql/      # Shared PostgreSQL (Keycloak + Backstage + Gitea + SonarQube)
│       └── sonarqube/       # Code Quality & Security
├── apps/                    # ArgoCD Application manifests
├── policies/                # Kyverno policies
├── crossplane/              # Crossplane XRDs & Compositions
├── terraform/               # IaC modules for cloud providers
└── scripts/                 # Automation scripts (Nushell)
```

### Documentation

- [Architecture Overview](docs/architecture.md)
- [Getting Started Guide](docs/guides/getting-started.md)
- [Local Development Guide](docs/guides/local-development.md)
- [ADR-001: Platform Architecture Decisions](docs/adr/001-bootstrap-framework-architecture.md)
- [Contributing](CONTRIBUTING.md)

### License

MIT License — see [LICENSE](LICENSE)

---

**DigiOrg** — The fully digitalized organization
