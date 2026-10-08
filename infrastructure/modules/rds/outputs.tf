output "proxy_endpoint" {
  description = "RDS Proxy endpoint the application connects to"
  value       = aws_db_proxy.main.endpoint
}

output "proxy_security_group_id" {
  description = "Security group of the RDS Proxy (application must be allowed to reach it)"
  value       = aws_security_group.proxy.id
}

output "secret_arn" {
  description = "ARN of the DB credentials secret in Secrets Manager"
  value       = aws_secretsmanager_secret.db.arn
}

output "database_name" {
  description = "Database name"
  value       = var.database_name
}

output "cluster_arn" {
  description = "Aurora cluster ARN"
  value       = aws_rds_cluster.main.arn
}
