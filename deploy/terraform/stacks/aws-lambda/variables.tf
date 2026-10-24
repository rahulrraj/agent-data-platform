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
  description = "DATA_CONNECTIONS JSON for the app (named data connections). Empty object by default."
  type        = string
  default     = "{}"
}

variable "lambda_memory_mb" {
  description = "Lambda memory in MB."
  type        = number
  default     = 1024
}

variable "function_url_auth" {
  description = "Function URL auth: NONE (public) or AWS_IAM (signed requests)."
  type        = string
  default     = "NONE"

  validation {
    condition     = contains(["NONE", "AWS_IAM"], var.function_url_auth)
    error_message = "function_url_auth must be NONE or AWS_IAM."
  }
}

variable "tags" {
  description = "Extra tags applied to all resources."
  type        = map(string)
  default     = {}
}
