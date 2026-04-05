# =============================================================================
# DigiOrg Core Platform - Makefile
# =============================================================================

.PHONY: help up down reset status test lint clean deps install

# Default target
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# =============================================================================
# Local Development (KinD)
# =============================================================================

CLUSTER_NAME := digiorg-core-dev
KIND_CONFIG := platform/bootstrap/kind-config.yaml
KUBECONFIG_LOCAL := $(PWD)/kubeconfig-local.yaml

up: ## Start local KinD cluster with all components
	@echo "Starting local development environment..."
	@if command -v nu >/dev/null 2>&1; then \
		nu scripts/local-setup.nu up; \
	else \
		echo "Nushell not found, using fallback..."; \
		$(MAKE) _up-fallback; \
	fi

_up-fallback:
	@kind create cluster --name $(CLUSTER_NAME) --config $(KIND_CONFIG) --kubeconfig $(KUBECONFIG_LOCAL) 2>/dev/null || true
	@echo "Cluster created. Installing components..."
	@export KUBECONFIG=$(KUBECONFIG_LOCAL) && $(MAKE) install
	@echo ""
	@echo "Local cluster ready!"
	@echo "Run: export KUBECONFIG=$(KUBECONFIG_LOCAL)"

down: ## Destroy local KinD cluster
	@echo "Destroying local cluster..."
	@kind delete cluster --name $(CLUSTER_NAME) 2>/dev/null || true
	@rm -f $(KUBECONFIG_LOCAL)
	@echo "Done."

reset: down up ## Reset local cluster (destroy + create)

status: ## Show cluster status
	@if command -v nu >/dev/null 2>&1; then \
		nu scripts/local-setup.nu status; \
	else \
		$(MAKE) _status-fallback; \
	fi

_status-fallback:
	@echo "Cluster Status"
	@echo "=============="
	@kind get clusters 2>/dev/null | grep -q $(CLUSTER_NAME) && echo "● Cluster is running" || echo "✗ Cluster is not running"
	@echo ""
	@if [ -f $(KUBECONFIG_LOCAL) ]; then \
		export KUBECONFIG=$(KUBECONFIG_LOCAL) && kubectl get nodes 2>/dev/null || true; \
	fi

# =============================================================================
# Component Installation
# =============================================================================

install: install-ingress install-argocd install-crossplane install-vault install-kyverno ## Install all platform components
	@echo "All components installed!"

install-ingress: ## Install NGINX Ingress Controller
	@echo "Installing Ingress Controller..."
	@export KUBECONFIG=$(KUBECONFIG_LOCAL) && \
		kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml
	@sleep 10
	@export KUBECONFIG=$(KUBECONFIG_LOCAL) && \
		kubectl wait --namespace ingress-nginx --for=condition=ready pod --selector=app.kubernetes.io/component=controller --timeout=180s || true

install-argocd: ## Install ArgoCD
	@echo "Installing ArgoCD..."
	@export KUBECONFIG=$(KUBECONFIG_LOCAL) && \
		helm repo add argo https://argoproj.github.io/argo-helm 2>/dev/null || true && \
		helm repo update && \
		helm upgrade --install argocd argo/argo-cd \
			--namespace argocd --create-namespace \
			--set 'server.service.type=NodePort' \
			--set 'server.service.nodePortHttp=30080' \
			--set 'server.service.nodePortHttps=30443' \
			--set 'configs.params.server\.insecure=true' \
			--wait --timeout 5m

install-crossplane: ## Install Crossplane
	@echo "Installing Crossplane..."
	@export KUBECONFIG=$(KUBECONFIG_LOCAL) && \
		helm repo add crossplane-stable https://charts.crossplane.io/stable 2>/dev/null || true && \
		helm repo update && \
		helm upgrade --install crossplane crossplane-stable/crossplane \
			--namespace crossplane-system --create-namespace \
			--wait --timeout 5m

install-vault: ## Install Vault (dev mode)
	@echo "Installing Vault..."
	@export KUBECONFIG=$(KUBECONFIG_LOCAL) && \
		helm repo add hashicorp https://helm.releases.hashicorp.com 2>/dev/null || true && \
		helm repo update && \
		helm upgrade --install vault hashicorp/vault \
			--namespace vault --create-namespace \
			--set 'server.dev.enabled=true' \
			--set 'server.dev.devRootToken=root' \
			--set 'ui.enabled=true' \
			--wait --timeout 5m

install-kyverno: ## Install Kyverno
	@echo "Installing Kyverno..."
	@export KUBECONFIG=$(KUBECONFIG_LOCAL) && \
		helm repo add kyverno https://kyverno.github.io/kyverno/ 2>/dev/null || true && \
		helm repo update && \
		helm upgrade --install kyverno kyverno/kyverno \
			--namespace kyverno --create-namespace \
			--set 'replicaCount=1' \
			--wait --timeout 5m

