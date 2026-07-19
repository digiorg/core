#!/usr/bin/env nu

# =============================================================================
# Local Development Environment Setup (App-of-Apps Pattern)
# =============================================================================
# This script bootstraps the local KinD cluster and deploys the ArgoCD root app.
# ArgoCD then manages all platform components via the App-of-Apps pattern.
#
# Usage:
#   nu scripts/local-setup.nu up        # Bootstrap cluster + deploy root app
#   nu scripts/local-setup.nu down      # Destroy local cluster
#   nu scripts/local-setup.nu reset     # Reset cluster (down + up)
#   nu scripts/local-setup.nu status    # Show cluster status
#   nu scripts/local-setup.nu bootstrap # Run only Phase 1 bootstrap (no root app)
#
# Architecture:
#   Phase 1 (this script): KinD → Ingress → CoreDNS → Secrets → ArgoCD → Root App
#   Phase 2 (ArgoCD):      Root App → ApplicationSet → Platform Components
# =============================================================================

# Configuration
let CLUSTER_NAME = "digiorg-core-dev"
let KIND_CONFIG = "platform/bootstrap/kind-config.yaml"
let KIND_NODE_IMAGE = "kindest/node:v1.36.1@sha256:3489c7674813ba5d8b1a9977baea8a6e553784dab7b84759d1014dbd78f7ebd5"
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
    
    # Configure Gitea (Keycloak OIDC integration + initial users/org) — requires Gitea to be up and running
    print ""
    print $"(ansi cyan_bold)Phase 3: Configure Gitea(ansi reset)"
    print "────────────────────────────────────"
    configure_gitea

    # Configure SonarQube (SAML + base URL)
    print ""
    print $"(ansi cyan_bold)Phase 4: Configure SonarQube(ansi reset)"
    print "────────────────────────────────────"
    configure_sonarqube

    # Restart OIDC-dependent pods after Keycloak is ready
    print ""
    print $"(ansi cyan_bold)Phase 5: Restart OIDC dependent PODs(ansi reset)"
    print "────────────────────────────────────"
    restart_oidc_dependent_pods
    
    print ""
    print $"(ansi green_bold)╔════════════════════════════════════════════════════════════════╗(ansi reset)"
    print $"(ansi green_bold)║  ✓ Platform Ready!                                             ║(ansi reset)"
    print $"(ansi green_bold)╚════════════════════════════════════════════════════════════════╝(ansi reset)"
    print ""
    print $"Export kubeconfig:"
    print $"  export KUBECONFIG=($KUBECONFIG_PATH)"
    print ""
    print "Access services (all via https://digiorg.local):"
    print "  Landing Page: https://digiorg.local/          (Login via Keycloak)"
    print "  Keycloak:     https://digiorg.local/keycloak  (admin / admin)"
    print "  Backstage:    https://digiorg.local/backstage (Login via Keycloak)"
    print "  Gitea:        https://digiorg.local/gitea     (Login via Keycloak)"
    print "  SonarQube:    https://digiorg.local/sonarqube (Login via Keycloak)"
    print "  ArgoCD:       https://digiorg.local/argocd    (Login via Keycloak)"
    print "  Grafana:      https://digiorg.local/grafana   (Login via Keycloak)"
    print "  Jaeger:       https://digiorg.local/jaeger    (Login via Keycloak)"
    print "  OpenCost:    https://digiorg.local/opencost  (Login via Keycloak)"
    print "  Harbor:       https://digiorg.local/harbor   (Login via Keycloak)"
    print ""
    print $"(ansi yellow)Prerequisites:(ansi reset)"
    print $"  1. Add to /etc/hosts: 127.0.0.1 digiorg.local"
    print $"  2. Import CA cert into your OS trust store [see above]"
}

