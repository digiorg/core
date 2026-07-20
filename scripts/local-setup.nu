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
    
    # Configure Gitea only after its direct Applications and certificate resources
    # are converged. This gate is independent of unrelated late-wave Apps and is
    # safe to re-enter on an interrupted `up` run.
    print ""
    print $"(ansi cyan_bold)Phase 3: Configure Gitea(ansi reset)"
    print "────────────────────────────────────"
    wait_for_configuration_dependencies "Gitea" ["gitea" "keycloak" "cert-manager"] [
        {namespace: "cert-manager", name: "digiorg-local-ca"}
        {namespace: "ingress-nginx", name: "digiorg-local-tls"}
    ]
    configure_gitea

    # Configure SonarQube only after its direct Applications and the same local
    # trust chain are ready; a CNPG or other unrelated drift cannot skip it.
    print ""
    print $"(ansi cyan_bold)Phase 4: Configure SonarQube(ansi reset)"
    print "────────────────────────────────────"
    wait_for_configuration_dependencies "SonarQube" ["sonarqube" "keycloak" "cert-manager"] [
        {namespace: "cert-manager", name: "digiorg-local-ca"}
        {namespace: "ingress-nginx", name: "digiorg-local-tls"}
    ]
    configure_sonarqube

    # Restart OIDC-dependent pods after Keycloak is ready
    print ""
    print $"(ansi cyan_bold)Phase 5: Restart OIDC dependent PODs(ansi reset)"
    print "────────────────────────────────────"
    restart_oidc_dependent_pods

    # Issue #281: run the strict all-Application convergence gate LAST, so a
    # genuine unresolved Application failure still blocks the final success
    # banner (fail-closed), but unrelated late-wave drift no longer skips the
    # identity configuration phases above. The dependency-aware, idempotent
    # configuration phases have already run and are safe to resume on a rerun.
    print ""
    print $"(ansi cyan_bold)Phase 6: Final Platform Convergence Gate(ansi reset)"
    print "────────────────────────────────────"
    wait_for_argocd_apps

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

def helm_force_conflicts_args_for_version [version: string] {
    let parsed = ($version | str trim | parse --regex '^v(?P<major>[0-9]+)\.[0-9]+\.[0-9]+(?:[+-].*)?$')
    if ($parsed | length) != 1 {
        error make {msg: $"Could not parse Helm version: ($version | str trim)"}
    }
    let major = ($parsed | get major | first | into int)
    if $major >= 4 { ["--force-conflicts"] } else { [] }
}

def helm_force_conflicts_args [] {
    let version = (do { helm version --short } | complete)
    if $version.exit_code != 0 {
        error make {msg: "Failed to determine the Helm version"}
    }
    helm_force_conflicts_args_for_version $version.stdout
}

