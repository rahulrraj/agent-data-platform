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

variable "image_tag" {
  description = "Tag of the Lambda image in ECR. The deploy pipeline builds and pushes this tag first, then applies with it."
  type        = string
  default     = "latest"
}

# --- Shared module tuning knobs (m-20): pass-throughs so operators can
# tune the shared modules without editing stack main.tf. Defaults match
# the previous hardcoded behavior exactly, so existing deploys are
# unchanged unless a knob is set.
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
  description = "RDS instance class (postgres module). Raise from db.t4g.micro together with lambda_reserved_concurrency before load-bearing use."
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
  description = "Allow deleting a non-empty ECR repository (ecr module). True is a dev default; set false for production."
  type        = bool
  default     = true
}

variable "ecr_image_tag_mutability" {
  description = "ECR tag mutability (ecr module). IMMUTABLE by default; the workflows push github.sha tags."
  type        = string
  default     = "IMMUTABLE"
}

variable "ecr_keep_image_count" {
  description = "Images kept by the ECR lifecycle policy — the rollback window in deploys (ecr module)."
  type        = number
  default     = 30
}

variable "frontend_force_destroy" {
  description = "Allow deleting a non-empty UI bucket (frontend module). True is a dev default; set false for production."
  type        = bool
  default     = true
}

variable "frontend_enable_versioning" {
  description = "Enable S3 versioning on the UI bucket (frontend module): what makes a bad `s3 sync --delete` recoverable."
  type        = bool
  default     = true
}

variable "frontend_access_log_bucket" {
  description = "S3 bucket receiving CloudFront access logs (frontend module). Empty disables logging (dev default); set it before any public production use. No WAF is attached by this module — that is a separate production step."
  type        = string
  default     = ""
}

variable "agent_framework" {
  description = "AGENT_FRAMEWORK for the function (strands or langgraph). Wired as an env var so framework changes do not require a stack edit; defaults match the app's own default."
  type        = string
  default     = "strands"

  validation {
    condition     = contains(["strands", "langgraph"], var.agent_framework)
    error_message = "agent_framework must be strands or langgraph."
  }
}

variable "gemini_model" {
  description = "GEMINI_MODEL for the function. Wired as an env var so model changes do not require a stack edit; default matches the app's own default."
  type        = string
  default     = "gemini-flash-latest"
}

variable "gemini_embed_model" {
  description = "GEMINI_EMBED_MODEL for the function. Wired as an env var so model changes do not require a stack edit; default matches the app's own default."
  type        = string
  default     = "gemini-embedding-001"
}

variable "lambda_reserved_concurrency" {
  description = "Reserved concurrent executions for the function (m-32). Caps Postgres connections against the small RDS instance; null means unreserved (no cap — do not use with db.t4g.micro under load; note 0 would throttle the function to zero, it does NOT mean unreserved). Raise together with db_instance_class, or add RDS Proxy, before load-bearing use."
  type        = number
  default     = 10
}

variable "model_provider" {
  description = "AGENT_MODEL_PROVIDER for the function. gemini or bedrock only — Ollama is not reachable from Lambda."
  type        = string
  default     = "gemini"

  validation {
    condition     = contains(["gemini", "bedrock"], var.model_provider)
    error_message = "model_provider must be gemini or bedrock (Ollama is not reachable from Lambda)."
  }
}

variable "gemini_api_key" {
  description = "GEMINI_API_KEY for the function. Required when model_provider is gemini. Sensitive — pass via TF_VAR_gemini_api_key or a secret, never a committed value. Prefer Secrets Manager for production."
  type        = string
  sensitive   = true
  default     = ""
}

variable "bedrock_model_id" {
  description = "BEDROCK_MODEL_ID for the function (Bedrock chat model)."
  type        = string
  default     = "us.anthropic.claude-sonnet-5-5"
}

variable "bedrock_embed_model" {
  description = "BEDROCK_EMBED_MODEL for the function (Bedrock embedding model)."
  type        = string
  default     = "amazon.titan-embed-text-v2:0"
}

variable "enable_bedrock_invoke" {
  description = "Grant the Lambda execution role bedrock:InvokeModel. Enable when model_provider is bedrock (credentials then come from the role, no key in env). Enforced by a lifecycle precondition on the function: bedrock + this flag false fails the plan instead of failing at runtime."
  type        = bool
  default     = false
}

variable "bedrock_model_arns" {
  description = "Explicit ARNs for the Bedrock invoke policy. Null (default) computes scoped ARNs for bedrock_model_id / bedrock_embed_model in both foundation-model and inference-profile forms. Set this explicitly when using model IDs whose ARN form the computation cannot guess (e.g. a provisioned-throughput ARN). Never \"*\": the iam module's empty default is fail-closed."
  type        = list(string)
  default     = null
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

variable "data_connections" {
  description = "DATA_CONNECTIONS JSON for the app (named data connections). Empty object by default. Sensitive: it carries data-source credentials and API keys by design, so it is redacted from plan/apply output — but it still lands as a plaintext Lambda environment variable and in Terraform state. For production, prefer Secrets Manager / SSM Parameter Store (the function reads the secret at startup) over this variable; see ../../README.md."
  type        = string
  sensitive   = true
  default     = "{}"
}

variable "cors_origins" {
  description = "CORS_ORIGINS for the app: allowed browser origins, comma-joined into the Lambda environment as CORS_ORIGINS. Empty (default) means the app falls back to its own default; set this to the real UI origin(s) (e.g. the CloudFront ui_url) for any deployment that serves browsers. The backend reads CORS_ORIGINS from the environment."
  type        = list(string)
  default     = []
}

variable "lambda_memory_mb" {
  description = "Lambda memory in MB."
  type        = number
  default     = 1024
}

variable "function_url_auth" {
  description = "Function URL auth: AWS_IAM (signed requests) or NONE (public). Default is AWS_IAM: the app itself has no auth middleware, so a public Function URL exposes every registry capability to the internet. NONE is dev-only — use it only for a throwaway local/dev deployment, never for anything reachable beyond your own testing, and note that a browser UI cannot sign AWS_IAM requests, so with the default the CloudFront UI needs a signing front door (or a separately authenticated API path) before it can call the API from a browser."
  type        = string
  default     = "AWS_IAM"

  validation {
    condition     = contains(["NONE", "AWS_IAM"], var.function_url_auth)
    error_message = "function_url_auth must be NONE or AWS_IAM."
  }
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
