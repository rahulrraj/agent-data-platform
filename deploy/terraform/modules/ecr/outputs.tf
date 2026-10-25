output "repository_url" {
  description = "ECR repository URL (registry host + name, no tag)."
  value       = aws_ecr_repository.this.repository_url
}

# Pruned on purpose (2026-10-25 review cleanup): repository_arn and
# repository_name were exported but consumed by no stack or workflow —
# the stacks and pipelines use repository_url only.
