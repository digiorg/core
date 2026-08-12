#!/usr/bin/env nu

# =============================================================================
# Local Development Environment Setup (App-of-Apps Pattern)
# =============================================================================
# This script bootstraps the local KinD cluster and deploys the ArgoCD root app.
# ArgoCD then manages all platform components via the App-of-Apps pattern.
#
# Usage:
#   nu scripts/local-setup.nu up            # Bootstrap cluster + deploy root app
#   nu scripts/local-setup.nu down          # Destroy local cluster
#   nu scripts/local-setup.nu reset         # Reset cluster (down + up)
#   nu scripts/local-setup.nu status        # Show cluster status
#   nu scripts/local-setup.nu bootstrap     # Run only Phase 1 bootstrap (no root app)
#   nu scripts/local-setup.nu future-infra  # Provision CNPG (optional, run after `up`)
#
# Architecture:
#   Phase 1 (this script): KinD → Ingress → CoreDNS → Secrets → ArgoCD → Core Data Gates → Root App
#   Phase 2 (ArgoCD):      Root App → Applications → Platform Components
#
# CNPG (CloudNativePG) is OPTIONAL, future hosted-application database
# infrastructure — see docs/guides/cnpg-future-app-database.md. `up` never
# provisions it; run `future-infra` explicitly, after `up` has succeeded.
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
    print "  up            - Bootstrap cluster and deploy ArgoCD root app"
    print "  down          - Destroy local cluster"
    print "  reset         - Reset cluster (down + up)"
    print "  status        - Show cluster and ArgoCD app status"
    print "  bootstrap     - Run only Phase 1 bootstrap (no root app)"
    print "  future-infra  - Provision CNPG (optional; run after 'up' succeeds)"
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

    # Issue #285 (TLS hardening): copy only the public digiorg.local CA
    # certificate (never cert-manager's private key) into every namespace
    # running a client that must verify the digiorg.local ingress's
    # certificate -- crossplane-system (provider-http's shared
    # ProviderConfig) and backstage (NODE_EXTRA_CA_CERTS).
    # Idempotent, so safe on both a first bootstrap and a resumed run.
    print "Copying the digiorg.local CA into consumer namespaces..."
    copy_digiorg_local_ca_to_namespace "crossplane-system"
    copy_digiorg_local_ca_to_namespace "backstage"
    # Harbor PostSync hooks mount this Secret optionally and wait for ca.crt,
    # avoiding a sync deadlock while keeping admin credentials off plaintext HTTP.
    copy_digiorg_local_ca_to_namespace "harbor"

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

    # Argo convergence proves that the XRD Application reconciled, but not that
    # Crossplane successfully established and offered its generated APIs. Keep
    # this final, fail-closed API gate after Argo and before the success banner.
    print ""
    print $"(ansi cyan_bold)Phase 7: AppClaim API Readiness Gate(ansi reset)"
    print "────────────────────────────────────"
    wait_for_appclaim_api_ready

    # Issue #283: `main up` deliberately does NOT promote CNPG (optional,
    # future hosted-application database infrastructure) — not even wrapped
    # in a try/catch. Catching a CNPG sync error does not undo the minutes it
    # can spend polling operator availability, webhook readiness and the
    # Cluster's sync operation first; that wait alone would delay this Ready
    # banner, which is exactly what Issue #283 prohibits. Run
    # `nu scripts/local-setup.nu future-infra` explicitly, whenever you
    # actually need CNPG, after core bootstrap has already succeeded.
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
    print "  Keycloak:     https://digiorg.local/keycloak  (admin console)"
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
    print ""
    print "Future Application Infrastructure (optional, not part of core readiness):"
    print "  CNPG is not provisioned by `up`. Run this explicitly whenever you need it:"
    print "    nu scripts/local-setup.nu future-infra"
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

    # containerd resolves registry hosts in the KinD node network namespace,
    # not through the host OS hosts file or cluster CoreDNS. Keep the single
    # local ingress hostname deterministic before any workload can pull a
    # promoted private image.
    ensure_kind_node_digiorg_local_resolution
    
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

# Provision/promote CNPG — OPTIONAL, future hosted-application database
# infrastructure (Issue #283). Deliberately SEPARATE from `main up`: no
# internal platform component depends on CNPG (Keycloak, Backstage, Gitea,
# SonarQube and Harbor permanently use legacy PostgreSQL), so its operator-
# availability, webhook-readiness and Cluster-sync waits must never run as
# part of — and therefore never delay — core-platform bootstrap. Run this
# explicitly, any time after `up` has already succeeded. Unlike the removed
# best-effort wrapper this had before, it is intentionally fail-closed: a
# real CNPG problem must surface as a real, non-zero-exit error here.
def "main future-infra" [] {
    print $"(ansi cyan_bold)Promoting Future Application Infrastructure \(CNPG, optional\)(ansi reset)"
    print "────────────────────────────────────"
    print "CNPG is optional, future hosted-application database infrastructure."
    print "Keycloak, Backstage, Gitea, SonarQube and Harbor are unaffected either way."
    print ""
    promote_cnpg_operator
    promote_cnpg_cluster
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

def kubectl_error_is_exact_not_found [stderr: string, resource: string, name: string] {
    let expected = (["Error from server (NotFound): " $resource ' "' $name '" not found'] | str join)
    ($stderr | str trim) == $expected
}

def secret_value_or_default [namespace: string, secret: string, key: string, override, fallback] {
    let explicit = ($override | default "")
    if ($explicit | into string) != "" {
        return ($explicit | into string)
    }

    let lookup = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH get secret $secret -n $namespace -o $"jsonpath={.data.($key)}"
    } | complete)
    if $lookup.exit_code == 0 {
        let existing = (try { $lookup.stdout | str trim | decode base64 | decode utf-8 } catch { "" })
        if ($existing | is-empty) {
            error make {msg: $"Existing Secret ($namespace)/($secret) has no usable ($key) value"}
        }
        return $existing
    }

    let secret_missing = (kubectl_error_is_exact_not_found $lookup.stderr "secrets" $secret)
    let namespace_missing = (kubectl_error_is_exact_not_found $lookup.stderr "namespaces" $namespace)
    if $secret_missing or $namespace_missing {
        return ($fallback | into string)
    }
    error make {msg: $"Failed to read Secret ($namespace)/($secret) while resolving ($key)"}
}

# One-time, secret-safe key migration for a known pre-fix Secret schema. The
# canonical key always wins. If the Secret exists but only the legacy key is
# populated, its value is returned and the caller's normal declarative apply
# writes it back under the canonical key. The caller persists it through stdin,
# so the value is neither printed nor placed in host process arguments.
def secret_value_or_legacy_key_or_default [namespace: string, secret: string, key: string, legacy_key: string, override, fallback] {
    let explicit = ($override | default "")
    if ($explicit | into string) != "" {
        return ($explicit | into string)
    }

    let lookup = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH get secret $secret -n $namespace -o $"jsonpath={.data.($key)}"
    } | complete)
    if $lookup.exit_code == 0 {
        let existing = (try { $lookup.stdout | str trim | decode base64 | decode utf-8 } catch { "" })
        if not ($existing | is-empty) {
            return $existing
        }

        let legacy_lookup = (do {
            kubectl --kubeconfig $KUBECONFIG_PATH get secret $secret -n $namespace -o $"jsonpath={.data.($legacy_key)}"
        } | complete)
        if $legacy_lookup.exit_code != 0 {
            error make {msg: $"Failed to read existing Secret ($namespace)/($secret) legacy key while resolving ($key)"}
        }
        let legacy_existing = (try { $legacy_lookup.stdout | str trim | decode base64 | decode utf-8 } catch { "" })
        if ($legacy_existing | is-empty) {
            error make {msg: $"Existing Secret ($namespace)/($secret) has no usable ($key) or legacy key value"}
        }
        return $legacy_existing
    }

    let secret_missing = (kubectl_error_is_exact_not_found $lookup.stderr "secrets" $secret)
    let namespace_missing = (kubectl_error_is_exact_not_found $lookup.stderr "namespaces" $namespace)
    if $secret_missing or $namespace_missing {
        return ($fallback | into string)
    }
    error make {msg: $"Failed to read Secret ($namespace)/($secret) while resolving ($key)"}
}

def persist_harbor_oidc_secret [value: string] {
    if ($value | is-empty) {
        error make {msg: "Refusing to persist an empty Harbor OIDC client secret"}
    }
    let manifest = ({
        apiVersion: "v1"
        kind: "Secret"
        metadata: {name: "harbor-oidc-secret", namespace: "harbor"}
        type: "Opaque"
        data: {OIDC_CLIENT_SECRET: ($value | encode base64)}
    } | to json)
    let apply_result = (do {
        $manifest | kubectl --kubeconfig $KUBECONFIG_PATH apply -f -
    } | complete)
    if $apply_result.exit_code != 0 {
        error make {msg: "Failed to persist the Harbor OIDC client secret"}
    }

    # Compare in memory: neither source nor readback is printed.
    let readback = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH get secret harbor-oidc-secret -n harbor -o jsonpath='{.data.OIDC_CLIENT_SECRET}'
    } | complete)
    if $readback.exit_code != 0 {
        error make {msg: "Failed to verify the persisted Harbor OIDC client secret"}
    }
    let persisted = (try { $readback.stdout | str trim | decode base64 | decode utf-8 } catch { "" })
    if ($persisted | is-empty) or ($persisted != $value) {
        error make {msg: "Persisted Harbor OIDC client secret did not match its source"}
    }
}

