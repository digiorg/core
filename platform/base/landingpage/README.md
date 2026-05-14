# Landing Page

The DigiOrg Platform Landing Page serves as the central entry point for all platform services. It is deployed in the `platform-apps` namespace.

## Features

- **Keycloak SSO** — OIDC authentication with the platform Keycloak instance
- **Service Discovery** — Dynamic list of available platform services
- **Theme Toggle** — Light/Dark mode with system preference detection
- **Responsive Design** — Works on desktop and mobile devices

## Files

| File | Description |
|------|-------------|
| `deployment.yaml` | Landing page deployment |
| `service.yaml` | ClusterIP service on port 8080 |
| `configmap.yaml` | Runtime configuration (base URL, Keycloak settings) |
| `services-configmap.yaml` | Service registry for UI components |
| `kustomization.yaml` | Kustomize entrypoint |

## Access

| Environment | URL |
|-------------|-----|
| Local (KinD) | https://digiorg.local/ |

## Ports

| Port | Protocol | Purpose |
|------|----------|---------|
| 8080 | HTTP | Application + health endpoint |

Health: `GET /health` — used by liveness and readiness probes.  
In-cluster: `http://landingpage.platform-apps.svc.cluster.local:8080`

## Configuration

### Runtime Configuration

The landing page runtime configuration is injected via the `landingpage-config` ConfigMap, mounted as `/usr/share/nginx/html/config.js`:

```javascript
window.__DIGIORG_CONFIG__ = {
  baseUrl: "https://digiorg.local",
  keycloak: {
    url: "https://digiorg.local/keycloak",
    realm: "digiorg-core-platform",
    clientId: "landingpage"
  },
  servicesEndpoint: "/api/services"
};
```

> **Note:** `servicesEndpoint` is present in the config object but services are not fetched from an API. The service list is statically loaded from the `landingpage-services` ConfigMap, which is mounted as `/usr/share/nginx/html/services.json` and read directly by the frontend at startup.

### ConfigMaps

| ConfigMap | Key | Mount Path |
|-----------|-----|------------|
| `landingpage-config` | `config.js` | `/usr/share/nginx/html/config.js` |
| `landingpage-services` | `services.json` | `/usr/share/nginx/html/services.json` |

### Service Registry

Platform services with UI are defined in `services-configmap.yaml`. Each service entry includes:

| Field | Description |
|-------|-------------|
| `id` | Unique identifier |
| `name` | Display name |
| `description` | Short description |
| `path` | URL path (relative to base URL) |
| `icon` | Icon name (key, git-branch, chart, code, git, etc.) |
| `category` | Grouping (`security`, `developer`, `deployment`, `monitoring`, `observability`, `devsecops`) |
| `requiresAuth` | Whether authentication is required to access |
| `displayOrder` | Render order of the service card in the UI |

### Registered Services

| displayOrder | ID | Name | Category | Path | requiresAuth |
|---|---|---|---|---|---|
| 1 | `keycloak` | Identities & Access | `security` | `/keycloak` | false |
| 2 | `backstage` | Components & Catalog | `developer` | `/backstage` | true |
| 3 | `gitea` | Repositories & Pipelines | `developer` | `/gitea` | true |
| 4 | `argocd` | GitOps & Deployment | `deployment` | `/argocd` | true |
| 5 | `grafana` | Logging & Monitoring | `monitoring` | `/grafana` | true |
| 6 | `jaeger` | Tracing & Observing | `observability` | `/jaeger` | true |
| 7 | `sonarqube` | Code Quality & Security | `devsecops` | `/sonarqube` | true |

## Architecture

```
Browser → NGINX /  → landingpage:8080 (platform-apps namespace)
                         ├── landingpage-config  (config.js)
                         └── landingpage-services (services.json)
                                   │
                             Keycloak OIDC
                          (public client, no secret)
```

## Resource Limits & Security

|        | Request | Limit |
|--------|---------|-------|
| CPU    | 10m     | 100m  |
| Memory | 32Mi    | 64Mi  |

The container runs as a non-root user (UID 101) with `allowPrivilegeEscalation: false` and all Linux capabilities dropped.

## Dependencies

- **Keycloak** — OIDC provider (client: `landingpage`)
- **Ingress** — Root path routing

## Container Image

- **Repository:** https://github.com/digiorg/core-landingpage
- **Image:** `ghcr.io/digiorg/core-landingpage:main`
- **imagePullPolicy:** `Always` — image is pulled on every pod start
