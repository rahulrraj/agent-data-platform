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

# --- The existing cluster (input, not created) ---

variable "cluster_name" {
  description = "Name of the EXISTING EKS cluster to deploy onto. The cluster, its VPC/subnets, node groups, and addons already exist outside this stack — this stack only reads the cluster (data source) and deploys the application onto it. Pass via -var=cluster_name=... (the deploy workflow reads the EKS_CLUSTER_NAME repo variable)."
  type        = string

  validation {
    condition     = length(var.cluster_name) > 0
    error_message = "cluster_name must be set to the name of the existing EKS cluster — this stack creates no cluster of its own."
  }
}

# --- The existing database (input, not created) ---

variable "postgres_dsn" {
  description = "Postgres DSN for the conversation store (POSTGRES_DSN in the k8s Secret). The database already exists outside this stack — e.g. the RDS instance provisioned by the aws-lambda stack, or any Postgres reachable from the cluster. Sensitive — pass via TF_VAR_postgres_dsn, never a default or a committed value."
  type        = string
  sensitive   = true
}

# --- Model provider (drives the IRSA Bedrock policy) ---

variable "model_provider" {
  description = "AGENT_MODEL_PROVIDER for the API pods. gemini or bedrock only — Ollama is not reachable from inside the cluster."
  type        = string
  default     = "gemini"

  validation {
    condition     = contains(["gemini", "bedrock"], var.model_provider)
    error_message = "model_provider must be gemini or bedrock (Ollama is not reachable from inside the cluster)."
  }
}

variable "bedrock_model_id" {
  description = "BEDROCK_MODEL_ID for the API pods (Bedrock chat model)."
  type        = string
  default     = "us.anthropic.claude-sonnet-5-5"
}

variable "bedrock_embed_model" {
  description = "BEDROCK_EMBED_MODEL for the API pods (Bedrock embedding model)."
  type        = string
  default     = "amazon.titan-embed-text-v2:0"
}

variable "enable_bedrock_invoke" {
  description = "Grant the API pods' IRSA role bedrock:InvokeModel. Enable when model_provider is bedrock (credentials then come from the role, no key in env). Enforced by a precondition in main.tf: bedrock + this flag false fails the plan instead of failing at runtime."
  type        = bool
  default     = false
}

variable "bedrock_model_arns" {
  description = "Explicit ARNs for the Bedrock invoke policy. Null (default) computes scoped ARNs for bedrock_model_id / bedrock_embed_model in both foundation-model and inference-profile forms. Set this explicitly when using model IDs whose ARN form the computation cannot guess (e.g. a provisioned-throughput ARN). Never \"*\": the iam module's empty default is fail-closed."
  type        = list(string)
  default     = null
}

# --- ECR tuning knobs (m-20): same pass-throughs as the Lambda stack,
# so both stacks stay consistent. Defaults match the previous behavior.

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

variable "tags" {
  description = "Extra tags applied to all resources."
  type        = map(string)
  default     = {}
}
