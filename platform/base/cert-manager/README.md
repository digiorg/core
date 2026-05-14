# cert-manager

Manages TLS certificate issuance and renewal for all platform services.

## Overview

cert-manager is deployed as a cluster-wide component and provides:

- **Automatic certificate issuance** via configured ClusterIssuers
- **Automatic renewal** before expiry (30 days before)
- **NGINX Ingress integration** via annotations

## Files

| File | Description |
|------|-------------|
| `cluster-issuers.yaml` | Defines the four ClusterIssuers (selfsigned-bootstrap, digiorg-local-ca-issuer, letsencrypt-staging, letsencrypt-prod) |
| `certificate-dev.yaml` | Defines the local CA certificate (digiorg-local-ca) and the wildcard TLS certificate (digiorg-local-tls) |
| `kustomization.yaml` | Kustomize entrypoint; installs cert-manager v1.17.0 and applies issuers and certificates |

## ClusterIssuers

| Name | Environment | CA |
|------|-------------|-----|
| `selfsigned-bootstrap` | Internal (bootstrap only) | Self-signed root |
| `digiorg-local-ca-issuer` | Local dev (`digiorg.local`) | Self-signed local CA |
| `letsencrypt-staging` | Staging | Let's Encrypt (staging) |
| `letsencrypt-prod` | Production | Let's Encrypt (production) |

## Bootstrap Sequence

Certificates and issuers are applied in ArgoCD sync waves to satisfy ordering dependencies:

| Wave | Resources |
|------|-----------|
| 1 | `selfsigned-bootstrap` ClusterIssuer, `letsencrypt-staging` ClusterIssuer, `letsencrypt-prod` ClusterIssuer, `digiorg-local-ca` Certificate |
| 2 | `digiorg-local-ca-issuer` ClusterIssuer (requires `digiorg-local-ca-secret` created in wave 1) |
| 3 | `digiorg-local-tls` Certificate (requires `digiorg-local-ca-issuer` ready from wave 2) |

## Certificates

| Name | Namespace | Issuer | Type | Duration | Key | Secret |
|------|-----------|--------|------|----------|-----|--------|
| `digiorg-local-ca` | `cert-manager` | `selfsigned-bootstrap` | CA certificate | 10 years | ECDSA P-256 | `digiorg-local-ca-secret` |
| `digiorg-local-tls` | `ingress-nginx` | `digiorg-local-ca-issuer` | Wildcard TLS (`digiorg.local`, `*.digiorg.local`) | 1 year | ECDSA P-256 | `digiorg-local-tls` |

## Local Development

The `digiorg-local-ca-issuer` signs a wildcard certificate for `*.digiorg.local`.

**To avoid browser warnings, import the CA into your OS trust store:**

```bash
# Extract the CA certificate from the cluster
kubectl get secret digiorg-local-ca-secret -n cert-manager \
  -o jsonpath='{.data.ca\.crt}' | base64 -d > digiorg-local-ca.crt

# macOS
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain digiorg-local-ca.crt

# Linux (Ubuntu/Debian)
sudo cp digiorg-local-ca.crt /usr/local/share/ca-certificates/
sudo update-ca-certificates

# Windows
certutil -addstore -f "ROOT" digiorg-local-ca.crt
```

## Switching Issuers (Staging/Production)

Update the annotation in `platform/base/ingress/digiorg-ingress.yaml`:

```yaml
# Local dev (default)
cert-manager.io/cluster-issuer: "digiorg-local-ca-issuer"

# Let's Encrypt staging (test)
cert-manager.io/cluster-issuer: "letsencrypt-staging"

# Let's Encrypt production
cert-manager.io/cluster-issuer: "letsencrypt-prod"
```

> **Prerequisite:** Before enabling `letsencrypt-staging` or `letsencrypt-prod`, replace the placeholder email `admin@digiorg.io` in `cluster-issuers.yaml` with a real address.

## Certificate Status

```bash
# List all certificates
kubectl get certificates -A

# Check certificate details
kubectl describe certificate digiorg-local-tls -n ingress-nginx

# Check cert-manager logs
kubectl logs -n cert-manager deploy/cert-manager
```
