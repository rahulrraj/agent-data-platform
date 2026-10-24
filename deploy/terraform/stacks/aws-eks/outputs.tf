output "cluster_name" {
  description = "EKS cluster name (for aws eks update-kubeconfig)."
  value       = aws_eks_cluster.this.name
}

output "cluster_endpoint" {
  description = "EKS cluster API endpoint."
  value       = aws_eks_cluster.this.endpoint
}

output "ecr_api_repository_url" {
  description = "ECR repository URL for the API image (pipeline push target)."
  value       = module.ecr_api.repository_url
}

output "ecr_ui_repository_url" {
  description = "ECR repository URL for the UI image (pipeline push target)."
  value       = module.ecr_ui.repository_url
}

output "rds_endpoint" {
  description = "RDS endpoint for the conversation store (pipeline writes it into the k8s Secret)."
  value       = module.postgres.endpoint
}

output "rds_dsn" {
  description = "Postgres DSN for the k8s Secret. Sensitive — it embeds the master password."
  value       = module.postgres.dsn
  sensitive   = true
}
