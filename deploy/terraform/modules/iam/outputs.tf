output "role_arn" {
  description = "ARN of the IAM role."
  value       = aws_iam_role.this.arn
}

# Pruned on purpose (2026-10-25 review cleanup): role_name was exported
# but consumed by no stack or workflow — the stacks use role_arn only.