# Run only Phase 1 bootstrap (no root app)
def "main bootstrap" [] {
    # 0. Check prerequisites
    check_prerequisites
    
    # 1. Create KinD cluster
    if (cluster_exists) {
        print $"(ansi yellow)✓ Cluster '($CLUSTER_NAME)' already exists(ansi reset)"
    } else {
        print "1.1 Creating KinD cluster..."
        kind create cluster --image $KIND_NODE_IMAGE --config $KIND_CONFIG --kubeconfig $KUBECONFIG_PATH
        print $"(ansi green)✓ KinD cluster created(ansi reset)"
    }
    $env.KUBECONFIG = $KUBECONFIG_PATH
    
    # Wait for cluster
    print "Waiting for cluster nodes..."
    kubectl wait --for=condition=Ready nodes --all --timeout=120s
    
    # 2. Set node runtime limits required by the local all-in-one platform.
    # OpenSearch needs vm.max_map_count. The many Kubernetes controllers use
    # inotify; KinD's default 128 user instances is exhausted during a full
    # bootstrap and prevents hook containers from starting (issue #279).
    print "1.2 Setting KinD node runtime limits..."
    let kind_node = $"($CLUSTER_NAME)-control-plane"
    docker exec $kind_node sysctl -w vm.max_map_count=262144
    docker exec $kind_node sysctl -w fs.inotify.max_user_instances=8192
    docker exec $kind_node sysctl -w fs.inotify.max_user_watches=1048576
    print $"(ansi green)✓ KinD node runtime limits configured(ansi reset)"
    
    # 3. Install Gateway API CRDs
    print "1.3 Installing Gateway API CRDs..."
    install_gateway_api

    # 3b. Install Prometheus Operator CRDs (required for ServiceMonitors)
    print "1.3b Installing Prometheus Operator CRDs..."
    install_prometheus_crds

    # 4. Install Ingress Controller
    print "1.4 Installing NGINX Ingress Controller..."
    install_ingress
    
    # 5. Apply Platform Ingress rules
    print "1.5 Installing Platform Ingress rules..."
    kubectl apply -k platform/base/ingress/
    print $"(ansi green)✓ Platform Ingress installed(ansi reset)"
    
    # 6. Configure CoreDNS for digiorg.local
    print "1.6 Configuring CoreDNS for digiorg.local..."
    configure_coredns_digiorg_local
    
    # 7. Create Platform Secrets (before ArgoCD!)
    print "1.7 Creating Platform Nanespaces and Secrets..."
    create_platform_namespaces_secrets
    
    # 8. Install ArgoCD (Helm)
    print "1.8 Installing ArgoCD (Helm)..."
    install_argocd

    # 9. Build & load the custom subpath-aware OpenCost UI image (Issue #272 Opt B)
    print "1.9 Building custom OpenCost UI image..."
    build_opencost_ui_image

    # 10. Build & load the Tier-1 DigiOrg images (Keycloak, Fluentd) — Issue #275
    print "1.10 Building Tier-1 DigiOrg images (Keycloak, Fluentd)..."
    build_tier1_images

    print ""
    print $"(ansi green_bold)✓ Phase 1 Bootstrap complete(ansi reset)"
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
        
        let namespaces = ["kube-system", "platform-db", "argocd", "keycloak", "messaging", "crossplane-system", "kyverno", "monitoring", "backstage", "gitea", "platform-apps", "cert-manager", "code-quality", "tracing", "external-secrets", "cost-monitoring", "harbor", "opensearch"]
        for ns in $namespaces {
            let status = try {
                let pods = (kubectl get pods -n $ns --no-headers | lines | length)
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

# Install Prometheus Operator CRDs (ServiceMonitor, PodMonitor, PrometheusRule)
# Required before ArgoCD deploys any ServiceMonitor resources.
# kube-prometheus-stack (grafana, Wave 2) will later adopt and manage these CRDs.
def install_prometheus_crds [] {
    let prom_op_version = "v0.92.1"  # Issue #275: aligned with kube-prometheus-stack 87.17.0 (prometheus-operator v0.92.1)
    let base_url = $"https://raw.githubusercontent.com/prometheus-operator/prometheus-operator/($prom_op_version)/example/prometheus-operator-crd"

    let crds = [
        "monitoring.coreos.com_alertmanagerconfigs.yaml"
        "monitoring.coreos.com_alertmanagers.yaml"
        "monitoring.coreos.com_podmonitors.yaml"
        "monitoring.coreos.com_probes.yaml"
        "monitoring.coreos.com_prometheusagents.yaml"
        "monitoring.coreos.com_prometheuses.yaml"
        "monitoring.coreos.com_prometheusrules.yaml"
        "monitoring.coreos.com_scrapeconfigs.yaml"
        "monitoring.coreos.com_servicemonitors.yaml"
        "monitoring.coreos.com_thanosrulers.yaml"
    ]

    for crd in $crds {
        let result = (do -i {
            kubectl apply --server-side -f $"($base_url)/($crd)"
        } | complete)
        if $result.exit_code == 0 {
            print $"(ansi green)✓ ($crd) installed(ansi reset)"
        } else {
            error make {msg: $"Failed to install Prometheus Operator CRD ($crd): ($result.stderr | str trim)"}
        }
    }
}

def install_ingress [] {
    kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml
    
    print "Waiting for ingress controller..."
    sleep 10sec
    
    try {
        kubectl wait --namespace ingress-nginx --for=condition=ready pod --selector=app.kubernetes.io/component=controller --timeout=180s
    } catch {
        print $"(ansi yellow)Warning: Ingress controller not ready yet, continuing...(ansi reset)"
    }
    
    # Wait for admission webhook
    print "Waiting for ingress admission webhook..."
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

def create_platform_namespaces_secrets [] {
    # Generate passwords (can be overridden via environment variables)
    let postgres_password = ($env.POSTGRES_PASSWORD? | default (generate_password))
    let keycloak_db_password = ($env.KEYCLOAK_DB_PASSWORD? | default (generate_password))
    let backstage_db_password = ($env.BACKSTAGE_DB_PASSWORD? | default (generate_password))
    let backstage_session_secret = ($env.AUTH_SESSION_SECRET? | default (generate_password))
    let backstage_oidc_secret = ($env.AUTH_OIDC_CLIENT_SECRET? | default "backstage-client-secret")
    let gitea_db_password = ($env.GITEA_DB_PASSWORD? | default (generate_password))
    let gitea_oidc_secret = ($env.GITEA_OIDC_CLIENT_SECRET? | default "gitea-client-secret")
    let sonarqube_db_password = ($env.SONARQUBE_DB_PASSWORD? | default (generate_password))
    let sonarqube_monitoring_passcode = ($env.SONARQUBE_MONITORING_PASSCODE? | default (generate_password))
    let harbor_admin_password = ($env.HARBOR_ADMIN_PASSWORD? | default "Harbor12345")
    let harbor_secret_key = ($env.HARBOR_SECRET_KEY? | default "not-a-secure-key")
    let harbor_db_password = ($env.HARBOR_DB_PASSWORD? | default (generate_password))
    let harbor_oidc_secret = ($env.HARBOR_OIDC_CLIENT_SECRET? | default "harbor-client-secret")
    
    # Platform-db namespace and PostgreSQL secrets (shared database for Keycloak + Backstage + Gitea)
    kubectl create namespace platform-db --dry-run=client -o yaml | kubectl apply -f -
    (kubectl create secret generic postgresql-secrets -n platform-db
        --from-literal=POSTGRES_PASSWORD=($postgres_password)
        --from-literal=KEYCLOAK_DB_PASSWORD=($keycloak_db_password)
        --from-literal=BACKSTAGE_DB_PASSWORD=($backstage_db_password)
        --from-literal=GITEA_DB_PASSWORD=($gitea_db_password)
        --from-literal=SONARQUBE_DB_PASSWORD=($sonarqube_db_password)
        --from-literal=HARBOR_DB_PASSWORD=($harbor_db_password)
        --dry-run=client -o yaml | kubectl apply -f -)

    # CNPG superuser secret (Issue #275, Tier-3 migration). CloudNativePG requires
    # its superuserSecret to be a kubernetes.io/basic-auth Secret with
    # username=postgres + password (the Opaque postgresql-secrets above cannot be
    # reused: a Secret's type is immutable). Reuse the SAME postgres_password so
    # the init Job (auths with POSTGRES_PASSWORD) connects. Used by the CNPG
    # Cluster in platform/base/cnpg/cluster.yaml.
    (kubectl create secret generic postgresql-cnpg-superuser -n platform-db
        --type=kubernetes.io/basic-auth
        --from-literal=username=postgres
        --from-literal=password=($postgres_password)
        --dry-run=client -o yaml | kubectl apply -f -)
    
    # Keycloak namespace and DB credentials secret
    kubectl create namespace keycloak --dry-run=client -o yaml | kubectl apply -f -
    (kubectl create secret generic keycloak-db-credentials -n keycloak
        --from-literal=password=($keycloak_db_password)
        --dry-run=client -o yaml | kubectl apply -f -)
    
    # Backstage secrets (use same password as PostgreSQL backstage user)
    kubectl create namespace backstage --dry-run=client -o yaml | kubectl apply -f -
    (kubectl create secret generic backstage-secrets -n backstage
        --from-literal=POSTGRES_PASSWORD=($backstage_db_password)
        --from-literal=AUTH_SESSION_SECRET=($backstage_session_secret)
        --from-literal=AUTH_OIDC_CLIENT_SECRET=($backstage_oidc_secret)
        --from-literal=GITHUB_TOKEN=""
        --dry-run=client -o yaml | kubectl apply -f -)

    # Backstage kubernetes-ingestor: create SA first, then token secret
    # SA is idempotent — ArgoCD will re-manage it later via platform/base/backstage/rbac.yaml
    print "Creating Backstage ServiceAccount and kubernetes-ingestor token..."
    (kubectl create serviceaccount backstage -n backstage
        --dry-run=client -o yaml | kubectl apply -f -)

    # Create token secret via temp file (reliable Nushell approach)
    let token_secret_file = (mktemp --suffix=".yaml")
    "apiVersion: v1
kind: Secret
metadata:
  name: backstage-k8s-token
  namespace: backstage
  annotations:
    kubernetes.io/service-account.name: backstage
type: kubernetes.io/service-account-token" | save --force $token_secret_file
    kubectl apply -f $token_secret_file
    rm -f $token_secret_file

    # Wait for Token Controller to populate the secret (requires SA to exist first)
    print "Waiting for backstage-k8s-token to be populated by Token Controller..."
    mut token_ready = false
    for _ in 1..30 {
        let result = (do -i {
            kubectl get secret backstage-k8s-token -n backstage -o jsonpath='{.data.token}'
        } | complete)
        if $result.exit_code == 0 and ($result.stdout | str trim) != "" {
            $token_ready = true
            break
        }
        sleep 1sec
    }
    if $token_ready {
        print $"(ansi green)✓ backstage-k8s-token ready(ansi reset)"
    } else {
        print $"(ansi yellow)⚠ backstage-k8s-token not yet populated — Backstage will retry(ansi reset)"
    }

    # Monitoring namespace (Grafana uses Helm values for OAuth secret)
    kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -
    
    # Crossplane namespace
    kubectl create namespace crossplane-system --dry-run=client -o yaml | kubectl apply -f -
    
    # Kyverno namespace
    kubectl create namespace kyverno --dry-run=client -o yaml | kubectl apply -f -
    
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
    } else {
        print $"(ansi yellow)  ! Existing gitea-admin-secret preserved; set GITEA_ADMIN_PASSWORD to rotate(ansi reset)"
    }

    # Code-quality namespace + SonarQube secrets
    kubectl create namespace code-quality --dry-run=client -o yaml | kubectl apply -f -
    (kubectl create secret generic sonarqube-db-secret -n code-quality
        --from-literal=SONAR_JDBC_PASSWORD=($sonarqube_db_password)
        --dry-run=client -o yaml | kubectl apply -f -)
    (kubectl create secret generic sonarqube-monitoring-secret -n code-quality
        --from-literal=SONAR_WEB_SYSTEMPASSCODE=($sonarqube_monitoring_passcode)
        --dry-run=client -o yaml | kubectl apply -f -)

    # Harbor namespace and secrets
    kubectl create namespace harbor --dry-run=client -o yaml | kubectl apply -f -
    (kubectl create secret generic harbor-admin-secret -n harbor
        --from-literal=HARBOR_ADMIN_PASSWORD=($harbor_admin_password)
        --dry-run=client -o yaml | kubectl apply -f -)
    (kubectl create secret generic harbor-secret-key -n harbor
        --from-literal=secretKey=($harbor_secret_key)
        --dry-run=client -o yaml | kubectl apply -f -)
    (kubectl create secret generic harbor-db-secret -n harbor
        --from-literal=password=($harbor_db_password)
        --dry-run=client -o yaml | kubectl apply -f -)
    (kubectl create secret generic harbor-oidc-secret -n harbor
        --from-literal=client-secret=($harbor_oidc_secret)
        --dry-run=client -o yaml | kubectl apply -f -)

    # Messaging namespace (for NATS server + Surveyor)
    kubectl create namespace messaging --dry-run=client -o yaml | kubectl apply -f -

    kubectl create namespace tracing --dry-run=client -o yaml | kubectl apply -f -

    # Jaeger oauth2-proxy secret (Keycloak OIDC client + cookie encryption)
    let jaeger_oidc_secret = ($env.JAEGER_OIDC_CLIENT_SECRET? | default "jaeger-client-secret")
    let jaeger_cookie_secret = ($env.JAEGER_COOKIE_SECRET? | default (generate_password | str substring 0..31 | encode base64))
    (kubectl create secret generic jaeger-oauth2-proxy-secrets -n tracing
        --from-literal=client-secret=($jaeger_oidc_secret)
        --from-literal=cookie-secret=($jaeger_cookie_secret)
        --dry-run=client -o yaml | kubectl apply -f -)

    # cost-monitoring namespace + OpenCost oauth2-proxy secrets
    kubectl create namespace cost-monitoring --dry-run=client -o yaml | kubectl apply -f -
    let opencost_oidc_secret = ($env.OPENCOST_OIDC_CLIENT_SECRET? | default "opencost-client-secret")
    let opencost_cookie_secret = ($env.OPENCOST_COOKIE_SECRET? | default (generate_password | str substring 0..31 | encode base64))
    (kubectl create secret generic opencost-oauth2-proxy-secrets -n cost-monitoring
        --from-literal=client-secret=($opencost_oidc_secret)
        --from-literal=cookie-secret=($opencost_cookie_secret)
        --dry-run=client -o yaml | kubectl apply -f -)

    # OpenSearch secret (admin password for observability backend)
    let opensearch_admin_password = ($env.OPENSEARCH_ADMIN_PASSWORD? | default (generate_password))
    # Secret in platform-db namespace (for OpenSearch itself)
    (kubectl create secret generic opensearch-secrets -n platform-db
        --from-literal=OPENSEARCH_ADMIN_PASSWORD=($opensearch_admin_password)
        --dry-run=client -o yaml | kubectl apply -f -)
    # Secret in tracing namespace (for Jaeger to authenticate against OpenSearch)
    (kubectl create secret generic jaeger-opensearch-credentials -n tracing
        --from-literal=password=($opensearch_admin_password)
        --dry-run=client -o yaml | kubectl apply -f -)
    
    print $"(ansi green)✓ Platform namespaces and secrets created.(ansi reset)"
}

def install_argocd [] {
    kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -

    helm repo add argo https://argoproj.github.io/argo-helm
    helm repo update

    # Issue #275: pin the bootstrap chart explicitly so a clean install is
    # reproducible. argo-cd 10.1.4 ships Argo CD app v3.4.5. Keep in sync with
    # the ARGOCD_CHART_VERSION comment in docs/guides/platform-versions.md and
    # the second (OIDC CA) helm upgrade below.
    (helm upgrade --install argocd argo/argo-cd
        --version 10.1.4
        --namespace argocd
        --create-namespace
        --values platform/base/argocd/values.yaml
        --set 'server.service.type=ClusterIP'
        --set 'configs.params.server\.insecure=true'
        --wait --timeout 10m)

    print $"(ansi green)✓ ArgoCD installed [Helm](ansi reset)"
}

# Build the custom subpath-aware OpenCost UI image and load it into the kind
# cluster (Issue #272 Option B). The opencost ArgoCD app (wave 2) selects this
# image via platform/base/opencost/values.yaml with pullPolicy Never, so it must
# exist in the node before the opencost pod schedules. build.nu is the single
# source of truth for how the image is built (pinned upstream v1.120.4 +
# vite_basename=/opencost). Missing Docker or a failed build aborts bootstrap.
def build_opencost_ui_image [] {
    if (which docker | length) == 0 {
        error make {msg: "Docker is required to build the local OpenCost UI image"}
    }
    try {
        nu platform/base/opencost/ui-image/build.nu
        print $"(ansi green)✓ Custom OpenCost UI image built and loaded.(ansi reset)"
    } catch {|err|
        error make {msg: $"OpenCost UI image build failed: ($err.msg)"}
    }
}

# Build & kind-load the Tier-1 DigiOrg images (Issue #275): the pinned,
# production-optimized Keycloak image and the pinned Fluentd log-forwarder image.
# Both use pullPolicy Never and must exist in the kind node before their pods
# schedule. Fail closed: never fall back to an untrusted public namespace.
def build_tier1_images [] {
    if (which docker | length) == 0 {
        error make {msg: "Docker is required to build the local Keycloak and Fluentd images"}
    }
    for spec in [
        {name: "Keycloak", path: "platform/images/keycloak/build.nu"}
        {name: "Fluentd",  path: "platform/images/fluentd/build.nu"}
    ] {
        try {
            nu $spec.path
            print $"(ansi green)✓ ($spec.name) image built and loaded.(ansi reset)"
        } catch {|err|
            error make {msg: $"($spec.name) image build failed: ($err.msg)"}
        }
    }
}

# -----------------------------------------------------------------------------
# Phase 2: App Deployment Functions
# -----------------------------------------------------------------------------

# Deploy ArgoCD Root App (triggers App-of-Apps)
def deploy_root_app [] {
    $env.KUBECONFIG = $KUBECONFIG_PATH

    print "Deploying ArgoCD Root App..."
    kubectl apply -f platform/base/argocd/applications/root-app.yaml

    print $"(ansi green)✓ Root App deployed - ArgoCD will now sync all platform components(ansi reset)"
    print ""
    print "ArgoCD Sync Waves:"
    print "  Wave -1: root-app (just deployed)"
    print "  Wave  0: cert-manager, cnpg, external-secrets, nats, postgresql"
    print "  Wave  1: cnpg-cluster, keycloak, argocd (self-managed)"
    print "  Wave  2: backstage, gitea, grafana, harbor, jaeger, landingpage, opencost, sonarqube"
    print "  Wave  3: crossplane, kyverno, opensearch"
    print "  Wave  4: crossplane-providers, fluentd, kyverno-policies"
    print "  Wave  5: monitoring-extras (ServiceMonitors)"
    print "  Wave  6: crossplane-provider-configs"
    print "  Wave  7: crossplane-xrds"
    print "  Wave  8: core-catalog"

    # Issue #279: the root Application immediately fans out into many
    # concurrent Git/Helm/Kustomize renders. Wait for argocd-repo-server to be
    # Ready and restart-stable before promoting gated syncs, so the first gated
    # operation doesn't race the initial render burst (confirmed cause of the
    # repo-server liveness restart that severed External Secrets' manifest
    # generation with gRPC Unavailable/EOF).
    print ""
    print $"(ansi cyan_bold)Waiting for argocd-repo-server to stabilize(ansi reset)"
    print "────────────────────────────────────"
    wait_for_repo_server_stable

    # The repository keeps major upgrades manual so a Git merge cannot trigger
    # them concurrently in a shared cluster. This script targets only the named
    # local KinD environment; invoking `main up` is the explicit approval to sync
    # its gated apps sequentially.
    sync_gated_apps_for_local_dev

    # Wait for apps to sync
    print ""
    print $"(ansi cyan_bold)Phase 3: Waiting for ArgoCD Apps(ansi reset)"
    print "────────────────────────────────────"
    wait_for_argocd_apps
}

# Issue #279: wait for argocd-repo-server to be Ready AND to have stopped
# restarting before promoting any gated Application. Bounded and fails closed
# — a repo-server that never stabilizes surfaces as a clear error rather than
# racing the first gated sync against it.
def wait_for_repo_server_stable [] {
    $env.KUBECONFIG = $KUBECONFIG_PATH

    mut previous_snapshot = []
    mut stable_checks = 0
    mut last_diagnostic = "repo-server state was not observed"
    for attempt in 1..60 {
        let pods_result = (do {
            kubectl get pods -n argocd -l app.kubernetes.io/name=argocd-repo-server -o json
        } | complete)
        let deployment_result = (do {
            kubectl get deployment argocd-repo-server -n argocd -o json
        } | complete)

        if $pods_result.exit_code == 0 and $deployment_result.exit_code == 0 {
            let pods = ($pods_result.stdout | from json | get -o items | default [])
            let deployment = ($deployment_result.stdout | from json)
            let desired = ($deployment | get -o spec.replicas | default 0)
            let ready_replicas = ($deployment | get -o status.readyReplicas | default 0)
            let updated_replicas = ($deployment | get -o status.updatedReplicas | default 0)
            let available_replicas = ($deployment | get -o status.availableReplicas | default 0)
            let observed_generation = ($deployment | get -o status.observedGeneration | default 0)
            let generation = ($deployment | get -o metadata.generation | default (-1))

            let pod_snapshot = (
                $pods
                | each {|pod|
                    let statuses = ($pod | get -o status.containerStatuses | default [])
                    let ready = (($statuses | length) > 0) and ($statuses | all {|status| $status.ready })
                    let restarts = if ($statuses | length) == 0 { 0 } else {
                        $statuses | get restartCount | math sum
                    }
                    {
                        name: ($pod | get -o metadata.name | default "")
                        uid: ($pod | get -o metadata.uid | default "")
                        ready: $ready
                        restarts: $restarts
                    }
                }
                | sort-by name
            )
            let all_pods_ready = (($pod_snapshot | length) == $desired) and ($pod_snapshot | all {|pod| $pod.ready })
            let rollout_complete = (
                $desired > 0
                and $ready_replicas == $desired
                and $updated_replicas == $desired
                and $available_replicas == $desired
                and $observed_generation == $generation
            )
            let unchanged = ($pod_snapshot == $previous_snapshot)

            if $all_pods_ready and $rollout_complete and $unchanged {
                $stable_checks = $stable_checks + 1
            } else {
                $stable_checks = 0
            }
            $previous_snapshot = $pod_snapshot
            $last_diagnostic = $"desired=($desired) ready=($ready_replicas) updated=($updated_replicas) available=($available_replicas) pods=($pod_snapshot | length)"
            print $"  repo-server: ($last_diagnostic) stable_checks=($stable_checks)/3"
            if $stable_checks >= 3 {
                let total_restarts = ($pod_snapshot | get restarts | math sum)
                print $"(ansi green)✓ argocd-repo-server rollout is Ready and identity-stable; totalRestarts=($total_restarts)(ansi reset)"
                return
            }
        } else {
            let stderr = ($pods_result.stderr + " " + $deployment_result.stderr | str trim)
            $last_diagnostic = (redact_sync_diagnostic $stderr)
            $stable_checks = 0
            $previous_snapshot = []
        }
        sleep 5sec
    }
    error make {msg: $"argocd-repo-server did not become Ready and identity-stable within the timeout: ($last_diagnostic)"}
}

# Issue #279: classify an Argo CD operationState.message as retryable
# (transient repo-server/network/comparison failures) or fatal (deterministic
# manifest/resource/hook/policy errors, which must fail immediately). Fail
# closed: an unrecognized message is treated as fatal, not retryable.
def is_retryable_sync_error [message: string] {
    if ($message | str trim | is-empty) {
        return false
    }
    let normalized = ($message | str lowercase)

    # Deterministic render/apply/auth/policy errors always win over transport
    # words embedded in their text (for example "unexpected EOF" or a webhook
    # endpoint reporting "connection refused").
    let deterministic_markers = [
        "invalidargument"
        "permissiondenied"
        "unauthenticated"
        "failed to unmarshal"
        "yaml parse error"
        "unexpected eof"
        "helm template failed"
        "admission webhook"
        "failed calling webhook"
        "backofflimitexceeded"
        "is invalid:"
    ]
    for marker in $deterministic_markers {
        if ($normalized | str contains $marker) {
            return false
        }
    }

    # One narrowly identified resource-health race is transient only when no
    # deterministic marker occurs anywhere in the combined diagnostics.
    if ($normalized | str contains "containers with incomplete status:") {
        return true
    }

    # gRPC Unavailable/DeadlineExceeded and an explicit broken server read are
    # transport failures. Generic dial/EOF/timeout words require repository or
    # comparison context below instead of being accepted globally.
    let explicit_transport_markers = [
        "code = unavailable"
        "code = deadlineexceeded"
        "error reading from server: eof"
        "transport is closing"
        "connection reset by peer"
        "client connection lost"
        "broken pipe"
    ]
    for marker in $explicit_transport_markers {
        if ($normalized | str contains $marker) {
            return true
        }
    }

    let repository_context_markers = [
        "repo-server"
        "repository"
        "comparisonerror"
        "manifest generation"
        "failed to list refs"
        "list refs"
        "github.com"
        "gitlab.com"
        "bitbucket.org"
        "helm repository"
    ]
    let generic_transport_markers = [
        "eof"
        "connection refused"
        "server misbehaving"
        "no route to host"
        "i/o timeout"
        "tls handshake timeout"
        "dial tcp"
        "context deadline exceeded"
        "temporary failure in name resolution"
        "no such host"
    ]
    let has_repository_context = ($repository_context_markers | any {|marker| $normalized | str contains $marker })
    let has_transport_failure = ($generic_transport_markers | any {|marker| $normalized | str contains $marker })
    $has_repository_context and $has_transport_failure
}

# Redact likely credentials from controller/admission messages before they are
# printed or included in a terminal error. Diagnostics are untrusted text: a
# failed webhook or tool can echo request headers, URLs with userinfo, or Secret
# values. Classification still uses the original text; only output is redacted.
def redact_sync_diagnostic [message: string] {
    $message
    | str replace --all --regex '(?i)authorization\s*[:=]\s*(bearer|basic)\s+[^\s,;]+' 'Authorization: [REDACTED]'
    | str replace --all --regex '(?i)https?://[^\s/@:]+:[^\s/@]+@' 'https://[REDACTED]@'
    | str replace --all --regex `(?i)["']?(password|token|api[_-]?key|client[_-]?secret|secret)["']?\s*[:=]\s*["'][^"']*["']` '[REDACTED]'
    | str replace --all --regex `(?i)["']?(password|token|api[_-]?key|client[_-]?secret|secret)["']?\s*[:=]\s*[^\s,;}]+` '[REDACTED]'
    | str replace --all --regex '(?i)secret\s+data\s*[:=]\s*[^\s,;]+' 'Secret data: [REDACTED]'
}

# Print operationState.message plus any failed resource/hook messages so a
# gated-sync failure is diagnosable from the script's own output.
def print_sync_diagnostics [state: record] {
    let message = ($state | get -o status.operationState.message | default "")
    if not ($message | is-empty) {
        let safe_message = (redact_sync_diagnostic $message)
        print $"(ansi red)    operationState.message: ($safe_message)(ansi reset)"
    }
    let resources = ($state | get -o status.operationState.syncResult.resources | default [])
    for r in $resources {
        let rstatus = ($r | get -o status | default "")
        let hook_phase = ($r | get -o hookPhase | default "")
        if ($rstatus == "SyncFailed") or ($hook_phase in ["Failed" "Error"]) {
            let kind = ($r | get -o kind | default "")
            let name = ($r | get -o name | default "")
            let rmsg = ($r | get -o message | default "")
            let safe_rmsg = (redact_sync_diagnostic $rmsg)
            print $"(ansi red)    ($kind)/($name): status=($rstatus) hookPhase=($hook_phase) ($safe_rmsg)(ansi reset)"
        }
    }
}

# Explicitly promote gated major upgrades on the disposable local KinD cluster.
# Shared/production clusters must follow docs/guides/platform-versions.md instead.
#
# Issue #279: a gated Application's operation can fail with a transient
# repo-server/comparison error (confirmed: repo-server liveness restart
# severing manifest generation mid-render, surfaced as ComparisonError / gRPC
# Unavailable / EOF). Those are retried with bounded exponential backoff by
# reissuing a genuinely fresh sync operation (`kubectl patch` again, not
# re-reading the stale terminal operation). Deterministic manifest/resource/
# hook/policy errors fail immediately — no retry.
def sync_gated_apps_for_local_dev [] {
    let gated_apps = [
        "external-secrets", "nats", "grafana", "opencost", "gitea",
        "sonarqube", "crossplane", "crossplane-providers",
        "crossplane-provider-configs", "crossplane-xrds", "core-catalog"
    ]
    let sync_payload = '{"operation":{"sync":{"prune":true,"syncOptions":["CreateNamespace=true","ServerSideApply=true"]}}}'
    let max_operation_retries = 3

    print ""
    print $"(ansi cyan_bold)Promoting gated upgrades sequentially on local KinD(ansi reset)"
    for app in $gated_apps {
        mut exists = false
        for attempt in 1..60 {
            let get_result = (do {
                kubectl get application $app -n argocd -o name
            } | complete)
            if $get_result.exit_code == 0 {
                $exists = true
                break
            }
            sleep 2sec
        }
        if not $exists {
            error make {msg: $"ArgoCD Application ($app) was not created by the root app"}
        }

        mut retry_count = 0
        loop {
            let previous_state = (kubectl get application $app -n argocd -o json | from json)
            let previous_started = ($previous_state | get -o status.operationState.startedAt | default "")
            let previous_finished = ($previous_state | get -o status.operationState.finishedAt | default "")
            print $"  Syncing gated Application: ($app) \(attempt ($retry_count + 1)/($max_operation_retries + 1)\)"
            let sync_result = (do {
                kubectl patch application $app -n argocd --type merge -p $sync_payload
            } | complete)
            if $sync_result.exit_code != 0 {
                error make {msg: $"Could not start sync for ($app): ($sync_result.stderr | str trim)"}
            }

            mut completed = false
            mut saw_new_operation = false
            mut succeeded = false
            mut last_state = {}
            for attempt in 1..90 {
                let state_result = (do {
                    kubectl get application $app -n argocd -o json
                } | complete)
                if $state_result.exit_code == 0 {
                    let state = ($state_result.stdout | from json)
                    $last_state = $state
                    let phase = ($state | get -o status.operationState.phase | default "")
                    let started = ($state | get -o status.operationState.startedAt | default "")
                    let finished = ($state | get -o status.operationState.finishedAt | default "")
                    let sync = ($state | get -o status.sync.status | default "")
                    let health = ($state | get -o status.health.status | default "")
                    if not ($started | is-empty) and $started != $previous_started {
                        $saw_new_operation = true
                    }
                    if $saw_new_operation and $phase in ["Failed" "Error"] {
                        $completed = true
                        break
                    }
                    if $saw_new_operation and $phase == "Succeeded" and $sync == "Synced" and $health == "Healthy" {
                        $completed = true
                        $succeeded = true
                        break
                    }
                }
                sleep 10sec
            }
            if not $completed {
                if not ($last_state | is-empty) {
                    print_sync_diagnostics $last_state
                }
                let observed_started = ($last_state | get -o status.operationState.startedAt | default "")
                let observed_finished = ($last_state | get -o status.operationState.finishedAt | default "")
                error make {msg: $"Gated Application ($app) did not complete a fresh successful Synced+Healthy operation within 15 minutes; previousStarted=($previous_started) previousFinished=($previous_finished) observedStarted=($observed_started) observedFinished=($observed_finished)"}
            }

            if $succeeded {
                print $"(ansi green)  ✓ ($app) sync Succeeded, Synced and Healthy(ansi reset)"
                break
            }

            # Operation reached Failed/Error: surface full diagnostics before
            # deciding whether this is retryable.
            let message = ($last_state | get -o status.operationState.message | default "")
            let resource_messages = (
                $last_state
                | get -o status.operationState.syncResult.resources
                | default []
                | each {|resource| $resource | get -o message | default "" }
                | where {|resource_message| not ($resource_message | is-empty) }
            )
            let classification_text = ([$message] | append $resource_messages | str join "\n")
            print $"(ansi red)  ✗ ($app) sync failed(ansi reset)"
            print_sync_diagnostics $last_state

            if (is_retryable_sync_error $classification_text) and $retry_count < $max_operation_retries {
                let backoff = (10 * (2 ** $retry_count)) * 1sec
                print $"(ansi yellow)  Transient error detected; retrying ($app) in ($backoff) with a fresh sync operation...(ansi reset)"
                sleep $backoff
                $retry_count = $retry_count + 1
            } else {
                let safe_message = (redact_sync_diagnostic $message)
                error make {msg: $"Gated Application ($app) sync failed with phase Error/Failed - not retryable or retries exhausted: ($safe_message)"}
            }
        }
    }
}

# Argo CD 3.x can retain stale per-resource OutOfSync status after a successful
# Helm sync even when its own diff engine reports no material difference (seen
# with API-defaulted CRD fields). Use the CLI only as a fail-closed secondary
# check: exit 0 means no diff; any missing CLI, setup error, or non-zero exit
# remains not ready. Diagnostics are captured and never printed.
def argocd_app_has_no_material_diff [app: string] {
    if (which argocd | is-empty) {
        return false
    }

    let temp_kubeconfig = (mktemp --tmpdir argocd-core-kubeconfig.XXXXXX | str trim)
    try {
        cp $KUBECONFIG_PATH $temp_kubeconfig
        let context = (do {
            kubectl --kubeconfig $temp_kubeconfig config set-context --current --namespace=argocd
        } | complete)
        if $context.exit_code != 0 {
            return false
        }

        let diff = (with-env {KUBECONFIG: $temp_kubeconfig} {
            do { argocd app diff $app --core --refresh } | complete
        })
        $diff.exit_code == 0
    } catch {
        false
    } finally {
        rm -f $temp_kubeconfig
    }
}

# Wait for ArgoCD apps to become healthy
def wait_for_argocd_apps [] {
    $env.KUBECONFIG = $KUBECONFIG_PATH
    
    print "Waiting for ArgoCD applications to sync (this may take 10-20 minutes)..."
    print ""
    
    # Apps to wait for (in wave order) — must match apps/platform/*.yaml exactly
    let apps = [
        # Wave 0
        "cert-manager", "cnpg", "external-secrets", "nats", "postgresql",
        # Wave 1
        "cnpg-cluster", "keycloak", "argocd",
        # Wave 2
        "backstage", "gitea", "grafana", "harbor", "jaeger", "landingpage", "opencost", "sonarqube",
        # Wave 3
        "crossplane", "kyverno", "opensearch",
        # Wave 4
        "crossplane-providers", "fluentd", "kyverno-policies",
        # Wave 5
        "monitoring-extras",
        # Wave 6
        "crossplane-provider-configs",
        # Wave 7
        "crossplane-xrds",
        # Wave 8
        "core-catalog"
    ]
    
    mut all_healthy = false
    mut attempts = 0
    let max_attempts = 150  # 25 minutes with 10sec intervals — platform has grown
    
    loop {
        $attempts = $attempts + 1
        if $attempts > $max_attempts {
            error make {msg: "Timeout waiting for every required ArgoCD Application to become Synced and Healthy"}
        }
        
        mut ready_count = 0
        
        for app in $apps {
            let status = (do {
                kubectl get application $app -n argocd -o json
            } | complete)
            if $status.exit_code == 0 {
                let state = ($status.stdout | from json)
                let health = ($state | get -o status.health.status | default "")
                let sync = ($state | get -o status.sync.status | default "")
                if $health == "Healthy" and $sync == "Synced" {
                    $ready_count = $ready_count + 1
                } else if $health == "Healthy" and $sync == "OutOfSync" and (argocd_app_has_no_material_diff $app) {
                    # Count only a successful, fresh Argo core diff with zero
                    # material changes; every tool/error path remains closed.
                    print $"  ($app): stale OutOfSync status, but Argo reports no material diff"
                    $ready_count = $ready_count + 1
                }
            }
        }

        print $"  Apps Synced+Healthy: ($ready_count)/($apps | length) [attempt ($attempts)/($max_attempts)]"

        if $ready_count == ($apps | length) {
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

# -----------------------------------------------------------------------------
# Phase 3: Configure Apps Functions
# -----------------------------------------------------------------------------

# Configure Gitea (register self-signed CA cert + add Keycloak OIDC provider + create initial users/org)
def configure_gitea [] {
    $env.KUBECONFIG = $KUBECONFIG_PATH

    # --- Step 1: Register self-signed CA cert in Gitea container ---
    print "1. Registering self-signed CA cert in Gitea..."

    # Wait for Gitea pod to be ready
    mut gitea_ready = false
    for attempt in 1..30 {
        let pod_result = (do { kubectl --kubeconfig $KUBECONFIG_PATH get pods -n gitea -l app.kubernetes.io/name=gitea -o jsonpath='{.items[0].metadata.name}' } | complete)
        if $pod_result.exit_code == 0 and ($pod_result.stdout | str trim | is-not-empty) {
            let ready_result = (do { kubectl --kubeconfig $KUBECONFIG_PATH wait --for=condition=ready pod -n gitea -l app.kubernetes.io/name=gitea --timeout=10s } | complete)
            if $ready_result.exit_code == 0 {
                $gitea_ready = true
                break
            }
        }
        print $"Waiting for Gitea pod... [attempt ($attempt)/30]"
        sleep 10sec
    }
    if not $gitea_ready {
        print $"(ansi yellow)Warning: Gitea pod not ready, skipping OIDC configuration(ansi reset)"
        return
    }
    let gitea_pod = (kubectl --kubeconfig $KUBECONFIG_PATH get pods -n gitea -l app.kubernetes.io/name=gitea -o jsonpath='{.items[0].metadata.name}' | str trim)

    # Extract CA cert from cert-manager secret
    # Copy CA cert into Gitea container and update trust store
    kubectl --kubeconfig $KUBECONFIG_PATH cp digiorg-local-ca.crt -c gitea gitea/($gitea_pod):/usr/local/share/ca-certificates/digiorg-local-ca.crt | complete
    kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- update-ca-certificates | complete
    print $"(ansi green)✓ CA cert registered in Gitea trust store(ansi reset)"

    # --- Step 2: Add Keycloak as OIDC authentication source ---
    print "2. Configuring Keycloak OIDC provider in Gitea..."
    
    # Check if Keycloak OIDC source already exists (idempotency)
    let existing_oauth = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- su git gitea admin auth list --vertical
    } | complete)

    mut oidc_exists = false
    if $existing_oauth.exit_code == 0 {
        if ($existing_oauth.stdout | str contains "Keycloak") {
            $oidc_exists = true
        }
    }

    if not $oidc_exists {
        # Add OIDC provider via gitea CLI (most reliable method inside the container)
        let add_result = (do {
            kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- su git -c 'gitea admin auth add-oauth --name "Keycloak" --provider openidConnect --key gitea --secret gitea-client-secret --auto-discover-url "https://digiorg.local/keycloak/realms/digiorg-core-platform/.well-known/openid-configuration"'
        } | complete)
        if $add_result.exit_code == 0 {
            print $"(ansi green)✓ Keycloak OIDC provider added to Gitea(ansi reset)"
        } else {
            print $"(ansi red)✗ Failed to add Keycloak OIDC provider: ($add_result.stderr)(ansi reset)"
            return
        }
    } else {
        print $"(ansi yellow)✓ Keycloak OIDC provider already exists in Gitea(ansi reset)"
    }

    # --- Step 3: Create initial users in Gitea ---
    print "3. Creating initial users in Gitea..."

    kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- su git -c 'gitea admin user create --username "digiorgadmin" --email "admin@digiorg.local" --password "digiorgadmin" --must-change-password false --admin true'
    kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- su git -c 'gitea admin user create --username "digiorgdeveloper" --email "developer@digiorg.local" --password "digiorgdeveloper" --must-change-password false --admin false'
    print $"(ansi green)✓ Initial users created(ansi reset)"

    # --- Step 4: Create DigiOrg organisation via tea CLI ---
    print "4. Creating DigiOrg organisation in Gitea..."

    # 4a: Install tea CLI if not already present
    let tea_check = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- sh -c 'test -x /usr/local/bin/tea && echo "exists"'
    } | complete)

    if ($tea_check.exit_code == 0) and ($tea_check.stdout | str contains "exists") {
        print $"(ansi yellow)✓ tea CLI already installed(ansi reset)"
    } else {
        let tea_install = (do {
            kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- sh -c 'wget -qO /usr/local/bin/tea https://dl.gitea.com/tea/0.9.2/tea-0.9.2-linux-amd64 && chmod +x /usr/local/bin/tea'
        } | complete)
        if $tea_install.exit_code == 0 {
            print $"(ansi green)✓ tea CLI installed(ansi reset)"
        } else {
            print $"(ansi red)✗ Failed to install tea CLI: ($tea_install.stderr)(ansi reset)"
            return
        }
    }

    # 4b: Check if tea login already exists (idempotency check first)
    let login_check = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- sh -c 'tea login list 2>/dev/null | grep -q "teaadmin-digiorg" && echo "exists"'
    } | complete)

    # 4b/4c/4d: Resolve gitea_token — immutable, derived from both branches
    # Nushell does not allow capturing mut variables in closures; use let + if expression instead.
    let gitea_token = if ($login_check.exit_code == 0) and ($login_check.stdout | str contains "exists") {
        print $"(ansi yellow)✓ tea login 'teaadmin-digiorg' already configured(ansi reset)"
        # Extract stored token from tea config for subsequent API calls
        let token_extract = (do {
            kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- sh -c 'grep -A10 "teaadmin-digiorg" /root/.config/tea/config.yml | grep "token:" | head -1 | sed "s/.*token: //"'
        } | complete)
        $token_extract.stdout | str trim
    } else {
        # 4c: Generate access token — must run as git user, not root
        let token_result = (do {
            kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- su git -c 'gitea admin user generate-access-token --username gitea_admin --token-name teaadmin --scopes write:activitypub,write:admin,write:issue,write:misc,write:notification,write:organization,write:package,write:repository,write:user --raw'
        } | complete)
        if $token_result.exit_code != 0 {
            print $"(ansi red)✗ Failed to generate access token: ($token_result.stderr)(ansi reset)"
            return
        }
        let token = ($token_result.stdout | str trim)
        print $"(ansi green)✓ Access token generated(ansi reset)"

        # 4d: Set up tea login (token-based, using immutable $token — capturable in closures)
        let login_add = (do {
            kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- sh -c $"tea login add --name=teaadmin-digiorg --url=https://digiorg.local/gitea --token=($token)"
        } | complete)
        if $login_add.exit_code == 0 {
            print $"(ansi green)✓ tea login 'teaadmin-digiorg' configured(ansi reset)"
        } else {
            print $"(ansi red)✗ Failed to configure tea login: ($login_add.stderr)(ansi reset)"
            return
        }
        $token
    }

    # 4e: Create DigiOrg organisation (idempotent)
    let org_check = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- sh -c 'tea organizations list --login teaadmin-digiorg 2>/dev/null | grep -q "DigiOrg" && echo "exists"'
    } | complete)

    if ($org_check.exit_code == 0) and ($org_check.stdout | str contains "exists") {
        print $"(ansi yellow)✓ Organisation 'DigiOrg' already exists(ansi reset)"
    } else {
        let org_create = (do {
            kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- sh -c 'tea organization create --description "DigiOrg Organization" --visibility public --repo-admins-can-change-team-access --login teaadmin-digiorg DigiOrg'
        } | complete)
        if $org_create.exit_code == 0 {
            print $"(ansi green)✓ Organisation 'DigiOrg' created(ansi reset)"
        } else {
            print $"(ansi red)✗ Failed to create organisation 'DigiOrg': ($org_create.stderr)(ansi reset)"
            return
        }
    }

    # 4f: Get Owners team ID via Gitea API
    let teams_result = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- sh -c $"curl -sk -H 'Authorization: token ($gitea_token)' https://digiorg.local/gitea/api/v1/orgs/DigiOrg/teams"
    } | complete)
    if $teams_result.exit_code != 0 {
        print $"(ansi red)✗ Failed to retrieve DigiOrg teams: ($teams_result.stderr)(ansi reset)"
        return
    }
    let owners_team = ($teams_result.stdout | from json | where name == "Owners")
    if ($owners_team | is-empty) {
        print $"(ansi red)✗ Could not find Owners team in DigiOrg(ansi reset)"
        return
    }
    let owners_team_id = ($owners_team | get id | first)
    print $"(ansi green)✓ DigiOrg Owners team ID: ($owners_team_id)(ansi reset)"

    # 4g: Add digiorgadmin to Owners team (idempotent)
    let admin_check = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- sh -c $"curl -sk -o /dev/null -w '%{http_code}' -H 'Authorization: token ($gitea_token)' https://digiorg.local/gitea/api/v1/teams/($owners_team_id)/members/digiorgadmin"
    } | complete)

    if ($admin_check.exit_code == 0) and (($admin_check.stdout | str trim) == "204") {
        print $"(ansi yellow)✓ 'digiorgadmin' already member of Owners team(ansi reset)"
    } else {
        let admin_add = (do {
            kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- sh -c $"curl -sk -X PUT -H 'Authorization: token ($gitea_token)' https://digiorg.local/gitea/api/v1/teams/($owners_team_id)/members/digiorgadmin"
        } | complete)
        if $admin_add.exit_code == 0 {
            print $"(ansi green)✓ 'digiorgadmin' added to Owners team(ansi reset)"
        } else {
            print $"(ansi red)✗ Failed to add 'digiorgadmin' to Owners team: ($admin_add.stderr)(ansi reset)"
            return
        }
    }

    # 4h: Add digiorgdeveloper to Owners team (idempotent)
    let dev_check = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- sh -c $"curl -sk -o /dev/null -w '%{http_code}' -H 'Authorization: token ($gitea_token)' https://digiorg.local/gitea/api/v1/teams/($owners_team_id)/members/digiorgdeveloper"
    } | complete)

    if ($dev_check.exit_code == 0) and (($dev_check.stdout | str trim) == "204") {
        print $"(ansi yellow)✓ 'digiorgdeveloper' already member of Owners team(ansi reset)"
    } else {
        let dev_add = (do {
            kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- sh -c $"curl -sk -X PUT -H 'Authorization: token ($gitea_token)' https://digiorg.local/gitea/api/v1/teams/($owners_team_id)/members/digiorgdeveloper"
        } | complete)
        if $dev_add.exit_code == 0 {
            print $"(ansi green)✓ 'digiorgdeveloper' added to Owners team(ansi reset)"
        } else {
            print $"(ansi red)✗ Failed to add 'digiorgdeveloper' to Owners team: ($dev_add.stderr)(ansi reset)"
            return
        }
    }

    # 4i: Verification — list Owners team members
    let verify = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- sh -c $"curl -sk -H 'Authorization: token ($gitea_token)' https://digiorg.local/gitea/api/v1/teams/($owners_team_id)/members"
    } | complete)
    if $verify.exit_code == 0 {
        let members = ($verify.stdout | from json | get login | str join ", ")
        print $"(ansi green)✓ DigiOrg Owners team members: ($members)(ansi reset)"
    }

    print $"(ansi green)✓ Gitea OIDC integration configured(ansi reset)"
}

