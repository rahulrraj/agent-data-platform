# AWS EKS stack: the cluster, node groups, and IAM roles via Terraform.
# The workload itself stays defined by the existing manifests under
# deploy/k8s/ — this stack does NOT duplicate the Deployment/Service/
# Ingress spec. The deploy pipeline applies those manifests to this
# cluster with the ECR image URLs substituted in.
#
# Differences from the Lambda stack, stated plainly:
# - The UI runs in-cluster (nginx, per deploy/k8s/ui-deployment.yaml),
#   so this stack does NOT create the S3 + CloudFront frontend module.
# - The in-cluster Postgres StatefulSet in deploy/k8s is NOT applied:
#   this stack provisions RDS via the shared postgres module instead,
#   and the pipeline points the k8s Secret at it.
# - RDS ingress here is VPC-CIDR based: EKS creates the node security
#   group itself, so no caller SG is known in advance. Tighten it to the
#   node SG via a launch template as a follow-up if needed.
# - Bedrock access for pods is NOT wired here: the correct pattern is
#   IRSA (OIDC provider + service-account role), which is a follow-up.
#   The Lambda stack covers Bedrock via its execution role today.

locals {
  # The stack suffix keeps this stack's resource names distinct from the
  # aws-lambda stack's: both stacks are co-deployable in one account (RDS
  # identifier and ECR repo names would otherwise collide). The deploy
  # workflow's ECR repo names must match this prefix exactly.
  name_prefix = "${var.project}-${var.environment}-eks"
  common_tags = merge(var.tags, {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
    Stack       = "aws-eks"
  })
}

module "networking" {
  source = "../../modules/networking"

  name                 = local.name_prefix
  cidr_block           = var.vpc_cidr_block
  az_count             = var.az_count
  enable_nat           = var.enable_nat
  nat_per_az           = var.nat_per_az
  enable_vpc_endpoints = var.enable_vpc_endpoints
  tags                 = local.common_tags
}

module "postgres" {
  source = "../../modules/postgres"

  name                    = local.name_prefix
  engine_version          = var.db_engine_version
  instance_class          = var.db_instance_class
  allocated_storage       = var.db_allocated_storage
  db_name                 = var.db_name
  username                = var.db_username
  password                = var.db_password
  multi_az                = var.db_multi_az
  deletion_protection     = var.deletion_protection
  skip_final_snapshot     = var.skip_final_snapshot
  backup_retention_period = var.backup_retention_period
  subnet_ids              = module.networking.private_subnet_ids
  vpc_id                  = module.networking.vpc_id
  allowed_cidr_blocks     = [module.networking.vpc_cidr_block]
  tags                    = local.common_tags
}

module "ecr_api" {
  source = "../../modules/ecr"

  name                 = "${local.name_prefix}-api"
  force_delete         = var.ecr_force_delete
  image_tag_mutability = var.ecr_image_tag_mutability
  keep_image_count     = var.ecr_keep_image_count
  tags                 = local.common_tags
}

module "ecr_ui" {
  source = "../../modules/ecr"

  name                 = "${local.name_prefix}-ui"
  force_delete         = var.ecr_force_delete
  image_tag_mutability = var.ecr_image_tag_mutability
  keep_image_count     = var.ecr_keep_image_count
  tags                 = local.common_tags
}

module "iam_cluster_role" {
  source = "../../modules/iam"

  name = "${local.name_prefix}-eks-cluster-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "eks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  managed_policy_arns = ["arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"]
  tags                = local.common_tags
}

module "iam_node_role" {
  source = "../../modules/iam"

  name = "${local.name_prefix}-eks-node-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  managed_policy_arns = [
    "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
    "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
  ]
  tags = local.common_tags
}

resource "aws_eks_cluster" "this" {
  name     = "${local.name_prefix}-cluster"
  version  = var.cluster_version
  role_arn = module.iam_cluster_role.role_arn

  vpc_config {
    subnet_ids              = concat(module.networking.private_subnet_ids, module.networking.public_subnet_ids)
    endpoint_public_access  = true
    endpoint_private_access = true
  }

  tags = local.common_tags
}

resource "aws_eks_node_group" "this" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "${local.name_prefix}-nodes"
  node_role_arn   = module.iam_node_role.role_arn
  subnet_ids      = module.networking.private_subnet_ids

  instance_types = var.node_instance_types
  disk_size      = var.node_disk_size

  scaling_config {
    desired_size = var.node_desired_size
    min_size     = var.node_min_size
    max_size     = var.node_max_size
  }

  # Cross-field validation (m-22): variable validation blocks cannot
  # compare two variables, so the min <= desired <= max invariant is
  # enforced here. Without it, an inverted scaling config applies
  # cleanly and fails only when the node group tries to scale.
  lifecycle {
    precondition {
      condition     = var.node_min_size <= var.node_desired_size && var.node_desired_size <= var.node_max_size
      error_message = "Node scaling must satisfy min_size <= desired_size <= max_size (got min=${var.node_min_size}, desired=${var.node_desired_size}, max=${var.node_max_size})."
    }
  }

  tags = local.common_tags
}

# Core addons. Versions are resolved per cluster version via the
# aws_eks_addon_version data sources (most recent compatible version),
# instead of being left to whatever the provider resolves at apply
# time — two applies of the same config must not produce different
# addon versions. Without these addons, pods have no networking or DNS.
data "aws_eks_addon_version" "vpc_cni" {
  addon_name         = "vpc-cni"
  kubernetes_version = var.cluster_version
  most_recent        = true
}

data "aws_eks_addon_version" "kube_proxy" {
  addon_name         = "kube-proxy"
  kubernetes_version = var.cluster_version
  most_recent        = true
}

data "aws_eks_addon_version" "coredns" {
  addon_name         = "coredns"
  kubernetes_version = var.cluster_version
  most_recent        = true
}

resource "aws_eks_addon" "vpc_cni" {
  cluster_name  = aws_eks_cluster.this.name
  addon_name    = "vpc-cni"
  addon_version = data.aws_eks_addon_version.vpc_cni.version

  tags = local.common_tags
}

resource "aws_eks_addon" "kube_proxy" {
  cluster_name  = aws_eks_cluster.this.name
  addon_name    = "kube-proxy"
  addon_version = data.aws_eks_addon_version.kube_proxy.version

  tags = local.common_tags
}

resource "aws_eks_addon" "coredns" {
  cluster_name  = aws_eks_cluster.this.name
  addon_name    = "coredns"
  addon_version = data.aws_eks_addon_version.coredns.version

  # On cluster upgrades the managed addon must win over any drifted
  # in-cluster CoreDNS config, or upgrades stall on conflicts.
  resolve_conflicts_on_create = "OVERWRITE"
  resolve_conflicts_on_update = "OVERWRITE"

  tags = local.common_tags

  depends_on = [aws_eks_node_group.this]
}
