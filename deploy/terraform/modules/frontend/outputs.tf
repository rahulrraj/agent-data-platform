output "bucket_name" {
  description = "S3 bucket name for UI uploads (pipeline sync target)."
  value       = aws_s3_bucket.this.id
}

output "bucket_arn" {
  description = "S3 bucket ARN."
  value       = aws_s3_bucket.this.arn
}

output "distribution_id" {
  description = "CloudFront distribution ID (pipeline invalidation target)."
  value       = aws_cloudfront_distribution.this.id
}

output "distribution_domain_name" {
  description = "CloudFront domain name serving the UI."
  value       = aws_cloudfront_distribution.this.domain_name
}

output "ui_url" {
  description = "HTTPS URL of the UI."
  value       = "https://${aws_cloudfront_distribution.this.domain_name}"
}
