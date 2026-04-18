#!/usr/bin/env nu

# =============================================================================
# Local Development Environment Setup (App-of-Apps Pattern)
# =============================================================================
# This script bootstraps the local KinD cluster and deploys the ArgoCD root app.
# ArgoCD then manages all platform components via the App-of-Apps pattern.
#
# Usage:
#   nu scripts/local-setup.nu up       # Bootstrap cluster + deploy root app
#   nu scripts/local-setup.nu down     # Destroy local cluster
#   nu scripts/local-setup.nu reset    # Reset cluster (down + up)
#   nu scripts/local-setup.nu status   # Show cluster status
#
# Architecture:
#   Phase 1 (this script): KinD → Ingress → CoreDNS → Secrets → ArgoCD → Root App
#   Phase 2 (ArgoCD):      Root App → ApplicationSet → Platform Components
# =============================================================================

# Configuration
let CLUSTER_NAME = "digiorg-core-dev"
let KIND_CONFIG = "platform/bootstrap/kind-config.yaml"
let KUBECONFIG_PATH = $"($env.PWD)/kubeconfig-local.yaml"

# Main entry point
def main [] {
    print "DigiOrg Core Platform - Local Development"
    print ""
    print "Commands:"
    print "  up       - Bootstrap cluster and deploy ArgoCD root app"
    print "  down     - Destroy local cluster"
    print "  reset    - Reset cluster (down + up)"
    print "  status   - Show cluster and ArgoCD app status"
    print "  bootstrap - Run only Phase 1 bootstrap (no root app)"
    print ""
    print $"Usage: nu scripts/local-setup.nu <command>"
}

# Bootstrap cluster and deploy root app (App-of-Apps pattern)
def "main up" [] {
    print $"(ansi green_bold)╔════════════════════════════════════════════════════════════════╗(ansi reset)"
    print $"(ansi green_bold)║  DigiOrg Core Platform - App-of-Apps Bootstrap                 ║(ansi reset)"
    print $"(ansi green_bold)╚════════════════════════════════════════════════════════════════╝(ansi reset)"
    print ""
    
    # Phase 1: Bootstrap
    print $"(ansi cyan_bold)Phase 1: Bootstrap Infrastructure(ansi reset)"
    print "────────────────────────────────────"
    
    main bootstrap
    
    # Phase 2: Deploy Root App
    print ""
    print $"(ansi cyan_bold)Phase 2: Deploy ArgoCD Root App(ansi reset)"
    print "────────────────────────────────────"
    
    deploy_root_app
    
    # Wait for apps to sync
    print ""
    print $"(ansi cyan_bold)Phase 3: Waiting for ArgoCD Apps(ansi reset)"
    print "────────────────────────────────────"
    
    wait_for_argocd_apps
    
    # Restart OIDC-dependent pods after Keycloak is ready
    restart_oidc_dependent_pods
    
    print ""
    print $"(ansi green_bold)╔════════════════════════════════════════════════════════════════╗(ansi reset)"
    print $"(ansi green_bold)║  ✓ Platform Ready!                                             ║(ansi reset)"
    print $"(ansi green_bold)╚════════════════════════════════════════════════════════════════╝(ansi reset)"
    print ""
    print $"Export kubeconfig:"
    print $"  export KUBECONFIG=($KUBECONFIG_PATH)"
    print ""
    print "Access services (all via digiorg.local):"
    print "  Keycloak:   http://digiorg.local/keycloak   (admin / admin)"
    print "  ArgoCD:     http://digiorg.local/argocd     (Login via Keycloak)"
    print "  Grafana:    http://digiorg.local/grafana    (Login via Keycloak)"
    print "  Backstage:  http://digiorg.local/backstage  (Login via Keycloak)"
    print "  Gitea:      http://digiorg.local/gitea      (admin login; configure OIDC in Admin UI)"
    print ""
    print $"(ansi yellow)Prerequisite: Add to /etc/hosts: 127.0.0.1 digiorg.local(ansi reset)"
}

