# SonarQube Community Build

Static code analysis platform for the DigiOrg Core Platform.

- **UI:** https://digiorg.local/sonarqube
- **Namespace:** `code-quality`
- **Helm Chart:** [SonarSource/helm-chart-sonarqube](https://github.com/SonarSource/helm-chart-sonarqube)
- **Auth:** Keycloak SAML (native Community Build support)
- **Database:** Shared PostgreSQL (`platform-db` namespace)

---

## Required Secrets

Three Kubernetes Secrets must exist in namespace `code-quality` before ArgoCD deploys SonarQube. Create them via `local-setup.nu` or manually:

### 1. `sonarqube-db-secret` — PostgreSQL credentials

```bash
kubectl create secret generic sonarqube-db-secret \
  --from-literal=SONAR_JDBC_PASSWORD=<password> \
  -n code-quality
```

The password must match `SONARQUBE_DB_PASSWORD` in the `postgresql-secrets` Secret (`platform-db` namespace).

---

### 2. `sonarqube-monitoring-secret` — Liveness probe passcode

Required for the liveness probe (`/api/system/liveness`). Without this the pod never becomes Ready.

```bash
kubectl create secret generic sonarqube-monitoring-secret \
  --from-literal=SONAR_WEB_SYSTEMPASSCODE=<random-passcode> \
  -n code-quality
```

Use a random string, e.g.: `openssl rand -hex 32`

---

### 3. `sonarqube-saml-secret` — Keycloak IdP certificate

Contains the Keycloak realm signing certificate for SAML signature verification.

**Step 1 — Obtain the certificate from Keycloak:**

```bash
# Get realm's public key (X.509 certificate)
kubectl exec -n keycloak deploy/keycloak -- \
  curl -sk https://digiorg.local/keycloak/realms/digiorg-core-platform \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['public_key'])"
```

**Step 2 — Create the Secret:**

```bash
CERT=$(kubectl exec -n keycloak deploy/keycloak -- \
  curl -sk https://digiorg.local/keycloak/realms/digiorg-core-platform \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['public_key'])")

kubectl create secret generic sonarqube-saml-secret \
  --from-literal="sonar.auth.saml.certificate.secured=${CERT}" \
  -n code-quality
```

> **Note:** The certificate must be recreated whenever Keycloak realm keys rotate or the cluster is rebuilt.

---

## PostgreSQL Setup

The shared PostgreSQL instance requires the `sonarqube` database and user. This is handled in two ways:

- **New clusters:** `platform/base/postgresql/statefulset.yaml` init script creates the DB on first startup
- **Existing clusters:** Run the one-time init Job (see [Step 7 in issue #78](https://github.com/digiorg/core/issues/78))

The `SONARQUBE_DB_PASSWORD` env var must be added to the `postgresql-secrets` Secret in `platform-db`.

---

## Keycloak SAML Client

The SonarQube SAML client is defined in `platform/base/keycloak/digiorg-core-platform-realm.json` and is imported automatically when Keycloak starts. No manual Keycloak configuration is needed.

**SAML callback URL:** `https://digiorg.local/sonarqube/oauth2/callback/saml`

---

## First Login

After deployment:
1. Navigate to `https://digiorg.local/sonarqube`
2. Log in with the default admin account: `admin` / `admin`
3. Change the admin password immediately
4. SAML SSO should be available via the "Log in with Keycloak" button

---

## Upgrading Community Build Version

Update `community.buildNumber` in `values.yaml` to the desired Community Build version.
Find available versions at [SonarQube Downloads](https://www.sonarsource.com/products/sonarqube/downloads/).
