variable "name" {
  description = "Name prefix for the bucket and distribution (bucket name gets a random suffix for global uniqueness)."
  type        = string
}

variable "force_destroy" {
  description = "Allow deleting a non-empty bucket. True is a dev default so stacks tear down cleanly; set false for production so a bad deploy cannot wipe the UI bucket."
  type        = bool
  default     = true
}

variable "enable_versioning" {
  description = "Enable S3 versioning: with the pipeline's `s3 sync --delete`, versioning is what makes a bad UI build recoverable (previous objects become versions instead of vanishing). True by default."
  type        = bool
  default     = true
}

variable "access_log_bucket" {
  description = "S3 bucket name (no domain suffix) receiving CloudFront access logs. Empty (default) disables access logging — acceptable for dev, not for a public production distribution; set this (to a bucket in the same account, with a bucket policy allowing CloudFront log delivery) before real use. No WAF is attached by this module: adding a WAF web ACL is a separate production step, not built here."
  type        = string
  default     = ""
}

variable "access_log_prefix" {
  description = "Prefix for CloudFront access logs inside access_log_bucket. Only used when access_log_bucket is set."
  type        = string
  default     = "cloudfront/"
}

variable "tags" {
  description = "Tags applied to all resources."
  type        = map(string)
  default     = {}
}