# Run only Phase 1 bootstrap (no root app)
def "main bootstrap" [] {
    # Check prerequisites
    check_prerequisites
    
    # 1. Create KinD cluster
    if (cluster_exists) {
        print $"(ansi yellow)✓ Cluster '($CLUSTER_NAME)' already exists(ansi reset)"
    } else {
        print "1. Creating KinD cluster..."
        kind create cluster --config $KIND_CONFIG --kubeconfig $KUBECONFIG_PATH
        print $"(ansi green)✓ KinD cluster created(ansi reset)"
    }
    
    $env.KUBECONFIG = $KUBECONFIG_PATH
    
    # Wait for cluster
    print "   Waiting for cluster nodes..."
    kubectl wait --for=condition=Ready nodes --all --timeout=120s
    
    # 2. Install Gateway API CRDs
    print "2. Installing Gateway API CRDs..."
    install_gateway_api
    
    # 3. Install Ingress Controller
    print "3. Installing NGINX Ingress Controller..."
    install_ingress
    
    # 4. Apply Platform Ingress rules
    print "4. Installing Platform Ingress rules..."
    kubectl apply -k platform/base/ingress/
    print $"(ansi green)✓ Platform Ingress installed(ansi reset)"
    
    # 5. Configure CoreDNS for digiorg.local
    print "5. Configuring CoreDNS for digiorg.local..."
    configure_coredns_digiorg_local
    
    # 6. Create Platform Secrets (before ArgoCD!)
    print "6. Creating Platform Secrets..."
    create_platform_secrets
    
    # 7. Install ArgoCD (Helm)
    print "7. Installing ArgoCD (Helm)..."
    install_argocd
    
    print ""
    print $"(ansi green_bold)✓ Phase 1 Bootstrap complete(ansi reset)"
}

# Deploy ArgoCD Root App (triggers App-of-Apps)
def deploy_root_app [] {
    $env.KUBECONFIG = $KUBECONFIG_PATH
    
    print "Deploying ArgoCD Root App..."
    kubectl apply -f platform/base/argocd/applications/root-app.yaml
    
    print $"(ansi green)✓ Root App deployed - ArgoCD will now sync all platform components(ansi reset)"
    print ""
    print "ArgoCD Sync Waves:"
    print "  Wave -1: root-app (just deployed)"
    print "  Wave  0: cert-manager (TLS), postgresql (shared database)"
    print "  Wave  1: keycloak (IdP), argocd (self-managed GitOps)"
    print "  Wave  2: landingpage, backstage, gitea, monitoring"
    print "  Wave  3: crossplane, kyverno"
}

# Wait for ArgoCD apps to become healthy
def wait_for_argocd_apps [] {
    $env.KUBECONFIG = $KUBECONFIG_PATH
    
    print "Waiting for ArgoCD applications to sync (this may take 10-15 minutes)..."
    print ""
    
    # Apps to wait for (in wave order)
    let apps = ["cert-manager", "postgresql", "keycloak", "gitea", "landingpage", "backstage", "monitoring", "crossplane", "kyverno"]
    
    mut all_healthy = false
    mut attempts = 0
    let max_attempts = 90  # 15 minutes with 10sec intervals
    
    loop {
        $attempts = $attempts + 1
        if $attempts > $max_attempts {
            print $"(ansi yellow)Warning: Timeout waiting for all apps. Check ArgoCD UI for status.(ansi reset)"
            break
        }
        
        mut healthy_count = 0
        
        for app in $apps {
            let status = (do { 
                kubectl get application $app -n argocd -o jsonpath='{.status.health.status}' 
            } | complete)
            
            if $status.exit_code == 0 and ($status.stdout | str trim) == "Healthy" {
                $healthy_count = $healthy_count + 1
            }
        }
        
        print $"  Apps healthy: ($healthy_count)/($apps | length) [attempt ($attempts)/($max_attempts)]"
        
        if $healthy_count == ($apps | length) {
            $all_healthy = true
            break
        }
        
        sleep 10sec
    }
    
    if $all_healthy {
        print $"(ansi green)✓ All ArgoCD applications are healthy!(ansi reset)"
    }
    
    # Show final status
    print ""
    print "ArgoCD Application Status:"
    kubectl get applications -n argocd -o wide

    # Patch ArgoCD OIDC config with self-signed CA cert
    patch_argocd_oidc_ca
}

