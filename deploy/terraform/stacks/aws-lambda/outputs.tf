output "function_name" {
  description = "Lambda function name."
  value       = aws_lambda_function.api.function_name
}

output "function_url" {
  description = "Function URL serving the API (chat included). No API Gateway in front."
  value       = aws_lambda_function_url.api.function_url
}

output "ecr_repository_url" {
  description = "ECR repository URL for the API image (pipeline push target)."
  value       = module.ecr.repository_url
}

output "rds_endpoint" {
  description = "RDS endpoint for the conversation store."
  value       = module.postgres.endpoint
}

output "ui_bucket_name" {
  description = "S3 bucket for UI uploads (pipeline sync target)."
  value       = module.frontend.bucket_name
}

output "ui_distribution_id" {
  description = "CloudFront distribution ID (pipeline invalidation target)."
  value       = module.frontend.distribution_id
}

output "ui_url" {
  description = "HTTPS URL of the UI."
  value       = module.frontend.ui_url
}