def create_platform_namespaces_secrets [] {
    # Preserve existing values on resume. Environment variables are explicit
    # rotation requests; random/default values are used only on a clean cluster.
    let postgres_password = (secret_value_or_default "platform-db" "postgresql-secrets" "POSTGRES_PASSWORD" $env.POSTGRES_PASSWORD? (generate_password))
    let keycloak_db_password = (secret_value_or_default "platform-db" "postgresql-secrets" "KEYCLOAK_DB_PASSWORD" $env.KEYCLOAK_DB_PASSWORD? (generate_password))
    let backstage_db_password = (secret_value_or_default "platform-db" "postgresql-secrets" "BACKSTAGE_DB_PASSWORD" $env.BACKSTAGE_DB_PASSWORD? (generate_password))
    let backstage_session_secret = (secret_value_or_default "backstage" "backstage-secrets" "AUTH_SESSION_SECRET" $env.AUTH_SESSION_SECRET? (generate_password))
    let backstage_oidc_secret = (secret_value_or_default "backstage" "backstage-secrets" "AUTH_OIDC_CLIENT_SECRET" $env.AUTH_OIDC_CLIENT_SECRET? "backstage-client-secret")
    let gitea_db_password = (secret_value_or_default "platform-db" "postgresql-secrets" "GITEA_DB_PASSWORD" $env.GITEA_DB_PASSWORD? (generate_password))
    let gitea_oidc_secret = (secret_value_or_default "gitea" "gitea-secrets" "AUTH_OIDC_CLIENT_SECRET" $env.GITEA_OIDC_CLIENT_SECRET? "gitea-client-secret")
    let sonarqube_db_password = (secret_value_or_default "platform-db" "postgresql-secrets" "SONARQUBE_DB_PASSWORD" $env.SONARQUBE_DB_PASSWORD? (generate_password))
    let sonarqube_monitoring_passcode = (secret_value_or_default "code-quality" "sonarqube-monitoring-secret" "SONAR_WEB_SYSTEMPASSCODE" $env.SONARQUBE_MONITORING_PASSCODE? (generate_password))
    let harbor_admin_password = (secret_value_or_default "harbor" "harbor-admin-secret" "HARBOR_ADMIN_PASSWORD" $env.HARBOR_ADMIN_PASSWORD? "Harbor12345")
    let harbor_secret_key = (secret_value_or_default "harbor" "harbor-secret-key" "secretKey" $env.HARBOR_SECRET_KEY? "not-a-secure-key")
    let harbor_db_password = (secret_value_or_default "platform-db" "postgresql-secrets" "HARBOR_DB_PASSWORD" $env.HARBOR_DB_PASSWORD? (generate_password))
    let harbor_oidc_secret = (secret_value_or_legacy_key_or_default "harbor" "harbor-oidc-secret" "OIDC_CLIENT_SECRET" "client-secret" $env.HARBOR_OIDC_CLIENT_SECRET? "harbor-client-secret")
    
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

    # Issue #283: no CNPG superuser secret is provisioned here. CNPG is optional
    # future-app database infrastructure and must use only its own,
    # auto-generated Secrets — never a credential coupled to the legacy
    # postgres_password. See platform/base/cnpg/cluster.yaml.

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
    persist_harbor_oidc_secret $harbor_oidc_secret

    # Issue #285: precomputed HTTP Basic-auth value for the Harbor admin
    # identity, consumed only by the declarative, one-time
    # crossplane-harbor-bootstrap Request (crossplane/bootstrap/harbor-robot-request.yaml)
    # to create the least-privilege crossplane-system system robot. provider-http's
    # `{{ name:namespace:key }}` templating substitutes a secret value verbatim
    # (it does not encode), so the base64(admin:password) pair is computed once
    # here, kept out of argv/logs, and never reused as a per-app credential.
    let harbor_admin_basic = ($"admin:($harbor_admin_password)" | encode base64)
    persist_opaque_secret "harbor" "harbor-admin-basic-auth" "value" $harbor_admin_basic

    # Messaging namespace (for NATS server + Surveyor)
    kubectl create namespace messaging --dry-run=client -o yaml | kubectl apply -f -
    ensure_nats_jetstream_controller_nkey

    kubectl create namespace tracing --dry-run=client -o yaml | kubectl apply -f -

    # Jaeger oauth2-proxy secret (Keycloak OIDC client + cookie encryption)
    let jaeger_oidc_secret = (secret_value_or_default "tracing" "jaeger-oauth2-proxy-secrets" "client-secret" $env.JAEGER_OIDC_CLIENT_SECRET? "jaeger-client-secret")
    let jaeger_cookie_secret = (secret_value_or_default "tracing" "jaeger-oauth2-proxy-secrets" "cookie-secret" $env.JAEGER_COOKIE_SECRET? (generate_password | str substring 0..31 | encode base64))
    (kubectl create secret generic jaeger-oauth2-proxy-secrets -n tracing
        --from-literal=client-secret=($jaeger_oidc_secret)
        --from-literal=cookie-secret=($jaeger_cookie_secret)
        --dry-run=client -o yaml | kubectl apply -f -)

    # cost-monitoring namespace + OpenCost oauth2-proxy secrets
    kubectl create namespace cost-monitoring --dry-run=client -o yaml | kubectl apply -f -
    let opencost_oidc_secret = (secret_value_or_default "cost-monitoring" "opencost-oauth2-proxy-secrets" "client-secret" $env.OPENCOST_OIDC_CLIENT_SECRET? "opencost-client-secret")
    let opencost_cookie_secret = (secret_value_or_default "cost-monitoring" "opencost-oauth2-proxy-secrets" "cookie-secret" $env.OPENCOST_COOKIE_SECRET? (generate_password | str substring 0..31 | encode base64))
    (kubectl create secret generic opencost-oauth2-proxy-secrets -n cost-monitoring
        --from-literal=client-secret=($opencost_oidc_secret)
        --from-literal=cookie-secret=($opencost_cookie_secret)
        --dry-run=client -o yaml | kubectl apply -f -)

    # OpenSearch secret (admin password for observability backend)
    let opensearch_admin_password = (secret_value_or_default "platform-db" "opensearch-secrets" "OPENSEARCH_ADMIN_PASSWORD" $env.OPENSEARCH_ADMIN_PASSWORD? (generate_password))
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

# Issue #283 (P1 correction): apply the core data layer's own Application
# manifests DIRECTLY — not via root-app's app-of-apps fan-out — and prove both
# functionally ready before returning. Applying root-app first was
# insufficient: most child Applications (Keycloak, Jaeger, ...) carry
# `syncPolicy.automated`, so the instant root-app creates their Application
# CRs, Argo starts reconciling them concurrently regardless of what this
# script does afterwards. Applying ONLY postgresql.yaml/opensearch.yaml here
# means no consumer Application CR exists yet at all — there is nothing for
# Argo to race. `kubectl apply -f` is idempotent, so a resumed run (or
# root-app later re-applying the same two manifests) is a no-op.
def deploy_core_data_layer [] {
    $env.KUBECONFIG = $KUBECONFIG_PATH

    print ""
    print $"(ansi cyan_bold)Deploying core data layer \(legacy PostgreSQL + OpenSearch\)(ansi reset)"
    print "────────────────────────────────────"
    print "Applying core data-layer Applications directly, before root-app, so no consumer Application can exist yet..."

    let pg_apply = (do { kubectl apply -f apps/platform/postgresql.yaml } | complete)
    if $pg_apply.exit_code != 0 {
        error make {msg: $"Failed to apply the postgresql Application: (redact_sync_diagnostic ($pg_apply.stderr | str trim))"}
    }
    let os_apply = (do { kubectl apply -f apps/platform/opensearch.yaml } | complete)
    if $os_apply.exit_code != 0 {
        error make {msg: $"Failed to apply the opensearch Application: (redact_sync_diagnostic ($os_apply.stderr | str trim))"}
    }
    print $"(ansi green)✓ postgresql and opensearch Applications applied(ansi reset)"

    wait_for_postgresql_ready
    wait_for_opensearch_ready
}

# Deploy ArgoCD Root App (triggers App-of-Apps)
def deploy_root_app [] {
    $env.KUBECONFIG = $KUBECONFIG_PATH

    # Issue #279: the root Application immediately fans out into many
    # concurrent Git/Helm/Kustomize renders. Wait for argocd-repo-server to be
    # Ready and restart-stable before applying ANY Application (including the
    # core data layer below), so the first sync doesn't race the initial
    # render burst (confirmed cause of the repo-server liveness restart that
    # severed External Secrets' manifest generation with gRPC Unavailable/EOF).
    print $"(ansi cyan_bold)Waiting for argocd-repo-server to stabilize(ansi reset)"
    print "────────────────────────────────────"
    wait_for_repo_server_stable

    # Issue #283 (P1 correction): the core data layer must be applied and
    # proven functionally ready BEFORE root-app is applied — see
    # deploy_core_data_layer for why waiting only afterwards does not work.
    deploy_core_data_layer

    print ""
    print "Deploying ArgoCD Root App..."
    kubectl apply -f platform/base/argocd/applications/root-app.yaml

    print $"(ansi green)✓ Root App deployed - ArgoCD will now sync the remaining platform components(ansi reset)"
    print ""
    print "ArgoCD Sync Waves:"
    print "  Wave -1: root-app (just deployed), namespaces"
    print "  Wave  0: cert-manager, external-secrets, nats, postgresql*, opensearch* (core data layer)"
    print "  Wave  1: keycloak, argocd (self-managed)"
    print "  Wave  2: backstage, gitea, grafana, harbor, jaeger, landingpage, opencost, sonarqube"
    print "  Wave  3: crossplane, kyverno"
    print "  Wave  4: crossplane-providers, fluentd, kyverno-policies, gitea-actions-runner"
    print "  Wave  5: monitoring-extras (ServiceMonitors)"
    print "  Wave  6: crossplane-provider-configs"
    print "  Wave  7: crossplane-xrds"
    print "  Wave  8: core-catalog"
    print "  Wave  9: cnpg (optional future-app database infrastructure)"
    print "  Wave 10: cnpg-cluster (optional future-app database infrastructure)"
    print "  * postgresql/opensearch were already applied directly and proven ready above"

    # The repository keeps major upgrades manual so a Git merge cannot trigger
    # them concurrently in a shared cluster. This script targets only the named
    # local KinD environment; invoking `main up` is the explicit approval to sync
    # its gated apps sequentially.
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

# -----------------------------------------------------------------------------
# Issue #283: Core data-layer functional readiness
# -----------------------------------------------------------------------------
# Legacy PostgreSQL and OpenSearch are the platform's core data layer. Argo CD's
# generic StatefulSet health (rollout complete / pods Ready) does not prove they
# are functionally usable by consumers, so these bounded, secret-safe checks run
# early in deploy_root_app — before any gated Application sync and before CNPG
# (optional future-app infrastructure) is even attempted — so Keycloak,
# Backstage, Gitea, SonarQube and Harbor (PostgreSQL) and Jaeger/Fluentd
# (OpenSearch) have a real, proven-ready data layer as early as possible.

# The internal-platform databases the legacy postgresql StatefulSet's init
# script creates (platform/base/postgresql/statefulset.yaml): keycloak,
# backstage, gitea, sonarqube and harbor's database "registry".
def postgresql_required_databases [] {
    ["keycloak" "backstage" "gitea" "sonarqube" "registry"]
}

# Pure predicate: does a newline-separated `datname` list contain every
# required internal-platform database? Fail closed on anything less.
def postgresql_has_required_databases [datnames: string] {
    let present = ($datnames | lines | each { str trim } | where {|x| $x != "" })
    postgresql_required_databases | all {|db| $db in $present }
}

# PostgreSQL must accept connections through the real Service/TCP path and each
# internal application role must authenticate to its own database with the
# required public-schema privileges. Passwords are expanded only inside the
# PostgreSQL container from its existing Secret-backed environment variables;
# no Secret value enters host argv/stdout. Every exec has a hard API timeout.
def wait_for_postgresql_ready [poll_interval: duration = 5sec] {
    $env.KUBECONFIG = $KUBECONFIG_PATH

    print ""
    print $"(ansi cyan_bold)Waiting for PostgreSQL functional readiness(ansi reset)"
    mut last_diagnostic = "postgresql readiness was not observed"
    for attempt in 1..60 {
        let ready = (do {
            kubectl --request-timeout=10s exec -n platform-db statefulset/postgresql -- pg_isready -h postgresql.platform-db.svc.cluster.local -U postgres
        } | complete)
        if $ready.exit_code == 0 {
            let dbs = (do {
                kubectl --request-timeout=10s exec -n platform-db statefulset/postgresql -- psql -U postgres -tAc "SELECT datname FROM pg_database"
            } | complete)
            if $dbs.exit_code == 0 {
                if (postgresql_has_required_databases $dbs.stdout) {
                    let application_targets = [
                        {role: "keycloak", database: "keycloak", password_env: "KEYCLOAK_DB_PASSWORD"}
                        {role: "backstage", database: "backstage", password_env: "BACKSTAGE_DB_PASSWORD"}
                        {role: "gitea", database: "gitea", password_env: "GITEA_DB_PASSWORD"}
                        {role: "sonarqube", database: "sonarqube", password_env: "SONARQUBE_DB_PASSWORD"}
                        {role: "harbor", database: "registry", password_env: "HARBOR_DB_PASSWORD"}
                    ]
                    mut failed_roles = []
                    for target in $application_targets {
                        # Construct only a reference such as $KEYCLOAK_DB_PASSWORD;
                        # the value is expanded by sh inside the container.
                        let password_reference = ('$' + $target.password_env)
                        let privilege_query = "SELECT CASE WHEN has_schema_privilege(current_user, 'public', 'USAGE,CREATE') THEN 1 ELSE 0 END"
                        let probe_command = $'PGPASSWORD="($password_reference)" psql -v ON_ERROR_STOP=1 -h postgresql.platform-db.svc.cluster.local -U ($target.role) -d ($target.database) -tAc "($privilege_query)"'
                        let probe = (do {
                            kubectl --request-timeout=10s exec -n platform-db statefulset/postgresql -- sh -c $probe_command
                        } | complete)
                        if $probe.exit_code != 0 or ($probe.stdout | str trim) != "1" {
                            $failed_roles = ($failed_roles | append $target.role)
                        }
                    }
                    if ($failed_roles | is-empty) {
                        print $"(ansi green)✓ PostgreSQL Service accepts every platform role and required schema privileges are initialized(ansi reset)"
                        return
                    }
                    $last_diagnostic = $"PostgreSQL application roles not ready: ($failed_roles | str join ', ')"
                } else {
                    $last_diagnostic = "PostgreSQL accepts connections but required databases are not yet initialized"
                }
            } else {
                $last_diagnostic = (redact_sync_diagnostic ($dbs.stderr | str trim))
            }
        } else {
            $last_diagnostic = (redact_sync_diagnostic (($ready.stdout + " " + $ready.stderr) | str trim))
        }
        print $"  postgresql readiness: ($last_diagnostic) [attempt ($attempt)/60]"
        sleep $poll_interval
    }
    error make {msg: $"PostgreSQL did not become functionally ready before its consumers: ($last_diagnostic)"}
}

# Pure predicate: is an OpenSearch `_cluster/health` JSON response acceptable?
# Fail closed on red status or unparseable input.
def opensearch_cluster_health_acceptable [health_json: string] {
    let parsed = (try { $health_json | from json } catch { null })
    if ($parsed | describe | str starts-with "record") == false {
        return false
    }
    let status = ($parsed | get -o status | default "")
    $status in ["green" "yellow"]
}

# OpenSearch must answer its real Service-DNS cluster-health request with an acceptable
# status before its consumers (Jaeger, Fluentd) may be considered safe to
# start. The security plugin is disabled for local dev (see
# platform/base/opensearch/values.yaml), so no credential is required; the
# request stays inside the cluster via `kubectl exec` regardless. Both kubectl
# and curl have hard timeouts so a stuck API/stream cannot defeat the poll bound.
def wait_for_opensearch_ready [poll_interval: duration = 5sec] {
    $env.KUBECONFIG = $KUBECONFIG_PATH

    print ""
    print $"(ansi cyan_bold)Waiting for OpenSearch functional readiness(ansi reset)"
    mut last_diagnostic = "opensearch readiness was not observed"
    for attempt in 1..90 {
        let result = (do {
            kubectl --request-timeout=10s exec -n platform-db statefulset/opensearch-cluster-master -- curl -s -m 5 "http://opensearch-cluster-master.platform-db.svc.cluster.local:9200/_cluster/health"
        } | complete)
        if $result.exit_code == 0 {
            if (opensearch_cluster_health_acceptable $result.stdout) {
                print $"(ansi green)✓ OpenSearch cluster health is acceptable(ansi reset)"
                return
            }
            $last_diagnostic = "OpenSearch cluster health is not yet green/yellow"
        } else {
            $last_diagnostic = (redact_sync_diagnostic (($result.stdout + " " + $result.stderr) | str trim))
        }
        print $"  opensearch readiness: ($last_diagnostic) [attempt ($attempt)/90]"
        sleep $poll_interval
    }
    error make {msg: $"OpenSearch did not become functionally ready before its consumers: ($last_diagnostic)"}
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

    # Two narrowly identified resource-health races are transient only when no
    # deterministic marker occurs anywhere in the combined diagnostics: the
    # Prometheus config-reloader sidecar taking a few seconds to become ready
    # after container start, confirmed under two exact wordings (init-container
    # form and stdout10's live main-container form). Argo concatenates all failed
    # resource diagnostics. Validate every line containing a container-health
    # fragment independently and require one complete allowlisted fragment per
    # line; malformed, unknown, duplicated, or mixed failures stay fail-closed.
    let container_health_lines = (
        $normalized
        | lines
        | where {|line| $line | str contains "containers with " }
    )
    if not ($container_health_lines | is-empty) {
        for line in $container_health_lines {
            if (($line | split row "containers with " | length) != 2) {
                return false
            }
            let confirmed_init_race = (
                $line | str ends-with "containers with incomplete status: [init-config-reloader]"
            )
            let confirmed_sidecar_race = (
                $line | str ends-with "containers with unready status: [prometheus config-reloader]"
            )
            if not ($confirmed_init_race or $confirmed_sidecar_race) {
                return false
            }
        }
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

def kubectl_result_is_not_found [result: record] {
    let diagnostic = $"($result.stdout) ($result.stderr)" | str lowercase
    # kubectl's Kubernetes API NotFound errors include the reason in parentheses.
    # Do not match generic "not found": credential-helper/config errors use that
    # wording too and must fail immediately.
    $diagnostic | str contains "(notfound)"
}

def kubectl_wait_is_pending [result: record] {
    let diagnostic = $"($result.stdout) ($result.stderr)" | str lowercase
    (kubectl_result_is_not_found $result) or ($diagnostic | str contains "timed out waiting")
}

# Kubernetes object names are DNS-1123 subdomains. A value that doesn't match
# cannot be a real resource name the apiserver would have assigned, so treat
# it as untrusted and refuse to interpolate it into a request path.
def is_valid_k8s_resource_name [name: string] {
    if ($name | is-empty) or (($name | str length) > 253) {
        return false
    }
    let labels = ($name | split row ".")
    $labels | all {|label|
        let non_empty = not ($label | is-empty)
        let valid_length = (($label | str length) <= 63)
        let valid_characters = (($label | parse --regex '^[a-z0-9]([-a-z0-9]*[a-z0-9])?$' | length) == 1)
        $non_empty and $valid_length and $valid_characters
    }
}

def provider_http_failure_summary [result: record] {
    let diagnostic = $"($result.stdout) ($result.stderr)" | str lowercase
    if ($diagnostic | str contains "forbidden") {
        return "Kubernetes API denied the request (Forbidden)"
    }
    if ($diagnostic | str contains "unauthorized") or ($diagnostic | str contains "unauthenticated") {
        return "Kubernetes API rejected authentication"
    }
    if ($diagnostic | str contains "credential") or ($diagnostic | str contains "kubeconfig") or ($diagnostic | str contains "current-context") {
        return "kubectl credential or configuration error"
    }
    let transport_markers = [
        "connection refused", "connection reset", "no route to host",
        "i/o timeout", "tls handshake timeout", "dial tcp",
        "context deadline exceeded", "server misbehaving", "no such host",
        "transport is closing", "error reading from server: eof",
    ]
    if ($transport_markers | any {|marker| $diagnostic | str contains $marker }) {
        return "Kubernetes API transport error"
    }
    # kubectl and credential helpers are external processes. Their diagnostics
    # are untrusted and can echo credentials in arbitrary formats, so never
    # include raw stdout/stderr in the terminal error.
    "kubectl request failed (details suppressed)"
}

# crossplane-harbor-bootstrap contains provider-http Request objects. Argo sync
# waves order Application creation only; they do not prove that package CRDs
# have been installed. Gate the first Request on the active revision and CRD.
def wait_for_provider_http_ready [] {
    print "  Waiting for provider-http ProviderRevision and Request CRD..."
    for attempt in 1..180 {
        # `kubectl get provider`/`providerrevision` resolves the resource
        # through discovery/RESTMapper, which can still be stale right after
        # the package CRDs install -- it then fails with a generic discovery
        # error (not NotFound) that never recovers on its own. `--raw` hits
        # the pinned pkg.crossplane.io/v1 path directly (group/version/scope
        # confirmed against the Crossplane v2.3.3 upstream CRDs), bypassing
        # discovery entirely.
        let provider_result = (do {
            kubectl get --raw /apis/pkg.crossplane.io/v1/providers/provider-http
        } | complete)
        if $provider_result.exit_code != 0 {
            if not (kubectl_result_is_not_found $provider_result) {
                error make {msg: $"Failed to query provider-http while waiting for readiness: (provider_http_failure_summary $provider_result)"}
            }
        } else {
            let provider = (try { $provider_result.stdout | from json } catch {
                error make {msg: "provider-http returned malformed JSON while waiting for readiness"}
            })
            let revision = ($provider | get -o status.currentRevision | default "")
            if not ($revision | is-empty) {
                if not (is_valid_k8s_resource_name $revision) {
                    error make {msg: "provider-http reported a currentRevision that is not a valid Kubernetes resource name"}
                }
                let revision_result = (do {
                    kubectl get --raw $"/apis/pkg.crossplane.io/v1/providerrevisions/($revision)"
                } | complete)
                if $revision_result.exit_code != 0 {
                    if not (kubectl_result_is_not_found $revision_result) {
                        error make {msg: $"Failed to query the active provider-http revision: (provider_http_failure_summary $revision_result)"}
                    }
                } else {
                    let revision_state = (try { $revision_result.stdout | from json } catch {
                        error make {msg: "The active provider-http revision returned malformed JSON"}
                    })
                    let desired = ($revision_state | get -o spec.desiredState | default "Active")
                    let conditions = ($revision_state | get -o status.conditions | default [])
                    # Crossplane v2.3.3's ProviderRevision never carries a bare
                    # "Healthy" condition -- the revision reconciler only ever
                    # marks RevisionHealthy/RuntimeHealthy on the revision itself;
                    # the aggregate "Healthy" condition is written to the parent
                    # Provider by PackageHealth() (apis/pkg/v1/conditions.go),
                    # which itself requires both to be True. Mirror that here.
                    let revision_healthy = ($conditions | any {|c| ((($c | get -o type | default "") == "RevisionHealthy") and (($c | get -o status | default "") == "True")) })
                    let runtime_healthy = ($conditions | any {|c| ((($c | get -o type | default "") == "RuntimeHealthy") and (($c | get -o status | default "") == "True")) })
                    if ($desired == "Active") and $revision_healthy and $runtime_healthy {
                        let crd_result = (do {
                            kubectl wait --for=condition=Established crd/requests.http.crossplane.io --timeout=5s
                        } | complete)
                        if $crd_result.exit_code == 0 {
                            print "  ✓ provider-http revision is RevisionHealthy and RuntimeHealthy, and requests.http.crossplane.io is Established"
                            return
                        }
                        if not (kubectl_wait_is_pending $crd_result) {
                            error make {msg: "Failed to query the provider-http Request CRD while waiting for readiness"}
                        }
                    }
                }
            }
        }
        sleep 2sec
    }
    error make {msg: "provider-http did not become RevisionHealthy/RuntimeHealthy with an Established Request CRD"}
}

# Issue #285 (stdout12 live evidence, two independent fresh runs): the exact
# three keys the crossplane-harbor-bootstrap Request injects into
# crossplane-system/crossplane-harbor-credentials
# (crossplane/bootstrap/harbor-robot-request.yaml's secretInjectionConfigs).
# Shared by the probe/repair boundary below so both sides always agree on the
# contract.
def harbor_credential_required_keys [] {
    ["name", "secret", "basicAuth"]
}

# Git preserves the checkout's line endings inside Nushell multiline strings.
# Normalize only at the container-command boundary: POSIX `sh -c` requires LF,
# and a CR in `set -e`/`set -eu` is parsed as part of the option token.
def normalize_posix_container_script [script: string] {
    $script
    | str replace --all "\r\n" "\n"
    | str replace --all "\r" "\n"
}

# Runs inside the read-only probe Job (harbor_credential_probe_job). Reports
# per-key presence/non-emptiness, canonical source bytes, and whether basicAuth
# is the byte-exact base64(name:secret) derivation. Credential bytes are read
# only inside this tokenless container and transformed through status-checked
# commands in memory-backed files; only key=true|false lines reach stdout.
def harbor_credential_probe_script [] {
    '#!/bin/sh
set -e
umask 077
root="/var/run/secrets/harbor-credential"
work="${HARBOR_PROBE_WORK:-/work}"
name_snapshot="$work/name-snapshot"
secret_snapshot="$work/secret-snapshot"
basic_snapshot="$work/basic-snapshot"
plain="$work/basic-plain"
encoded="$work/basic-encoded"
expected="$work/basic-expected"
printable_name="$work/name-printable"
printable_secret="$work/secret-printable"
cleanup() {
  rm -f "$name_snapshot" "$secret_snapshot" "$basic_snapshot" \
    "$plain" "$encoded" "$expected" "$printable_name" "$printable_secret" \
    >/dev/null 2>&1 || true
}
trap cleanup 0 1 2 3 15

name_present=false
secret_present=false
basic_present=false
basic_auth_valid=false
revision_stable=false

# Bind every read to one immutable Kubernetes projected-Secret revision.
# Reopening top-level key symlinks would allow ..data to switch mid-check.
start_link=$(readlink "$root/..data" 2>/dev/null) || start_link=""
snapshot_root=""
case "$start_link" in
  ..*/*|"") ;;
  ..*) snapshot_root="$root/$start_link" ;;
esac

if [ -n "$snapshot_root" ]; then
  if cat "$snapshot_root/name" > "$name_snapshot" && [ -s "$name_snapshot" ]; then
    name_present=true
  fi
  if cat "$snapshot_root/secret" > "$secret_snapshot" && [ -s "$secret_snapshot" ]; then
    secret_present=true
  fi
  if cat "$snapshot_root/basicAuth" > "$basic_snapshot" && [ -s "$basic_snapshot" ]; then
    basic_present=true
  fi
  end_link=$(readlink "$root/..data" 2>/dev/null) || end_link=""
  if [ "$end_link" = "$start_link" ]; then
    revision_stable=true
  else
    name_present=false
    secret_present=false
    basic_present=false
  fi
fi

if [ "$revision_stable" = true ] \
    && [ "$name_present" = true ] \
    && [ "$secret_present" = true ] \
    && [ "$basic_present" = true ] \
    && LC_ALL=C tr -cd " -~" < "$name_snapshot" > "$printable_name" \
    && cmp -s "$name_snapshot" "$printable_name" \
    && grep -Eq "^[^\$]+\\\$crossplane-system$" "$name_snapshot" \
    && LC_ALL=C tr -cd " -~" < "$secret_snapshot" > "$printable_secret" \
    && cmp -s "$secret_snapshot" "$printable_secret" \
    && : > "$plain" \
    && cat "$name_snapshot" > "$plain" \
    && printf ":" >> "$plain" \
    && cat "$secret_snapshot" >> "$plain" \
    && base64 "$plain" > "$encoded" \
    && tr -d "\\r\\n" < "$encoded" > "$expected" \
    && cmp -s "$basic_snapshot" "$expected"; then
  basic_auth_valid=true
fi

printf "name=%s\n" "$name_present"
printf "secret=%s\n" "$secret_present"
printf "basicAuth=%s\n" "$basic_present"
printf "basicAuthValid=%s\n" "$basic_auth_valid"
'
    | normalize_posix_container_script $in
}

# A Job whose pod mounts crossplane-harbor-credentials as an *optional*,
# read-only projected Secret volume (Issue #285: the Secret may still be a
# bare shell with zero data keys -- `optional: true` on the volume, verified
# against kubernetes v1.36.1's pinned kubelet source
# (pkg/volume/secret/secret.go MakePayload), skips any individual missing
# *key* without failing volume setup, exactly like a missing Secret). No
# ServiceAccount token is mounted -- this pod never talks to any API.
def harbor_credential_probe_job [] {
    {
        apiVersion: "batch/v1"
        kind: "Job"
        metadata: {
            name: "harbor-credential-probe"
            namespace: "crossplane-system"
        }
        spec: {
            backoffLimit: 0
            template: {
                spec: {
                    automountServiceAccountToken: false
                    restartPolicy: "Never"
                    # The UID is pinned NUMERICALLY on purpose (Issue #285
                    # review finding 8): curlimages/curl declares a
                    # non-numeric image user (`curl_user`, UID 101 in its own
                    # /etc/passwd), and with `runAsNonRoot: true` the kubelet
                    # refuses to start a container whose image user it cannot
                    # prove is non-root. 65534/nobody exists in this image and
                    # needs no write access at all here; fsGroup keeps the
                    # projected credential readable under the 0440 mode below.
                    securityContext: {
                        runAsNonRoot: true
                        runAsUser: 65534
                        runAsGroup: 65534
                        fsGroup: 65534
                        seccompProfile: {type: "RuntimeDefault"}
                    }
                    containers: [
                        {
                            name: "probe"
                            image: "curlimages/curl:8.16.0@sha256:463eaf6072688fe96ac64fa623fe73e1dbe25d8ad6c34404a669ad3ce1f104b6"
                            command: ["sh", "-c", (harbor_credential_probe_script)]
                            securityContext: {
                                allowPrivilegeEscalation: false
                                readOnlyRootFilesystem: true
                                capabilities: {drop: ["ALL"]}
                            }
                            volumeMounts: [
                                {name: "credential", mountPath: "/var/run/secrets/harbor-credential", readOnly: true}
                                {name: "work", mountPath: "/work"}
                            ]
                        }
                    ]
                    volumes: [
                        {
                            name: "credential"
                            secret: {
                                secretName: "crossplane-harbor-credentials"
                                optional: true
                                defaultMode: 288
                                items: [
                                    {key: "name", path: "name"}
                                    {key: "secret", path: "secret"}
                                    {key: "basicAuth", path: "basicAuth"}
                                ]
                            }
                        }
                        {
                            name: "work"
                            emptyDir: {
                                medium: "Memory"
                                sizeLimit: "64Ki"
                            }
                        }
                    ]
                }
            }
        }
    }
}

# Runs the probe Job, reads only its stdout (the true/false lines -- never
# the Secret via the Kubernetes API), and returns a record of key -> bool.
def probe_harbor_credential_keys [] {
    let report = (run_bootstrap_job (harbor_credential_probe_job) "harbor-credential-probe" "60s")
    parse_harbor_credential_probe_output $report
}

# Single owner of the probe/repair Job lifecycle (Issue #285 review finding 3).
#
# These Jobs have fixed names, so a leftover from an earlier run is the
# dangerous case: `kubectl apply` against a *stale completed* Job is a no-op,
# `kubectl wait --for=condition=Complete` is then satisfied instantly by the
# OLD condition, and `kubectl logs` returns the PREVIOUS run's report -- which
# for the probe could report "all keys present" for a credential that is in
# fact still empty. Every step here therefore fails closed:
#
#   1. the pre-delete must succeed (its exit code is checked, not discarded),
#   2. the Job must be *observed absent* afterwards,
#   3. the new Job is `create`d (never `apply`ed), so a surviving object is a
#      hard error instead of a silent reuse,
#   4. the created Job's metadata.uid is captured and re-verified after the
#      wait, so the logs that are returned provably belong to this run's Job.
#
# Issue #285 third review finding 2: every one of the intermediate cleanup
# calls below used to discard both the delete's exit code and the state
# afterward -- a failed or partial deletion (e.g. a still-Terminating Pod)
# could leave a stale Job, or worse for the probe, its Secret-mounting Pod,
# behind while the run pressed on regardless. Cleanup is now a single
# checked-delete-then-verify-absence helper used on *every* path (including
# the Job UID fetch/validation step, which previously had no cleanup call at
# all), and a cleanup failure is itself fatal rather than swallowed.
#
# Returns the Job's stdout. The caller deletes nothing: cleanup happens here.

# Strictly parses a Kubernetes PodList. JSON records missing apiVersion/kind,
# missing items, or carrying non-array items are malformed -- never equivalent
# to an empty namespace.
# PR#287 independent review (round 7): every downstream survivor/target/
# leftover predicate accesses a Pod item's `metadata.name`, `metadata.uid`,
# `metadata.labels`, `metadata.ownerReferences` and `spec.serviceAccountName`
# by cell path. Nushell's `get -o` only suppresses a MISSING column -- it
# still throws whenever an intermediate value has the wrong type (a string
# where a record is expected, a record/foreign element where a list of
# records is expected). Checked here, once, so a malformed item fails the
# PodList closed instead of throwing inside a predicate far away.
def pod_item_is_well_formed [pod: any] {
    if (($pod | describe) | str starts-with "record") == false {
        return false
    }
    let pod_columns = ($pod | columns)
    if ("metadata" in $pod_columns) == false {
        return false
    }
    let metadata = $pod.metadata
    if (($metadata | describe) | str starts-with "record") == false {
        return false
    }
    let metadata_columns = ($metadata | columns)
    let pod_name = ($metadata | get -o name | default null)
    if (($pod_name | describe) != "string") or ($pod_name | str trim | is-empty) {
        return false
    }
    let pod_uid = ($metadata | get -o uid | default null)
    if (($pod_uid | describe) != "string") or ($pod_uid | str trim | is-empty) {
        return false
    }
    let owners = if "ownerReferences" in $metadata_columns {
        $metadata.ownerReferences
    } else {
        []
    }
    let owners_type = ($owners | describe)
    if (($owners_type | str starts-with "list") or ($owners_type | str starts-with "table")) == false {
        return false
    }
    if ($owners | any {|owner| (($owner | describe) | str starts-with "record") == false }) {
        return false
    }
    if ($owners | any {|owner|
        let kind = ($owner | get -o kind | default null)
        let name = ($owner | get -o name | default null)
        let uid = ($owner | get -o uid | default null)
        if ($kind | describe) != "string" {
            true
        } else if ($kind | str trim | is-empty) {
            true
        } else if ($name | describe) != "string" {
            true
        } else if ($name | str trim | is-empty) {
            true
        } else if ($uid | describe) != "string" {
            true
        } else if ($uid | str trim | is-empty) {
            true
        } else if ("controller" in ($owner | columns)) and (($owner.controller | describe) != "bool") {
            true
        } else {
            false
        }
    }) {
        return false
    }
    let labels = if "labels" in $metadata_columns {
        $metadata.labels
    } else {
        {}
    }
    if (($labels | describe) | str starts-with "record") == false {
        return false
    }
    if ("job-name" in ($labels | columns)) and ((($labels | get "job-name" | describe) != "string")) {
        return false
    }
    if ("spec" in $pod_columns) == false {
        return false
    }
    let spec = $pod.spec
    if (($spec | describe) | str starts-with "record") == false {
        return false
    }
    if ("serviceAccountName" in ($spec | columns)) and ((($spec.serviceAccountName | describe) != "string")) {
        return false
    }
    true
}

def parse_pod_list [pods_json: string] {
    let parsed = (try { $pods_json | from json } catch { null })
    if ($parsed | describe | str starts-with "record") == false {
        return {ok: false, items: [], reason: "unparseable PodList"}
    }
    let items = ($parsed | get -o items | default null)
    let item_type = ($items | describe)
    let items_are_array = (($item_type | str starts-with "list") or ($item_type | str starts-with "table"))
    if (($parsed | get -o apiVersion | default "") != "v1") or (($parsed | get -o kind | default "") != "PodList") or (not $items_are_array) {
        return {ok: false, items: [], reason: "malformed PodList"}
    }
    if ($items | any {|pod| not (pod_item_is_well_formed $pod) }) {
        return {ok: false, items: [], reason: "malformed pod item in PodList"}
    }
    {ok: true, items: $items, reason: ""}
}

# Fetches the typed Core API PodList directly so kubectl discovery or output
# negotiation cannot wrap the response in a generic kind=List. The namespace
# and resource path are fixed security-boundary constants, never input.
def get_crossplane_system_pod_list [] {
    do {
        kubectl get --raw "/api/v1/namespaces/crossplane-system/pods"
    } | complete
}

# Deletes a Job and positively re-verifies that neither the Job nor any Pod
# traceable to this run remains. Mutable labels are only a fallback for the
# pre-create cleanup; after creation the immutable Job/Pod identities are
# carried into this boundary explicitly.
def cleanup_bootstrap_job_verified [
    job_name: string,
    job_uid?: string,
    pod_name?: string,
    pod_uid?: string,
] {
    let tracked_job_uid = ($job_uid | default "")
    let tracked_pod_name = ($pod_name | default "")
    let tracked_pod_uid = ($pod_uid | default "")
    let delete_result = (do {
        kubectl delete job $job_name -n crossplane-system --ignore-not-found --wait=true --cascade=foreground
    } | complete)
    if $delete_result.exit_code != 0 {
        return {ok: false, reason: $"failed to delete job/($job_name)"}
    }

    let residual_job = (do {
        kubectl get job $job_name -n crossplane-system --ignore-not-found -o name
    } | complete)
    if $residual_job.exit_code != 0 {
        return {ok: false, reason: $"failed to confirm job/($job_name) is absent"}
    }
    if not ($residual_job.stdout | str trim | is-empty) {
        return {ok: false, reason: $"job/($job_name) still exists after deletion"}
    }

    # List the namespace, not only a mutable `job-name` label selector. This
    # catches an orphan or relabelled Pod by its direct identity or immutable
    # owner UID. Before create, when no UID exists yet, the label remains a
    # conservative stale-resource fallback.
    let residual_pods = (get_crossplane_system_pod_list)
    if $residual_pods.exit_code != 0 {
        return {ok: false, reason: $"failed to confirm the pods of job/($job_name) are absent"}
    }
    let parsed_pods = (parse_pod_list $residual_pods.stdout)
    if not $parsed_pods.ok {
        return {ok: false, reason: $"could not verify the residual PodList for job/($job_name): ($parsed_pods.reason)"}
    }
    let items = $parsed_pods.items
    let survivors = ($items | where {|pod|
        let name = ($pod | get -o metadata.name | default "")
        let uid = ($pod | get -o metadata.uid | default "")
        let labels = ($pod | get -o metadata.labels | default {})
        let owners = ($pod | get -o metadata.ownerReferences | default [])
        let direct_identity = (($tracked_pod_name | is-not-empty)
            and ($name == $tracked_pod_name)
            and (($tracked_pod_uid | is-empty) or ($uid == $tracked_pod_uid)))
        let reused_tracked_name = (($tracked_pod_name | is-not-empty) and ($name == $tracked_pod_name))
        let immutable_owner = (($tracked_job_uid | is-not-empty) and ($owners | any {|owner|
            ((($owner | get -o kind | default "") == "Job") and (($owner | get -o uid | default "") == $tracked_job_uid))
        }))
        # PR#287 independent review finding 1: this job_name is fixed and can
        # only ever belong to one logical Job across incarnations, so a
        # controller Job owner reference naming it is itself sufficient proof
        # of survivorship -- independent of UID. Without this, a Pod owned by
        # an EARLIER incarnation of this same fixed-name Job escaped both
        # pre-cleanup (no UID tracked yet) and post-cleanup (the tracked UID
        # belongs to the NEW incarnation and can never match the old owner)
        # whenever its mutable job-name label had since been changed.
        let owned_by_job_name = ($owners | any {|owner|
            ((($owner | get -o kind | default "") == "Job")
                and (($owner | get -o name | default "") == $job_name)
                and (($owner | get -o controller | default false) == true))
        })
        let fallback_label = (($labels | get -o job-name | default "") == $job_name)
        $direct_identity or $reused_tracked_name or $immutable_owner or $owned_by_job_name or $fallback_label
    })
    if ($survivors | length) > 0 {
        return {ok: false, reason: $"a pod traceable to job/($job_name) still exists after deletion"}
    }

    {ok: true, reason: ""}
}

# Cleans up the Job (verified), then always raises -- with the cleanup
# outcome folded into the message if cleanup itself failed, so a leftover
# Job/Pod is never silently masked by the error that triggered it.
def fail_bootstrap_job_after_cleanup [
    job_name: string,
    primary_msg: string,
    job_uid?: string,
    pod_name?: string,
    pod_uid?: string,
] {
    let cleanup = (cleanup_bootstrap_job_verified $job_name ($job_uid | default "") ($pod_name | default "") ($pod_uid | default ""))
    if not $cleanup.ok {
        error make {msg: $"($primary_msg); additionally, cleanup failed: ($cleanup.reason)"}
    }
    error make {msg: $primary_msg}
}

# Validates a fixed-name Job lookup against the immutable UID returned by the
# create response. Pure and fail-closed so malformed/missing objects are never
# treated as continuity.
def job_identity_matches [job_json: string, expected_name: string, expected_uid: string] {
    let parsed = (try { $job_json | from json } catch { null })
    if ($parsed | describe | str starts-with "record") == false {
        return false
    }
    ((($parsed | get -o kind | default "") == "Job") and (($parsed | get -o metadata.name | default "") == $expected_name) and (($parsed | get -o metadata.uid | default "") == $expected_uid) and ($expected_uid | is-not-empty))
}

def run_bootstrap_job [
    manifest: record,
    job_name: string,
    timeout: string,
    startup_timeout?: duration,
] {
    $env.KUBECONFIG = $KUBECONFIG_PATH

    let pre_cleanup = (cleanup_bootstrap_job_verified $job_name "" "" "")
    if not $pre_cleanup.ok {
        error make {msg: $"Failed to remove a previous ($job_name) Job before starting a new one: ($pre_cleanup.reason)"}
    }

    # The create response is the only atomic source of the object identity this
    # invocation created. Never create by name and then bind to a separate
    # name-based GET that may already resolve a replacement object.
    let create_result = (do { $manifest | to json | kubectl create -f - -o json } | complete)
    if $create_result.exit_code != 0 {
        fail_bootstrap_job_after_cleanup $job_name $"Failed to create the ($job_name) Job" "" "" ""
    }
    let created = (try { $create_result.stdout | from json } catch { null })
    if ($created | describe | str starts-with "record") == false {
        fail_bootstrap_job_after_cleanup $job_name $"Failed to parse the identity of the ($job_name) Job that was just created" "" "" ""
    }
    let created_name = ($created | get -o metadata.name | default "")
    let created_uid = ($created | get -o metadata.uid | default "")
    if (($created | get -o kind | default "") != "Job") or ($created_name != $job_name) or ($created_uid | is-empty) {
        fail_bootstrap_job_after_cleanup $job_name $"The created ($job_name) Job returned an invalid name or UID" $created_uid "" ""
    }

    # Image acquisition, scheduling and container startup have a separate
    # budget from the caller's functional completion timeout. A fresh node
    # may legitimately spend longer pulling the pinned image than the probe's
    # 60-second execution budget, so that functional clock starts only after
    # the exact Pod controlled by this Job is Running or terminal.
    let startup_budget = ($startup_timeout | default 300sec)
    let startup_started = (date now)
    mut tracked_pod_name = ""
    mut tracked_pod_uid = ""
    loop {
        let startup_pods_result = (get_crossplane_system_pod_list)
        if $startup_pods_result.exit_code != 0 {
            fail_bootstrap_job_after_cleanup $job_name $"Failed to list the pods of the ($job_name) Job while waiting for startup" $created_uid $tracked_pod_name $tracked_pod_uid
        }
        let startup_pods = (parse_pod_list $startup_pods_result.stdout)
        if not $startup_pods.ok {
            fail_bootstrap_job_after_cleanup $job_name $"Refusing to trust the ($job_name) Job startup state: ($startup_pods.reason)" $created_uid $tracked_pod_name $tracked_pod_uid
        }
        let startup_owned = (select_owned_pod $startup_pods_result.stdout $job_name $created_uid)
        if not $startup_owned.ok {
            if ($startup_owned.reason != "no pod is owned by the Job this run created") or ($tracked_pod_name | is-not-empty) {
                fail_bootstrap_job_after_cleanup $job_name $"Refusing to trust the ($job_name) Job startup state: ($startup_owned.reason)" $created_uid $tracked_pod_name $tracked_pod_uid
            }
        } else {
            if ($tracked_pod_name | is-empty) {
                $tracked_pod_name = $startup_owned.name
                $tracked_pod_uid = $startup_owned.uid
            } else if ($startup_owned.name != $tracked_pod_name) or ($startup_owned.uid != $tracked_pod_uid) {
                fail_bootstrap_job_after_cleanup $job_name $"The ($job_name) Job's pod identity changed while waiting for startup" $created_uid $tracked_pod_name $tracked_pod_uid
            }

            let pod = (
                $startup_pods.items
                | where {|item|
                    ((($item | get -o metadata.name | default "") == $tracked_pod_name)
                        and (($item | get -o metadata.uid | default "") == $tracked_pod_uid))
                }
                | first
            )
            let containers = ($pod | get -o spec.containers | default null)
            let containers_type = ($containers | describe)
            if (
                ((($containers_type | str starts-with "list") or ($containers_type | str starts-with "table")) == false)
                    or (($containers | length) != 1)
                    or (((($containers | first) | describe) | str starts-with "record") == false)
                    or (((($containers | first) | get -o name | default null) | describe) != "string")
                    or (((($containers | first) | get -o name | default "") | str trim | is-empty))
            ) {
                fail_bootstrap_job_after_cleanup $job_name $"The ($job_name) Job's pod returned a malformed workload container" $created_uid $tracked_pod_name $tracked_pod_uid
            }
            let status = ($pod | get -o status | default null)
            if (($status | describe) | str starts-with "record") == false {
                fail_bootstrap_job_after_cleanup $job_name $"The ($job_name) Job's pod returned a malformed startup status" $created_uid $tracked_pod_name $tracked_pod_uid
            }
            let phase = ($status | get -o phase | default null)
            if (($phase | describe) != "string") or (not ($phase in ["Pending" "Running" "Succeeded" "Failed"])) {
                fail_bootstrap_job_after_cleanup $job_name $"The ($job_name) Job's pod returned a malformed startup phase" $created_uid $tracked_pod_name $tracked_pod_uid
            }
            if $phase == "Failed" {
                fail_bootstrap_job_after_cleanup $job_name $"The ($job_name) Job's pod failed during startup" $created_uid $tracked_pod_name $tracked_pod_uid
            }
            if $phase in ["Running" "Succeeded"] {
                break
            }
        }

        if (((date now) - $startup_started) >= $startup_budget) {
            fail_bootstrap_job_after_cleanup $job_name $"The ($job_name) Job did not start within its acquisition budget" $created_uid $tracked_pod_name $tracked_pod_uid
        }
        sleep 1sec
    }

    let wait_result = (do {
        kubectl wait --for=condition=Complete $"job/($job_name)" -n crossplane-system $"--timeout=($timeout)"
    } | complete)
    if $wait_result.exit_code != 0 {
        fail_bootstrap_job_after_cleanup $job_name $"The ($job_name) Job did not complete successfully" $created_uid $tracked_pod_name $tracked_pod_uid
    }

    # Startup discovery already selected the one Pod by immutable Job owner
    # UID. Carry that exact Pod identity across completion and into every
    # subsequent revalidation and cleanup path.
    let owned = {ok: true, name: $tracked_pod_name, uid: $tracked_pod_uid, reason: ""}

    # Immediately before log retrieval, prove both fixed-name Job continuity
    # and exact Pod continuity. Any lookup or identity mismatch is fatal.
    let pre_log_job_result = (do {
        kubectl get job $job_name -n crossplane-system -o json
    } | complete)
    if ($pre_log_job_result.exit_code != 0) or (not (job_identity_matches $pre_log_job_result.stdout $job_name $created_uid)) {
        fail_bootstrap_job_after_cleanup $job_name $"The ($job_name) Job identity changed before reading logs" $created_uid $owned.name $owned.uid
    }
    let relist_result = (get_crossplane_system_pod_list)
    if $relist_result.exit_code != 0 {
        fail_bootstrap_job_after_cleanup $job_name $"Failed to re-verify the ($job_name) Job's pod before reading logs" $created_uid $owned.name $owned.uid
    }
    let reowned = (select_owned_pod $relist_result.stdout $job_name $created_uid)
    if (not $reowned.ok) or ($reowned.uid != $owned.uid) or ($reowned.name != $owned.name) {
        fail_bootstrap_job_after_cleanup $job_name $"The ($job_name) Job's pod could not be re-verified by name, owner and UID before reading logs" $created_uid $owned.name $owned.uid
    }

    let logs_result = (do {
        kubectl logs $owned.name -n crossplane-system
    } | complete)
    if $logs_result.exit_code != 0 {
        fail_bootstrap_job_after_cleanup $job_name $"Failed to read the ($job_name) Job result" $created_uid $owned.name $owned.uid
    }

    # `kubectl logs` resolves a mutable pod name. Buffer its output, then prove
    # Job and Pod continuity again before returning a single byte to the caller.
    let post_log_job_result = (do {
        kubectl get job $job_name -n crossplane-system -o json
    } | complete)
    if ($post_log_job_result.exit_code != 0) or (not (job_identity_matches $post_log_job_result.stdout $job_name $created_uid)) {
        fail_bootstrap_job_after_cleanup $job_name $"The ($job_name) Job identity changed after reading logs" $created_uid $owned.name $owned.uid
    }
    let post_log_relist_result = (get_crossplane_system_pod_list)
    if $post_log_relist_result.exit_code != 0 {
        fail_bootstrap_job_after_cleanup $job_name $"Failed to re-verify the ($job_name) Job's pod after reading logs" $created_uid $owned.name $owned.uid
    }
    let post_log_reowned = (select_owned_pod $post_log_relist_result.stdout $job_name $created_uid)
    if (not $post_log_reowned.ok) or ($post_log_reowned.uid != $owned.uid) or ($post_log_reowned.name != $owned.name) {
        fail_bootstrap_job_after_cleanup $job_name $"The ($job_name) Job's pod could not be re-verified by name, owner UID and pod UID after reading logs" $created_uid $owned.name $owned.uid
    }

    let cleanup = (cleanup_bootstrap_job_verified $job_name $created_uid $owned.name $owned.uid)
    if not $cleanup.ok {
        error make {msg: $"The ($job_name) Job produced a result, but cleanup afterward failed: ($cleanup.reason)"}
    }
    $logs_result.stdout
}

# Picks the single pod controlled by the Job UID this run created, from a
# `kubectl get pods -o json` payload. Pure and fail-closed: a pod owned by a
# *replacement* Job, an ambiguous set, an orphan, a non-Job owner or an
# unparseable payload all yield ok=false with a reason, so a caller can never
# accept another Job's output.
#
# Issue #285 third review finding 2: the returned `name` alone let a caller
# only ever re-resolve the pod by its (mutable) name. `uid` is now also
# returned so a caller can bind logs to the exact pod object selected here --
# not merely to whichever pod currently answers to that name.
def select_owned_pod [pods_json: string, job_name: string, job_uid: string] {
    let parsed = (parse_pod_list $pods_json)
    if not $parsed.ok {
        return {ok: false, name: "", uid: "", reason: $parsed.reason}
    }
    let items = $parsed.items
    let owned = ($items | where {|pod|
        ($pod | get -o metadata.ownerReferences | default [])
        | any {|owner|
            ((($owner | get -o kind | default "") == "Job")
                and (($owner | get -o name | default "") == $job_name)
                and (($owner | get -o uid | default "") == $job_uid)
                and (($owner | get -o controller | default false) == true))
        }
    })
    if ($owned | length) == 0 {
        return {ok: false, name: "", uid: "", reason: "no pod is owned by the Job this run created"}
    }
    if ($owned | length) > 1 {
        return {ok: false, name: "", uid: "", reason: "the Job owns more than one pod"}
    }
    let name = ($owned | first | get -o metadata.name | default "")
    if ($name | is-empty) {
        return {ok: false, name: "", uid: "", reason: "the owned pod has no name"}
    }
    let uid = ($owned | first | get -o metadata.uid | default "")
    if ($uid | is-empty) {
        return {ok: false, name: "", uid: "", reason: "the owned pod has no uid"}
    }
    {ok: true, name: $name, uid: $uid, reason: ""}
}

# Turns the probe Job's `<key>=true|false` report into a record of booleans.
# Fail-closed by construction: every required key starts false, and only a
# literal `true` for a known key flips it, so truncated, reordered, noisy or
# unknown output can never be read as "credential present".
#
# `upsert` (never `insert`) is required for the second write: the keys are
# pre-seeded above, and Nushell 0.114.1 raises `column_already_exists` when
# `insert` targets an existing column -- which crashed every probe at runtime
# while source-level tests still passed.
def parse_harbor_credential_probe_output [output: string] {
    let probe_fields = ((harbor_credential_required_keys) | append "basicAuthValid")
    mut result = {}
    mut counts = {}
    for key in $probe_fields {
        $result = ($result | upsert $key false)
        $counts = ($counts | upsert $key 0)
    }

    let report_lines = ($output | lines)
    mut canonical = (($report_lines | length) == ($probe_fields | length))
    for line in $report_lines {
        let parsed = ($line | parse "{key}={value}")
        if ($parsed | length) != 1 {
            $canonical = false
        } else {
            let key = ($parsed | get key | first)
            let value = ($parsed | get value | first)
            if (not ($key in $probe_fields)) or (not ($value in ["true" "false"])) {
                $canonical = false
            } else {
                let count = (($counts | get $key) + 1)
                $counts = ($counts | upsert $key $count)
                if $count != 1 {
                    $canonical = false
                }
                $result = ($result | upsert $key ($value == "true"))
            }
        }
    }
    for key in $probe_fields {
        if ($counts | get $key) != 1 {
            $canonical = false
        }
    }

    if $canonical { $result } else {
        mut failed = {}
        for key in $probe_fields {
            $failed = ($failed | upsert $key false)
        }
        $failed
    }
}

# Keys the probe reported as missing/empty or structurally inconsistent. A
# present but invalid derived basicAuth is classified as basicAuth drift so the
# existing bounded transactional recovery rotates and persists all three keys.
def harbor_credential_missing_keys [probe: record] {
    (harbor_credential_required_keys) | where {|key|
        let present = ($probe | get -o $key | default false)
        let derived_valid = ($probe | get -o basicAuthValid | default false)
        (not $present) or (($key == "basicAuth") and (not $derived_valid))
    }
}

# Ensures crossplane-system/crossplane-harbor-credentials exists as a Secret
# (never overwriting or pruning any provider-owned data key already there --
# only used on the genuinely-absent path, before the first probe ever runs).
def ensure_harbor_credential_secret_shell [] {
    $env.KUBECONFIG = $KUBECONFIG_PATH
    let exists_result = (do {
        kubectl get secret crossplane-harbor-credentials -n crossplane-system --ignore-not-found -o name
    } | complete)
    if $exists_result.exit_code != 0 {
        error make {msg: "Failed to inspect crossplane-harbor-credentials before probing"}
    }
    if ($exists_result.stdout | str trim | is-empty) {
        let manifest = {
            apiVersion: "v1"
            kind: "Secret"
            metadata: {name: "crossplane-harbor-credentials", namespace: "crossplane-system"}
            type: "Opaque"
        }
        let create_result = (do { $manifest | to json | kubectl create -f - } | complete)
        if $create_result.exit_code != 0 {
            error make {msg: "Failed to create the crossplane-harbor-credentials Secret shell"}
        }
    }
}

# Least-privilege, namespaced-only RBAC for the recovery Job below: `get` on
# exactly the Harbor admin Basic-auth Secret (harbor namespace) needed to
# authenticate to Harbor's API, `get`+`patch` on exactly the target
# credential Secret (crossplane-system) needed for the optimistic-concurrency
# readback and the merge-patch write. No ClusterRole, no list/watch/delete,
# no wildcard verb -- torn down again immediately after the recovery Job
# finishes (repair_harbor_credential_secret).
def harbor_credential_recovery_rbac [] {
    'apiVersion: v1
kind: ServiceAccount
metadata:
  name: harbor-credential-recovery
  namespace: crossplane-system
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: harbor-credential-recovery
  namespace: crossplane-system
rules:
  - apiGroups: [""]
    resources: ["secrets"]
    resourceNames: ["crossplane-harbor-credentials"]
    verbs: ["get"]
  - apiGroups: [""]
    resources: ["secrets"]
    resourceNames: ["crossplane-harbor-credentials"]
    verbs: ["patch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: harbor-credential-recovery
  namespace: crossplane-system
subjects:
  - kind: ServiceAccount
    name: harbor-credential-recovery
    namespace: crossplane-system
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: harbor-credential-recovery
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: harbor-credential-recovery-admin-secret
  namespace: harbor
rules:
  - apiGroups: [""]
    resources: ["secrets"]
    resourceNames: ["harbor-admin-basic-auth"]
    verbs: ["get"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: harbor-credential-recovery-admin-secret
  namespace: harbor
subjects:
  - kind: ServiceAccount
    name: harbor-credential-recovery
    namespace: crossplane-system
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: harbor-credential-recovery-admin-secret
'
}

# The exact least-privilege permission set the declarative bootstrap Request
# (crossplane/bootstrap/harbor-robot-request.yaml) grants this robot. The
# recovery compares Harbor's live permission set against this before rotating
# anything -- an over-privileged or drifted robot must never be refreshed and
# handed to the platform. Kept byte-identical to that manifest's payload by
# platform/tests/test_harbor_bootstrap_credential_recovery.py.
def harbor_robot_expected_permissions [] {
    r#'[{"kind":"system","namespace":"/","access":[{"resource":"project","action":"create"}]},{"kind":"project","namespace":"*","access":[{"resource":"robot","action":"create"},{"resource":"robot","action":"read"},{"resource":"artifact","action":"read"},{"resource":"repository","action":"push"},{"resource":"repository","action":"pull"}]}]'#
}

# Structural jq selection of the bootstrap robot from an accumulated (all
# pages) Harbor robot list. Issue #285 review finding 6/7:
#
#  * identity is EXACT and requires Harbor's nonempty display prefix. Harbor
#    stores the robot under an admin-configurable prefix (`robot$` by default;
#    `populate()` in goharbor/harbor v2.15.1 src/controller/robot/controller.go
#    adds it to responses). A response must contain a nonempty prefix followed
#    by `$` (`^[^$]+\$`) before that prefix is stripped and the remainder
#    compared exactly. Decoys and unprefixed responses therefore fail closed.
#  * the complete permission set is compared exactly (order-insensitively,
#    including Harbor's effect-sensitive subset key; an omitted effect
#    normalizes only to the empty string), so any extra, missing or nonempty
#    effect fails closed as permission drift.
#  * anything ambiguous, absent, mistyped or drifted returns a reason instead
#    of an id, and never any credential material.
def harbor_robot_selector_jq [] {
    r#'def hasprefix: ((.name // null) | type) == "string" and (.name | test("^[^$]+\\$"));
def canonical: (.name // "") | sub("^[^$]+\\$"; "");
def hasArrayOfObjects: (type == "array") and (all(type == "object"));
def normperms:
  if (hasArrayOfObjects | not) then null else
    [ .[] | if ((.access) | hasArrayOfObjects | not) then null
            else {kind: .kind, namespace: .namespace, access: ([ .access[] | {resource: .resource, action: .action, effect: (.effect // "")} ] | sort)}
            end ] | sort
  end;
[ .[] | select((.level == "system") and hasprefix and (canonical == $name)) ] as $matches
| if ($matches | length) == 0 then {ok: false, reason: "no-match", matches: 0}
  elif ($matches | length) > 1 then {ok: false, reason: "ambiguous", matches: ($matches | length)}
  else ($matches[0]) as $r
    | if (($r.id | type) != "number") then {ok: false, reason: "invalid-id", matches: 1}
      elif (($r.permissions // []) | normperms) == ($expected | normperms)
      then {ok: true, id: $r.id, name: $r.name, matches: 1}
      else {ok: false, reason: "permission-drift", matches: 1}
      end
  end'#
}

# The provider-http Request can remain Ready=True while its asynchronous UPDATE
# has not reached Harbor yet. Parse only its latest secret-free Observe response
# and accept it when the current generation is observed, HTTP is 200, and the
# one canonical system robot has the exact least-privilege permission set.
# provider-http v1.0.14 may store response.body as either JSON text or an
# already-decoded value, so both representations are handled explicitly.
def harbor_robot_observe_response_ready [request_json: string] {
    let request = (try { $request_json | from json } catch { return false })
    if not (($request | describe) | str starts-with "record") {
        return false
    }

    let generation = ($request | get -o metadata.generation)
    if (($generation | describe) != "int") or $generation <= 0 {
        return false
    }
    let conditions = ($request | get -o status.conditions | default [])
    let conditions_type = ($conditions | describe)
    if not (($conditions_type | str starts-with "list") or ($conditions_type | str starts-with "table")) {
        return false
    }
    let synced = ($conditions | where {|condition|
        ((($condition | describe) | str starts-with "record") and (($condition | get -o type | default "") == "Synced") and (($condition | get -o status | default "") == "True") and (($condition | get -o observedGeneration) == $generation))
    })
    if ($synced | length) != 1 {
        return false
    }

    let status_code = ($request | get -o status.response.statusCode)
    if (($status_code | describe) != "int") or $status_code != 200 {
        return false
    }
    let raw_body = ($request | get -o status.response.body)
    let body = if (($raw_body | describe) == "string") {
        try { $raw_body | from json } catch { return false }
    } else {
        $raw_body
    }
    let body_type = ($body | describe)
    if not (($body_type | str starts-with "list") or ($body_type | str starts-with "table")) {
        return false
    }
    if ($body | length) != 1 {
        return false
    }

    let matches = ($body | where {|robot|
        ((($robot | describe) | str starts-with "record") and (($robot | get -o level | default "") == "system") and (($robot | get -o name | default "") =~ '^[^$]+\$crossplane-system$'))
    })
    if ($matches | length) != 1 {
        return false
    }
    let robot = ($matches | first)
    let robot_id = ($robot | get -o id)
    if (($robot_id | describe) != "int") or $robot_id <= 0 {
        return false
    }

    let normalize_permissions = {|permissions|
        let permissions_type = ($permissions | describe)
        if not (($permissions_type | str starts-with "list") or ($permissions_type | str starts-with "table")) {
            return null
        }
        mut normalized = []
        for permission in $permissions {
            if not (($permission | describe) | str starts-with "record") {
                return null
            }
            let kind = ($permission | get -o kind)
            let namespace = ($permission | get -o namespace)
            let access = ($permission | get -o access)
            let access_type = ($access | describe)
            if (($kind | describe) != "string") or (($namespace | describe) != "string") or not (($access_type | str starts-with "list") or ($access_type | str starts-with "table")) {
                return null
            }
            mut normalized_access = []
            for entry in $access {
                if not (($entry | describe) | str starts-with "record") {
                    return null
                }
                let resource = ($entry | get -o resource)
                let action = ($entry | get -o action)
                let effect = if "effect" in ($entry | columns) {
                    $entry | get effect
                } else {
                    ""
                }
                if (($resource | describe) != "string") or (($action | describe) != "string") or (($effect | describe) != "string") {
                    return null
                }
                $normalized_access = ($normalized_access | append ([$resource $action $effect] | to json -r))
            }
            let permission_record = {
                kind: $kind
                namespace: $namespace
                access: ($normalized_access | sort)
            }
            $normalized = ($normalized | append ($permission_record | to json -r))
        }
        $normalized | sort
    }

    let actual = (do $normalize_permissions ($robot | get -o permissions))
    let expected_permissions = (try { harbor_robot_expected_permissions | from json } catch { return false })
    let expected = (do $normalize_permissions $expected_permissions)
    if ($actual == null) or ($expected == null) {
        return false
    }
    $actual == $expected
}

# Bound the asynchronous provider-http UPDATE/OBSERVE window before the
# credential gate is allowed to rotate anything. Request conditions alone are
# insufficient: Ready remained true for ~50 seconds before Harbor received the
# permission PUT in the Issue #301 upgrade runtime.
def wait_for_harbor_robot_permissions_ready [] {
    $env.KUBECONFIG = $KUBECONFIG_PATH
    print "  Waiting for Harbor bootstrap robot permission convergence..."
    for attempt in 1..45 {
        let request_result = (do {
            kubectl --request-timeout=1s get request.http.crossplane.io harbor-crossplane-system-robot -o json
        } | complete)
        if ($request_result.exit_code == 0) and (harbor_robot_observe_response_ready $request_result.stdout) {
            print $"(ansi green)  ✓ Harbor bootstrap robot permissions converged(ansi reset)"
            return
        }
        if $attempt > 40 {
            print $"  Harbor bootstrap robot permissions pending [attempt ($attempt)/45]"
        }
        if $attempt < 45 {
            sleep 5sec
        }
    }
    error make {msg: "Harbor bootstrap robot permissions did not converge to the exact least-privilege contract within 5 minutes"}
}

# Runs entirely inside the recovery Job. Refreshes the EXISTING bootstrap
# robot's secret (goharbor/harbor v2.15.1 PATCH /robots/{id} -> RefreshSec,
# src/server/v2.0/handler/robot.go) instead of creating a second one --
# re-POSTing would collide with Harbor's own
# `unique_robot UNIQUE(name, project_id)` constraint anyway.
#
# Transaction order is deliberate (Issue #285 review finding 4): a Harbor
# rotation is irreversible -- Harbor mints a new secret and forgets the old
# one -- so the write target (the Kubernetes Secret and its resourceVersion)
# and the robot's exact identity/permissions are read and validated FIRST.
# Only then is the secret rotated, immediately conditional-patched under that
# resourceVersion, and read back for byte-exact equality. A 409 (someone else
# wrote concurrently) restarts the whole guarded transaction, bounded.
#
# Every credential value lives only in the memory-backed workspace, reaches
# Harbor/Kubernetes only through curl `--config` header files or
# `--data-binary @file`, and is never placed in argv, stdout or a host path.
def harbor_credential_repair_script [] {
    let template = r#'set -eu
umask 077

HARBOR_API="https://digiorg.local/api/v2.0"
K8S_API="https://kubernetes.default.svc"
HARBOR_CACERT="/var/run/secrets/digiorg-ca/ca.crt"
K8S_CACERT="/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
K8S_TOKEN_FILE="/var/run/secrets/kubernetes.io/serviceaccount/token"
WORKDIR="/workspace"
TARGET_SECRET_URL="$K8S_API/api/v1/namespaces/crossplane-system/secrets/crossplane-harbor-credentials"
ROBOT_NAME="crossplane-system"
MAX_ATTEMPTS=3
MAX_PAGES=20
PAGE_SIZE=100

expected_permissions_file="$WORKDIR/expected-permissions.json"
selector_file="$WORKDIR/selector.jq"

# Reads Harbor's authoritative X-Total-Count from a captured header block.
#
# Header parsing is LINE-WISE on purpose. The previous implementation piped
# the block through `tr -d` of newlines first, which collapsed every header
# onto a single line so the field test could never match -- the total came
# back empty and an empty total was then accepted, letting a truncated robot
# list be trusted (fail-open). Only CR is stripped now, and the result must be
# exactly one syntactically valid, non-negative integer: a missing, malformed,
# negative or ambiguous/duplicated total is a hard error, never a shrug.
parse_total_count() {
  headers_file="$1"
  # Issue #285 third review finding 4: `awk '{print $2}'` field-splits on
  # whitespace, so it silently drops everything after the first token
  # (`X-Total-Count: 42 garbage` was accepted as `42`) and cannot match the
  # zero-OWS form `X-Total-Count:42` (RFC 9110 5.5 permits no space after
  # the colon; there $2 is empty). The complete value after the first colon
  # is now taken and trimmed of only leading/trailing OWS, so both forms
  # parse correctly and any non-numeric remainder fails the regex below.
  totals=$(tr -d '\r' < "$headers_file" \
    | awk '{
        line = $0
        colon = index(line, ":")
        if (colon == 0) next
        name = tolower(substr(line, 1, colon - 1))
        if (name != "x-total-count") next
        value = substr(line, colon + 1)
        gsub(/^[ \t]+/, "", value)
        gsub(/[ \t]+$/, "", value)
        print value
      }')
  if [ -z "$totals" ]; then
    echo "ERROR: Harbor response carried no X-Total-Count header" >&2
    return 1
  fi
  total_lines=$(printf '%s\n' "$totals" | wc -l | tr -d ' ')
  if [ "$total_lines" != "1" ]; then
    echo "ERROR: Harbor response carried $total_lines X-Total-Count headers" >&2
    return 1
  fi
  if ! printf '%s' "$totals" | grep -Eq '^[0-9]+$'; then
    echo "ERROR: Harbor reported a malformed X-Total-Count" >&2
    return 1
  fi
  printf '%s' "$totals"
}

# Collect the exact system-level robot candidate. Harbor v2.15.1's bare
# `Name=value` syntax is an exact database-side filter; `Name=~value` is fuzzy
# and is intentionally forbidden. Pagination/total reconciliation remains
# fail-closed even though an exact canonical name should produce at most one
# stored robot.
#
# Issue #285 third review finding 3: the previous version treated "the page
# I just fetched came back short of PAGE_SIZE" as the sole loop-exit signal,
# and treated "the loop counter walked past MAX_PAGES" as the sole overflow
# signal -- so a result set of EXACTLY MAX_PAGES*PAGE_SIZE (every one of the
# 20 allowed pages full) advanced the counter to MAX_PAGES+1 after the last
# full page and was then rejected as "exceeded", even though it had already
# collected the complete, authoritative total. Termination is now driven by
# the running collected count against the authoritative total itself: once
# collected reaches total the loop stops (whether or not that page was
# full), and a short page reached *before* the total is met is a genuine
# inconsistency, not silently accepted as "done" -- so overflow (a total
# that genuinely cannot fit in MAX_PAGES*PAGE_SIZE) is the only remaining way
# to exhaust the loop, and only fires strictly above the bound.
collect_robots() {
  page=1
  : > "$WORKDIR/pages.json"
  total=""
  collected=0
  while [ "$page" -le "$MAX_PAGES" ]; do
    headers="$WORKDIR/robots-headers-$page.txt"
    body="$WORKDIR/robots-page-$page.json"
    curl --config "$harbor_auth_cfg" --cacert "$HARBOR_CACERT" -fsS -D "$headers" \
      "$HARBOR_API/robots?q=Name=$ROBOT_NAME&page=$page&page_size=$PAGE_SIZE" -o "$body"
    if ! jq -e 'type == "array"' "$body" > /dev/null 2>&1; then
      echo "ERROR: Harbor returned a malformed robot list on page $page" >&2
      return 1
    fi
    if [ -z "$total" ]; then
      total=$(parse_total_count "$headers") || return 1
    fi
    cat "$body" >> "$WORKDIR/pages.json"
    count=$(jq 'length' "$body")
    collected=$((collected + count))
    if [ "$collected" -ge "$total" ]; then
      break
    fi
    if [ "$count" -lt "$PAGE_SIZE" ]; then
      echo "ERROR: Harbor returned a short page ($count) on page $page before reaching the reported total ($total)" >&2
      return 1
    fi
    page=$((page + 1))
  done
  jq -s 'add // []' "$WORKDIR/pages.json" > "$WORKDIR/robots.json"
  collected_final=$(jq 'length' "$WORKDIR/robots.json")
  if [ -z "$total" ]; then
    echo "ERROR: Harbor never reported an authoritative X-Total-Count" >&2
    return 1
  fi
  if [ "$collected_final" -ne "$total" ]; then
    if [ "$page" -ge "$MAX_PAGES" ]; then
      echo "ERROR: Harbor robot list exceeded $MAX_PAGES pages ($total robots reported)" >&2
    else
      echo "ERROR: collected $collected_final robots but Harbor reported $total" >&2
    fi
    return 1
  fi
  return 0
}

encode_base64_file() {
  input_file="$1"
  output_file="$2"
  raw_file="$output_file.raw"
  if ! base64 < "$input_file" > "$raw_file"; then
    echo "ERROR: failed to base64-encode recovered credential material" >&2
    return 1
  fi
  if ! LC_ALL=C tr -cd 'A-Za-z0-9+/=' < "$raw_file" > "$output_file"; then
    echo "ERROR: failed to normalize recovered credential encoding" >&2
    return 1
  fi
  if [ ! -s "$output_file" ]; then
    echo "ERROR: recovered credential encoding was empty" >&2
    return 1
  fi
}

encode_recovered_credential_files() {
  name_value_file="$WORKDIR/name.value"
  credential_pair_file="$WORKDIR/basic-auth-plain.value"
  basic_auth_value_file="$WORKDIR/basic-auth.value"
  name_b64_file="$WORKDIR/name.b64"
  secret_b64_file="$WORKDIR/secret.b64"
  basic_auth_b64_file="$WORKDIR/basic-auth.b64"

  if ! printf '%s' "$robot_name" > "$name_value_file"; then
    echo "ERROR: failed to stage recovered robot name" >&2
    return 1
  fi
  if ! printf '%s:' "$robot_name" > "$credential_pair_file"; then
    echo "ERROR: failed to stage recovered Basic-auth name" >&2
    return 1
  fi
  if ! cat "$secret_file" >> "$credential_pair_file"; then
    echo "ERROR: failed to stage recovered Basic-auth secret" >&2
    return 1
  fi

  # Kubernetes Secret .data is itself base64. name and secret therefore need
  # one encoding layer, while basicAuth needs two: the Secret's decoded value
  # remains base64(name:secret), matching the declarative CREATE contract.
  encode_base64_file "$name_value_file" "$name_b64_file" || return 1
  encode_base64_file "$secret_file" "$secret_b64_file" || return 1
  encode_base64_file "$credential_pair_file" "$basic_auth_value_file" || return 1
  encode_base64_file "$basic_auth_value_file" "$basic_auth_b64_file" || return 1
}

# Sourcing this script with HARBOR_REPAIR_LIB_ONLY=1 defines the pure helpers
# above and stops before any I/O, so the real shell logic can be exercised
# directly against fixtures (Issue #285 second review: source-level assertions
# missed a header parser that could never match).
if [ "${HARBOR_REPAIR_LIB_ONLY:-0}" = "1" ]; then
  return 0 2>/dev/null || exit 0
fi

# --- Kubernetes API authentication -----------------------------------------
# The bearer token is read from its projected file straight into a curl
# config file; it never appears in argv or in any log line.
k8s_auth_cfg="$WORKDIR/k8s.cfg"
{ printf 'header = "Authorization: Bearer '; cat "$K8S_TOKEN_FILE"; printf '"
'; } > "$k8s_auth_cfg"

# --- Harbor API authentication ---------------------------------------------
# `.data.value` is Kubernetes' OUTER base64 wrapper around a logical value
# that is itself already base64(admin:password) -- exactly what HTTP Basic
# needs. So it is decoded exactly once here, at this API boundary, and the
# result is validated before use. Sending the outer text verbatim produced
# Basic base64(base64(admin:password)) and could only ever yield 401.
admin_auth_response="$WORKDIR/harbor-admin-auth.json"
curl --config "$k8s_auth_cfg" --cacert "$K8S_CACERT" -fsS \
  "$K8S_API/api/v1/namespaces/harbor/secrets/harbor-admin-basic-auth" \
  -o "$admin_auth_response"

harbor_basic_file="$WORKDIR/harbor-basic.txt"
jq -r '.data.value // empty' "$admin_auth_response" | base64 -d > "$harbor_basic_file"
if [ ! -s "$harbor_basic_file" ]; then
  echo "ERROR: harbor-admin-basic-auth is missing or empty" >&2
  exit 1
fi
# Validate the decoded Basic token without ever printing it: it must be
# well-formed base64 that decodes to admin:<nonempty>.
if ! tr -d '
' < "$harbor_basic_file" | grep -Eq '^[A-Za-z0-9+/]+={0,2}$'; then
  echo "ERROR: the Harbor admin Basic token is not valid base64" >&2
  exit 1
fi
if ! tr -d '
' < "$harbor_basic_file" | base64 -d 2>/dev/null | grep -Eq '^admin:.+$'; then
  echo "ERROR: the Harbor admin Basic token does not decode to admin:<secret>" >&2
  exit 1
fi

harbor_auth_cfg="$WORKDIR/harbor.cfg"
{ printf 'header = "Authorization: Basic '; tr -d '
' < "$harbor_basic_file"; printf '"
'; } > "$harbor_auth_cfg"

cat <<'EXPECTED_PERMISSIONS' > "$expected_permissions_file"
__EXPECTED_PERMISSIONS__
EXPECTED_PERMISSIONS

cat <<'SELECTOR_FILTER' > "$selector_file"
__SELECTOR_JQ__
SELECTOR_FILTER

attempt=1
while [ "$attempt" -le "$MAX_ATTEMPTS" ]; do
  # 1. Read and validate the WRITE TARGET first: the rotation below is
  #    irreversible, so nothing may be rotated until it is known exactly where
  #    the result will be stored and under which resourceVersion.
  current_secret="$WORKDIR/current-secret.json"
  curl --config "$k8s_auth_cfg" --cacert "$K8S_CACERT" -fsS "$TARGET_SECRET_URL" -o "$current_secret"
  resource_version=$(jq -r '.metadata.resourceVersion // empty' "$current_secret")
  if [ -z "$resource_version" ]; then
    echo "ERROR: the target Secret has no resourceVersion" >&2
    exit 1
  fi

  # 2. Re-resolve the robot structurally and re-verify its exact identity and
  #    complete least-privilege permission set, immediately before rotating.
  if ! collect_robots; then
    exit 1
  fi
  selection=$(jq -c --arg name "$ROBOT_NAME" \
    --argjson expected "$(cat "$expected_permissions_file")" \
    -f "$selector_file" "$WORKDIR/robots.json")
  if [ "$(printf '%s' "$selection" | jq -r '.ok')" != "true" ]; then
    reason=$(printf '%s' "$selection" | jq -r '.reason')
    matches=$(printf '%s' "$selection" | jq -r '.matches')
    echo "ERROR: refusing to rotate; robot selection failed (reason=$reason matches=$matches)" >&2
    exit 1
  fi
  robot_id=$(printf '%s' "$selection" | jq -r '.id')
  robot_name=$(printf '%s' "$selection" | jq -r '.name')

  # 3. Rotate. RefreshSec with an empty body: Harbor generates the secret
  #    itself and returns it, leaving the permission set untouched.
  refresh_body="$WORKDIR/refresh-body.json"
  printf "{}" > "$refresh_body"
  refresh_response="$WORKDIR/refresh-response.json"
  curl --config "$harbor_auth_cfg" --cacert "$HARBOR_CACERT" -fsS -X PATCH \
    -H "Content-Type: application/json" --data-binary @"$refresh_body" \
    "$HARBOR_API/robots/$robot_id" -o "$refresh_response"

  secret_file="$WORKDIR/refreshed-secret.txt"
  jq -j '.secret // empty' "$refresh_response" > "$secret_file"
  # goharbor v2.15.1 CreateSec -> utils.GenerateRandomStringWithLen(32) over
  # [a-zA-Z0-9], retried until IsValidSec (8..128 chars, upper+lower+digit).
  if ! grep -Eq '^[A-Za-z0-9]{8,128}$' "$secret_file" \
     || ! grep -q '[a-z]' "$secret_file" \
     || ! grep -q '[A-Z]' "$secret_file" \
     || ! grep -q '[0-9]' "$secret_file"; then
    echo "ERROR: the refreshed robot secret failed format validation" >&2
    exit 1
  fi

  # Keep every encoded credential field in the memory-backed workspace. Even
  # Kubernetes' base64 representation is credential material and must not be
  # expanded into another process's argv.
  if ! encode_recovered_credential_files; then
    exit 1
  fi

  # 4. Conditional write. The resourceVersion precondition makes the API
  #    server reject the patch if anything changed since step 1. A JSON merge
  #    patch touches only these three keys: unrelated data keys, metadata and
  #    type are preserved exactly. jq reads credential fields from tmpfs files;
  #    only file paths and the non-secret resourceVersion appear in argv.
  patch_body="$WORKDIR/secret-patch.json"
  jq -n --arg rv "$resource_version" \
    --rawfile n "$name_b64_file" --rawfile s "$secret_b64_file" --rawfile b "$basic_auth_b64_file" \
    '{metadata: {resourceVersion: $rv}, data: {name: $n, secret: $s, basicAuth: $b}}' > "$patch_body"

  patch_code=$(curl --config "$k8s_auth_cfg" --cacert "$K8S_CACERT" -s -o /dev/null -w '%{http_code}' \
    -X PATCH -H "Content-Type: application/merge-patch+json" --data-binary @"$patch_body" \
    "$TARGET_SECRET_URL")
  if [ "$patch_code" = "409" ]; then
    echo "conflict=true attempt=$attempt"
    attempt=$((attempt + 1))
    continue
  fi
  if [ "$patch_code" != "200" ]; then
    echo "ERROR: Kubernetes API rejected the credential patch (HTTP $patch_code)" >&2
    exit 1
  fi

  # 5. Authenticated byte-exact readback. A 200 only proves the request was
  #    accepted, not what is stored. Extract the persisted opaque base64 values
  #    into tmpfs files and compare file-to-file so credential material appears
  #    neither in argv nor in logs.
  post_patch_secret="$WORKDIR/post-patch-secret.json"
  curl --config "$k8s_auth_cfg" --cacert "$K8S_CACERT" -fsS "$TARGET_SECRET_URL" -o "$post_patch_secret"
  post_name_b64_file="$WORKDIR/post-name.b64"
  post_secret_b64_file="$WORKDIR/post-secret.b64"
  post_basic_auth_b64_file="$WORKDIR/post-basic-auth.b64"
  jq -j '.data.name // empty' "$post_patch_secret" > "$post_name_b64_file"
  jq -j '.data.secret // empty' "$post_patch_secret" > "$post_secret_b64_file"
  jq -j '.data.basicAuth // empty' "$post_patch_secret" > "$post_basic_auth_b64_file"
  if ! cmp -s "$post_name_b64_file" "$name_b64_file" \
     || ! cmp -s "$post_secret_b64_file" "$secret_b64_file" \
     || ! cmp -s "$post_basic_auth_b64_file" "$basic_auth_b64_file"; then
    echo "ERROR: readback verification failed -- the persisted credential does not match" >&2
    exit 1
  fi

  echo "credential_recovered=true"
  exit 0
done

echo "ERROR: exhausted $MAX_ATTEMPTS attempts due to concurrent modification" >&2
exit 1
'#
    # Composed rather than interpolated: the raw strings above stay byte-exact
    # (no Nushell subexpression/escape processing inside the shell or jq text).
    $template
    | str replace "__EXPECTED_PERMISSIONS__" (harbor_robot_expected_permissions)
    | str replace "__SELECTOR_JQ__" (harbor_robot_selector_jq)
    | normalize_posix_container_script $in
}

# The recovery Job: mounts a memory-backed (tmpfs) workspace for every file
# the repair script writes (never a host path), plus the digiorg.local CA
# already copied into crossplane-system by copy_digiorg_local_ca_to_namespace.
# Runs as the narrowly-scoped ServiceAccount from harbor_credential_recovery_rbac.
def harbor_credential_repair_job [] {
    {
        apiVersion: "batch/v1"
        kind: "Job"
        metadata: {
            name: "harbor-credential-repair"
            namespace: "crossplane-system"
        }
        spec: {
            backoffLimit: 0
            template: {
                spec: {
                    serviceAccountName: "harbor-credential-recovery"
                    restartPolicy: "Never"
                    # Verified against the image's own layers rather than its
                    # tag: nats-box 0.19.2 ships /usr/bin/jq and /usr/bin/curl
                    # (its second layer) and defines nobody:65534. Runs
                    # unprivileged: the memory-backed workspace is created 0777
                    # by the kubelet (pkg/volume/emptydir/empty_dir.go `perm`)
                    # and additionally group-owned via fsGroup, while the CA and
                    # the projected ServiceAccount token are readable through
                    # the same fsGroup.
                    securityContext: {
                        runAsNonRoot: true
                        runAsUser: 65534
                        runAsGroup: 65534
                        fsGroup: 65534
                        seccompProfile: {type: "RuntimeDefault"}
                    }
                    containers: [
                        {
                            name: "repair"
                            image: "natsio/nats-box:0.19.2@sha256:8031d190c7ee24081f3f27cc939fb647a1eeb29ebb5c60fef9b5b6c7a846d6a2"
                            command: ["sh", "-c", (harbor_credential_repair_script)]
                            workingDir: "/workspace"
                            securityContext: {
                                allowPrivilegeEscalation: false
                                readOnlyRootFilesystem: true
                                capabilities: {drop: ["ALL"]}
                            }
                            volumeMounts: [
                                {name: "workspace", mountPath: "/workspace"}
                                {name: "harbor-ca", mountPath: "/var/run/secrets/digiorg-ca", readOnly: true}
                            ]
                        }
                    ]
                    volumes: [
                        {name: "workspace", emptyDir: {medium: "Memory"}}
                        {name: "harbor-ca", secret: {secretName: "digiorg-local-ca", defaultMode: 288}}
                    ]
                }
            }
        }
    }
}

# Namespace-wide checked deletion of any Pod traceable to the recovery
# identity by immutable ServiceAccount or owning-Job-name -- never a mutable
# label -- so an orphaned or relabelled leftover Pod cannot escape the resume
# preflight below. Mirrors the identity predicate `harbor_recovery_privilege_
# leftovers` uses to detect them.
def harbor_recovery_delete_leftover_pods [] {
    $env.KUBECONFIG = $KUBECONFIG_PATH
    let pods_result = (get_crossplane_system_pod_list)
    if $pods_result.exit_code != 0 {
        return {ok: false, reason: "failed to list crossplane-system pods before resuming recovery"}
    }
    let parsed = (parse_pod_list $pods_result.stdout)
    if not $parsed.ok {
        return {ok: false, reason: $"could not verify crossplane-system pods before resuming recovery: ($parsed.reason)"}
    }
    let targets = ($parsed.items | where {|pod|
        let recovery_sa = (($pod | get -o spec.serviceAccountName | default "") == "harbor-credential-recovery")
        let recovery_owner = (($pod | get -o metadata.ownerReferences | default []) | any {|owner|
            ((($owner | get -o kind | default "") == "Job") and (($owner | get -o name | default "") == "harbor-credential-repair"))
        })
        $recovery_sa or $recovery_owner
    })
    # PR#287 independent review finding 2 (round 7): every matching Pod must
    # be attempted regardless of an earlier one's outcome -- returning on the
    # first failed delete silently left every later stale recovery Pod
    # running, still mounting the harbor-credential-recovery ServiceAccount.
    # Failures are accumulated by pod name only; kubectl's own stderr is
    # never folded in here to avoid leaking cluster/log detail.
    mut failed_names = []
    for pod in $targets {
        let name = ($pod | get -o metadata.name | default "")
        let delete_result = (do { kubectl delete pod $name -n crossplane-system --ignore-not-found --wait=true } | complete)
        if $delete_result.exit_code != 0 {
            $failed_names = ($failed_names | append $name)
        }
    }
    if not ($failed_names | is-empty) {
        return {ok: false, reason: $"failed to delete stale recovery pod\(s\) before resuming recovery: ($failed_names | str join ', ')"}
    }
    {ok: true, reason: ""}
}

# PR#287 independent review finding 2 (round 2): RBAC must be revoked BEFORE
# any stale recovery Job or Pod cleanup is even attempted -- not merely
# before any RBAC is (re)applied. Kubernetes authorizes every request
# against the RBAC state that is current AT REQUEST TIME -- it is never
# baked into a token at pod start -- so a Pod that survived an earlier crash
# and still mounts the harbor-credential-recovery ServiceAccount token
# retains the ability to read the Harbor admin credential for as long as its
# Role/RoleBinding still exist, independent of whether or when that Pod
# itself gets deleted. Revoking RBAC only after attempting Job/Pod cleanup
# would leave such a surviving Pod fully privileged for the entire cleanup
# window. This preflight therefore revokes the stale recovery RBAC FIRST,
# then cleans up any stale Job and Pod traceable to the recovery identity --
# by immutable owner/ServiceAccount identity, never the mutable job-name
# label -- and only then positively re-verifies every one of them is gone.
# Any failure in this sequence is fail-closed: fresh RBAC is never applied on
# top of an unverified state.
def harbor_recovery_resume_preflight [] {
    $env.KUBECONFIG = $KUBECONFIG_PATH
    let rbac_manifest = (harbor_credential_recovery_rbac)

    let rbac_delete = (do { $rbac_manifest | kubectl delete -f - --ignore-not-found } | complete)
    if $rbac_delete.exit_code != 0 {
        return {ok: false, reason: "failed to revoke a stale recovery RBAC grant before resuming recovery"}
    }

    let job_cleanup = (cleanup_bootstrap_job_verified "harbor-credential-repair")
    if not $job_cleanup.ok {
        return {ok: false, reason: $"failed to remove a stale harbor-credential-repair Job before resuming recovery: ($job_cleanup.reason)"}
    }

    let pod_cleanup = (harbor_recovery_delete_leftover_pods)
    if not $pod_cleanup.ok {
        return {ok: false, reason: $pod_cleanup.reason}
    }

    let leftovers = (harbor_recovery_privilege_leftovers)
    if not ($leftovers | is-empty) {
        return {ok: false, reason: $"stale recovery privilege still present before resuming recovery: ($leftovers | str join ', ')"}
    }

    {ok: true, reason: ""}
}

# Applies the recovery RBAC + repair Job, waits for it to finish, and tears
# both down unconditionally (success or failure) -- nothing from this
# recovery boundary is left behind on the cluster afterwards.
def repair_harbor_credential_secret [] {
    $env.KUBECONFIG = $KUBECONFIG_PATH
    print $"(ansi yellow)  ! crossplane-harbor-credentials is missing or invalid -- attempting crash-safe recovery via Harbor RefreshSec...(ansi reset)"

    let rbac_manifest = (harbor_credential_recovery_rbac)

    # Issue #285 review finding 5: the RBAC apply itself belongs INSIDE the
    # guarded region. It is a multi-document apply, so it can partially
    # succeed -- creating the ServiceAccount and Role (which can read the
    # Harbor admin credential) and only then failing on the RoleBinding. With
    # the apply outside the guard, that path aborted with privileged objects
    # already on the cluster and no cleanup at all. Everything from the apply
    # onwards is now caught into a plain record so the teardown below always
    # runs, whatever failed.
    let outcome = (try {
        # PR#287 independent review finding 2: a stale recovery grant from an
        # earlier crashed run must be confirmed gone before this run grants a
        # fresh one -- otherwise a surviving Pod using that grant's
        # ServiceAccount could exploit it in the window before
        # run_bootstrap_job's own pre-cleanup ever runs.
        let preflight = (harbor_recovery_resume_preflight)
        if not $preflight.ok {
            error make {msg: $"Refusing to grant Harbor credential recovery privilege: ($preflight.reason)"}
        }

        let rbac_apply = (do { $rbac_manifest | kubectl apply -f - } | complete)
        if $rbac_apply.exit_code != 0 {
            error make {msg: "Failed to apply the least-privilege recovery RBAC for the Harbor credential repair job"}
        }

        ensure_harbor_credential_secret_shell
        run_bootstrap_job (harbor_credential_repair_job) "harbor-credential-repair" "180s"
        {ok: true, error: ""}
    } catch {|err|
        {ok: false, error: $err.msg}
    })

    # Guaranteed cleanup, on every path above: a partial RBAC apply, a shell
    # setup failure, a Job create/wait failure, or success. Extracted into
    # `harbor_recovery_final_teardown` so it is independently testable and
    # runs identically regardless of the recovery outcome.
    let verdict = (harbor_recovery_final_teardown $outcome $rbac_manifest)
    if not $verdict.ok {
        error make {msg: $verdict.msg}
    }

    print $"(ansi green)  ✓ Harbor bootstrap credential recovery job completed(ansi reset)"
}

# PR#287 independent review (round N): this final teardown -- which runs on
# EVERY path, success or failure -- used to clean up the fixed-name repair
# Job BEFORE revoking the recovery RBAC. Kubernetes authorizes every request
# live against whatever RBAC state currently exists; it is never baked into
# a Pod's token at start. A Pod that survived past this recovery attempt and
# still mounts the harbor-credential-recovery ServiceAccount therefore stayed
# fully privileged for the entire span of the Job/Pod cleanup below -- the
# exact same exposure `harbor_recovery_resume_preflight` already closes for
# the *next* run's preflight, but left open here at the end of *this* one.
#
# RBAC is now revoked (checked) FIRST. Every remaining cleanup step -- the
# fixed-name Job cleanup and the recovery-identity Pod cleanup (by
# ServiceAccount or by owning Job, independent of the Job's own cascade) --
# is then attempted independently: none may short-circuit another, so a
# malformed/unavailable Pod listing or a failed Job cleanup can never mask or
# skip the RBAC revocation, or prevent the other cleanup from being
# attempted. Absence is positively re-verified last, and every failure
# (original outcome, RBAC, Job, Pod, or surviving leftovers) is combined into
# one verdict.
def harbor_recovery_final_teardown [outcome: record, rbac_manifest: string] {
    $env.KUBECONFIG = $KUBECONFIG_PATH

    let rbac_delete = (do { $rbac_manifest | kubectl delete -f - --ignore-not-found } | complete)

    # PR#287 independent review findings 1 (rounds 7-8): each of these three steps
    # is independently guarded. `parse_pod_list`/`pod_item_is_well_formed`
    # already fail a malformed Pod shape closed rather than throwing, but this
    # is defense in depth: a thrown error from ANY one of them -- anticipated
    # or not -- must convert to a failed result for that step alone, and can
    # never skip or short-circuit the other two, or the leftovers check below.
    # Catch payloads are deliberately discarded: they can carry API output,
    # logs, stderr, Pod JSON, or credentials and must never reach the verdict.
    let job_cleanup = (try {
        cleanup_bootstrap_job_verified "harbor-credential-repair"
    } catch { {ok: false, reason: "recovery Job cleanup failed unexpectedly"} })
    let pod_cleanup = (try {
        harbor_recovery_delete_leftover_pods
    } catch { {ok: false, reason: "recovery Pod cleanup failed unexpectedly"} })

    let leftovers = (try {
        harbor_recovery_privilege_leftovers
    } catch { ["crossplane-system/recovery privilege state (unverifiable)"] })
    (harbor_recovery_cleanup_verdict
        $outcome.ok $outcome.error
        $job_cleanup.ok ($rbac_delete.exit_code == 0) $pod_cleanup.ok
        $leftovers)
}

# Enumerates every object the recovery boundary creates that is still present.
# Never throws: a failed lookup is itself reported as an (unverifiable)
# leftover, so an API error can never be mistaken for "cleanly removed".
def harbor_recovery_privilege_leftovers [] {
    $env.KUBECONFIG = $KUBECONFIG_PATH
    let expected_absent = [
        {kind: "job", name: "harbor-credential-repair", namespace: "crossplane-system"}
        {kind: "serviceaccount", name: "harbor-credential-recovery", namespace: "crossplane-system"}
        {kind: "role", name: "harbor-credential-recovery", namespace: "crossplane-system"}
        {kind: "rolebinding", name: "harbor-credential-recovery", namespace: "crossplane-system"}
        {kind: "role", name: "harbor-credential-recovery-admin-secret", namespace: "harbor"}
        {kind: "rolebinding", name: "harbor-credential-recovery-admin-secret", namespace: "harbor"}
    ]
    mut leftovers = []
    for target in $expected_absent {
        let descriptor = $"($target.namespace)/($target.kind)/($target.name)"
        let lookup = (do {
            kubectl get $target.kind $target.name -n $target.namespace --ignore-not-found -o name
        } | complete)
        if $lookup.exit_code != 0 {
            $leftovers = ($leftovers | append $"($descriptor) \(unverifiable\)")
        } else if not ($lookup.stdout | str trim | is-empty) {
            $leftovers = ($leftovers | append $descriptor)
        }
    }
    let pod_descriptor = "crossplane-system/recovery-identity pods"
    let pod_lookup = (get_crossplane_system_pod_list)
    if $pod_lookup.exit_code != 0 {
        $leftovers = ($leftovers | append $"($pod_descriptor) \(unverifiable\)")
    } else {
        let parsed = (parse_pod_list $pod_lookup.stdout)
        if not $parsed.ok {
            $leftovers = ($leftovers | append $"($pod_descriptor) \(unverifiable\)")
        } else {
            let recovery_pods = ($parsed.items | where {|pod|
                let recovery_sa = (($pod | get -o spec.serviceAccountName | default "") == "harbor-credential-recovery")
                let recovery_owner = (($pod | get -o metadata.ownerReferences | default []) | any {|owner|
                    ((($owner | get -o kind | default "") == "Job") and (($owner | get -o name | default "") == "harbor-credential-repair"))
                })
                $recovery_sa or $recovery_owner
            })
            if ($recovery_pods | length) > 0 {
                $leftovers = ($leftovers | append $pod_descriptor)
            }
        }
    }
    $leftovers
}

# Pure decision function: combines the recovery outcome with the cleanup
# results into a single verdict. Kept free of I/O so every combination is
# directly testable. The message is assembled only from these inputs -- it can
# never carry Job logs or Secret contents.
def harbor_recovery_cleanup_verdict [
    outcome_ok: bool
    outcome_error: string
    job_cleanup_ok: bool
    rbac_cleanup_ok: bool
    pod_cleanup_ok: bool
    leftovers: list
] {
    mut problems = []
    if not $outcome_ok {
        $problems = ($problems | append $"Harbor credential recovery failed: ($outcome_error)")
    }
    if not $job_cleanup_ok {
        $problems = ($problems | append "failed to delete the recovery Job")
    }
    if not $rbac_cleanup_ok {
        $problems = ($problems | append "failed to delete the recovery RBAC objects")
    }
    if not $pod_cleanup_ok {
        $problems = ($problems | append "failed to delete a stale recovery Pod")
    }
    if not ($leftovers | is-empty) {
        $problems = ($problems | append $"temporary recovery privilege still present: ($leftovers | str join ', ')")
    }
    if ($problems | is-empty) {
        {ok: true, msg: ""}
    } else {
        {ok: false, msg: ($problems | str join "; ")}
    }
}


# Issue #285 stdout12: the gate that actually closes the loop. Argo sync/health
# and the Request's own Ready/Synced conditions all proved insufficient -- the
# credential Secret itself must be probed. A complete credential short-circuits
# immediately (never rotated on a healthy resume); an incomplete one triggers
# exactly one recovery attempt, then is re-probed before the run is failed.
def ensure_crossplane_harbor_credentials [] {
    $env.KUBECONFIG = $KUBECONFIG_PATH
    print ""
    print $"(ansi cyan_bold)Verifying the Harbor bootstrap credential(ansi reset)"

    # The Request's own Ready/Synced conditions are not trusted as the final
    # credential verdict. The exact server-side OBSERVE query converges the
    # declarative lifecycle; the probe/recovery below independently verifies
    # and, when necessary, repairs the credential Secret.

    let probe = (probe_harbor_credential_keys)
    let missing = (harbor_credential_missing_keys $probe)
    if ($missing | is-empty) {
        print $"(ansi green)  ✓ crossplane-harbor-credentials already carries every required key(ansi reset)"
        return
    }

    print $"(ansi yellow)  ! crossplane-harbor-credentials has (($missing | length)) missing or invalid required key\(s\)(ansi reset)"
    repair_harbor_credential_secret

    let reprobe = (probe_harbor_credential_keys)
    let still_missing = (harbor_credential_missing_keys $reprobe)
    if not ($still_missing | is-empty) {
        error make {msg: $"crossplane-harbor-credentials still has missing or invalid required key\(s\) after recovery: ($still_missing)"}
    }
    print $"(ansi green)  ✓ crossplane-harbor-credentials recovered(ansi reset)"
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
def gated_sync_operation_succeeded [
    app: string,
    saw_new_operation: bool,
    phase: string,
    sync: string,
    health: string
] {
    if not $saw_new_operation or $phase != "Succeeded" or $health != "Healthy" {
        return false
    }
    if $sync == "Synced" {
        return true
    }
    if $sync != "OutOfSync" {
        return false
    }
    argocd_app_has_no_material_diff $app
}

# The platform ingress is installed before Argo CD exists, so it cannot rely on
# an Application for first bootstrap. Existing local clusters still need an
# idempotent, declarative promotion path when those manifests change. Keep that
# promotion inside the same resume/upgrade wrapper used for gated Applications
# rather than requiring an operator to run an ad-hoc kubectl command.
def apply_bootstrap_managed_ingress_for_local_dev [] {
    let result = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH apply -k platform/base/ingress/
    } | complete)
    if $result.exit_code != 0 {
        error make {msg: $"Failed to promote bootstrap-managed platform ingress: ($result.stderr | str trim)"}
    }
    print $"(ansi green)✓ Bootstrap-managed platform ingress promoted(ansi reset)"
}

# containerd runs inside the KinD node and uses the node's resolver for image
# pulls. `digiorg.local` is a loopback ingress endpoint in this all-in-one local
# cluster: the node's port 443 is mapped to the ingress controller, while host
# and Pod DNS configuration does not apply to containerd. Enforce the one
# permitted mapping idempotently and reject ambiguous/conflicting state rather
# than rewriting an operator-managed hosts file.
def ensure_kind_node_digiorg_local_resolution [] {
    let kind_node = $"($CLUSTER_NAME)-control-plane"
    let node_script = r#'
set -eu
hosts_file="/etc/hosts"

inspect_hosts() {
    awk '
        BEGIN { occurrences = 0; exact_lines = 0; ambiguous = 0 }
        {
            line_occurrences = 0
            for (i = 1; i <= NF; i++) {
                if ($i == "digiorg.local") {
                    occurrences++
                    line_occurrences++
                }
            }
            if (line_occurrences > 0) {
                if (line_occurrences == 1 && NF == 2 && $1 == "127.0.0.1" && $2 == "digiorg.local") {
                    exact_lines++
                } else {
                    ambiguous = 1
                }
            }
        }
        END {
            if (occurrences == 0) print "absent"
            else if (occurrences == 1 && exact_lines == 1 && ambiguous == 0) print "exact"
            else print "conflict"
        }
    ' "$hosts_file"
}

if ! state=$(inspect_hosts); then
    echo "failed to inspect KinD node hosts file" >&2
    exit 1
fi
case "$state" in
    exact) exit 0 ;;
    absent) ;;
    conflict)
        echo "conflicting digiorg.local entry in KinD node hosts file" >&2
        exit 1
        ;;
    *)
        echo "failed to inspect KinD node hosts file: invalid result" >&2
        exit 1
        ;;
esac

printf '\n127.0.0.1 digiorg.local\n' >>"$hosts_file"
if ! state=$(inspect_hosts); then
    echo "failed to inspect KinD node hosts file after update" >&2
    exit 1
fi
if [ "$state" != "exact" ]; then
    echo "KinD node hosts file did not converge to exactly one digiorg.local loopback mapping" >&2
    exit 1
fi
'#
    # Windows Git checkouts can transcode this multiline literal to CRLF. The
    # command executes in the Linux KinD node, so normalize at the final command
    # boundary rather than relying on host checkout settings.
    let normalized_node_script = (
        $node_script
        | str replace --all "\r\n" "\n"
        | str replace --all "\r" "\n"
    )
    let result = (do {
        docker exec $kind_node sh -c $normalized_node_script
    } | complete)
    if $result.exit_code != 0 {
        error make {msg: $"Failed to configure KinD node digiorg.local resolution: ($result.stderr | str trim)"}
    }
    print $"(ansi green)✓ KinD node resolves digiorg.local to 127.0.0.1(ansi reset)"
}

# containerd's pinned v2.3.1 hosts configuration is loaded dynamically from
# /etc/containerd/certs.d, so the local Harbor CA can be installed without a
# daemon or Pod restart. Transfer only the public CA over stdin, validate it in
# the Linux node, and keep any conflicting hosts.toml fail-closed.
def ensure_kind_node_digiorg_local_ca_trust [] {
    let kind_node = $"($CLUSTER_NAME)-control-plane"
    let ca_result = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH get secret digiorg-local-ca-secret -n cert-manager -o jsonpath='{.data.ca\.crt}'
    } | complete)
    if $ca_result.exit_code != 0 or ($ca_result.stdout | str trim | is-empty) {
        error make {msg: "Failed to read the public digiorg.local CA for KinD containerd"}
    }
    let ca_b64 = ($ca_result.stdout | str trim)
    let node_script = r#'
set -eu
registry_dir="/etc/containerd/certs.d/digiorg.local"
trust_root=${registry_dir%/digiorg.local}
ca_path="$registry_dir/ca.crt"
hosts_path="$registry_dir/hosts.toml"
if [ -e "$trust_root" ]; then
    if [ ! -d "$trust_root" ] || [ -L "$trust_root" ]; then
        echo "invalid containerd trust root" >&2
        exit 1
    fi
else
    mkdir "$trust_root"
fi
if [ -e "$registry_dir" ]; then
    if [ ! -d "$registry_dir" ] || [ -L "$registry_dir" ]; then
        echo "invalid containerd registry trust directory for digiorg.local" >&2
        exit 1
    fi
else
    mkdir "$registry_dir"
fi

tmp_ca=$(mktemp "$registry_dir/.ca.crt.XXXXXX")
tmp_hosts=$(mktemp "$registry_dir/.hosts.toml.XXXXXX")
cleanup() {
    [ -z "${tmp_ca:-}" ] || rm -f "$tmp_ca"
    [ -z "${tmp_hosts:-}" ] || rm -f "$tmp_hosts"
}
trap cleanup EXIT HUP INT TERM

cat >"$tmp_ca"
if [ ! -s "$tmp_ca" ] || ! openssl x509 -in "$tmp_ca" -noout >/dev/null 2>&1; then
    echo "invalid digiorg.local public CA" >&2
    exit 1
fi
chmod 0644 "$tmp_ca"

cat >"$tmp_hosts" <<'EOF'
server = "https://digiorg.local"

capabilities = ["pull", "resolve"]
ca = "/etc/containerd/certs.d/digiorg.local/ca.crt"
EOF
chmod 0644 "$tmp_hosts"
expected_hosts_sha=$(sha256sum "$tmp_hosts" | awk '{print $1}')
hosts_matches() {
    [ -f "$hosts_path" ] && [ ! -L "$hosts_path" ] || return 1
    actual_hosts_sha=$(sha256sum "$hosts_path" | awk '{print $1}') || return 1
    [ "$actual_hosts_sha" = "$expected_hosts_sha" ]
}

# Publish the restrictive policy first. If the process stops before the CA is
# installed, its referenced CA is absent and containerd fails closed. `mv -n`
# never overwrites a hosts.toml created between the existence check and rename.
if [ -e "$hosts_path" ]; then
    if ! hosts_matches; then
        echo "conflicting containerd hosts configuration for digiorg.local" >&2
        exit 1
    fi
else
    if ! mv -n "$tmp_hosts" "$hosts_path"; then
        echo "failed to publish containerd hosts configuration for digiorg.local" >&2
        exit 1
    fi
fi
if ! hosts_matches; then
    echo "conflicting containerd hosts configuration for digiorg.local" >&2
    exit 1
fi
[ ! -e "$tmp_hosts" ] || rm -f "$tmp_hosts"
tmp_hosts=""

if [ -e "$ca_path" ]; then
    if [ ! -f "$ca_path" ] || [ -L "$ca_path" ]; then
        echo "invalid containerd CA path for digiorg.local" >&2
        exit 1
    fi
fi
if [ -f "$ca_path" ] && cmp -s "$tmp_ca" "$ca_path"; then
    rm -f "$tmp_ca"
else
    mv -f "$tmp_ca" "$ca_path"
fi
tmp_ca=""

chmod 0644 "$ca_path" "$hosts_path"
if ! hosts_matches || ! openssl x509 -in "$ca_path" -noout >/dev/null 2>&1; then
    echo "KinD containerd trust did not converge for digiorg.local" >&2
    exit 1
fi
'#
    # The script executes in Linux even when Git/Nushell run on Windows.
    let normalized_node_script = (
        $node_script
        | str replace --all "\r\n" "\n"
        | str replace --all "\r" "\n"
    )
    let result = (do {
        ($ca_b64 | decode base64) | docker exec -i $kind_node sh -c $normalized_node_script
    } | complete)
    if $result.exit_code != 0 {
        error make {msg: $"Failed to configure KinD containerd digiorg.local CA trust: ($result.stderr | str trim)"}
    }
    print $"(ansi green)✓ KinD containerd trusts the digiorg.local public CA(ansi reset)"
}

def sync_gated_apps_for_local_dev [] {
    let gated_apps = [
        "external-secrets", "nats", "grafana", "opencost", "gitea",
        "sonarqube", "crossplane", "crossplane-providers",
        "crossplane-provider-configs", "crossplane-harbor-bootstrap",
        "crossplane-xrds", "core-catalog"
    ]
    let sync_payload = '{"operation":{"sync":{"prune":true,"syncOptions":["CreateNamespace=true","ServerSideApply=true"]}}}'
    let max_operation_retries = 3

    print ""
    print $"(ansi cyan_bold)Promoting gated upgrades sequentially on local KinD(ansi reset)"

    ensure_kind_node_digiorg_local_resolution
    apply_bootstrap_managed_ingress_for_local_dev

    # Issue #285 runtime-v11: crossplane-harbor-bootstrap's Request objects need
    # crossplane-system/digiorg-local-ca (proven live ReconcileError: missing
    # crossplane-system/digiorg-local-ca), and Harbor's own PostSync hook
    # (Job/harbor-oidc-config) needs that same CA copied into harbor's own
    # namespace too (proven live: Harbor's Application reaches Synced/Healthy
    # with every pod Ready while its Running operation still waits on that
    # hook, because Phase 3's copy into harbor runs AFTER this entire gated
    # loop). Gitea and its Actions runner also consume the CA from a mounted
    # Secret, which must exist before Gitea's gated sync. Copy the CA into the
    # consumer namespaces once, before the very first
    # gated Application sync -- root-app has already created every
    # Application by this point -- waiting only for cert-manager itself and
    # its Certificate, never for Harbor (which is not yet synced and would
    # deadlock on its own PostSync hook). Idempotent with Phase 3's copies.
    wait_for_configuration_dependencies "Digiorg local CA" ["cert-manager"] [
        {namespace: "cert-manager", name: "digiorg-local-ca"}
    ]
    ensure_kind_node_digiorg_local_ca_trust
    copy_digiorg_local_ca_to_namespace "harbor"
    copy_digiorg_local_ca_to_namespace "crossplane-system"
    let gitea_deployment_lookup = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH get deployment gitea -n gitea --ignore-not-found -o name
    } | complete)
    if $gitea_deployment_lookup.exit_code != 0 {
        error make {msg: "Failed to determine whether deployment gitea/gitea exists"}
    }
    let gitea_existed_before_ca_copy = ($gitea_deployment_lookup.stdout | str trim | is-not-empty)
    let gitea_ca_changed = (copy_digiorg_local_ca_to_namespace "gitea")

    for app in $gated_apps {
        if $app == "crossplane-harbor-bootstrap" {
            wait_for_provider_http_ready
        }
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
            mut accepted_zero_diff = false
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
                    if (gated_sync_operation_succeeded $app $saw_new_operation $phase $sync $health) {
                        if $sync == "OutOfSync" {
                            # Fixed text only: the captured diff streams may
                            # contain credentials and must never be printed.
                            print $"  ($app): fresh successful operation has no material diff"
                            $accepted_zero_diff = true
                        }
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
                if $accepted_zero_diff {
                    print $"(ansi green)  ✓ ($app) sync Succeeded and Healthy with no material diff(ansi reset)"
                } else {
                    print $"(ansi green)  ✓ ($app) sync Succeeded, Synced and Healthy(ansi reset)"
                }
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

        # Issue #285 stdout12: Argo's own Synced/Healthy verdict above proved
        # insufficient for this one Application -- the credential Secret its
        # Request injects must be independently verified (and, if needed,
        # recovered) before any downstream Application (crossplane-xrds,
        # core-catalog) is allowed to sync.
        if $app == "crossplane-harbor-bootstrap" {
            wait_for_harbor_robot_permissions_ready
            ensure_crossplane_harbor_credentials
        }
    }

    wait_for_ingress_local_ca_convergence

    # Existing processes cache Go roots. Reload only processes which could
    # have started with the previous CA, after ingress has published the new
    # CA and every gated Application has converged.
    if $gitea_ca_changed {
        if $gitea_existed_before_ca_copy {
            restart_oidc_deployment "gitea" "gitea" "120s"
        } else {
            print $"(ansi yellow)○ gitea/gitea was created with the current CA; restart not required(ansi reset)"
        }

        let runner_token_lookup = (do {
            kubectl --kubeconfig $KUBECONFIG_PATH get secret gitea-actions-runner-token -n gitea --ignore-not-found -o name
        } | complete)
        if $runner_token_lookup.exit_code != 0 {
            error make {msg: "Failed to determine whether the Gitea Actions runner token exists"}
        }
        let runner_token_exists = ($runner_token_lookup.stdout | str trim | is-not-empty)
        if $runner_token_exists {
            restart_oidc_deployment_if_present "gitea" "gitea-actions-runner" "120s"
        } else {
            print $"(ansi yellow)○ gitea/gitea-actions-runner is awaiting first-time registration; it will mount the current CA on first start(ansi reset)"
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

# Issue #283: explicitly promote the manual CNPG operator Application before
# touching the Cluster Application. The operation identity rules mirror the
# Cluster promotion: an already converged operator is a no-op, an identifiable
# Running operation is observed, and a terminal stale operation must be
# replaced by a genuinely fresh startedAt. All failures remain fail-closed and
# diagnostics are redacted.
def promote_cnpg_operator [poll_interval: duration = 10sec] {
    $env.KUBECONFIG = $KUBECONFIG_PATH

    mut exists = false
    for attempt in 1..60 {
        let get_result = (do { kubectl get application cnpg -n argocd -o name } | complete)
        if $get_result.exit_code == 0 {
            $exists = true
            break
        }
        sleep 2sec
    }
    if not $exists {
        error make {msg: "ArgoCD Application cnpg was not created by the root app"}
    }

    let initial_result = (do { kubectl get application cnpg -n argocd -o json } | complete)
    if $initial_result.exit_code != 0 {
        error make {msg: $"Could not read CNPG operator Application state: (redact_sync_diagnostic ($initial_result.stderr | str trim))"}
    }
    let initial_state = (try { $initial_result.stdout | from json } catch {
        error make {msg: "Could not parse CNPG operator Application state"}
    })
    let previous_started = ($initial_state | get -o status.operationState.startedAt | default "")
    let initial_phase = ($initial_state | get -o status.operationState.phase | default "")
    let initial_sync = ($initial_state | get -o status.sync.status | default "")
    let initial_health = ($initial_state | get -o status.health.status | default "")

    if $initial_phase == "Succeeded" and $initial_sync == "Synced" and $initial_health == "Healthy" {
        print $"(ansi green)  ✓ cnpg operator is already Synced and Healthy(ansi reset)"
        return
    }

    mut resuming_existing_operation = false
    if $initial_phase == "Running" {
        if ($previous_started | is-empty) {
            error make {msg: "Running CNPG operator operation has no startedAt identity; refusing to overwrite it"}
        }
        $resuming_existing_operation = true
        print $"  Resuming observation of in-flight CNPG operator operation started at ($previous_started)..."
    } else {
        let sync_payload = '{"operation":{"sync":{"prune":true,"syncOptions":["CreateNamespace=true","ServerSideApply=true"]}}}'
        let sync_result = (do {
            kubectl patch application cnpg -n argocd --type merge -p $sync_payload
        } | complete)
        if $sync_result.exit_code != 0 {
            error make {msg: $"Could not start CNPG operator sync: (redact_sync_diagnostic ($sync_result.stderr | str trim))"}
        }
    }

    mut completed = false
    mut succeeded = false
    mut saw_new_operation = $resuming_existing_operation
    mut last_state = {}
    for attempt in 1..90 {
        let state_result = (do { kubectl get application cnpg -n argocd -o json } | complete)
        if $state_result.exit_code == 0 {
            let state = (try { $state_result.stdout | from json } catch { {} })
            $last_state = $state
            let phase = ($state | get -o status.operationState.phase | default "")
            let started = ($state | get -o status.operationState.startedAt | default "")
            let sync = ($state | get -o status.sync.status | default "")
            let health = ($state | get -o status.health.status | default "")
            if not $resuming_existing_operation and not ($started | is-empty) and $started != $previous_started {
                $saw_new_operation = true
            }
            if $resuming_existing_operation and $started != $previous_started {
                error make {msg: "The in-flight CNPG operator operation identity changed unexpectedly during resume"}
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
        error make {msg: "CNPG operator did not reach a fresh Synced+Healthy operation before the timeout"}
    }
    if not $succeeded {
        print_sync_diagnostics $last_state
        let message = ($last_state | get -o status.operationState.message | default "")
        error make {msg: $"CNPG operator sync failed: (redact_sync_diagnostic $message)"}
    }
    print $"(ansi green)  ✓ cnpg operator sync Succeeded, Synced and Healthy(ansi reset)"
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
    # Issue #283: the argocd CLI is an optional host tool (only this fallback
    # needs it). Require MAJOR.MINOR compatibility with the deployed server,
    # not an exact patch match, so a safe patch-level CLI upgrade cannot
    # silently disable material-drift detection.
    let version = (do { argocd version --client --short } | complete)
    if $version.exit_code != 0 or not (argocd_client_version_compatible $version.stdout "3.4") {
        return false
    }

    let temp_kubeconfig = (
        $nu.temp-dir
        | path join $"argocd-core-kubeconfig.(random uuid).yaml"
    )
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

    # Apps to wait for — this is the fail-closed CORE-PLATFORM gate. This
    # client-side readiness inventory must match apps/platform/*.yaml EXACTLY,
    # one entry per child Application, EXCEPT `cnpg` and `cnpg-cluster`. A
    # contract test (test_bootstrap_convergence.py) fails if this list drifts
    # from the manifests, so a stale inventory can no longer silently
    # under-count readiness (issue #281: the omitted `namespaces`).
    #
    # Issue #283: `cnpg` and `cnpg-cluster` are deliberately EXCLUDED. CNPG is
    # optional, future hosted-application database infrastructure — no core
    # platform component depends on it (Keycloak, Backstage, Gitea, SonarQube
    # and Harbor permanently use legacy PostgreSQL) — so it must never be able
    # to time out or fail this core-platform gate. `up` never promotes it at
    # all; run the separate, explicit `main future-infra` command (which fails
    # closed) whenever CNPG is actually needed.
    let apps = [
        # Wave -1
        "namespaces",
        # Wave 0
        "cert-manager", "external-secrets", "nats", "postgresql", "opensearch",
        # Wave 1
        "keycloak", "argocd", "nats-jetstream-controller",
        # Wave 2
        "backstage", "gitea", "grafana", "harbor", "jaeger", "landingpage", "opencost", "sonarqube",
        # Wave 3
        "crossplane", "kyverno",
        # Wave 4
        "crossplane-providers", "fluentd", "kyverno-policies", "gitea-actions-runner",
        # Wave 5
        "monitoring-extras",
        # Wave 6
        "crossplane-provider-configs",
        # Wave 7
        "crossplane-xrds", "crossplane-harbor-bootstrap",
        # Wave 8
        "core-catalog",
        # Wave 9/10 (cnpg, cnpg-cluster) intentionally excluded — see above.
        # Wave 11
        "app-config",
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

# Return a bounded Kubernetes condition verdict. Only the condition's
# name/status/reason are retained; message and the rest of the API body are
# deliberately ignored because controller messages can contain sensitive data.
def appclaim_readiness_condition [resource, condition_type: string] {
    let conditions = (try { $resource | get status.conditions } catch { [] })
    let conditions_shape = ($conditions | describe)
    if not (
        ($conditions_shape | str starts-with "list")
        or ($conditions_shape | str starts-with "table")
    ) {
        return {
            ready: false,
            status: "Invalid",
            reason: "ConditionsInvalid",
        }
    }
    let matches = ($conditions | where {|condition|
        try {
            (($condition | get type) == $condition_type)
        } catch {
            false
        }
    })
    let condition = ($matches | get -o 0 | default {})
    let raw_status = (try { $condition | get status } catch { "Missing" })
    let raw_reason = (try { $condition | get reason } catch { "ConditionMissing" })
    let status = if (($raw_status | describe) == "string") {
        $raw_status
    } else {
        "Invalid"
    }
    let reason = if (($raw_reason | describe) == "string") {
        $raw_reason
    } else {
        "ReasonInvalid"
    }
    {
        ready: ($status == "True"),
        status: $status,
        reason: (redact_sync_diagnostic $reason),
    }
}

# Parse one readiness API response without ever echoing its body. Empty or
# malformed successful responses are deterministic failures rather than
# retryable states, because retrying them would conceal a broken kubectl/API
# contract for the entire poll budget.
def parse_appclaim_readiness_json [resource_name: string, result: record] {
    if ($result.stdout | str trim | is-empty) {
        error make {
            msg: $"($resource_name): status=Invalid reason=EmptyData"
        }
    }
    let parsed = (try { $result.stdout | from json } catch { null })
    if not (($parsed | describe) | str starts-with "record") {
        error make {
            msg: $"($resource_name): status=Invalid reason=InvalidJSON"
        }
    }
    $parsed
}

# Crossplane can report its Argo-managed XRD as converged before the generated
# composite/claim APIs are usable. Poll the raw APIs so discovery-cache lag
# cannot produce a false negative, and pass the repository kubeconfig on every
# call so the caller's original KUBECONFIG remains untouched (including on
# Windows Docker Desktop and when the working path contains spaces).
def wait_for_appclaim_api_ready [
    max_attempts: int = 20,
    poll_interval: duration = 2sec,
] {
    if $max_attempts < 1 {
        error make {msg: "AppClaim API readiness requires a positive attempt budget"}
    }

    let xrd_name = "applications.platform.digiorg.io"
    let xrd_path = "/apis/apiextensions.crossplane.io/v1/compositeresourcedefinitions/applications.platform.digiorg.io"
    let crds = [
        {
            name: "applications.platform.digiorg.io",
            path: "/apis/apiextensions.k8s.io/v1/customresourcedefinitions/applications.platform.digiorg.io",
        },
        {
            name: "appclaims.platform.digiorg.io",
            path: "/apis/apiextensions.k8s.io/v1/customresourcedefinitions/appclaims.platform.digiorg.io",
        },
    ]

    print "Waiting for the AppClaim XRD and generated CRDs to become ready..."
    mut last_diagnostics = []
    for attempt in 1..$max_attempts {
        mut diagnostics = []

        let xrd_result = (do {
            kubectl --kubeconfig $KUBECONFIG_PATH --request-timeout=2s get --raw $xrd_path
        } | complete)
        if $xrd_result.exit_code != 0 {
            $diagnostics = ($diagnostics | append $"XRD/($xrd_name): status=Unavailable reason=RequestFailed")
        } else {
            let xrd = (parse_appclaim_readiness_json $"XRD/($xrd_name)" $xrd_result)
            for condition_type in ["Established", "Offered"] {
                let verdict = (appclaim_readiness_condition $xrd $condition_type)
                if not $verdict.ready {
                    $diagnostics = ($diagnostics | append $"XRD/($xrd_name) ($condition_type): status=($verdict.status) reason=($verdict.reason)")
                }
            }

            let controller_fields = [
                {
                    name: "compositeResourceType.kind",
                    value: (try { $xrd | get status.controllers.compositeResourceType.kind } catch { "" }),
                    expected: "Application",
                },
                {
                    name: "compositeResourceType.apiVersion",
                    value: (try { $xrd | get status.controllers.compositeResourceType.apiVersion } catch { "" }),
                    expected: "platform.digiorg.io/v1alpha1",
                },
                {
                    name: "compositeResourceClaimType.kind",
                    value: (try { $xrd | get status.controllers.compositeResourceClaimType.kind } catch { "" }),
                    expected: "AppClaim",
                },
                {
                    name: "compositeResourceClaimType.apiVersion",
                    value: (try { $xrd | get status.controllers.compositeResourceClaimType.apiVersion } catch { "" }),
                    expected: "platform.digiorg.io/v1alpha1",
                },
            ]
            for field in $controller_fields {
                let value = $field.value
                let is_string = (($value | describe) == "string")
                let matches = $is_string and ($value == $field.expected)
                if not $matches {
                    let missing = (not $is_string) or ($value | is-empty)
                    let status = if $missing { "Empty" } else { "Unexpected" }
                    let reason = if $missing { "ControllerIdentityMissing" } else { "ControllerIdentityMismatch" }
                    $diagnostics = ($diagnostics | append $"XRD/($xrd_name) ($field.name): status=($status) reason=($reason)")
                }
            }
        }

        for crd in $crds {
            let crd_result = (do {
                kubectl --kubeconfig $KUBECONFIG_PATH --request-timeout=2s get --raw $crd.path
            } | complete)
            if $crd_result.exit_code != 0 {
                $diagnostics = ($diagnostics | append $"CRD/($crd.name) Established: status=Unavailable reason=RequestFailed")
                continue
            }
            let resource = (parse_appclaim_readiness_json $"CRD/($crd.name)" $crd_result)
            let verdict = (appclaim_readiness_condition $resource "Established")
            if not $verdict.ready {
                $diagnostics = ($diagnostics | append $"CRD/($crd.name) Established: status=($verdict.status) reason=($verdict.reason)")
            }
        }

        if ($diagnostics | is-empty) {
            print $"(ansi green)✓ AppClaim API is ready: XRD Established+Offered, controller identities published, generated CRDs Established(ansi reset)"
            return
        }
        $last_diagnostics = $diagnostics
        if $attempt == $max_attempts {
            let bounded = ($last_diagnostics | first 20 | str join ", ")
            error make {
                msg: $"AppClaim API readiness timed out [attempt ($attempt)/($max_attempts)]: ($bounded)"
            }
        }
        sleep $poll_interval
    }
}

# -----------------------------------------------------------------------------
# Phase 3: Configure Apps Functions
# -----------------------------------------------------------------------------

# Wait only for the direct dependencies of an identity-configuration phase.
# This lets Gitea/SonarQube configuration proceed independently of unrelated
# late-wave drift while remaining bounded and fail-closed. App and Certificate
# names are trusted repository constants; no untrusted operation messages or
# Secret data are emitted.
def wait_for_configuration_dependencies [
    phase: string,
    apps: list,
    certificates: list,
    --poll-delay: duration = 5sec,
    --transient-grace-attempts: int = 15,
] {
    $env.KUBECONFIG = $KUBECONFIG_PATH

    let normal_attempts = 60
    let max_attempts = ($normal_attempts + $transient_grace_attempts)
    mut last_non_ready = []
    for attempt in 1..$max_attempts {
        mut non_ready = []
        mut transient_grace_eligible = true

        for app in $apps {
            let result = (do { kubectl get application $app -n argocd -o json } | complete)
            if $result.exit_code != 0 {
                $non_ready = ($non_ready | append $"Application/($app)=missing")
                $transient_grace_eligible = false
                continue
            }
            let state = (try { $result.stdout | from json } catch { null })
            if ($state | describe | str starts-with "record") == false {
                $non_ready = ($non_ready | append $"Application/($app)=invalid-status")
                $transient_grace_eligible = false
                continue
            }
            let health = ($state | get -o status.health.status | default "Unknown")
            let sync = ($state | get -o status.sync.status | default "Unknown")
            if not ($health == "Healthy" and $sync == "Synced") {
                $non_ready = ($non_ready | append $"Application/($app)=health:($health),sync:($sync)")
                let conditions = ($state | get -o status.conditions | default [])
                let condition_messages = (
                    $conditions
                    | each {|condition| $condition | get -o message | default "" }
                )
                let transient_unknown = (
                    ($health == "Healthy")
                    and ($sync == "Unknown")
                    and (not ($condition_messages | is-empty))
                    and ($condition_messages | all {|message| is_retryable_sync_error $message })
                )
                if not $transient_unknown {
                    $transient_grace_eligible = false
                }
            }
        }

        for cert in $certificates {
            let namespace = ($cert | get namespace)
            let name = ($cert | get name)
            let result = (do { kubectl get certificate $name -n $namespace -o json } | complete)
            if $result.exit_code != 0 {
                $non_ready = ($non_ready | append $"Certificate/($namespace)/($name)=missing")
                $transient_grace_eligible = false
                continue
            }
            let resource = (try { $result.stdout | from json } catch { null })
            if ($resource | describe | str starts-with "record") == false {
                $non_ready = ($non_ready | append $"Certificate/($namespace)/($name)=invalid-status")
                $transient_grace_eligible = false
                continue
            }
            let conditions = ($resource | get -o status.conditions | default [])
            let ready = ($conditions | any {|condition|
                (($condition | get -o type | default "") == "Ready") and (($condition | get -o status | default "") == "True")
            })
            if not $ready {
                $non_ready = ($non_ready | append $"Certificate/($namespace)/($name)=Ready:False")
                $transient_grace_eligible = false
            }
        }

        $last_non_ready = $non_ready
        if ($non_ready | is-empty) {
            print $"(ansi green)✓ ($phase) configuration dependencies are ready(ansi reset)"
            return
        }

        if ($attempt > 55) and ($attempt <= $normal_attempts) {
            print $"  ($phase) dependencies pending: (($non_ready | str join ', ')) [attempt ($attempt)/($normal_attempts)]"
        }

        if $attempt >= $normal_attempts {
            if not $transient_grace_eligible {
                break
            }
            if $attempt > $normal_attempts {
                print $"  ($phase) transient comparison grace: (($non_ready | str join ', ')) [grace ($attempt - $normal_attempts)/($transient_grace_attempts)]"
            }
            if $attempt == $max_attempts {
                break
            }
        }
        sleep $poll_delay
    }

    let bounded = ($last_non_ready | first 20 | str join ", ")
    error make {msg: $"($phase) configuration dependencies did not become ready: ($bounded)"}
}

# Remove client-side apply's credential-bearing metadata copy. Removal is safe
# when the annotation is already absent and is verified without reading data.
def scrub_secret_last_applied_annotation [namespace: string, name: string] {
    let scrub_result = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH annotate secret $name -n $namespace kubectl.kubernetes.io/last-applied-configuration- --overwrite
    } | complete)
    if $scrub_result.exit_code != 0 {
        error make {msg: $"Failed to scrub client-side apply metadata from secret ($namespace)/($name)"}
    }
    let annotation_result = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH get secret $name -n $namespace -o jsonpath='{.metadata.annotations.kubectl\.kubernetes\.io/last-applied-configuration}'
    } | complete)
    if ($annotation_result.exit_code != 0) or (not ($annotation_result.stdout | str trim | is-empty)) {
        error make {msg: $"Secret ($namespace)/($name) still contains client-side apply metadata"}
    }
}

# Generic single-key opaque Secret writer (Issue #285). Remove client-side
# apply's annotation before the first SSA write so kubectl cannot migrate and
# then prune ownership for the whole object. Apply only the target data key with
# a stable per-key manager; unrelated keys can change concurrently and survive.
# No credential enters argv, environment variables, logs, or disk.
def persist_opaque_secret [
    namespace: string,
    name: string,
    key: string,
    value: string,
    --annotation-key: string = "",
    --annotation-value: string = "",
] {
    if ($value | is-empty) {
        error make {msg: $"Refusing to persist an empty value for secret ($namespace)/($name) key ($key)"}
    }
    if (($annotation_key | is-empty) != ($annotation_value | is-empty)) {
        error make {msg: $"Secret ($namespace)/($name) annotation key and value must be provided together"}
    }
    let exists_result = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH get secret $name -n $namespace -o name --ignore-not-found
    } | complete)
    if $exists_result.exit_code != 0 {
        error make {msg: $"Failed to inspect secret ($namespace)/($name) before persisting key ($key)"}
    }
    if not ($exists_result.stdout | str trim | is-empty) {
        # This ordering is critical: without it, kubectl's client-side apply
        # migration can transfer ownership of unrelated fields to this manager.
        scrub_secret_last_applied_annotation $namespace $name
    }

    let encoded = ($value | encode base64)
    let field_manager = $"digiorg-bootstrap-secret-($key | str replace --all '.' '-')"
    let secret_metadata = if ($annotation_key | is-empty) {
        {name: $name, namespace: $namespace}
    } else {
        {
            name: $name
            namespace: $namespace
            annotations: {($annotation_key): $annotation_value}
        }
    }
    let manifest = {
        apiVersion: "v1"
        kind: "Secret"
        metadata: $secret_metadata
        type: "Opaque"
        data: {($key): $encoded}
    }
    let apply_result = (do {
        $manifest | to json | kubectl --kubeconfig $KUBECONFIG_PATH apply --server-side --force-conflicts --field-manager $field_manager -f -
    } | complete)
    if $apply_result.exit_code != 0 {
        error make {msg: $"Failed to persist secret ($namespace)/($name)"}
    }
    scrub_secret_last_applied_annotation $namespace $name

    let readback = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH get secret $name -n $namespace -o json
    } | complete)
    if $readback.exit_code != 0 {
        error make {msg: $"Failed to verify the persisted secret ($namespace)/($name)"}
    }
    let verified = (try {
        let secret = ($readback.stdout | from json)
        let persisted_value = ($secret.data | get $key | decode base64 | decode utf-8)
        let persisted_annotation = if ($annotation_key | is-empty) {
            ""
        } else {
            $secret.metadata.annotations | get $annotation_key
        }
        {
            value: $persisted_value
            annotation: $persisted_annotation
        }
    } catch { {value: "", annotation: ""} })
    if ($verified.value | is-empty) or ($verified.value != $value) {
        error make {msg: $"Persisted secret ($namespace)/($name) did not match its source"}
    }
    if (not ($annotation_key | is-empty)) and ($verified.annotation != $annotation_value) {
        error make {msg: $"Persisted secret ($namespace)/($name) annotation contract did not match its source"}
    }
}

# Write (or resume-preserve) the Argo CD repository credential. Existing
# credentials are not rotated implicitly, but stale client-side metadata is
# removed on every run.
def persist_argocd_repo_secret [name: string, repo_url: string, username: string, password: string] {
    if ($password | is-empty) {
        error make {msg: $"Refusing to persist an empty password for ArgoCD repo credential ($name)"}
    }
    let existing = ((do -i { kubectl --kubeconfig $KUBECONFIG_PATH get secret $name -n argocd } | complete).exit_code == 0)
    if $existing {
        scrub_secret_last_applied_annotation "argocd" $name
        print $"(ansi yellow)✓ ArgoCD repository credential '($name)' already present — preserved(ansi reset)"
        return
    }
    let manifest = ({
        apiVersion: "v1"
        kind: "Secret"
        metadata: {
            name: $name
            namespace: "argocd"
            labels: {"argocd.argoproj.io/secret-type": "repository"}
        }
        type: "Opaque"
        data: {
            url: ($repo_url | encode base64)
            username: ($username | encode base64)
            password: ($password | encode base64)
        }
    } | to json)
    let apply_result = (do {
        $manifest | kubectl --kubeconfig $KUBECONFIG_PATH apply --server-side --force-conflicts --field-manager digiorg-bootstrap-argocd-repository -f -
    } | complete)
    if $apply_result.exit_code != 0 {
        error make {msg: $"Failed to persist ArgoCD repository credential ($name)"}
    }
    scrub_secret_last_applied_annotation "argocd" $name
    let readback = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH get secret $name -n argocd -o jsonpath='{.data.password}'
    } | complete)
    if $readback.exit_code != 0 {
        error make {msg: $"Failed to verify the persisted ArgoCD repository credential ($name)"}
    }
    let persisted = (try { $readback.stdout | str trim | decode base64 | decode utf-8 } catch { "" })
    if ($persisted | is-empty) or ($persisted != $password) {
        error make {msg: $"Persisted ArgoCD repository credential ($name) did not match its source"}
    }
}

# Generate or validate the dedicated NACK user NKey. The digest-pinned Linux
# tool container works on Docker Engine/Desktop without a host nsc/nk install.
# The private seed stays in memory and is passed to nk over stdin only.
def ensure_nats_jetstream_controller_nkey [] {
    let name = "nats-jetstream-controller-nkey"
    let tool_image = "natsio/nats-box:0.19.2@sha256:8031d190c7ee24081f3f27cc939fb647a1eeb29ebb5c60fef9b5b6c7a846d6a2"
    let exists = ((do -i {
        kubectl --kubeconfig $KUBECONFIG_PATH get secret $name -n messaging -o name
    } | complete).exit_code == 0)

    if $exists {
        let seed_result = (do {
            kubectl --kubeconfig $KUBECONFIG_PATH get secret $name -n messaging -o jsonpath='{.data.seed\.nk}'
        } | complete)
        let public_result = (do {
            kubectl --kubeconfig $KUBECONFIG_PATH get secret $name -n messaging -o jsonpath='{.data.public\.nk}'
        } | complete)
        if $seed_result.exit_code != 0 or $public_result.exit_code != 0 {
            error make {msg: "Failed to read the persisted NATS controller NKey pair"}
        }
        let seed = (try { $seed_result.stdout | str trim | decode base64 | decode utf-8 } catch { "" })
        let public = (try { $public_result.stdout | str trim | decode base64 | decode utf-8 } catch { "" })
        if ($seed | is-empty) {
            error make {msg: "Persisted NATS controller NKey seed is missing"}
        }
        let derived_result = (do {
            $seed | docker run --rm -i $tool_image nk -inkey /dev/stdin -pubout
        } | complete)
        let derived = ($derived_result.stdout | str trim)
        if ($derived_result.exit_code != 0) or (not ($derived | str starts-with "U")) {
            error make {msg: "Persisted NATS controller seed is invalid"}
        }
        if ($public | is-empty) {
            persist_opaque_secret "messaging" $name "public.nk" $derived
            print "✓ Repaired missing NATS JetStream controller public NKey from persisted seed"
        } else if $derived != $public {
            error make {msg: "Persisted NATS controller seed does not match its public NKey"}
        }
        scrub_secret_last_applied_annotation "messaging" $name
        print "✓ NATS JetStream controller NKey pair already present and verified"
        return
    }

    let seed_result = (do {
        docker run --rm $tool_image nk -gen user
    } | complete)
    let seed = ($seed_result.stdout | str trim)
    if ($seed_result.exit_code != 0) or (not ($seed | str starts-with "SU")) {
        error make {msg: "Failed to generate a NATS user seed"}
    }
    let public_result = (do {
        $seed | docker run --rm -i $tool_image nk -inkey /dev/stdin -pubout
    } | complete)
    let public = ($public_result.stdout | str trim)
    if ($public_result.exit_code != 0) or (not ($public | str starts-with "U")) {
        error make {msg: "Failed to derive the NATS user public key"}
    }
    persist_opaque_secret "messaging" "nats-jetstream-controller-nkey" "seed.nk" $seed
    persist_opaque_secret "messaging" "nats-jetstream-controller-nkey" "public.nk" $public
    print "✓ Dedicated NATS JetStream controller NKey created"
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

# Create a Gitea user whose password is generated server-side by the `gitea`
# CLI itself (`--random-password`) rather than interpolated into this exec's
# process argument (Issue #285 blocker #3: a `ps`-visible generated password
# on the node). The generated password is intentionally never captured or
# printed -- every identity this helper creates authenticates solely via a
# later admin-generated access token, so nothing of value is discarded.
def gitea_create_user_random_password [gitea_pod: string, username: string, email: string, is_admin: bool] {
    let admin_flag = if $is_admin { "true" } else { "false" }
    let create_result = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- su git -c $'gitea admin user create --username "($username)" --email "($email)" --random-password --random-password-length 32 --must-change-password=false --admin ($admin_flag)'
    } | complete)
    if $create_result.exit_code != 0 {
        error make {msg: $"Failed to create the Gitea user ($username)"}
    }
}

# Gitea's Bool flag requires --must-change-password=false at creation. Repair
# identities created by older bootstraps as well; otherwise Git Smart HTTP with
# a PAT is redirected to the interactive password-change page.
def gitea_unset_service_user_must_change_password [gitea_pod: string, username: string] {
    if not ($username =~ '^[a-z0-9][a-z0-9-]{0,38}$') {
        error make {msg: "Refusing an invalid Gitea service username"}
    }
    let unset_result = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- su git -c $'gitea admin user must-change-password --unset "($username)"'
    } | complete)
    if $unset_result.exit_code != 0 {
        error make {msg: $"Failed to clear must-change-password for Gitea service user ($username)"}
    }
}

# Configure Gitea (add Keycloak OIDC provider + create initial users/org)
def configure_gitea [] {
    $env.KUBECONFIG = $KUBECONFIG_PATH

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

    # --- Step 1: Add Keycloak as OIDC authentication source ---
    print "1. Configuring Keycloak OIDC provider in Gitea..."
    
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

    # --- Step 2: Create initial users in Gitea (resume-safe) ---
    print "2. Ensuring initial users exist in Gitea..."

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

    # --- Step 3: Create DigiOrg organisation via the Gitea API ---
    print "3. Ensuring DigiOrg organisation exists in Gitea..."

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
    } else if (kubectl_error_is_exact_not_found $token_secret.stderr "secrets" "gitea-bootstrap-token") {
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
        $gitea_token | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n gitea $gitea_pod -c gitea -- sh -c 'IFS= read -r token; printf "header = \"Authorization: token %s\"\n" "$token" | curl --config - --cacert /etc/gitea/trusted-cas/digiorg-local-ca.crt -sS -o /dev/null -w "%{http_code}" https://digiorg.local/gitea/api/v1/orgs/DigiOrg'
    } | complete)
    if $org_check.exit_code != 0 {
        error make {msg: "Failed to query the DigiOrg organisation in Gitea"}
    }
    let org_status = ($org_check.stdout | str trim)
    if $org_status == "200" {
        print $"(ansi yellow)✓ Organisation 'DigiOrg' already exists(ansi reset)"
    } else if $org_status == "404" {
        let org_create = (do {
            $gitea_token | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n gitea $gitea_pod -c gitea -- sh -c 'IFS= read -r token; printf "header = \"Authorization: token %s\"\n" "$token" | curl --config - --cacert /etc/gitea/trusted-cas/digiorg-local-ca.crt -sS -o /dev/null -w "%{http_code}" -X POST -H "Content-Type: application/json" --data "{\"username\":\"DigiOrg\",\"full_name\":\"DigiOrg Organization\",\"visibility\":\"public\",\"repo_admin_change_team_access\":true}" https://digiorg.local/gitea/api/v1/orgs'
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
        $gitea_token | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n gitea $gitea_pod -c gitea -- sh -c 'IFS= read -r token; printf "header = \"Authorization: token %s\"\n" "$token" | curl --config - --cacert /etc/gitea/trusted-cas/digiorg-local-ca.crt -fsS https://digiorg.local/gitea/api/v1/orgs/DigiOrg/teams'
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
        $gitea_token | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n gitea $gitea_pod -c gitea -- sh -c $'IFS= read -r token; printf "header = \"Authorization: token %s\"\n" "$token" | curl --config - --cacert /etc/gitea/trusted-cas/digiorg-local-ca.crt -sS -o /dev/null -w "%{http_code}" https://digiorg.local/gitea/api/v1/teams/($owners_team_id)/members/digiorgadmin'
    } | complete)

    if ($admin_check.exit_code == 0) and (($admin_check.stdout | str trim) == "200") {
        print $"(ansi yellow)✓ 'digiorgadmin' already member of Owners team(ansi reset)"
    } else {
        let admin_add = (do {
            $gitea_token | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n gitea $gitea_pod -c gitea -- sh -c $'IFS= read -r token; printf "header = \"Authorization: token %s\"\n" "$token" | curl --config - --cacert /etc/gitea/trusted-cas/digiorg-local-ca.crt -fsS -X PUT https://digiorg.local/gitea/api/v1/teams/($owners_team_id)/members/digiorgadmin'
        } | complete)
        if $admin_add.exit_code == 0 {
            print $"(ansi green)✓ 'digiorgadmin' added to Owners team(ansi reset)"
        } else {
            error make {msg: "Failed to add digiorgadmin to the Gitea Owners team"}
        }
    }

    # 4h: Add digiorgdeveloper to Owners team (idempotent)
    let dev_check = (do {
        $gitea_token | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n gitea $gitea_pod -c gitea -- sh -c $'IFS= read -r token; printf "header = \"Authorization: token %s\"\n" "$token" | curl --config - --cacert /etc/gitea/trusted-cas/digiorg-local-ca.crt -sS -o /dev/null -w "%{http_code}" https://digiorg.local/gitea/api/v1/teams/($owners_team_id)/members/digiorgdeveloper'
    } | complete)

    if ($dev_check.exit_code == 0) and (($dev_check.stdout | str trim) == "200") {
        print $"(ansi yellow)✓ 'digiorgdeveloper' already member of Owners team(ansi reset)"
    } else {
        let dev_add = (do {
            $gitea_token | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n gitea $gitea_pod -c gitea -- sh -c $'IFS= read -r token; printf "header = \"Authorization: token %s\"\n" "$token" | curl --config - --cacert /etc/gitea/trusted-cas/digiorg-local-ca.crt -fsS -X PUT https://digiorg.local/gitea/api/v1/teams/($owners_team_id)/members/digiorgdeveloper'
        } | complete)
        if $dev_add.exit_code == 0 {
            print $"(ansi green)✓ 'digiorgdeveloper' added to Owners team(ansi reset)"
        } else {
            error make {msg: "Failed to add digiorgdeveloper to the Gitea Owners team"}
        }
    }

    # 4i: Verification — list Owners team members
    let verify = (do {
        $gitea_token | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n gitea $gitea_pod -c gitea -- sh -c $'IFS= read -r token; printf "header = \"Authorization: token %s\"\n" "$token" | curl --config - --cacert /etc/gitea/trusted-cas/digiorg-local-ca.crt -fsS https://digiorg.local/gitea/api/v1/teams/($owners_team_id)/members'
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

    # --- Step 5: Create the app-config GitOps sink repository (Issue #285) ---
    print "5. Ensuring the app-config repository exists..."
    configure_app_config_repo $gitea_pod $gitea_token

    # --- Step 6: Least-privilege Crossplane Gitea credentials (Issue #285) ---
    print "6. Ensuring least-privilege Crossplane Gitea credentials exist..."
    configure_crossplane_gitea_credentials $gitea_pod $gitea_token

    # --- Step 7: Least-privilege ArgoCD access to app-config (Issue #285) ---
    print "7. Ensuring ArgoCD has least-privilege read-only access to app-config..."
    configure_argocd_gitea_access $gitea_pod $gitea_token

    # --- Step 8: Least-privilege Backstage publish credential (Issue #285) ---
    print "8. Ensuring Backstage has a least-privilege GITEA_TOKEN for app-config publishing..."
    configure_backstage_gitea_publisher $gitea_pod $gitea_token

    # --- Step 9: Gitea Actions runner registration (Issue #285) ---
    print "9. Ensuring the Gitea Actions runner is registered and online..."
    configure_gitea_actions_runner $gitea_pod $gitea_token
}

# Reduce a failed seed GET's stderr to a fixed non-secret diagnostic enum. The
# raw text is intentionally never printed: kubectl/curl diagnostics are not a
# stable API and future versions could add request details.
def classify_app_config_seed_query_error [stderr: string] {
    let normalized = ($stderr | str lowercase)
    if ($normalized | str contains "could not resolve host") or ($normalized | str contains "server misbehaving") or ($normalized | str contains "no such host") {
        "dns"
    } else if ($normalized | str contains "failed to connect") or ($normalized | str contains "connection refused") or ($normalized | str contains "connection reset") {
        "connection"
    } else if ($normalized | str contains "certificate") or ($normalized | str contains "x509") {
        "tls"
    } else if ($normalized | str contains "unable to upgrade connection") or ($normalized | str contains "container not found") or ($normalized | str contains "pod not found") {
        "exec"
    } else if ($normalized | str contains "deadline exceeded") or ($normalized | str contains "timed out") or ($normalized | str contains "timeout") {
        "timeout"
    } else {
        "unknown"
    }
}

# Create (idempotently) the private DigiOrg/app-config repository that is the
# GitOps sink for generated AppClaim manifests (Issue #285 P0: "no declared
# GitOps sink exists for generated manifests"), and seed the `claims/`
# directory that the app-config ArgoCD Application (apps/platform/app-config.yaml)
# watches -- this directory name MUST match core-portal's (already tested)
# publishPhase.git.targetPath exactly, or every merged AppClaim PR silently
# never reconciles. Uses the existing admin bootstrap token: this is one-time
# platform bootstrap, not a per-AppClaim credential (see
# configure_crossplane_gitea_credentials for that separate, narrower identity).
def configure_app_config_repo [
    gitea_pod: string,
    gitea_token: string,
    --retry-delay: duration = 10sec,
] {
    if not ($gitea_token =~ '^[0-9a-f]{40}$') {
        error make {msg: "Refusing a malformed Gitea bootstrap token"}
    }
    let repo_check = (do {
        $gitea_token | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n gitea $gitea_pod -c gitea -- sh -c 'IFS= read -r token; printf "header = \"Authorization: token %s\"\n" "$token" | curl --config - --cacert /etc/gitea/trusted-cas/digiorg-local-ca.crt -sS -o /dev/null -w "%{http_code}" https://digiorg.local/gitea/api/v1/repos/DigiOrg/app-config'
    } | complete)
    if $repo_check.exit_code != 0 {
        error make {msg: "Failed to query the DigiOrg/app-config repository in Gitea"}
    }
    let repo_status = ($repo_check.stdout | str trim)
    if $repo_status == "200" {
        print $"(ansi yellow)✓ Repository 'DigiOrg/app-config' already exists(ansi reset)"
    } else if $repo_status == "404" {
        let repo_create = (do {
            $gitea_token | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n gitea $gitea_pod -c gitea -- sh -c 'IFS= read -r token; printf "header = \"Authorization: token %s\"\n" "$token" | curl --config - --cacert /etc/gitea/trusted-cas/digiorg-local-ca.crt -sS -o /dev/null -w "%{http_code}" -X POST -H "Content-Type: application/json" --data "{\"name\":\"app-config\",\"description\":\"GitOps sink for generated AppClaim manifests (Issue #285). ArgoCD watches claims/ automatically.\",\"private\":true,\"auto_init\":true,\"default_branch\":\"main\"}" https://digiorg.local/gitea/api/v1/orgs/DigiOrg/repos'
        } | complete)
        if $repo_create.exit_code != 0 or (($repo_create.stdout | str trim) != "201") {
            error make {msg: "Failed to create the DigiOrg/app-config repository in Gitea"}
        }
        print $"(ansi green)✓ Repository 'DigiOrg/app-config' created \(private\)(ansi reset)"
    } else {
        error make {msg: $"Unexpected HTTP status while querying DigiOrg/app-config: ($repo_status)"}
    }

    # Seed claims/.gitkeep so the directory the app-config Application
    # watches exists from the first sync, even before any AppClaim PR merges.
    let seed_check = (do {
        $gitea_token | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n gitea $gitea_pod -c gitea -- sh -c 'IFS= read -r token; printf "header = \"Authorization: token %s\"\n" "$token" | curl --config - --cacert /etc/gitea/trusted-cas/digiorg-local-ca.crt -sS -o /dev/null -w "%{http_code}" https://digiorg.local/gitea/api/v1/repos/DigiOrg/app-config/contents/claims/.gitkeep'
    } | complete)
    if $seed_check.exit_code != 0 {
        error make {msg: "Failed to query the app-config claims/.gitkeep seed file"}
    }
    let seed_status = ($seed_check.stdout | str trim)
    if $seed_status == "200" {
        print $"(ansi yellow)✓ 'claims/' already seeded(ansi reset)"
    } else if $seed_status == "404" {
        let seed_content = ("" | encode base64)
        let seed_create = (do {
            $gitea_token | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n gitea $gitea_pod -c gitea -- sh -c $'IFS= read -r token; printf "header = \"Authorization: token %s\"\n" "$token" | curl --config - --cacert /etc/gitea/trusted-cas/digiorg-local-ca.crt -sS -o /dev/null -w "%{http_code}" -X POST -H "Content-Type: application/json" --data "{\"content\":\"($seed_content)\",\"message\":\"chore: seed claims directory, issue 285\",\"branch\":\"main\"}" https://digiorg.local/gitea/api/v1/repos/DigiOrg/app-config/contents/claims/.gitkeep'
        } | complete)
        if $seed_create.exit_code != 0 or (($seed_create.stdout | str trim) != "201") {
            error make {msg: "Failed to seed the app-config claims/ directory"}
        }
        print $"(ansi green)✓ 'claims/' seeded(ansi reset)"
    } else {
        error make {msg: $"Unexpected HTTP status while checking the claims/ seed file: ($seed_status)"}
    }

    # The local app-config repository is stored inside the disposable KinD
    # cluster. Seed the already-approved Issue #301 development Claim through
    # Git so a fresh bootstrap reproduces the complete delivery chain. Preserve
    # any existing file byte-for-byte: subsequent portal PRs own its lifecycle.
    let app_claim_seed_target = "claims/digiorg-core-dev/app-claims/AppClaim/myapp.yaml"
    let app_claim_seed_path = ($env.PWD | path join "bootstrap/app-config/claims/digiorg-core-dev/app-claims/AppClaim/myapp.yaml")
    let app_claim_seed_ca = ($env.PWD | path join "digiorg-local-ca.crt")
    mut app_claim_seed_check = {exit_code: 1, stdout: "", stderr: ""}
    mut app_claim_seed_status = ""
    mut app_claim_seed_transport_ok = false
    for attempt in 1..5 {
        $app_claim_seed_check = (do {
            $"header = \"Authorization: token ($gitea_token)\"\n" | curl --connect-timeout 5 --max-time 10 --config - --resolve digiorg.local:443:127.0.0.1 --cacert $app_claim_seed_ca -sS -o /dev/null -w "%{http_code}" $"https://digiorg.local/gitea/api/v1/repos/DigiOrg/app-config/contents/($app_claim_seed_target)"
        } | complete)
        if $app_claim_seed_check.exit_code != 0 {
            let error_class = (classify_app_config_seed_query_error $app_claim_seed_check.stderr)
            print $"  app-config seed GET retry class: ($error_class); curl exit: ($app_claim_seed_check.exit_code) [attempt ($attempt)/5]"
        } else {
            let http_status = $app_claim_seed_check.stdout
            if not ($http_status =~ '^[0-9]{3}$') {
                error make {msg: "Malformed app-config seed GET transport response"}
            }
            $app_claim_seed_status = $http_status
            $app_claim_seed_transport_ok = true
            break
        }
        if $attempt < 5 {
            sleep $retry_delay
        }
    }
    if not $app_claim_seed_transport_ok {
        error make {msg: "Failed to query the approved app-config AppClaim seed"}
    }
    if $app_claim_seed_status == "200" {
        print $"(ansi yellow)✓ Approved app-config AppClaim already exists; preserving repository state(ansi reset)"
    } else if $app_claim_seed_status == "404" {
        let app_claim_seed_content = (open --raw $app_claim_seed_path | encode base64)
        let app_claim_seed_create = (do {
            $"header = \"Authorization: token ($gitea_token)\"\n" | curl --connect-timeout 5 --max-time 10 --config - --resolve digiorg.local:443:127.0.0.1 --cacert $app_claim_seed_ca -sS -o /dev/null -w "%{http_code}" -X POST -H "Content-Type: application/json" --data $"{\"content\":\"($app_claim_seed_content)\",\"message\":\"feat: seed approved Issue 301 AppClaim\",\"branch\":\"main\"}" $"https://digiorg.local/gitea/api/v1/repos/DigiOrg/app-config/contents/($app_claim_seed_target)"
        } | complete)
        if $app_claim_seed_create.exit_code != 0 or ($app_claim_seed_create.stdout != "201") {
            error make {msg: "Failed to seed the approved app-config AppClaim"}
        }
        print $"(ansi green)✓ Approved Issue #301 AppClaim seeded through app-config Git(ansi reset)"
    } else {
        error make {msg: $"Unexpected HTTP status while checking the approved AppClaim seed: ($app_claim_seed_status)"}
    }
}

# Parse only the non-secret resume state required for scope-contract decisions.
# Malformed JSON, missing/invalid base64, and empty token values fail closed;
# the token itself is never returned or printed.
def parse_crossplane_gitea_secret_state [raw: string, scope_annotation: string] {
    let secret = (try {
        $raw | from json
    } catch {
        error make {msg: "crossplane-gitea-credentials is not valid JSON"}
    })
    let encoded_token = (try {
        $secret.data | get token | into string | str trim
    } catch {
        error make {msg: "crossplane-gitea-credentials is missing data.token"}
    })
    if ($encoded_token | is-empty) {
        error make {msg: "crossplane-gitea-credentials contains an empty encoded token"}
    }
    let decoded_token = (try {
        $encoded_token | decode base64 | decode utf-8 | str trim
    } catch {
        error make {msg: "crossplane-gitea-credentials contains an invalid encoded token"}
    })
    if ($decoded_token | is-empty) {
        error make {msg: "crossplane-gitea-credentials contains an empty token"}
    }
    let scope_contract_current = (try {
        $secret.metadata.annotations | get $scope_annotation | into string | str trim
    } catch { "" })
    {scope_contract_current: $scope_contract_current}
}

# Gitea v1.26.1 PATCH /teams/{id} returns both deprecated units and units_map.
# Keep the legacy/base team permission at none: the granular TeamUnit contract
# alone grants repo.code write, while can_create_org_repo is the separate
# organization-repository creation capability. Require both unit
# representations to reject all-repository/admin or extra-unit access.
def crossplane_gitea_team_is_exact [] {
    let team = $in
    try {
        let units = ($team.units | each {|unit| $unit | into string } | sort)
        let unit_keys = ($team.units_map | columns | sort)
        [
            ($team.name == "platform-provisioners")
            ($team.permission == "none")
            ($team.includes_all_repositories == false)
            ($team.can_create_org_repo == true)
            ($units == ["repo.code"])
            ($unit_keys == ["repo.code"])
            (($team.units_map | get "repo.code") == "write")
        ] | all {|matches| $matches }
    } catch { false }
}

# Create a dedicated, least-privilege Gitea identity for Crossplane's per-app
# repository/CI provisioning (Issue #285 security constraint: "do not reuse a
# broad platform administrator token"). Distinct from gitea_admin (one-time
# platform bootstrap only) and from argocd-reader (read-only GitOps sink
# access, below) -- this identity can only create repositories under DigiOrg
# and push their contents, nothing else. Resume-safe: a token is preserved only
# while its persisted scope contract is current; an unmarked pre-fix Secret is
# rotated once after user/team authorization has been re-verified.
def configure_crossplane_gitea_credentials [gitea_pod: string, gitea_token: string] {
    let scope_contract = "write:organization,write:repository"
    let scope_annotation = "platform.digiorg.io/gitea-token-scopes"
    let secret_result = (do -i {
        kubectl --kubeconfig $KUBECONFIG_PATH get secret crossplane-gitea-credentials -n crossplane-system -o json
    } | complete)
    let secret_exists = ($secret_result.exit_code == 0)
    if (not $secret_exists) and (not (kubectl_error_is_exact_not_found $secret_result.stderr "secrets" "crossplane-gitea-credentials")) {
        error make {msg: "Failed to inspect crossplane-gitea-credentials before scope-contract reconciliation"}
    }
    let scope_contract_current = if $secret_exists {
        let secret_state = (parse_crossplane_gitea_secret_state $secret_result.stdout $scope_annotation)
        $secret_state.scope_contract_current
    } else { "" }

    let users_result = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- su git -c 'gitea admin user list'
    } | complete)
    if $users_result.exit_code != 0 {
        error make {msg: "Failed to list existing Gitea users"}
    }
    if not ($users_result.stdout | lines | any {|line| $line | str contains "crossplane-provisioner" }) {
        gitea_create_user_random_password $gitea_pod "crossplane-provisioner" "crossplane-provisioner@digiorg.local" false
    }
    gitea_unset_service_user_must_change_password $gitea_pod "crossplane-provisioner"

    # Team scoped to repo creation + code push only -- no org administration,
    # no member/webhook management (can_create_org_repo is the specific Gitea
    # team permission that lets a non-Owner member create repos in the org).
    let teams_result = (do {
        $gitea_token | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n gitea $gitea_pod -c gitea -- sh -c 'IFS= read -r token; printf "header = \"Authorization: token %s\"\n" "$token" | curl --config - --cacert /etc/gitea/trusted-cas/digiorg-local-ca.crt -fsS https://digiorg.local/gitea/api/v1/orgs/DigiOrg/teams'
    } | complete)
    if $teams_result.exit_code != 0 {
        error make {msg: "Failed to retrieve the DigiOrg teams from Gitea"}
    }
    let provisioners_team = ($teams_result.stdout | from json | where name == "platform-provisioners")
    let provisioners_team_id = if ($provisioners_team | is-empty) {
        let team_create = (do {
            $gitea_token | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n gitea $gitea_pod -c gitea -- sh -c 'IFS= read -r token; printf "header = \"Authorization: token %s\"\n" "$token" | curl --config - --cacert /etc/gitea/trusted-cas/digiorg-local-ca.crt -fsS -X POST -H "Content-Type: application/json" --data "{\"name\":\"platform-provisioners\",\"description\":\"Least-privilege team: create+push app source repositories only (Issue #285)\",\"permission\":\"none\",\"includes_all_repositories\":false,\"can_create_org_repo\":true,\"units_map\":{\"repo.code\":\"write\"}}" https://digiorg.local/gitea/api/v1/orgs/DigiOrg/teams'
        } | complete)
        if $team_create.exit_code != 0 {
            error make {msg: "Failed to create the platform-provisioners Gitea team"}
        }
        (($team_create.stdout | from json).id)
    } else {
        ($provisioners_team | get id | first)
    }

    # Reconcile existing as well as freshly created teams. Gitea v1.26.1's
    # EditTeam handler replaces Units from units_map and returns the complete
    # Team representation, which is checked before membership/token handling.
    let team_url = $"https://digiorg.local/gitea/api/v1/teams/($provisioners_team_id)"
    let team_reconcile = (do {
        $gitea_token | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n gitea $gitea_pod -c gitea -- sh -c 'IFS= read -r token; printf "header = \"Authorization: token %s\"\n" "$token" | curl --config - --cacert /etc/gitea/trusted-cas/digiorg-local-ca.crt -fsS -X PATCH -H "Content-Type: application/json" --data "{\"name\":\"platform-provisioners\",\"description\":\"Least-privilege team: create+push app source repositories only (Issue #285)\",\"permission\":\"none\",\"includes_all_repositories\":false,\"can_create_org_repo\":true,\"units_map\":{\"repo.code\":\"write\"}}" "$1"' sh $team_url
    } | complete)
    if $team_reconcile.exit_code != 0 {
        error make {msg: "Failed to reconcile the platform-provisioners Gitea team"}
    }
    let reconciled_team = (try {
        $team_reconcile.stdout | from json
    } catch {
        error make {msg: "Gitea returned malformed platform-provisioners team JSON"}
    })
    if not ($reconciled_team | crossplane_gitea_team_is_exact) {
        error make {msg: "platform-provisioners Gitea team did not match the exact least-privilege contract after reconciliation"}
    }
    print $"(ansi green)✓ DigiOrg 'platform-provisioners' team ID: ($provisioners_team_id)(ansi reset)"

    let member_check = (do {
        $gitea_token | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n gitea $gitea_pod -c gitea -- sh -c $'IFS= read -r token; printf "header = \"Authorization: token %s\"\n" "$token" | curl --config - --cacert /etc/gitea/trusted-cas/digiorg-local-ca.crt -sS -o /dev/null -w "%{http_code}" https://digiorg.local/gitea/api/v1/teams/($provisioners_team_id)/members/crossplane-provisioner'
    } | complete)
    if not (($member_check.exit_code == 0) and (($member_check.stdout | str trim) == "200")) {
        let member_add = (do {
            $gitea_token | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n gitea $gitea_pod -c gitea -- sh -c $'IFS= read -r token; printf "header = \"Authorization: token %s\"\n" "$token" | curl --config - --cacert /etc/gitea/trusted-cas/digiorg-local-ca.crt -fsS -X PUT https://digiorg.local/gitea/api/v1/teams/($provisioners_team_id)/members/crossplane-provisioner'
        } | complete)
        if $member_add.exit_code != 0 {
            error make {msg: "Failed to add crossplane-provisioner to the platform-provisioners team"}
        }
    }

    # User/team authorization is always re-verified above. Preserve the token
    # only when its persisted scope contract is current; an unmarked pre-fix
    # Secret rotates once so org-repository creation can succeed on resume.
    if $secret_exists and ($scope_contract_current == $scope_contract) {
        print $"(ansi yellow)✓ 'crossplane-gitea-credentials' already satisfies scope contract — preserved \(membership re-verified\)(ansi reset)"
        return
    }
    if $secret_exists {
        print $"(ansi yellow)↻ Rotating 'crossplane-gitea-credentials' to current least-privilege scope contract(ansi reset)"
    }

    # Gitea's organization-repository endpoint requires write:organization in
    # addition to write:repository. The user's team membership still restricts
    # it to repository creation and code push under DigiOrg.
    let token_name = $"crossplane-((date now | format date '%Y%m%d%H%M%S'))"
    let token_result = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- su git -c $'gitea admin user generate-access-token --username crossplane-provisioner --token-name "($token_name)" --scopes write:organization,write:repository --raw'
    } | complete)
    if $token_result.exit_code != 0 {
        error make {msg: "Failed to generate the crossplane-provisioner access token"}
    }
    let provisioner_token = ($token_result.stdout | str trim)
    if ($provisioner_token | is-empty) {
        error make {msg: "Gitea returned an empty crossplane-provisioner access token"}
    }
    persist_opaque_secret "crossplane-system" "crossplane-gitea-credentials" "token" $provisioner_token --annotation-key $scope_annotation --annotation-value $scope_contract
    print $"(ansi green)✓ Least-privilege 'crossplane-gitea-credentials' reconciled \(write:organization,write:repository\)(ansi reset)"
}

# Create a dedicated, read-only Gitea identity so ArgoCD's repo-server can
# clone DigiOrg/app-config without any Gitea admin credential. The GitOps PR
# destination and the credential that reads it back are a deliberately
# separate concern from the per-app Gitea provisioning identity above (Issue
# #285: "The GitOps pull-request repository and the per-application Gitea
# source repository are separate concerns and must not be conflated").
def configure_argocd_gitea_access [gitea_pod: string, gitea_token: string] {
    let secret_exists = ((do -i { kubectl --kubeconfig $KUBECONFIG_PATH get secret app-config-repo-creds -n argocd } | complete).exit_code == 0)

    let users_result = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- su git -c 'gitea admin user list'
    } | complete)
    if $users_result.exit_code != 0 {
        error make {msg: "Failed to list existing Gitea users"}
    }
    if not ($users_result.stdout | lines | any {|line| $line | str contains "argocd-reader" }) {
        gitea_create_user_random_password $gitea_pod "argocd-reader" "argocd-reader@digiorg.local" false
    }
    gitea_unset_service_user_must_change_password $gitea_pod "argocd-reader"

    # Read-only collaborator on DigiOrg/app-config ONLY -- no org membership,
    # no other repository. Re-applied every run (idempotent PUT) so a resumed
    # run repairs drift even when the token Secret already exists (Issue #285
    # blocker #9).
    let collab_add = (do {
        $gitea_token | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n gitea $gitea_pod -c gitea -- sh -c 'IFS= read -r token; printf "header = \"Authorization: token %s\"\n" "$token" | curl --config - --cacert /etc/gitea/trusted-cas/digiorg-local-ca.crt -fsS -X PUT -H "Content-Type: application/json" --data "{\"permission\":\"read\"}" https://digiorg.local/gitea/api/v1/repos/DigiOrg/app-config/collaborators/argocd-reader'
    } | complete)
    if $collab_add.exit_code != 0 {
        error make {msg: "Failed to grant argocd-reader read-only access to DigiOrg/app-config"}
    }
    print $"(ansi green)✓ 'argocd-reader' has read-only access to DigiOrg/app-config(ansi reset)"

    # Only the token itself is resume-preserved (rotation is an explicit
    # operator action); user existence and collaborator access above are
    # always re-verified/repaired first, even when the Secret already exists.
    if $secret_exists {
        print $"(ansi yellow)✓ ArgoCD app-config repository credential already present — preserved \(membership re-verified\)(ansi reset)"
        return
    }

    # Scoped ONLY to read:repository.
    let token_name = $"argocd-((date now | format date '%Y%m%d%H%M%S'))"
    let token_result = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- su git -c $'gitea admin user generate-access-token --username argocd-reader --token-name "($token_name)" --scopes read:repository --raw'
    } | complete)
    if $token_result.exit_code != 0 {
        error make {msg: "Failed to generate the argocd-reader access token"}
    }
    let reader_token = ($token_result.stdout | str trim)
    if ($reader_token | is-empty) {
        error make {msg: "Gitea returned an empty argocd-reader access token"}
    }
    # The trusted digiorg.local ingress over HTTPS (Issue #285 TLS
    # hardening) -- must exact-match apps/platform/app-config.yaml's
    # spec.source.repoURL (see the comment there for why).
    persist_argocd_repo_secret "app-config-repo-creds" "https://digiorg.local/gitea/DigiOrg/app-config.git" "argocd-reader" $reader_token
    print $"(ansi green)✓ ArgoCD app-config repository credential created \(read:repository only\)(ansi reset)"
}

# Create a dedicated, least-privilege Gitea identity for Backstage's own
# create-pull-request publish action (Issue #285 blocker #2: Backstage's
# core-portal app-config.yaml expects GITEA_TOKEN -- integrations.gitea[0].
# password: ${GITEA_TOKEN} -- but no dedicated identity/env wiring existed,
# so publishing an AppClaim manifest could never authenticate). Write access
# ONLY to the private DigiOrg/app-config repository -- no org membership, no
# other repository, and a separate identity from crossplane-provisioner
# (creates per-app source repos) and argocd-reader (read-only GitOps sync):
# each of the three credentials this issue introduces is scoped to exactly
# one actor's actual need.
def configure_backstage_gitea_publisher [gitea_pod: string, gitea_token: string] {
    let secret_exists = ((do -i { kubectl --kubeconfig $KUBECONFIG_PATH get secret backstage-gitea-credentials -n backstage } | complete).exit_code == 0)

    let users_result = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- su git -c 'gitea admin user list'
    } | complete)
    if $users_result.exit_code != 0 {
        error make {msg: "Failed to list existing Gitea users"}
    }
    if not ($users_result.stdout | lines | any {|line| $line | str contains "backstage-appclaim-publisher" }) {
        gitea_create_user_random_password $gitea_pod "backstage-appclaim-publisher" "backstage-appclaim-publisher@digiorg.local" false
    }
    gitea_unset_service_user_must_change_password $gitea_pod "backstage-appclaim-publisher"

    # Write collaborator on DigiOrg/app-config ONLY -- no org membership, no
    # other repository. Re-applied every run (idempotent PUT) so a resumed
    # run repairs drift even when the token Secret already exists (Issue #285
    # blocker #9).
    let collab_add = (do {
        $gitea_token | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n gitea $gitea_pod -c gitea -- sh -c 'IFS= read -r token; printf "header = \"Authorization: token %s\"\n" "$token" | curl --config - --cacert /etc/gitea/trusted-cas/digiorg-local-ca.crt -fsS -X PUT -H "Content-Type: application/json" --data "{\"permission\":\"write\"}" https://digiorg.local/gitea/api/v1/repos/DigiOrg/app-config/collaborators/backstage-appclaim-publisher'
    } | complete)
    if $collab_add.exit_code != 0 {
        error make {msg: "Failed to grant backstage-appclaim-publisher write access to DigiOrg/app-config"}
    }
    print $"(ansi green)✓ 'backstage-appclaim-publisher' has write access to DigiOrg/app-config(ansi reset)"

    # Only the token itself is resume-preserved (rotation is an explicit
    # operator action); user existence and collaborator access above are
    # always re-verified/repaired first, even when the Secret already exists.
    if $secret_exists {
        print $"(ansi yellow)✓ Backstage app-config publish credential already present — preserved \(membership re-verified\)(ansi reset)"
        return
    }

    # Scoped ONLY to write:repository -- Backstage only needs to push a
    # branch and open a pull request against DigiOrg/app-config.
    let token_name = $"backstage-((date now | format date '%Y%m%d%H%M%S'))"
    let token_result = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH exec -n gitea $gitea_pod -c gitea -- su git -c $'gitea admin user generate-access-token --username backstage-appclaim-publisher --token-name "($token_name)" --scopes write:repository --raw'
    } | complete)
    if $token_result.exit_code != 0 {
        error make {msg: "Failed to generate the backstage-appclaim-publisher access token"}
    }
    let publisher_token = ($token_result.stdout | str trim)
    if ($publisher_token | is-empty) {
        error make {msg: "Gitea returned an empty backstage-appclaim-publisher access token"}
    }
    # Dedicated single-purpose Secret, deliberately NOT a new key merged into
    # the shared multi-key backstage-secrets object: persist_opaque_secret
    # applies a Secret containing only this one key, and Kubernetes'
    # three-way apply would otherwise prune backstage-secrets' other keys
    # (POSTGRES_PASSWORD, AUTH_SESSION_SECRET, AUTH_OIDC_CLIENT_SECRET, ...).
    persist_opaque_secret "backstage" "backstage-gitea-credentials" "GITEA_TOKEN" $publisher_token
    print $"(ansi green)✓ Least-privilege 'backstage-gitea-credentials' created \(write:repository only\)(ansi reset)"
}

# Generate (once, resume-preserved) and persist the Gitea Actions runner
# registration token, then unconditionally verify the runner is actually
# online (Issue #285 blocker #4). Uses the exact Gitea Admin API this
# platform's pinned chart actually runs (apps/platform/gitea.yaml: chart
# 12.6.0, appVersion 1.26.1) -- confirmed against go-gitea/gitea's
# templates/swagger/v1_json.tmpl at tag v1.26.1:
# `POST /api/v1/admin/actions/runners/registration-token`. The older v1.23
# `GET /api/v1/admin/runners/registration-token` no longer exists on this
# chart's pinned appVersion. Registers at instance scope (the admin
# endpoint), so the runner is available to every org/repo, including
# DigiOrg/app-config and every per-app Gitea repository the pipeline
# Composition creates. Uses the platform admin bootstrap token deliberately:
# registering the one shared platform CI runner is one-time platform
# bootstrap, not a per-app credential.
def configure_gitea_actions_runner [gitea_pod: string, gitea_token: string] {
    let secret_exists = ((do -i { kubectl --kubeconfig $KUBECONFIG_PATH get secret gitea-actions-runner-token -n gitea } | complete).exit_code == 0)

    if not $secret_exists {
        let token_result = (do {
            $gitea_token | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n gitea $gitea_pod -c gitea -- sh -c 'IFS= read -r token; printf "header = \"Authorization: token %s\"\n" "$token" | curl --config - --cacert /etc/gitea/trusted-cas/digiorg-local-ca.crt -fsS -X POST https://digiorg.local/gitea/api/v1/admin/actions/runners/registration-token'
        } | complete)
        if $token_result.exit_code != 0 {
            error make {msg: "Failed to generate the Gitea Actions runner registration token"}
        }
        let parsed = (try { $token_result.stdout | from json } catch { null })
        let runner_token = if (($parsed | describe | str starts-with "record")) { ($parsed | get -o token | default "") } else { "" }
        if ($runner_token | is-empty) {
            error make {msg: "Gitea returned an empty Actions runner registration token"}
        }
        persist_opaque_secret "gitea" "gitea-actions-runner-token" "token" $runner_token
        print $"(ansi green)✓ Gitea Actions runner registration token generated and persisted(ansi reset)"
    } else {
        print $"(ansi yellow)✓ 'gitea-actions-runner-token' already present — preserved(ansi reset)"
    }

    # Registration state (act_runner's own `.runner` file, on the runner's
    # PersistentVolumeClaim) is what makes re-registration resume-stable, not
    # this Secret alone -- so coming online is re-verified every run,
    # regardless of whether the token above was just generated or preserved.
    wait_for_gitea_actions_runner_online $gitea_pod $gitea_token
}

# Poll Gitea's Admin API until the runner named "digiorg-local-runner" is
# listed and its status is explicitly "online". Gitea's Admin API only ever
# emits "status": "online" or "offline" for a runner (services/convert/
# convert.go's ToActionRunner @ go-gitea/gitea v1.26.1 hardcodes apiStatus to
# "offline" and overwrites it to "online" only when runner.IsOnline() --
# there is no third value). Accepting anything other than the literal
# "online" -- including this function's own "unknown" fallback for a
# missing/malformed `status` field -- fails closed instead of treating an
# unverified/unrecognized status as ready. Bounded and fails closed: a
# runner that never registers (bad token, image pull failure, rootless
# dockerd startup failure, ...) surfaces as a real, actionable error here
# rather than a silently-broken CI/CD capability discovered only later on
# the first AppClaim's Gitea Actions run.
def wait_for_gitea_actions_runner_online [gitea_pod: string, gitea_token: string] {
    print "Waiting for the Gitea Actions runner to register and come online..."
    mut last_diagnostic = "runner status was not observed"
    for attempt in 1..60 {
        let result = (do {
            $gitea_token | kubectl --kubeconfig $KUBECONFIG_PATH exec -i -n gitea $gitea_pod -c gitea -- sh -c 'IFS= read -r token; printf "header = \"Authorization: token %s\"\n" "$token" | curl --config - --cacert /etc/gitea/trusted-cas/digiorg-local-ca.crt -fsS https://digiorg.local/gitea/api/v1/admin/actions/runners'
        } | complete)
        if $result.exit_code == 0 {
            let parsed = (try { $result.stdout | from json } catch { null })
            let runners = if (($parsed | describe | str starts-with "record")) { ($parsed | get -o runners | default []) } else { [] }
            let matching = ($runners | where {|r| ($r | get -o name | default "") == "digiorg-local-runner" })
            if (($matching | length) > 0) {
                let status = ($matching | first | get -o status | default "unknown")
                $last_diagnostic = $"status=($status)"
                if $status == "online" {
                    print $"(ansi green)✓ Gitea Actions runner 'digiorg-local-runner' is registered \(status: ($status)\)(ansi reset)"
                    return
                }
            } else {
                $last_diagnostic = "runner 'digiorg-local-runner' not yet listed"
            }
        } else {
            $last_diagnostic = ($result.stderr | str trim)
        }
        sleep 5sec
    }
    error make {msg: $"Gitea Actions runner did not come online within the timeout \(last status: ($last_diagnostic)\)"}
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
    } else if (kubectl_error_is_exact_not_found $admin_pass.stderr "secrets" "sonarqube-admin-secret") {
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

# A newly created CA Secret can precede Argo CD creating the Actions runner on
# a clean cluster. Missing is therefore safe only for this early refresh path:
# the runner will mount the current CA when it is first created. API, RBAC and
# transport failures remain fatal, while an existing Deployment uses the same
# strict restart/rollout gate as every other OIDC client.
def restart_oidc_deployment_if_present [namespace: string, deployment: string, timeout: string] {
    let lookup = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH get deployment $deployment -n $namespace --ignore-not-found -o name
    } | complete)
    if $lookup.exit_code != 0 {
        error make {msg: $"Failed to determine whether deployment ($namespace)/($deployment) exists"}
    }
    if ($lookup.stdout | str trim | is-empty) {
        print $"(ansi yellow)○ ($namespace)/($deployment) not created yet; it will mount the current CA on first start(ansi reset)"
        return
    }
    restart_oidc_deployment $namespace $deployment $timeout
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

# Wait until ingress-nginx has published the current cert-manager public CA.
# Compare only the base64-encoded Secret fields: certificate bytes are never
# decoded or printed. Missing/not-yet-populated ingress data may converge;
# command failures remain fatal.
def wait_for_ingress_local_ca_convergence [] {
    let expected_ca_result = (do {
        kubectl --kubeconfig $KUBECONFIG_PATH get secret digiorg-local-ca-secret -n cert-manager -o jsonpath='{.data.ca\.crt}'
    } | complete)
    if $expected_ca_result.exit_code != 0 or ($expected_ca_result.stdout | str trim | is-empty) {
        error make {msg: "Failed to read the current cert-manager public CA"}
    }
    let expected_ca = ($expected_ca_result.stdout | str trim)

    for attempt in 1..30 {
        let ingress_ca_result = (do {
            kubectl --kubeconfig $KUBECONFIG_PATH get secret digiorg-local-tls -n ingress-nginx --ignore-not-found -o jsonpath='{.data.ca\.crt}'
        } | complete)
        if $ingress_ca_result.exit_code != 0 {
            error make {msg: "Failed to read the ingress TLS public CA"}
        }
        let ingress_ca = ($ingress_ca_result.stdout | str trim)
        if $ingress_ca == $expected_ca {
            return
        }
        if $attempt < 30 {
            sleep 2sec
        }
    }

    error make {msg: "Ingress TLS public CA did not converge to the current cert-manager CA within 60 seconds"}
}

# Issue #285 (TLS hardening): copy only the public digiorg.local CA
# certificate -- never cert-manager's private key -- from
# cert-manager/digiorg-local-ca-secret into an Opaque Secret named
# `digiorg-local-ca` in the given namespace, so credential-bearing clients
# there (the crossplane provider-http ProviderConfig, Backstage, the Gitea
# Actions runner) can verify the digiorg.local ingress's certificate without
# insecureSkipVerify/NODE_TLS_REJECT_UNAUTHORIZED/-k. Idempotent (safe on
# both a first bootstrap run and a resumed one): re-applies every run, and
# returns whether the content actually changed so callers can decide whether
# a dependent client needs restarting.
def copy_digiorg_local_ca_to_namespace [target_namespace: string] {
    $env.KUBECONFIG = $KUBECONFIG_PATH
    let secret_name = "digiorg-local-ca"

    let ca_cert_b64_result = (do {
        kubectl get secret digiorg-local-ca-secret -n cert-manager -o jsonpath='{.data.ca\.crt}'
    } | complete)
    if $ca_cert_b64_result.exit_code != 0 or ($ca_cert_b64_result.stdout | str trim | is-empty) {
        error make {msg: $"Could not extract the CA certificate to copy into ($target_namespace)"}
    }
    let ca_cert = ($ca_cert_b64_result.stdout | str trim)

    let previous_b64_result = (do {
        kubectl get secret $secret_name -n $target_namespace -o jsonpath='{.data.ca\.crt}'
    } | complete)
    let unchanged = ($previous_b64_result.exit_code == 0) and (($previous_b64_result.stdout | str trim) == $ca_cert)

    # Windows/KinD-portable: a real temp file (not /dev/stdin) handed to
    # `kubectl create secret --from-file`, matching the pattern
    # patch_argocd_oidc_ca already uses for this same CA cert content.
    let ca_tmp_file = $"digiorg-local-ca-($target_namespace).crt"
    ($ca_cert | decode base64 | decode) | save -f $ca_tmp_file
    let apply_result = (do {
        (kubectl create secret generic $secret_name --namespace $target_namespace $"--from-file=ca.crt=($ca_tmp_file)" --dry-run=client -o yaml) | kubectl apply -f -
    } | complete)
    rm $ca_tmp_file
    if $apply_result.exit_code != 0 {
        error make {msg: $"Failed to copy the digiorg.local CA certificate into ($target_namespace)"}
    }

    if $unchanged {
        print $"(ansi yellow)✓ CA cert already current in ($target_namespace)/($secret_name)(ansi reset)"
    } else {
        print $"(ansi green)✓ CA cert copied into ($target_namespace)/($secret_name)(ansi reset)"
    }
    not $unchanged
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

    # Write Helm values override with oidc.config containing rootCA, plus
    # (Issue #285 TLS hardening) configs.tls.certificates -- the argo-cd
    # chart's argocd-tls-certs-cm mechanism -- so ArgoCD's repo-server trusts
    # this same CA for the app-config GitOps sink repo
    # (apps/platform/app-config.yaml), now cloned via the trusted
    # digiorg.local ingress over HTTPS rather than the raw in-cluster
    # gitea-http Service address.
    let helm_override = {
        configs: {
            cm: {"oidc.config": $oidc_config}
            tls: {certificates: {"digiorg.local": $ca_cert}}
        }
    }
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

# Parse the real `argocd version --client --short` output and compare
# MAJOR.MINOR compatibility with the deployed server. Issue #283: an exact
# patch match is brittle (it fails a routine, compatible patch upgrade of the
# CLI) — the client/server compatibility contract that matters here is the
# minor version line, not the patch. Build metadata is allowed; a different
# major or minor (e.g. v3.5.0 or v4.4.5) never satisfies "3.4".
def argocd_client_version_compatible [output: string, expected_minor: string] {
    let parsed = ($output | str trim | parse --regex '^argocd:\s+v(?P<major>[0-9]+)\.(?P<minor>[0-9]+)\.[0-9]+(?:\+[0-9A-Za-z.-]+)?$')
    if ($parsed | length) != 1 {
        return false
    }
    let row = ($parsed | first)
    ($"($row.major).($row.minor)" == $expected_minor)
}

# Check if prerequisite tools (kind, kubectl, helm) are installed before
# proceeding. `argocd` is intentionally NOT in this mandatory list — see below.
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

    # Issue #283: `argocd` is OPTIONAL. It is used by
    # argocd_app_has_no_material_diff, a fail-closed secondary check for stale
    # Healthy/OutOfSync status in both the gated fresh-operation poll and the
    # global Application wait. Its absence or incompatibility does not block
    # startup, but either fallback remains closed; report it informationally.
    let expected_argocd_minor = "3.4"
    if (which argocd | is-empty) {
        print $"(ansi yellow)  argocd CLI not found \(optional\) — the stale-status zero-diff fallback will be unavailable(ansi reset)"
    } else {
        let argocd_version = (do { argocd version --client --short } | complete)
        if $argocd_version.exit_code == 0 and (argocd_client_version_compatible $argocd_version.stdout $expected_argocd_minor) {
            print $"(ansi green)✓ argocd CLI \(compatible with v($expected_argocd_minor).x\)(ansi reset)"
        } else {
            print $"(ansi yellow)  argocd CLI found but not compatible with v($expected_argocd_minor).x \(optional\) — the stale-status zero-diff fallback will be unavailable(ansi reset)"
        }
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
