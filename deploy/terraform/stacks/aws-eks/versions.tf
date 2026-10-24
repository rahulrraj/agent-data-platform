terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Local state by default. For shared/team state, add an S3 backend block
  # (see ../../README.md) — do not commit state files.
}
