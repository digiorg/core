# Contributing to DigiOrg Core Platform

Thank you for your interest in contributing! This document provides guidelines and information for contributors.

## 🚀 Quick Start

1. Fork the repository
2. Clone your fork: `git clone https://github.com/YOUR_USERNAME/core.git`
3. Set up local environment: `nu scripts/local-setup.nu up`
4. Create a feature branch: `git checkout -b feature/your-feature-name`
5. Make your changes
6. Submit a Pull Request

## 📁 Project Structure

```
digiorg-core-platform/
├── platform/
│   ├── bootstrap/           # KinD cluster configuration
│   └── base/                # Base Kustomize configurations
│       ├── argocd/          # ArgoCD with Keycloak SSO
│       ├── backstage/       # Backstage Developer Portal
│       ├── crossplane/      # Crossplane setup
│       ├── ingress/         # NGINX Ingress + routing rules
│       ├── keycloak/        # Keycloak IdP
│       ├── kyverno/         # Policy Engine
│       └── monitoring/      # Prometheus + Grafana
├── apps/                    # ArgoCD Application manifests
├── policies/
│   └── kyverno/             # Kyverno policies
│       ├── cluster-policies/
│       └── policies/
├── crossplane/
│   ├── xrds/                # Composite Resource Definitions
│   ├── compositions/        # Compositions per provider
│   └── providers/           # Provider configurations
├── terraform/
│   └── modules/             # Terraform modules per provider
│       ├── aws/
│       ├── azure/
│       └── gcp/
├── docs/
│   ├── adr/                 # Architecture Decision Records
│   └── guides/              # User guides
└── scripts/                 # Automation scripts (Nushell)
```

## 🔀 Git Workflow

### Branch Naming

- `feature/<issue-number>-<short-description>` - New features
- `fix/<issue-number>-<short-description>` - Bug fixes
- `docs/<short-description>` - Documentation changes
- `chore/<short-description>` - Maintenance

### Commit Messages

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

**Types:**
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation
- `style`: Formatting (no code change)
- `refactor`: Code refactoring
- `test`: Adding tests
- `chore`: Maintenance

**Examples:**
```
feat(backstage): Add Keycloak OIDC authentication
fix(argocd): Correct RBAC permissions for app-of-apps
docs(readme): Update architecture diagram
chore(deps): Update Helm chart versions
```

### Pull Request Process

1. Ensure your PR references an issue (e.g., "Closes #5")
2. Update documentation if needed
3. Test locally with `nu scripts/local-setup.nu up`
4. Request review from maintainers
5. Address review feedback

## 📝 Coding Standards

### YAML Files (Kubernetes/Helm/ArgoCD)

- Use 2-space indentation
- Include comments for non-obvious configurations
- Use explicit `apiVersion` and `kind`
- Follow Kubernetes naming conventions (lowercase, dashes)

### Terraform

- Use 2-space indentation
- Include `description` for all variables
- Use meaningful resource names
- Add `tags` to all resources

### Kyverno Policies

- Include `metadata.annotations` with:
  - `policies.kyverno.io/title`
  - `policies.kyverno.io/description`
  - `policies.kyverno.io/severity`
- Test policies locally before submitting

### Crossplane XRDs/Compositions

- Use clear naming: `x<resource>s.platform.digiorg.io`
- Include JSON Schema validation in XRDs
- Document all spec fields
- Provide example claims

## 🧪 Testing

### Local Testing with KinD

```bash
# Start local cluster
nu scripts/local-setup.nu up

# Apply your changes
kubectl apply -k platform/base/<component>/

# Verify
kubectl get pods -n <namespace>

# Access services via http://digiorg.local/<service>

# Cleanup
nu scripts/local-setup.nu down
```

### Service Access

All services are accessible via `http://digiorg.local`:

| Service | URL | Login |
|---------|-----|-------|
| Keycloak | /keycloak | admin / admin |
| ArgoCD | /argocd | via Keycloak |
| Grafana | /grafana | via Keycloak |
| Backstage | /backstage | via Keycloak |

## 📄 Architecture Decision Records (ADRs)

For significant architectural changes, create an ADR:

1. Copy `docs/adr/template.md` to `docs/adr/NNN-title.md`
2. Fill in the template
3. Submit as part of your PR

## 🔒 Security

- **Never commit secrets** - Use Kubernetes Secrets or External Secrets
- Report security issues privately to maintainers
- Follow the principle of least privilege
- All containers must run as non-root (enforced by Kyverno)

## 📫 Getting Help

- Open an issue for questions
- Check existing issues and PRs before creating new ones
- Review the [documentation](docs/)

## 📜 License

By contributing, you agree that your contributions will be licensed under the MIT License.

---

Thank you for contributing to the DigiOrg Core Platform! 🎉
