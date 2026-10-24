variable "name" {
  description = "IAM role name."
  type        = string
}

variable "assume_role_policy" {
  description = "Trust policy JSON for the role (who may assume it)."
  type        = string
}

variable "managed_policy_arns" {
  description = "AWS managed policies to attach to the role."
  type        = list(string)
  default     = []
}

variable "enable_bedrock_invoke" {
  description = "Attach an inline policy allowing bedrock:InvokeModel and bedrock:InvokeModelWithResponseStream. Used by the Lambda execution role when the model provider is bedrock; EKS workloads should prefer IRSA (see stacks/aws-eks, follow-up)."
  type        = bool
  default     = false
}

variable "bedrock_model_arns" {
  description = "Model ARNs the Bedrock invoke policy covers. Default \"*\" is broad; scope to specific model ARNs for production."
  type        = list(string)
  default     = ["*"]
}

variable "tags" {
  description = "Tags applied to all resources."
  type        = map(string)
  default     = {}
}
