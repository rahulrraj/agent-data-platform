output "endpoint" {
  description = "RDS endpoint (host:port)."
  value       = aws_db_instance.this.endpoint
}

output "address" {
  description = "RDS hostname."
  value       = aws_db_instance.this.address
}

output "port" {
  description = "RDS port."
  value       = aws_db_instance.this.port
}

output "db_name" {
  description = "Database name."
  value       = aws_db_instance.this.db_name
}

output "username" {
  description = "Master username."
  value       = aws_db_instance.this.username
}

output "dsn" {
  description = "Postgres DSN for POSTGRES_DSN. Sensitive — it embeds the master password; prefer a secret manager for production."
  value       = "postgres://${var.username}:${var.password}@${aws_db_instance.this.address}:${aws_db_instance.this.port}/${var.db_name}"
  sensitive   = true
}

output "security_group_id" {
  description = "ID of the DB security group."
  value       = aws_security_group.db.id
}
