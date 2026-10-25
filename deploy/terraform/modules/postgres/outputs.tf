output "endpoint" {
  description = "RDS endpoint (host:port)."
  value       = aws_db_instance.this.endpoint
}

# Pruned on purpose (2026-10-25 review cleanup): address, port, db_name,
# username, and security_group_id were exported but consumed by no stack
# or workflow — the stacks use endpoint + dsn only. Re-add one if a
# consumer needs it; the module surface stays minimal until then.
output "dsn" {
  description = "Postgres DSN for POSTGRES_DSN. Sensitive — it embeds the master password; prefer a secret manager for production."
  value       = "postgres://${var.username}:${var.password}@${aws_db_instance.this.address}:${aws_db_instance.this.port}/${var.db_name}"
  sensitive   = true
}