install-monitoring: ## Install Prometheus Stack (optional, slow)
	@echo "Installing Monitoring Stack..."
	@export KUBECONFIG=$(KUBECONFIG_LOCAL) && \
		helm repo add prometheus-community https://prometheus-community.github.io/helm-charts 2>/dev/null || true && \
		helm repo update && \
		helm upgrade --install prometheus prometheus-community/kube-prometheus-stack \
			--namespace monitoring --create-namespace \
			--set 'grafana.service.type=NodePort' \
			--set 'grafana.service.nodePort=30090' \
			--set 'prometheus.prometheusSpec.retention=1d' \
			--set 'alertmanager.enabled=false' \
			--wait --timeout 10m

# =============================================================================
# Access Services
# =============================================================================

argocd-password: ## Get ArgoCD admin password
	@export KUBECONFIG=$(KUBECONFIG_LOCAL) && \
		kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d && echo

port-forward-argocd: ## Port forward ArgoCD (https://localhost:8080)
	@echo "ArgoCD available at https://localhost:8080"
	@export KUBECONFIG=$(KUBECONFIG_LOCAL) && \
		kubectl port-forward svc/argocd-server -n argocd 8080:443

port-forward-vault: ## Port forward Vault (http://localhost:8200)
	@echo "Vault available at http://localhost:8200 (token: root)"
	@export KUBECONFIG=$(KUBECONFIG_LOCAL) && \
		kubectl port-forward svc/vault -n vault 8200:8200

port-forward-grafana: ## Port forward Grafana (http://localhost:3000)
	@echo "Grafana available at http://localhost:3000"
	@export KUBECONFIG=$(KUBECONFIG_LOCAL) && \
		kubectl port-forward svc/prometheus-grafana -n monitoring 3000:80

# =============================================================================
# Testing & Linting
# =============================================================================

test: ## Run all tests
	@echo "Running tests..."
	@echo "TODO: Implement tests"

lint: ## Lint all configurations
	@echo "Linting YAML files..."
	@yamllint . 2>/dev/null || echo "yamllint not installed, skipping..."
	@echo "Validating Kubernetes manifests..."
	@if [ -f $(KUBECONFIG_LOCAL) ]; then \
		export KUBECONFIG=$(KUBECONFIG_LOCAL) && \
		find apps -name "*.yaml" -exec kubectl apply --dry-run=client -f {} \; 2>/dev/null || true; \
	fi
	@echo "Done."

validate-policies: ## Validate Kyverno policies
	@echo "Validating Kyverno policies..."
	@kyverno validate policies/kyverno/ 2>/dev/null || echo "kyverno CLI not installed"

validate-crossplane: ## Validate Crossplane compositions
	@echo "Validating Crossplane configurations..."
	@crossplane beta validate crossplane/xrds/ crossplane/compositions/ 2>/dev/null || echo "crossplane CLI not installed"

# =============================================================================
# Utilities
# =============================================================================

clean: ## Clean temporary files
	@echo "Cleaning temporary files..."
	@rm -rf .terraform/ *.tfstate* *.tfplan
	@rm -rf tmp/ *.log
	@rm -f kubeconfig-*.yaml
	@echo "Done."

deps: ## Check required dependencies
	@echo "Checking dependencies..."
	@echo ""
	@printf "%-15s %s\n" "Tool" "Status"
	@printf "%-15s %s\n" "----" "------"
	@command -v kubectl >/dev/null 2>&1 && printf "%-15s \033[32m✓ installed\033[0m\n" "kubectl" || printf "%-15s \033[31m✗ missing\033[0m\n" "kubectl"
	@command -v helm >/dev/null 2>&1 && printf "%-15s \033[32m✓ installed\033[0m\n" "helm" || printf "%-15s \033[31m✗ missing\033[0m\n" "helm"
	@command -v kind >/dev/null 2>&1 && printf "%-15s \033[32m✓ installed\033[0m\n" "kind" || printf "%-15s \033[31m✗ missing\033[0m\n" "kind"
	@command -v terraform >/dev/null 2>&1 && printf "%-15s \033[32m✓ installed\033[0m\n" "terraform" || printf "%-15s \033[33m○ optional\033[0m\n" "terraform"
	@command -v nu >/dev/null 2>&1 && printf "%-15s \033[32m✓ installed\033[0m\n" "nushell" || printf "%-15s \033[33m○ optional\033[0m\n" "nushell"
	@command -v kyverno >/dev/null 2>&1 && printf "%-15s \033[32m✓ installed\033[0m\n" "kyverno" || printf "%-15s \033[33m○ optional\033[0m\n" "kyverno"
	@command -v crossplane >/dev/null 2>&1 && printf "%-15s \033[32m✓ installed\033[0m\n" "crossplane" || printf "%-15s \033[33m○ optional\033[0m\n" "crossplane"

kubeconfig: ## Print kubeconfig export command
	@echo "export KUBECONFIG=$(KUBECONFIG_LOCAL)"
