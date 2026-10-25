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
  description = "EKS Kubernetes version."
  type        = string
  default     = "1.29"
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