# Destroy local cluster
def "main down" [] {
    print $"(ansi yellow_bold)Destroying local cluster...(ansi reset)"
    
    if (cluster_exists) {
        kind delete cluster --name $CLUSTER_NAME
        rm -f $KUBECONFIG_PATH
        print $"(ansi green)✓ Cluster destroyed.(ansi reset)"
    } else {
        print $"Cluster '($CLUSTER_NAME)' does not exist."
    }
}

# Reset cluster (destroy + create)
def "main reset" [] {
    print $"(ansi yellow_bold)Resetting local cluster...(ansi reset)"
    main down
    main up
}

# Show cluster status
def "main status" [] {
    print $"(ansi cyan_bold)Cluster Status(ansi reset)"
    print "=============="
    
    if (cluster_exists) {
        print $"(ansi green)● Cluster '($CLUSTER_NAME)' is running(ansi reset)"
        print ""
        
        $env.KUBECONFIG = $KUBECONFIG_PATH
        
        print "Nodes:"
        kubectl get nodes -o wide
        
        print ""
        print $"(ansi cyan_bold)ArgoCD Applications(ansi reset)"
        print "==================="
        try {
            kubectl get applications -n argocd -o wide
        } catch {
            print $"(ansi yellow)ArgoCD not installed or no applications yet(ansi reset)"
        }
        
        print ""
        print $"(ansi cyan_bold)Platform Pods(ansi reset)"
        print "============="
        
        let namespaces = ["platform-db", "argocd", "keycloak", "crossplane-system", "kyverno", "monitoring", "backstage", "gitea"]
        for ns in $namespaces {
            let status = try {
                let pods = (kubectl get pods -n $ns --no-headers 2>/dev/null | lines | length)
                if $pods > 0 {
                    $"(ansi green)● ($ns) - ($pods) pods(ansi reset)"
                } else {
                    $"(ansi yellow)○ ($ns) - no pods(ansi reset)"
                }
            } catch {
                $"(ansi red)✗ ($ns) - not installed(ansi reset)"
            }
            print $"  ($status)"
        }
    } else {
        print $"(ansi red)✗ Cluster '($CLUSTER_NAME)' is not running(ansi reset)"
        print ""
        print "Run 'nu scripts/local-setup.nu up' to create the cluster."
    }
}

# -----------------------------------------------------------------------------
# Phase 1: Bootstrap Functions
# -----------------------------------------------------------------------------

def install_gateway_api [] {
    let gateway_api_version = "v1.2.1"
    let manifest_url = $"https://github.com/kubernetes-sigs/gateway-api/releases/download/($gateway_api_version)/standard-install.yaml"

    try {
        kubectl apply -f $manifest_url
        print $"(ansi green)✓ Gateway API CRDs ($gateway_api_version) installed(ansi reset)"
    } catch {
        print $"(ansi yellow)Warning: Could not install Gateway API CRDs, continuing...(ansi reset)"
    }
}

