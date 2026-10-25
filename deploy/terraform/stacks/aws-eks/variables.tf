variable "project" {
  description = "Project name, used as a prefix for all resources."
  type        = string
  default     = "agent-data-platform"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)."
  type        = string
  default     = "dev"
}

variable "aws_region" {
  description = "AWS region for all resources."
  type        = string
  default     = "us-east-1"
}

variable "cluster_version" {
  description = "EKS Kubernetes version. 1.36 (standard support until Aug 2027): the previous default 1.29 left standard support in Mar 2025 and extended support in Mar 2026, so it is out of support entirely. Keep the deploy-eks workflow's kubectl version on the same minor (kubectl skew policy). Addon versions resolve per cluster version via data sources in main.tf."
  type        = string
  default     = "1.36"
}

# --- Shared module tuning knobs (m-20): same pass-throughs as the
# Lambda stack, so both stacks stay consistent. Defaults match the
# previous hardcoded behavior exactly.
variable "vpc_cidr_block" {
  description = "VPC CIDR block (networking module). Prefix must be /24 or shorter."
  type        = string
  default     = "10.0.0.0/16"
}

variable "az_count" {
  description = "Number of AZs to span (networking module). Must not exceed the AZs available in the region."
  type        = number
  default     = 2
}

variable "enable_nat" {
  description = "Create NAT gateway(s) for private-subnet egress (networking module)."
  type        = bool
  default     = true
}

variable "nat_per_az" {
  description = "One NAT gateway per AZ instead of a single shared one (networking module). Set true for production: the single-NAT default is a single-AZ egress SPOF."
  type        = bool
  default     = false
}

variable "enable_vpc_endpoints" {
  description = "Create S3/ECR/STS VPC endpoints to reduce NAT dependence (networking module). Requires enable_nat = true."
  type        = bool
  default     = false
}

variable "db_engine_version" {
  description = "Postgres major version for RDS (postgres module)."
  type        = string
  default     = "16"
}

variable "db_instance_class" {
  description = "RDS instance class (postgres module)."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage" {
  description = "RDS allocated storage in GB (postgres module)."
  type        = number
  default     = 20
}

variable "db_multi_az" {
  description = "Multi-AZ RDS deployment (postgres module). Off by default for cost; set true for production."
  type        = bool
  default     = false
}

variable "backup_retention_period" {
  description = "RDS automated backup retention in days (postgres module)."
  type        = number
  default     = 7
}

variable "ecr_force_delete" {
  description = "Allow deleting non-empty ECR repositories (ecr module, api + ui). True is a dev default; set false for production."
  type        = bool
  default     = true
}

variable "ecr_image_tag_mutability" {
  description = "ECR tag mutability (ecr module, api + ui). IMMUTABLE by default; the workflows push github.sha tags."
  type        = string
  default     = "IMMUTABLE"
}

variable "ecr_keep_image_count" {
  description = "Images kept by the ECR lifecycle policy — the rollback window in deploys (ecr module, api + ui)."
  type        = number
  default     = 30
}

variable "node_instance_types" {
  description = "EC2 instance types for the managed node group."
  type        = list(string)
  default     = ["t3.medium"]
}

variable "node_desired_size" {
  description = "Desired node count."
  type        = number
  default     = 2
}

variable "node_min_size" {
  description = "Minimum node count."
  type        = number
  default     = 1
}

variable "node_max_size" {
  description = "Maximum node count."
  type        = number
  default     = 3
}

variable "node_disk_size" {
  description = "Node root disk size in GB."
  type        = number
  default     = 20
}

variable "db_password" {
  description = "RDS master password. Sensitive — pass via TF_VAR_db_password, never a default or a committed value."
  type        = string
  sensitive   = true
}

variable "db_username" {
  description = "RDS master username."
  type        = string
  default     = "agent"
}

variable "db_name" {
  description = "RDS database name."
  type        = string
  default     = "agentdb"
}

variable "deletion_protection" {
  description = "RDS deletion protection. Off by default so dev stacks tear down cleanly; set true for production."
  type        = bool
  default     = false
}

variable "skip_final_snapshot" {
  description = "Skip a final RDS snapshot on destroy. True by default for dev; set false for production so destroy keeps a final snapshot."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Extra tags applied to all resources."
  type        = map(string)
  default     = {}
}
