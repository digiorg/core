# Scripts

This directory contains automation scripts for the DigiOrg Core Platform.

## Prerequisites

- [Nushell](https://www.nushell.sh/) >= 0.90
- [Docker](https://www.docker.com/) >= 20.10
- [kubectl](https://kubernetes.io/docs/tasks/tools/) >= 1.28
- [Helm](https://helm.sh/) >= 3.12
- [KinD](https://kind.sigs.k8s.io/) >= 0.20

> **Note:** `check_prerequisites` only validates `kind`, `kubectl`, and `helm`. Docker and Nushell (`nu`) are also required but not auto-checked — ensure they are installed before running.

## Scripts

### local-setup.nu

Manages the local KinD development cluster using the **App-of-Apps pattern**.

```bash
# Bootstrap cluster and deploy ArgoCD root app
nu scripts/local-setup.nu up

# Destroy local cluster
nu scripts/local-setup.nu down

# Reset cluster (down + up)
nu scripts/local-setup.nu reset

# Show cluster and ArgoCD app status
nu scripts/local-setup.nu status

# Run only Phase 1 bootstrap (no root app)
nu scripts/local-setup.nu bootstrap
```

## Architecture

The setup follows a three-phase approach:

### Phase 1: Bootstrap (Setup Script)

The script installs only the minimal infrastructure needed to run ArgoCD:

1. **KinD Cluster** (`digiorg-core-dev`)
2. **vm.max_map_count=262144** set on the KinD node via `docker exec` (required by OpenSearch's embedded Elasticsearch)
3. **Gateway API CRDs**
4. **NGINX Ingress Controller**
5. **Platform Ingress** (unified routing via `digiorg.local`)
6. **CoreDNS Patch** (internal `digiorg.local` resolution)
7. **Platform Secrets** (shared PostgreSQL credentials + per-service secrets)
   - `platform-db/postgresql-secrets`: Shared PostgreSQL superuser and per-database passwords
   - `backstage/backstage-secrets`: Bootstrap application secret
   - `keycloak/keycloak-db-credentials`: Keycloak PostgreSQL database credentials
   - `gitea/gitea-secrets`: PostgreSQL password, OIDC client secret
   - `gitea/gitea-admin-secret`: Admin username and randomly generated password
   - `code-quality/sonarqube-db-secret`: `SONAR_JDBC_PASSWORD`
   - `code-quality/sonarqube-monitoring-secret`: `SONAR_WEB_SYSTEMPASSCODE`
   - `tracing/jaeger-oauth2-proxy-secrets`: `client-secret`, `cookie-secret`
   - `platform-db/opensearch-secrets`: `OPENSEARCH_ADMIN_PASSWORD`
   - `tracing/jaeger-opensearch-credentials`: `password` (Jaeger → OpenSearch auth)
   - `cost-monitoring/opencost-oauth2-proxy-secrets`: `client-secret`, `cookie-secret`
   - `harbor/harbor-admin-secret`: `HARBOR_ADMIN_PASSWORD`
   - `harbor/harbor-secret-key`: `secretKey` (16-char internal encryption key)
   - `harbor/harbor-db-secret`: `password` (shared PostgreSQL)
   - `harbor/harbor-oidc-secret`: `OIDC_CLIENT_SECRET` (Keycloak OIDC)
8. **ArgoCD** (Helm install)
9. **Root App** (triggers App-of-Apps)

### Phase 2: App-of-Apps (ArgoCD)

ArgoCD creates the child Applications via the root App. The core Applications use sync-wave ordering metadata; CNPG remains manual:

| Wave | Applications | Description |
|------|--------------|-------------|
| bootstrap | root-app | Deployed by the script after the core data layer is functionally ready |
| -1 | namespaces | Pre-create platform namespaces |
| 0 | cert-manager, external-secrets, nats, opensearch, postgresql | Foundation + core data layer |
| 1 | keycloak, argocd, nats-jetstream-controller | Identity + self-managed ArgoCD + NACK JetStream controller |
| 2 | backstage, gitea, grafana, harbor, jaeger, landingpage, opencost, sonarqube | Platform services |
| 3 | crossplane, kyverno | Extensions and policy |
| 4 | crossplane-providers, fluentd, kyverno-policies | Provider plugins, log shipping, policies |
| 5 | monitoring-extras | ServiceMonitors (requires monitoring stack) |
| 6 | crossplane-provider-configs | Provider configurations |
| 7 | crossplane-xrds, crossplane-harbor-bootstrap | Composite Resource Definitions + least-privilege Harbor system robot (Issue #285) |
| 8 | core-catalog | Core catalog (single deterministic AppClaim pipeline Composition) |
| 9 | cnpg | **Manual** optional future-app database operator |
| 10 | cnpg-cluster | **Manual** optional future-app database cluster |
| 11 | app-config | AppClaim GitOps sink (Issue #285) — reconciles merged AppClaim manifests from the private DigiOrg/app-config Gitea repository |

`up` directly applies PostgreSQL and OpenSearch and proves their real Service paths ready before creating the root App. Sync waves alone are not used as a readiness guarantee. CNPG is never promoted by `up`; run `nu scripts/local-setup.nu future-infra` explicitly when the optional future-app database infrastructure is required.

ArgoCD deploys platform components as individual Application resources defined in `apps/platform/*.yaml`, not via an ApplicationSet CRD.

The architecture flow is: **Root App → individual ArgoCD Applications → Platform Components**

### Phase 3: Post-Deployment Configuration

After all core ArgoCD apps reach their required state, the script runs the post-deployment configuration steps:

#### a) `configure_gitea`
- Registers the self-signed CA cert in the Gitea container trust store
- Adds Keycloak as an OIDC provider via `gitea admin auth add-oauth` CLI inside the pod
- Creates initial realm users: `digiorgadmin` and `digiorgdeveloper`
- Creates the `DigiOrg` organisation via the Gitea REST API (bootstrap admin token, generated once and persisted to `gitea/gitea-bootstrap-token`)
- Adds both users to the DigiOrg Owners team via the Gitea API
- **(Issue #285)** `configure_app_config_repo`: creates the private `DigiOrg/app-config` repository — the GitOps sink `apps/platform/app-config.yaml` watches — and seeds its `claims/` directory (matching core-portal's `publishPhase.git.targetPath` exactly)
- **(Issues #285/#301)** `configure_crossplane_gitea_credentials`: creates a dedicated `crossplane-provisioner` Gitea user and reconciles the `platform-provisioners` team to exactly legacy/base permission `none`, granular `repo.code: write`, and organization-repository creation (no org ownership/admin, all-repository access, or extra units). Its PAT has exactly `write:organization,write:repository`, as Gitea's organization-repository endpoint requires both scopes, and is persisted to `crossplane-system/crossplane-gitea-credentials` with a scope-contract annotation in the same atomic server-side apply.
- **(Issue #285)** `configure_argocd_gitea_access`: creates a dedicated `argocd-reader` Gitea user with `read`-only collaborator access on `DigiOrg/app-config` only, generates a `read:repository`-only token, and persists the ArgoCD repository-credential Secret `argocd/app-config-repo-creds`
- The Gitea identity steps are resume-safe: user authorization is always reconciled. ArgoCD and Backstage credentials remain preserved when present. The Crossplane token is preserved only when its Secret has a structurally valid non-empty token and the current scope-contract annotation; an unmarked pre-#301 Secret rotates once. Gitea v1.26.1's available CLI can generate but cannot list/delete PATs, so the replaced narrower PAT becomes unreachable from Kubernetes but cannot be automatically referentially revoked; an apply failure after generation can likewise leave an unreachable PAT for operator cleanup.

#### b) `configure_sonarqube`
- Waits for SonarQube to report status `UP`
- Sets `sonar.core.serverBaseURL` via the Settings API
- Fetches the Keycloak IdP X.509 certificate from the SAML metadata descriptor
- Pushes all `sonar.auth.saml.*` settings via the Settings API
- Enables SAML authentication

#### c) `restart_oidc_dependent_pods`
Restarts ArgoCD Server, Grafana, Backstage, and Landing Page to pick up Keycloak OIDC configuration.

#### d) `patch_argocd_oidc_ca` (runs during Phase 2 wait)
Embeds the self-signed CA certificate into the ArgoCD Helm release via `helm upgrade --reuse-values` so ArgoCD self-sync does not overwrite it. The cert is also saved to `./digiorg-local-ca.crt` for local trust store import. **(Issue #285)** The private `DigiOrg/app-config` GitOps sink repository is cloned through the trusted `https://digiorg.local/gitea` ingress; Argo CD receives the local CA so credential-bearing Git traffic never falls back to plaintext HTTP.

#### e) Declarative Harbor system-robot bootstrap (`crossplane-harbor-bootstrap` Application, Issue #285)
Not a Nushell step: `crossplane/bootstrap/harbor-robot-request.yaml` is a `provider-http` `Request` applied by ArgoCD (wave 7) that creates a least-privilege Harbor **system** robot (`project:create` system-wide plus project-wildcard `robot:create`, `robot:read`, `artifact:read`, and exactly `repository:push`/`repository:pull` so Harbor v2.15.1 permits delegating those actions to per-app CI robots — never delete/update/user/registry access) using the Harbor admin Basic-auth value `create_platform_namespaces_secrets` precomputes into `harbor/harbor-admin-basic-auth`. Existing robots converge in place through `PUT /robots/{id}` without credential rotation; provider-http-converted string/object payloads are both accepted, and only a complete canonical Harbor CREATE response with HTTP `201` updates the three credential keys atomically—partial, error, OBSERVE, and UPDATE responses return `null` for every key so preserve-on-missing cannot mix generations or synthesize `null:null`. The tokenless resume probe binds all reads to one stable Kubernetes projected-Secret revision, validates canonical robot-name and printable secret source bytes, then verifies `basicAuth == base64(name:secret)`; any malformed, inconsistent, or concurrently changing state routes through the bounded transactional credential recovery. Its response is captured via `secretInjectionConfigs` into `crossplane-system/crossplane-harbor-credentials` (`name`, `secret`, `basicAuth`) for the AppClaim Composition.

The gated local rollout does not trust Argo `Synced/Healthy` or a stale `Ready=True` Request condition as proof that Harbor has received the asynchronous update. Before credential probing or recovery, it waits up to five minutes for the current Request generation's secret-free Observe response (string- or object-valued body) to report HTTP `200`, exactly one canonical positive-integer robot identity, and exactly the six expected permissions. This prevents fail-closed recovery from racing provider-http's later permission `PUT`.

## Service Access

After `up` completes, access services via:

| Service | URL | Credentials |
|---------|-----|-------------|
| Landing Page | https://digiorg.local/ | Login via Keycloak |
| Keycloak | https://digiorg.local/keycloak | admin / admin |
| ArgoCD | https://digiorg.local/argocd | Login via Keycloak |
| Grafana | https://digiorg.local/grafana | Login via Keycloak |
| Backstage | https://digiorg.local/backstage | Login via Keycloak |
| Gitea | https://digiorg.local/gitea | `gitea_admin` / password from `gitea/gitea-admin-secret` |
| SonarQube | https://digiorg.local/sonarqube | admin / admin — change immediately |
| Jaeger | https://digiorg.local/jaeger | Login via Keycloak |
| OpenCost | https://digiorg.local/opencost | Login via Keycloak |
| Harbor | https://digiorg.local/harbor | Login via Keycloak (OIDC post-setup required) |

**Note:** Requires `/etc/hosts` entry: `127.0.0.1 digiorg.local`

## CA Certificate Trust

The self-signed CA certificate is saved to `./digiorg-local-ca.crt` after `nu scripts/local-setup.nu up` completes. Import it into your OS trust store so browsers and CLI tools accept the platform's TLS certificates:

**macOS:**
```bash
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain digiorg-local-ca.crt
```

**Linux (Ubuntu/Debian):**
```bash
sudo cp digiorg-local-ca.crt /usr/local/share/ca-certificates/
sudo update-ca-certificates
```

**Windows:**
```cmd
certutil -addstore -f ROOT digiorg-local-ca.crt
```

Restart your browser after importing the certificate.

## Environment Variable Overrides

All passwords and secrets used by the bootstrap script can be overridden by setting environment variables before running `nu scripts/local-setup.nu up`. Unset variables receive a random 24-character alphanumeric password.

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_PASSWORD` | random | PostgreSQL superuser password |
| `KEYCLOAK_DB_PASSWORD` | random | Keycloak PostgreSQL database password |
| `BACKSTAGE_DB_PASSWORD` | random | Backstage PostgreSQL database password |
| `AUTH_SESSION_SECRET` | random | Backstage session secret |
| `AUTH_OIDC_CLIENT_SECRET` | `backstage-client-secret` | Backstage OIDC client secret |
| `GITEA_DB_PASSWORD` | random | Gitea PostgreSQL database password |
| `GITEA_OIDC_CLIENT_SECRET` | `gitea-client-secret` | Gitea OIDC client secret |
| `GITEA_ADMIN_PASSWORD` | random (preserved on re-runs) | Gitea admin password — only regenerated if not already set or explicitly overridden |
| `SONARQUBE_DB_PASSWORD` | random | SonarQube PostgreSQL database password |
| `SONARQUBE_MONITORING_PASSCODE` | random | SonarQube monitoring passcode |
| `JAEGER_OIDC_CLIENT_SECRET` | `jaeger-client-secret` | Jaeger OAuth2 proxy OIDC client secret |
| `JAEGER_COOKIE_SECRET` | random (base64) | Jaeger oauth2-proxy cookie encryption secret |
| `OPENSEARCH_ADMIN_PASSWORD` | random | OpenSearch admin password |
| `OPENCOST_OIDC_CLIENT_SECRET` | `opencost-client-secret` | OpenCost OAuth2 proxy OIDC client secret |
| `OPENCOST_COOKIE_SECRET` | random (base64) | OpenCost oauth2-proxy cookie encryption secret |
| `HARBOR_ADMIN_PASSWORD` | `Harbor12345` | Harbor initial admin password |
| `HARBOR_SECRET_KEY` | `not-a-secure-key` | Harbor 16-char internal encryption key |
| `HARBOR_DB_PASSWORD` | random | Harbor PostgreSQL database password |
| `HARBOR_OIDC_CLIENT_SECRET` | `harbor-client-secret` | Harbor Keycloak OIDC client secret |

## Cluster Name

The local cluster is named `digiorg-core-dev`.

```bash
# Check if cluster exists
kind get clusters | grep digiorg-core-dev

# Get kubeconfig
export KUBECONFIG=$(pwd)/kubeconfig-local.yaml
```

## Troubleshooting

### Cluster won't start

```bash
# Check Docker
docker info

# Clean up old clusters
kind delete cluster --name digiorg-core-dev

# Try again
nu scripts/local-setup.nu up
```

### ArgoCD apps not syncing

```bash
# Check ArgoCD UI
open https://digiorg.local/argocd

# Check app status
kubectl get applications -n argocd

# Check app logs
kubectl logs -n argocd -l app.kubernetes.io/name=argocd-application-controller
```

### Component not ready

```bash
# Check specific namespace
kubectl get pods -n <namespace>

# View logs
kubectl logs -n <namespace> -l app=<app-name>

# Check ArgoCD app details
kubectl describe application <app-name> -n argocd
```
