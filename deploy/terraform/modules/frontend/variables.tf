variable "name" {
  description = "Name prefix for the bucket and distribution (bucket name gets a random suffix for global uniqueness)."
  type        = string
}

variable "tags" {
  description = "Tags applied to all resources."
  type        = map(string)
  default     = {}
}
