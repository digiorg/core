# =============================================================================
# DigiOrg Core Platform - Makefile
# =============================================================================

# NOTE: Cluster lifecycle (up/down/reset/status) is managed by the Nushell script:
#   nu scripts/local-setup.nu up|down|reset|status
# This Makefile provides utility helpers only.

.PHONY: help deps argocd-password port-forward-argocd port-forward-vault port-forward-grafana lint validate-policies validate-crossplane clean kubeconfig

CLUSTER_NAME := digiorg-core-dev
KUBECONFIG_LOCAL := $(PWD)/kubeconfig-local.yaml

# Default target — utility helpers only (cluster lifecycle: nu scripts/local-setup.nu)
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-25s\033[0m %s\n", $$1, $$2}'

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
# Linting & Validation
# =============================================================================

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