# Configure SonarQube
def configure_sonarqube [] {
    let sonar_url = "https://digiorg.local/sonarqube"
    let keycloak_saml_descriptor_url = "https://digiorg.local/keycloak/realms/digiorg-core-platform/protocol/saml/descriptor"
    let admin_pass = (do -i { kubectl --kubeconfig $KUBECONFIG_PATH get secret sonarqube-admin-secret -n code-quality -o jsonpath='{.data.password}' } | complete)

    let password = if $admin_pass.exit_code == 0 {
        $admin_pass.stdout | str trim | decode base64
    } else {
        ($env.SONARQUBE_ADMIN_PASSWORD? | default "admin")
    }

    # Wait for SonarQube to be ready (up to 5 min)
    mut sonar_ready = false
    for attempt in 1..30 {
        let status = (do -i {
            curl --noproxy "*" -sk -u $"admin:($password)" $"($sonar_url)/api/system/status"
        } | complete)
        if $status.exit_code == 0 and ($status.stdout | str contains '"status":"UP"') {
            $sonar_ready = true
            break
        }
        print $"Waiting for SonarQube... [attempt ($attempt)/30]"
        sleep 10sec
    }

    if not $sonar_ready {
        print $"(ansi yellow)Warning: SonarQube not ready, skipping Server Base URL configuration(ansi reset)"
        return
    }

    # Set sonar.core.serverBaseURL via Settings API
    let result = (do -i { curl --noproxy "*" -sk -u $"admin:($password)" -X POST $"($sonar_url)/api/settings/set" --data-urlencode "key=sonar.core.serverBaseURL" --data-urlencode $"value=($sonar_url)" } | complete)

    if $result.exit_code == 0 {
        print $"(ansi green)✓ SonarQube Server Base URL set to ($sonar_url)(ansi reset)"
    } else {
        print $"(ansi red)✗ Failed to set Server Base URL: ($result.stderr)(ansi reset)"
    }

    # --- Step 1: Fetch Keycloak IdP X.509 certificate from SAML descriptor ---
    # The SAML metadata descriptor endpoint returns the full X.509 certificate
    # (not just the raw public key), which is what SonarQube requires.
    print "  1. Fetching Keycloak IdP X.509 certificate from SAML descriptor..."
    let cert_result = (do -i { curl --noproxy "*" -sk $keycloak_saml_descriptor_url } | complete)
    if $cert_result.exit_code != 0 {
        print $"(ansi red)✗ Failed to reach Keycloak SAML descriptor endpoint: ($cert_result.stderr)(ansi reset)"
        return
    }
    let keycloak_cert = ($cert_result.stdout | parse --regex '(?s)<ds:X509Certificate>(.*?)</ds:X509Certificate>' | get capture0 | first | str trim)
    if ($keycloak_cert | is-empty) {
        print $"(ansi red)✗ Could not extract X509Certificate from SAML descriptor(ansi reset)"
        return
    }
    print $"(ansi green)✓ Keycloak IdP X.509 certificate fetched(ansi reset)"

    # --- Step 3: Push all SAML settings via Settings API ---
    print "3. Pushing SAML settings to SonarQube API..."
    let saml_settings = [
        [key value];
        ["sonar.auth.saml.applicationId"     "sonarqube"]
        ["sonar.auth.saml.providerName"       "Keycloak"]
        ["sonar.auth.saml.providerId"         "https://digiorg.local/keycloak/realms/digiorg-core-platform"]
        ["sonar.auth.saml.loginUrl"           "https://digiorg.local/keycloak/realms/digiorg-core-platform/protocol/saml"]
        ["sonar.auth.saml.user.login"         "login"]
        ["sonar.auth.saml.user.name"          "name"]
        ["sonar.auth.saml.user.email"         "email"]
        ["sonar.auth.saml.group.name"         "groups"]
        ["sonar.auth.saml.allowUsersToSignUp" "true"]
        ["sonar.auth.saml.certificate.secured" $keycloak_cert]
    ]

    mut all_ok = true
    for setting in $saml_settings {
        let r = (do -i { curl --noproxy "*" -sk -u $"admin:($password)" -X POST $"($sonar_url)/api/settings/set" --data-urlencode $"key=($setting.key)" --data-urlencode $"value=($setting.value)" } | complete)
        if $r.exit_code != 0 {
            print $"(ansi red)✗ Failed to set ($setting.key): ($r.stderr)(ansi reset)"
            $all_ok = false
        }
    }

    # --- Step 4: Enable SAML ---
    let enable_result = (do -i { curl --noproxy "*" -sk -u $"admin:($password)" -X POST $"($sonar_url)/api/settings/set" --data-urlencode "key=sonar.auth.saml.enabled" --data-urlencode "value=true" } | complete)
    if $enable_result.exit_code != 0 {
        print $"(ansi red)✗ Failed to enable SAML: ($enable_result.stderr)(ansi reset)"
        $all_ok = false
    }

    if $all_ok {
        print $"(ansi green)✓ SAML fully configured and enabled in SonarQube(ansi reset)"
    } else {
        print $"(ansi yellow)Warning: Some SAML settings may not have been applied(ansi reset)"
    }
}

