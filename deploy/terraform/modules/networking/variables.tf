variable "name" {
  description = "Name prefix for all resources in this module."
  type        = string
}

variable "cidr_block" {
  description = "CIDR block for the VPC. Must leave room for 2 * az_count /24-sized subnets carved with cidrsubnet(..., 8, ...): prefix length must be <= /24."
  type        = string
  default     = "10.0.0.0/16"

  validation {
    condition     = can(cidrhost(var.cidr_block, 0)) && tonumber(split("/", var.cidr_block)[1]) <= 24
    error_message = "cidr_block must be a valid CIDR with a prefix length of /24 or shorter (subnets are carved as /24s via cidrsubnet(..., 8, ...))."
  }
}

variable "az_count" {
  description = "Number of Availability Zones to span (public + private subnet per AZ). Must not exceed the AZs available in the region: slice() on the AZ list fails the plan if it does (Terraform variable validation cannot see data sources, so this is the fail-fast point for that case)."
  type        = number
  default     = 2

  validation {
    condition     = var.az_count >= 1 && var.az_count <= 128
    error_message = "az_count must be between 1 and 128 (2 * az_count subnets must fit in the 256 /24s carved from the VPC CIDR)."
  }
}

variable "enable_nat" {
  description = "Create NAT gateway(s) so private subnets can reach the internet (needed for Lambda model-API egress and EKS node pulls)."
  type        = bool
  default     = true
}

variable "nat_per_az" {
  description = "One NAT gateway per AZ instead of a single shared one. Default false (dev cost): a single NAT in the first public subnet is a single-AZ egress SPOF — if that AZ fails, Lambda model-API egress and EKS image pulls from every AZ fail. Set true for production; see enable_vpc_endpoints for reducing NAT dependence further."
  type        = bool
  default     = false
}

variable "enable_vpc_endpoints" {
  description = "Create VPC endpoints to reduce NAT dependence: an S3 gateway endpoint plus interface endpoints for ECR (api + dkr) and STS, attached to the private subnets with private DNS. Default false (interface endpoints cost per AZ); set true for production alongside nat_per_az. Requires enable_nat = true: the S3 gateway endpoint attaches to the private route tables, which only exist when NAT is enabled."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Tags applied to all resources."
  type        = map(string)
  default     = {}
}
