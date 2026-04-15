# Landing Page

The DigiOrg Platform Landing Page serves as the central entry point for all platform services.

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
| Local (KinD) | http://digiorg.local/ |

## Configuration

### Runtime Configuration

The landing page configuration is injected via ConfigMap:

```javascript
window.__DIGIORG_CONFIG__ = {
  baseUrl: "http://digiorg.local",
  keycloak: {
    url: "http://digiorg.local/keycloak",
    realm: "digiorg-core-platform",
    clientId: "landingpage"
  },
  servicesEndpoint: "/api/services"
};
```

### Service Registry

Platform services with UI are defined in `services-configmap.yaml`. Each service entry includes:

| Field | Description |
|-------|-------------|
| `id` | Unique identifier |
| `name` | Display name |
| `description` | Short description |
| `path` | URL path (relative to base URL) |
| `icon` | Icon name (key, git-branch, chart, code, git, etc.) |
| `category` | Grouping (security, deployment, monitoring, developer) |
| `requiresAuth` | Whether authentication is required to access |

## Dependencies

- **Keycloak** — OIDC provider (client: `landingpage`)
- **Ingress** — Root path routing

## Container Image

- **Repository:** https://github.com/digiorg/core-landingpage
- **Image:** `ghcr.io/digiorg/core-landingpage:main`