def install_ingress [] {
    kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml
    
    print "   Waiting for ingress controller..."
    sleep 10sec
    
    try {
        kubectl wait --namespace ingress-nginx --for=condition=ready pod --selector=app.kubernetes.io/component=controller --timeout=180s
    } catch {
        print $"(ansi yellow)Warning: Ingress controller not ready yet, continuing...(ansi reset)"
    }
    
    # Wait for admission webhook
    print "   Waiting for ingress admission webhook..."
    mut webhook_ready = false
    mut attempts = 0
    loop {
        $attempts = $attempts + 1
        if $attempts > 60 {
            print $"(ansi yellow)Warning: Admission webhook not confirmed ready, continuing...(ansi reset)"
            break
        }
        
        let endpoint_result = (do { kubectl get endpoints -n ingress-nginx ingress-nginx-controller-admission -o jsonpath='{.subsets[0].addresses[0].ip}' } | complete)
        if $endpoint_result.exit_code != 0 or ($endpoint_result.stdout | str trim | is-empty) {
            sleep 2sec
            continue
        }
        
        let pod_ready = (do { kubectl get pods -n ingress-nginx -l app.kubernetes.io/component=controller -o jsonpath='{.items[0].status.conditions[?(@.type=="Ready")].status}' } | complete)
        if $pod_ready.exit_code == 0 and ($pod_ready.stdout | str trim) == "True" {
            sleep 3sec
            $webhook_ready = true
            break
        }
        
        sleep 2sec
    }
    
    if $webhook_ready {
        print $"(ansi green)✓ NGINX Ingress Controller installed(ansi reset)"
    }
}

def configure_coredns_digiorg_local [] {
    let ingress_ip = (kubectl get svc -n ingress-nginx ingress-nginx-controller -o jsonpath='{.spec.clusterIP}')
    
    if ($ingress_ip | is-empty) {
        print $"(ansi yellow)Warning: Ingress Controller not found, skipping DNS config(ansi reset)"
        return
    }
    
    let current_corefile = (kubectl get configmap coredns -n kube-system -o jsonpath='{.data.Corefile}')
    
    if ($current_corefile | str contains "digiorg.local") {
        print $"(ansi green)✓ digiorg.local already configured in CoreDNS(ansi reset)"
        return
    }
    
    let temp_file = "coredns-config-temp.yaml"
    let configmap_yaml = $"apiVersion: v1
kind: ConfigMap
metadata:
  name: coredns
  namespace: kube-system
data:
  Corefile: |
    .:53 {
        errors
        health {
           lameduck 5s
        }
        ready
        kubernetes cluster.local in-addr.arpa ip6.arpa {
           pods insecure
           fallthrough in-addr.arpa ip6.arpa
           ttl 30
        }
        hosts {
           ($ingress_ip) digiorg.local
           fallthrough
        }
        prometheus :9153
        forward . /etc/resolv.conf {
           max_concurrent 1000
        }
        cache 30
        loop
        reload
        loadbalance
    }
"
    
    $configmap_yaml | save -f $temp_file
    kubectl replace -f $temp_file
    rm $temp_file
    
    kubectl rollout restart deployment coredns -n kube-system
    kubectl rollout status deployment coredns -n kube-system --timeout=60s
    
    sleep 5sec
    
    print $"(ansi green)✓ CoreDNS configured for digiorg.local [($ingress_ip)](ansi reset)"
}

# Generate a random password (alphanumeric, 24 chars)
def generate_password [] {
    random chars --length 24
}

