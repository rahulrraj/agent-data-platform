output "bucket_name" {
  description = "S3 bucket name for UI uploads (pipeline sync target)."
  value       = aws_s3_bucket.this.id
}

output "distribution_id" {
  description = "CloudFront distribution ID (pipeline invalidation target)."
  value       = aws_cloudfront_distribution.this.id
}

output "ui_url" {
  description = "HTTPS URL of the UI."
  value       = "https://${aws_cloudfront_distribution.this.domain_name}"
}

# Pruned on purpose (2026-10-25 review cleanup): bucket_arn and
# distribution_domain_name were exported but consumed by no stack or
# workflow — the stacks use bucket_name, distribution_id, and ui_url.
# (The bucket policy in main.tf uses the bucket ARN internally; that is
# not an output.)