# Helm 4 uses server-side apply and needs explicit conflict ownership when the
# self-managed Argo CD Application has touched Helm-owned fields. Helm 3 does
# not support that flag, so compute a version-aware optional argument list.
def install_argocd [] {
    kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -

    helm repo add argo https://argoproj.github.io/argo-helm
    helm repo update

    # Issue #275: pin the bootstrap chart explicitly so a clean install is
    # reproducible. argo-cd 10.1.4 ships Argo CD app v3.4.5. Keep in sync with
    # the ARGOCD_CHART_VERSION comment in docs/guides/platform-versions.md and
    # the second (OIDC CA) helm upgrade below.
    let helm_conflict_args = (helm_force_conflicts_args)
    (helm upgrade --install argocd argo/argo-cd
        --version 10.1.4
        --namespace argocd
        --create-namespace
        --values platform/base/argocd/values.yaml
        --set 'server.service.type=ClusterIP'
        --set 'configs.params.server\.insecure=true'
        ...$helm_conflict_args
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
    # Issue #281: the CNPG Cluster (cnpg-cluster, wave 1) races the CNPG operator
    # admission webhook on a clean bootstrap — the Cluster apply hit
    # `connection refused` before the webhook endpoint accepted connections, and
    # the resulting SyncFailed operation then stayed Running forever behind an
    # idle Pending backup PVC, so Argo's retry never recovered. Gate the Cluster
    # apply on operator Deployment availability + a ready webhook endpoint first.
    promote_cnpg_cluster

    sync_gated_apps_for_local_dev

    # Issue #281: the ArgoCD OIDC CA patch is independent of the global
    # convergence gate (which now runs LAST, in `main up`, after identity
    # configuration). Embedding the CA here keeps ArgoCD's own OIDC working
    # without coupling it to full-platform convergence.
    patch_argocd_oidc_ca
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
    let redacted = ($message
        | str replace --all --regex '(?i)authorization\s*[:=]\s*(bearer|basic)\s+[^\s,;]+' 'Authorization: [REDACTED]'
        | str replace --all --regex '(?i)https?://[^\s/@:]+:[^\s/@]+@' 'https://[REDACTED]@'
        | str replace --all --regex `(?i)["']?(password|token|api[_-]?key|client[_-]?secret|secret)["']?\s*[:=]\s*["'][^"']*["']` '[REDACTED]'
        | str replace --all --regex `(?i)["']?(password|token|api[_-]?key|client[_-]?secret|secret)["']?\s*[:=]\s*[^\s,;}]+` '[REDACTED]'
        | str replace --all --regex '(?i)secret\s+data\s*[:=]\s*[^\s,;]+' 'Secret data: [REDACTED]')
    # Controller/admission messages are untrusted and may be arbitrarily large.
    # Bound every caller to a single 1 KiB diagnostic after redaction.
    $redacted | str substring 0..1023
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

# Issue #281: is the CNPG operator Deployment Available? Accepts either a single
# Deployment or a (label-selected) DeploymentList JSON. Fail closed: unparseable
# input or no Available replica yields false.
def cnpg_operator_available [deployment_json: string] {
    let parsed = (try { $deployment_json | from json } catch { null })
    # `from json` accepts a bare scalar as a JSON string, so guard on the type:
    # only a record is a real Deployment/List object. Fail closed otherwise.
    if ($parsed | describe | str starts-with "record") == false {
        return false
    }
    let deployments = if ($parsed | get -o kind | default "") == "List" or ($parsed | get -o kind | default "") == "DeploymentList" {
        $parsed | get -o items | default []
    } else {
        [$parsed]
    }
    $deployments | any {|dep|
        let desired = ($dep | get -o spec.replicas | default 0)
        let available = ($dep | get -o status.availableReplicas | default 0)
        ($desired > 0) and ($available >= $desired)
    }
}

# Issue #281: does the CNPG admission webhook have a ready, addressed endpoint?
# Parses a discovery.k8s.io/v1 EndpointSliceList (NOT the deprecated core
# Endpoints API). Ready iff some slice has an endpoint with conditions.ready ==
# true AND at least one address. Fail closed on unparseable input.
def cnpg_webhook_endpoint_ready [endpointslices_json: string] {
    let parsed = (try { $endpointslices_json | from json } catch { null })
    # `from json` accepts a bare scalar as a JSON string, so guard on the type:
    # only a record is a real EndpointSliceList object. Fail closed otherwise.
    if ($parsed | describe | str starts-with "record") == false {
        return false
    }
    let slices = ($parsed | get -o items | default [])
    $slices | any {|slice|
        ($slice | get -o endpoints | default []) | any {|ep|
            let ready = ($ep | get -o conditions.ready | default false)
            let addresses = ($ep | get -o addresses | default [])
            $ready and (($addresses | length) > 0)
        }
    }
}

# Issue #281: block the CNPG Cluster apply until the operator Deployment is
# Available AND its admission webhook endpoint is serving. On a clean bootstrap
# the Cluster otherwise raced the webhook and hit `connection refused`, leaving a
# non-terminal Running operation that Argo never retried. Bounded and fails
# closed with a redacted diagnostic.
def wait_for_cnpg_webhook_ready [] {
    $env.KUBECONFIG = $KUBECONFIG_PATH

    let operator_ns = "cnpg-system"
    let webhook_service = "cnpg-webhook-service"
    mut last_diagnostic = "cnpg operator/webhook readiness was not observed"
    for attempt in 1..60 {
        let dep_result = (do {
            kubectl get deployment -n $operator_ns -l app.kubernetes.io/name=cloudnative-pg -o json
        } | complete)
        # discovery.k8s.io/v1 EndpointSlices for the webhook Service — the core
        # Endpoints API is deprecated and must not be used here.
        let slice_result = (do {
            kubectl get endpointslices.discovery.k8s.io -n $operator_ns -l $"kubernetes.io/service-name=($webhook_service)" -o json
        } | complete)

        if $dep_result.exit_code == 0 and $slice_result.exit_code == 0 {
            let operator_ok = (cnpg_operator_available $dep_result.stdout)
            let webhook_ok = (cnpg_webhook_endpoint_ready $slice_result.stdout)
            $last_diagnostic = $"operatorAvailable=($operator_ok) webhookEndpointReady=($webhook_ok)"
            print $"  cnpg readiness: ($last_diagnostic) [attempt ($attempt)/60]"
            if $operator_ok and $webhook_ok {
                print $"(ansi green)✓ CNPG operator is Available and the webhook endpoint is ready(ansi reset)"
                return
            }
        } else {
            $last_diagnostic = (redact_sync_diagnostic (($dep_result.stderr + " " + $slice_result.stderr) | str trim))
        }
        sleep 5sec
    }
    error make {msg: $"CNPG operator Deployment/webhook endpoint did not become ready before the CNPG Cluster apply: ($last_diagnostic)"}
}

# Issue #281: promote the script-driven `cnpg-cluster` Application only after the
# operator webhook is ready, then sync it with a genuinely fresh operation (new
# startedAt — never re-read a stale terminal operation). Rerunning is safe: an
# already Synced/Healthy Cluster reconciles as a no-op. Fails closed.
def promote_cnpg_cluster [poll_interval: duration = 10sec] {
    $env.KUBECONFIG = $KUBECONFIG_PATH

    print ""
    print $"(ansi cyan_bold)Promoting CNPG Cluster after operator/webhook readiness(ansi reset)"

    mut exists = false
    for attempt in 1..60 {
        let get_result = (do { kubectl get application cnpg-cluster -n argocd -o name } | complete)
        if $get_result.exit_code == 0 {
            $exists = true
            break
        }
        sleep 2sec
    }
    if not $exists {
        error make {msg: "ArgoCD Application cnpg-cluster was not created by the root app"}
    }

    # Order the Cluster apply strictly AFTER the operator webhook is serving.
    wait_for_cnpg_webhook_ready

    let sync_payload = '{"operation":{"sync":{"prune":true,"syncOptions":["CreateNamespace=true","ServerSideApply=true"]}}}'
    let initial_state = (kubectl get application cnpg-cluster -n argocd -o json | from json)
    let previous_started = ($initial_state | get -o status.operationState.startedAt | default "")
    let initial_phase = ($initial_state | get -o status.operationState.phase | default "")
    let initial_sync = ($initial_state | get -o status.sync.status | default "")
    let initial_health = ($initial_state | get -o status.health.status | default "")

    # A resumed setup must not overwrite an operation that the previous invocation
    # already started: Argo rejects that with "another operation is already in
    # progress". Observe it to completion instead. Conversely, a terminal stale
    # operation is never accepted as fresh; patching below must produce a changed
    # startedAt. An already converged Application is a true idempotent no-op.
    if $initial_phase == "Succeeded" and $initial_sync == "Synced" and $initial_health == "Healthy" {
        print $"(ansi green)  ✓ cnpg-cluster is already Synced and Healthy(ansi reset)"
        return
    }

    mut resuming_existing_operation = false
    if $initial_phase == "Running" {
        if ($previous_started | is-empty) {
            error make {msg: "Running cnpg-cluster operation has no startedAt identity; refusing to overwrite an unidentifiable in-flight operation"}
        }
        $resuming_existing_operation = true
        print $"  Resuming observation of in-flight cnpg-cluster operation started at ($previous_started)..."
    } else {
        print "  Syncing cnpg-cluster with a fresh operation..."
        let sync_result = (do {
            kubectl patch application cnpg-cluster -n argocd --type merge -p $sync_payload
        } | complete)
        if $sync_result.exit_code != 0 {
            error make {msg: $"Could not start CNPG Cluster sync: (redact_sync_diagnostic ($sync_result.stderr | str trim))"}
        }
    }

    mut completed = false
    mut saw_new_operation = $resuming_existing_operation
    mut succeeded = false
    mut last_state = {}
    for attempt in 1..90 {
        let state_result = (do { kubectl get application cnpg-cluster -n argocd -o json } | complete)
        if $state_result.exit_code == 0 {
            let state = ($state_result.stdout | from json)
            $last_state = $state
            let phase = ($state | get -o status.operationState.phase | default "")
            let started = ($state | get -o status.operationState.startedAt | default "")
            let sync = ($state | get -o status.sync.status | default "")
            let health = ($state | get -o status.health.status | default "")
            if not $resuming_existing_operation and not ($started | is-empty) and $started != $previous_started {
                $saw_new_operation = true
            }
            if $resuming_existing_operation and $started != $previous_started {
                error make {msg: "The in-flight CNPG operation identity changed unexpectedly during resume"}
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
        sleep $poll_interval
    }

    if not $completed {
        if not ($last_state | is-empty) {
            print_sync_diagnostics $last_state
        }
        error make {msg: "CNPG Cluster did not reach a fresh Synced+Healthy operation before the timeout"}
    }
    if not $succeeded {
        print_sync_diagnostics $last_state
        let message = ($last_state | get -o status.operationState.message | default "")
        error make {msg: $"CNPG Cluster sync failed: (redact_sync_diagnostic $message)"}
    }
    print $"(ansi green)  ✓ cnpg-cluster sync Succeeded, Synced and Healthy(ansi reset)"
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

# Render a bounded, redacted one-line-per-app report of the non-ready
# Applications. Input is a list of {name, health, sync, phase} records; only
# those enum-like status fields are printed (never operation messages, which
# can echo credentials), and each line is still passed through the shared
# redaction as defence-in-depth. Empty input yields an empty string.
def format_non_ready_report [non_ready: list] {
    $non_ready
    | each {|a|
        let name = ($a | get -o name | default "")
        let health = ($a | get -o health | default "")
        let sync = ($a | get -o sync | default "")
        let phase = ($a | get -o phase | default "")
        redact_sync_diagnostic $"    ($name): health=($health) sync=($sync) phase=($phase)"
    }
    | str join "\n"
}

# Wait for ArgoCD apps to become healthy
def wait_for_argocd_apps [] {
    $env.KUBECONFIG = $KUBECONFIG_PATH

    print "Waiting for ArgoCD applications to sync (this may take 10-20 minutes)..."
    print ""

    # Apps to wait for — this client-side readiness inventory must match
    # apps/platform/*.yaml EXACTLY (one entry per child Application, including
    # `namespaces`). A contract test (test_bootstrap_convergence.py) fails if
    # this list drifts from the manifests, so a stale inventory can no longer
    # silently under-count readiness (issue #281: the omitted `namespaces`).
    let apps = [
        # Wave -1
        "namespaces",
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
    mut last_non_ready = []

    loop {
        $attempts = $attempts + 1
        if $attempts > $max_attempts {
            # Fail closed, but first name every blocking Application with bounded,
            # redacted health/sync/phase, then print the full status table — both
            # were previously unreachable once the timeout aborted (issue #281).
            print ""
            print $"(ansi red)Non-ready ArgoCD Applications at timeout:(ansi reset)"
            print (format_non_ready_report $last_non_ready)
            print ""
            print "ArgoCD Application Status:"
            kubectl get applications -n argocd -o wide
            let blocking = ($last_non_ready | get -o name | default [] | str join ", ")
            error make {msg: $"Timeout waiting for every required ArgoCD Application to become Synced and Healthy; non-ready: ($blocking)"}
        }

        mut ready_count = 0
        mut non_ready = []

        for app in $apps {
            let status = (do {
                kubectl get application $app -n argocd -o json
            } | complete)
            mut is_ready = false
            mut health = "Unknown"
            mut sync = "Unknown"
            mut phase = ""
            if $status.exit_code == 0 {
                let state = ($status.stdout | from json)
                $health = ($state | get -o status.health.status | default "")
                $sync = ($state | get -o status.sync.status | default "")
                $phase = ($state | get -o status.operationState.phase | default "")
                if $health == "Healthy" and $sync == "Synced" {
                    $is_ready = true
                } else if $health == "Healthy" and $sync == "OutOfSync" and (argocd_app_has_no_material_diff $app) {
                    # Count only a successful, fresh Argo core diff with zero
                    # material changes; every tool/error path remains closed.
                    print $"  ($app): stale OutOfSync status, but Argo reports no material diff"
                    $is_ready = true
                }
            }
            if $is_ready {
                $ready_count = $ready_count + 1
            } else {
                $non_ready = ($non_ready | append {name: $app, health: $health, sync: $sync, phase: $phase})
            }
        }
        $last_non_ready = $non_ready

        print $"  Apps Synced+Healthy: ($ready_count)/($apps | length) [attempt ($attempts)/($max_attempts)]"
        # Surface which Applications are blocking during the final attempts so a
        # stall is diagnosable well before the timeout error fires.
        if ($non_ready | is-not-empty) and $attempts > ($max_attempts - 5) {
            print (format_non_ready_report $non_ready)
        }

        if $ready_count == ($apps | length) {
            $all_healthy = true
            break
        }

        sleep 10sec
    }

    if $all_healthy {
        print $"(ansi green)✓ All ArgoCD applications are healthy!(ansi reset)"
    }

    # Show final status (success path — the timeout path prints its own table)
    print ""
    print "ArgoCD Application Status:"
    kubectl get applications -n argocd -o wide
}

# -----------------------------------------------------------------------------
# Phase 3: Configure Apps Functions
# -----------------------------------------------------------------------------

# Wait only for the direct dependencies of an identity-configuration phase.
# This lets Gitea/SonarQube configuration proceed independently of unrelated
# late-wave drift while remaining bounded and fail-closed. App and Certificate
# names are trusted repository constants; no untrusted operation messages or
# Secret data are emitted.
def wait_for_configuration_dependencies [phase: string, apps: list, certificates: list] {
    $env.KUBECONFIG = $KUBECONFIG_PATH

    mut last_non_ready = []
    for attempt in 1..60 {
        mut non_ready = []

        for app in $apps {
            let result = (do { kubectl get application $app -n argocd -o json } | complete)
            if $result.exit_code != 0 {
                $non_ready = ($non_ready | append $"Application/($app)=missing")
                continue
            }
            let state = (try { $result.stdout | from json } catch { null })
            if ($state | describe | str starts-with "record") == false {
                $non_ready = ($non_ready | append $"Application/($app)=invalid-status")
                continue
            }
            let health = ($state | get -o status.health.status | default "Unknown")
            let sync = ($state | get -o status.sync.status | default "Unknown")
            if not ($health == "Healthy" and $sync == "Synced") {
                $non_ready = ($non_ready | append $"Application/($app)=health:($health),sync:($sync)")
            }
        }

        for cert in $certificates {
            let namespace = ($cert | get namespace)
            let name = ($cert | get name)
            let result = (do { kubectl get certificate $name -n $namespace -o json } | complete)
            if $result.exit_code != 0 {
                $non_ready = ($non_ready | append $"Certificate/($namespace)/($name)=missing")
                continue
            }
            let resource = (try { $result.stdout | from json } catch { null })
            if ($resource | describe | str starts-with "record") == false {
                $non_ready = ($non_ready | append $"Certificate/($namespace)/($name)=invalid-status")
                continue
            }
            let conditions = ($resource | get -o status.conditions | default [])
            let ready = ($conditions | any {|condition|
                (($condition | get -o type | default "") == "Ready") and (($condition | get -o status | default "") == "True")
            })
            if not $ready {
                $non_ready = ($non_ready | append $"Certificate/($namespace)/($name)=Ready:False")
            }
        }

        $last_non_ready = $non_ready
        if ($non_ready | is-empty) {
            print $"(ansi green)✓ ($phase) configuration dependencies are ready(ansi reset)"
            return
        }

        if $attempt > 55 {
            print $"  ($phase) dependencies pending: (($non_ready | str join ', ')) [attempt ($attempt)/60]"
        }
        sleep 5sec
    }

    let bounded = ($last_non_ready | first 20 | str join ", ")
    error make {msg: $"($phase) configuration dependencies did not become ready: ($bounded)"}
}

def persist_gitea_bootstrap_token [token: string] {
    if ($token | is-empty) {
        error make {msg: "Refusing to persist an empty Gitea bootstrap token"}
    }
    let manifest = ({
        apiVersion: "v1"
        kind: "Secret"
        metadata: {name: "gitea-bootstrap-token", namespace: "gitea"}
        type: "Opaque"
        data: {token: ($token | encode base64)}
    } | to json)
    let apply_result = (do {
        $manifest | kubectl --kubeconfig $KUBECONFIG_PATH apply -f -
    } | complete)
    if $apply_result.exit_code != 0 {
        error make {msg: "Failed to persist the Gitea bootstrap token"}
    }

    # Read back and compare without printing either value. This catches apply
    # transport failures and malformed persistence while keeping the token out of
    # argv and remaining portable across Linux, macOS and Windows hosts.
    let readback = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH get secret gitea-bootstrap-token -n gitea -o jsonpath='{.data.token}'
    } | complete)
    if $readback.exit_code != 0 {
        error make {msg: "Failed to verify the persisted Gitea bootstrap token"}
    }
    let persisted = (try { $readback.stdout | str trim | decode base64 | decode utf-8 } catch { "" })
    if ($persisted | is-empty) or ($persisted != $token) {
        error make {msg: "Persisted Gitea bootstrap token did not match its source"}
    }
}

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
        error make {msg: "Gitea pod did not become ready for OIDC configuration"}
    }
    let gitea_pod = (kubectl --kubeconfig $KUBECONFIG_PATH get pods -n gitea -l app.kubernetes.io/name=gitea -o jsonpath='{.items[0].metadata.name}' | str trim)

    # Extract CA cert from cert-manager secret
    # Copy CA cert into Gitea container and update trust store.
    let ca_copy = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH cp digiorg-local-ca.crt -c gitea gitea/($gitea_pod):/usr/local/share/ca-certificates/digiorg-local-ca.crt
    } | complete)
    if $ca_copy.exit_code != 0 {
        error make {msg: "Failed to copy the local CA certificate into Gitea"}
    }
    let ca_update = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- update-ca-certificates
    } | complete)
    if $ca_update.exit_code != 0 {
        error make {msg: "Failed to update the Gitea trust store"}
    }
    print $"(ansi green)✓ CA cert registered in Gitea trust store(ansi reset)"

    # --- Step 2: Add Keycloak as OIDC authentication source ---
    print "2. Configuring Keycloak OIDC provider in Gitea..."
    
    # Check if Keycloak OIDC source already exists (idempotency)
    let existing_oauth = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- su git -c 'gitea admin auth list'
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
            error make {msg: "Failed to add the Keycloak OIDC provider to Gitea"}
        }
    } else {
        print $"(ansi yellow)✓ Keycloak OIDC provider already exists in Gitea(ansi reset)"
    }

    # --- Step 3: Create initial users in Gitea (resume-safe) ---
    print "3. Ensuring initial users exist in Gitea..."

    let users_result = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- su git -c 'gitea admin user list'
    } | complete)
    if $users_result.exit_code != 0 {
        error make {msg: "Failed to list existing Gitea users"}
    }

    if not ($users_result.stdout | lines | any {|line| $line | str contains "digiorgadmin" }) {
        let create_admin = (do {
            kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- su git -c 'gitea admin user create --username "digiorgadmin" --email "admin@digiorg.local" --password "digiorgadmin" --must-change-password false --admin true'
        } | complete)
        if $create_admin.exit_code != 0 {
            error make {msg: "Failed to create the initial Gitea administrator"}
        }
    }

    if not ($users_result.stdout | lines | any {|line| $line | str contains "digiorgdeveloper" }) {
        let create_developer = (do {
            kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- su git -c 'gitea admin user create --username "digiorgdeveloper" --email "developer@digiorg.local" --password "digiorgdeveloper" --must-change-password false --admin false'
        } | complete)
        if $create_developer.exit_code != 0 {
            error make {msg: "Failed to create the initial Gitea developer"}
        }
    }
    print $"(ansi green)✓ Initial users are present(ansi reset)"

    # --- Step 4: Create DigiOrg organisation via the Gitea API ---
    print "4. Ensuring DigiOrg organisation exists in Gitea..."

    # Persist the bootstrap token in Kubernetes rather than a CLI config file.
    # The value travels only over stdin and is never placed in a process argument.
    let token_secret = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH get secret gitea-bootstrap-token -n gitea -o jsonpath='{.data.token}'
    } | complete)
    let gitea_token = if $token_secret.exit_code == 0 {
        let stored_token = (try { $token_secret.stdout | str trim | decode base64 | decode utf-8 } catch { "" })
        if ($stored_token | is-empty) {
            error make {msg: "The existing gitea-bootstrap-token Secret has no usable token"}
        }
        print $"(ansi yellow)✓ Existing Gitea bootstrap token reused(ansi reset)"
        $stored_token
    } else if ($token_secret.stderr | str contains 'secrets "gitea-bootstrap-token" not found') {
        let token_name = $"local-setup-((date now | format date '%Y%m%d%H%M%S'))"
        let token_result = (do {
            kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- su git -c $'gitea admin user generate-access-token --username gitea_admin --token-name "($token_name)" --scopes write:activitypub,write:admin,write:issue,write:misc,write:notification,write:organization,write:package,write:repository,write:user --raw'
        } | complete)
        if $token_result.exit_code != 0 {
            error make {msg: "Failed to generate the Gitea configuration access token"}
        }
        let token = ($token_result.stdout | str trim)
        if ($token | is-empty) {
            error make {msg: "Gitea returned an empty configuration access token"}
        }
        persist_gitea_bootstrap_token $token
        print $"(ansi green)✓ Gitea bootstrap token generated and persisted(ansi reset)"
        $token
    } else {
        error make {msg: "Failed to read the gitea-bootstrap-token Secret"}
    }

    # Create the organisation through the API so no CLI configuration needs the
    # administrative token in argv. Exact HTTP status handling is fail-closed.
    let org_check = (do {
        $gitea_token | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n gitea $gitea_pod -c gitea -- sh -c 'IFS= read -r token; printf "header = \"Authorization: token %s\"\n" "$token" | curl --config - -sSk -o /dev/null -w "%{http_code}" https://digiorg.local/gitea/api/v1/orgs/DigiOrg'
    } | complete)
    if $org_check.exit_code != 0 {
        error make {msg: "Failed to query the DigiOrg organisation in Gitea"}
    }
    let org_status = ($org_check.stdout | str trim)
    if $org_status == "200" {
        print $"(ansi yellow)✓ Organisation 'DigiOrg' already exists(ansi reset)"
    } else if $org_status == "404" {
        let org_create = (do {
            $gitea_token | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n gitea $gitea_pod -c gitea -- sh -c 'IFS= read -r token; printf "header = \"Authorization: token %s\"\n" "$token" | curl --config - -sSk -o /dev/null -w "%{http_code}" -X POST -H "Content-Type: application/json" --data "{\"username\":\"DigiOrg\",\"full_name\":\"DigiOrg Organization\",\"visibility\":\"public\",\"repo_admin_change_team_access\":true}" https://digiorg.local/gitea/api/v1/orgs'
        } | complete)
        if $org_create.exit_code != 0 or (($org_create.stdout | str trim) != "201") {
            error make {msg: "Failed to create the DigiOrg organisation in Gitea"}
        }
        print $"(ansi green)✓ Organisation 'DigiOrg' created(ansi reset)"
    } else {
        error make {msg: $"Unexpected HTTP status while querying the DigiOrg organisation: ($org_status)"}
    }

    # 4f: Get Owners team ID via Gitea API. Feed the token on stdin so it is
    # never embedded in a process argument or printed command; --fail turns
    # every HTTP 4xx/5xx response into a fail-closed command result.
    let teams_result = (do {
        $gitea_token | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n gitea $gitea_pod -c gitea -- sh -c 'IFS= read -r token; printf "header = \"Authorization: token %s\"\n" "$token" | curl --config - -fsSk https://digiorg.local/gitea/api/v1/orgs/DigiOrg/teams'
    } | complete)

    if $teams_result.exit_code != 0 {
        error make {msg: "Failed to retrieve the DigiOrg teams from Gitea"}
    }
    let owners_team = ($teams_result.stdout | from json | where name == "Owners")
    if ($owners_team | is-empty) {
        error make {msg: "Could not find the Owners team in the DigiOrg organisation"}
    }
    let owners_team_id = ($owners_team | get id | first)
    print $"(ansi green)✓ DigiOrg Owners team ID: ($owners_team_id)(ansi reset)"

    # 4g: Add digiorgadmin to Owners team (idempotent)
    let admin_check = (do {
        $gitea_token | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n gitea $gitea_pod -c gitea -- sh -c $'IFS= read -r token; printf "header = \"Authorization: token %s\"\n" "$token" | curl --config - -sSk -o /dev/null -w "%{http_code}" https://digiorg.local/gitea/api/v1/teams/($owners_team_id)/members/digiorgadmin'
    } | complete)

    if ($admin_check.exit_code == 0) and (($admin_check.stdout | str trim) == "200") {
        print $"(ansi yellow)✓ 'digiorgadmin' already member of Owners team(ansi reset)"
    } else {
        let admin_add = (do {
            $gitea_token | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n gitea $gitea_pod -c gitea -- sh -c $'IFS= read -r token; printf "header = \"Authorization: token %s\"\n" "$token" | curl --config - -fsSk -X PUT https://digiorg.local/gitea/api/v1/teams/($owners_team_id)/members/digiorgadmin'
        } | complete)
        if $admin_add.exit_code == 0 {
            print $"(ansi green)✓ 'digiorgadmin' added to Owners team(ansi reset)"
        } else {
            error make {msg: "Failed to add digiorgadmin to the Gitea Owners team"}
        }
    }

    # 4h: Add digiorgdeveloper to Owners team (idempotent)
    let dev_check = (do {
        $gitea_token | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n gitea $gitea_pod -c gitea -- sh -c $'IFS= read -r token; printf "header = \"Authorization: token %s\"\n" "$token" | curl --config - -sSk -o /dev/null -w "%{http_code}" https://digiorg.local/gitea/api/v1/teams/($owners_team_id)/members/digiorgdeveloper'
    } | complete)

    if ($dev_check.exit_code == 0) and (($dev_check.stdout | str trim) == "200") {
        print $"(ansi yellow)✓ 'digiorgdeveloper' already member of Owners team(ansi reset)"
    } else {
        let dev_add = (do {
            $gitea_token | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n gitea $gitea_pod -c gitea -- sh -c $'IFS= read -r token; printf "header = \"Authorization: token %s\"\n" "$token" | curl --config - -fsSk -X PUT https://digiorg.local/gitea/api/v1/teams/($owners_team_id)/members/digiorgdeveloper'
        } | complete)
        if $dev_add.exit_code == 0 {
            print $"(ansi green)✓ 'digiorgdeveloper' added to Owners team(ansi reset)"
        } else {
            error make {msg: "Failed to add digiorgdeveloper to the Gitea Owners team"}
        }
    }

    # 4i: Verification — list Owners team members
    let verify = (do {
        $gitea_token | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n gitea $gitea_pod -c gitea -- sh -c $'IFS= read -r token; printf "header = \"Authorization: token %s\"\n" "$token" | curl --config - -fsSk https://digiorg.local/gitea/api/v1/teams/($owners_team_id)/members'
    } | complete)
    if $verify.exit_code == 0 {
        let members = ($verify.stdout | from json | get login)
        let required_members = ["digiorgadmin" "digiorgdeveloper"]
        let missing_members = ($required_members | where {|username| $username not-in $members })
        if not ($missing_members | is-empty) {
            error make {msg: $"Required Gitea Owners team members are missing: ($missing_members | str join ', ')"}
        }
        print $"(ansi green)✓ DigiOrg Owners team members: ($members | str join ', ')(ansi reset)"
    } else {
        error make {msg: "Failed to verify the Gitea Owners team membership"}
    }

    print $"(ansi green)✓ Gitea OIDC integration configured(ansi reset)"
}

