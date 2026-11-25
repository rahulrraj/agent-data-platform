output "cluster_name" {
  description = "EKS cluster name (for aws eks update-kubeconfig). Echoes the cluster_name input — the cluster pre-exists and is not created by this stack."
  value       = data.aws_eks_cluster.this.name
}

output "cluster_endpoint" {
  description = "EKS cluster API endpoint (read from the existing cluster)."
  value       = data.aws_eks_cluster.this.endpoint
}

output "ecr_api_repository_url" {
  description = "ECR repository URL for the API image (pipeline push target)."
  value       = module.ecr_api.repository_url
}

output "ecr_ui_repository_url" {
  description = "ECR repository URL for the UI image (pipeline push target)."
  value       = module.ecr_ui.repository_url
}

output "irsa_role_arn" {
  description = "IRSA role ARN for the API pods' ServiceAccount (the pipeline annotates deploy/k8s/serviceaccount.yaml with this value)."
  value       = module.iam_api_irsa.role_arn
}

output "postgres_dsn" {
  description = "Postgres DSN for the k8s Secret. Sensitive — it echoes the postgres_dsn input (the database itself is outside this stack); feed it to the Secret, see the deploy workflow."
  value       = var.postgres_dsn
  sensitive   = true
}