def create_platform_secrets [] {
    # Generate passwords (can be overridden via environment variables)
    let postgres_password = ($env.POSTGRES_PASSWORD? | default (generate_password))
    let keycloak_db_password = ($env.KEYCLOAK_DB_PASSWORD? | default (generate_password))
    let backstage_db_password = ($env.BACKSTAGE_DB_PASSWORD? | default (generate_password))
    let backstage_session_secret = ($env.AUTH_SESSION_SECRET? | default (generate_password))
    let backstage_oidc_secret = ($env.AUTH_OIDC_CLIENT_SECRET? | default "backstage-client-secret")
    let gitea_db_password = ($env.GITEA_DB_PASSWORD? | default (generate_password))
    let gitea_oidc_secret = ($env.GITEA_OIDC_CLIENT_SECRET? | default "gitea-client-secret")
    
    # Platform-db namespace and PostgreSQL secrets (shared database for Keycloak + Backstage + Gitea)
    kubectl create namespace platform-db --dry-run=client -o yaml | kubectl apply -f -
    (kubectl create secret generic postgresql-secrets -n platform-db
        --from-literal=POSTGRES_PASSWORD=($postgres_password)
        --from-literal=KEYCLOAK_DB_PASSWORD=($keycloak_db_password)
        --from-literal=BACKSTAGE_DB_PASSWORD=($backstage_db_password)
        --from-literal=GITEA_DB_PASSWORD=($gitea_db_password)
        --dry-run=client -o yaml | kubectl apply -f -)
    print $"(ansi green)✓ PostgreSQL secrets created [platform-db](ansi reset)"
    
    # Keycloak namespace and DB credentials secret
    kubectl create namespace keycloak --dry-run=client -o yaml | kubectl apply -f -
    (kubectl create secret generic keycloak-db-credentials -n keycloak
        --from-literal=password=($keycloak_db_password)
        --dry-run=client -o yaml | kubectl apply -f -)
    print $"(ansi green)✓ Keycloak namespace and secrets created(ansi reset)"
    
    # Backstage secrets (use same password as PostgreSQL backstage user)
    kubectl create namespace backstage --dry-run=client -o yaml | kubectl apply -f -
    (kubectl create secret generic backstage-secrets -n backstage
        --from-literal=POSTGRES_PASSWORD=($backstage_db_password)
        --from-literal=AUTH_SESSION_SECRET=($backstage_session_secret)
        --from-literal=AUTH_OIDC_CLIENT_SECRET=($backstage_oidc_secret)
        --from-literal=GITHUB_TOKEN=""
        --dry-run=client -o yaml | kubectl apply -f -)
    print $"(ansi green)✓ Backstage secrets created(ansi reset)"
    
    # Monitoring namespace (Grafana uses Helm values for OAuth secret)
    kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -
    print $"(ansi green)✓ Monitoring namespace created(ansi reset)"
    
    # Crossplane namespace
    kubectl create namespace crossplane-system --dry-run=client -o yaml | kubectl apply -f -
    print $"(ansi green)✓ Crossplane namespace created(ansi reset)"
    
    # Kyverno namespace
    kubectl create namespace kyverno --dry-run=client -o yaml | kubectl apply -f -
    print $"(ansi green)✓ Kyverno namespace created(ansi reset)"
    
    # Gitea namespace and secrets
    let gitea_admin_password_override = ($env.GITEA_ADMIN_PASSWORD? | default "")
    kubectl create namespace gitea --dry-run=client -o yaml | kubectl apply -f -
    (kubectl create secret generic gitea-secrets -n gitea
        --from-literal=POSTGRES_PASSWORD=($gitea_db_password)
        --from-literal=AUTH_OIDC_CLIENT_SECRET=($gitea_oidc_secret)
        --dry-run=client -o yaml | kubectl apply -f -)
    # Admin secret is bootstrap-only for Gitea; preserve on re-runs unless explicitly overridden
    let gitea_admin_secret_exists = ((do -i { kubectl get secret gitea-admin-secret -n gitea } | complete).exit_code == 0)
    if (not $gitea_admin_secret_exists) or ($gitea_admin_password_override != "") {
        let gitea_admin_password = (if $gitea_admin_password_override != "" { $gitea_admin_password_override } else { generate_password })
        (kubectl create secret generic gitea-admin-secret -n gitea
            --from-literal=username=gitea_admin
            --from-literal=password=($gitea_admin_password)
            --dry-run=client -o yaml | kubectl apply -f -)
        print $"(ansi green)✓ Gitea namespace and secrets created(ansi reset)"
    } else {
        print $"(ansi green)✓ Gitea namespace and secrets created(ansi reset)"
        print $"(ansi yellow)  ! Existing gitea-admin-secret preserved; set GITEA_ADMIN_PASSWORD to rotate(ansi reset)"
    }
}

