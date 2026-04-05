#!/usr/bin/env nu

# =============================================================================
# Local Development Environment Setup
# =============================================================================
# This script manages the local KinD cluster for development.
#
# Usage:
#   nu scripts/local-setup.nu up       # Start local cluster
#   nu scripts/local-setup.nu down     # Destroy local cluster
#   nu scripts/local-setup.nu reset    # Reset cluster
#   nu scripts/local-setup.nu status   # Show cluster status
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
    print "  up      - Create local cluster and install components"
    print "  down    - Destroy local cluster"
    print "  reset   - Reset cluster (down + up)"
    print "  status  - Show cluster status"
    print "  install - Install platform components on existing cluster"
    print ""
    print $"Usage: nu scripts/local-setup.nu <command>"
}

# Create local cluster and install all components
def "main up" [
    --skip-components  # Skip installing platform components
] {
    print $"(ansi green_bold)Creating local development cluster...(ansi reset)"
    
    # Check prerequisites
    check_prerequisites
    
    # Create cluster if it doesn't exist
    if (cluster_exists) {
        print $"(ansi yellow)Cluster '($CLUSTER_NAME)' already exists.(ansi reset)"
    } else {
        print $"Creating KinD cluster '($CLUSTER_NAME)'..."
        kind create cluster --config $KIND_CONFIG --kubeconfig $KUBECONFIG_PATH
    }
    
    # Set KUBECONFIG
    $env.KUBECONFIG = $KUBECONFIG_PATH
    
    # Wait for cluster to be ready
    print "Waiting for cluster to be ready..."
    kubectl wait --for=condition=Ready nodes --all --timeout=120s
    
    if not $skip_components {
        # Install platform components
        main install
    }
    
    print ""
    print $"(ansi green_bold)✓ Local cluster is ready!(ansi reset)"
    print ""
    print $"Export kubeconfig:"
    print $"  export KUBECONFIG=($KUBECONFIG_PATH)"
    print ""
    print "Access services (all via digiorg.local):"
    print "  Keycloak:   http://digiorg.local/keycloak   (admin / admin)"
    print "  ArgoCD:     http://digiorg.local/argocd     (Login via Keycloak)"
    print "  Grafana:    http://digiorg.local/grafana    (Login via Keycloak)"
    print "  Backstage:  http://digiorg.local/backstage  (Login via Keycloak)"
    print ""
    print $"(ansi yellow)Prerequisite: Add to /etc/hosts: 127.0.0.1 digiorg.local(ansi reset)"
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
        print "Platform Components:"
        
        # Check namespaces
        let namespaces = ["argocd", "keycloak", "crossplane-system", "kyverno", "monitoring", "backstage"]
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

# Install platform components
def "main install" [
    --components: string = "all"  # Components to install (all, gateway, ingress, argocd, keycloak, crossplane, kyverno, monitoring, backstage)
] {
    print $"(ansi cyan_bold)Installing platform components...(ansi reset)"

    $env.KUBECONFIG = $KUBECONFIG_PATH

    let install_all = $components == "all"

    # 1. Install Gateway API CRDs (before ingress, no feature gate needed)
    if $install_all or ($components | str contains "gateway") {
        install_gateway_api
    }

    # 2. Install Ingress (Controller + Platform routing rules)
    if $install_all or ($components | str contains "ingress") {
        install_ingress
        install_platform_ingress
    }

    # 3. Install Keycloak (IdP for SSO)
    if $install_all or ($components | str contains "keycloak") {
        install_keycloak_deployment
        wait_for_keycloak_realm
    }

    # 4. Install ArgoCD (after Keycloak so OIDC is available from the start)
    if $install_all or ($components | str contains "argocd") {
        install_argocd
    }

    # 5. Install Crossplane
    if $install_all or ($components | str contains "crossplane") {
        install_crossplane
    }

    # 6. Install Kyverno
    if $install_all or ($components | str contains "kyverno") {
        install_kyverno
    }

    # 7. Install Monitoring
    if $install_all or ($components | str contains "monitoring") {
        install_monitoring
    }

    # 8. Install Backstage Developer Portal
    if $install_all or ($components | str contains "backstage") {
        install_backstage
    }

    # 9. Restart OIDC-dependent pods to pick up CoreDNS changes
    # This ensures ArgoCD, Grafana, and Backstage can resolve digiorg.local
    if $install_all {
        restart_oidc_dependent_pods
    }

    print ""
    print $"(ansi green_bold)✓ Platform components installed!(ansi reset)"
}

# -----------------------------------------------------------------------------
# Component Installation Functions
# -----------------------------------------------------------------------------

def install_gateway_api [] {
    print "Installing Gateway API CRDs..."

    # Standard channel includes GatewayClass, Gateway, HTTPRoute, ReferenceGrant
    # See: https://github.com/kubernetes-sigs/gateway-api/releases
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
    print "Installing NGINX Ingress Controller..."
    
    kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml
    
    print "Waiting for ingress controller..."
    sleep 10sec
    
    try {
        kubectl wait --namespace ingress-nginx --for=condition=ready pod --selector=app.kubernetes.io/component=controller --timeout=180s
    } catch {
        print $"(ansi yellow)Warning: Ingress controller not ready yet, continuing...(ansi reset)"
    }
    
    # Wait for admission webhook to be fully ready
    # The webhook validates Ingress resources and must be available before creating Ingress rules
    # We need to wait for BOTH: endpoint IP exists AND webhook is actually accepting connections
    print "Waiting for ingress admission webhook..."
    mut webhook_ready = false
    mut attempts = 0
    loop {
        $attempts = $attempts + 1
        if $attempts > 60 {
            print $"(ansi yellow)Warning: Admission webhook not confirmed ready after 120s, continuing...(ansi reset)"
            break
        }
        
        # First check if the endpoint has an IP
        let endpoint_result = (do { kubectl get endpoints -n ingress-nginx ingress-nginx-controller-admission -o jsonpath='{.subsets[0].addresses[0].ip}' } | complete)
        if $endpoint_result.exit_code != 0 or ($endpoint_result.stdout | str trim | is-empty) {
            sleep 2sec
            continue
        }
        
        # Endpoint has IP, now verify the webhook service is actually accepting connections
        # by checking that the controller pod is fully ready (not just scheduled)
        let pod_ready = (do { kubectl get pods -n ingress-nginx -l app.kubernetes.io/component=controller -o jsonpath='{.items[0].status.conditions[?(@.type=="Ready")].status}' } | complete)
        if $pod_ready.exit_code == 0 and ($pod_ready.stdout | str trim) == "True" {
            # Additional wait to ensure webhook server inside pod is listening
            sleep 3sec
            $webhook_ready = true
            break
        }
        
        sleep 2sec
    }
    
    if $webhook_ready {
        print $"(ansi green)✓ Ingress admission webhook ready(ansi reset)"
    }
}

def install_platform_ingress [] {
    print "Installing DigiOrg Platform Ingress (unified routing)..."
    
    # Apply the central ingress configuration
    kubectl apply -k platform/base/ingress/
    
    # Configure digiorg.local DNS resolution for all pods via CoreDNS
    # This must happen after Ingress Controller is installed (needs ClusterIP)
    # but before applications that need to resolve digiorg.local
    configure_coredns_digiorg_local
    
    print $"(ansi green)✓ Platform Ingress installed [digiorg.local](ansi reset)"
}

def install_argocd [] {
    print "Installing ArgoCD..."

    # Create namespace
    kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -

    # Add Helm repo
    helm repo add argo https://argoproj.github.io/argo-helm
    helm repo update

    # Install ArgoCD with values (OIDC config) - accessed via Ingress
    (helm upgrade --install argocd argo/argo-cd
        --namespace argocd
        --create-namespace
        --values platform/base/argocd/values.yaml
        --set 'server.service.type=ClusterIP'
        --set 'configs.params.server\.insecure=true'
        --wait --timeout 10m)

    print $"(ansi green)✓ ArgoCD installed with Keycloak SSO(ansi reset)"
}

# Install Keycloak deployment only (realm check happens later after Ingress is installed)
def install_keycloak_deployment [] {
    print "Installing Keycloak (IdP)..."

    # Apply all manifests: PostgreSQL + Keycloak Deployment + realm ConfigMap
    kubectl create namespace keycloak --dry-run=client -o yaml | kubectl apply -f -
    kubectl apply -k platform/base/keycloak/

    # Wait for PostgreSQL first
    kubectl rollout status deployment/postgres -n keycloak --timeout=5m

    # Wait for Keycloak pod to be ready (JVM startup is slow)
    kubectl rollout status deployment/keycloak -n keycloak --timeout=15m

    print $"(ansi green)✓ Keycloak deployment ready(ansi reset)"
}

# Wait for Keycloak realm (called after Platform Ingress is installed)
def wait_for_keycloak_realm [] {
    print "Waiting for Keycloak realm 'digiorg-core-platform' to be available (up to 10m)..."
    mut realm_ready = false
    mut attempts = 0
    loop {
        $attempts = $attempts + 1
        if $attempts > 60 {
            print $"(ansi yellow)Warning: Keycloak realm not confirmed reachable after 10m, continuing...(ansi reset)"
            break
        }
        # Check realm via Ingress URL (requires /etc/hosts entry for digiorg.local)
        let result = (do { curl -sf http://digiorg.local/keycloak/realms/digiorg-core-platform } | complete)
        if $result.exit_code == 0 {
            $realm_ready = true
            break
        }
        print $"  Realm not ready yet [attempt ($attempts)/60]..."
        sleep 10sec
    }

    if $realm_ready {
        print $"(ansi green)✓ Keycloak realm 'digiorg-core-platform' ready [admin / admin — http://digiorg.local/keycloak](ansi reset)"
    }
}

def install_crossplane [] {
    print "Installing Crossplane..."
    
    # Add Helm repo
    helm repo add crossplane-stable https://charts.crossplane.io/stable
    helm repo update
    
    # Install Crossplane
    helm upgrade --install crossplane crossplane-stable/crossplane --namespace crossplane-system --create-namespace --wait --timeout 15m
    
    print $"(ansi green)✓ Crossplane installed(ansi reset)"
}

def install_kyverno [] {
    print "Installing Kyverno..."
    
    # Add Helm repo
    helm repo add kyverno https://kyverno.github.io/kyverno/
    helm repo update
    
    # Install Kyverno with reduced resource requests for local development
    # Default requests (400m CPU total) exceed available resources in KinD
    (helm upgrade --install kyverno kyverno/kyverno
        --namespace kyverno
        --create-namespace
        --set 'replicaCount=1'
        --set 'admissionController.replicas=1'
        --set 'admissionController.container.resources.requests.cpu=50m'
        --set 'admissionController.container.resources.requests.memory=64Mi'
        --set 'admissionController.initContainer.resources.requests.cpu=10m'
        --set 'admissionController.initContainer.resources.requests.memory=32Mi'
        --set 'backgroundController.resources.requests.cpu=50m'
        --set 'backgroundController.resources.requests.memory=32Mi'
        --set 'cleanupController.resources.requests.cpu=50m'
        --set 'cleanupController.resources.requests.memory=32Mi'
        --set 'reportsController.resources.requests.cpu=50m'
        --set 'reportsController.resources.requests.memory=32Mi'
        --wait --timeout 15m)
    
    print $"(ansi green)✓ Kyverno installed(ansi reset)"
}

def install_monitoring [] {
    print "Installing Prometheus Stack (this may take a while)..."

    # Add Helm repo
    helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
    helm repo update

    # Install with values file (includes Grafana OAuth2 via Keycloak)
    (helm upgrade --install prometheus prometheus-community/kube-prometheus-stack
        --namespace monitoring
        --create-namespace
        --values platform/base/monitoring/values.yaml
        --wait --timeout 15m)

    print $"(ansi green)✓ Monitoring installed with Grafana OAuth2 [Keycloak](ansi reset)"
}

def install_backstage [] {
    print "Installing Backstage Developer Portal..."

    # Apply Backstage manifests (namespace, postgres, secrets, deployment, service)
    # Using custom DigiOrg Backstage image from ghcr.io
    kubectl apply -k platform/base/backstage/

    # Wait for PostgreSQL first
    print "  Waiting for PostgreSQL..."
    kubectl rollout status deployment/backstage-postgres -n backstage --timeout=5m

    # Wait for Backstage to be ready (image pull may take a while)
    print "  Waiting for Backstage (may take a few minutes for image pull)..."
    kubectl rollout status deployment/backstage -n backstage --timeout=10m

    print $"(ansi green)✓ Backstage installed [ghcr.io/digiorg/core-backstage-image](ansi reset)"
    print $"  (ansi yellow)Note: For GitHub integration, add a token to backstage-secrets(ansi reset)"
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

# Configure CoreDNS to resolve digiorg.local to Ingress Controller
# This makes digiorg.local accessible from ALL pods in the cluster
def configure_coredns_digiorg_local [] {
    print "  Configuring digiorg.local DNS via CoreDNS..."
    
    # Get Ingress Controller ClusterIP
    let ingress_ip = (kubectl get svc -n ingress-nginx ingress-nginx-controller -o jsonpath='{.spec.clusterIP}')
    
    if ($ingress_ip | is-empty) {
        print $"  (ansi yellow)Warning: Ingress Controller not found, skipping DNS config(ansi reset)"
        return
    }
    
    # Get current CoreDNS Corefile
    let current_corefile = (kubectl get configmap coredns -n kube-system -o jsonpath='{.data.Corefile}')
    
    # Check if hosts block already exists
    if ($current_corefile | str contains "digiorg.local") {
        print $"  (ansi green)✓ digiorg.local already configured in CoreDNS(ansi reset)"
        return
    }
    
    # Create ConfigMap YAML with new Corefile (temp file for Windows compatibility)
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
    
    # Save and replace (works on both Windows and Linux)
    # Using 'replace' instead of 'apply' to avoid annotation warning
    # since the ConfigMap was created by KinD, not kubectl apply
    $configmap_yaml | save -f $temp_file
    kubectl replace -f $temp_file
    rm $temp_file
    
    # Restart CoreDNS to pick up changes
    kubectl rollout restart deployment coredns -n kube-system
    kubectl rollout status deployment coredns -n kube-system --timeout=60s
    
    # Wait a moment for DNS propagation
    sleep 5sec
    
    print $"  (ansi green)✓ digiorg.local DNS configured via CoreDNS [($ingress_ip)](ansi reset)"
}

# Restart pods that depend on OIDC/Keycloak to ensure they have fresh DNS cache
# This is needed because pods started before CoreDNS was patched won't resolve digiorg.local
def restart_oidc_dependent_pods [] {
    print "Restarting OIDC-dependent pods to refresh DNS cache..."
    
    # ArgoCD Server needs to reach Keycloak for OIDC discovery
    try {
        kubectl rollout restart deployment argocd-server -n argocd
        kubectl rollout status deployment argocd-server -n argocd --timeout=120s
        print $"  (ansi green)✓ ArgoCD Server restarted(ansi reset)"
    } catch {
        print $"  (ansi yellow)Warning: Could not restart ArgoCD Server(ansi reset)"
    }
    
    # Grafana needs Keycloak for OAuth (if installed)
    try {
        let grafana_exists = (kubectl get deployment prometheus-grafana -n monitoring out>err | complete)
        if $grafana_exists.exit_code == 0 {
            kubectl rollout restart deployment prometheus-grafana -n monitoring
            kubectl rollout status deployment prometheus-grafana -n monitoring --timeout=120s
            print $"  (ansi green)✓ Grafana restarted(ansi reset)"
        }
    } catch {
        # Grafana might not be installed yet, that's fine
    }
    
    # Backstage needs Keycloak for OIDC (if installed)
    try {
        let backstage_exists = (kubectl get deployment backstage -n backstage out>err | complete)
        if $backstage_exists.exit_code == 0 {
            kubectl rollout restart deployment backstage -n backstage
            kubectl rollout status deployment backstage -n backstage --timeout=120s
            print $"  (ansi green)✓ Backstage restarted(ansi reset)"
        }
    } catch {
        # Backstage might not be installed yet, that's fine
    }
    
    print $"(ansi green)✓ OIDC-dependent pods restarted(ansi reset)"
}
