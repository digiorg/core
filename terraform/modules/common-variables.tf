# =============================================================================
# Common Variables for All Cloud Provider Modules
# =============================================================================
# Copy this file to each provider module and customize as needed.
# This ensures a consistent interface across all providers.
# =============================================================================

# -----------------------------------------------------------------------------
# Required Variables
# -----------------------------------------------------------------------------

variable "cluster_name" {
  description = "Name of the Kubernetes cluster"
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,38}[a-z0-9]$", var.cluster_name))
    error_message = "cluster_name must be lowercase alphanumeric with hyphens, 3-40 chars"
  }
}

variable "region" {
  description = "Cloud provider region for resource deployment"
  type        = string
}

variable "environment" {
  description = "Environment name (dev, staging, production)"
  type        = string

  validation {
    condition     = contains(["dev", "staging", "production"], var.environment)
    error_message = "environment must be dev, staging, or production"
  }
}

# -----------------------------------------------------------------------------
# Optional Variables with Defaults
# -----------------------------------------------------------------------------

variable "kubernetes_version" {
  description = "Kubernetes version to deploy"
  type        = string
  default     = "1.29"
}

variable "node_count" {
  description = "Number of worker nodes"
  type        = number
  default     = 3

  validation {
    condition     = var.node_count >= 1 && var.node_count <= 100
    error_message = "node_count must be between 1 and 100"
  }
}

variable "node_size" {
  description = "Node size category (maps to provider-specific instance types)"
  type        = string
  default     = "small"

  validation {
    condition     = contains(["small", "medium", "large"], var.node_size)
    error_message = "node_size must be small, medium, or large"
  }
}

variable "enable_autoscaling" {
  description = "Enable cluster autoscaling"
  type        = bool
  default     = true
}

variable "min_nodes" {
  description = "Minimum number of nodes when autoscaling is enabled"
  type        = number
  default     = 1
}

variable "max_nodes" {
  description = "Maximum number of nodes when autoscaling is enabled"
  type        = number
  default     = 10
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}

# -----------------------------------------------------------------------------
# Networking Variables
# -----------------------------------------------------------------------------

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "enable_private_cluster" {
  description = "Create private cluster (no public API endpoint)"
  type        = bool
  default     = false
}

# -----------------------------------------------------------------------------
# Computed Common Tags
# -----------------------------------------------------------------------------

locals {
  common_tags = merge(
    {
      "Environment"             = var.environment
      "ManagedBy"               = "terraform"
      "Platform"                = "digiorg-core"
      "Cluster"                 = var.cluster_name
    },
    var.tags
  )
}