def install_argocd [] {
    kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -

    helm repo add argo https://argoproj.github.io/argo-helm
    helm repo update

    (helm upgrade --install argocd argo/argo-cd
        --namespace argocd
        --create-namespace
        --values platform/base/argocd/values.yaml
        --set 'server.service.type=ClusterIP'
        --set 'configs.params.server\.insecure=true'
        --wait --timeout 10m)

    print $"(ansi green)✓ ArgoCD installed [Helm](ansi reset)"
}

# Restart pods that depend on OIDC/Keycloak
def restart_oidc_dependent_pods [] {
    $env.KUBECONFIG = $KUBECONFIG_PATH
    
    print "Restarting OIDC-dependent pods to refresh DNS/config..."
    
    # ArgoCD Server
    try {
        kubectl rollout restart deployment argocd-server -n argocd
        kubectl rollout status deployment argocd-server -n argocd --timeout=120s
        print $"  (ansi green)✓ ArgoCD Server restarted(ansi reset)"
    } catch {
        print $"  (ansi yellow)Warning: Could not restart ArgoCD Server(ansi reset)"
    }
    
    # Grafana
    try {
        let grafana_exists = (do { kubectl get deployment prometheus-grafana -n monitoring } | complete)
        if $grafana_exists.exit_code == 0 {
            kubectl rollout restart deployment prometheus-grafana -n monitoring
            kubectl rollout status deployment prometheus-grafana -n monitoring --timeout=120s
            print $"  (ansi green)✓ Grafana restarted(ansi reset)"
        }
    } catch { }
    
    # Backstage
    try {
        let backstage_exists = (do { kubectl get deployment backstage -n backstage } | complete)
        if $backstage_exists.exit_code == 0 {
            kubectl rollout restart deployment backstage -n backstage
            kubectl rollout status deployment backstage -n backstage --timeout=180s
            print $"  (ansi green)✓ Backstage restarted(ansi reset)"
        }
    } catch { }

    # Landing Page
    try {
        let lp_exists = (do { kubectl get deployment landingpage -n platform-apps } | complete)
        if $lp_exists.exit_code == 0 {
            kubectl rollout restart deployment landingpage -n platform-apps
            print $"  (ansi green)✓ Landing Page restarted(ansi reset)"
        }
    } catch { }
    
    print $"(ansi green)✓ OIDC-dependent pods restarted(ansi reset)"
}

