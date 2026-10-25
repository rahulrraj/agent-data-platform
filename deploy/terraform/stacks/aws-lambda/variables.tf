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
  description = "Grant the Lambda execution role bedrock:InvokeModel. Enable when model_provider is bedrock (credentials then come from the role, no key in env)."
  type        = bool
  default     = false
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