# Compare SonarQube's /api/settings/values response with an expected JSON record.
# The secured certificate itself is intentionally excluded from readback because
# SonarQube does not return secured values. Invalid/missing JSON fails closed.
def sonarqube_settings_match [settings_json: string, expected_json: string] {
    let parsed = (try { $settings_json | from json } catch { null })
    let expected = (try { $expected_json | from json } catch { null })
    if (($parsed | describe | str starts-with "record") == false) or (($expected | describe | str starts-with "record") == false) {
        return false
    }
    let settings = ($parsed | get -o settings | default [])
    $expected | transpose key value | all {|entry|
        $settings | any {|setting|
            (($setting | get -o key | default "") == $entry.key) and (($setting | get -o value | default "") == $entry.value)
        }
    }
}

def sonarqube_setting_definition_present [definitions_json: string, required_key: string] {
    let parsed = (try { $definitions_json | from json } catch { null })
    if (($parsed | describe | str starts-with "record") == false) {
        return false
    }
    let definitions = ($parsed | get -o definitions | default [])
    $definitions | any {|definition| ($definition | get -o key | default "") == $required_key }
}

def sonarqube_http_status_matches [exit_code: int, status: string, expected: string] {
    $exit_code == 0 and ($status | str trim) == $expected
}

# Configure SonarQube
def configure_sonarqube [] {
    # Persisted browser-facing URLs remain public, while bootstrap traffic stays
    # inside the cluster and does not depend on a host /etc/hosts entry.
    let sonar_url = "https://digiorg.local/sonarqube"
    let sonar_api_url = "http://sonarqube-sonarqube.code-quality.svc.cluster.local:9000/sonarqube"
    let keycloak_saml_descriptor_url = "http://keycloak.keycloak.svc.cluster.local:8080/keycloak/realms/digiorg-core-platform/protocol/saml/descriptor"
    let sonar_pod_result = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH get pods -n code-quality -l app=sonarqube -o jsonpath='{.items[0].metadata.name}'
    } | complete)
    let sonar_pod = ($sonar_pod_result.stdout | str trim)
    if $sonar_pod_result.exit_code != 0 or ($sonar_pod | is-empty) {
        error make {msg: "Failed to find the SonarQube pod for in-cluster configuration"}
    }
    let admin_pass = (do -i { kubectl --kubeconfig $KUBECONFIG_PATH get secret sonarqube-admin-secret -n code-quality -o jsonpath='{.data.password}' } | complete)
    let password = if $admin_pass.exit_code == 0 {
        try { $admin_pass.stdout | str trim | decode base64 } catch { "" }
    } else if ($admin_pass.stderr | str contains 'secrets "sonarqube-admin-secret" not found') {
        # The upstream local-development chart starts with the documented default
        # admin password and does not create an admin Secret. Operators can provide
        # SONARQUBE_ADMIN_PASSWORD after changing it. Only exact NotFound falls back;
        # auth, kubeconfig and transport errors remain fatal.
        $env.SONARQUBE_ADMIN_PASSWORD? | default "admin"
    } else {
        error make {msg: "Failed to read the SonarQube admin password from sonarqube-admin-secret"}
    }
    if ($password | is-empty) {
        error make {msg: "Failed to read the SonarQube admin password from sonarqube-admin-secret"}
    }
    # Feed curl credentials through stdin config so the password is not exposed
    # in a process argument. Never print this value.
    let sonar_auth_config = $"user = \"admin:($password)\"\n"

    # Wait for SonarQube to be ready (up to 5 min)
    mut sonar_ready = false
    for attempt in 1..30 {
        let status = (do -i {
            $sonar_auth_config | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n code-quality $sonar_pod -c sonarqube -- curl --config - -fsS $"($sonar_api_url)/api/system/status"
        } | complete)
        if $status.exit_code == 0 and ($status.stdout | str contains '"status":"UP"') {
            $sonar_ready = true
            break
        }
        print $"Waiting for SonarQube... [attempt ($attempt)/30]"
        sleep 10sec
    }

    if not $sonar_ready {
        error make {msg: "SonarQube did not become ready for SAML configuration"}
    }

    # Set sonar.core.serverBaseURL via Settings API
    let result = (do -i { $sonar_auth_config | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n code-quality $sonar_pod -c sonarqube -- curl --config - -sS -o /dev/null -w "%{http_code}" -X POST $"($sonar_api_url)/api/settings/set" --data-urlencode "key=sonar.core.serverBaseURL" --data-urlencode $"value=($sonar_url)" } | complete)

    if (sonarqube_http_status_matches $result.exit_code $result.stdout "204") {
        print $"(ansi green)✓ SonarQube Server Base URL set to ($sonar_url)(ansi reset)"
    } else {
        error make {msg: "Failed to set the SonarQube Server Base URL"}
    }

    # --- Step 1: Fetch Keycloak IdP X.509 certificate from SAML descriptor ---
    # The SAML metadata descriptor endpoint returns the full X.509 certificate
    # (not just the raw public key), which is what SonarQube requires.
    print "  1. Fetching Keycloak IdP X.509 certificate from SAML descriptor..."
    let cert_result = (do -i { kubectl --kubeconfig $KUBECONFIG_PATH exec -n code-quality $sonar_pod -c sonarqube -- curl -fsS $keycloak_saml_descriptor_url } | complete)
    if $cert_result.exit_code != 0 {
        error make {msg: "Failed to reach the Keycloak SAML descriptor endpoint"}
    }
    let keycloak_cert = ($cert_result.stdout | parse --regex '(?s)<ds:X509Certificate>(.*?)</ds:X509Certificate>' | get capture0 | first | str trim)
    if ($keycloak_cert | is-empty) {
        error make {msg: "Could not extract the X509Certificate from the Keycloak SAML descriptor"}
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
        let r = (do -i { $sonar_auth_config | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n code-quality $sonar_pod -c sonarqube -- curl --config - -sS -o /dev/null -w "%{http_code}" -X POST $"($sonar_api_url)/api/settings/set" --data-urlencode $"key=($setting.key)" --data-urlencode $"value=($setting.value)" } | complete)
        if not (sonarqube_http_status_matches $r.exit_code $r.stdout "204") {
            print $"(ansi red)✗ Failed to set ($setting.key)(ansi reset)"
            $all_ok = false
        }
    }

    # --- Step 4: Enable SAML ---
    let enable_result = (do -i { $sonar_auth_config | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n code-quality $sonar_pod -c sonarqube -- curl --config - -sS -o /dev/null -w "%{http_code}" -X POST $"($sonar_api_url)/api/settings/set" --data-urlencode "key=sonar.auth.saml.enabled" --data-urlencode "value=true" } | complete)
    if not (sonarqube_http_status_matches $enable_result.exit_code $enable_result.stdout "204") {
        print $"(ansi red)✗ Failed to enable SAML(ansi reset)"
        $all_ok = false
    }

    if not $all_ok {
        error make {msg: "One or more SonarQube SAML settings could not be applied"}
    }

    # Mandatory readback of every non-secured setting. Successful POST status is
    # insufficient: proxies or API compatibility errors can otherwise leave the
    # platform unconfigured while the bootstrap reports success.
    let expected_readback = {
        "sonar.core.serverBaseURL": $sonar_url
        "sonar.auth.saml.applicationId": "sonarqube"
        "sonar.auth.saml.providerName": "Keycloak"
        "sonar.auth.saml.providerId": "https://digiorg.local/keycloak/realms/digiorg-core-platform"
        "sonar.auth.saml.loginUrl": "https://digiorg.local/keycloak/realms/digiorg-core-platform/protocol/saml"
        "sonar.auth.saml.user.login": "login"
        "sonar.auth.saml.user.name": "name"
        "sonar.auth.saml.user.email": "email"
        "sonar.auth.saml.group.name": "groups"
        "sonar.auth.saml.allowUsersToSignUp": "true"
        "sonar.auth.saml.enabled": "true"
    }
    let secured_certificate_key = "sonar.auth.saml.certificate.secured"
    let readback_keys = ($expected_readback | columns | str join ",")
    let readback = (do -i {
        $sonar_auth_config | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n code-quality $sonar_pod -c sonarqube -- curl --config - -sS -w "\n%{http_code}" --get $"($sonar_api_url)/api/settings/values" --data-urlencode $"keys=($readback_keys)"
    } | complete)
    let readback_lines = ($readback.stdout | lines)
    let readback_status = ($readback_lines | last | default "")
    let readback_body = ($readback_lines | drop 1 | str join "\n")
    if not (sonarqube_http_status_matches $readback.exit_code $readback_status "200") or not (sonarqube_settings_match $readback_body ($expected_readback | to json)) {
        error make {msg: "SonarQube settings readback did not match the required SAML configuration"}
    }

    # Secured values are intentionally omitted from /api/settings/values even
    # when configured. Verify the non-secret setting definition instead; together
    # with the exact 204 mutation status this is the strongest available signal.
    let definitions = (do -i {
        $sonar_auth_config | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n code-quality $sonar_pod -c sonarqube -- curl --config - -sS -w "\n%{http_code}" $"($sonar_api_url)/api/settings/list_definitions"
    } | complete)
    let definition_lines = ($definitions.stdout | lines)
    let definition_status = ($definition_lines | last | default "")
    let definition_body = ($definition_lines | drop 1 | str join "\n")
    if not (sonarqube_http_status_matches $definitions.exit_code $definition_status "200") or not (sonarqube_setting_definition_present $definition_body $secured_certificate_key) {
        error make {msg: "SonarQube does not expose the required secured SAML certificate setting definition"}
    }

    print $"(ansi green)✓ SAML fully configured, enabled and verified in SonarQube(ansi reset)"
}

