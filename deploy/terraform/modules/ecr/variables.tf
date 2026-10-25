variable "name" {
  description = "ECR repository name (e.g. agent-data-platform-api)."
  type        = string
}

variable "force_delete" {
  description = "Allow deleting a non-empty repository. True is a dev default so stacks tear down cleanly; set false for production."
  type        = bool
  default     = true
}

variable "image_tag_mutability" {
  description = "Tag mutability. IMMUTABLE by default: the deploy workflows push content-addressed github.sha tags, so a re-push of the same tag is a real error, not a silent overwrite of what 'latest' means. Re-running a workflow for the same commit skips the push when the tag already exists (see the deploy workflows). Set MUTABLE only for a manual flow that re-pushes one tag."
  type        = string
  default     = "IMMUTABLE"

  validation {
    condition     = contains(["MUTABLE", "IMMUTABLE"], var.image_tag_mutability)
    error_message = "image_tag_mutability must be MUTABLE or IMMUTABLE."
  }
}

variable "keep_image_count" {
  description = "Number of recent images the lifecycle policy keeps (rollback window, in deploys)."
  type        = number
  default     = 30

  validation {
    condition     = var.keep_image_count >= 1
    error_message = "keep_image_count must be at least 1."
  }
}

variable "tags" {
  description = "Tags applied to all resources."
  type        = map(string)
  default     = {}
}
