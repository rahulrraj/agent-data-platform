variable "name" {
  description = "Name prefix for all resources in this module."
  type        = string
}

variable "engine_version" {
  description = "Postgres major version for RDS."
  type        = string
  default     = "16"
}

variable "instance_class" {
  description = "RDS instance class. db.t4g.micro is the smallest (free-tier eligible class family); raise for production."
  type        = string
  default     = "db.t4g.micro"
}

variable "allocated_storage" {
  description = "Allocated storage in GB."
  type        = number
  default     = 20
}

variable "db_name" {
  description = "Database name."
  type        = string
  default     = "agentdb"
}

variable "username" {
  description = "Master username."
  type        = string
  default     = "agent"
}

variable "password" {
  description = "Master password. Sensitive — pass via TF_VAR_db_password or a secret, never a default or a committed value."
  type        = string
  sensitive   = true
}

variable "subnet_ids" {
  description = "Subnet IDs for the DB subnet group (private subnets)."
  type        = list(string)
}

variable "vpc_id" {
  description = "VPC ID for the DB security group."
  type        = string
}

variable "allowed_security_group_ids" {
  description = "Security groups allowed to reach Postgres on 5432 (e.g. the Lambda SG). Leave empty when using CIDR access instead."
  type        = list(string)
  default     = []
}

variable "allowed_cidr_blocks" {
  description = "CIDR blocks allowed to reach Postgres on 5432 (e.g. the VPC CIDR for EKS, whose node SG is created by EKS). Prefer security groups where the caller SG is known."
  type        = list(string)
  default     = []
}

variable "multi_az" {
  description = "Multi-AZ deployment (production). Off by default for cost."
  type        = bool
  default     = false
}

variable "deletion_protection" {
  description = "Protect the instance from deletion. Off by default so dev stacks tear down cleanly."
  type        = bool
  default     = false
}

variable "skip_final_snapshot" {
  description = "Skip a final snapshot on destroy. True by default for dev; set false for production."
  type        = bool
  default     = true
}

variable "backup_retention_period" {
  description = "Automated backup retention in days (0 disables automated backups). 7 by default: no PITR window below that."
  type        = number
  default     = 7

  validation {
    condition     = var.backup_retention_period >= 0 && var.backup_retention_period <= 35
    error_message = "backup_retention_period must be between 0 and 35 days (RDS limit)."
  }
}

variable "tags" {
  description = "Tags applied to all resources."
  type        = map(string)
  default     = {}
}
