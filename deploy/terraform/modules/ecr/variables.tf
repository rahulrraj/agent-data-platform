variable "name" {
  description = "ECR repository name (e.g. agent-data-platform-api)."
  type        = string
}

variable "force_delete" {
  description = "Allow deleting a non-empty repository. True is a dev default so stacks tear down cleanly; set false for production."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags applied to all resources."
  type        = map(string)
  default     = {}
}