# Restart pods that depend on OIDC/Keycloak
def restart_oidc_dependent_pods [] {
    $env.KUBECONFIG = $KUBECONFIG_PATH
    print "Restarting OIDC-dependent pods to refresh DNS/config..."
    
    
    # ArgoCD Server
    try {
        kubectl rollout restart deployment argocd-server -n argocd
        kubectl rollout status deployment argocd-server -n argocd --timeout=120s
        print $"(ansi green)✓ ArgoCD Server restarted(ansi reset)"
    } catch {
        print $"(ansi yellow)Warning: Could not restart ArgoCD Server(ansi reset)"
    }
    
    # Grafana
    try {
        let grafana_exists = (do { kubectl get deployment prometheus-grafana -n monitoring } | complete)
        if $grafana_exists.exit_code == 0 {
            kubectl rollout restart deployment prometheus-grafana -n monitoring
            kubectl rollout status deployment prometheus-grafana -n monitoring --timeout=120s
            print $"(ansi green)✓ Grafana restarted(ansi reset)"
        }
    } catch { }
    
    # Backstage
    try {
        let backstage_exists = (do { kubectl get deployment backstage -n backstage } | complete)
        if $backstage_exists.exit_code == 0 {
            kubectl rollout restart deployment backstage -n backstage
            kubectl rollout status deployment backstage -n backstage --timeout=180s
            print $"(ansi green)✓ Backstage restarted(ansi reset)"
        }
    } catch { }

    # Landing Page
    try {
        let lp_exists = (do { kubectl get deployment landingpage -n platform-apps } | complete)
        if $lp_exists.exit_code == 0 {
            kubectl rollout restart deployment landingpage -n platform-apps
            print $"(ansi green)✓ Landing Page restarted(ansi reset)"
        }
    } catch { }
    
    print $"(ansi green)✓ OIDC-dependent pods restarted(ansi reset)"
}

# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

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
        print $"  Waiting for CA cert... attempt ($attempts)/30"
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
        --version 10.1.4
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

# Check if prequisite tools (kind, kubectl, helm) are installed before proceeding
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

# Check if cluster exists
def cluster_exists [] {
    let result = (do { kind get clusters } | complete)
    if $result.exit_code == 0 {
        $CLUSTER_NAME in ($result.stdout | str trim | lines)
    } else {
        false
    }
}

# Generate a random password (alphanumeric, 24 chars)
def generate_password [] {
    random chars --length 24
}
