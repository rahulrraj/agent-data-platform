variable "name" {
  description = "Name prefix for all resources in this module."
  type        = string
}

variable "cidr_block" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.0.0.0/16"
}

variable "az_count" {
  description = "Number of Availability Zones to span (public + private subnet per AZ)."
  type        = number
  default     = 2
}

variable "enable_nat" {
  description = "Create a NAT gateway so private subnets can reach the internet (needed for Lambda model-API egress and EKS node pulls). Single NAT gateway (cost) in the first public subnet."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags applied to all resources."
  type        = map(string)
  default     = {}
}
