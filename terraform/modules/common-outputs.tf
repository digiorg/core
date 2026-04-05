# =============================================================================
# Common Outputs for All Cloud Provider Modules
# =============================================================================
# Copy this file to each provider module and implement the actual outputs.
# This ensures a consistent interface across all providers.
# =============================================================================

# -----------------------------------------------------------------------------
# Cluster Information
# -----------------------------------------------------------------------------

# output "cluster_endpoint" {
#   description = "Kubernetes API server endpoint"
#   value       = <provider-specific-value>
# }

# output "cluster_ca_certificate" {
#   description = "Cluster CA certificate (base64 encoded)"
#   value       = <provider-specific-value>
#   sensitive   = true
# }

# output "cluster_name" {
#   description = "Name of the created cluster"
#   value       = var.cluster_name
# }

# -----------------------------------------------------------------------------
# Authentication
# -----------------------------------------------------------------------------

# output "kubeconfig" {
#   description = "Kubeconfig file content for cluster access"
#   value       = <provider-specific-value>
#   sensitive   = true
# }

# output "kubeconfig_path" {
#   description = "Path to the kubeconfig file (if written to disk)"
#   value       = local_file.kubeconfig.filename
# }

# -----------------------------------------------------------------------------
# Networking
# -----------------------------------------------------------------------------

# output "vpc_id" {
#   description = "ID of the VPC created for the cluster"
#   value       = <provider-specific-value>
# }

# output "subnet_ids" {
#   description = "List of subnet IDs"
#   value       = <provider-specific-value>
# }

# -----------------------------------------------------------------------------
# IAM / Service Accounts
# -----------------------------------------------------------------------------

# output "cluster_service_account" {
#   description = "Service account used by the cluster"
#   value       = <provider-specific-value>
# }

# output "crossplane_role_arn" {
#   description = "IAM role ARN for Crossplane (AWS) or equivalent"
#   value       = <provider-specific-value>
# }

# -----------------------------------------------------------------------------
# Example Implementation (AWS)
# -----------------------------------------------------------------------------

# output "cluster_endpoint" {
#   description = "Kubernetes API server endpoint"
#   value       = aws_eks_cluster.main.endpoint
# }

# output "cluster_ca_certificate" {
#   description = "Cluster CA certificate (base64 encoded)"
#   value       = aws_eks_cluster.main.certificate_authority[0].data
#   sensitive   = true
# }

# output "kubeconfig" {
#   description = "Kubeconfig file content"
#   value = templatefile("${path.module}/templates/kubeconfig.tpl", {
#     cluster_name     = var.cluster_name
#     cluster_endpoint = aws_eks_cluster.main.endpoint
#     cluster_ca       = aws_eks_cluster.main.certificate_authority[0].data
#     region          = var.region
#   })
#   sensitive = true
# }
