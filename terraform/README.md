# Terraform

This directory contains Terraform modules for initial cluster provisioning (bootstrap only).

> **Note:** Terraform is used only for **initial management cluster setup**. Day-2 operations and workload infrastructure are managed by **Crossplane**.

## Structure

```
terraform/
└── modules/
    ├── aws/       # AWS EKS module
    ├── azure/     # Azure AKS module
    ├── gcp/       # GCP GKE module
    └── ionos/     # IONOS Kubernetes module
```

## Usage

Each module can be used independently:

```hcl
module "eks" {
  source = "./modules/aws"

  cluster_name    = "management-cluster"
  region          = "eu-central-1"
  node_count      = 3
  node_size       = "small"  # small, medium, large

  tags = {
    Environment = "production"
    ManagedBy   = "terraform"
  }
}
```

## Module Interface

All modules follow a consistent interface:

### Required Variables

| Variable | Description |
|----------|-------------|
| `cluster_name` | Name of the Kubernetes cluster |
| `region` | Cloud provider region |

### Optional Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `node_count` | 3 | Number of nodes |
| `node_size` | "small" | Node size (small/medium/large) |
| `kubernetes_version` | Latest | Kubernetes version |

### Outputs

| Output | Description |
|--------|-------------|
| `cluster_endpoint` | Kubernetes API endpoint |
| `kubeconfig` | Kubeconfig for cluster access |
| `cluster_ca_certificate` | Cluster CA certificate |

## State Management

Store Terraform state remotely:

```hcl
terraform {
  backend "s3" {
    bucket = "digiorg-terraform-state"
    key    = "management-cluster/terraform.tfstate"
    region = "eu-central-1"
  }
}
```

## Terraform vs Crossplane

| Aspect | Terraform | Crossplane |
|--------|-----------|------------|
| Use Case | Bootstrap management cluster | Day-2 operations |
| State | Remote backend (S3/GCS) | Kubernetes etcd |
| Reconciliation | Manual | Continuous |
