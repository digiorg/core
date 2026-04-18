# cert-manager

Manages TLS certificate issuance and renewal for all platform services.

## Overview

cert-manager is deployed as a cluster-wide component and provides:

- **Automatic certificate issuance** via configured ClusterIssuers
- **Automatic renewal** before expiry (30 days before)
- **NGINX Ingress integration** via annotations

## ClusterIssuers

| Name | Environment | CA |
|------|-------------|-----|
| `selfsigned-bootstrap` | Internal (bootstrap only) | Self-signed root |
| `digiorg-local-ca-issuer` | Local dev (`digiorg.local`) | Self-signed local CA |
| `letsencrypt-staging` | Staging | Let's Encrypt (staging) |
| `letsencrypt-prod` | Production | Let's Encrypt (production) |

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

Also update `letsencrypt-staging` and `letsencrypt-prod` in `cluster-issuers.yaml` with a real email address.

## Certificate Status

```bash
# List all certificates
kubectl get certificates -A

# Check certificate details
kubectl describe certificate digiorg-local-tls -n ingress-nginx

# Check cert-manager logs
kubectl logs -n cert-manager deploy/cert-manager
```