# Patch ArgoCD OIDC config with the self-signed CA cert via Helm upgrade.
# Uses helm upgrade --reuse-values so ArgoCD self-sync does not overwrite it.
# kubectl patch is NOT used because ArgoCD self-manages its own Helm release
# and would overwrite any direct ConfigMap patch on the next sync.
def patch_argocd_oidc_ca [] {
    $env.KUBECONFIG = $KUBECONFIG_PATH

    print "Patching ArgoCD OIDC config with self-signed CA cert (via Helm)..."

    # Wait for cert-manager to issue the CA cert
    mut attempts = 0
    loop {
        $attempts = $attempts + 1
        if $attempts > 30 {
            print $"(ansi yellow)Warning: CA cert not available yet, skipping ArgoCD OIDC patch(ansi reset)"
            return
        }
        let secret_result = (do {
            kubectl get secret digiorg-local-ca-secret -n cert-manager --ignore-not-found -o name
        } | complete)
        if $secret_result.exit_code == 0 and ($secret_result.stdout | str trim | is-not-empty) {
            break
        }
        print $"  Waiting for CA cert... (attempt ($attempts)/30)"
        sleep 10sec
    }

    # Extract CA cert (base64-encoded)
    let ca_cert_b64_result = (do {
        kubectl get secret digiorg-local-ca-secret -n cert-manager -o jsonpath='{.data.ca\.crt}'
    } | complete)
    if $ca_cert_b64_result.exit_code != 0 or ($ca_cert_b64_result.stdout | str trim | is-empty) {
        print $"(ansi yellow)Warning: Could not extract CA cert, skipping ArgoCD OIDC patch(ansi reset)"
        return
    }

    # Decode using Nushell native decode (portable across macOS and Linux)
    let ca_cert = ($ca_cert_b64_result.stdout | str trim | decode base64 | decode)

    # Save CA cert to file for user reference
    $ca_cert | save -f digiorg-local-ca.crt

    # Build oidc.config YAML with rootCA embedded
    # Indent cert lines with 2 spaces for rootCA block scalar
    let indented_cert = ($ca_cert | str trim | lines | each { |line| $"  ($line)" } | str join "\n")
    let oidc_config = $"name: Keycloak
issuer: https://digiorg.local/keycloak/realms/digiorg-core-platform
clientID: argocd
clientSecret: $oidc.keycloak.clientSecret
requestedScopes:
  - openid
  - profile
  - email
  - roles
rootCA: |\n($indented_cert)
"

    # Write Helm values override with oidc.config containing rootCA
    let helm_override = {configs: {cm: {"oidc.config": $oidc_config}}}
    $helm_override | to yaml | save -f ./argocd-oidc-override.yaml

    # Re-run helm upgrade with the override — embeds CA cert in the Helm release
    # so ArgoCD self-sync will not overwrite it
    print "  Running helm upgrade to embed CA cert in ArgoCD release..."
    (helm upgrade argocd argo/argo-cd
        --namespace argocd
        --reuse-values
        --values platform/base/argocd/values.yaml
        --values ./argocd-oidc-override.yaml
        --force-conflicts
        --wait --timeout 5m)

    print $"(ansi green)✓ ArgoCD OIDC config updated with CA cert via Helm(ansi reset)"

    # Restart ArgoCD server to pick up new config immediately
    kubectl rollout restart deployment argocd-server -n argocd
    kubectl rollout status deployment argocd-server -n argocd --timeout=120s
    print $"(ansi green)✓ ArgoCD server restarted(ansi reset)"

    # Print CA trust instructions
    print ""
    print $"(ansi cyan_bold)╔════════════════════════════════════════════════════════════════╗(ansi reset)"
    print $"(ansi cyan_bold)║  Trust the Self-Signed CA Certificate                          ║(ansi reset)"
    print $"(ansi cyan_bold)╚════════════════════════════════════════════════════════════════╝(ansi reset)"
    print ""
    print "  CA cert saved to: ./digiorg-local-ca.crt"
    print ""
    print "  macOS:"
    print "    sudo security add-trusted-cert -d -r trustRoot \\"
    print "      -k /Library/Keychains/System.keychain digiorg-local-ca.crt"
    print ""
    print "  Linux (Ubuntu/Debian):"
    print "    sudo cp digiorg-local-ca.crt /usr/local/share/ca-certificates/"
    print "    sudo update-ca-certificates"
    print ""
    print "  Windows:"
    print "    certutil -addstore -f ROOT digiorg-local-ca.crt"
    print ""
    print $"(ansi yellow)Restart your browser after importing the CA certificate.(ansi reset)"
}

# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

def check_prerequisites [] {
    print "Checking prerequisites..."
    
    let tools = [
        ["kind", "https://kind.sigs.k8s.io/docs/user/quick-start/#installation"],
        ["kubectl", "https://kubernetes.io/docs/tasks/tools/"],
        ["helm", "https://helm.sh/docs/intro/install/"]
    ]
    
    mut missing = []
    
    for tool in $tools {
        let name = $tool.0
        let url = $tool.1
        
        let exists = (which $name | length) > 0
        if not $exists {
            $missing = ($missing | append $name)
            print $"(ansi red)✗ ($name) not found(ansi reset) - Install: ($url)"
        } else {
            print $"(ansi green)✓ ($name)(ansi reset)"
        }
    }
    
    if ($missing | length) > 0 {
        print ""
        print $"(ansi red_bold)Missing required tools. Please install them and try again.(ansi reset)"
        exit 1
    }
    
    print ""
}

def cluster_exists [] {
    let result = (do { kind get clusters } | complete)
    if $result.exit_code == 0 {
        $CLUSTER_NAME in ($result.stdout | str trim | lines)
    } else {
        false
    }
}
