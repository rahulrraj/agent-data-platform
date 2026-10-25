terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Remote state (required for CI deploys: GitHub runners are ephemeral,
  # so local state would be lost between runs and the second deploy
  # would try to re-create existing resources). The bucket and lock table
  # are created OUT-OF-BAND, once, outside Terraform — see
  # ../../README.md for the prerequisite commands. Backend blocks cannot
  # use variables, so these values are literals; if your account uses a
  # different bucket/table/region, change them here (and only here).
  # If you previously used local state, run `terraform init -migrate-state`
  # once to move it into this backend.
  backend "s3" {
    bucket         = "agent-data-platform-tfstate"
    key            = "aws-eks/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "agent-data-platform-tf-locks"
    encrypt        = true
  }
}
