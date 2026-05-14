# SonarQube Community Build

Static code analysis platform for the DigiOrg Core Platform.

- **UI:** https://digiorg.local/sonarqube
- **Namespace:** `code-quality`
- **Helm Chart:** [SonarSource/helm-chart-sonarqube](https://github.com/SonarSource/helm-chart-sonarqube)
- **Auth:** Keycloak SAML (native Community Build support)
- **Database:** Shared PostgreSQL (`platform-db` namespace)

---

## Files

| File | Purpose |
|------|---------|
| `namespace.yaml` | Namespace `code-quality` |
| `values.yaml` | SonarQube Helm Chart values (edition, DB, probes, resources, SAML, Prometheus) |
| `kustomization.yaml` | Kustomize entrypoint — manages only `namespace.yaml`; SonarQube itself is deployed via Helm by ArgoCD (`apps/platform/sonarqube.yaml`) |

> **Note:** `kubectl apply -k .` from this directory only creates the `code-quality` namespace. SonarQube is not deployed by Kustomize.

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

Contains the Keycloak realm signing certificate used for SAML signature verification. This Secret is **not mounted as a properties file** — `sonarSecretProperties` is intentionally left unset. Setting it would create a required Secret volume mount with no `optional: true` in the chart, preventing the pod from starting on fresh clusters where the Secret may not yet exist (see [issue #109](https://github.com/digiorg/core/issues/109)).

> **Note:** `sonar.auth.saml.*` settings are database-stored, not system properties. They are silently ignored in both `sonarProperties` and `sonarSecretProperties`. SAML is configured exclusively via the SonarQube Settings API.

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

**Step 3 — Configure SAML:**

SAML is configured automatically by `local-setup.nu` (function: `configure_sonarqube_saml`), which reads the certificate from this Secret and applies all `sonar.auth.saml.*` settings via the SonarQube Settings API after first startup. To configure manually instead, use **Administration → Security → SAML** in the Admin UI.

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

## Configuration Notes

### `sonar.core.serverBaseURL`

`sonar.core.serverBaseURL` is **not a system property** and cannot be set via `sonarProperties` or `sonarSecretProperties`. It is a database-stored setting applied via the SonarQube Settings API by `local-setup.nu` (function: `configure_sonarqube_base_url`). No manual action is required.

---

## First Login

After deployment:
1. Navigate to `https://digiorg.local/sonarqube`
2. Log in with the default admin account: `admin` / `admin`
3. Change the admin password immediately
4. SAML SSO should be available via the "Log in with Keycloak" button

---

## Deployment

- **Type:** StatefulSet (single replica)
- **Service:** ClusterIP on port `9000`
- **Persistence:** 5 Gi PVC (`ReadWriteOnce`) — stores plugins and temporary files
- **Web context:** `/sonarqube` (set via `sonarWebContext`; injected automatically as `SONAR_WEB_CONTEXT`)

---

## Resources

SonarQube is the most resource-intensive component in the platform due to its embedded Elasticsearch instance.

| | CPU | Memory |
|-|-----|--------|
| Request | `200m` | `2Gi` |
| Limit | `2000m` | `4Gi` |

> **Warning:** SonarQube + embedded Elasticsearch requires **at least 2 Gi of memory** to start. Scheduling on a node with insufficient available memory results in OOMKill or a pod stuck in `Pending`.

---

## Probes

SonarQube can take 2–3 minutes to start (Elasticsearch initialisation + DB schema migrations on first run).

| Probe | Initial Delay | Period | Failure Threshold | Budget |
|-------|--------------|--------|-------------------|--------|
| Startup | 30s | 10s | 24 | ~4 min |
| Liveness | 120s | 30s | 6 | — |
| Readiness | 120s | 30s | 6 | — |

> **Important:** Helm chart 10.x generates probe scripts using `wget`, but the SonarQube Community Build image ships only `curl`. The probe `exec.command` is overridden in `values.yaml` to use `curl` explicitly (see [issue #111](https://github.com/digiorg/core/issues/111)).

---

## Embedded Elasticsearch

SonarQube runs an embedded Elasticsearch instance in-process. This is **not** the platform OpenSearch deployment — they are completely separate.

**Local KinD (default `values.yaml`):**

`vm.max_map_count` enforcement is disabled because KinD nodes do not support sysctl changes:

```yaml
elasticsearch:
  bootstrapChecks: false
initSysctl:
  enabled: false
```

**Production clusters:**

`vm.max_map_count` must be set to at least `262144` on every node that may run SonarQube. Enable the init container to apply it:

```yaml
elasticsearch:
  bootstrapChecks: true
initSysctl:
  enabled: true   # requires privileged node access
```

Failure to set `vm.max_map_count` in production causes Elasticsearch to abort with:
`max virtual memory areas vm.max_map_count [65530] is too low, increase to at least [262144]`

---

## Prometheus Monitoring

A `PodMonitor` is enabled in `values.yaml`:

```yaml
prometheusMonitoring:
  podMonitor:
    enabled: true
    interval: 30s
    labels:
      release: prometheus   # required for kube-prometheus-stack discovery
```

The `release: prometheus` label is required for `kube-prometheus-stack` to discover the `PodMonitor`. Removing or changing this label causes metrics to stop being scraped.

---

## Security Context

| Setting | Value |
|---------|-------|
| `runAsUser` | `1000` |
| `runAsGroup` | `0` |
| `fsGroup` | `0` |
| `allowPrivilegeEscalation` | `false` |
| `capabilities.drop` | `["ALL"]` |
| `seccompProfile` | `RuntimeDefault` |

---

## Upgrading Community Build Version

Update `community.buildNumber` in `values.yaml` to the desired Community Build version.
Find available versions at [SonarQube Downloads](https://www.sonarsource.com/products/sonarqube/downloads/).

---

## Architecture

```
Browser → NGINX /sonarqube → SonarQube:9000 (code-quality ns, StatefulSet)
                                   ├── Embedded Elasticsearch (in-process, not platform OpenSearch)
                                   ├── PostgreSQL :5432 (platform-db ns) — analysis data
                                   ├── 5Gi PVC — plugins, temp files
                                   └── Keycloak SAML (settings configured via API)
```

---

## Troubleshooting

**Check pod status:**

```bash
kubectl get pods -n code-quality
```

**Inspect logs** (pod name follows the StatefulSet pattern, e.g. `sonarqube-sonarqube-0`):

```bash
kubectl logs -n code-quality <pod-name>
```

**Check persistent storage:**

```bash
kubectl get pvc -n code-quality
```

**Startup time:** SonarQube takes 2–3 minutes to initialise on first boot (Elasticsearch startup + DB migrations). The UI is unavailable until the pod reports `Ready`. Monitor progress with:

```bash
kubectl get pods -n code-quality -w
```