# Restart one existing OIDC-dependent deployment and fail closed if either the
# restart request or rollout cannot complete. A missing deployment is left to the
# final Application convergence gate; it must not be reported as restarted.
def restart_oidc_deployment [namespace: string, deployment: string, timeout: string] {
    let exists = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH get deployment $deployment -n $namespace -o name
    } | complete)
    if $exists.exit_code != 0 {
        error make {msg: $"Required OIDC-dependent deployment ($namespace)/($deployment) is not present"}
    }

    let restart = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH rollout restart deployment $deployment -n $namespace
    } | complete)
    if $restart.exit_code != 0 {
        error make {msg: $"Failed to restart OIDC-dependent deployment ($namespace)/($deployment)"}
    }

    let rollout = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH rollout status deployment $deployment -n $namespace $"--timeout=($timeout)"
    } | complete)
    if $rollout.exit_code != 0 {
        error make {msg: $"OIDC-dependent deployment ($namespace)/($deployment) did not complete its rollout"}
    }
    print $"(ansi green)✓ ($namespace)/($deployment) restarted(ansi reset)"
}

# Restart pods that depend on OIDC/Keycloak. This function is called only after
# both Gitea and SonarQube configuration returned successfully.
def restart_oidc_dependent_pods [] {
    $env.KUBECONFIG = $KUBECONFIG_PATH
    print "Restarting OIDC-dependent pods to refresh DNS/config..."

    wait_for_configuration_dependencies "OIDC restarts" ["argocd" "grafana" "backstage" "landingpage"] []

    restart_oidc_deployment "argocd" "argocd-server" "120s"
    restart_oidc_deployment "monitoring" "prometheus-grafana" "120s"
    restart_oidc_deployment "backstage" "backstage" "180s"
    restart_oidc_deployment "platform-apps" "landingpage" "120s"

    print $"(ansi green)✓ Existing OIDC-dependent deployments restarted(ansi reset)"
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
            error make {msg: "CA certificate did not become available for the ArgoCD OIDC configuration"}
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
        error make {msg: "Could not extract the CA certificate for the ArgoCD OIDC configuration"}
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
    let helm_conflict_args = (helm_force_conflicts_args)
    let helm_result = (do {
        helm upgrade argocd argo/argo-cd --version 10.1.4 --namespace argocd --reuse-values --values platform/base/argocd/values.yaml --values ./argocd-oidc-override.yaml ...$helm_conflict_args --wait --timeout 5m
    } | complete)
    if $helm_result.exit_code != 0 {
        error make {msg: "Failed to update the ArgoCD OIDC CA configuration via Helm"}
    }

    print $"(ansi green)✓ ArgoCD OIDC config updated with CA cert via Helm(ansi reset)"

    # Restart ArgoCD server to pick up new config immediately.
    restart_oidc_deployment "argocd" "argocd-server" "120s"

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

# Parse the real `argocd version --client --short` output and compare the
# semantic version exactly. Build metadata is allowed; prefix/suffix versions
# such as v3.4.50 must not satisfy a v3.4.5 requirement.
def argocd_client_version_matches [output: string, expected: string] {
    let parsed = ($output | str trim | parse --regex '^argocd:\s+v(?P<version>[0-9]+\.[0-9]+\.[0-9]+)(?:\+[0-9A-Za-z.-]+)?$')
    if ($parsed | length) != 1 {
        return false
    }
    (($parsed | get version | first) == $expected)
}

# Check if prequisite tools (kind, kubectl, helm) are installed before proceeding
def check_prerequisites [] {
    print "Checking prerequisites..."
    
    let tools = [
        ["kind", "https://kind.sigs.k8s.io/docs/user/quick-start/#installation"],
        ["kubectl", "https://kubernetes.io/docs/tasks/tools/"],
        ["helm", "https://helm.sh/docs/intro/install/"],
        # Issue #281: `argocd` is required, not optional. Final readiness treats a
        # stale Healthy/OutOfSync Application as ready ONLY when the Argo CD core
        # diff proves zero material change (see argocd_app_has_no_material_diff).
        # A missing CLI silently disabled that fallback and stalled the bootstrap
        # at 24/26 for the full timeout. Pin a CLI that matches the deployed
        # Argo CD server (v3.4.5 — see docs/guides/platform-versions.md).
        ["argocd", "https://argo-cd.readthedocs.io/en/stable/cli_installation/"]
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

    # The zero-diff fallback is part of readiness semantics, so merely finding an
    # arbitrary argocd binary is insufficient. Match the deployed Argo CD v3.4.5
    # client exactly on Windows and Linux before any long-running bootstrap work.
    let expected_argocd_version = "3.4.5"
    let argocd_version = (do { argocd version --client --short } | complete)
    if $argocd_version.exit_code != 0 or not (argocd_client_version_matches $argocd_version.stdout $expected_argocd_version) {
        error make {msg: $"argocd CLI v($expected_argocd_version) is required to match the deployed server"}
    }
    print $"(ansi green)✓ argocd CLI v($expected_argocd_version)(ansi reset)"
    
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
